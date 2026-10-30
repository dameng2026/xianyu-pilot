"""Project-owned stateless Streamable HTTP MCP business gateway.

Explicit per-product grants; no raw QQ sending or arbitrary account/target tools.
"""
import hashlib
import json
import secrets
import time
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from pydantic import Field, ValidationError

from .storage import Conflict
from .workflow_models import StrictModel, OrderInput, TaskAction
from .workflow_store import WorkflowStore, dump


class GrantInput(StrictModel):
    name: str = Field(min_length=1, max_length=60)
    product_ids: list[str] = Field(default_factory=list, max_length=50)
    allow_create: bool = False
    allow_actions: bool = False
    expires_days: int = Field(default=7, ge=1, le=30)


class Empty(StrictModel):
    pass


class TaskID(StrictModel):
    task_id: str = Field(min_length=1, max_length=80)


class ChangeTask(TaskID):
    expected_revision: int = Field(ge=1)
    parameters: dict[str, str] = Field(default_factory=dict, max_length=12)


class NativeMCP:
    VERSIONS = {"2025-03-26", "2025-06-18", "2025-11-25"}
    READ = {
        "get_workspace_status": ("查看授权商品范围内的任务状态；不返回账号凭据", Empty),
        "list_products": ("列出本连接被明确授权的商品规则", Empty),
        "list_qq_services": ("列出授权商品引用的处理服务；不返回 QQ 接入凭据", Empty),
        "list_tasks": ("查看授权商品最近的任务状态", Empty),
        "get_task": ("查看授权商品任务的快照、结果和操作记录", TaskID),
    }
    WRITES = {
        "update_task_parameters": ("执行前更新参数，随后按商品策略决定是否执行", ChangeTask),
        "confirm_task": ("确认当前任务快照，可能向其固定 QQ 好友提交请求或交付内容", ChangeTask),
        "cancel_task": ("只取消尚未执行的任务；已发送请求不能撤回", ChangeTask),
        "stop_task_delivery": ("终止任务后续交付；不撤回已发送内容", ChangeTask),
    }

    def __init__(self, repository, port):
        self.repo, self.port = repository, port

    def grants(self):
        with self.repo.store.connect() as db:
            return [{**dict(row), "scope": json.loads(row["scope"])} for row in db.execute(
                "SELECT id,name,scope,expires,revoked FROM wf_mcp_grants WHERE tenant_id=? ORDER BY rowid DESC", (self.repo.tenant,))]

    def create_grant(self, body: GrantInput):
        with self.repo.transaction() as db:
            if not body.product_ids: raise Conflict("请选择本连接可以访问的商品，不默认开放全部商品")
            for identity in body.product_ids: self.repo._get(db, "product", identity)
            token, identity = secrets.token_urlsafe(40), str(uuid4())
            scope = body.model_dump(exclude={"name", "expires_days"})
            db.execute("INSERT INTO wf_mcp_grants(id,tenant_id,name,token_hash,scope,expires) VALUES (?,?,?,?,?,?)",
                       (identity, self.repo.tenant, body.name, hashlib.sha256(token.encode()).hexdigest(), dump(scope), time.time() + body.expires_days * 86400))
            self.repo.audit(db, identity, "mcp_grant_created", "用户创建按商品授权的 MCP 凭据；仅保存摘要")
        return {"id": identity, "token": token, "url": f"http://127.0.0.1:{self.port}/mcp", "scope": scope,
                "note": "令牌仅显示本次，请妥善保存；支持本机 Streamable HTTP 与 Bearer Token"}

    def revoke(self, identity):
        with self.repo.transaction() as db:
            changed = db.execute("UPDATE wf_mcp_grants SET revoked=1 WHERE tenant_id=? AND id=?", (self.repo.tenant, identity)).rowcount
            if changed != 1: raise Conflict("MCP 连接不存在")
            self.repo.audit(db, identity, "mcp_revoked", "用户撤销 MCP 凭据；不会撤回已创建任务")

    def authorize(self, authorization):
        if not authorization.startswith("Bearer ") or len(authorization) > 200: return None
        digest = hashlib.sha256(authorization[7:].encode()).hexdigest()
        with self.repo.transaction() as db:
            row = db.execute("SELECT * FROM wf_mcp_grants WHERE tenant_id=? AND token_hash=? AND revoked=0 AND expires>?", (self.repo.tenant, digest, time.time())).fetchone()
            if row is None: return None
            row = dict(row)
            count = row["window_count"] + 1 if time.time() - row["window_start"] < 60 else 1
            start = row["window_start"] if count > 1 else time.time()
            if count > 120: raise Conflict("MCP 调用频率超限，请稍后再试")
            db.execute("UPDATE wf_mcp_grants SET window_start=?,window_count=? WHERE id=? AND tenant_id=?", (start, count, row["id"], self.repo.tenant))
            return {**row, "scope": json.loads(row["scope"])}

    def definitions(self, grant):
        definitions = dict(self.READ)
        if grant["scope"]["allow_create"]: definitions["create_task"] = ("按授权商品规则创建任务；订单编号幂等，可能触发已授权业务", OrderInput)
        if grant["scope"]["allow_actions"]: definitions.update(self.WRITES)
        return definitions

    def tools(self, grant):
        return [{"name": name, "description": description, "inputSchema": model.model_json_schema(),
                 "annotations": {"readOnlyHint": name in self.READ, "destructiveHint": name in self.WRITES,
                                 "idempotentHint": name in self.READ or name == "create_task", "openWorldHint": name not in self.READ}}
                for name, (description, model) in self.definitions(grant).items()]

    def call(self, grant, name, arguments):
        definitions = self.definitions(grant)
        if name not in definitions: raise Conflict("工具未获本连接授权")
        body = definitions[name][1].model_validate(arguments)
        allowed = set(grant["scope"]["product_ids"])
        if name in {"get_task", *self.WRITES}:
            row = self.repo.detail(body.task_id)
            if row["product_id"] not in allowed: raise Conflict("任务不在本连接的商品授权范围内")
            if name == "get_task": return row
            action = {"update_task_parameters": "parameters", "confirm_task": "confirm", "cancel_task": "cancel", "stop_task_delivery": "stop_delivery"}[name]
            result = self.repo.action(body.task_id, TaskAction(action=action, expected_revision=body.expected_revision, parameters=body.parameters))
        elif name == "create_task":
            if body.product_id not in allowed: raise Conflict("商品不在本连接授权范围内")
            if body.parent_id and self.repo.detail(body.parent_id)["product_id"] not in allowed: raise Conflict("关联任务不在授权范围内")
            row, created = self.repo.create_order(body)
            result = {"task": self.repo.summary(row), "created": created}
        else:
            resources = self.repo.resources()
            products = [item for item in resources["products"] if item["id"] in allowed]
            with self.repo.store.connect() as db:
                placeholders = ",".join("?" for _ in allowed)
                records = db.execute(f"SELECT * FROM wf_tasks WHERE tenant_id=? AND product_id IN ({placeholders}) ORDER BY created_at DESC,id DESC LIMIT 100", (self.repo.tenant, *allowed)).fetchall()
                tasks = [self.repo.summary(self.repo._decode(item)) for item in records]
            if name == "list_products": return {"products": products}
            if name == "list_tasks": return {"tasks": tasks}
            if name == "list_qq_services":
                ids = {item["service_id"] for item in products if item["mode"] == "qq"}
                return {"services": [{key: value for key, value in item.items() if key in {"id", "name", "version", "required_fields", "enabled"}} for item in resources["services"] if item["id"] in ids]}
            return {"authorized_products": len(products), "recent_tasks": len(tasks), "scope": "explicit_products", "real_xianyu_connected": False}
        with self.repo.transaction() as db:
            self.repo.audit(db, grant["id"], "mcp_tool", "MCP 业务调用：" + name)
        return result

    def dispatch(self, grant, payload):
        request_id = payload.get("id") if isinstance(payload, dict) else None
        def error(code, message): return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
        if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0" or not isinstance(payload.get("method"), str):
            return error(-32600, "Invalid Request"), 400
        if "id" not in payload:
            return None, 202
        if isinstance(request_id, bool) or not isinstance(request_id, (str, int)):
            return error(-32600, "Invalid request ID"), 400
        method, params = payload["method"], payload.get("params", {})
        if not isinstance(params, dict): return error(-32602, "Invalid params"), 200
        if method == "initialize":
            requested = params.get("protocolVersion")
            result = {"protocolVersion": requested if requested in self.VERSIONS else "2025-11-25",
                      "capabilities": {"tools": {"listChanged": False}}, "serverInfo": {"name": "xianyu-pilot-workflow", "version": "0.2.0"},
                      "instructions": "仅操作本连接授权的商品任务。真实平台行为以回执为准，未知结果不能重发。"}
        elif method == "ping": result = {}
        elif method == "tools/list": result = {"tools": self.tools(grant)}
        elif method == "tools/call":
            try:
                if set(params) - {"name", "arguments", "_meta"} or not isinstance(params.get("name"), str): raise Conflict("无效工具参数")
                data = self.call(grant, params["name"], params.get("arguments", {}))
                result = {"structuredContent": data, "content": [{"type": "text", "text": dump(data)}], "isError": False}
            except (Conflict, ValueError, ValidationError):
                result = {"content": [{"type": "text", "text": "工具执行失败：权限、参数或任务状态不满足要求，请在本地工作台核对。"}], "isError": True}
        else: return error(-32601, "Method not found"), 200
        return {"jsonrpc": "2.0", "id": request_id, "result": result}, 200

    def router(self):
        import asyncio
        router = APIRouter()

        @router.post("/mcp")
        async def endpoint(request: Request):
            try: grant = await asyncio.to_thread(self.authorize, request.headers.get("authorization", ""))
            except Conflict: return JSONResponse({"error": "rate_limited"}, status_code=429)
            if grant is None: return JSONResponse({"error": "unauthorized"}, status_code=401, headers={"WWW-Authenticate": "Bearer"})
            if request.headers.get("mcp-protocol-version", "2025-03-26") not in self.VERSIONS:
                return JSONResponse({"error": "unsupported_protocol_version"}, status_code=400)
            accept = request.headers.get("accept", "")
            if "application/json" not in accept or "text/event-stream" not in accept:
                return JSONResponse({"error": "accept_must_include_json_and_event_stream"}, status_code=406)
            try: payload = await request.json()
            except (ValueError, RecursionError): return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}, status_code=400)
            result, status = await asyncio.to_thread(self.dispatch, grant, payload)
            return Response(status_code=status) if result is None else JSONResponse(result, status_code=status)

        @router.get("/mcp")
        def no_stream(): return Response(status_code=405, headers={"Allow": "POST"})

        return router
