"""Our own MCP gateway and replaceable QQ connector; no third-party program runs."""
import asyncio
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "apps" / "api")]
from apps.desktop.app_factory import create_app
from apps.desktop.mcp_server import NativeMCP, GrantInput
from apps.desktop.qq_connector import OneBotClient, ConnectorError, accept_event, verify_account, validate_endpoint
from apps.desktop.storage import Store, Conflict
from apps.desktop.workflow_models import AccountInput, ServiceInput, ProductInput, OrderInput, TaskAction
from apps.desktop.workflow_store import WorkflowStore
from apps.desktop.workflow_worker import WorkflowWorker


class FixtureVault:
    def __init__(self): self.cipher = Fernet(Fernet.generate_key())
    def seal(self, context, value): return self.cipher.encrypt(json.dumps([context, value]).encode())
    def open(self, context, value):
        scope, text = json.loads(self.cipher.decrypt(value)); assert scope == context; return text


@pytest.fixture
def repo(tmp_path):
    store = Store(tmp_path / "workflow.sqlite3"); store.migrate()
    return WorkflowStore(store, vault=FixtureVault())


def event_bytes(secret, **overrides):
    event = {"self_id": 10001, "time": int(time.time()), "post_type": "meta_event", "meta_event_type": "heartbeat", **overrides}
    raw = json.dumps(event).encode()
    return raw, "sha1=" + hmac.new(secret.encode(), raw, hashlib.sha1).hexdigest()


@pytest.fixture
def bridge():
    state = {"sent": [], "disconnect": False, "identity": 10001}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/get_login_info": data = {"user_id": state["identity"]}
            elif self.path == "/get_friend_list": data = [{"user_id": 20002, "nickname": "自己的目标好友"}]
            elif self.path == "/send_private_msg":
                state["sent"].append(body)
                if state["disconnect"]: self.close_connection = True; return
                data = {"message_id": 901}
            else: self.send_error(404); return
            payload = json.dumps({"status": "ok", "retcode": 0, "data": data}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(payload))); self.end_headers(); self.wfile.write(payload)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    state["endpoint"] = f"http://127.0.0.1:{server.server_port}"
    try: yield state
    finally: server.shutdown(); server.server_close(); thread.join(timeout=2)


def prepare(repo, bridge, result_mode="manual"):
    secret = os.urandom(24).hex()
    account = repo.save_account(AccountInput(name="自己的 QQ", identity="10001", driver="onebot", endpoint=bridge["endpoint"], credential=os.urandom(24).hex(), event_secret=secret))
    assert not verify_account(repo, account["id"])["verified"]  # API identity alone is not a ready event channel.
    raw, signature = event_bytes(secret)
    accept_event(repo, account["id"], raw, signature)
    assert verify_account(repo, account["id"])["verified"]
    service = repo.save_service(ServiceInput(name="自己的查询", account_id=account["id"], target="10001:direct:20002", allow_auto=True, result_mode=result_mode))
    product = repo.save_product(ProductInput(name="查询商品", external_id="QQ", mode="qq", service_id=service["id"], parameter_source="seller", execution_policy="auto", delivery_template="{result}"))
    row = repo.create_order(OrderInput(order_key="ORDER-1", product_id=product["id"], parameters={"value": "DEMO"}))[0]
    return account, product, row, secret


@pytest.mark.asyncio
async def test_connector_verifies_identity_and_matches_signed_event_without_auto_completion(repo, bridge):
    account, _, task, secret = prepare(repo, bridge)
    worker = WorkflowWorker(repo, 0)
    await worker.process_one()
    assert len(bridge["sent"]) == 1
    assert bridge["sent"][0]["user_id"] == 20002
    assert task["id"] in bridge["sent"][0]["message"][0]["data"]["text"]
    for key, sender, text, reply in [(1, 33333, task["id"] + " 错误来源", None), (2, 20002, "其他请求", None), (3, 20002, "已核对的第一段", "901")]:
        segments = [{"type": "text", "data": {"text": text}}]
        if reply: segments.insert(0, {"type": "reply", "data": {"id": reply}})
        raw, signature = event_bytes(secret, post_type="message", message_type="private", user_id=sender, message_id=key, message=segments)
        accept_event(repo, account["id"], raw, signature)
        accept_event(repo, account["id"], raw, signature)  # Replay does not duplicate the result.
    await worker.process_one()
    result = repo.detail(task["id"])
    assert result["state"] == "waiting_qq" and result["result"] == "已核对的第一段"
    assert result["result_origin"] == "connector_pending" and len(bridge["sent"]) == 1
    row = repo.action(task["id"], TaskAction(action="manual_result", expected_revision=result["revision"], text=result["result"]))
    assert row["state"] == "result_review"
    with repo.store.connect() as db:
        assert db.execute("SELECT count(*) FROM wf_connector_events").fetchone()[0] == 3


@pytest.mark.asyncio
async def test_connector_unknown_send_never_retries(repo, bridge):
    _, _, task, _ = prepare(repo, bridge)
    bridge["disconnect"] = True
    worker = WorkflowWorker(repo, 0)
    await worker.process_one()
    result = repo.detail(task["id"])
    assert result["state"] == "manual_required" and result["failure"]["unknown"]
    assert "retry" not in result["allowed_actions"]
    repo.recover()
    assert not await worker.process_one() and len(bridge["sent"]) == 1


def test_connector_rejects_foreign_identity_bad_signature_and_unsafe_endpoints(repo, bridge):
    account, _, _, secret = prepare(repo, bridge)
    raw, signature = event_bytes(secret)
    with pytest.raises(ConnectorError): accept_event(repo, account["id"], raw, signature + "bad")
    raw, signature = event_bytes(secret, self_id=99999)
    with pytest.raises(ConnectorError): accept_event(repo, account["id"], raw, signature)
    raw, signature = event_bytes(secret, time=1)
    with pytest.raises(ConnectorError): accept_event(repo, account["id"], raw, signature)
    bridge["identity"] = 99999
    assert not verify_account(repo, account["id"])["verified"]
    for address in ["http://example.com:3000", "http://localhost:3000", "http://127.0.0.1:3000/path", "http://u:p@127.0.0.1:3000", "http://127.0.0.1:3000?token=a"]:
        with pytest.raises(ConnectorError): validate_endpoint(address)


def app_client(repo, tmp_path):
    web = tmp_path / "web"; web.mkdir(); (web / "index.html").write_text('<meta content="__DESKTOP_CSRF__">', encoding="utf-8")
    return TestClient(create_app(repo.store, port=15179, web_dir=web, delay=0, vault=repo.vault), base_url="http://127.0.0.1:15179")


def test_native_mcp_handshake_tools_scopes_revocation_and_no_tenant_override(repo, tmp_path):
    allowed = repo.save_product(ProductInput(name="允许访问的商品", external_id="ALLOW"))
    other = repo.save_product(ProductInput(name="禁止访问的商品", external_id="DENY"))
    mcp = NativeMCP(repo, 15179)
    readonly = mcp.create_grant(GrantInput(name="仅读取", product_ids=[allowed["id"]]))
    writable = mcp.create_grant(GrantInput(name="允许任务操作", product_ids=[allowed["id"]], allow_create=True, allow_actions=True))
    with app_client(repo, tmp_path) as client:
        assert client.post('/mcp', json={}).status_code == 401
        headers = {"Authorization": "Bearer " + readonly["token"], "Accept": "application/json, text/event-stream"}
        init = client.post('/mcp', headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "fixture", "version": "1"}}})
        assert init.status_code == 200 and init.json()["result"]["serverInfo"]["name"] == "xianyu-pilot-workflow"
        assert client.post('/mcp', headers=headers, json={"jsonrpc": "2.0", "method": "notifications/initialized"}).status_code == 202
        tools = client.post('/mcp', headers=headers, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}).json()["result"]["tools"]
        assert "create_task" not in {tool["name"] for tool in tools}
        assert not {"send_qq_message", "manage_qq_group", "switch_account", "get_credentials"} & {tool["name"] for tool in tools}

        def call(name, arguments, auth=headers):
            return client.post('/mcp', headers=auth, json={"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": name, "arguments": arguments}}).json()["result"]
        products = call("list_products", {})["structuredContent"]["products"]
        assert [row["id"] for row in products] == [allowed["id"]]
        assert call("create_task", {"order_key": "ORDER", "product_id": allowed["id"]})["isError"]
        write_headers = {**headers, "Authorization": "Bearer " + writable["token"]}
        assert call("create_task", {"order_key": "DENY", "product_id": other["id"]}, write_headers)["isError"]
        assert call("create_task", {"order_key": "ORDER", "product_id": allowed["id"], "tenant_id": "other"}, write_headers)["isError"]
        first = call("create_task", {"order_key": "ORDER", "product_id": allowed["id"]}, write_headers)["structuredContent"]
        replay = call("create_task", {"order_key": "ORDER", "product_id": allowed["id"]}, write_headers)["structuredContent"]
        assert first["created"] and not replay["created"]
        assert len(call("list_tasks", {})["structuredContent"]["tasks"]) == 1
        assert client.post('/mcp', headers={**headers, "Origin": "https://evil.example"}, json={}).status_code == 403
        assert client.post('/mcp', headers={**headers, "Host": "evil.example"}, json={}).status_code == 403
        assert client.post('/mcp', headers={**headers, "MCP-Protocol-Version": "invalid"}, json={}).status_code == 400
        assert client.get('/mcp', headers=headers).status_code == 405
        mcp.revoke(readonly["id"])
        assert client.post('/mcp', headers=headers, json={}).status_code == 401
        with repo.store.connect() as db:
            stored = db.execute("SELECT token_hash FROM wf_mcp_grants WHERE id=?", (writable["id"],)).fetchone()[0]
        assert writable["token"] not in stored


def test_signature_event_endpoint_uses_its_own_auth_and_does_not_disable_ui_csrf(repo, bridge, tmp_path):
    account, _, _, secret = prepare(repo, bridge)
    with app_client(repo, tmp_path) as client:
        raw, signature = event_bytes(secret)
        target = f"/api/qq/events/{account['id']}"
        assert client.post(target, content=raw, headers={"Content-Type": "application/json"}).status_code == 403
        assert client.post(target, content=raw, headers={"Content-Type": "application/json", "X-Signature": signature}).status_code == 204
        assert client.post('/api/workflow/accounts', json={}).status_code == 401
        csrf = re.search(r'content="([^"]+)"', client.get('/').text)[1]
        assert client.post('/api/workflow/accounts', json={}).status_code == 403
        assert client.post('/api/workflow/accounts', headers={"Origin": "http://127.0.0.1:15179", "X-Demo-CSRF": csrf}, json={"name": "账号", "identity": "demo", "tenant_id": "other"}).status_code == 422
