"""Exercise the original model schema and the isolated desktop startup path."""
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "apps" / "api") not in sys.path:
    sys.path.insert(0, str(ROOT / "apps" / "api"))

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import text
from sqlalchemy.dialects import mysql, sqlite
from sqlalchemy.schema import CreateTable

from app.core.config import Settings
from app.core.database import DatabaseRuntime
from app.core.instance_lock import SingleInstance
from app.desktop_app import create_desktop_app
from app.desktop_storage import DesktopSchemaError, DesktopStore, MIGRATIONS
from app.models.entities import CardGroup, CardItem


@pytest.fixture
def desktop_config(tmp_path, monkeypatch):
    from app.core import paths
    monkeypatch.setattr(paths, "SOURCE_ROOT", tmp_path)
    return Settings(runtime_mode="desktop")


@pytest.mark.asyncio
async def test_original_models_persist_and_generate_ids_without_mysql(desktop_config):
    config = desktop_config
    assert DesktopStore(config.sqlite_path).initialize()["current"]
    runtime = DatabaseRuntime(config)
    async with runtime.session() as session:
        group = CardGroup(group_name="本地卡密", group_type="kami")
        session.add(group)
        await session.flush()
        card = CardItem(group_id=group.id, card_key="local-test-card")
        session.add(card)
        await session.commit()
        group_id, card_id = group.id, card.id
    await runtime.dispose()
    restarted = DatabaseRuntime(config)
    try:
        async with restarted.session() as session:
            assert (await session.get(CardGroup, group_id)).group_name == "本地卡密"
            assert (await session.get(CardItem, card_id)).group_id == group_id
            assert (await session.execute(text("PRAGMA foreign_keys"))).scalar() == 1
            assert (await session.execute(text("PRAGMA busy_timeout"))).scalar() == 5000
    finally:
        await restarted.dispose()
    assert "id INTEGER NOT NULL" in str(CreateTable(CardGroup.__table__).compile(dialect=sqlite.dialect()))
    assert "id BIGINT NOT NULL AUTO_INCREMENT" in str(CreateTable(CardGroup.__table__).compile(dialect=mysql.dialect()))
    with sqlite3.connect(config.sqlite_path) as connection:
        names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"xianyu_account", "xianyu_trade_order", "delivery_rule", "delivery_record", "card_item"} <= names
    assert not any(name.startswith("wf_") for name in names)


def test_migrations_reject_other_databases_and_modified_history(tmp_path):
    foreign = tmp_path / "foreign.sqlite3"
    with sqlite3.connect(foreign) as connection:
        connection.execute("CREATE TABLE existing_data(value TEXT)")
        connection.execute("INSERT INTO existing_data VALUES ('keep')")
    with pytest.raises(DesktopSchemaError, match="未修改"):
        DesktopStore(foreign).initialize()
    with sqlite3.connect(foreign) as connection:
        assert connection.execute("SELECT value FROM existing_data").fetchone() == ("keep",)
    store = DesktopStore(tmp_path / "app.sqlite3")
    first = store.initialize()
    assert store.initialize() == first
    with sqlite3.connect(store.path) as connection:
        connection.execute("UPDATE desktop_schema_history SET checksum='changed'")
    with pytest.raises(DesktopSchemaError, match="校验失败"):
        store.initialize()


def test_failed_migration_rolls_back_and_newer_database_is_preserved(tmp_path):
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    for original in MIGRATIONS.glob("*.sql"):
        (migrations / original.name).write_bytes(original.read_bytes())
    store = DesktopStore(tmp_path / "app.sqlite3", migrations)
    store.initialize()
    newer = migrations / "999_failure.sql"
    newer.write_text("CREATE TABLE partial(value TEXT);\nINVALID SQL;\n", encoding="utf-8")
    with pytest.raises(sqlite3.Error):
        store.initialize()
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("SELECT name FROM sqlite_master WHERE name='partial'").fetchone() is None
        assert connection.execute("SELECT count(*) FROM desktop_schema_history").fetchone() == (len(store.migrations()) - 1,)
    newer.write_text("CREATE TABLE future(value TEXT);\n", encoding="utf-8")
    store.initialize()
    with pytest.raises(DesktopSchemaError, match="版本较新"):
        DesktopStore(store.path).initialize()
    assert store.status()["version"] == "999_failure.sql"


def test_startup_lock_local_boundary_and_explicit_unavailable_routes(desktop_config):
    config = desktop_config
    with TestClient(create_desktop_app(config), base_url="http://127.0.0.1:15177") as client:
        ready = client.get("/health/ready")
        assert ready.status_code == 200
        assert ready.json()["storage"] == "sqlite"
        assert ready.json()["externalServicesRequired"] == []
        assert ready.json()["businessRoutesEnabled"] is True
        assert ready.json()["enabledBusinessModules"] == ["cards", "delivery-rules", "goods-config", "orders-read", "delivery-records-read"]
        assert client.post("/api/delivery/send", json={}).status_code == 503
        assert client.get("/api/desktop/status", headers={"origin": "https://outside.example"}).status_code == 403
        assert client.get("/health", headers={"host": "outside.example"}).status_code == 400
        with pytest.raises(RuntimeError, match="已有应用"):
            with SingleInstance(config.desktop_data_path):
                pass
    with SingleInstance(config.desktop_data_path):
        pass


def test_desktop_import_ignores_server_configuration_and_external_clients(tmp_path):
    env = {**os.environ, "RUNTIME_MODE": "desktop", "DESKTOP_DATA_DIR": str(tmp_path / "ignored"),
           "MYSQL_PORT": "invalid", "REDIS_PORT": "invalid", "APP_ENV": "production",
           "MYSQL_PASSWORD_FILE": str(tmp_path / "missing-secret"),
           "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    script = """
import sys
from pathlib import Path
from app.core import paths
paths.SOURCE_ROOT = Path(sys.argv[1])
from fastapi.testclient import TestClient
from app.main import app
with TestClient(app, base_url='http://127.0.0.1:15177') as client:
    assert client.get('/health/ready').status_code == 200
assert not any(name in sys.modules for name in ('app.server_app', 'aiomysql', 'redis', 'app.migrations.runner'))
"""
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path)], env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "data" / "app.sqlite3").is_file()
    assert not (tmp_path / "ignored").exists()


def test_storage_stays_beside_source_program_when_cwd_or_environment_changes(tmp_path, monkeypatch):
    from app.core import paths
    from apps.desktop.runtime import default_data_dir
    program = tmp_path / "portable"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setattr(paths, "SOURCE_ROOT", program)
    monkeypatch.chdir(elsewhere)
    for name in ("DESKTOP_DATA_DIR", "XYA_DESKTOP_DATA_DIR", "LOCALAPPDATA", "XDG_DATA_HOME", "UPLOADS_DIR", "STORAGE_DIR"):
        monkeypatch.setenv(name, str(elsewhere))
    config = Settings(runtime_mode="desktop", desktop_data_dir=str(elsewhere))
    assert config.desktop_data_path == program / "data"
    assert config.sqlite_path == program / "data" / "app.sqlite3"
    assert config.uploads_path == program / "data" / "uploads"
    assert Path(config.STORAGE_DIR) == program / "data"
    assert default_data_dir() == program / "data" / "demo"
    assert not (elsewhere / "data").exists()


def test_packaged_storage_uses_executable_location_instead_of_extraction_directory(tmp_path, monkeypatch):
    from app.core import paths
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "portable" / "XianyuPilot.exe"))
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "extracted-bundle"), raising=False)
    assert paths.data_directory() == tmp_path / "portable" / "data"


def test_personal_cleanup_preserves_core_and_optional_ai_routes():
    from app.api.v1.api import api_router
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(api_router)
    paths = set(app.openapi()["paths"])
    assert not any(path.startswith(("/ads", "/feedback", "/carousel", "/home/carousel")) for path in paths)
    assert any("cards" in path for path in paths)
    assert any("order" in path for path in paths)
    assert any("ai" in path or "auto-reply" in path for path in paths)
    from app.services.personal_content import about_content
    assert about_content()["heroBadgeText"] == "个人使用"


def test_desktop_runtime_and_launcher_importable_without_pythonpath():
    repo_root = Path(__file__).resolve().parents[3]
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    script = """
import sys
from pathlib import Path
repo = Path(sys.argv[1])
sys.path.insert(0, str(repo))
import apps.desktop.runtime
import apps.api.run_desktop
assert apps.desktop.runtime.default_data_dir() is not None
assert apps.api.run_desktop.CURRENT_DIR.is_dir()
"""
    result = subprocess.run([sys.executable, "-c", script, str(repo_root)], env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr

