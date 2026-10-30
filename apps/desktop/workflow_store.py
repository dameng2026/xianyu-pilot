"""Tenant-scoped workflow records and short, atomic state transitions."""
from contextlib import contextmanager
from dataclasses import asdict
import json
import sqlite3
from uuid import uuid4

from .regex_worker import extract
from .storage import Conflict, Store, now
from .vault import WindowsVault
from .workflow_models import AccountInput, ProductInput, ServiceInput, STATES, template_fields


TERMINAL = {"completed", "cancelled", "delivery_stopped"}
EARLY = {"awaiting_payment", "awaiting_parameters", "awaiting_confirmation", "queued_qq", "awaiting_delivery"}


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class WorkflowStore:
    def __init__(self, store: Store, tenant_id: str = "local", vault=None):
        self.store, self.tenant = store, tenant_id
        self.vault = vault or WindowsVault()

    @contextmanager
    def transaction(self):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            yield db

    def _get(self, db, kind, identity):
        # Table names come only from internal constants, never request parameters.
        tables = {"account": "wf_accounts", "service": "wf_services", "product": "wf_products", "task": "wf_tasks"}
        row = db.execute(f"SELECT * FROM {tables[kind]} WHERE tenant_id=? AND id=?", (self.tenant, identity)).fetchone()
        if row is None:
            raise Conflict("记录不存在或不属于当前工作区")
        return dict(row)

    def audit(self, db, entity, action, message):
        db.execute("INSERT INTO wf_audit(tenant_id,entity_id,action,message,created_at) VALUES (?,?,?,?,?)",
                   (self.tenant, entity, action, message, now()))

    @staticmethod
    def public_account(row):
        return {key: value for key, value in row.items() if key not in {"credential", "evidence", "tenant_id"}} | {
            "has_credential": bool(row.get("credential")), "evidence": json.loads(row["evidence"]),
            "is_simulated": row["driver"] == "mock",
        }

    def save_account(self, body: AccountInput, identity=None):
        from .qq_connector import validate_endpoint
        if body.driver == "onebot":
            validate_endpoint(body.endpoint)
        with self.transaction() as db:
            account_id = identity or str(uuid4())
            old = self._get(db, "account", account_id) if identity else None
            if old and (body.identity != old["identity"] or body.driver != old["driver"]):
                raise Conflict("QQ 身份和接入方式不能覆盖，请解绑后新增账号")
            credentials = json.loads(self.vault.open(f"{self.tenant}:{account_id}", old["credential"])) if old and old["credential"] else {}
            if body.credential is not None: credentials["api_token"] = body.credential
            if body.event_secret is not None: credentials["event_secret"] = body.event_secret
            credential = self.vault.seal(f"{self.tenant}:{account_id}", dump(credentials)) if any(credentials.values()) else None
            generation = old["generation"] + 1 if old else 1
            if old:
                db.execute("UPDATE wf_accounts SET name=?,endpoint=?,credential=?,status='unverified',verified=0,generation=?,evidence='{}',updated_at=? WHERE tenant_id=? AND id=?",
                           (body.name, body.endpoint, credential, generation, now(), self.tenant, account_id))
            else:
                db.execute("INSERT INTO wf_accounts(id,tenant_id,name,identity,driver,endpoint,credential,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                           (account_id, self.tenant, body.name, body.identity, body.driver, body.endpoint, credential, now()))
            self.audit(db, account_id, "account_saved", "用户登记或更新自己的 QQ 接入，等待重新验证")
            return self.public_account(self._get(db, "account", account_id))

    def account_private(self, identity):
        with self.store.connect() as db:
            row = self._get(db, "account", identity)
        credentials = json.loads(self.vault.open(f"{self.tenant}:{identity}", row["credential"])) if row["credential"] else {}
        return row, credentials

    def record_verification(self, identity, generation, evidence, ok):
        with self.transaction() as db:
            row = self._get(db, "account", identity)
            if row["generation"] != generation or row["status"] == "unbound":
                raise Conflict("账号配置已变化，请重新检测")
            status = "online" if ok else "unverified" if row["driver"] == "manual" else "offline"
            db.execute("UPDATE wf_accounts SET verified=?,status=?,evidence=?,updated_at=? WHERE tenant_id=? AND id=?",
                       (int(ok), status, dump(evidence), now(), self.tenant, identity))
            self.audit(db, identity, "verification", "接入检测通过（模拟）" if ok and row["driver"] == "mock" else "接入检测通过" if ok else "接入检测未通过，自动提交保持关闭")
            return self.public_account(self._get(db, "account", identity))

    def unbind(self, identity):
        with self.transaction() as db:
            self._get(db, "account", identity)
            db.execute("UPDATE wf_accounts SET status='unbound',verified=0,credential=NULL,generation=generation+1,updated_at=? WHERE tenant_id=? AND id=?", (now(), self.tenant, identity))
            self.audit(db, identity, "unbind", "用户解绑自己的账号并清除本应用保存的凭据；未切换到其他账号")
            for row in self._rows(db):
                account = row["snapshot"].get("account")
                if account and account["id"] == identity and not self.step(row, "qq")["state"] == "done" and row["state"] not in TERMINAL:
                    self._fail(db, row, "qq", "account_unbound", "账号已解绑，等待用户处理", unknown=row["state"] in {"qq_sending", "waiting_qq"})

    def save_service(self, body: ServiceInput, identity=None):
        with self.transaction() as db:
            account = self._get(db, "account", body.account_id)
            if account["status"] == "unbound":
                raise Conflict("不能绑定已解绑的账号")
            if account["driver"] == "onebot" and not body.target.startswith(account["identity"] + ":direct:"):
                raise Conflict("QQ 服务只允许该账号下的已核对好友私聊")
            if account["driver"] == "onebot" and body.allow_auto and body.result_mode != "manual_fill" and "request_id" not in template_fields(body.request_template):
                raise Conflict("自动匹配的请求模板必须包含 {request_id}，不回显编号的服务应使用手动回填")
            if account["driver"] == "onebot" and account["verified"]:
                targets = json.loads(account["evidence"]).get("friends", [])
                if body.target not in {item["conversation_id"] for item in targets}:
                    raise Conflict("目标未出现在该账号的已授权好友检测结果中")
            service_id = identity or str(uuid4())
            settings = body.model_dump(exclude={"name", "account_id", "enabled"})
            if identity:
                self._get(db, "service", identity)
                db.execute("UPDATE wf_services SET account_id=?,name=?,settings=?,enabled=?,version=version+1 WHERE tenant_id=? AND id=?",
                           (body.account_id, body.name, dump(settings), int(body.enabled), self.tenant, identity))
            else:
                db.execute("INSERT INTO wf_services(id,tenant_id,account_id,name,settings,enabled) VALUES (?,?,?,?,?,?)",
                           (service_id, self.tenant, body.account_id, body.name, dump(settings), int(body.enabled)))
            self.audit(db, service_id, "service_saved", "保存 QQ 服务版本；已有任务快照不变")
            return self._config(self._get(db, "service", service_id))

    @staticmethod
    def _config(row):
        return {k: v for k, v in row.items() if k not in {"settings", "tenant_id"}} | json.loads(row["settings"])

    def save_product(self, body: ProductInput, identity=None):
        with self.transaction() as db:
            if body.mode == "qq":
                service = self._config(self._get(db, "service", body.service_id))
                allowed = set(service["required_fields"]) | {"request_id", "order_id", "result"}
                if body.parameter_source == "none" and service["required_fields"]:
                    raise Conflict("该 QQ 服务需要参数，请选择参数来源")
                if body.parameter_source == "fixed" and any(not body.fixed_parameters.get(key, "").strip() for key in service["required_fields"]):
                    raise Conflict("固定参数尚未满足服务的必填字段")
            else:
                allowed = {"order_id", "request_id"}
            if template_fields(body.delivery_template) - allowed:
                raise Conflict("交付模板引用了未声明参数；直接发货只支持订单和请求编号")
            product_id = identity or str(uuid4())
            settings = dump(body.model_dump(exclude={"name", "external_id", "enabled"}))
            try:
                if identity:
                    self._get(db, "product", identity)
                    db.execute("UPDATE wf_products SET name=?,external_id=?,settings=?,enabled=?,version=version+1 WHERE tenant_id=? AND id=?",
                               (body.name, body.external_id, settings, int(body.enabled), self.tenant, identity))
                else:
                    db.execute("INSERT INTO wf_products(id,tenant_id,name,external_id,settings,enabled) VALUES (?,?,?,?,?,?)",
                               (product_id, self.tenant, body.name, body.external_id, settings, int(body.enabled)))
            except sqlite3.IntegrityError as exc:
                raise Conflict("该商品编号已配置，请编辑已有商品") from exc
            self.audit(db, product_id, "product_saved", "保存商品默认规则版本；已有任务快照不变")
            return self._config(self._get(db, "product", product_id))

    def _snapshot(self, db, product_id, *, mode=None, service_id=None, base_product=None):
        product = json.loads(dump(base_product)) if base_product is not None else self._config(self._get(db, "product", product_id))
        if not product["enabled"]:
            raise Conflict("商品默认规则已停用")
        if mode:
            product["mode"] = mode
            product["service_id"] = service_id if mode == "qq" else None
            # An explicit override must be reviewed, including the delivery text.
            product["execution_policy"] = "confirm"
            if mode == "direct":
                product["parameter_source"] = "none"
        snapshot = {"product": product, "service": None, "account": None}
        if product["mode"] == "qq":
            service = self._config(self._get(db, "service", product["service_id"]))
            if not service["enabled"]:
                raise Conflict("QQ 服务已停用")
            account = self._get(db, "account", service["account_id"])
            if account["status"] == "unbound":
                raise Conflict("QQ 账号已解绑")
            snapshot.update(service=service, account={key: account[key] for key in
                ("id", "name", "identity", "driver", "endpoint", "verified", "generation")})
        return snapshot

    @staticmethod
    def _steps(mode):
        return [{"kind": kind, "state": "pending", "attempts": 0, "operation_id": str(uuid4()), "receipt": None}
                for kind in (["qq"] if mode == "qq" else []) + ["delivery", "shipping"]]

    @staticmethod
    def step(row, kind):
        return next((step for step in row["steps"] if step["kind"] == kind), None)

    @staticmethod
    def _decode(record):
        return {k: v for k, v in dict(record).items() if k not in {"document", "tenant_id"}} | json.loads(record["document"])

    def _task(self, db, identity):
        return self._decode(self._get(db, "task", identity))

    def _rows(self, db, active=False):
        query = "SELECT * FROM wf_tasks WHERE tenant_id=?"
        if active:
            query += " AND (state NOT IN ('completed','cancelled','delivery_stopped') OR (state='delivery_stopped' AND json_extract(document,'$.steps[0].kind')='qq' AND json_extract(document,'$.steps[0].state')='waiting'))"
        return [self._decode(row) for row in db.execute(query + " ORDER BY created_at,id", (self.tenant,))]

    def _write(self, db, row, action, message):
        excluded = {"id", "order_key", "product_id", "state", "revision", "created_at", "updated_at"}
        doc = {key: value for key, value in row.items() if key not in excluded}
        changed = db.execute("UPDATE wf_tasks SET state=?,document=?,revision=revision+1,updated_at=? WHERE tenant_id=? AND id=? AND revision=?",
                             (row["state"], dump(doc), now(), self.tenant, row["id"], row["revision"])).rowcount
        if changed != 1:
            raise Conflict("任务已变化，请刷新后重新核对")
        row["revision"] += 1
        self.audit(db, row["id"], action, message)

    @staticmethod
    def _check_params(values):
        if len(dump(values)) > 5000 or any(len(key) > 40 or len(value) > 1000 for key, value in values.items()):
            raise Conflict("参数过多或过长")

    def _advance(self, row):
        product, service = row["snapshot"]["product"], row["snapshot"]["service"]
        if row.get("stop_delivery"):
            return "delivery_stopped"
        if not row["paid"]:
            return "awaiting_payment"
        if service and self.step(row, "qq")["state"] != "done":
            if any(not row["parameters"].get(name, "").strip() for name in service["required_fields"]):
                return "awaiting_parameters"
        needs_confirmation = product["execution_policy"] == "confirm" or (
            product["execution_policy"] == "threshold" and row["amount_fen"] >= product["approval_threshold"]
        ) or bool(service and (not service["allow_auto"] or not row["snapshot"]["account"]["verified"]))
        if (needs_confirmation or product["parameter_source"] == "extract_review") and not row["confirmed"]:
            return "awaiting_confirmation"
        if service and self.step(row, "qq")["state"] != "done":
            return "queued_qq"
        if service and (product["review_result"] or service["result_mode"] != "regex") and not row["result_approved"]:
            return "result_review"
        return "awaiting_delivery" if self.step(row, "delivery")["state"] != "done" else "awaiting_shipping"

    def create_order(self, body):
        self._check_params(body.parameters)
        with self.transaction() as db:
            old = db.execute("SELECT * FROM wf_tasks WHERE tenant_id=? AND order_key=?", (self.tenant, body.order_key)).fetchone()
            if old:
                old = self._decode(old)
                if old["product_id"] != body.product_id or old["amount_fen"] != body.amount_fen:
                    raise Conflict("同一订单编号对应不同商品或金额，拒绝覆盖")
                return old, False
            if body.parent_id:
                self._get(db, "task", body.parent_id)
            active = db.execute("SELECT count(*) FROM wf_tasks WHERE tenant_id=? AND state NOT IN ('completed','cancelled','delivery_stopped')", (self.tenant,)).fetchone()[0]
            if active >= 500:
                raise Conflict("当前有 500 个未结束任务，请先处理积压")
            snapshot = self._snapshot(db, body.product_id)
            product = snapshot["product"]
            parameters = dict(body.parameters)
            extraction_failed = False
            if product["parameter_source"] == "fixed":
                parameters = dict(product["fixed_parameters"])
            elif product["parameter_source"] in {"buyer_chat", "extract_review"} and body.buyer_text:
                pattern = product["parameter_pattern"]
                try:
                    parameters = extract(pattern, body.buyer_text) or {} if pattern else {"value": body.buyer_text}
                except ValueError:
                    parameters, extraction_failed = {}, True
            identity, stamp = str(uuid4()), now()
            doc = {"snapshot": snapshot, "parameters": parameters, "paid": body.paid,
                   "amount_fen": body.amount_fen, "confirmed": False, "result_approved": False,
                   "scenario": body.scenario, "parent_id": body.parent_id, "steps": self._steps(product["mode"]),
                   "result": None, "result_origin": None, "failure": None, "stop_delivery": False,
                   "manual_qq": bool(snapshot["account"] and (snapshot["account"]["driver"] == "manual" or not snapshot["account"]["verified"])),
                   "source": "seller_manual_order"}
            state = self._advance(doc)
            db.execute("INSERT INTO wf_tasks(id,tenant_id,order_key,product_id,state,document,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                       (identity, self.tenant, body.order_key, body.product_id, state, dump(doc), stamp, stamp))
            db.execute("INSERT INTO wf_revisions VALUES (?,?,?,?)", (identity, 1, dump(snapshot), stamp))
            self.audit(db, identity, "created", "手动登记订单并保存商品、服务、账号和模板版本快照；不是平台支付回调")
            if extraction_failed:
                self.audit(db, identity, "parameter_extraction_failed", "参数提取超时或失败，等待卖家手动填写；未提交 QQ")
            return self._task(db, identity), True

    def _freeze(self, db, row, message):
        service, account = row["snapshot"]["service"], row["snapshot"]["account"]
        if service:
            db.execute("INSERT INTO wf_channels VALUES (?,?,?,?,?) ON CONFLICT(tenant_id,account_id,target) DO UPDATE SET task_id=excluded.task_id,frozen_reason=excluded.frozen_reason",
                       (self.tenant, account["id"], service["target"], row["id"], message))

    def _fail(self, db, row, stage, code, message, unknown=False):
        row["failure"] = {"stage": stage, "code": code, "message": message, "unknown": unknown}
        row["state"] = "delivery_stopped" if row.get("stop_delivery") else "manual_required"
        step = self.step(row, stage)
        if step:
            step["state"] = "unknown" if unknown else "failed"
        if stage == "qq":
            self._freeze(db, row, message)
        self._write(db, row, "manual_required", message)

    def recover(self):
        with self.transaction() as db:
            for row in self._rows(db, active=True):
                stage = {"qq_sending": "qq", "delivering": "delivery", "confirming_shipping": "shipping"}.get(row["state"])
                if stage:
                    self._fail(db, row, stage, "interrupted", "启动恢复：操作中断，结果不明，禁止自动重发", unknown=True)
                    step = self.step(row, stage)
                    db.execute("UPDATE wf_attempts SET status='unknown',updated_at=? WHERE tenant_id=? AND id=?", (now(), self.tenant, step["operation_id"]))

    def resources(self):
        with self.store.connect() as db:
            accounts = [self.public_account(dict(row)) for row in db.execute("SELECT * FROM wf_accounts WHERE tenant_id=? ORDER BY updated_at DESC", (self.tenant,))]
            services = [self._config(dict(row)) for row in db.execute("SELECT * FROM wf_services WHERE tenant_id=? ORDER BY rowid DESC", (self.tenant,))]
            products = [self._config(dict(row)) for row in db.execute("SELECT * FROM wf_products WHERE tenant_id=? ORDER BY rowid DESC", (self.tenant,))]
            rows = [self._decode(row) for row in db.execute("SELECT * FROM wf_tasks WHERE tenant_id=? ORDER BY created_at DESC,id DESC LIMIT 100", (self.tenant,))]
            tasks = [self.summary(row) for row in rows]
            counts = dict(db.execute("SELECT state,count(*) FROM wf_tasks WHERE tenant_id=? GROUP BY state", (self.tenant,)))
        return {"workspace": {"id": self.tenant, "name": "我的本机工作区"}, "accounts": accounts, "services": services,
                "products": products, "tasks": tasks, "counts": counts, "states": STATES, "total": sum(counts.values())}

    @staticmethod
    def summary(row):
        return {k: row[k] for k in ("id", "product_id", "order_key", "state", "revision", "created_at", "updated_at")} | {
            "product_name": row["snapshot"]["product"]["name"], "mode": row["snapshot"]["product"]["mode"],
            "service_name": (row["snapshot"]["service"] or {}).get("name"), "failure": row["failure"],
            "delivery_channel": row["snapshot"]["product"]["delivery_channel"],
        }

    def detail(self, identity):
        with self.store.connect() as db:
            row = self._task(db, identity)
            row["logs"] = [dict(item) for item in db.execute("SELECT action,message,created_at FROM wf_audit WHERE tenant_id=? AND entity_id=? ORDER BY id LIMIT 200", (self.tenant, identity))]
            row["attempts"] = [{**dict(item), "receipt": json.loads(item["receipt"])} for item in db.execute("SELECT id,stage,attempt,status,receipt,updated_at FROM wf_attempts WHERE tenant_id=? AND task_id=? ORDER BY rowid", (self.tenant, identity))]
            row["channel_frozen"] = self._frozen_reason(db, row)
            row["allowed_actions"] = self.allowed(row, bool(row["channel_frozen"]))
            row["outbound_preview"] = self.render_delivery(row) if row["state"] in {"awaiting_delivery", "awaiting_shipping", "completed"} else None
            service = row["snapshot"]["service"]
            ready = service and all(row["parameters"].get(key, "").strip() for key in service["required_fields"])
            row["request_preview"] = self.render_request(row) if ready and row["state"] != "awaiting_payment" else None
            return row

    def _frozen_reason(self, db, row):
        service, account = row["snapshot"]["service"], row["snapshot"]["account"]
        if not service: return None
        channel = db.execute("SELECT frozen_reason FROM wf_channels WHERE tenant_id=? AND account_id=? AND target=? AND task_id=?",
                             (self.tenant, account["id"], service["target"], row["id"])).fetchone()
        return channel[0] if channel else None

    def allowed(self, row, channel_frozen=False):
        state = row["state"]
        out = []
        untouched = all(step["attempts"] == 0 for step in row["steps"])
        if state in EARLY and untouched:
            out += ["edit", "cancel"]
        if state == "awaiting_payment": out += ["pay"]
        if state in {"awaiting_parameters", "awaiting_confirmation"} and untouched and row["snapshot"]["service"] and row["snapshot"]["service"]["required_fields"]: out += ["parameters"]
        if state == "awaiting_confirmation": out += ["confirm"]
        if state == "queued_qq" and row["manual_qq"]: out += ["manual_sent"]
        if state == "waiting_qq" or state == "manual_required" and row["failure"]["stage"] == "qq" or (
            state == "delivery_stopped" and self.step(row, "qq") and self.step(row, "qq")["state"] == "waiting"
        ): out += ["manual_result"]
        if state == "result_review": out += ["approve", "manual_result"]
        if state == "awaiting_delivery" and row["snapshot"]["product"]["delivery_channel"] == "manual" or (
            state == "manual_required" and row["failure"]["stage"] == "delivery"
        ): out += ["confirm_delivery"]
        if state == "awaiting_shipping" or state == "manual_required" and row["failure"]["stage"] == "shipping": out += ["confirm_shipping"]
        if state not in TERMINAL and self.step(row, "delivery")["state"] != "done": out += ["stop_delivery"]
        if state == "manual_required":
            out += ["notify"]
            failure = row["failure"]
            limit = (row["snapshot"]["service"] or {}).get("retry_limit", 1)
            if not failure["unknown"] and self.step(row, failure["stage"])["attempts"] <= limit:
                out += ["retry"]
        if channel_frozen and row["snapshot"]["service"] and (state == "manual_required" or state in TERMINAL or self.step(row, "qq")["state"] == "done"): out += ["release_channel"]
        return out

    @staticmethod
    def render_request(row):
        return row["snapshot"]["service"]["request_template"].format_map({**row["parameters"], "request_id": row["id"], "order_id": row["order_key"]})

    @staticmethod
    def render_delivery(row):
        return row["snapshot"]["product"]["delivery_template"].format_map({**row["parameters"], "request_id": row["id"], "order_id": row["order_key"], "result": row["result"] or ""})

    def action(self, identity, body):
        self._check_params(body.parameters)
        with self.transaction() as db:
            row = self._task(db, identity)
            if row["revision"] != body.expected_revision:
                raise Conflict("任务状态已变化，请刷新详情后操作")
            if body.action not in self.allowed(row, bool(self._frozen_reason(db, row))):
                raise Conflict("当前状态不允许此操作；已发送的请求不能撤回")
            action, message = body.action, "卖家手动核对任务"
            if action == "pay": row["paid"] = True; row["state"] = self._advance(row); message = "卖家手动登记已付款（未查询闲鱼支付状态）"
            elif action == "parameters":
                row["parameters"] = dict(body.parameters); row["confirmed"] = False; row["state"] = self._advance(row); message = "卖家更新参数，提交前重新核对"
            elif action == "confirm":
                row["confirmed"] = True; row["state"] = self._advance(row); message = "卖家确认当前参数、目标和请求模板，允许本任务执行"
            elif action == "approve":
                row["result_approved"] = True; row["state"] = self._advance(row); message = "卖家审核结果通过，允许后续交付"
            elif action == "cancel": row["state"] = "cancelled"; message = "执行前取消任务，尚未向 QQ 或买家提交"
            elif action == "stop_delivery": row["stop_delivery"] = True; row["state"] = "delivery_stopped"; message = "已终止后续交付；已经发出的请求或消息不能撤销"
            elif action == "edit":
                if body.mode is None: raise Conflict("请选择新的处理方式")
                snapshot = self._snapshot(db, row["product_id"], mode=body.mode, service_id=body.service_id, base_product=row["snapshot"]["product"])
                product = snapshot["product"]
                if body.delivery_template is not None:
                    if not body.delivery_template.strip(): raise Conflict("交付内容不能为空")
                    product["delivery_template"] = body.delivery_template
                if template_fields(product["delivery_template"]) - ({"order_id", "request_id", "result"} | set(row["parameters"])):
                    raise Conflict("交付模板需要调整，请先修改商品模板再覆盖此任务")
                if body.mode == "direct" and "result" in template_fields(product["delivery_template"]):
                    raise Conflict("原模板依赖 QQ 结果，请先为商品设置直接交付内容")
                row.update(snapshot=snapshot, confirmed=False, steps=self._steps(body.mode), result=None, result_approved=False,
                           manual_qq=bool(snapshot["account"] and (snapshot["account"]["driver"] == "manual" or not snapshot["account"]["verified"])))
                row["state"] = self._advance(row)
                db.execute("INSERT INTO wf_revisions VALUES (?,?,?,?)", (identity, row["revision"] + 1, dump(snapshot), now()))
                message = "执行前显式变更任务规则并保留旧快照，等待重新确认"
            elif action == "manual_sent":
                step = self.step(row, "qq"); step["state"] = "waiting"; step["attempts"] += 1
                step["receipt"] = {"source": "seller_manual_record"}; row["state"] = "waiting_qq"
                import time
                row["qq_runtime"] = {"deadline": time.time() + row["snapshot"]["service"]["timeout_seconds"]}
                self._insert_attempt(db, row, "qq", "confirmed", step["receipt"])
                message = "卖家记录已在 QQ 手动提交；本应用未发送消息"
            elif action == "manual_result":
                if not body.text.strip(): raise Conflict("请输入人工核对的完整结果")
                row["result"] = body.text; row["result_origin"] = "seller_manual_record"; row["result_approved"] = False
                row["failure"] = None; self.step(row, "qq")["state"] = "done"
                row["state"] = "delivery_stopped" if row["stop_delivery"] else "result_review"; message = "卖家手动回填结果；已终止的交付不会恢复"
            elif action == "confirm_delivery":
                step = self.step(row, "delivery"); step["state"] = "done"
                step["receipt"] = {"source": "seller_manual_record"}; row["state"] = "awaiting_shipping"
                self._manual_receipt(db, row, "delivery")
                row["failure"] = None
                message = "卖家确认已手动回发买家，等待确认闲鱼发货"
            elif action == "confirm_shipping":
                if self.step(row, "delivery")["state"] != "done": raise Conflict("尚未交付，不能确认发货")
                step = self.step(row, "shipping"); step["state"] = "done"
                step["receipt"] = {"source": "seller_manual_record"}; row["state"] = "completed"
                self._manual_receipt(db, row, "shipping")
                row["failure"] = None
                message = "卖家确认已在闲鱼手动完成发货，任务结束"
            elif action == "retry":
                stage = row["failure"]["stage"]; step = self.step(row, stage); step["state"] = "pending"; step["operation_id"] = str(uuid4())
                row["failure"] = None; row["state"] = {"qq": "queued_qq", "delivery": "awaiting_delivery", "shipping": "awaiting_shipping"}[stage]
                if stage == "qq":
                    account, service = row["snapshot"]["account"], row["snapshot"]["service"]
                    db.execute("UPDATE wf_channels SET frozen_reason=NULL WHERE tenant_id=? AND account_id=? AND target=? AND task_id=?",
                               (self.tenant, account["id"], service["target"], identity))
                message = "卖家明确重试已确认未执行的阶段；已成功的阶段不重复执行"
            elif action == "release_channel":
                account, service = row["snapshot"]["account"], row["snapshot"]["service"]
                db.execute("UPDATE wf_channels SET frozen_reason=NULL,task_id=NULL WHERE tenant_id=? AND account_id=? AND target=? AND task_id=?",
                           (self.tenant, account["id"], service["target"], identity))
                message = "卖家核对后解除该目标通道暂停，旧任务不自动重发"
            elif action == "notify":
                # A preview is not an external message receipt.
                message = "生成安抚话术预览；未自动发给买家"
            self._write(db, row, action, message)
        return self.detail(identity)

    def _insert_attempt(self, db, row, stage, status, receipt=None):
        step = self.step(row, stage)
        db.execute("INSERT INTO wf_attempts(id,tenant_id,task_id,stage,attempt,status,receipt,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                   (step["operation_id"], self.tenant, row["id"], stage, step["attempts"], status, dump(receipt or {}), now()))

    def _manual_receipt(self, db, row, stage):
        step = self.step(row, stage)
        if step["attempts"] == 0:
            step["attempts"] = 1
            self._insert_attempt(db, row, stage, "manual_confirmed", step["receipt"])
        else:
            db.execute("UPDATE wf_attempts SET status='manual_confirmed',receipt=?,updated_at=? WHERE tenant_id=? AND id=?",
                       (dump(step["receipt"]), now(), self.tenant, step["operation_id"]))

    def claim(self):
        with self.transaction() as db:
            for row in sorted(self._rows(db, active=True), key=lambda item: item["state"] == "waiting_qq"):
                state, product = row["state"], row["snapshot"]["product"]
                if state == "queued_qq" and not row["manual_qq"]:
                    account, service = row["snapshot"]["account"], row["snapshot"]["service"]
                    channel = db.execute("SELECT * FROM wf_channels WHERE tenant_id=? AND account_id=? AND target=?", (self.tenant, account["id"], service["target"])).fetchone()
                    if channel and (channel["frozen_reason"] or channel["task_id"] not in (None, row["id"])):
                        continue
                    current = self._get(db, "account", account["id"])
                    if current["generation"] != account["generation"] or not current["verified"] or current["status"] != "online":
                        self._fail(db, row, "qq", "account_changed", "账号离线、已解绑或验证版本改变，等待用户处理")
                        continue
                    db.execute("INSERT INTO wf_channels VALUES (?,?,?,?,NULL) ON CONFLICT(tenant_id,account_id,target) DO UPDATE SET task_id=excluded.task_id", (self.tenant, account["id"], service["target"], row["id"]))
                    stage, next_state = "qq", "qq_sending"
                elif state in {"waiting_qq", "delivery_stopped"} and self.step(row, "qq") and self.step(row, "qq")["state"] == "waiting":
                    import time
                    if row["manual_qq"] or row["snapshot"]["service"]["result_mode"] == "manual_fill":
                        if time.time() >= row.get("qq_runtime", {}).get("deadline", float("inf")):
                            self._fail(db, row, "qq", "timeout", "人工回填等待超时，不能据此重发请求", unknown=True)
                        continue
                    return row, "receive"
                elif state == "awaiting_delivery" and product["delivery_channel"] == "mock":
                    stage, next_state = "delivery", "delivering"
                elif state == "awaiting_shipping" and product["shipping_policy"] == "auto" and product["delivery_channel"] == "mock":
                    stage, next_state = "shipping", "confirming_shipping"
                else:
                    continue
                step = self.step(row, stage); step["state"] = "executing"; step["attempts"] += 1
                row["state"] = next_state
                self._insert_attempt(db, row, stage, "prepared")
                self._write(db, row, "dispatch", "领取步骤：" + stage + "，先保存操作编号再执行")
                return row, stage
        return None

    def checkpoint(self, identity, runtime):
        with self.transaction() as db:
            row = self._task(db, identity)
            row["qq_runtime"] = runtime
            self._write(db, row, "checkpoint", "保存请求编号与接收游标；未记录凭据")

    def update_runtime(self, identity, stage, *, outcome, receipt=None, result=None, runtime=None):
        with self.transaction() as db:
            row = self._task(db, identity)
            step = self.step(row, stage)
            receipt = receipt or {}
            expected = {"waiting"} if stage == "qq" and result is not None else {"executing", "waiting"}
            if step["state"] not in expected:
                self.audit(db, identity, "late_receipt", "收到迟到回执，不覆盖已核对结果或恢复已暂停任务")
                return False
            step["receipt"] = receipt
            if runtime is not None: row["qq_runtime"] = runtime
            db.execute("UPDATE wf_attempts SET status=?,receipt=?,updated_at=? WHERE tenant_id=? AND id=?",
                       (outcome, dump(receipt), now(), self.tenant, step["operation_id"]))
            if outcome in {"unknown", "failed"}:
                self._fail(db, row, stage, "unknown" if outcome == "unknown" else "not_executed",
                           "操作结果不明，禁止自动重发" if outcome == "unknown" else "操作未执行或回复超时，等待人工核对", unknown=outcome == "unknown")
            elif stage == "qq" and result is None:
                step["state"] = "waiting"
                if not row["stop_delivery"]: row["state"] = "waiting_qq"
                self._write(db, row, "submitted", "QQ 发送已确认，等待对应目标的结果")
            else:
                step["state"] = "done"
                if stage == "qq":
                    row["result"], row["result_origin"] = result, "mock" if row["snapshot"]["account"]["driver"] == "mock" else "onebot"
                    row["state"] = self._advance(row)
                    account, service = row["snapshot"]["account"], row["snapshot"]["service"]
                    db.execute("UPDATE wf_channels SET task_id=NULL WHERE tenant_id=? AND account_id=? AND target=? AND task_id=? AND frozen_reason IS NULL", (self.tenant, account["id"], service["target"], identity))
                elif stage == "delivery": row["state"] = "delivery_stopped" if row["stop_delivery"] else "awaiting_shipping"
                else: row["state"] = "completed"
                self._write(db, row, "step_completed", "步骤已确认完成：" + stage + ("（本地模拟回执）" if receipt.get("simulated") else ""))

    def record_incoming(self, identity, event, rejection=None):
        with self.transaction() as db:
            self._get(db, "task", identity)
            if rejection:
                self.audit(db, identity, "filtered", rejection)
                return
            added = db.execute("INSERT OR IGNORE INTO wf_inbox VALUES (?,?,?,?)", (self.tenant, event.event_id, identity, dump(asdict(event)))).rowcount
            self.audit(db, identity, "received", "接收匹配来源的回复片段" if added else "重复事件已忽略")

    def partial_result(self, identity, text):
        with self.transaction() as db:
            row = self._task(db, identity)
            if self.step(row, "qq")["state"] == "waiting" and row.get("result") != text:
                row["result"], row["result_origin"] = text, "connector_pending"
                self._write(db, row, "partial_result", "已保存关联回复片段，等待卖家核对完整性；不会因短暂无消息就自动交付")

    def incoming(self, identity):
        from app.services.qq_bridge.contracts import Incoming
        with self.store.connect() as db:
            self._get(db, "task", identity)
            return [Incoming(**json.loads(row[0])) for row in db.execute("SELECT payload FROM wf_inbox WHERE tenant_id=? AND task_id=? ORDER BY rowid", (self.tenant, identity))]
