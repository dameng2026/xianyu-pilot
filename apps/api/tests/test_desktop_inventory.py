"""Real SQLite/API regressions: transaction atomicity, stock and restart safety."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.config import Settings
from app.desktop_app import create_desktop_app
from app.desktop_storage import DesktopStore, MIGRATIONS
from app.services.desktop_inventory import DesktopInventory

BASE = "http://127.0.0.1:15177"
JSON = {"Content-Type": "application/json"}


@pytest.fixture
def config(tmp_path, monkeypatch):
    from app.core import paths
    monkeypatch.setattr(paths, "SOURCE_ROOT", tmp_path)
    return Settings(runtime_mode="desktop")


@pytest.fixture
def client(config):
    with TestClient(create_desktop_app(config), base_url=BASE, headers=JSON) as session:
        yield session


def data(response):
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["code"] == 200, payload
    return payload["data"]


def group(client, name="测试卡密"):
    return data(client.post("/api/cards", json={"groupName": name, "alertThreshold": 2}))


def test_warehouse_crud_contracts_counts_and_restart(client, config):
    gid = group(client)
    data(client.post(f"/api/cards/{gid}/items", json={"content": "card-a----password-a"}))
    batch = data(client.post(f"/api/cards/{gid}/items/batch", json={"items": [
        {"cardContent": "card-a", "password": "password-a"},
        {"content": "card-b"}, {"content": "card-b"}, {"content": "card-c"}]}))
    assert batch == {"successCount": 2, "duplicateCount": 2, "failCount": 0}
    assert client.post(f"/api/cards/{gid}/items", json={"content": "card-b"}).status_code == 409
    items = data(client.get(f"/api/cards/{gid}/items"))
    assert items["total"] == 3
    ids = {item["cardKey"]: item["id"] for item in items["records"]}
    item = ids["card-a"]
    data(client.post(f"/api/cards/{gid}/items/{item}/lock"))
    stock = data(client.get(f"/api/cards/{gid}/stats"))
    assert (stock["remainCount"], stock["lockedCount"], stock["usedCount"]) == (2, 1, 0)
    assert client.delete(f"/api/cards/{gid}/items/{item}").status_code == 409
    data(client.post(f"/api/cards/{gid}/items/{item}/reset"))
    data(client.post(f"/api/cards/{gid}/items/{item}/invalid"))
    assert data(client.get(f"/api/cards/{gid}/stats"))["invalidCount"] == 1
    assert data(client.get(f"/api/cards/{gid}/items", params={"status": 3}))["total"] == 1
    assert data(client.get("/api/cards", params={"keyword": "测试", "size": 1}))["total"] == 1
    exported = client.get(f"/api/cards/{gid}/export")
    assert exported.text == "card-a----password-a\ncard-b\ncard-c"
    assert exported.headers["cache-control"] == "no-store"
    assert exported.headers["x-content-type-options"] == "nosniff"
    data(client.put(f"/api/cards/{gid}", json={"groupName": "已修改", "remark": "本地"}))
    # A separate connection observes committed data; cached counters match it.
    restarted = DesktopInventory(config.sqlite_path)
    assert restarted.get_group(gid)["groupName"] == "已修改"
    with sqlite3.connect(config.sqlite_path) as conn:
        assert conn.execute("SELECT total_count,available_count,used_count FROM card_group WHERE id=?", (gid,)).fetchone() == (3, 2, 0)
    data(client.delete(f"/api/cards/{gid}/items/{ids['card-c']}"))
    assert data(client.get(f"/api/cards/{gid}/stats"))["totalCount"] == 2
    data(client.delete(f"/api/cards/{gid}"))
    assert client.get(f"/api/cards/{gid}").status_code == 404
    assert data(client.get("/api/cards"))["total"] == 0


def test_invalid_batch_and_storage_failure_rollback_entire_import(client, config):
    gid = group(client)
    invalid = client.post(f"/api/cards/{gid}/items/batch", json={"items": [{"content": "valid"}, {"content": "  "}]})
    assert invalid.status_code == 400
    assert data(client.get(f"/api/cards/{gid}/stats"))["totalCount"] == 0
    # Force a failure after the first INSERT to prove DB rollback, not just validation.
    with sqlite3.connect(config.sqlite_path) as conn:
        conn.execute("""CREATE TRIGGER fail_second BEFORE INSERT ON card_item WHEN NEW.card_key='fail'
                        BEGIN SELECT RAISE(ABORT,'simulated failure'); END""")
    response = client.post(f"/api/cards/{gid}/items/batch", json={"items": [{"content": "first"}, {"content": "fail"}]})
    assert response.status_code == 503
    assert "simulated failure" not in response.text
    assert data(client.get(f"/api/cards/{gid}/stats"))["totalCount"] == 0
    with sqlite3.connect(config.sqlite_path) as conn:
        assert conn.execute("SELECT total_count,available_count FROM card_group WHERE id=?", (gid,)).fetchone() == (0, 0)


def test_parallel_import_deduplicates_and_separates_groups(client, config):
    gid = group(client)
    other = group(client, "另一组")
    repo = DesktopInventory(config.sqlite_path)
    with ThreadPoolExecutor(max_workers=4) as executor:
        outcomes = list(executor.map(lambda _: repo.import_items(gid, [{"content": "one----secret"}]), range(4)))
    assert sum(result["successCount"] for result in outcomes) == 1
    assert sum(result["duplicateCount"] for result in outcomes) == 3
    data(client.post(f"/api/cards/{other}/items", json={"content": "one----secret"}))
    assert data(client.get(f"/api/cards/{gid}/stats"))["totalCount"] == 1
    # The database also enforces identity for writers outside the repository.
    with sqlite3.connect(config.sqlite_path) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO card_item(group_id,card_key,card_value,deleted) VALUES(?,?,?,0)", (gid, "one", "secret"))


def test_claimed_used_and_wrong_group_items_are_protected(client, config):
    gid, other = group(client), group(client, "其他")
    data(client.post(f"/api/cards/{gid}/items/batch", json={"items": [{"content": "used"}, {"content": "claimed"}]}))
    records = data(client.get(f"/api/cards/{gid}/items"))["records"]
    ids = {row["cardKey"]: row["id"] for row in records}
    with sqlite3.connect(config.sqlite_path) as conn:
        conn.execute("UPDATE card_item SET is_used=1,status=2,used_order_id=99 WHERE id=?", (ids["used"],))
        conn.execute("UPDATE card_item SET claim_token='pending-attempt' WHERE id=?", (ids["claimed"],))
    for item in ids.values():
        for action in ("reset", "lock", "invalid"):
            assert client.post(f"/api/cards/{gid}/items/{item}/{action}").status_code == 409
        assert client.delete(f"/api/cards/{gid}/items/{item}").status_code == 409
        assert client.post(f"/api/cards/{other}/items/{item}/reset").status_code == 404
    assert client.delete(f"/api/cards/{gid}").status_code == 409
    assert data(client.get(f"/api/cards/{gid}/usage"))["total"] == 1
    assert data(client.get(f"/api/cards/{gid}/stats"))["usedCount"] == 1
    assert data(client.get(f"/api/cards/{gid}/stats"))["remainCount"] == 0
    assert data(client.get(f"/api/cards/{gid}/items", params={"status": 1}))["total"] == 1


def test_group_edit_matches_vue_null_remark_and_preserves_omitted_settings(client):
    gid = data(client.post("/api/cards", json={"groupName": "原组", "cardType": "card_password",
        "remark": None, "costPrice": 2.5, "alertThreshold": 3, "cardPrefix": "卡号"}))
    data(client.post(f"/api/cards/{gid}/items", json={"content": "key----pass"}))
    data(client.put(f"/api/cards/{gid}", json={"groupName": "新组", "remark": None}))
    stored = data(client.get(f"/api/cards/{gid}"))
    assert (stored["cardType"], stored["costPrice"], stored["alertThreshold"], stored["cardPrefix"]) == ("card_password", 2.5, 3, "卡号")
    assert len(data(client.get("/api/cards/alerts"))) == 1
    assert client.put(f"/api/cards/{gid}", json={"groupName": "新组", "cardType": "unique"}).status_code == 409


def test_rule_crud_references_and_restart(config):
    with TestClient(create_desktop_app(config), base_url=BASE, headers=JSON) as client:
        gid = group(client)
        body = {"ruleName": "卡密规则", "deliveryMode": "card", "cardGroupId": gid, "matchKeywords": "关键词"}
        rid = data(client.post("/api/auto-delivery/rules", json=body))["id"]
        assert data(client.get("/api/auto-delivery/rules"))["records"][0]["status"] == 0
        assert client.delete(f"/api/cards/{gid}").status_code == 409
        assert client.post("/api/auto-delivery/rules", json={**body, "cardGroupId": 99999}).status_code == 404
        assert client.post("/api/auto-delivery/rules", json={**body, "accountId": 99999}).status_code == 404
        assert client.post("/api/auto-delivery/rules", json={"ruleName": "空文字"}).status_code == 422
        body = {"ruleName": "文字规则", "deliveryMode": "text", "replyContent": "测试内容", "matchKeywords": "", "status": 1}
        data(client.put(f"/api/auto-delivery/rules/{rid}", json=body))
        # Changing to text clears the former card reference.
        data(client.delete(f"/api/cards/{gid}"))
        assert client.put("/api/auto-delivery/rules/999", json=body).status_code == 404
    with TestClient(create_desktop_app(config), base_url=BASE, headers=JSON) as restarted:
        records = data(restarted.get("/api/auto-delivery/rules"))["records"]
        assert len(records) == 1
        assert records[0]["replyContent"] == "测试内容"
        assert records[0]["cardGroupId"] is None
        assert records[0]["matchKeywords"] == ""
        ready = restarted.get("/health/ready").json()
        assert ready["phase"] == "verified-delivery-config"
        assert ready["automaticDeliveryEnabled"] is ready["realPlatformConnections"] is False
        data(restarted.delete(f"/api/auto-delivery/rules/{rid}"))
        assert data(restarted.get("/api/auto-delivery/rules"))["total"] == 0


def test_local_boundary_validation_and_unadapted_routes(client):
    assert client.post("/api/cards", json={"groupName": "x"}, headers={"Origin": "https://outside.example"}).status_code == 403
    assert client.post("/api/cards", content="groupName=x", headers={"Content-Type": "application/x-www-form-urlencoded"}).status_code == 415
    assert client.post("/api/cards", json={"groupName": " "}).status_code == 422
    assert client.get("/api/cards", params={"size": 1000000}).status_code == 422
    assert client.get("/api/cards", params={"current": 10**30}).status_code == 422
    assert client.get(f"/api/cards/{10**30}").status_code == 422
    assert client.post("/api/cards/999/items", json={"content": "x"}).status_code == 404
    assert client.post("/api/cards/999/items/batch", json={"items": []}).status_code == 422
    for path in ("/api/auto-delivery/trigger", "/api/auto-delivery/recover", "/api/auto-delivery/rules/batch"):
        assert client.post(path, json={}).status_code == 503
    preview = data(client.post("/api/cards/import/validate", json={"items": [{"content": "x"}, {"content": ""}, {"content": "x"}]}))
    assert preview == {"total": 3, "valid": 2, "invalid": 1, "duplicateCount": 1}
    gid = group(client)
    data(client.post(f"/api/cards/{gid}/items", json={"content": "bodyless-test"}))
    item = data(client.get(f"/api/cards/{gid}/items"))["records"][0]["id"]
    # The original Axios client removes Content-Type when no body is present.
    data(client.post(f"/api/cards/{gid}/items/{item}/lock", headers={"Content-Type": "", "X-Request-Id": "web-test"}))


def test_upgrade_keeps_existing_stock_and_rejects_duplicates_without_data_loss(tmp_path):
    old_migrations = tmp_path / "old"
    old_migrations.mkdir()
    first = MIGRATIONS / "001_original_core.sql"
    (old_migrations / first.name).write_bytes(first.read_bytes())
    path = tmp_path / "upgrade.sqlite3"
    DesktopStore(path, old_migrations).initialize()
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO card_group(id,group_name,deleted) VALUES(1,'old',0)")
        conn.execute("INSERT INTO card_item(group_id,card_key,deleted) VALUES(1,'existing',0)")
    assert DesktopStore(path).initialize()["version"] == "005_delivery_safety.sql"
    assert DesktopStore(path).initialize()["current"]
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT card_key FROM card_item").fetchall() == [("existing",)]
        assert conn.execute("SELECT alert_threshold FROM card_group").fetchone() == (10,)
    duplicate_path = tmp_path / "duplicates.sqlite3"
    DesktopStore(duplicate_path, old_migrations).initialize()
    with sqlite3.connect(duplicate_path) as conn:
        conn.executemany("INSERT INTO card_item(group_id,card_key,deleted) VALUES(1,?,0)", [("duplicate",), ("duplicate",)])
    with pytest.raises(sqlite3.IntegrityError):
        DesktopStore(duplicate_path).initialize()
    with sqlite3.connect(duplicate_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM card_item").fetchone() == (2,)
        assert conn.execute("SELECT COUNT(*) FROM desktop_schema_history").fetchone() == (1,)
        assert "alert_threshold" not in {r[1] for r in conn.execute("PRAGMA table_info(card_group)")}


def test_optimized_inventory_wal_export_parsing_and_alerts(client, config):
    with sqlite3.connect(config.sqlite_path) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"

    gid = group(client, "解析测试")
    data(client.post(f"/api/cards/{gid}/items", json={"cardContent": "auto-key----auto-pass"}))
    items = data(client.get(f"/api/cards/{gid}/items"))["records"]
    assert len(items) == 1
    assert items[0]["cardKey"] == "auto-key"
    assert items[0]["cardValue"] == "auto-pass"

    rid = data(client.post("/api/auto-delivery/rules", json={
        "ruleName": "特产搜索规则", "deliveryMode": "text", "replyContent": "发货消息", "matchKeywords": "特产"
    }))["id"]
    hit = data(client.get("/api/auto-delivery/rules", params={"keyword": "特产"}))
    assert hit["total"] == 1
    miss = data(client.get("/api/auto-delivery/rules", params={"keyword": "数码"}))
    assert miss["total"] == 0

    healthy_gid = group(client, "充足分组")
    data(client.put(f"/api/cards/{healthy_gid}", json={"groupName": "充足分组", "alertThreshold": 1}))
    data(client.post(f"/api/cards/{healthy_gid}/items", json={"content": "item-1"}))
    data(client.post(f"/api/cards/{healthy_gid}/items", json={"content": "item-2"}))
    alert_groups = data(client.get("/api/cards/alerts"))
    alert_ids = [g["id"] for g in alert_groups]
    assert healthy_gid not in alert_ids
    assert gid in alert_ids
