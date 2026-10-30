"""Workflow invariants with real SQLite and no real QQ/buyer operations."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "apps" / "api")]

from apps.desktop.app_factory import create_app
from apps.desktop.regex_worker import extract
from apps.desktop.storage import Conflict, Store
from apps.desktop.qq_connector import verify_account
from apps.desktop.vault import WindowsVault, CredentialError
from apps.desktop.workflow_models import AccountInput, ServiceInput, ProductInput, OrderInput, TaskAction
from apps.desktop.workflow_store import WorkflowStore
from apps.desktop.workflow_worker import WorkflowWorker


class TestVault:
    """Explicit test-only encryption, never selected by application runtime."""
    __test__ = False

    def __init__(self): self.cipher = Fernet(Fernet.generate_key())
    def seal(self, context, value): return self.cipher.encrypt(json.dumps([context, value]).encode())
    def open(self, context, value):
        saved, secret = json.loads(self.cipher.decrypt(value))
        assert saved == context
        return secret


@pytest.fixture
def repository(tmp_path):
    store = Store(tmp_path / "workflow.sqlite3")
    store.migrate()
    return WorkflowStore(store, vault=TestVault())


def setup_qq(repo, *, driver="mock", verified=True, service_settings=None, product_settings=None):
    account = repo.save_account(AccountInput(name="自己的账号", identity="demo-owner" if driver != "onebot" else "10001", driver=driver))
    if verified:
        repo.record_verification(account["id"], account["generation"], {"source": "test", "friends": []}, True)
    service = repo.save_service(ServiceInput(name="自有查询服务", account_id=account["id"], target="demo-bot",
        allow_auto=True, **(service_settings or {})))
    settings = dict(name="查询商品", external_id="QQ-1", mode="qq", service_id=service["id"],
                    parameter_source="seller", execution_policy="auto", delivery_template="结果：{result}", shipping_policy="auto")
    product = repo.save_product(ProductInput(**(settings | (product_settings or {}))))
    return account, service, product


def order(repo, product, key="ORDER-1", **kwargs):
    return repo.create_order(OrderInput(order_key=key, product_id=product["id"], **kwargs))[0]


def act(repo, identity, action, **kwargs):
    row = repo.detail(identity)
    return repo.action(identity, TaskAction(action=action, expected_revision=row["revision"], **kwargs))


async def drain(worker, limit=20):
    for _ in range(limit):
        if not await worker.process_one(): return
    raise AssertionError("Workflow did not settle within bounded steps")


@pytest.mark.asyncio
async def test_direct_product_needs_no_qq_and_shipping_follows_delivery(repository, monkeypatch):
    import apps.desktop.workflow_worker as module
    monkeypatch.setattr(module, "MockConnector", lambda *args: pytest.fail("Direct delivery accessed QQ"))
    product = repository.save_product(ProductInput(name="教程", external_id="DIRECT", execution_policy="auto", shipping_policy="auto", delivery_template="教程内容，订单 {order_id}"))
    task = order(repository, product)
    assert task["snapshot"]["account"] is None
    assert [step["kind"] for step in task["steps"]] == ["delivery", "shipping"]
    with pytest.raises(Conflict): act(repository, task["id"], "confirm_shipping")
    worker = WorkflowWorker(repository, delay=0)
    await worker.process_one()
    assert repository.detail(task["id"])["state"] == "awaiting_shipping"
    await worker.process_one()
    result = repository.detail(task["id"])
    assert result["state"] == "completed"
    assert [row["stage"] for row in result["attempts"]] == ["delivery", "shipping"]
    assert all(row["receipt"]["simulated"] for row in result["attempts"])
    assert not await worker.process_one()


def test_concurrent_order_dedup_and_conflicting_order_rejected(repository):
    product = repository.save_product(ProductInput(name="教程", external_id="DIRECT"))
    with ThreadPoolExecutor(max_workers=6) as executor:
        tasks = list(executor.map(lambda _: order(repository, product), range(12)))
    assert len({task["id"] for task in tasks}) == 1
    assert repository.resources()["total"] == 1
    with pytest.raises(Conflict): order(repository, product, amount_fen=100)


def test_tenant_foreign_references_and_task_reads_are_rejected(repository):
    with repository.store.connect() as db: db.execute("INSERT INTO wf_tenants VALUES ('another','Other owner')")
    other = WorkflowStore(repository.store, "another", vault=TestVault())
    account, service, product = setup_qq(repository)
    task = order(repository, product, parameters={"value": "DEMO"})
    with pytest.raises(Conflict): other.save_service(ServiceInput(name="他人的", account_id=account["id"], target="other"))
    with pytest.raises(Conflict): other.save_product(ProductInput(name="他人的", external_id="OTHER", mode="qq", service_id=service["id"]))
    with pytest.raises(Conflict): other.detail(task["id"])
    with pytest.raises(Conflict): other.action(task["id"], TaskAction(action="cancel", expected_revision=1))
    assert other.resources()["accounts"] == [] and other.resources()["tasks"] == []


def test_task_snapshot_versions_survive_product_and_service_edits(repository):
    account, service, product = setup_qq(repository)
    task = order(repository, product, parameters={"value": "DEMO"})
    repository.save_service(ServiceInput(name="新版服务", account_id=account["id"], target="different-bot", allow_auto=True), service["id"])
    repository.save_product(ProductInput(name="新版商品", external_id="QQ-1", delivery_template="新版内容"), product["id"])
    old = repository.detail(task["id"])
    assert old["snapshot"]["service"]["target"] == "demo-bot"
    assert old["snapshot"]["product"]["delivery_template"] == "结果：{result}"
    edited = act(repository, task["id"], "edit", mode="direct", delivery_template="本任务改为教程")
    assert edited["snapshot"]["service"] is None
    assert edited["snapshot"]["product"]["name"] == "查询商品"
    assert edited["state"] == "awaiting_confirmation"
    assert [step["kind"] for step in edited["steps"]] == ["delivery", "shipping"]
    with repository.store.connect() as db:
        assert db.execute("SELECT count(*) FROM wf_revisions WHERE task_id=?", (task["id"],)).fetchone()[0] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["normal", "split", "duplicate", "wrong_sender"])
async def test_qq_receives_result_then_review_then_delivery(repository, scenario):
    _, _, product = setup_qq(repository)
    task = order(repository, product, parameters={"value": "DEMO"}, scenario=scenario)
    worker = WorkflowWorker(repository, delay=0)
    await worker.process_one()
    assert repository.detail(task["id"])["state"] == "waiting_qq"
    with pytest.raises(Conflict): act(repository, task["id"], "edit", mode="direct", delivery_template="不允许改")
    await worker.process_one()
    result = repository.detail(task["id"])
    assert result["state"] == "result_review" and result["result"]
    assert not await worker.process_one()
    act(repository, task["id"], "approve")
    await drain(worker)
    result = repository.detail(task["id"])
    assert result["state"] == "completed"
    assert [attempt["stage"] for attempt in result["attempts"]] == ["qq", "delivery", "shipping"]
    if scenario == "duplicate": assert any("重复" in log["message"] for log in result["logs"])
    if scenario == "wrong_sender": assert any("来源" in log["message"] for log in result["logs"])


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["unknown", "timeout"])
async def test_uncertain_qq_never_retries_and_does_not_block_direct_delivery(repository, scenario):
    _, _, product = setup_qq(repository, service_settings={"retry_limit": 3})
    task = order(repository, product, parameters={"value": "DEMO"}, scenario=scenario)
    worker = WorkflowWorker(repository, delay=0)
    await drain(worker)
    failed = repository.detail(task["id"])
    assert failed["state"] == "manual_required" and failed["failure"]["unknown"]
    assert "retry" not in failed["allowed_actions"]
    second = order(repository, product, key="QQ-2", parameters={"value": "NEXT"})
    direct = repository.save_product(ProductInput(name="独立教程", external_id="DIRECT", execution_policy="auto", shipping_policy="auto"))
    direct_task = order(repository, direct, key="DIRECT-2")
    await drain(worker)
    assert repository.detail(direct_task["id"])["state"] == "completed"
    assert repository.detail(second["id"])["state"] == "queued_qq"
    act(repository, task["id"], "release_channel")
    await drain(worker)
    assert repository.detail(second["id"])["state"] == "result_review"
    assert len(repository.detail(task["id"])["attempts"]) == 1


@pytest.mark.asyncio
async def test_definitely_unsent_retry_is_explicit_and_bounded(repository):
    _, _, product = setup_qq(repository, service_settings={"retry_limit": 1})
    task = order(repository, product, parameters={"value": "DEMO"}, scenario="refused")
    worker = WorkflowWorker(repository, delay=0)
    await drain(worker)
    failed = repository.detail(task["id"])
    assert not failed["failure"]["unknown"] and "retry" in failed["allowed_actions"]
    first_operation = failed["attempts"][0]["id"]
    act(repository, task["id"], "retry")
    await drain(worker)
    result = repository.detail(task["id"])
    assert len(result["attempts"]) == 2 and result["attempts"][1]["id"] != first_operation
    assert "retry" not in result["allowed_actions"]


@pytest.mark.asyncio
async def test_stop_delivery_retains_inflight_result_without_delivery(repository):
    _, _, product = setup_qq(repository)
    task = order(repository, product, parameters={"value": "DEMO"})
    worker = WorkflowWorker(repository, delay=0)
    await worker.process_one()
    act(repository, task["id"], "stop_delivery")
    await drain(worker)
    result = repository.detail(task["id"])
    assert result["state"] == "delivery_stopped" and result["result"]
    assert len(result["attempts"]) == 1 and repository.step(result, "delivery")["state"] == "pending"


@pytest.mark.asyncio
async def test_unverified_manual_account_requires_human_and_manual_delivery_is_honest(repository):
    _, _, product = setup_qq(repository, driver="manual", verified=False,
                            product_settings={"delivery_channel": "manual", "shipping_policy": "manual"})
    task = order(repository, product, parameters={"value": "DEMO"})
    worker = WorkflowWorker(repository, delay=0)
    assert task["state"] == "awaiting_confirmation" and not await worker.process_one()
    act(repository, task["id"], "confirm")
    assert not await worker.process_one()
    act(repository, task["id"], "manual_sent")
    act(repository, task["id"], "manual_result", text="卖家核对的完整结果")
    act(repository, task["id"], "approve")
    assert not await worker.process_one()
    act(repository, task["id"], "confirm_delivery")
    result = act(repository, task["id"], "confirm_shipping")
    assert result["state"] == "completed"
    assert all(item["receipt"]["source"] == "seller_manual_record" for item in result["attempts"])


def test_parameters_payment_and_confirmation_policies(repository):
    account, service, product = setup_qq(repository)
    task = order(repository, product, paid=False)
    assert task["state"] == "awaiting_payment"
    assert act(repository, task["id"], "pay")["state"] == "awaiting_parameters"
    assert act(repository, task["id"], "parameters", parameters={"value": "DEMO"})["state"] == "queued_qq"
    fixed = repository.save_product(ProductInput(name="固定参数", external_id="FIXED", mode="qq", service_id=service["id"], parameter_source="fixed", fixed_parameters={"value": "FIXED"}, execution_policy="threshold", approval_threshold=100))
    assert order(repository, fixed, key="LOW", amount_fen=50)["state"] == "queued_qq"
    assert order(repository, fixed, key="HIGH", amount_fen=100)["state"] == "awaiting_confirmation"
    extracted = repository.save_product(ProductInput(name="提取确认", external_id="EXTRACT", mode="qq", service_id=service["id"], parameter_source="extract_review", parameter_pattern=r"编号:(?P<value>[A-Z0-9-]+)", execution_policy="auto"))
    task = order(repository, extracted, key="EXTRACT-1", buyer_text="编号:DEMO-1")
    assert task["parameters"] == {"value": "DEMO-1"} and task["state"] == "awaiting_confirmation"
    assert act(repository, task["id"], "confirm")["state"] == "queued_qq"
    with pytest.raises(Conflict): repository.save_product(ProductInput(name="无来源", external_id="BAD", mode="qq", service_id=service["id"], parameter_source="none"))


@pytest.mark.asyncio
async def test_recovery_and_late_results_do_not_resend_or_overwrite_manual_result(repository):
    _, _, product = setup_qq(repository)
    task = order(repository, product, parameters={"value": "DEMO"})
    repository.claim()
    repository.recover()
    row = repository.detail(task["id"])
    assert row["failure"]["unknown"] and row["state"] == "manual_required"
    act(repository, task["id"], "manual_result", text="用户已经核对的结果")
    repository.update_runtime(task["id"], "qq", outcome="confirmed", result="不应覆盖的迟到结果")
    assert repository.detail(task["id"])["result"] == "用户已经核对的结果"
    assert not await WorkflowWorker(repository, 0).process_one()
    direct = repository.save_product(ProductInput(name="教程", external_id="DIRECT", execution_policy="auto"))
    task = order(repository, direct, key="INTERRUPTED-DELIVERY")
    repository.claim()
    repository.recover()
    row = repository.detail(task["id"])
    assert row["failure"]["unknown"] and "retry" not in row["allowed_actions"]
    act(repository, task["id"], "confirm_delivery")
    assert act(repository, task["id"], "confirm_shipping")["state"] == "completed"


def test_unbind_and_revision_guards(repository):
    account, _, product = setup_qq(repository)
    task = order(repository, product, parameters={"value": "DEMO"})
    act(repository, task["id"], "edit", mode="direct", delivery_template="教程")
    with pytest.raises(Conflict): repository.action(task["id"], TaskAction(action="cancel", expected_revision=1))
    second = order(repository, product, key="SECOND", parameters={"value": "DEMO"})
    repository.unbind(account["id"])
    assert repository.detail(second["id"])["state"] == "manual_required"
    assert repository.resources()["accounts"][0]["status"] == "unbound"
    assert not repository.resources()["accounts"][0]["has_credential"]


def test_template_validation_and_regex_is_bounded():
    with pytest.raises(ValidationError): ServiceInput(name="坏模板", account_id="a", target="b", request_template="{value.__class__}")
    with pytest.raises(ValidationError): ServiceInput(name="坏正则", account_id="a", target="b", result_pattern="(")
    with pytest.raises(ValidationError): ProductInput(name="不真实的自动发货", external_id="a", delivery_channel="manual", shipping_policy="auto")
    assert extract(r"编号:(?P<value>[A-Z0-9-]+)", "编号:DEMO-123") == {"value": "DEMO-123"}
    with pytest.raises(ValueError, match="超时|失败"): extract(r"(a+)+$", "a" * 1000 + "!")


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI is intentionally required")
def test_windows_credentials_encrypted_and_bound_to_record(repository):
    vault = WindowsVault()
    secret = "fixture-" + os.urandom(32).hex()
    blob = vault.seal("local:account", secret)
    assert secret.encode() not in blob and vault.open("local:account", blob) == secret
    with pytest.raises(CredentialError): vault.open("another:account", blob)
    repo = WorkflowStore(repository.store, vault=vault)
    account = repo.save_account(AccountInput(name="自己的接入器", identity="10001", driver="onebot", credential=secret))
    assert "credential" not in account
    assert repo.account_private(account["id"])[1]["api_token"] == secret
    with repo.store.connect() as db:
        assert secret.encode() not in db.execute("SELECT credential FROM wf_accounts WHERE id=?", (account["id"],)).fetchone()[0]


