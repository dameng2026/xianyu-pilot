"""Durable IM hints + periodic seller probes feed ONE verified delivery pipeline."""
import asyncio
import hashlib
import json
import time
from urllib.parse import urlparse,parse_qs

from app.core.paths import contained_data_path
from app.services.desktop_delivery_contracts import StepResult
from app.services.official_delivery_contracts import IncomingNotice,WAITING,SHIPPED
from app.services.official_delivery_store import OfficialDeliveryStore
from app.services.unified_delivery_rules import UnifiedDeliveryRules
from app.services.desktop_inventory import InventoryError,now
from app.services.seller_orders_api import PlatformOrderError
from app.services.delivery_resume import ResumeDetector
from app.services.delivery_gate import DeliveryGate


def notice_from_ws(message,seller_id):
    if str(message.get("direction", "IN")).upper()!="IN":
        return None
    mid=str(message.get("pnmId") or "")
    if not mid:
        return None
    timestamp=message.get("messageTime")
    if timestamp:
        try:
            sent=float(timestamp)/(1000 if float(timestamp)>10**11 else 1)
            if not 0<=time.time()-sent<=1800:
                return None
        except (TypeError,ValueError):
            return None
    query=parse_qs(urlparse(str(message.get("reminderUrl") or "")).query)
    def single(key):
        values=query.get(key,[])
        return values[0] if len(values)==1 else ""
    order_id=single("orderId") or single("tradeId")
    sender=str(message.get("senderUserId") or "").removesuffix("@goofish")
    if order_id:
        return IncomingNotice(message_id=mid,kind="order_hint",order_id=order_id,
            item_id=single("itemId") or str(message.get("xyGoodsId") or ""),buyer_id=single("buyerUserId") or single("peerUserId"))
    if not sender or sender==seller_id or message.get("contentType")!=1:
        return None
    return IncomingNotice(message_id=mid,kind="buyer_text",buyer_id=sender,
        item_id=str(message.get("xyGoodsId") or ""),session_id=str(message.get("sId") or ""),text=str(message.get("msgContent") or "")[:4000])


class OfficialDeliveryMonitor:
    def __init__(self,path,source,sender,*,interval=20):
        if not 15<=interval<=30:
            raise ValueError("轮询间隔必须为 15–30 秒")
        self.store=OfficialDeliveryStore(path)
        self.rules=UnifiedDeliveryRules(path)
        self.source,self.sender,self.interval=source,sender,interval
        self._locks={}
        self._wakes={}
        self._stopping=False
        self._resume_required={}
        self._resume_generation={}
        self.continuity=ResumeDetector()
        self.gate=DeliveryGate()

    def lock(self,account_id):
        return self._locks.setdefault(account_id,asyncio.Lock())

    def request_reconciliation(self,account_id,reason,*,wake=True):
        self._resume_required[account_id]=reason
        self._resume_generation[account_id]=self._resume_generation.get(account_id,0)+1
        if wake:
            self._wakes.setdefault(account_id,asyncio.Event()).set()

    def observe_continuity(self,account_id):
        # Run at dispatch boundaries too: the first task scheduled after sleep
        # must not send before the five-second lifecycle watcher gets CPU time.
        if self.continuity.sample():
            for aid in set(self._locks)|set(self._wakes)|{account_id}:
                self.request_reconciliation(aid,"dispatch_continuity_gap")
            return False
        return True

    async def ensure_reconciled(self,account_id):
        """Caller holds the account lock, after any previous external call settled."""
        if account_id not in self._resume_required:
            return True
        generation=self._resume_generation[account_id]
        await asyncio.to_thread(self.store.monitor_state,account_id,"reconciling",self._resume_required[account_id])
        await asyncio.to_thread(self.store.recover_interrupted,account_id)
        with self.store.connection() as conn:
            ids=[r[0] for r in conn.execute("""SELECT r.id FROM delivery_record r JOIN xianyu_trade_order o ON o.id=r.order_id
                WHERE o.account_id=? AND r.receipt_mode='platform' AND r.workflow_state!='completed' ORDER BY r.id""",(account_id,))]
        for delivery_id in ids:
            await self._reconcile_record(delivery_id)
        # Even an empty recovery queue must verify the account's official API
        # session before the tray can report a healthy green state.
        await self.pages(account_id)
        if not self.sender.is_ready(account_id):
            await asyncio.to_thread(self.store.monitor_state,account_id,"paused","im_disconnected_after_reconciliation")
            return False
        if self._resume_generation[account_id]!=generation:
            return False
        await asyncio.to_thread(self.store.monitor_state,account_id,"monitoring","reconciliation_completed")
        if self._resume_generation[account_id]!=generation:
            return False
        self._resume_required.pop(account_id,None)
        return True

    async def pages(self,account_id):
        result=[]
        seen=set()
        for page in range(1,101):
            orders,more=await self.source.list_waiting(account_id,page)
            for order in orders:
                if order.account_id!=account_id:
                    raise PlatformOrderError("account_mismatch")
                if order.order_id in seen:
                    raise PlatformOrderError("pagination_duplicate_order")
                seen.add(order.order_id)
                result.append(order)
            if not more:
                return result
        raise PlatformOrderError("pagination_limit")

    async def on_ws_message(self,account_id,message,seller_id):
        notice=notice_from_ws(message,seller_id)
        if notice:
            await asyncio.to_thread(self.store.enqueue,account_id,notice)
            self._wakes.setdefault(account_id,asyncio.Event()).set()

    async def drain(self,account_id):
        if self.gate.blocked:
            return
        self.observe_continuity(account_id)
        async with self.lock(account_id):
            if not await self.ensure_reconciled(account_id):
                return
            with self.store.connection() as conn:
                messages=conn.execute("SELECT * FROM delivery_inbox WHERE account_id=? AND state='pending' ORDER BY id LIMIT 100",(account_id,)).fetchall()
            for row in messages:
                if self._stopping or self.gate.blocked:
                    return
                notice=IncomingNotice.model_validate_json(row["payload_json"])
                if not 0<=time.time()-row["created_at"]<=1800:
                    outcome="expired"
                elif notice.kind=="order_hint":
                    try:
                        await self.consider(account_id,notice.order_id,notice)
                        outcome="done"
                    except InventoryError:
                        outcome="blocked"
                        await asyncio.to_thread(self.store.monitor_state,account_id,"blocked","configuration_or_identity_mismatch")
                else:
                    if not await asyncio.to_thread(self.rules.has_keyword,account_id,notice.item_id,notice.text):
                        with self.store.connection(write=True) as conn:
                            conn.execute("UPDATE delivery_inbox SET state='ignored' WHERE id=?",(row["id"],))
                        continue
                    orders=await self.pages(account_id)
                    candidates=[order for order in orders if order.status==WAITING and not order.refunding and order.buyer_id==notice.buyer_id
                        and notice.item_id and order.item_id==notice.item_id]
                    if len(candidates)!=1:
                        outcome="ambiguous" if len(candidates)>1 else "not_eligible"
                    else:
                        try:
                            await self.consider(account_id,candidates[0].order_id,notice)
                            outcome="done"
                        except InventoryError:
                            outcome="blocked"
                            await asyncio.to_thread(self.store.monitor_state,account_id,"blocked","configuration_or_identity_mismatch")
                with self.store.connection(write=True) as conn:
                    conn.execute("UPDATE delivery_inbox SET state=? WHERE id=?",(outcome,row["id"]))

    async def consider(self,account_id,order_id,notice=None):
        if self.gate.blocked or account_id in self._resume_required:
            return None
        official=await self.source.get_order(account_id,order_id)
        if official.account_id!=account_id or official.order_id!=order_id:
            raise PlatformOrderError("order_identity_mismatch")
        if notice and ((notice.item_id and notice.item_id!=official.item_id) or (notice.buyer_id and notice.buyer_id!=official.buyer_id)):
            return None
        if official.status!=WAITING or official.refunding:
            return None
        with self.store.connection() as conn:
            prior=conn.execute("""SELECT r.* FROM delivery_record r JOIN xianyu_trade_order o ON o.id=r.order_id
                WHERE o.account_id=? AND o.external_order_id=? AND r.desktop_managed=1""",(account_id,order_id)).fetchone()
        if prior and prior["workflow_state"]!="stock_blocked":
            if prior["workflow_state"] not in ("completed","needs_review","delivering"):
                await self.execute(prior["id"])
            return prior["id"]
        goods_id,variant=await asyncio.to_thread(self.rules.resolve,account_id,official.item_id,official.sku_id)
        oid=await asyncio.to_thread(self.store.ingest_order,official,goods_id)
        # IM and polling converge on the same account-scoped order. Existing
        # tasks retain their frozen plan and are never allocated a second time.
        with self.store.connection() as conn:
            old=conn.execute("SELECT id,workflow_state FROM delivery_record WHERE order_id=? AND desktop_managed=1",(oid,)).fetchone()
        if old and old["workflow_state"] in ("completed","needs_review","delivering","confirmation_pending"):
            return old["id"]
        message_id=None
        if notice and notice.kind=="buyer_text" and notice.text.strip() in variant.trigger.keywords:
            message_id=notice.message_id
        allowed=await asyncio.to_thread(self.store.authorize,oid,variant,message_id)
        if not allowed:
            return None
        did=await asyncio.to_thread(self.store.prepare,oid,variant.plan)
        if self.store.detail(did)["workflowState"]=="stock_blocked":
            await asyncio.to_thread(self.store.monitor_state,account_id,"stock_blocked","inventory_insufficient")
            return did
        await self.execute(did)
        return did

    async def execute(self,delivery_id):
        while not self._stopping and not self.gate.blocked:
            with self.store.connection() as conn:
                record=self.store.record(conn,delivery_id)
                order=conn.execute("SELECT * FROM xianyu_trade_order WHERE id=?",(record["order_id"],)).fetchone()
            self.observe_continuity(order["account_id"])
            if self.gate.blocked or order["account_id"] in self._resume_required:
                return
            if record["receipt_mode"]!="platform" or record["workflow_state"] in ("completed","needs_review","stock_blocked"):
                if record["workflow_state"]=="completed":
                    await asyncio.to_thread(self.store.monitor_state,order["account_id"],"monitoring","official_delivery_completed")
                return
            # Recheck the official order before EVERY external step, not merely
            # the notification or the 20-second list snapshot.
            official=await self.source.get_order(order["account_id"],order["external_order_id"])
            frozen=json.loads(record["snapshot_json"])["order"]
            goods_id=frozen["goodsId"]
            amount=f"{official.amount_cent//100}.{official.amount_cent%100:02d}"
            if (official.account_id!=order["account_id"] or official.order_id!=order["external_order_id"] or
                official.buyer_id!=frozen["buyerId"] or official.item_id!=record["platform_item_id"] or
                official.sku_id!=frozen["skuId"] or official.quantity!=frozen["quantity"] or amount!=frozen["amount"]):
                with self.store.connection(write=True) as conn:
                    self.store._review(conn,delivery_id,"official_order_identity_changed")
                return
            await asyncio.to_thread(self.store.ingest_order,official,goods_id)
            self.observe_continuity(order["account_id"])
            if self.gate.blocked or order["account_id"] in self._resume_required:
                return
            if official.status!=WAITING or official.refunding:
                if official.status in SHIPPED and not official.refunding:
                    await asyncio.to_thread(self.store.reconcile_fulfillment,delivery_id)
                else:
                    with self.store.connection(write=True) as conn:
                        self.store._review(conn,delivery_id,"official_status_no_longer_waiting")
                return
            if not self.sender.is_ready(order["account_id"]) and any(s["state"]!="confirmed" for s in self.store.detail(delivery_id)["segments"]):
                self.request_reconciliation(order["account_id"],"im_disconnected")
                await asyncio.to_thread(self.store.monitor_state,order["account_id"],"paused","im_disconnected")
                return
            work=await asyncio.to_thread(self.store.claim_next,delivery_id)
            if work is None:
                if self.store.detail(delivery_id)["confirmationState"]=="manual_pending":
                    await asyncio.to_thread(self.store.monitor_state,order["account_id"],"waiting_manual_confirmation","manual_confirmation_required")
                return
            self.observe_continuity(order["account_id"])
            if self._stopping or self.gate.blocked or order["account_id"] in self._resume_required:
                await asyncio.to_thread(self.store.finish,work,StepResult("not_sent","local:paused_before_external_call",True))
                return
            try:
                if work.kind=="image":
                    path=contained_data_path(self.store.path.parent,work.payload["content"])
                    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=work.payload["sha256"]:
                        result=StepResult("not_sent","local:asset_changed",retry_safe=False)
                    else:
                        result=await self.sender.send(work,official,path.read_bytes())
                elif work.kind=="platform_confirm":
                    result=await self.sender.confirm_shipment(work,official)
                else:
                    result=await self.sender.send(work,official,None)
                if not isinstance(result,StepResult):
                    result=StepResult("unknown")
            except Exception:
                result=StepResult("unknown")
            await asyncio.to_thread(self.store.finish,work,result)
            if result.status!="confirmed":
                await asyncio.to_thread(self.store.monitor_state,order["account_id"],"blocked",
                    "delivery_result_unknown" if result.status=="unknown" else "delivery_step_not_sent")
                return

    async def poll_once(self,account_id):
        if self.gate.blocked:
            return
        self.observe_continuity(account_id)
        async with self.lock(account_id):
            if not await self.ensure_reconciled(account_id):
                return
            with self.store.connection() as conn:
                queued=conn.execute("""SELECT r.id,o.external_order_id FROM delivery_record r JOIN xianyu_trade_order o ON o.id=r.order_id
                    WHERE o.account_id=? AND r.receipt_mode='platform' AND r.workflow_state IN ('ready','failed','confirmation_pending')""",(account_id,)).fetchall()
            for queued_record in queued:
                if self._stopping or self.gate.blocked:
                    return
                await self.execute(queued_record[0])
            if self._stopping or self.gate.blocked:
                return
            await self.reconcile_confirmation(account_id)
            orders=await self.pages(account_id)
            for order in orders:
                if self._stopping:
                    return
                if order.status==WAITING and not order.refunding and order.order_id not in {row["external_order_id"] for row in queued}:
                    try:
                        await self.consider(account_id,order.order_id)
                    except InventoryError:
                        await asyncio.to_thread(self.store.monitor_state,account_id,"blocked","configuration_or_identity_mismatch")

    async def reconcile_confirmation(self,account_id):
        with self.store.connection() as conn:
            records=conn.execute("""SELECT r.id FROM delivery_record r
                JOIN xianyu_trade_order o ON o.id=r.order_id WHERE o.account_id=? AND r.receipt_mode='platform'
                AND r.workflow_state='needs_review'""",(account_id,)).fetchall()
        for record in records:
            await self._reconcile_record(record["id"])

    async def reconcile_record(self,delivery_id,*,retry_confirmation=False,request_key=""):
        with self.store.connection() as conn:
            record=self.store.record(conn,delivery_id)
            account_id=record["account_id"]
        async with self.lock(account_id):
            await asyncio.to_thread(self.store.recover_interrupted,account_id)
            return await self._reconcile_record(delivery_id,retry_confirmation,request_key)

    async def _reconcile_record(self,delivery_id,retry_confirmation=False,request_key=""):
        if retry_confirmation and (self._stopping or self.gate.blocked):
            raise InventoryError("自动发货已暂停，禁止平台履约操作",409)
        with self.store.connection() as conn:
            record=self.store.record(conn,delivery_id)
            order=conn.execute("SELECT * FROM xianyu_trade_order WHERE id=?",(record["order_id"],)).fetchone()
            owner=conn.execute("SELECT external_uid FROM xianyu_account WHERE id=?",(record["account_id"],)).fetchone()
        if record["receipt_mode"]!="platform":
            raise InventoryError("模拟任务不调用真实平台",409)
        official=await self.source.get_order(record["account_id"],order["external_order_id"])
        continuous=self.observe_continuity(record["account_id"])
        frozen=json.loads(record["snapshot_json"])["order"]
        if (official.account_id!=record["account_id"] or official.order_id!=order["external_order_id"] or not owner
            or official.seller_id!=owner[0] or official.buyer_id!=frozen["buyerId"] or official.item_id!=record["platform_item_id"]
            or official.sku_id!=frozen["skuId"] or official.quantity!=frozen["quantity"]
            or f"{official.amount_cent//100}.{official.amount_cent%100:02d}"!=frozen["amount"]):
            with self.store.connection(write=True) as conn:
                self.store._review(conn,delivery_id,"reconciliation_identity_mismatch")
            raise InventoryError("官方订单身份与冻结记录不一致，保留库存待核查",409)
        await asyncio.to_thread(self.store.ingest_order,official,frozen["goodsId"])
        if official.status in SHIPPED and not official.refunding:
            await asyncio.to_thread(self.store.reconcile_fulfillment,delivery_id)
        elif official.status==WAITING and not official.refunding:
            with self.store.connection(write=True) as conn:
                self.store.audit(conn,delivery_id,"official_waiting_checked")
            if retry_confirmation:
                if not continuous:
                    raise InventoryError("检测到休眠或时钟间断，保留绑定，请完成恢复核验后重试",409)
                generation=self._resume_generation.get(record["account_id"],0)
                granted=await asyncio.to_thread(self.store.grant_confirmation_retry,delivery_id,request_key)
                if granted:
                    work=await asyncio.to_thread(self.store.claim_confirmation_only,delivery_id)
                    if work is not None:
                        self.observe_continuity(record["account_id"])
                        if self._stopping or self.gate.blocked or self._resume_generation.get(record["account_id"],0)!=generation:
                            await asyncio.to_thread(self.store.finish,work,StepResult("not_sent","local:paused_before_confirmation",True))
                            return self.store.detail(delivery_id)
                        try:
                            result=await self.sender.confirm_shipment(work,official)
                            if not isinstance(result,StepResult):
                                result=StepResult("unknown")
                        except Exception:
                            result=StepResult("unknown")
                        await asyncio.to_thread(self.store.finish,work,result)
        else:
            with self.store.connection(write=True) as conn:
                self.store._review(conn,delivery_id,"official_state_blocks_delivery")
            if retry_confirmation:
                raise InventoryError("官方订单不是待发货状态，禁止补发阶段 B",409)
        return self.store.detail(delivery_id)

    async def probe_loop(self,account_id,stop):
        failures=0
        while not stop.is_set():
            try:
                await self.drain(account_id)
                await self.poll_once(account_id)
                failures=0
            except asyncio.CancelledError:
                raise
            except Exception:
                failures+=1
                self.request_reconciliation(account_id,"official_connection_failed",wake=False)
                await asyncio.to_thread(self.store.monitor_state,account_id,"paused","official_verification_failed")
            try:
                await asyncio.wait_for(stop.wait(),timeout=min(300,self.interval*2**min(failures,4)))
            except asyncio.TimeoutError:
                pass

    async def message_loop(self,account_id,stop):
        wake=self._wakes.setdefault(account_id,asyncio.Event())
        wake.set()
        while not stop.is_set():
            await wake.wait()
            wake.clear()
            try:
                await self.drain(account_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.request_reconciliation(account_id,"official_connection_failed",wake=False)
                await asyncio.to_thread(self.store.monitor_state,account_id,"paused","im_order_verification_failed")

    async def run(self,account_id,stop):
        tasks=[asyncio.create_task(self.probe_loop(account_id,stop)),asyncio.create_task(self.message_loop(account_id,stop))]
        try:
            await stop.wait()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
