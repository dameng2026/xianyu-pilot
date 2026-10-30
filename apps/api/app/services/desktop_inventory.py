"""Local warehouse operations on the original card and delivery_rule tables.

Every mutation owns one short BEGIN IMMEDIATE transaction. No network calls or
platform imports belong here; inventory counts commit with the cards they count.
"""
from contextlib import contextmanager
import datetime as dt
import sqlite3
import json

CARD_STATUS_SQL = """CASE WHEN is_used=1 OR status=2 OR used_by_order_id IS NOT NULL
    OR used_time IS NOT NULL THEN 2 WHEN claim_token IS NOT NULL OR realtime_attempt_id IS NOT NULL OR used_order_id IS NOT NULL
    THEN 1 ELSE COALESCE(status,0) END"""


class InventoryError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def now():
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat(sep=" ", timespec="microseconds")


def page(records, total, current, size):
    return {"records": records, "total": total, "current": current, "size": size}


def card_status(row):
    # Legacy rows may have NULL status or only is_used populated.
    if row["is_used"] == 1 or row["status"]==2 or row["used_by_order_id"] is not None or row["used_time"] is not None:
        return 2
    if row["claim_token"] is not None or row["realtime_attempt_id"] is not None or row["used_order_id"] is not None:
        return 1
    return row["status"] if row["status"] is not None else 0


def item_record(row):
    content = row["card_key"] + ("----" + row["card_value"] if row["card_value"] else "")
    return {"id": row["id"], "groupId": row["group_id"], "cardKey": row["card_key"],
            "cardValue": row["card_value"] or "", "cardContent": content, "content": content,
            "extraInfo": row["extra_info"] or "", "status": card_status(row),
            "usedOrderId": row["used_order_id"] or row["used_by_order_id"],
            "usedTime": row["used_time"], "remark": row["remark"] or "",
            "createdTime": row["created_time"], "updatedTime": row["updated_time"]}


def parse_item(raw):
    if not isinstance(raw, dict):
        raise InventoryError("每条卡密必须是对象")
    key = raw.get("cardContent") or ""
    value = raw.get("password") or ""
    content = raw.get("content") or ""
    extra = raw.get("extraInfo") or ""
    if not all(isinstance(v, str) for v in (key, value, content, extra)):
        raise InventoryError("卡密内容必须是文字")
    if not key.strip() and content.strip():
        parts = content.split("----", 1)
        key = parts[0]
        value = parts[1] if len(parts) == 2 else value
    elif not value.strip() and "----" in key:
        parts = key.split("----", 1)
        key = parts[0]
        value = parts[1]
    key, value, extra = key.strip(), value.strip(), extra.strip()
    if not key or max(len(key), len(value), len(extra)) > 16000 or "\n" in key or "\r" in key or "\n" in value or "\r" in value:
        raise InventoryError("卡密不能为空、不能换行，且单个字段不得超过 16000 字符")
    return key, value, extra


class DesktopInventory:
    def __init__(self, path):
        self.path = path

    @contextmanager
    def connection(self, *, write=False):
        # mode=rw prevents a misplaced request from creating an unversioned DB.
        conn = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("PRAGMA temp_store=MEMORY")
            conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def group(conn, group_id):
        row = conn.execute("SELECT * FROM card_group WHERE id=? AND deleted=0", (group_id,)).fetchone()
        if row is None:
            raise InventoryError("卡密分组不存在", 404)
        return row

    @staticmethod
    def stats(conn, group_id):
        row = conn.execute(f"""SELECT COUNT(*) AS totalCount,
            COALESCE(SUM(({CARD_STATUS_SQL})=0),0) AS remainCount,
            COALESCE(SUM(({CARD_STATUS_SQL})=1),0) AS lockedCount,
            COALESCE(SUM(({CARD_STATUS_SQL})=2),0) AS usedCount,
            COALESCE(SUM(({CARD_STATUS_SQL})=3),0) AS invalidCount,
            COALESCE(SUM(({CARD_STATUS_SQL})=4),0) AS errorCount
            FROM card_item WHERE group_id=? AND deleted=0""", (group_id,)).fetchone()
        return dict(row)

    def refresh(self, conn, group_id):
        stats = self.stats(conn, group_id)
        conn.execute("""UPDATE card_group SET total_count=?, used_count=?, available_count=?, updated_time=? WHERE id=?""",
                     (stats["totalCount"], stats["usedCount"], stats["remainCount"], now(), group_id))

    def group_record(self, conn, row):
        return {"id": row["id"], "groupName": row["group_name"],
                "cardType": "unique" if row["group_type"] in (None, "kami", "") else row["group_type"],
                "cardPrefix": row["card_prefix"] or "", "passwordPrefix": row["password_prefix"] or "",
                "costPrice": row["cost_price"], "suggestedPrice": row["suggested_price"],
                "alertThreshold": row["alert_threshold"], "remark": row["remark"] or "",
                "status": row["status"], "createdTime": row["created_time"], "updatedTime": row["updated_time"],
                **self.stats(conn, row["id"])}

    def list_groups(self, keyword, current, size, alerts=False):
        with self.connection() as conn:
            where, params = "deleted=0", []
            if keyword:
                where += " AND instr(group_name, ?) > 0"
                params.append(keyword)
            if alerts:
                where += " AND available_count < alert_threshold"
                rows = conn.execute(f"SELECT * FROM card_group WHERE {where} ORDER BY id DESC", params)
                return [self.group_record(conn, row) for row in rows]
            total = conn.execute(f"SELECT COUNT(*) FROM card_group WHERE {where}", params).fetchone()[0]
            rows = conn.execute(f"SELECT * FROM card_group WHERE {where} ORDER BY id DESC LIMIT ? OFFSET ?",
                                (*params, size, (current - 1) * size))
            return page([self.group_record(conn, r) for r in rows], total, current, size)

    def get_group(self, group_id, stats=False):
        with self.connection() as conn:
            group = self.group(conn, group_id)
            return self.stats(conn, group_id) if stats else self.group_record(conn, group)

    def save_group(self, values, group_id=None):
        with self.connection(write=True) as conn:
            if group_id is not None:
                original = self.group(conn, group_id)
                values = {**dict(original), **values}
                # Do not reinterpret already stocked card/password formats.
                if conn.execute("SELECT 1 FROM card_item WHERE group_id=? AND deleted=0 LIMIT 1", (group_id,)).fetchone():
                    original_type = "unique" if original["group_type"] in ("kami", "", None) else original["group_type"]
                    updated_type = "unique" if values["group_type"] in ("kami", "", None) else values["group_type"]
                    if original_type != updated_type:
                        raise InventoryError("已有卡密的分组不能更改类型", 409)
                conn.execute("""UPDATE card_group SET group_name=:group_name, group_type=:group_type,
                    card_prefix=:card_prefix, password_prefix=:password_prefix, cost_price=:cost_price,
                    suggested_price=:suggested_price, alert_threshold=:alert_threshold, remark=:remark,
                    status=:status, updated_time=:updated_time WHERE id=:id""", {**values, "id": group_id, "updated_time": now()})
            else:
                stamp = now()
                group_id = conn.execute("""INSERT INTO card_group(group_name,group_type,card_prefix,password_prefix,
                    cost_price,suggested_price,alert_threshold,remark,status,total_count,used_count,available_count,
                    deleted,created_time,updated_time) VALUES (:group_name,:group_type,:card_prefix,:password_prefix,
                    :cost_price,:suggested_price,:alert_threshold,:remark,:status,0,0,0,0,:stamp,:stamp)""",
                    {**values, "stamp": stamp}).lastrowid
            return group_id

    def delete_group(self, group_id):
        with self.connection(write=True) as conn:
            self.group(conn, group_id)
            if conn.execute("SELECT 1 FROM delivery_rule WHERE card_group_id=? AND deleted=0 LIMIT 1", (group_id,)).fetchone():
                raise InventoryError("分组仍被发货规则引用，请先修改或删除规则", 409)
            if conn.execute("""SELECT 1 FROM card_item WHERE group_id=? AND deleted=0 AND
                (status IN (1,2) OR is_used=1 OR claim_token IS NOT NULL OR realtime_attempt_id IS NOT NULL
                 OR used_order_id IS NOT NULL OR used_by_order_id IS NOT NULL OR used_time IS NOT NULL) LIMIT 1""", (group_id,)).fetchone():
                raise InventoryError("分组包含锁定、已使用或已认领卡密，不能删除", 409)
            stamp = now()
            conn.execute("UPDATE card_item SET deleted=1,updated_time=? WHERE group_id=? AND deleted=0", (stamp, group_id))
            conn.execute("UPDATE card_group SET deleted=1,updated_time=? WHERE id=?", (stamp, group_id))

    def list_items(self, group_id, status, current, size, *, usage=False, export=False):
        with self.connection() as conn:
            self.group(conn, group_id)
            where, params = "group_id=? AND deleted=0", [group_id]
            if status is not None:
                where += f" AND ({CARD_STATUS_SQL})=?"
                params.append(status)
            if usage:
                where += f" AND ({CARD_STATUS_SQL})=2"
            total = conn.execute(f"SELECT COUNT(*) FROM card_item WHERE {where}", params).fetchone()[0]
            if export:
                if total > 50000:
                    raise InventoryError("单次导出最多 50000 条，请按状态筛选")
                cursor = conn.execute(f"SELECT card_key, card_value FROM card_item WHERE {where} ORDER BY id", params)
                return [{"content": r["card_key"] + ("----" + r["card_value"] if r["card_value"] else "")} for r in cursor]
            rows = conn.execute(f"SELECT * FROM card_item WHERE {where} ORDER BY id DESC LIMIT ? OFFSET ?",
                                (*params, size, (current - 1) * size))
            return page([item_record(r) for r in rows], total, current, size)

    def import_items(self, group_id, items, *, single=False):
        # Validate the whole input before writing: invalid input never partly imports.
        parsed = [parse_item(raw) for raw in items]
        imported = duplicates = 0
        seen_in_batch = set()
        with self.connection(write=True) as conn:
            self.group(conn, group_id)
            for key, value, extra in parsed:
                identity = (key, value)
                if identity in seen_in_batch:
                    if single:
                        raise InventoryError("该卡密已存在", 409)
                    duplicates += 1
                    continue
                exists = conn.execute("""SELECT 1 FROM card_item WHERE group_id=? AND card_key=?
                    AND COALESCE(card_value,'')=? AND deleted=0 LIMIT 1""", (group_id, key, value)).fetchone()
                if exists:
                    if single:
                        raise InventoryError("该卡密已存在", 409)
                    duplicates += 1
                    seen_in_batch.add(identity)
                    continue
                seen_in_batch.add(identity)
                stamp = now()
                conn.execute("""INSERT INTO card_item(group_id,card_key,card_value,extra_info,status,is_used,deleted,
                    created_time,updated_time) VALUES(?,?,?,?,0,0,0,?,?)""", (group_id, key, value or None, extra, stamp, stamp))
                imported += 1
            self.refresh(conn, group_id)
        return {"successCount": imported, "duplicateCount": duplicates, "failCount": 0}

    def change_item(self, group_id, item_id, action):
        with self.connection(write=True) as conn:
            self.group(conn, group_id)
            row = conn.execute("SELECT * FROM card_item WHERE id=? AND group_id=? AND deleted=0", (item_id, group_id)).fetchone()
            if row is None:
                raise InventoryError("卡密不存在", 404)
            if (card_status(row) == 2 or row["claim_token"] is not None or row["realtime_attempt_id"] is not None
                    or row["used_order_id"] is not None or row["used_by_order_id"] is not None or row["used_time"] is not None):
                raise InventoryError("卡密已使用或已被发货任务认领，不能修改或重新入库", 409)
            if action == "delete":
                if card_status(row) == 1:
                    raise InventoryError("请先解除卡密锁定", 409)
                conn.execute("UPDATE card_item SET deleted=1,updated_time=? WHERE id=?", (now(), item_id))
            else:
                status = {"reset": 0, "lock": 1, "invalid": 3}[action]
                conn.execute("UPDATE card_item SET status=?,is_used=0,updated_time=? WHERE id=?", (status, now(), item_id))
            self.refresh(conn, group_id)

    @staticmethod
    def rule_record(row):
        return {"id": row["id"], "ruleName": row["rule_name"], "accountId": row["account_id"],
                "goodsId": row["goods_id"], "deliveryMode": row["delivery_mode"], "deliveryType": row["delivery_mode"],
                "cardGroupId": row["card_group_id"], "replyContent": row["delivery_content"] or "",
                "deliveryContent": row["delivery_content"] or "", "matchType": "keyword",
                "matchKeywords": row["trigger_keyword"] or "", "triggerKeyword": row["trigger_keyword"] or "",
                "triggerOnPay": row["trigger_on_pay"], "maxDeliveryPerDay": row["max_delivery_per_day"],
                "status": row["status"], "createdTime": row["created_time"], "updatedTime": row["updated_time"],
                "skuId":row["sku_id"],"configVersion":row["config_version"],
                "plan":json.loads(row["plan_json"]) if row["plan_json"] else None,
                "trigger":json.loads(row["trigger_json"]) if row["trigger_json"] else None}

    def list_rules(self, account_id, current, size, keyword=""):
        with self.connection() as conn:
            where, params = "deleted=0", []
            if account_id is not None:
                where += " AND account_id=?"
                params.append(account_id)
            if keyword:
                where += " AND (instr(rule_name, ?) > 0 OR instr(trigger_keyword, ?) > 0)"
                params.extend([keyword, keyword])
            total = conn.execute(f"SELECT COUNT(*) FROM delivery_rule WHERE {where}", params).fetchone()[0]
            rows = conn.execute(f"SELECT * FROM delivery_rule WHERE {where} ORDER BY id DESC LIMIT ? OFFSET ?",
                                (*params, size, (current - 1) * size))
            return page([self.rule_record(r) for r in rows], total, current, size)

    def save_rule(self, values, rule_id=None):
        from app.services.unified_delivery_rules import UnifiedDeliveryRules
        return UnifiedDeliveryRules(self.path).save_legacy(values,rule_id)

    def delete_rule(self, rule_id):
        with self.connection(write=True) as conn:
            prior=conn.execute("SELECT goods_id FROM delivery_rule WHERE id=? AND deleted=0",(rule_id,)).fetchone()
            if conn.execute("UPDATE delivery_rule SET deleted=1,updated_time=? WHERE id=? AND deleted=0", (now(), rule_id)).rowcount != 1:
                raise InventoryError("发货规则不存在", 404)
            if prior[0] is not None:
                from app.services.unified_delivery_rules import UnifiedDeliveryRules
                UnifiedDeliveryRules(self.path).project(conn,prior[0])
