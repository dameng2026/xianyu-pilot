"""Authenticated-platform contract fakes, SQLite and async dual-trigger fault tests."""
import asyncio
import json
import time
from urllib.parse import parse_qs
import httpx
import pytest
from fastapi.testclient import TestClient

from test_desktop_delivery import store,rows
from app.core.config import Settings
from app.desktop_app import create_desktop_app
from app.services.desktop_delivery_contracts import DeliveryPlan,DeliveryPart,StepResult
from app.services.desktop_inventory import InventoryError
from app.services.official_delivery_contracts import OfficialOrder,TriggerPolicy,RuleVariant,GoodsDeliveryConfig,IncomingNotice,WAITING
from app.services.official_delivery_monitor import OfficialDeliveryMonitor,notice_from_ws
from app.services.official_delivery_gateway import OfficialDeliverySender,OfficialDeliveryRuntime
from app.services.official_delivery_store import OfficialDeliveryStore
from app.services.unified_delivery_rules import UnifiedDeliveryRules
from app.services.seller_orders_api import SellerOrdersAPI,SellerSession,PlatformOrderError


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    import socket
    def blocked(*args,**kwargs):
        raise AssertionError("real network forbidden")
    monkeypatch.setattr(socket,"create_connection",blocked)
    monkeypatch.setattr(socket,"getaddrinfo",blocked)


def fact(**changes):
    values=dict(account_id=1,seller_id="seller-1",order_id="official-1",buyer_id="buyer-1",item_id="item-1",
        sku_id="",quantity=1,amount_cent=2500,status=WAITING,refunding=False)
    return OfficialOrder(**{**values,**changes})


def variant(sku="",group=1,keyword=False,image=True):
    parts=[DeliveryPart(kind="text",content="感谢购买"),DeliveryPart(kind="card",content="{卡号}----{密码}"),
        DeliveryPart(kind="text",content="使用说明")]
    if image:
        parts.append(DeliveryPart(kind="image",content="assets/guide.png"))
    parts.append(DeliveryPart(kind="link",content="https://example.invalid/guide 提取码：abcd"))
    return RuleVariant(sku_id=sku,enabled=True,trigger=TriggerPolicy(mode="payment_keyword",keywords=("发卡","提卡")) if keyword else TriggerPolicy(),
        plan=DeliveryPlan(sku_id=sku,card_group_id=group,parts=tuple(parts)))


class Source:
    contract_verified=True
    def __init__(self,*orders):
        self.orders={order.order_id:order for order in (orders or (fact(),))}
        self.lookups=[]
        self.lists=[]
        self.on_get=None
    async def get_order(self,account_id,order_id):
        self.lookups.append((account_id,order_id))
        if self.on_get:
            self.on_get(len(self.lookups))
        return self.orders[order_id]
    async def list_waiting(self,account_id,page):
        self.lists.append(page)
        return list(self.orders.values()),False
    async def call(self,*args):
        return {}


class Sender:
    def __init__(self):
        self.ready=True
        self.sent=[]
        self.confirmed=[]
        self.results=[]
        self.confirm_results=[]
        self.on_send=None
    def is_ready(self,account_id):
        return self.ready
    async def send(self,work,official,image):
        self.sent.append((work,official,image))
        if self.on_send:
            self.on_send(work)
        result=self.results.pop(0) if self.results else StepResult("confirmed","test:im-ack")
        if isinstance(result,BaseException):
            raise result
        return result
    async def confirm_shipment(self,work,official):
        self.confirmed.append(work)
        result=self.confirm_results.pop(0) if self.confirm_results else StepResult("confirmed","test:platform-ack")
        if isinstance(result,BaseException):
            raise result
        return result


@pytest.fixture
def configured(store):
    with store.connection(write=True) as conn:
        conn.execute("UPDATE xianyu_account SET external_uid='seller-1' WHERE id=1")
    rules=UnifiedDeliveryRules(store.path)
    rules.save_goods(1,GoodsDeliveryConfig(variants=(variant(),)))
    source,sender=Source(),Sender()
    monitor=OfficialDeliveryMonitor(store.path,source,sender)
    return store,rules,source,sender,monitor


@pytest.mark.asyncio
@pytest.mark.parametrize("status",["WAIT_BUYER_PAY","TRADE_CLOSED","UNKNOWN","待发货","WAIT_SELLER_SEND_GOODS "])
async def test_only_exact_official_waiting_status_can_reserve(configured,status):
    store,rules,source,sender,monitor=configured
    source.orders["official-1"]=fact(status=status)
    await monitor.poll_once(1)
    assert rows(store,"SELECT * FROM delivery_card_reservation")==[]
    assert rows(store,"SELECT * FROM delivery_record")==[]
    assert not sender.sent


@pytest.mark.asyncio
async def test_refund_and_false_buyer_payment_claim_never_dispatch(configured):
    store,rules,source,sender,monitor=configured
    source.orders["official-1"]=fact(refunding=True)
    await monitor.poll_once(1)
    await monitor.on_ws_message(1,{"pnmId":"fake-text","direction":"IN","senderUserId":"buyer-1","contentType":1,
        "msgContent":"我已付款，马上发货","xyGoodsId":"item-1","sId":"session-1"},"seller-1")
    await monitor.drain(1)
    assert not source.lookups
    assert not sender.sent
    assert store.get_group(1,stats=True)["remainCount"]==12


@pytest.mark.asyncio
async def test_spoofed_order_hint_is_only_a_hint_and_ids_must_match(configured):
    store,rules,source,sender,monitor=configured
    await monitor.on_ws_message(1,{"pnmId":"hint","contentType":8,"reminderUrl":
        "https://www.goofish.com/order?orderId=official-1&itemId=wrong-item&buyerUserId=buyer-1"},"seller-1")
    await monitor.drain(1)
    assert len(source.lookups)==1
    assert not sender.sent
    source.orders["official-1"]=fact(status="WAIT_BUYER_PAY")
    await monitor.on_ws_message(1,{"pnmId":"hint-2","reminderUrl":"https://www.goofish.com/order?orderId=official-1"},"seller-1")
    await monitor.drain(1)
    assert not rows(store,"SELECT * FROM delivery_card_reservation")


@pytest.mark.asyncio
async def test_poll_and_im_converge_n_cards_once_and_instructions_once(configured):
    store,rules,source,sender,monitor=configured
    source.orders["official-1"]=fact(quantity=3)
    await monitor.on_ws_message(1,{"pnmId":"payment","reminderUrl":
        "https://www.goofish.com/order?orderId=official-1&itemId=item-1&buyerUserId=buyer-1"},"seller-1")
    await asyncio.gather(monitor.drain(1),monitor.poll_once(1))
    await monitor.poll_once(1)
    assert len(rows(store,"SELECT * FROM delivery_record"))==1
    assert len(rows(store,"SELECT * FROM delivery_card_reservation"))==3
    assert [work.kind for work,_,_ in sender.sent]==["text","card","card","card","text","image","link"]
    assert len(sender.confirmed)==1
    assert sender.sent[-2][2]==b"mock image version one"
    record=rows(store,"SELECT * FROM delivery_record")[0]
    assert record["receipt_mode"]=="platform" and record["workflow_state"]=="completed"
    assert rows(store,"SELECT order_status FROM xianyu_trade_order")[0]["order_status"]==3


@pytest.mark.asyncio
async def test_keyword_requires_exact_word_verified_buyer_and_unique_waiting_order(configured):
    store,rules,source,sender,monitor=configured
    rules.save_goods(1,GoodsDeliveryConfig(variants=(variant(keyword=True),)))
    await monitor.poll_once(1)
    assert not sender.sent
    for mid,buyer,text in (("wrong-buyer","other-buyer","发卡"),("negative","buyer-1","不要发卡")):
        await monitor.on_ws_message(1,{"pnmId":mid,"senderUserId":buyer,"contentType":1,"msgContent":text,"xyGoodsId":"item-1"},"seller-1")
    await monitor.drain(1)
    assert not sender.sent
    await monitor.on_ws_message(1,{"pnmId":"keyword","senderUserId":"buyer-1@goofish","contentType":1,
        "msgContent":"发卡","xyGoodsId":"item-1"},"seller-1")
    await monitor.drain(1)
    assert len(sender.confirmed)==1
    assert len(rows(store,"SELECT * FROM delivery_keyword_grant"))==1
    await monitor.drain(1)
    await monitor.poll_once(1)
    assert len(sender.confirmed)==1


@pytest.mark.asyncio
async def test_two_waiting_orders_never_guess_keyword_owner(configured):
    store,rules,source,sender,monitor=configured
    rules.save_goods(1,GoodsDeliveryConfig(variants=(variant(keyword=True),)))
    source.orders["official-2"]=fact(order_id="official-2")
    await monitor.on_ws_message(1,{"pnmId":"ambiguous","senderUserId":"buyer-1","contentType":1,
        "msgContent":"发卡","xyGoodsId":"item-1"},"seller-1")
    await monitor.drain(1)
    await monitor.poll_once(1)
    assert not sender.sent
    assert rows(store,"SELECT state FROM delivery_inbox")==[{"state":"ambiguous"}]


@pytest.mark.asyncio
async def test_shortage_hangs_whole_order_then_reuses_task_and_original_snapshot(configured):
    store,rules,source,sender,monitor=configured
    source.orders["official-1"]=fact(quantity=13)
    did=await monitor.consider(1,"official-1")
    assert store.detail(did)["workflowState"]=="stock_blocked"
    assert not sender.sent and not rows(store,"SELECT * FROM delivery_card_reservation")
    assert rows(store,"SELECT reason FROM delivery_monitor_state")==[{"reason":"inventory_insufficient"}]
    store.import_items(1,[{"content":"extra----secret"}])
    assert await monitor.consider(1,"official-1")==did
    assert len([w for w,_,_ in sender.sent if w.kind=="card"])==13


@pytest.mark.asyncio
async def test_sku_selects_separate_group_and_unknown_sku_fails_closed(configured):
    store,rules,source,sender,monitor=configured
    gid=store.save_group({"group_name":"SKU-B","group_type":"unique","card_prefix":"","password_prefix":"",
        "cost_price":0,"suggested_price":0,"alert_threshold":1,"remark":"","status":1})
    store.import_items(gid,[{"content":"B-key----B-password"}])
    rules.save_goods(1,GoodsDeliveryConfig(variants=(variant(),variant(sku="B",group=gid))))
    source.orders["official-1"]=fact(sku_id="B")
    await monitor.poll_once(1)
    assert next(w for w,_,_ in sender.sent if w.kind=="card").payload["content"]=="B-key----B-password"
    source.orders["new"]=fact(order_id="new",sku_id="missing")
    with pytest.raises(InventoryError,match="SKU"):
        await monitor.consider(1,"new")
    assert len(rows(store,"SELECT * FROM delivery_record"))==1


@pytest.mark.asyncio
async def test_rechecks_official_order_before_each_external_step(configured):
    store,rules,source,sender,monitor=configured
    def close_after_greeting(work):
        source.orders["official-1"]=fact(status="TRADE_CLOSED")
    sender.on_send=close_after_greeting
    did=await monitor.consider(1,"official-1")
    assert [w.kind for w,_,_ in sender.sent]==["text"]
    assert store.detail(did)["workflowState"]=="needs_review"
    assert store.get_group(1,stats=True)["lockedCount"]==1
    assert store.get_group(1,stats=True)["usedCount"]==0


@pytest.mark.asyncio
async def test_status_changes_between_list_and_detail_never_reserve(configured):
    store,rules,source,sender,monitor=configured
    source.on_get=lambda _: source.orders.update({"official-1":fact(status="WAIT_BUYER_PAY")})
    await monitor.poll_once(1)
    assert not rows(store,"SELECT * FROM delivery_card_reservation")
    assert not sender.sent


@pytest.mark.asyncio
async def test_unknown_confirmation_reconciles_from_official_status_without_resending(configured):
    store,rules,source,sender,monitor=configured
    sender.confirm_results=[TimeoutError("unknown")]
    did=await monitor.consider(1,"official-1")
    assert store.detail(did)["confirmationState"]=="unknown"
    with pytest.raises(InventoryError,match="官方"):
        monitor.store.reconcile_confirmed(did,store.detail(did)["confirmationOperationId"],actor="seller",evidence="I think it shipped")
    sent=len(sender.sent)
    source.orders["official-1"]=fact(status="WAIT_BUYER_CONFIRM_GOODS")
    await monitor.poll_once(1)
    assert store.detail(did)["workflowState"]=="completed"
    assert len(sender.sent)==sent and len(sender.confirmed)==1
    assert rows(store,"SELECT official_status FROM xianyu_trade_order")[0]["official_status"]=="WAIT_BUYER_CONFIRM_GOODS"


@pytest.mark.asyncio
async def test_unknown_card_not_released_or_retried_by_poll(configured):
    store,rules,source,sender,monitor=configured
    sender.results=[StepResult("confirmed","test:greeting"),TimeoutError("ambiguous card")]
    did=await monitor.consider(1,"official-1")
    await monitor.poll_once(1)
    assert store.detail(did)["workflowState"]=="needs_review"
    assert len(sender.sent)==2
    assert store.get_group(1,stats=True)["remainCount"]==11


@pytest.mark.asyncio
async def test_disconnected_im_reconnect_rechecks_and_preserves_allocation(configured):
    store,rules,source,sender,monitor=configured
    sender.ready=False
    did=await monitor.consider(1,"official-1")
    assert not sender.sent
    reservations=rows(store,"SELECT card_id FROM delivery_card_reservation")
    sender.ready=True
    await monitor.poll_once(1)
    assert store.detail(did)["workflowState"]=="completed"
    assert rows(store,"SELECT card_id FROM delivery_card_reservation")==reservations


@pytest.mark.asyncio
async def test_official_facts_cannot_bypass_keyword_authorization_and_freshness(configured):
    store,rules,source,sender,monitor=configured
    rules.save_goods(1,GoodsDeliveryConfig(variants=(variant(keyword=True),)))
    _,resolved=rules.resolve(1,"item-1", "")
    official_store=OfficialDeliveryStore(store.path)
    oid=official_store.ingest_order(fact(),1)
    with pytest.raises(InventoryError,match="触发"):
        official_store.prepare(oid,resolved.plan)
    with store.connection(write=True) as conn:
        conn.execute("UPDATE xianyu_trade_order SET official_checked_at=0")
    with pytest.raises(InventoryError,match="过期"):
        official_store.prepare(oid,resolved.plan)


def test_rule_and_goods_json_paths_write_one_authoritative_configuration(configured,monkeypatch):
    store,rules,source,sender,monitor=configured
    from app.core import paths
    monkeypatch.setattr(paths,"SOURCE_ROOT",store.path.parent.parent)
    with TestClient(create_desktop_app(Settings(runtime_mode="desktop")),base_url="http://127.0.0.1:15177",headers={"Content-Type":"application/json"}) as client:
        current=client.get("/api/auto-delivery/goods/1/config").json()["data"]
        rid=current["variants"][0]["plan"]["ruleId"]
        legacy={"ruleName":"旧接口修改","goodsId":1,"deliveryMode":"text","replyContent":"原接口的说明","status":1}
        assert client.put(f"/api/auto-delivery/rules/{rid}",json=legacy).status_code==200
        changed=client.get("/api/auto-delivery/goods/1/config").json()["data"]
        assert changed["variants"][0]["plan"]["parts"][0]["content"]=="原接口的说明"
        assert client.put("/api/auto-delivery/goods/1/config",json={"expectedVersion":current["version"],"variants":[]}).status_code==409
        old_json={"timing":"payDelivery","enabled":1,"mode":"card","cardGroupId":1,"cardTemplate":"{卡密}",
            "header":"问候","content":"说明","footer":"链接：https://example.invalid/guide","sourceId":None,"sourceTitle":"",
            "retryCount":3,"alertThreshold":5,"autoDisableOnLowStock":False}
        response=client.put("/api/auto-delivery/goods/1/config",json=old_json)
        assert response.status_code==200,response.text
        assert [p["kind"] for p in response.json()["data"]["variants"][0]["plan"]["parts"]]==["text","card","text","text"]
        assert response.json()["data"]["variants"][0]["plan"]["maxAttempts"]==4
        assert len(client.get("/api/auto-delivery/rules").json()["data"]["records"])==1
        assert client.put("/api/auto-delivery/goods/1/config",json={"timing":"reviewDelivery","content":"x"}).status_code==400
        assert client.delete(f"/api/auto-delivery/rules/{rid}").status_code==200
        assert client.get("/api/auto-delivery/goods/1/config").json()["data"]["variants"]==[]
        assert client.get("/health/ready").json()["automaticDeliveryEnabled"] is False


def test_goods_config_failure_is_atomic_and_rejects_external_asset(configured):
    store,rules,source,sender,monitor=configured
    before=rules.get_goods(1)
    invalid=RuleVariant(sku_id="bad",enabled=True,plan=DeliveryPlan(sku_id="bad",parts=(DeliveryPart(kind="image",content="C:/Users/x/image.png"),)))
    with pytest.raises(InventoryError,match="相对路径"):
        rules.save_goods(1,GoodsDeliveryConfig(variants=(variant(),invalid)))
    assert rules.get_goods(1)==before


@pytest.mark.asyncio
async def test_im_wakes_immediately_instead_of_waiting_for_poll_interval(configured):
    store,rules,source,sender,monitor=configured
    rules.save_goods(1,GoodsDeliveryConfig(variants=(variant(keyword=True),)))
    stop=asyncio.Event()
    sent=asyncio.Event()
    sender.on_send=lambda _:sent.set()
    task=asyncio.create_task(monitor.run(1,stop))
    try:
        await asyncio.sleep(0)
        await monitor.on_ws_message(1,{"pnmId":"instant","senderUserId":"buyer-1","contentType":1,"msgContent":"提卡","xyGoodsId":"item-1"},"seller-1")
        await asyncio.wait_for(sent.wait(),timeout=2)
    finally:
        stop.set()
        await asyncio.wait_for(task,timeout=5)


def test_outbound_old_messages_and_duplicate_payloads_are_not_keyword_grants(configured):
    store,rules,source,sender,monitor=configured
    assert notice_from_ws({"pnmId":"out","direction":"OUT","msgContent":"发卡","contentType":1,"senderUserId":"buyer-1"},"seller-1") is None
    assert notice_from_ws({"pnmId":"old","messageTime":1,"contentType":1,"senderUserId":"buyer-1"},"seller-1") is None
    notice=IncomingNotice(message_id="unique",kind="buyer_text",text="发卡",buyer_id="buyer-1",item_id="item-1")
    monitor.store.enqueue(1,notice)
    with pytest.raises(InventoryError,match="冲突"):
        monitor.store.enqueue(1,notice.model_copy(update={"buyer_id":"different"}))


def api_row(**changes):
    return {**{"orderId":"official-1","buyerId":"buyer-1","itemId":"item-1","skuId":"","quantity":1,
        "amountCent":2500,"orderStatus":WAITING,"inRefund":False},**changes}


@pytest.mark.asyncio
async def test_signed_order_transport_uses_requested_api_and_never_logs_in_or_guesses_status():
    requests=[]
    def response(request):
        requests.append(request)
        return httpx.Response(200,json={"ret":["SUCCESS::ok"],"data":{"orders":[api_row()],"hasMore":False}})
    session=SellerSession(1,"seller-1","unb=seller-1; _m_h5_tk=test-token_9999")
    source=SellerOrdersAPI(lambda _:session,contract_verified=True,transport=httpx.MockTransport(response))
    orders,more=await source.list_waiting(1)
    assert orders[0].status==WAITING and more is False
    assert "mtop.taobao.idle.seller.order.list" in requests[0].url.path
    payload=json.loads(parse_qs(requests[0].content.decode())["data"][0])
    assert payload["orderStatus"]==WAITING
    assert requests[0].headers["cache-control"]=="no-cache"
    assert (await source.get_order(1,"official-1")).order_id=="official-1"
    blocked=SellerOrdersAPI(lambda _:session,transport=httpx.MockTransport(response))
    with pytest.raises(PlatformOrderError,match="unverified"):
        await blocked.list_waiting(1)
    assert len(requests)==2


@pytest.mark.parametrize("changes",[{"quantity":"1.5"},{"inRefund":"false"},{"orderStatus":None},{"amountCent":True},{"skuId":None}])
def test_bad_official_shape_is_rejected(changes):
    source=SellerOrdersAPI(lambda _:SellerSession(1,"seller-1",""))
    with pytest.raises(PlatformOrderError,match="schema"):
        source.parse_order(1,api_row(**changes))
    # Localized states are preserved as-is and cannot pass the strict gate.
    assert source.parse_order(1,api_row(orderStatus="已付款")).status=="已付款"


@pytest.mark.asyncio
async def test_pagination_contract_error_and_redirect_fail_closed():
    session=SellerSession(1,"seller-1","unb=seller-1; _m_h5_tk=test-token")
    async def redirect(request):
        return httpx.Response(302,headers={"Location":"https://outside.invalid/steal"})
    source=SellerOrdersAPI(lambda _:session,contract_verified=True,transport=httpx.MockTransport(redirect))
    with pytest.raises(PlatformOrderError,match="response"):
        await source.list_waiting(1)
    source.transport=httpx.MockTransport(lambda req:httpx.Response(200,json={"ret":["SUCCESS::ok"],"data":{"orders":[api_row()]}}))
    with pytest.raises(PlatformOrderError,match="pagination"):
        await source.list_waiting(1)


@pytest.mark.asyncio
async def test_cookie_refresh_is_persisted_without_replaying_confirmation():
    requests=[]
    saved=[]
    session=SellerSession(1,"seller-1","unb=seller-1; _m_h5_tk=old-token")
    async def persist(account,field,cookie):saved.append((account,field,cookie))
    def response(request):
        requests.append(request)
        if len(requests)==1:
            return httpx.Response(200,headers={"Set-Cookie":"_m_h5_tk=new-token; Path=/; Secure"},
                json={"ret":["FAIL_SYS_TOKEN_EXPIRED::expired"],"data":{}})
        return httpx.Response(200,json={"ret":["SUCCESS::ok"],"data":{"orders":[api_row()],"hasMore":False}})
    source=SellerOrdersAPI(lambda _:session,contract_verified=True,transport=httpx.MockTransport(response),credential_updates=persist)
    with pytest.raises(PlatformOrderError):
        await source.call(1,"mtop.taobao.idle.logistic.consign.dummy",{"orderId":"official-1"})
    assert len(requests)==1
    assert session.cookie.endswith("new-token") and saved[0][1]=="cookie"
    await source.list_waiting(1)
    assert len(requests)==2 and "new-token" in requests[1].headers["Cookie"]


@pytest.mark.asyncio
async def test_gateway_requires_positive_ack_and_confirmation_is_separate(configured):
    store,rules,source,sender,monitor=configured
    class Client:
        account_id=1
        unb="seller-1"
        is_connected=True
        async def send_text_message(self,*args,**kwargs):
            assert kwargs["persist"] is False
            return {"code":200,"uuid":"message-ack"}
    async def resolver(*args):return "verified-session"
    gateway=OfficialDeliverySender(source,{1:Client()},resolver)
    from app.services.desktop_delivery_contracts import DeliveryWork
    work=DeliveryWork(1,1,1,"stable-op","text",{"content":"test"})
    assert (await gateway.send(work,fact())).status=="confirmed"
    assert (await gateway.confirm_shipment(work,fact())).status=="confirmed"
    gateway.clients[1].is_connected=False
    result=await gateway.send(work,fact())
    assert result.status=="not_sent" and result.retry_safe


@pytest.mark.asyncio
async def test_runtime_refuses_unverified_contract_before_starting_im(configured):
    store,rules,source,sender,monitor=configured
    source.contract_verified=False
    runtime=OfficialDeliveryRuntime(monitor,{}, {})
    with pytest.raises(PlatformOrderError,match="unverified"):
        await runtime.start()


def test_probe_interval_is_bounded(configured):
    store,rules,source,sender,monitor=configured
    assert monitor.interval==20
    for value in (14,31):
        with pytest.raises(ValueError):
            OfficialDeliveryMonitor(store.path,source,sender,interval=value)


def test_legacy_sync_no_longer_promotes_unknown_or_refunding_to_paid():
    from app.services.xianyu_order_sync import _map_order_status
    assert _map_order_status("UNKNOWN") == 6
    assert _map_order_status(None) == 6
    assert _map_order_status("退款中") == 6
    assert _map_order_status("待发货",in_refund=True) == 6
    assert _map_order_status("待发货") == 2


@pytest.mark.asyncio
async def test_wrong_seller_im_cannot_receive_another_sellers_delivery(configured):
    store,rules,source,sender,monitor=configured
    class WrongClient:
        account_id=1
        unb="other-seller"
        is_connected=True
        async def send_text_message(self,*args,**kwargs):
            raise AssertionError("wrong seller must not send")
    async def resolver(*args):return "cid"
    gateway=OfficialDeliverySender(source,{1:WrongClient()},resolver)
    from app.services.desktop_delivery_contracts import DeliveryWork
    work=DeliveryWork(1,1,1,"op","text",{"content":"card"})
    result=await gateway.send(work,fact())
    assert result.status=="not_sent" and result.retry_safe is False


@pytest.mark.asyncio
async def test_keyword_grant_cannot_be_reused_for_another_order(configured):
    store,rules,source,sender,monitor=configured
    rules.save_goods(1,GoodsDeliveryConfig(variants=(variant(keyword=True),)))
    _,resolved=rules.resolve(1,"item-1", "")
    official_store=OfficialDeliveryStore(store.path)
    first=official_store.ingest_order(fact(),1)
    second=official_store.ingest_order(fact(order_id="second"),1)
    for mid in ("one-message","another-message"):
        official_store.enqueue(1,IncomingNotice(message_id=mid,kind="buyer_text",buyer_id="buyer-1",item_id="item-1",text="发卡"))
    assert official_store.authorize(first,resolved,"one-message")
    assert official_store.authorize(first,resolved,"another-message")
    with pytest.raises(InventoryError,match="多笔"):
        official_store.authorize(second,resolved,"one-message")


@pytest.mark.asyncio
async def test_existing_daily_rule_cap_is_reserved_atomically(configured):
    store,rules,source,sender,monitor=configured
    with store.connection(write=True) as conn:
        conn.execute("UPDATE delivery_rule SET max_delivery_per_day=1 WHERE goods_id=1")
    await monitor.consider(1,"official-1")
    source.orders["second"]=fact(order_id="second")
    with pytest.raises(InventoryError,match="额度"):
        await monitor.consider(1,"second")
    assert len(rows(store,"SELECT * FROM delivery_card_reservation"))==1


@pytest.mark.asyncio
async def test_new_config_does_not_rewrite_existing_reserved_plan(configured):
    store,rules,source,sender,monitor=configured
    sender.ready=False
    did=await monitor.consider(1,"official-1")
    frozen=rows(store,"SELECT snapshot_json FROM delivery_record")[0]
    rules.save_goods(1,GoodsDeliveryConfig(variants=()))
    sender.ready=True
    await monitor.poll_once(1)
    assert store.detail(did)["workflowState"]=="completed"
    assert rows(store,"SELECT snapshot_json FROM delivery_record")[0]==frozen


@pytest.mark.asyncio
async def test_changed_config_during_stock_shortage_cannot_allocate_using_old_authorization(configured):
    store,rules,source,sender,monitor=configured
    source.orders["official-1"]=fact(quantity=13)
    did=await monitor.consider(1,"official-1")
    rules.save_goods(1,GoodsDeliveryConfig(variants=(variant(),)))
    store.import_items(1,[{"content":"extra-stock"}])
    with pytest.raises(InventoryError,match="授权"):
        await monitor.consider(1,"official-1")
    assert not rows(store,"SELECT * FROM delivery_card_reservation")
    assert store.detail(did)["workflowState"]=="stock_blocked"


@pytest.mark.asyncio
async def test_failed_platform_probe_keeps_pending_keyword_durable(configured):
    store,rules,source,sender,monitor=configured
    rules.save_goods(1,GoodsDeliveryConfig(variants=(variant(keyword=True),)))
    await monitor.on_ws_message(1,{"pnmId":"retry-later","senderUserId":"buyer-1","contentType":1,"msgContent":"发卡","xyGoodsId":"item-1"},"seller-1")
    original=source.list_waiting
    async def fail(*args):raise PlatformOrderError("network_offline")
    source.list_waiting=fail
    with pytest.raises(PlatformOrderError):
        await monitor.drain(1)
    assert rows(store,"SELECT state FROM delivery_inbox")==[{"state":"pending"}]
    source.list_waiting=original
    await monitor.drain(1)
    assert len(sender.confirmed)==1


def test_legacy_json_does_not_turn_string_false_into_enabled(configured):
    store,rules,source,sender,monitor=configured
    with pytest.raises(InventoryError,match="开关"):
        rules.translate_goods_body(1,{"mode":"text","content":"hello","enabled":"false"})


@pytest.mark.asyncio
async def test_desktop_im_credentials_use_injected_local_vault_and_no_legacy_db(configured,monkeypatch):
    from app.services.official_delivery_gateway import create_desktop_im_client
    from app.services import ws_token
    from app.core import database
    recorded=[]
    async def save(account,field,value):recorded.append((account,field,value))
    session=SellerSession(1,"seller-1","unb=seller-1; _m_h5_tk=old-token")
    monkeypatch.setattr(ws_token,"get_ws_token_with_refreshed_m_h5_tk",lambda *args:("access-token","new-token",None,"unb=seller-1; _m_h5_tk=new-token"))
    monkeypatch.setattr(database,"async_session",lambda *args,**kwargs:(_ for _ in ()).throw(AssertionError("legacy database forbidden")))
    client=create_desktop_im_client(session,save)
    assert await client._refresh_token()
    await client._auto_solve_captcha_after_failure("captcha")
    assert client.notifications_enabled is False
    assert (1,"token","new-token") in recorded
    assert (1,"status","0") in recorded
    assert session.cookie.endswith("new-token")
    assert "new-token" not in repr(session)


@pytest.mark.asyncio
async def test_runtime_owns_both_im_and_probe_lifecycles(configured):
    store,rules,source,sender,monitor=configured
    class Client:
        account_id=1
        unb="seller-1"
        is_connected=False
        stopped=False
        async def start(self):self.is_connected=True
        async def stop(self):self.stopped=True;self.is_connected=False
    client=Client()
    runtime=OfficialDeliveryRuntime(monitor,{1:client},{1:"seller-1"})
    await runtime.start()
    assert runtime.running and client.is_connected
    await client.on_message_callback(1,{"pnmId":"lifecycle","reminderUrl":"https://www.goofish.com/?orderId=official-1"})
    await runtime.stop()
    assert not runtime.running and client.stopped
    assert runtime.tasks==[]


@pytest.mark.asyncio
async def test_keyword_authorization_rejects_invented_message_ids_and_modified_policy(configured):
    store,rules,source,sender,monitor=configured
    rules.save_goods(1,GoodsDeliveryConfig(variants=(variant(keyword=True),)))
    _,resolved=rules.resolve(1,"item-1", "")
    official_store=OfficialDeliveryStore(store.path)
    oid=official_store.ingest_order(fact(),1)
    with pytest.raises(InventoryError,match="IM"):
        official_store.authorize(oid,resolved,"invented")
    forged=resolved.model_copy(update={"trigger":TriggerPolicy()})
    with pytest.raises(InventoryError,match="权威"):
        official_store.authorize(oid,forged)


@pytest.mark.asyncio
async def test_low_stock_pause_and_retry_limits_are_not_ignored(configured):
    store,rules,source,sender,monitor=configured
    paused=variant().model_copy(update={"plan":variant().plan.model_copy(update={"low_stock_threshold":13,"pause_on_low_stock":True})})
    rules.save_goods(1,GoodsDeliveryConfig(variants=(paused,)))
    did=await monitor.consider(1,"official-1")
    assert store.detail(did)["workflowState"]=="stock_blocked"
    assert not sender.sent and not rows(store,"SELECT * FROM delivery_card_reservation")
    # A different order/plan has an explicit one-attempt limit.
    limited=variant().model_copy(update={"plan":variant().plan.model_copy(update={"max_attempts":1})})
    rules.save_goods(1,GoodsDeliveryConfig(variants=(limited,)))
    source.orders["other"]=fact(order_id="other")
    sender.results=[StepResult("not_sent","test:preflight-refused",True)]
    other=await monitor.consider(1,"other")
    await monitor.execute(other)
    assert len(sender.sent)==1
    assert store.detail(other)["segments"][0]["retrySafe"] is False
