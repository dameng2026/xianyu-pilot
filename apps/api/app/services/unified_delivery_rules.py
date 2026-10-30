"""The original rule table is authoritative; goods JSON is a transactionally updated projection."""
import json
import re
from app.services.desktop_delivery import DesktopDeliveryStore, encode
from app.services.desktop_delivery_contracts import DeliveryPlan, DeliveryPart
from app.services.official_delivery_contracts import TriggerPolicy, RuleVariant, GoodsDeliveryConfig
from app.services.desktop_inventory import InventoryError, now


class UnifiedDeliveryRules(DesktopDeliveryStore):
    @staticmethod
    def decode(row):
        if row["plan_json"]:
            plan = DeliveryPlan.model_validate_json(row["plan_json"])
        else:
            card = row["delivery_mode"] in ("card","kami")
            plan = DeliveryPlan(sku_id=row["sku_id"],card_group_id=row["card_group_id"] if card else None,
                parts=(DeliveryPart(kind="card" if card else "text",content=row["delivery_content"] or ("{卡密}" if card else "")),))
        words = tuple(word.strip() for word in re.split(r"[,，|]",row["trigger_keyword"] or "") if word.strip())
        trigger = TriggerPolicy.model_validate_json(row["trigger_json"]) if row["trigger_json"] else TriggerPolicy(mode="payment_keyword" if words else "payment",keywords=words)
        return RuleVariant(sku_id=row["sku_id"],enabled=row["status"]==1,
            plan=plan.model_copy(update={"rule_id":row["id"],"version":row["config_version"]}),trigger=trigger)

    def project(self,conn,goods_id):
        variants = [self.decode(row).model_dump() for row in conn.execute("SELECT * FROM delivery_rule WHERE goods_id=? AND deleted=0 ORDER BY sku_id",(goods_id,))]
        payload = encode({"schemaVersion":2,"variants":variants})
        conn.execute("""INSERT INTO delivery_goods_config(goods_id,config_json,version,deleted) VALUES(?,?,1,0)
            ON CONFLICT(goods_id) DO UPDATE SET config_json=excluded.config_json,version=delivery_goods_config.version+1,deleted=0""",(goods_id,payload))

    def validate_variant(self,conn,variant):
        if variant.plan.card_group_id is not None:
            self.group(conn,variant.plan.card_group_id)
        self.asset_manifest(variant.plan)

    def save_legacy(self,values,rule_id=None):
        with self.connection(write=True) as conn:
            if values["trigger_on_pay"]!=1:
                raise InventoryError("官方付款核验不可关闭；关键词模式也必须先付款")
            original = conn.execute("SELECT * FROM delivery_rule WHERE id=? AND deleted=0",(rule_id,)).fetchone() if rule_id else None
            if rule_id and original is None:
                raise InventoryError("发货规则不存在",404)
            goods_id,account_id = values["goods_id"],values["account_id"]
            if goods_id is not None:
                goods=conn.execute("SELECT * FROM xianyu_goods WHERE id=? AND deleted=0",(goods_id,)).fetchone()
                if not goods:
                    raise InventoryError("商品不存在",404)
                if account_id is not None and account_id != goods["account_id"]:
                    raise InventoryError("商品不属于所选账号")
                account_id=goods["account_id"]
            if account_id is not None and not conn.execute("SELECT 1 FROM xianyu_account WHERE id=? AND deleted=0",(account_id,)).fetchone():
                raise InventoryError("账号不存在",404)
            words=tuple(word.strip() for word in re.split(r"[,，|]",values["trigger_keyword"] or "") if word.strip())
            trigger=values.get("trigger") or TriggerPolicy(mode="payment_keyword" if words else "payment",keywords=words)
            if not isinstance(trigger,TriggerPolicy):
                trigger=TriggerPolicy.model_validate(trigger)
            plan=values.get("plan")
            if plan is None:
                card=values["delivery_mode"] in ("card","kami")
                plan=DeliveryPlan(sku_id=values.get("sku_id", ""),card_group_id=values["card_group_id"] if card else None,
                    parts=(DeliveryPart(kind="card" if card else "text",content=values["delivery_content"] or ("{卡密}" if card else "")),))
            elif not isinstance(plan,DeliveryPlan):
                plan=DeliveryPlan.model_validate(plan)
            variant=RuleVariant(sku_id=plan.sku_id,plan=plan,trigger=trigger,enabled=values["status"]==1)
            self.validate_variant(conn,variant)
            stamp=now()
            version=(original["config_version"]+1) if original else 1
            params=(account_id,values["rule_name"],goods_id,"card" if plan.card_group_id else "text",plan.card_group_id,
                    next((part.content for part in plan.parts if part.kind in ("text","card")),""),values["trigger_on_pay"],
                    "|".join(trigger.keywords),values["max_delivery_per_day"],values["status"],stamp,
                    plan.sku_id,version,encode(plan.model_dump()),encode(trigger.model_dump()))
            conflict=conn.execute("SELECT id FROM delivery_rule WHERE goods_id=? AND sku_id=? AND deleted=0 AND id!=?",(goods_id,plan.sku_id,rule_id or -1)).fetchone()
            if conflict:
                raise InventoryError("该商品 SKU 已有规则",409)
            if original:
                conn.execute("""UPDATE delivery_rule SET account_id=?,rule_name=?,goods_id=?,delivery_mode=?,card_group_id=?,
                    delivery_content=?,trigger_on_pay=?,trigger_keyword=?,max_delivery_per_day=?,status=?,updated_time=?,
                    sku_id=?,config_version=?,plan_json=?,trigger_json=? WHERE id=?""",(*params,rule_id))
            else:
                rule_id=conn.execute("""INSERT INTO delivery_rule(account_id,rule_name,goods_id,delivery_mode,card_group_id,
                    delivery_content,trigger_on_pay,trigger_keyword,max_delivery_per_day,status,updated_time,sku_id,
                    config_version,plan_json,trigger_json,created_time,deleted) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)""",(*params,stamp)).lastrowid
            for gid in {goods_id,original["goods_id"] if original else None}-{None}:
                self.project(conn,gid)
            return {"id":rule_id}

    def save_goods(self,goods_id,config:GoodsDeliveryConfig):
        with self.connection(write=True) as conn:
            goods=conn.execute("SELECT * FROM xianyu_goods WHERE id=? AND deleted=0",(goods_id,)).fetchone()
            if not goods:
                raise InventoryError("商品不存在",404)
            old=conn.execute("SELECT version FROM delivery_goods_config WHERE goods_id=?",(goods_id,)).fetchone()
            if config.expected_version is not None and config.expected_version != (old[0] if old else 0):
                raise InventoryError("配置已改变，请刷新后再保存",409)
            existing={r["sku_id"]:r for r in conn.execute("SELECT * FROM delivery_rule WHERE goods_id=? AND deleted=0",(goods_id,))}
            wanted={v.sku_id for v in config.variants}
            for variant in config.variants:
                self.validate_variant(conn,variant)
                prior=existing.get(variant.sku_id)
                card=variant.plan.card_group_id is not None
                data=(goods["account_id"],goods_id,goods["title"] or "商品规则","card" if card else "text",variant.plan.card_group_id,
                    next((part.content for part in variant.plan.parts if part.kind in ("card","text")),""),
                    "|".join(variant.trigger.keywords),int(variant.enabled),now(),variant.sku_id,
                    prior["config_version"]+1 if prior else 1,encode(variant.plan.model_dump()),encode(variant.trigger.model_dump()))
                if prior:
                    conn.execute("""UPDATE delivery_rule SET account_id=?,goods_id=?,rule_name=?,delivery_mode=?,card_group_id=?,
                        delivery_content=?,trigger_keyword=?,status=?,updated_time=?,sku_id=?,config_version=?,plan_json=?,trigger_json=? WHERE id=?""",(*data,prior["id"]))
                else:
                    conn.execute("""INSERT INTO delivery_rule(account_id,goods_id,rule_name,delivery_mode,card_group_id,delivery_content,
                        trigger_keyword,status,updated_time,sku_id,config_version,plan_json,trigger_json,created_time,deleted,trigger_on_pay,max_delivery_per_day)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,1,0)""",(*data,now()))
            for sku,row in existing.items():
                if sku not in wanted:
                    conn.execute("UPDATE delivery_rule SET deleted=1,updated_time=? WHERE id=?",(now(),row["id"]))
            self.project(conn,goods_id)
        return self.get_goods(goods_id)

    def get_goods(self,goods_id):
        with self.connection() as conn:
            if not conn.execute("SELECT 1 FROM xianyu_goods WHERE id=? AND deleted=0",(goods_id,)).fetchone():
                raise InventoryError("商品不存在",404)
            cached=conn.execute("SELECT version FROM delivery_goods_config WHERE goods_id=?",(goods_id,)).fetchone()
            return {"schemaVersion":2,"version":cached[0] if cached else 0,"variants":[self.decode(row).model_dump() for row in
                conn.execute("SELECT * FROM delivery_rule WHERE goods_id=? AND deleted=0 ORDER BY sku_id",(goods_id,))]}

    def resolve(self,account_id,item_id,sku_id):
        with self.connection() as conn:
            goods=conn.execute("""SELECT id FROM xianyu_goods WHERE account_id=? AND deleted=0 AND
                (external_goods_id=? OR goods_id=?)""",(account_id,item_id,item_id)).fetchall()
            if len(goods)!=1:
                raise InventoryError("商品标识无唯一匹配",409)
            rules=conn.execute("SELECT * FROM delivery_rule WHERE goods_id=? AND sku_id=? AND deleted=0 AND status=1",(goods[0][0],sku_id)).fetchall()
            if len(rules)!=1:
                raise InventoryError("SKU 无唯一启用规则",409)
            return goods[0][0],self.decode(rules[0])

    def has_keyword(self,account_id,item_id,text):
        if not item_id:
            return False
        with self.connection() as conn:
            rows=conn.execute("""SELECT r.* FROM delivery_rule r JOIN xianyu_goods g ON g.id=r.goods_id
                WHERE g.account_id=? AND g.deleted=0 AND (g.external_goods_id=? OR g.goods_id=?) AND r.deleted=0 AND r.status=1""",
                (account_id,item_id,item_id)).fetchall()
            return any(self.decode(row).trigger.mode=="payment_keyword" and text.strip() in self.decode(row).trigger.keywords for row in rows)

    def translate_goods_body(self,goods_id,body):
        if "variants" in body:
            return GoodsDeliveryConfig.model_validate(body)
        allowed={"timing","mode","cardGroupId","cardTemplate","header","content","footer","segmentSend","segments",
            "enabled","autoConfirmShipment","skuId","triggerMode","keywords","expectedVersion","accountId",
            "sourceId","sourceTitle","retryCount","alertThreshold","autoDisableOnLowStock"}
        if set(body)-allowed or body.get("timing","payDelivery")!="payDelivery":
            raise InventoryError("当前仅支持付款时机的直接组合内容，请使用规范配置，不能静默忽略未知字段")
        if body.get("accountId") is not None:
            with self.connection() as conn:
                owner=conn.execute("SELECT account_id FROM xianyu_goods WHERE id=? AND deleted=0",(goods_id,)).fetchone()
            if not owner or owner[0]!=body["accountId"]:
                raise InventoryError("商品不属于所选账号",409)
        kind=body.get("mode","text")
        if kind not in ("text","card","kami"):
            raise InventoryError("不支持的发货方式")
        if body.get("sourceId") not in (None,"") or body.get("sourceTitle") not in (None,""):
            raise InventoryError("旧货源库尚未迁移，请使用直接内容或规范组合配置")
        retries=body.get("retryCount",2)
        threshold=body.get("alertThreshold",0)
        if type(retries) is not int or not 0<=retries<=10 or type(threshold) is not int or not 0<=threshold<=10000:
            raise InventoryError("重试次数或库存阈值无效")
        parts=[]
        def boolean(key,default):
            value=body.get(key,default)
            if not isinstance(value,(int,bool)) or value not in (0,1):
                raise InventoryError("启用和自动确认开关必须明确为 true/false 或 0/1")
            return bool(value)
        def text_parts(text):
            if text is not None and not isinstance(text,str):
                raise InventoryError("发货文字必须为字符串")
            for part in (text or "").split("{分段}"):
                if part.strip():
                    parts.append(DeliveryPart(kind="text",content=part))
        text_parts(body.get("header"))
        if kind in ("card","kami"):
            parts.append(DeliveryPart(kind="card",content=body.get("cardTemplate") or "{卡密}"))
            text_parts(body.get("content"))
        elif body.get("segments") is not None:
            if not isinstance(body["segments"],list):
                raise InventoryError("组合段落必须为列表")
            for segment in body["segments"]:
                if not isinstance(segment,dict) or segment.get("type") not in ("text","image","link"):
                    raise InventoryError("无效的组合段落，保存已拒绝")
                parts.append(DeliveryPart(kind=segment["type"],content=segment.get("imageUrl") if segment["type"]=="image" else segment.get("content", "")))
        else:
            text_parts(body.get("content"))
        text_parts(body.get("footer"))
        sku=body.get("skuId", "")
        words=body.get("keywords",())
        if isinstance(words,str):
            words=tuple(w.strip() for w in re.split(r"[,，|]",words) if w.strip())
        trigger=TriggerPolicy(mode=body.get("triggerMode","payment"),keywords=words)
        variant=RuleVariant(sku_id=sku,enabled=boolean("enabled",False),trigger=trigger,
            plan=DeliveryPlan(sku_id=sku,card_group_id=body.get("cardGroupId") if kind in ("card","kami") else None,
                confirm_shipment=boolean("autoConfirmShipment",True),max_attempts=retries+1,
                low_stock_threshold=threshold,pause_on_low_stock=boolean("autoDisableOnLowStock",False),parts=tuple(parts)))
        original=self.get_goods(goods_id)
        variants=[RuleVariant.model_validate(v) for v in original["variants"] if v["skuId"]!=sku]
        variants.append(variant)
        return GoodsDeliveryConfig(variants=tuple(variants),expected_version=body.get("expectedVersion",original["version"]))
