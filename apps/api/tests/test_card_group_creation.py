"""Original card API regression; isolated SQL storage, no accounts or live sends."""
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.api.v1.routes import delivery_workflow_compat as delivery


class CardSession:
    def __init__(self, path, *, hide_id=False):
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.create_function("NOW", 0, lambda: "2026-10-29 00:00:00")
        self.connection.execute("""CREATE TABLE card_group (
            id INTEGER PRIMARY KEY AUTOINCREMENT, group_name TEXT, group_type TEXT,
            card_prefix TEXT, password_prefix TEXT, cost_price REAL, suggested_price REAL,
            alert_threshold INTEGER, total_count INTEGER, used_count INTEGER,
            available_count INTEGER, remark TEXT, status INTEGER, deleted INTEGER,
            created_time TEXT, updated_time TEXT)""")
        self.hide_id = hide_id
        self.commits = 0

    async def execute(self, statement, params):
        result = self.connection.execute(str(statement), params)
        return SimpleNamespace(lastrowid=None) if self.hide_id else result

    async def commit(self):
        self.connection.commit()
        self.commits += 1

    async def rollback(self):
        self.connection.rollback()


@pytest.mark.parametrize("hide_id", [False, True])
def test_create_card_group_returns_committed_id_or_rolls_back(tmp_path, hide_id):
    session = CardSession(tmp_path / "cards.sqlite3", hide_id=hide_id)
    app = FastAPI()
    app.include_router(delivery.router, prefix="/api")
    app.dependency_overrides[delivery.get_db] = lambda: session
    app.dependency_overrides[delivery.get_current_user] = lambda: {"username": "test-admin"}
    try:
        with TestClient(app) as client:
            response = client.post("/api/cards", json={"groupName": "原发货流程的卡密组", "alertThreshold": 0})
            assert response.status_code == 200
            result = response.json()
            rows = session.connection.execute("SELECT id,group_name,alert_threshold FROM card_group").fetchall()
            if hide_id:
                assert result["code"] == 500 and result["data"] is None
                assert rows == [] and session.commits == 0
            else:
                assert result["code"] == 200 and isinstance(result["data"], int)
                assert rows == [(result["data"], "原发货流程的卡密组", 0)]
                assert session.commits == 1
                invalid = client.post("/api/cards", json={"groupName": "  "}).json()
                assert invalid["code"] == 400
                assert session.connection.execute("SELECT count(*) FROM card_group").fetchone()[0] == 1
    finally:
        session.connection.close()
