"""Real SQLite + real ASGI boundary; no server settings, MySQL, Redis or QQ."""
import asyncio
from dataclasses import replace
from pathlib import Path
import re
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.services.qq_bridge.client_mock import MockConnector
from app.services.qq_bridge.contracts import Binding, Incoming, Query
from app.services.qq_bridge.result_matcher import match_result
from apps.desktop.app_factory import create_app
from apps.desktop.runtime import InstanceLock
from apps.desktop.storage import Conflict, Store
from apps.desktop.worker import DemoWorker


@pytest.fixture
def store(tmp_path):
    result = Store(tmp_path / "demo.sqlite3")
    result.migrate()
    return result


def event(query, **overrides):
    result = Incoming("event-1", "demo-self", "demo-conversation", "demo-bot", query.operation_id, "第一段", 1, 2)
    return replace(result, **overrides)


def test_matcher_requires_source_operation_and_all_parts():
    query, binding = Query("op-1", "虚构输入", "split"), Binding()
    first = event(query)
    assert match_result(binding, query, [first, first]) is None
    other = event(query, event_id="other", part=2, body="无关", sender_id="other-person")
    late = event(query, event_id="late", part=2, body="迟到", operation_id="old-op")
    assert match_result(binding, query, [first, other, late]) is None
    second = event(query, event_id="event-2", part=2, body="第二段")
    assert match_result(binding, query, [second, first, second]) == "第一段\n第二段"
    with pytest.raises(ValueError, match="冲突"):
        match_result(binding, query, [first, replace(first, event_id="conflict", body="不同内容")])
    with pytest.raises(ValueError, match="总数"):
        match_result(binding, query, [first, replace(second, total=3)])


def test_sqlite_migrations_idempotency_and_atomic_claim(store):
    store.migrate()
    first, created = store.create_task("same-request", "虚构输入", "normal")
    duplicate, created_twice = store.create_task("same-request", "虚构输入", "normal")
    assert created and not created_twice and first["id"] == duplicate["id"]
    with pytest.raises(Conflict):
        store.create_task("same-request", "更改的输入", "normal")
    store.create_task("next-request", "下一条", "normal")
    assert store.claim()["id"] == first["id"]
    assert store.claim() is None  # An in-flight sender owns this serial channel.
    with store.connect() as db:
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        db.execute("UPDATE schema_history SET checksum='modified'")
    with pytest.raises(RuntimeError, match="迁移校验失败"):
        store.migrate()


def test_migrations_reject_newer_database_and_partial_schema(tmp_path):
    store = Store(tmp_path / "newer.sqlite3")
    store.migrate()
    with store.connect() as db:
        db.execute("INSERT INTO schema_history VALUES ('999_future.sql','future')")
    with pytest.raises(RuntimeError, match="数据库版本"):
        store.migrate()
    broken = Store(tmp_path / "broken.sqlite3")
    with broken.connect() as db:
        db.execute("CREATE TABLE journal(existing INTEGER)")
    with pytest.raises(Exception):
        broken.migrate()
    with broken.connect() as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE name='tasks'").fetchone() is None
        assert db.execute("SELECT count(*) FROM schema_history").fetchone()[0] == 0


def test_os_lock_released_and_data_directory_independent(tmp_path):
    with InstanceLock(tmp_path):
        with pytest.raises(RuntimeError, match="已有演示服务"):
            with InstanceLock(tmp_path):
                pytest.fail("Second host acquired the same data directory")
        with InstanceLock(tmp_path / "separate"):
            pass
    with InstanceLock(tmp_path):
        pass


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["normal", "split", "duplicate", "wrong_sender", "refused", "unknown", "timeout"])
async def test_simulator_end_to_end_and_reopen(store, scenario):
    task, _ = store.create_task("request-1", "虚构订单 DEMO-1001", scenario)
    worker = DemoWorker(store, MockConnector(Binding(), delay=0), Binding())
    assert await worker.process_one()
    reopened = Store(store.path)
    reopened.migrate()
    detail = reopened.detail(task["id"])
    expected = {"refused": "failed_safe", "unknown": "unknown", "timeout": "timed_out"}.get(scenario, "completed")
    assert detail["status"] == expected
    if expected == "completed":
        assert "模拟结果" in detail["result"]
    if scenario == "duplicate":
        assert any("重复消息" in item["message"] for item in detail["journal"])
    if scenario == "wrong_sender":
        assert any("发送者不符" in item["message"] for item in detail["journal"])
        assert all(item.sender_id == "demo-bot" for item in reopened.incoming(task["id"]))
    assert bool(reopened.snapshot()["frozen_reason"]) == (scenario in {"unknown", "timeout"})


@pytest.mark.asyncio
async def test_restart_unknown_never_resends_waiting_receives_without_submit(store):
    first, _ = store.create_task("first", "第一条虚构输入", "normal")
    store.claim()  # Crash after sending boundary, before acknowledgement.
    queued, _ = store.create_task("second", "第二条虚构输入", "normal")
    store.recover()
    assert store.detail(first["id"])["status"] == "unknown"
    assert store.claim() is None
    store.resume()
    second = store.claim()
    assert second["id"] == queued["id"]
    store.transition(second["id"], "sending", "waiting_reply", "模拟已确认")
    store.recover()

    class NoResend(MockConnector):
        async def submit(self, query):
            pytest.fail("A waiting task must not be sent again after a restart")

    worker = DemoWorker(store, NoResend(Binding(), delay=0), Binding())
    await worker.process_one()
    assert store.detail(second["id"])["status"] == "completed"
    assert store.detail(first["id"])["status"] == "unknown"


def client_app(store, tmp_path):
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text('<html><meta name="desktop-csrf" content="__DESKTOP_CSRF__"></html>', encoding="utf-8")
    return create_app(store, port=15179, web_dir=web, delay=0)


def bootstrap(client):
    response = client.get("/")
    assert response.status_code == 200
    csrf = re.search(r'content="([^"]+)"', response.text)[1]
    assert "HttpOnly" in response.headers["set-cookie"]
    return {"Origin": "http://127.0.0.1:15179", "X-Demo-CSRF": csrf}


def test_local_api_boundary_validation_and_idempotent_submission(store, tmp_path):
    app = client_app(store, tmp_path)
    with TestClient(app, base_url="http://127.0.0.1:15179") as client:
        assert client.get("/health").json()["real_connections"] is False
        assert client.get("/api/state").status_code == 401
        assert client.get("/", headers={"Host": "malicious.example"}).status_code == 403
        assert client.get("/", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
        headers = bootstrap(client)
        state = client.get("/api/state")
        assert state.status_code == 200
        assert "frame-ancestors 'none'" in state.headers["content-security-policy"]
        body = {"client_request_id": "request-1", "text": "虚构输入", "scenario": "normal"}
        assert client.post("/api/tasks", json=body).status_code == 403
        assert client.post("/api/tasks", headers={**headers, "Origin": "https://other.example"}, json=body).status_code == 403
        assert client.post("/api/tasks", headers=headers, content="x" * 32769).status_code in {413, 415}
        assert client.post("/api/tasks", headers={**headers, "Content-Type": "application/json"}, content="x" * 32769).status_code == 413
        assert client.post("/api/tasks", headers=headers, json={**body, "target_qq": "other"}).status_code == 422
        assert client.post("/api/tasks", headers=headers, json={**body, "scenario": "real"}).status_code == 422
        assert client.post("/api/tasks", headers=headers, json={**body, "text": "  "}).status_code == 422
        assert client.post("/api/tasks", headers=headers, json={**body, "text": "x" * 501}).status_code == 422
        first = client.post("/api/tasks", headers=headers, json=body).json()
        duplicate = client.post("/api/tasks", headers=headers, json=body).json()
        assert first["task"]["id"] == duplicate["task"]["id"]
        assert not duplicate["created"]
        assert client.post("/api/tasks", headers=headers, json={**body, "text": "不同"}).status_code == 409
        assert client.get("/api/tasks/not-a-task").status_code == 404
        assert client.get("/api/state").json()["total"] == 1
    # Session credentials from the old process must not authorize a new host.
    with TestClient(app, base_url="http://127.0.0.1:15179") as restarted:
        restarted.cookies.update(client.cookies)
        assert restarted.get("/api/state").status_code == 401
        bootstrap(restarted)
        assert restarted.get("/api/state").json()["total"] == 1


def test_demo_import_does_not_load_server_database_or_configuration():
    script = """
import os, sys
os.environ['APP_ENV'] = 'production'
os.environ['MYSQL_HOST'] = 'unreachable.invalid'
sys.path[:0] = ['.', 'apps/api']
from apps.desktop.app_factory import create_app
for name in ['app.main', 'app.core.config', 'app.core.database', 'redis', 'aiomysql', 'pymysql']:
    assert name not in sys.modules, name
"""
    subprocess.run([sys.executable, "-c", script], cwd=ROOT, check=True, capture_output=True, timeout=20)


def test_page_shutdown_requires_session_and_preserves_records(store, tmp_path):
    store.create_task("saved-task", "虚构输入", "normal")
    stopped = []
    web = tmp_path / "shutdown-web"
    web.mkdir()
    (web / "index.html").write_text('<meta content="__DESKTOP_CSRF__">', encoding="utf-8")
    app = create_app(store, port=15179, web_dir=web, on_shutdown=lambda: stopped.append(True))
    with TestClient(app, base_url="http://127.0.0.1:15179") as client:
        assert client.post("/api/host/stop", json={}).status_code == 401
        headers = bootstrap(client)
        response = client.post("/api/host/stop", json={}, headers=headers)
        assert response.status_code == 200
        assert response.json()["records_retained"] is True
        assert stopped == [True]
    assert store.snapshot()["total"] == 1
