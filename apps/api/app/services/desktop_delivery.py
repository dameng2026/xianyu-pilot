"""SQLite adapter shared by offline tests and the guarded official-order loop.

Each operation commits before an external call. Ambiguous results retain stock;
ordinary startup installs no live gateway without explicit host composition.
"""
import hashlib
import json
import re
import uuid
import time
import os

from app.core.paths import contained_data_path
from app.services.desktop_inventory import DesktopInventory, InventoryError, now, page
from app.services.desktop_delivery_contracts import DeliveryPlan, DeliveryWork, MockPaidOrder, StepResult
from app.services.delivery_safety import safety_summary


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def fingerprint(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


class DesktopDeliveryStore(DesktopInventory):
    @staticmethod
    def audit(conn, delivery_id, action, operation=None, actor="system", evidence=None):
        conn.execute("INSERT INTO delivery_audit(delivery_id,operation_id,actor,action,evidence,created_time) VALUES(?,?,?,?,?,?)",
                     (delivery_id, operation, actor, action, evidence, now()))

    def register_mock_order(self, order: MockPaidOrder):
        """Offline fixture/service entry only; never exposed as a payment API."""
        with self.connection(write=True) as conn:
            account = conn.execute("SELECT id FROM xianyu_account WHERE id=? AND deleted=0", (order.account_id,)).fetchone()
            goods = conn.execute("SELECT * FROM xianyu_goods WHERE id=? AND account_id=? AND deleted=0",
                                 (order.goods_id, order.account_id)).fetchone()
            if not account or not goods:
                raise InventoryError("关联账号或商品不存在", 404)
            previous = conn.execute("SELECT * FROM xianyu_trade_order WHERE account_id=? AND external_order_id=?",
                                    (order.account_id, order.external_order_id)).fetchone()
            evidence = fingerprint(order.model_dump())
            if previous:
                if previous["verification_source"] != "mock" or previous["payment_evidence"] != evidence or previous["deleted"]:
                    raise InventoryError("订单身份或核验来源冲突，不能重复创建", 409)
                return previous["id"]
            stamp = now()
            amount = f"{order.amount_cent // 100}.{order.amount_cent % 100:02d}"
            external_item = goods["external_goods_id"] or goods["goods_id"] or str(goods["id"])
            order_id = conn.execute("""INSERT INTO xianyu_trade_order(account_id,external_order_id,order_status,
                total_amount,buyer_id,item_id,pay_time,deleted,created_time,updated_time,verification_source,payment_evidence)
                VALUES(?,?,1,?,?,?,?,0,?,?,'mock',?)""",
                (order.account_id, order.external_order_id, amount, order.buyer_id, external_item, stamp, stamp, stamp, evidence)).lastrowid
            conn.execute("""INSERT INTO xianyu_trade_order_item(order_id,goods_id,goods_title,quantity,goods_count,
                sku_id,deleted,created_time,updated_time) VALUES(?,?,?,?,?,?,0,?,?)""",
                (order_id, order.goods_id, goods["title"], order.quantity, order.quantity, order.sku_id, stamp, stamp))
            return order_id

    @staticmethod
    def order_context(conn, order_id):
        order = conn.execute("SELECT * FROM xianyu_trade_order WHERE id=? AND deleted=0", (order_id,)).fetchone()
        items = conn.execute("SELECT * FROM xianyu_trade_order_item WHERE order_id=? AND deleted=0", (order_id,)).fetchall()
        if order is None:
            raise InventoryError("订单不存在", 404)
        if order["verification_source"] not in ("mock","platform") or order["order_status"] not in (1, 2) or not order["payment_evidence"]:
            raise InventoryError("订单缺少平台核验或明确的离线 Mock 来源",409)
        if order["verification_source"] == "platform":
            age=time.time()-(order["official_checked_at"] or 0)
            if order["official_status"] != "WAIT_SELLER_SEND_GOODS" or order["official_refunding"] or not 0<=age<=30:
                raise InventoryError("官方状态不允许发货或核验已过期",409)
            seller=conn.execute("SELECT external_uid FROM xianyu_account WHERE id=?",(order["account_id"],)).fetchone()
            if not seller or not seller[0] or seller[0]!=order["official_seller_id"]:
                raise InventoryError("官方卖家身份不匹配",409)
        if len(items) != 1 or not 1 <= (items[0]["quantity"] or 0) <= 1000:
            raise InventoryError("本阶段须为单商品规格订单，数量必须为 1–1000", 409)
        item = items[0]
        if not conn.execute("SELECT 1 FROM xianyu_account WHERE id=? AND status=1 AND deleted=0", (order["account_id"],)).fetchone():
            raise InventoryError("订单账号已停用或不存在", 409)
        if not conn.execute("SELECT 1 FROM xianyu_goods WHERE id=? AND account_id=? AND deleted=0", (item["goods_id"], order["account_id"])).fetchone():
            raise InventoryError("订单商品与账号不匹配", 409)
        return {"orderId": order["id"], "accountId": order["account_id"], "externalOrderId": order["external_order_id"],
                "buyerId": order["buyer_id"], "goodsId": item["goods_id"], "goodsTitle": item["goods_title"] or "",
                "skuId": item["sku_id"] or "", "quantity": item["quantity"], "amount": order["total_amount"],
                "evidence": order["payment_evidence"]}

    def asset_manifest(self, plan):
        manifest = {}
        for part in plan.parts:
            if part.kind != "image":
                continue
            relative = part.content
            if not relative.startswith("assets/"):
                raise InventoryError("图片必须引用 data/assets 内的相对路径")
            try:
                asset = contained_data_path(self.path.parent, relative)
            except ValueError as exc:
                raise InventoryError("图片引用超出本地素材目录") from exc
            if not asset.is_file() or asset.stat().st_size > 20 * 1024 * 1024:
                raise InventoryError("图片不存在或超过 20 MB")
            manifest[relative] = hashlib.sha256(asset.read_bytes()).hexdigest()
        return manifest

    def freeze_assets(self,plan):
        """Copy images into content-addressed files below data/assets, never Temp."""
        manifest=self.asset_manifest(plan)
        parts=[]
        for part in plan.parts:
            if part.kind!="image":
                parts.append(part)
                continue
            source=contained_data_path(self.path.parent,part.content)
            content=source.read_bytes()
            digest=hashlib.sha256(content).hexdigest()
            if digest!=manifest[part.content]:
                raise InventoryError("素材在快照期间发生变化，请重新核验",409)
            relative=f"assets/.snapshots/{digest}.asset"
            target=contained_data_path(self.path.parent,relative)
            target.parent.mkdir(parents=True,exist_ok=True)
            if target.exists():
                if hashlib.sha256(target.read_bytes()).hexdigest()!=digest:
                    raise InventoryError("已冻结素材损坏，请保留数据并核查",409)
            else:
                temporary=contained_data_path(self.path.parent,f"tmp/snapshot-{uuid.uuid4().hex}")
                temporary.parent.mkdir(parents=True,exist_ok=True)
                try:
                    with temporary.open("xb") as stream:
                        stream.write(content)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temporary,target)
                finally:
                    temporary.unlink(missing_ok=True)
            parts.append(part.model_copy(update={"content":relative}))
        frozen=plan.model_copy(update={"parts":tuple(parts)})
        return frozen,self.asset_manifest(frozen)

    def prepare(self, order_id, plan: DeliveryPlan):
        """Freeze plan and reserve ALL cards atomically, before any message."""
        with self.connection(write=True) as conn:
            context = self.order_context(conn, order_id)
            identity = fingerprint(context)
            record = conn.execute("SELECT * FROM delivery_record WHERE order_id=? AND desktop_managed=1", (order_id,)).fetchone()
            if record:
                if record["identity_hash"] != identity:
                    raise InventoryError("订单内容已改变，需人工核对", 409)
                if record["workflow_state"] != "stock_blocked":
                    return record["id"]
                snapshot = json.loads(record["snapshot_json"])
                plan = DeliveryPlan.model_validate(snapshot["plan"])
                delivery_id = record["id"]
            else:
                if context["skuId"] != plan.sku_id:
                    raise InventoryError("SKU 没有精确匹配的发货规则", 409)
                if plan.rule_id is not None and not conn.execute("SELECT 1 FROM delivery_rule WHERE id=? AND deleted=0 AND status=1", (plan.rule_id,)).fetchone():
                    raise InventoryError("发货规则不存在或未启用", 409)
                source=conn.execute("SELECT verification_source FROM xianyu_trade_order WHERE id=?",(order_id,)).fetchone()[0]
                if source=="platform":
                    gate=conn.execute("""SELECT a.* FROM delivery_authorization a JOIN delivery_rule r ON r.id=a.rule_id
                        WHERE a.order_id=? AND a.rule_id=? AND a.rule_version=? AND r.config_version=a.rule_version AND r.deleted=0 AND r.status=1""",
                        (order_id,plan.rule_id,plan.version)).fetchone()
                    if not gate or not 0<=time.time()-gate["verified_at"]<=30:
                        raise InventoryError("订单未通过触发条件核验",409)
                plan,assets=self.freeze_assets(plan)
                snapshot = {"schemaVersion": 1, "plan": plan.model_dump(), "assets": assets, "order": context}
                stamp = now()
                delivery_id = conn.execute("""INSERT INTO delivery_record(account_id,order_id,rule_id,delivery_type,
                    delivery_mode,delivery_timing,status,delivery_status,quantity_requested,quantity_sent,deleted,
                    created_time,updated_time,desktop_managed,workflow_state,receipt_mode,identity_hash,snapshot_json,
                    confirmation_state,confirmation_operation_id,platform_item_id)
                    VALUES(?,?,?,'automatic','composite','after_payment',0,'mock_pending',?,0,0,?,?,1,'stock_blocked',?,?,?,?,?,?)""",
                    (context["accountId"], order_id, plan.rule_id, context["quantity"], stamp, stamp,source,identity,encode(snapshot),
                     "pending" if plan.confirm_shipment else "manual_pending",uuid.uuid4().hex,
                     conn.execute("SELECT item_id FROM xianyu_trade_order WHERE id=?",(order_id,)).fetchone()[0])).lastrowid
                if source=="platform":
                    conn.execute("UPDATE delivery_record SET delivery_status='pending' WHERE id=?",(delivery_id,))
                self.audit(conn, delivery_id, "snapshot_frozen")
            if snapshot["assets"] != self.asset_manifest(plan):
                raise InventoryError("素材与订单快照不一致，禁止继续发送", 409)
            is_platform=conn.execute("SELECT verification_source FROM xianyu_trade_order WHERE id=?",(order_id,)).fetchone()[0]=="platform"
            if is_platform:
                gate=conn.execute("""SELECT a.verified_at FROM delivery_authorization a JOIN delivery_rule r ON r.id=a.rule_id
                    WHERE a.order_id=? AND a.rule_id=? AND a.rule_version=? AND r.config_version=a.rule_version AND r.status=1 AND r.deleted=0""",
                    (order_id,plan.rule_id,plan.version)).fetchone()
                if not gate or not 0<=time.time()-gate[0]<=30:
                    raise InventoryError("订单规则授权失效，禁止领取库存",409)
            cards = []
            if plan.card_group_id is not None:
                group = self.group(conn, plan.card_group_id)
                if group["status"] != 1:
                    raise InventoryError("卡密组未启用", 409)
                availability="""group_id=? AND deleted=0
                    AND COALESCE(status,0)=0 AND COALESCE(is_used,0)=0 AND claim_token IS NULL
                    AND realtime_attempt_id IS NULL AND used_order_id IS NULL AND used_by_order_id IS NULL
                    AND used_time IS NULL AND (expire_time IS NULL OR expire_time>?)
                    AND NOT EXISTS(SELECT 1 FROM delivery_card_reservation r WHERE r.card_id=c.id)"""
                available=conn.execute(f"SELECT COUNT(*) FROM card_item c WHERE {availability}",(plan.card_group_id,now())).fetchone()[0]
                if available<plan.low_stock_threshold:
                    self.audit(conn,delivery_id,"low_stock_warning")
                    if plan.pause_on_low_stock:
                        conn.execute("UPDATE delivery_record SET error_message='低库存阈值触发，整单暂停',updated_time=? WHERE id=?",(now(),delivery_id))
                        return delivery_id
                cards=conn.execute(f"SELECT * FROM card_item c WHERE {availability} ORDER BY id LIMIT ?",(plan.card_group_id,now(),context["quantity"])).fetchall()
                if len(cards) < context["quantity"]:
                    conn.execute("UPDATE delivery_record SET error_message='库存不足，整单暂停',updated_time=? WHERE id=?", (now(), delivery_id))
                    self.audit(conn, delivery_id, "stock_insufficient")
                    return delivery_id
            stamp = now()
            if plan.rule_id is not None:
                quota=conn.execute("SELECT max_delivery_per_day FROM delivery_rule WHERE id=?",(plan.rule_id,)).fetchone()
                if quota and (quota[0] or 0)>0:
                    allocated=conn.execute("SELECT COALESCE(SUM(quantity),0) FROM delivery_daily_reservation WHERE rule_id=? AND day=?",(plan.rule_id,stamp[:10])).fetchone()[0]
                    if allocated+context["quantity"]>quota[0]:
                        raise InventoryError("规则每日发货额度不足，整单暂停",409)
                    conn.execute("INSERT INTO delivery_daily_reservation VALUES(?,?,?,?)",(delivery_id,plan.rule_id,stamp[:10],context["quantity"]))
            for card in cards:
                conn.execute("INSERT INTO delivery_card_reservation VALUES(?,?,?,'reserved',?,?)",
                             (card["id"], delivery_id, order_id, stamp, stamp))
                updated = conn.execute("""UPDATE card_item SET status=1,claim_token=?,used_order_id=?,updated_time=?
                    WHERE id=? AND COALESCE(status,0)=0 AND deleted=0 AND claim_token IS NULL AND used_order_id IS NULL""",
                                       (f"desktop:{delivery_id}",order_id,stamp,card["id"])).rowcount
                if updated != 1:
                    raise InventoryError("库存占用冲突，整单已回滚", 409)
            position = 0
            for part in plan.parts:
                copies = context["quantity"] if part.kind == "card" or part.repeat == "item" else 1
                for index in range(copies):
                    card = cards[index] if part.kind == "card" else None
                    content = part.content
                    replacements = {"{订单编号}": context["externalOrderId"], "{商品标题}": context["goodsTitle"],
                                    "{件号}": str(index + 1), "{购买数量}": str(context["quantity"])}
                    if card:
                        if "{密码}" in content and not card["card_value"] and not any(t in content for t in ("{卡密}", "{卡号}")):
                            raise InventoryError("所选卡密没有密码，不能使用仅含密码的模板", 409)
                        replacements.update({"{卡号}": card["card_key"], "{密码}": card["card_value"] or "",
                                             "{卡密}": card["card_key"] + ("----" + card["card_value"] if card["card_value"] else "")})
                    if part.kind != "image":
                        # One pass prevents secret contents from being interpreted as template tokens.
                        content = re.sub("|".join(re.escape(key) for key in replacements), lambda m: replacements[m.group()], content)
                    payload = {"content": content, "itemNumber": index + 1 if copies > 1 else None}
                    if part.kind == "image":
                        payload["sha256"] = snapshot["assets"][part.content]
                    conn.execute("""INSERT INTO delivery_segment(delivery_id,position,kind,payload_json,operation_id,
                        state,card_id,updated_time) VALUES(?,?,?,?,?,'pending',?,?)""",
                        (delivery_id, position, part.kind, encode(payload), uuid.uuid4().hex, card["id"] if card else None, stamp))
                    position += 1
            if cards:
                self.refresh(conn, plan.card_group_id)
            conn.execute("UPDATE delivery_record SET workflow_state='ready',error_message=NULL,updated_time=? WHERE id=?", (now(), delivery_id))
            self.audit(conn, delivery_id, "inventory_reserved_and_segments_ready")
            return delivery_id

    @staticmethod
    def record(conn, delivery_id):
        row = conn.execute("SELECT * FROM delivery_record WHERE id=? AND desktop_managed=1", (delivery_id,)).fetchone()
        if row is None:
            raise InventoryError("本地发货记录不存在", 404)
        return row

    def claim_next(self, delivery_id):
        """Commit sending BEFORE returning work. No stale sending takeover."""
        with self.connection(write=True) as conn:
            record = self.record(conn, delivery_id)
            if record["workflow_state"] in ("stock_blocked", "needs_review", "completed"):
                return None
            try:
                context = self.order_context(conn, record["order_id"])
            except InventoryError:
                self._review(conn, delivery_id, "payment_or_order_no_longer_eligible")
                return None
            if fingerprint(context) != record["identity_hash"]:
                self._review(conn, delivery_id, "order_identity_changed")
                return None
            segment = conn.execute("SELECT * FROM delivery_segment WHERE delivery_id=? AND state!='confirmed' ORDER BY position LIMIT 1", (delivery_id,)).fetchone()
            max_attempts=DeliveryPlan.model_validate(json.loads(record["snapshot_json"])["plan"]).max_attempts
            if segment:
                if segment["state"] in ("sending", "unknown") or (segment["state"] == "failed" and not segment["retry_safe"]):
                    return None
                operation, segment_id, kind = segment["operation_id"], segment["id"], segment["kind"]
                if conn.execute("SELECT COUNT(*) FROM delivery_step_attempt WHERE operation_id=?",(operation,)).fetchone()[0]>=max_attempts:
                    conn.execute("UPDATE delivery_segment SET retry_safe=0 WHERE id=?",(segment_id,))
                    self.audit(conn,delivery_id,"retry_limit_reached",operation)
                    return None
                payload = json.loads(segment["payload_json"])
                conn.execute("UPDATE delivery_segment SET state='sending',retry_safe=0,updated_time=? WHERE id=?", (now(), segment_id))
                conn.execute("UPDATE delivery_record SET workflow_state='delivering',status=1,updated_time=? WHERE id=?", (now(), delivery_id))
            else:
                if record["confirmation_state"] == "confirmed":
                    self._complete(conn, delivery_id)
                    return None
                if record["confirmation_state"] == "manual_pending":
                    conn.execute("UPDATE delivery_record SET workflow_state='confirmation_pending',updated_time=? WHERE id=?", (now(), delivery_id))
                    return None
                if record["confirmation_state"] in ("sending", "unknown") or (record["confirmation_state"] == "failed" and not record["confirmation_retry_safe"]):
                    return None
                operation, segment_id, kind = record["confirmation_operation_id"], None, "platform_confirm"
                if (conn.execute("SELECT COUNT(*) FROM delivery_step_attempt WHERE operation_id=?",(operation,)).fetchone()[0]>=max_attempts
                    and record["confirmation_manual_budget"]<=0):
                    conn.execute("UPDATE delivery_record SET confirmation_retry_safe=0 WHERE id=?",(delivery_id,))
                    self.audit(conn,delivery_id,"confirmation_retry_limit_reached",operation)
                    return None
                payload = {"orderId": context["externalOrderId"], "accountId": context["accountId"]}
                conn.execute("UPDATE delivery_record SET workflow_state='confirmation_pending',confirmation_state='sending',confirmation_retry_safe=0,confirmation_manual_budget=0,updated_time=? WHERE id=?", (now(), delivery_id))
            attempt_id = conn.execute("""INSERT INTO delivery_step_attempt(delivery_id,segment_id,operation_id,state,created_time,updated_time)
                VALUES(?,?,?,'sending',?,?)""", (delivery_id, segment_id, operation, now(), now())).lastrowid
            self.audit(conn, delivery_id, "operation_started", operation)
            return DeliveryWork(delivery_id, segment_id, attempt_id, operation, kind, payload)

    def finish(self, work, result):
        with self.connection(write=True) as conn:
            attempt = conn.execute("SELECT * FROM delivery_step_attempt WHERE id=? AND delivery_id=? AND operation_id=?",
                                   (work.attempt_id, work.delivery_id, work.operation_id)).fetchone()
            if not attempt or attempt["state"] != "sending" or attempt["segment_id"]!=work.segment_id:
                raise InventoryError("操作回执已过期，必须人工核对", 409)
            conn.execute("UPDATE delivery_step_attempt SET state=?,receipt_ref=?,updated_time=? WHERE id=?",
                         (result.status, result.receipt_ref, now(), work.attempt_id))
            state = {"confirmed": "confirmed", "not_sent": "failed", "unknown": "unknown"}[result.status]
            if work.segment_id is None:
                conn.execute("UPDATE delivery_record SET confirmation_state=?,confirmation_retry_safe=?,updated_time=? WHERE id=?",
                             (state, int(result.retry_safe), now(), work.delivery_id))
            else:
                conn.execute("UPDATE delivery_segment SET state=?,retry_safe=?,updated_time=? WHERE id=?",
                             (state, int(result.retry_safe), now(), work.segment_id))
                if state == "confirmed":
                    self._deliver_card(conn, work.segment_id)
            if state == "unknown":
                self._review(conn, work.delivery_id, "operation_result_unknown")
            elif state == "failed":
                conn.execute("UPDATE delivery_record SET workflow_state='failed',status=3,delivery_status=CASE WHEN receipt_mode='mock' THEN 'mock_failed' ELSE 'failed' END,updated_time=? WHERE id=?", (now(), work.delivery_id))
            else:
                conn.execute("UPDATE delivery_record SET workflow_state='ready',updated_time=? WHERE id=?", (now(), work.delivery_id))
                self._complete(conn, work.delivery_id)
            self.audit(conn, work.delivery_id, "operation_" + result.status, work.operation_id)

    def _deliver_card(self, conn, segment_id):
        segment = conn.execute("SELECT * FROM delivery_segment WHERE id=?", (segment_id,)).fetchone()
        if segment["card_id"] is None:
            return
        reserve = conn.execute("SELECT * FROM delivery_card_reservation WHERE card_id=? AND delivery_id=?", (segment["card_id"], segment["delivery_id"])).fetchone()
        if reserve is None:
            raise InventoryError("卡密占用关系缺失，需人工核对", 409)
        conn.execute("UPDATE delivery_card_reservation SET state='delivered',updated_time=? WHERE card_id=?", (now(), segment["card_id"]))
        conn.execute("""UPDATE card_item SET status=2,is_used=1,used_order_id=?,used_by_order_id=?,used_time=?,updated_time=? WHERE id=?""",
                     (reserve["order_id"], reserve["order_id"], now(), now(), segment["card_id"]))
        card = conn.execute("SELECT group_id FROM card_item WHERE id=?", (segment["card_id"],)).fetchone()
        self.refresh(conn, card["group_id"])

    def _review(self, conn, delivery_id, action):
        conn.execute("UPDATE delivery_record SET workflow_state='needs_review',status=5,delivery_status=CASE WHEN receipt_mode='mock' THEN 'mock_unknown' ELSE 'unknown' END,updated_time=? WHERE id=?", (now(), delivery_id))
        conn.execute("UPDATE delivery_card_reservation SET state='unknown',updated_time=? WHERE delivery_id=? AND state='reserved'", (now(), delivery_id))
        self.audit(conn, delivery_id, action)

    @staticmethod
    def _complete(conn, delivery_id):
        record = conn.execute("SELECT * FROM delivery_record WHERE id=?", (delivery_id,)).fetchone()
        remaining = conn.execute("SELECT 1 FROM delivery_segment WHERE delivery_id=? AND state!='confirmed'", (delivery_id,)).fetchone()
        delivered = conn.execute("SELECT COUNT(*) FROM delivery_card_reservation WHERE delivery_id=? AND state='delivered'", (delivery_id,)).fetchone()[0]
        conn.execute("UPDATE delivery_record SET quantity_sent=? WHERE id=?", (delivered if remaining else record["quantity_requested"], delivery_id))
        if record["confirmation_state"] == "confirmed" and not remaining:
            conn.execute("""UPDATE delivery_record SET workflow_state='completed',status=2,delivery_status=CASE WHEN receipt_mode='mock' THEN 'mock_completed' ELSE 'success' END,
                quantity_sent=quantity_requested,completed_time=?,updated_time=? WHERE id=?""", (now(), now(), delivery_id))
            if record["receipt_mode"]=="platform":
                conn.execute("UPDATE xianyu_trade_order SET order_status=3,ship_time=?,updated_time=? WHERE id=?",(now(),now(),record["order_id"]))

    def recover_interrupted(self,account_id=None):
        """Host startup, or resume under the account executor's exclusive lock."""
        with self.connection(write=True) as conn:
            account_clause=" AND o.account_id=?" if account_id is not None else ""
            params=(account_id,) if account_id is not None else ()
            affected={r[0] for r in conn.execute(f"""SELECT DISTINCT r.id FROM delivery_record r JOIN xianyu_trade_order o ON o.id=r.order_id
                WHERE r.desktop_managed=1 AND (r.confirmation_state='sending' OR EXISTS(
                SELECT 1 FROM delivery_segment s WHERE s.delivery_id=r.id AND s.state='sending')){account_clause}""",params)}
            for delivery_id in affected:
                conn.execute("UPDATE delivery_segment SET state='unknown',retry_safe=0,updated_time=? WHERE delivery_id=? AND state='sending'", (now(), delivery_id))
                conn.execute("UPDATE delivery_record SET confirmation_state='unknown',confirmation_retry_safe=0 WHERE id=? AND confirmation_state='sending'", (delivery_id,))
                conn.execute("UPDATE delivery_step_attempt SET state='unknown',updated_time=? WHERE delivery_id=? AND state='sending'", (now(), delivery_id))
                self._review(conn, delivery_id, "interrupted_operation_requires_review")
            return len(affected)

    @staticmethod
    def assert_official_fulfilled(conn,record):
        official=conn.execute("SELECT * FROM xianyu_trade_order WHERE id=?",(record["order_id"],)).fetchone()
        items=conn.execute("SELECT * FROM xianyu_trade_order_item WHERE order_id=? AND deleted=0",(record["order_id"],)).fetchall()
        original=json.loads(record["snapshot_json"])["order"]
        owner=conn.execute("SELECT external_uid FROM xianyu_account WHERE id=?",(record["account_id"],)).fetchone()
        if (record["receipt_mode"]!="platform" or official["verification_source"]!="platform"
            or official["official_status"] not in ("WAIT_BUYER_CONFIRM_GOODS","TRADE_FINISHED") or official["official_refunding"]
            or not 0<=time.time()-(official["official_checked_at"] or 0)<=30 or not owner or owner[0]!=official["official_seller_id"]
            or len(items)!=1 or official["buyer_id"]!=original["buyerId"] or official["total_amount"]!=original["amount"]
            or official["item_id"]!=record["platform_item_id"] or items[0]["goods_id"]!=original["goodsId"]
            or items[0]["sku_id"]!=original["skuId"] or items[0]["quantity"]!=original["quantity"]):
            raise InventoryError("平台确认必须依据新鲜的官方已发货核验，不能只凭人工备注",409)
        return official

    def reconcile_fulfillment(self,delivery_id):
        """An official shipped state proves B, never the contents of A."""
        with self.connection(write=True) as conn:
            record=self.record(conn,delivery_id)
            official=self.assert_official_fulfilled(conn,record)
            conn.execute("UPDATE delivery_record SET confirmation_state='confirmed',confirmation_retry_safe=0,confirmation_manual_budget=0,updated_time=? WHERE id=?",(now(),delivery_id))
            conn.execute("""UPDATE delivery_step_attempt SET state='confirmed',receipt_ref=?,updated_time=?
                WHERE delivery_id=? AND segment_id IS NULL AND state='sending'""",("official:order-state:"+official["official_status"],now(),delivery_id))
            remaining=conn.execute("SELECT 1 FROM delivery_segment WHERE delivery_id=? AND state!='confirmed'",(delivery_id,)).fetchone()
            if remaining:
                self._review(conn,delivery_id,"platform_fulfilled_content_unconfirmed")
            else:
                self._complete(conn,delivery_id)
            self.audit(conn,delivery_id,"official_fulfillment_reconciled",record["confirmation_operation_id"])

    def grant_confirmation_retry(self,delivery_id,request_key):
        """A local operator authorizes ONE B-only attempt after official WAITING."""
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,100}",request_key):
            raise InventoryError("核对请求编号无效")
        with self.connection(write=True) as conn:
            record=self.record(conn,delivery_id)
            if record["receipt_mode"]!="platform":
                raise InventoryError("模拟任务不能调用真实平台履约接口",409)
            context=self.order_context(conn,record["order_id"])
            if fingerprint(context)!=record["identity_hash"]:
                raise InventoryError("订单身份与快照不一致",409)
            if conn.execute("SELECT 1 FROM delivery_segment WHERE delivery_id=? AND state!='confirmed'",(delivery_id,)).fetchone():
                raise InventoryError("内容尚未全部确认，只能先人工核对，不能补发阶段 B",409)
            if record["confirmation_state"] in ("confirmed","sending"):
                return False
            if conn.execute("SELECT 1 FROM delivery_operator_action WHERE delivery_id=? AND request_key=?",(delivery_id,request_key)).fetchone():
                return False
            conn.execute("INSERT INTO delivery_operator_action VALUES(?,?,'retry_platform_confirmation',?)",(delivery_id,request_key,now()))
            conn.execute("""UPDATE delivery_record SET confirmation_state='pending',confirmation_retry_safe=0,
                confirmation_manual_budget=1,workflow_state='confirmation_pending',updated_time=? WHERE id=?""",(now(),delivery_id))
            self.audit(conn,delivery_id,"operator_authorized_B_only_retry",record["confirmation_operation_id"],"desktop-local-operator",request_key)
            return True

    def claim_confirmation_only(self,delivery_id):
        """Dedicated operation: never renders content or allocates stock."""
        with self.connection(write=True) as conn:
            record=self.record(conn,delivery_id)
            context=self.order_context(conn,record["order_id"])
            if record["receipt_mode"]!="platform" or fingerprint(context)!=record["identity_hash"]:
                raise InventoryError("平台订单身份核验失败",409)
            if conn.execute("SELECT 1 FROM delivery_segment WHERE delivery_id=? AND state!='confirmed'",(delivery_id,)).fetchone():
                raise InventoryError("阶段 A 尚未全部确认",409)
            if record["confirmation_manual_budget"]<=0 or record["confirmation_state"]!='pending':
                return None
            operation=record["confirmation_operation_id"]
            conn.execute("UPDATE delivery_record SET confirmation_state='sending',confirmation_manual_budget=0,updated_time=? WHERE id=?",(now(),delivery_id))
            attempt=conn.execute("INSERT INTO delivery_step_attempt(delivery_id,segment_id,operation_id,state,created_time,updated_time) VALUES(?,NULL,?,'sending',?,?)",(delivery_id,operation,now(),now())).lastrowid
            self.audit(conn,delivery_id,"platform_confirmation_only_started",operation)
            return DeliveryWork(delivery_id,None,attempt,operation,"platform_confirm",{"orderId":context["externalOrderId"],"accountId":context["accountId"]})

    def reconcile_confirmed(self, delivery_id, operation_id, *, actor, evidence):
        """Explicit positive evidence only; no unknown-to-unsent shortcut."""
        if not actor.strip() or not evidence.strip() or len(actor) > 100 or len(evidence) > 1000:
            raise InventoryError("核验必须提供操作人和证据引用")
        with self.connection(write=True) as conn:
            record = self.record(conn, delivery_id)
            if record["receipt_mode"]=="platform" and record["confirmation_operation_id"]==operation_id:
                self.assert_official_fulfilled(conn,record)
            segment = conn.execute("SELECT * FROM delivery_segment WHERE delivery_id=? AND operation_id=?", (delivery_id, operation_id)).fetchone()
            if segment:
                if segment["state"] != "unknown":
                    raise InventoryError("仅能核对结果未知的操作", 409)
                conn.execute("UPDATE delivery_segment SET state='confirmed',retry_safe=0,updated_time=? WHERE id=?", (now(), segment["id"]))
                self._deliver_card(conn, segment["id"])
            elif record["confirmation_operation_id"] == operation_id and record["confirmation_state"] in ("unknown", "manual_pending"):
                conn.execute("UPDATE delivery_record SET confirmation_state='confirmed',confirmation_retry_safe=0 WHERE id=?", (delivery_id,))
            else:
                raise InventoryError("未知操作不存在", 404)
            outstanding = conn.execute("SELECT 1 FROM delivery_segment WHERE delivery_id=? AND state='unknown'", (delivery_id,)).fetchone()
            if not outstanding and conn.execute("SELECT confirmation_state FROM delivery_record WHERE id=?", (delivery_id,)).fetchone()[0] != "unknown":
                conn.execute("UPDATE delivery_record SET workflow_state='ready',updated_time=? WHERE id=?", (now(), delivery_id))
                self._complete(conn, delivery_id)
            self.audit(conn, delivery_id, "manually_confirmed", operation_id, actor, evidence)

    def detail(self, delivery_id):
        with self.connection() as conn:
            record = self.record(conn, delivery_id)
            segments = conn.execute("SELECT * FROM delivery_segment WHERE delivery_id=? ORDER BY position", (delivery_id,)).fetchall()
            order=conn.execute("SELECT external_order_id,official_status,official_checked_at FROM xianyu_trade_order WHERE id=?",(record["order_id"],)).fetchone()
            return {"id": delivery_id, "orderId": record["order_id"], "workflowState": record["workflow_state"],
                    "orderNumber": order["external_order_id"] if order else "",
                    "officialStatus": order["official_status"] if order else "",
                    "officialCheckedAt": order["official_checked_at"] if order else None,
                    "receiptMode": record["receipt_mode"], "confirmationState": record["confirmation_state"],
                    "confirmationOperationId": record["confirmation_operation_id"], "quantityRequested": record["quantity_requested"],
                    "quantitySent": record["quantity_sent"], "errorMessage": record["error_message"],
                    "segments": [{"id": r["id"], "position": r["position"], "kind": r["kind"], "state": r["state"],
                                  "operationId": r["operation_id"], "retrySafe": bool(r["retry_safe"]), "cardId": r["card_id"]} for r in segments],
                    "attempts": [dict(r) for r in conn.execute("SELECT id,segment_id AS segmentId,operation_id AS operationId,state,receipt_ref AS receiptRef,created_time AS createdTime,updated_time AS updatedTime FROM delivery_step_attempt WHERE delivery_id=? ORDER BY id", (delivery_id,))],
                    "audit": [dict(r) for r in conn.execute("SELECT actor,action,operation_id AS operationId,evidence,created_time AS createdTime FROM delivery_audit WHERE delivery_id=? ORDER BY id", (delivery_id,))],
                    "snapshotReadOnly":True,"assetReferences":list(json.loads(record["snapshot_json"])["assets"]),
                    **safety_summary(record,segments)}

    def list_records(self, current=1, size=20, needs_review=False):
        with self.connection() as conn:
            where = "desktop_managed=1" + (" AND workflow_state='needs_review'" if needs_review else "")
            total = conn.execute(f"SELECT COUNT(*) FROM delivery_record WHERE {where}").fetchone()[0]
            rows = conn.execute(f"SELECT * FROM delivery_record WHERE {where} ORDER BY id DESC LIMIT ? OFFSET ?", (size, (current-1)*size)).fetchall()
            records=[]
            for row in rows:
                segments=conn.execute("SELECT state,retry_safe FROM delivery_segment WHERE delivery_id=? ORDER BY position",(row["id"],)).fetchall()
                order=conn.execute("SELECT external_order_id FROM xianyu_trade_order WHERE id=?",(row["order_id"],)).fetchone()
                records.append({"id":row["id"],"orderId":row["order_id"],"orderNumber":order[0] if order else "","workflowState":row["workflow_state"],
                    "receiptMode":row["receipt_mode"],"confirmationState":row["confirmation_state"],**safety_summary(row,segments)})
            return page(records,total,current,size)


class DesktopDeliveryCoordinator:
    def __init__(self, store, gateway):
        if getattr(gateway, "mode", None) != "mock":
            raise ValueError("阶段 1 未装配真实平台网关")
        self.store, self.gateway = store, gateway

    def execute(self, delivery_id):
        while (work := self.store.claim_next(delivery_id)) is not None:
            try:
                if work.kind == "image":
                    asset = contained_data_path(self.store.path.parent, work.payload["content"])
                    if not asset.is_file() or hashlib.sha256(asset.read_bytes()).hexdigest() != work.payload["sha256"]:
                        result = StepResult("not_sent", "local:asset_changed", retry_safe=False)
                    else:
                        result = self.gateway.send(work)
                else:
                    result = self.gateway.confirm_shipment(work) if work.kind == "platform_confirm" else self.gateway.send(work)
                if not isinstance(result, StepResult):
                    result = StepResult("unknown")
            except Exception:
                result = StepResult("unknown")
            # DB failure here deliberately leaves sending committed. Startup
            # converts it to unknown; NEVER report a safely retryable failure.
            self.store.finish(work, result)
            if result.status != "confirmed":
                break
        return self.store.detail(delivery_id)
