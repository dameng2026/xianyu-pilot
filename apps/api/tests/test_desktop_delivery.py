"""Offline fault tests on real SQLite original orders and warehouse tables."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import os

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.config import Settings
from app.core.database import DatabaseRuntime
from app.desktop_app import create_desktop_app
from app.desktop_inventory_api import GroupBody
from app.desktop_storage import DesktopStore
from app.services.desktop_delivery import DesktopDeliveryStore, DesktopDeliveryCoordinator
from app.services.desktop_delivery_contracts import MockPaidOrder, DeliveryPlan, DeliveryPart, StepResult
from app.services.desktop_inventory import InventoryError


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Network is forbidden in phase 1 fault tests")
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)


@pytest.fixture
def store(tmp_path):
    root = tmp_path / "program" / "data"
    root.mkdir(parents=True)
    (root / "assets").mkdir()
    (root / "assets" / "guide.png").write_bytes(b"mock image version one")
    DesktopStore(root / "app.sqlite3").initialize()
    repo = DesktopDeliveryStore(root / "app.sqlite3")
    with repo.connection(write=True) as conn:
        conn.execute("INSERT INTO xianyu_account(id,nickname,status,deleted) VALUES(1,'mock',1,0)")
        conn.execute("INSERT INTO xianyu_account(id,nickname,status,deleted) VALUES(2,'mock-other',1,0)")
        conn.execute("""INSERT INTO xianyu_goods(id,account_id,title,external_goods_id,deleted,auto_relist_enabled,has_snapshot)
                        VALUES(1,1,'测试商品','item-1',0,0,0)""")
        conn.execute("""INSERT INTO xianyu_goods(id,account_id,title,external_goods_id,deleted,auto_relist_enabled,has_snapshot)
                        VALUES(2,2,'其他账号商品','item-2',0,0,0)""")
    gid = repo.save_group(GroupBody(group_name="测试卡密").values())
    assert gid == 1
    repo.import_items(gid, [{"content": f"key-{i}----password-{i}"} for i in range(12)])
    return repo


def order(store, identity="order-1", quantity=1, sku="", **extra):
    return store.register_mock_order(MockPaidOrder(account_id=1, goods_id=1, external_order_id=identity,
        buyer_id="buyer-1", quantity=quantity, sku_id=sku, **extra))


def plan(*, sku="", instructions="使用说明", repeat="order", image=True, confirm=True):
    parts = [DeliveryPart(kind="text", content="感谢购买"),
             DeliveryPart(kind="card", content="卡号：{卡号}\n密码：{密码}\n完整：{卡密}"),
             DeliveryPart(kind="text", content=instructions, repeat=repeat)]
    if image:
        parts.append(DeliveryPart(kind="image", content="assets/guide.png"))
    parts.append(DeliveryPart(kind="link", content="https://example.invalid/guide 提取码：abcd"))
    return DeliveryPlan(sku_id=sku, card_group_id=1, confirm_shipment=confirm, parts=tuple(parts))


class Gateway:
    mode = "mock"

    def __init__(self, failures=None, confirm_results=None, hook=None):
        self.calls = []
        self.confirm_calls = []
        self.failures = failures or {}
        self.confirm_results = list(confirm_results or [])
        self.hook = hook

    def send(self, work):
        self.calls.append(work)
        if self.hook:
            self.hook(work)
        results = self.failures.get(work.kind, [])
        result = results.pop(0) if results else StepResult("confirmed", "mock:message-receipt")
        if isinstance(result, BaseException):
            raise result
        return result

    def confirm_shipment(self, work):
        self.confirm_calls.append(work)
        result = self.confirm_results.pop(0) if self.confirm_results else StepResult("confirmed", "mock:platform-receipt")
        if isinstance(result, BaseException):
            raise result
        return result


def rows(store, sql, params=()):
    with store.connection() as conn:
        return [dict(r) for r in conn.execute(sql, params)]


def test_multi_quantity_order_once_parts_and_individual_card_passwords(store):
    oid = order(store, quantity=3, sku="sku-a")
    did = store.prepare(oid, plan(sku="sku-a"))
    gateway = Gateway()
    result = DesktopDeliveryCoordinator(store, gateway).execute(did)
    assert result["workflowState"] == "completed"
    assert result["receiptMode"] == "mock"
    assert result["quantitySent"] == 3
    assert [w.kind for w in gateway.calls] == ["text", "card", "card", "card", "text", "image", "link"]
    assert len(gateway.confirm_calls) == 1
    for i, work in enumerate(w for w in gateway.calls if w.kind == "card"):
        assert f"卡号：key-{i}" in work.payload["content"]
        assert f"密码：password-{i}" in work.payload["content"]
        assert f"完整：key-{i}----password-{i}" in work.payload["content"]
    assert store.get_group(1, stats=True)["usedCount"] == 3
    assert len({r["card_id"] for r in rows(store, "SELECT * FROM delivery_card_reservation")}) == 3
    # Mock receipt cannot turn an original order into a platform-shipped order.
    assert rows(store, "SELECT order_status FROM xianyu_trade_order WHERE id=?", (oid,))[0]["order_status"] == 1
    assert store.prepare(oid, plan(sku="sku-a", instructions="改过的说明")) == did
    DesktopDeliveryCoordinator(store, gateway).execute(did)
    assert len(gateway.calls) == 7
    assert not any("password-" in str(r) for r in rows(store, "SELECT * FROM delivery_audit"))
    assert not any("password-" in json.dumps(r) for r in result["segments"])


def test_per_item_instructions_and_sku_mismatch(store):
    oid = order(store, quantity=2, sku="sku-b")
    with pytest.raises(InventoryError, match="SKU"):
        store.prepare(oid, plan())
    assert rows(store, "SELECT * FROM delivery_record") == []
    did = store.prepare(oid, plan(sku="sku-b", repeat="item"))
    gateway = Gateway()
    DesktopDeliveryCoordinator(store, gateway).execute(did)
    assert sum(w.payload["content"] == "使用说明" for w in gateway.calls) == 2


def test_stock_shortage_is_whole_order_stop_and_frozen_plan_survives_replenishment(store):
    oid = order(store, quantity=13)
    did = store.prepare(oid, plan(instructions="原说明"))
    gateway = Gateway()
    assert DesktopDeliveryCoordinator(store, gateway).execute(did)["workflowState"] == "stock_blocked"
    assert not gateway.calls and not gateway.confirm_calls
    assert rows(store, "SELECT * FROM delivery_card_reservation") == []
    assert rows(store, "SELECT * FROM delivery_segment") == []
    assert store.get_group(1, stats=True)["remainCount"] == 12
    store.import_items(1, [{"content": "extra----secret"}])
    assert store.prepare(oid, plan(instructions="新说明")) == did
    DesktopDeliveryCoordinator(store, gateway).execute(did)
    assert any(w.payload["content"] == "原说明" for w in gateway.calls)
    assert not any(w.payload["content"] == "新说明" for w in gateway.calls)


def test_parallel_duplicate_orders_and_distinct_orders_never_double_allocate(store):
    with ThreadPoolExecutor(max_workers=6) as pool:
        ids = list(pool.map(lambda _: order(store, quantity=2), range(6)))
    assert len(set(ids)) == 1
    with ThreadPoolExecutor(max_workers=6) as pool:
        tasks = list(pool.map(lambda _: store.prepare(ids[0], plan()), range(6)))
    assert len(set(tasks)) == 1
    other = order(store, "order-other", quantity=2)
    store.prepare(other, plan())
    reservations = rows(store, "SELECT * FROM delivery_card_reservation")
    assert len(reservations) == 4
    assert len({r["card_id"] for r in reservations}) == 4
    with pytest.raises(InventoryError, match="冲突"):
        order(store, quantity=3)


def test_different_accounts_may_share_external_order_id_without_colliding(store):
    first = order(store)
    second = store.register_mock_order(MockPaidOrder(account_id=2,goods_id=2,external_order_id="order-1",buyer_id="buyer-2"))
    assert first != second


def test_failure_mid_reservation_rolls_back_orders_record_cards_and_counts(store):
    oid = order(store, quantity=2)
    with store.connection(write=True) as conn:
        conn.execute("""CREATE TRIGGER fail_segment BEFORE INSERT ON delivery_segment WHEN NEW.position=1
                        BEGIN SELECT RAISE(ABORT,'fault after reservation'); END""")
    with pytest.raises(sqlite3.IntegrityError):
        store.prepare(oid, plan())
    assert rows(store, "SELECT * FROM delivery_record") == []
    assert rows(store, "SELECT * FROM delivery_card_reservation") == []
    assert rows(store, "SELECT * FROM delivery_segment") == []
    assert store.get_group(1, stats=True)["remainCount"] == 12
    assert not any(r["claim_token"] for r in rows(store, "SELECT * FROM card_item"))


def test_failed_image_retries_only_it_with_stable_operation_and_original_cards(store):
    did = store.prepare(order(store), plan())
    gateway = Gateway(failures={"image": [StepResult("not_sent", "mock:upload-refused", retry_safe=True)]})
    coordinator = DesktopDeliveryCoordinator(store, gateway)
    partial = coordinator.execute(did)
    assert partial["workflowState"] == "failed"
    assert partial["quantitySent"] == 1
    original_cards = rows(store, "SELECT * FROM delivery_card_reservation")
    assert coordinator.execute(did)["workflowState"] == "completed"
    assert [w.kind for w in gateway.calls] == ["text", "card", "text", "image", "image", "link"]
    images = [w for w in gateway.calls if w.kind == "image"]
    assert images[0].operation_id == images[1].operation_id
    assert rows(store, "SELECT * FROM delivery_card_reservation") == original_cards


@pytest.mark.parametrize("failure", [TimeoutError("unknown"), StepResult("unknown")])
def test_unknown_card_stays_locked_no_retry_and_requires_positive_evidence(store, failure):
    did = store.prepare(order(store), plan())
    gateway = Gateway(failures={"card": [failure]})
    coordinator = DesktopDeliveryCoordinator(store, gateway)
    result = coordinator.execute(did)
    assert result["workflowState"] == "needs_review"
    assert rows(store, "SELECT state FROM delivery_card_reservation")[0]["state"] == "unknown"
    coordinator.execute(did)
    assert len(gateway.calls) == 2
    assert store.recover_interrupted() == 0
    card = rows(store, "SELECT * FROM card_item WHERE claim_token IS NOT NULL")[0]
    assert card["status"] == 1
    assert store.get_group(1, stats=True)["remainCount"] == 11
    with pytest.raises(InventoryError):
        store.change_item(1, card["id"], "reset")
    unknown = next(s for s in result["segments"] if s["state"] == "unknown")
    with pytest.raises(InventoryError):
        store.reconcile_confirmed(did, unknown["operationId"], actor="", evidence="")
    store.reconcile_confirmed(did, unknown["operationId"], actor="seller", evidence="mock:verified-history-message")
    assert coordinator.execute(did)["workflowState"] == "completed"
    assert sum(w.kind == "card" for w in gateway.calls) == 1


def test_restart_converts_inflight_to_unknown_and_rejects_late_ack(store):
    did = store.prepare(order(store), plan())
    work = store.claim_next(did)
    assert work.kind == "text"
    restarted = DesktopDeliveryStore(store.path)
    assert restarted.recover_interrupted() == 1
    assert restarted.recover_interrupted() == 0
    with pytest.raises(InventoryError, match="过期"):
        store.finish(work, StepResult("confirmed", "late:receipt"))
    gateway = Gateway()
    assert DesktopDeliveryCoordinator(restarted, gateway).execute(did)["workflowState"] == "needs_review"
    assert not gateway.calls
    assert rows(store, "SELECT state FROM delivery_card_reservation")[0]["state"] == "unknown"


def test_database_failure_after_external_success_never_retries_on_restart(store):
    did = store.prepare(order(store), plan())
    with store.connection(write=True) as conn:
        conn.execute("""CREATE TRIGGER fail_ack BEFORE UPDATE ON delivery_step_attempt WHEN NEW.state='confirmed'
                        BEGIN SELECT RAISE(ABORT,'ack disk failure'); END""")
    gateway = Gateway()
    with pytest.raises(sqlite3.IntegrityError):
        DesktopDeliveryCoordinator(store, gateway).execute(did)
    assert len(gateway.calls) == 1
    assert store.detail(did)["segments"][0]["state"] == "sending"
    store.recover_interrupted()
    DesktopDeliveryCoordinator(store, gateway).execute(did)
    assert len(gateway.calls) == 1
    assert store.detail(did)["workflowState"] == "needs_review"


def test_confirmation_retry_only_touches_platform_step(store):
    did = store.prepare(order(store), plan())
    gateway = Gateway(confirm_results=[StepResult("not_sent", "mock:platform-rejected", retry_safe=True)])
    coordinator = DesktopDeliveryCoordinator(store, gateway)
    result = coordinator.execute(did)
    assert result["confirmationState"] == "failed"
    assert result["quantitySent"] == 1
    sent = list(gateway.calls)
    assert coordinator.execute(did)["workflowState"] == "completed"
    assert gateway.calls == sent
    assert len(gateway.confirm_calls) == 2
    assert gateway.confirm_calls[0].operation_id == gateway.confirm_calls[1].operation_id


def test_confirmation_unknown_and_restart_preserve_completed_message_segments(store):
    did = store.prepare(order(store), plan())
    gateway = Gateway(confirm_results=[TimeoutError("confirmation unknown")])
    coordinator = DesktopDeliveryCoordinator(store, gateway)
    result = coordinator.execute(did)
    assert result["workflowState"] == "needs_review"
    assert all(s["state"] == "confirmed" for s in result["segments"])
    assert store.get_group(1, stats=True)["usedCount"] == 1
    coordinator.execute(did)
    assert len(gateway.confirm_calls) == 1
    store.reconcile_confirmed(did, result["confirmationOperationId"], actor="seller", evidence="mock:order-confirmed-shipped")
    assert coordinator.execute(did)["workflowState"] == "completed"
    assert len(gateway.confirm_calls) == 1


def test_concurrent_execute_does_not_take_over_inflight_operation(store):
    did = store.prepare(order(store), plan())
    started, release = threading.Event(), threading.Event()
    def hook(work):
        if not started.is_set():
            started.set()
            assert release.wait(5)
    gateway = Gateway(hook=hook)
    coordinator = DesktopDeliveryCoordinator(store, gateway)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(coordinator.execute, did)
        assert started.wait(5)
        second = pool.submit(coordinator.execute, did)
        assert second.result(timeout=5)["workflowState"] == "delivering"
        release.set()
        assert first.result(timeout=5)["workflowState"] == "completed"
    assert len({w.operation_id for w in gateway.calls}) == len(gateway.calls)


def test_external_gateway_calls_hold_no_sqlite_write_transaction(store):
    did = store.prepare(order(store), plan())
    writes = []
    def hook(work):
        # This independent writer would time out if the caller held its write lock.
        with store.connection(write=True) as conn:
            conn.execute("UPDATE card_group SET remark='gateway in progress' WHERE id=1")
            writes.append(work.operation_id)
    assert DesktopDeliveryCoordinator(store, Gateway(hook=hook)).execute(did)["workflowState"] == "completed"
    assert writes


def test_asset_changed_and_unverified_identity_block_dispatch(store):
    did = store.prepare(order(store), plan())
    pinned=store.detail(did)["assetReferences"][0]
    (store.path.parent / pinned).write_bytes(b"corrupt frozen guide")
    gateway = Gateway()
    result = DesktopDeliveryCoordinator(store, gateway).execute(did)
    assert result["workflowState"] == "failed"
    assert not any(w.kind == "image" for w in gateway.calls)
    assert not gateway.confirm_calls
    assert next(s for s in result["segments"] if s["kind"] == "image")["retrySafe"] is False
    other = order(store, "other")
    with store.connection(write=True) as conn:
        conn.execute("UPDATE xianyu_trade_order SET verification_source='unverified' WHERE id=?", (other,))
    with pytest.raises(InventoryError, match="Mock"):
        store.prepare(other, plan())


def test_relative_assets_moving_program_and_snapshot_payloads(store, tmp_path):
    did = store.prepare(order(store), plan())
    # Close/checkpoint via SQLite backup; never copy a live SQLite main file alone.
    moved = tmp_path / "moved" / "data"
    shutil.copytree(store.path.parent / "assets", moved / "assets")
    with sqlite3.connect(store.path) as src, sqlite3.connect(moved / "app.sqlite3") as dest:
        src.backup(dest)
    relocated = DesktopDeliveryStore(moved / "app.sqlite3")
    gateway = Gateway()
    assert DesktopDeliveryCoordinator(relocated, gateway).execute(did)["workflowState"] == "completed"
    image = next(w for w in gateway.calls if w.kind == "image")
    assert image.payload["content"].startswith("assets/.snapshots/")
    assert store.detail(did)["workflowState"] == "ready"


@pytest.mark.parametrize("path", ["../secret.png", "assets/../../secret.png", "C:/secret.png", "assets/..\\secret.png"])
def test_bad_image_reference_never_reserves_inventory(store, path):
    invalid = DeliveryPlan(card_group_id=1, parts=(DeliveryPart(kind="card",content="{卡密}"),DeliveryPart(kind="image",content=path)))
    with pytest.raises(InventoryError):
        store.prepare(order(store), invalid)
    assert rows(store, "SELECT * FROM delivery_card_reservation") == []


def test_read_only_apis_and_startup_recovery_never_enable_real_sending(store, monkeypatch):
    from app.core import paths
    monkeypatch.setattr(paths, "SOURCE_ROOT", store.path.parent.parent)
    did = store.prepare(order(store), plan())
    store.claim_next(did)
    with TestClient(create_desktop_app(Settings(runtime_mode="desktop")), base_url="http://127.0.0.1:15177") as client:
        ready = client.get("/health/ready").json()
        assert ready["phase"] == "verified-delivery-config"
        assert ready["automaticDeliveryEnabled"] is ready["realPlatformConnections"] is False
        assert client.get("/api/orders").json()["data"]["total"] == 1
        assert client.get("/api/orders/1").json()["data"]["verificationSource"] == "mock"
        detail = client.get(f"/api/auto-delivery/records/{did}")
        assert detail.json()["data"]["workflowState"] == "needs_review"
        assert "password-" not in detail.text
        assert detail.headers["cache-control"] == "no-store"
        assert client.get("/api/auto-delivery/records?needsReview=true").json()["data"]["total"] == 1
        for path in ("/api/desktop/orders/mock", "/api/orders/1/manual-delivery", "/api/auto-delivery/trigger"):
            assert client.post(path, json={}).status_code == 503


@pytest.mark.asyncio
async def test_legacy_healing_and_aging_preserve_every_ambiguous_card(store, monkeypatch):
    from app.core import paths
    from app.services.realtime_delivery import heal_orphaned_card_claims
    from app.services.delivery_recovery import _resurrect_dead_letter_attempts
    monkeypatch.setattr(paths, "SOURCE_ROOT", store.path.parent.parent)
    with store.connection(write=True) as conn:
        conn.execute("CREATE TABLE realtime_delivery_attempt(id INTEGER PRIMARY KEY,state TEXT,created_time TEXT,retry_safe INTEGER)")
        for i, state in enumerate(("success", "failed", "unknown"), 1):
            conn.execute("INSERT INTO realtime_delivery_attempt VALUES(?,?, '2000-01-01',0)", (i, state))
            conn.execute("UPDATE card_item SET status=1,realtime_attempt_id=?,claim_token='old-claim' WHERE id=?", (i, i))
    before = rows(store, "SELECT * FROM card_item")
    runtime = DatabaseRuntime(Settings(runtime_mode="desktop"))
    try:
        async with runtime.session() as session:
            assert await heal_orphaned_card_claims(session) == 0
            assert await _resurrect_dead_letter_attempts(session, max_age_hours=1) == 0
            await session.commit()
    finally:
        await runtime.dispose()
    assert rows(store, "SELECT * FROM card_item") == before
    assert rows(store, "SELECT state,retry_safe FROM realtime_delivery_attempt WHERE id=3") == [{"state": "unknown", "retry_safe": 0}]


def test_real_gateway_cannot_be_installed_in_phase_one(store):
    gateway = Gateway()
    gateway.mode = "platform"
    with pytest.raises(ValueError, match="真实平台"):
        DesktopDeliveryCoordinator(store, gateway)


def test_real_process_death_after_mock_card_send_preserves_claim_and_prevents_resend(store):
    did = store.prepare(order(store), DeliveryPlan(card_group_id=1,parts=(DeliveryPart(kind="card",content="{卡密}"),)))
    program = r'''
import os, sys
from pathlib import Path
from app.core import paths
paths.SOURCE_ROOT = Path(sys.argv[1]).parent.parent
from app.core.portable_runtime import prepare_portable_runtime
root = prepare_portable_runtime()
from app.services.desktop_delivery import DesktopDeliveryStore, DesktopDeliveryCoordinator
class CrashGateway:
    mode = "mock"
    def send(self, work):
        with (root / "tmp" / "mock-external-receipt.txt").open("a", encoding="utf-8") as stream:
            stream.write(work.operation_id + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os._exit(23)
DesktopDeliveryCoordinator(DesktopDeliveryStore(Path(sys.argv[1])), CrashGateway()).execute(int(sys.argv[2]))
'''
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1]), "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run([sys.executable,"-c",program,str(store.path),str(did)],env=env,capture_output=True,text=True,timeout=15)
    assert result.returncode == 23, result.stderr
    receipt = store.path.parent / "tmp" / "mock-external-receipt.txt"
    assert len(receipt.read_text().splitlines()) == 1
    assert store.detail(did)["segments"][0]["state"] == "sending"
    assert store.recover_interrupted() == 1
    gateway = Gateway()
    assert DesktopDeliveryCoordinator(store,gateway).execute(did)["workflowState"] == "needs_review"
    assert not gateway.calls
    assert store.get_group(1,stats=True)["lockedCount"] == 1


def test_payment_eligibility_changed_after_reservation_stops_before_first_send(store):
    oid = order(store)
    did = store.prepare(oid,plan())
    with store.connection(write=True) as conn:
        conn.execute("UPDATE xianyu_trade_order SET order_status=0 WHERE id=?", (oid,))
    gateway = Gateway()
    assert DesktopDeliveryCoordinator(store,gateway).execute(did)["workflowState"] == "needs_review"
    assert not gateway.calls


def test_expired_cards_excluded_and_invalid_card_template_rolls_back_whole_plan(store):
    with store.connection(write=True) as conn:
        conn.execute("UPDATE card_item SET expire_time='2000-01-01' WHERE id=1")
    did = store.prepare(order(store),plan())
    assert rows(store,"SELECT card_id FROM delivery_card_reservation")[0]["card_id"] == 2
    store.import_items(1,[{"content":"no-password"}])
    with store.connection(write=True) as conn:
        conn.execute("UPDATE card_item SET status=3 WHERE id<13 AND claim_token IS NULL")
    bad = DeliveryPlan(card_group_id=1,parts=(DeliveryPart(kind="card",content="密码：{密码}"),))
    with pytest.raises(InventoryError,match="没有密码"):
        store.prepare(order(store,"password-required"),bad)
    assert len(rows(store,"SELECT * FROM delivery_card_reservation")) == 1


@pytest.mark.parametrize("change", ["UPDATE xianyu_account SET status=0 WHERE id=1",
                                  "UPDATE xianyu_trade_order SET total_amount='999.00' WHERE id=1",
                                  "UPDATE xianyu_trade_order_item SET quantity=2 WHERE order_id=1"])
def test_account_and_order_changes_suspend_existing_snapshot(store, change):
    did = store.prepare(order(store),plan())
    with store.connection(write=True) as conn:
        conn.execute(change)
    gateway = Gateway()
    assert DesktopDeliveryCoordinator(store,gateway).execute(did)["workflowState"] == "needs_review"
    assert not gateway.calls


def test_external_result_cannot_claim_retry_safety_without_positive_evidence():
    with pytest.raises(ValueError,match="evidence"):
        StepResult("not_sent",retry_safe=True)
    with pytest.raises(ValueError,match="definitely unsent"):
        StepResult("unknown",retry_safe=True)


def test_fixed_content_delivery_needs_no_card_group_and_can_leave_platform_confirmation_manual(store):
    did = store.prepare(order(store,quantity=3),DeliveryPlan(confirm_shipment=False,parts=(
        DeliveryPart(kind="text",content="教程链接"), DeliveryPart(kind="link",content="https://example.invalid/guide"))))
    gateway = Gateway()
    result = DesktopDeliveryCoordinator(store,gateway).execute(did)
    assert result["workflowState"] == "confirmation_pending"
    assert result["confirmationState"] == "manual_pending"
    assert result["quantitySent"] == 3
    assert len(gateway.calls) == 2
    assert not gateway.confirm_calls
    assert not rows(store,"SELECT * FROM delivery_card_reservation")
    assert store.get_group(1,stats=True)["remainCount"] == 12
    store.reconcile_confirmed(did,result["confirmationOperationId"],actor="seller",evidence="mock:manual-shipped-evidence")
    assert store.detail(did)["workflowState"] == "completed"


def test_delivery_detail_and_list_records_handle_missing_order_gracefully(store):
    did = store.prepare(order(store, quantity=1), DeliveryPlan(confirm_shipment=False, parts=(DeliveryPart(kind="text", content="测试文本"),)))
    with store.connection(write=True) as conn:
        conn.execute("DELETE FROM xianyu_trade_order WHERE id=(SELECT order_id FROM delivery_record WHERE id=?)", (did,))
    detail = store.detail(did)
    assert detail["orderNumber"] == ""
    assert detail["officialStatus"] == ""
    assert detail["officialCheckedAt"] is None
    page_data = store.list_records(current=1, size=10)
    assert any(r["id"] == did and r["orderNumber"] == "" for r in page_data["records"])
