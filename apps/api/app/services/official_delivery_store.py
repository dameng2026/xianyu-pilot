"""Persist facts fetched through authenticated platform adapters, never buyer text."""
import time
from app.services.desktop_delivery import DesktopDeliveryStore, fingerprint, encode
from app.services.desktop_inventory import InventoryError,now
from app.services.official_delivery_contracts import OfficialOrder,IncomingNotice,WAITING


class OfficialDeliveryStore(DesktopDeliveryStore):
    def ingest_order(self,order:OfficialOrder,goods_id):
        with self.connection(write=True) as conn:
            account=conn.execute("SELECT * FROM xianyu_account WHERE id=? AND status=1 AND deleted=0",(order.account_id,)).fetchone()
            goods=conn.execute("SELECT * FROM xianyu_goods WHERE id=? AND account_id=? AND deleted=0",(goods_id,order.account_id)).fetchone()
            if not account or account["external_uid"]!=order.seller_id or not goods or order.item_id not in (goods["external_goods_id"],goods["goods_id"]):
                raise InventoryError("官方订单卖家或商品身份不匹配",409)
            previous=conn.execute("SELECT * FROM xianyu_trade_order WHERE account_id=? AND external_order_id=?",(order.account_id,order.order_id)).fetchone()
            if previous and (previous["verification_source"] not in ("platform","unverified") or previous["deleted"]):
                raise InventoryError("平台订单与本地模拟数据冲突",409)
            stamp=now()
            amount=f"{order.amount_cent//100}.{order.amount_cent%100:02d}"
            identity={"seller":order.seller_id,"buyer":order.buyer_id,"item":order.item_id,"sku":order.sku_id,"quantity":order.quantity,"amount":amount}
            proof=fingerprint(identity)
            local_status=2 if order.status==WAITING and not order.refunding else (5 if order.status in ("TRADE_CLOSED","WAIT_BUYER_PAY") or order.refunding else 3)
            if previous:
                oid=previous["id"]
                conn.execute("""UPDATE xianyu_trade_order SET buyer_id=?,item_id=?,total_amount=?,order_status=?,verification_source='platform',
                    payment_evidence=?,official_status=?,official_seller_id=?,official_refunding=?,official_checked_at=?,updated_time=? WHERE id=?""",
                    (order.buyer_id,order.item_id,amount,local_status,proof,order.status,order.seller_id,int(order.refunding),time.time(),stamp,oid))
                items=conn.execute("SELECT * FROM xianyu_trade_order_item WHERE order_id=? AND deleted=0",(oid,)).fetchall()
                if len(items)>1:
                    raise InventoryError("多商品订单尚未支持",409)
                if items:
                    conn.execute("UPDATE xianyu_trade_order_item SET goods_id=?,sku_id=?,quantity=?,goods_count=?,updated_time=? WHERE id=?",
                        (goods_id,order.sku_id,order.quantity,order.quantity,stamp,items[0]["id"]))
            else:
                oid=conn.execute("""INSERT INTO xianyu_trade_order(account_id,external_order_id,buyer_id,item_id,total_amount,order_status,
                    deleted,verification_source,payment_evidence,official_status,official_seller_id,official_refunding,official_checked_at,created_time,updated_time)
                    VALUES(?,?,?,?,?,?,0,'platform',?,?,?,?,?,?,?)""",
                    (order.account_id,order.order_id,order.buyer_id,order.item_id,amount,local_status,proof,order.status,order.seller_id,int(order.refunding),time.time(),stamp,stamp)).lastrowid
                items=[]
            if not items:
                conn.execute("""INSERT INTO xianyu_trade_order_item(order_id,goods_id,goods_title,sku_id,quantity,goods_count,deleted,created_time,updated_time)
                    VALUES(?,?,?,?,?,?,0,?,?)""",(oid,goods_id,goods["title"],order.sku_id,order.quantity,order.quantity,stamp,stamp))
            return oid

    def authorize(self,order_id,variant,message_id=None):
        with self.connection(write=True) as conn:
            context=self.order_context(conn,order_id)
            current=conn.execute("SELECT * FROM delivery_rule WHERE id=? AND deleted=0",(variant.plan.rule_id,)).fetchone()
            if (not current or current["config_version"]!=variant.plan.version or current["status"]!=1
                or current["goods_id"]!=context["goodsId"] or current["sku_id"]!=context["skuId"]):
                raise InventoryError("发货配置已改变，重新核验",409)
            from app.services.unified_delivery_rules import UnifiedDeliveryRules
            if UnifiedDeliveryRules.decode(current)!=variant:
                raise InventoryError("触发策略与权威配置不一致",409)
            if message_id:
                receipt=conn.execute("SELECT * FROM delivery_inbox WHERE account_id=? AND message_id=?",(context["accountId"],message_id)).fetchone()
                if not receipt or not 0<=time.time()-receipt["created_at"]<=1800:
                    raise InventoryError("关键词缺少可信 IM 消息记录",409)
                notice=IncomingNotice.model_validate_json(receipt["payload_json"])
                item=conn.execute("SELECT item_id FROM xianyu_trade_order WHERE id=?",(order_id,)).fetchone()[0]
                if notice.kind!="buyer_text" or notice.buyer_id!=context["buyerId"] or notice.item_id!=item or notice.text.strip() not in variant.trigger.keywords:
                    raise InventoryError("关键词消息不属于当前订单买家或商品",409)
                used=conn.execute("SELECT * FROM delivery_keyword_grant WHERE account_id=? AND message_id=?",(context["accountId"],message_id)).fetchone()
                if used and used["external_order_id"]!=context["externalOrderId"]:
                    raise InventoryError("一条关键词消息不能授权多笔订单",409)
                conn.execute("""INSERT INTO delivery_keyword_grant(account_id,external_order_id,message_id,rule_id,rule_version,created_at)
                    VALUES(?,?,?,?,?,?) ON CONFLICT(account_id,message_id) DO NOTHING""",
                    (context["accountId"],context["externalOrderId"],message_id,variant.plan.rule_id,variant.plan.version,time.time()))
            if variant.trigger.mode=="payment_keyword":
                grant=conn.execute("SELECT * FROM delivery_keyword_grant WHERE account_id=? AND external_order_id=? AND rule_id=? AND rule_version=? ORDER BY created_at DESC LIMIT 1",
                    (context["accountId"],context["externalOrderId"],variant.plan.rule_id,variant.plan.version)).fetchone()
                if not grant or not 0<=time.time()-grant["created_at"]<=1800:
                    return False
            conn.execute("""INSERT INTO delivery_authorization VALUES(?,?,?,?) ON CONFLICT(order_id) DO UPDATE SET
                rule_id=excluded.rule_id,rule_version=excluded.rule_version,verified_at=excluded.verified_at""",
                (order_id,variant.plan.rule_id,variant.plan.version,time.time()))
            return True

    def enqueue(self,account_id,notice:IncomingNotice):
        payload=encode(notice.model_dump())
        with self.connection(write=True) as conn:
            old=conn.execute("SELECT payload_json FROM delivery_inbox WHERE account_id=? AND message_id=?",(account_id,notice.message_id)).fetchone()
            if old:
                if old[0]!=payload:
                    raise InventoryError("重复消息编号的内容冲突",409)
                return
            conn.execute("INSERT INTO delivery_inbox(account_id,message_id,payload_json,created_at) VALUES(?,?,?,?)",(account_id,notice.message_id,payload,time.time()))

    def monitor_state(self,account_id,state,reason):
        with self.connection(write=True) as conn:
            conn.execute("INSERT INTO delivery_monitor_state VALUES(?,?,?,?) ON CONFLICT(account_id) DO UPDATE SET state=excluded.state,reason=excluded.reason,updated_at=excluded.updated_at",
                (account_id,state,reason,time.time()))
