"""Small dedicated demo database. Does not adapt or migrate the server database."""
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

from app.services.qq_bridge.contracts import Incoming


class Conflict(ValueError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def migrate(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        migrations = sorted((Path(__file__).parent / "migrations").glob("*.sql"))
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS schema_history (version TEXT PRIMARY KEY, checksum TEXT NOT NULL)")
            applied = dict(db.execute("SELECT version, checksum FROM schema_history"))
            known = {file.name for file in migrations}
            if applied.keys() - known:
                raise RuntimeError("数据库版本比本程序新，请使用匹配的程序版本")
            for file in migrations:
                sql = file.read_text(encoding="utf-8")
                checksum = hashlib.sha256(sql.encode()).hexdigest()
                if file.name in applied:
                    if applied[file.name] != checksum:
                        raise RuntimeError("迁移校验失败；请保留数据并恢复对应程序版本")
                    continue
                # No executescript(): it implicitly commits and breaks migration atomicity.
                db.execute("BEGIN IMMEDIATE")
                statement = ""
                for line in sql.splitlines(keepends=True):
                    statement += line
                    if sqlite3.complete_statement(statement):
                        db.execute(statement)
                        statement = ""
                if statement.strip():
                    raise RuntimeError("迁移文件包含未结束的 SQL 语句")
                db.execute("INSERT INTO schema_history VALUES (?, ?)", (file.name, checksum))
                db.commit()

    def recover(self) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM ui_sessions")
            sending = db.execute("SELECT id FROM tasks WHERE status='sending'").fetchall()
            for row in sending:
                db.execute("UPDATE tasks SET status='unknown', updated_at=? WHERE id=?", (now(), row["id"]))
                self._log(db, row["id"], "启动恢复：发送阶段中断，结果不明，不会自动重发")
            if sending:
                db.execute("UPDATE channel SET frozen_reason='发送阶段中断，请核对后继续' WHERE id=1")

    @staticmethod
    def _log(db, task_id: str, message: str) -> None:
        db.execute("INSERT INTO journal(task_id,message,created_at) VALUES (?,?,?)", (task_id, message, now()))

    def create_task(self, client_request_id: str, text: str, scenario: str) -> tuple[dict, bool]:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT * FROM tasks WHERE client_request_id=?", (client_request_id,)).fetchone()
            if old:
                if old["input"] != text or old["scenario"] != scenario:
                    raise Conflict("相同请求编号的内容发生变化，请新建请求")
                return dict(old), False
            count = db.execute("SELECT count(*) FROM tasks WHERE status IN ('queued','sending','waiting_reply')").fetchone()[0]
            if count >= 100:
                raise Conflict("队列已有 100 个任务，请等待处理完成")
            task_id, stamp = str(uuid4()), now()
            db.execute("INSERT INTO tasks VALUES (?,?,?,?, 'queued',NULL,?,?)",
                       (task_id, client_request_id, text, scenario, stamp, stamp))
            self._log(db, task_id, "手动提交的演示请求已入队，数据只保存在本机")
            return dict(db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()), True

    def claim(self) -> dict | None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT frozen_reason FROM channel WHERE id=1").fetchone()[0]:
                return None
            if db.execute("SELECT 1 FROM tasks WHERE status='sending' LIMIT 1").fetchone():
                return None
            row = db.execute("SELECT * FROM tasks WHERE status='waiting_reply' ORDER BY created_at,id LIMIT 1").fetchone()
            if row:
                return dict(row)
            row = db.execute("SELECT * FROM tasks WHERE status='queued' ORDER BY created_at,id LIMIT 1").fetchone()
            if not row:
                return None
            db.execute("UPDATE tasks SET status='sending', updated_at=? WHERE id=?", (now(), row["id"]))
            self._log(db, row["id"], "模拟连接器领取任务，开始模拟发送")
            return {**dict(row), "status": "sending"}

    def transition(self, task_id: str, expected: str, status: str, message: str,
                   result: str | None = None, freeze: bool = False) -> None:
        with self.connect() as db:
            changed = db.execute("UPDATE tasks SET status=?,result=?,updated_at=? WHERE id=? AND status=?",
                                 (status, result, now(), task_id, expected)).rowcount
            if changed != 1:
                raise Conflict("任务状态已变化，停止本次处理")
            self._log(db, task_id, message)
            if freeze:
                db.execute("UPDATE channel SET frozen_reason=? WHERE id=1", (message,))

    def record_event(self, task_id: str, event: Incoming, rejection: str | None) -> None:
        with self.connect() as db:
            if rejection:
                self._log(db, task_id, rejection)
                return
            added = db.execute("INSERT OR IGNORE INTO events VALUES (?,?,?)",
                               (event.event_id, task_id, json.dumps(asdict(event), ensure_ascii=False))).rowcount
            self._log(db, task_id, f"已接收模拟回复第 {event.part}/{event.total} 段" if added else "重复消息已忽略")

    def incoming(self, task_id: str) -> list[Incoming]:
        with self.connect() as db:
            return [Incoming(**json.loads(row[0])) for row in db.execute("SELECT payload FROM events WHERE task_id=?", (task_id,))]

    def resume(self) -> None:
        with self.connect() as db:
            if db.execute("SELECT frozen_reason FROM channel WHERE id=1").fetchone()[0]:
                row = db.execute("SELECT id FROM tasks WHERE status IN ('unknown','timed_out') ORDER BY updated_at DESC LIMIT 1").fetchone()
                if row:
                    self._log(db, row["id"], "用户核对后继续队列；本任务保留原状，没有重发")
            db.execute("UPDATE channel SET frozen_reason=NULL WHERE id=1")

    def snapshot(self) -> dict:
        with self.connect() as db:
            tasks = [dict(row) for row in db.execute("SELECT * FROM tasks ORDER BY created_at DESC,id DESC LIMIT 100")]
            counts = dict(db.execute("SELECT status,count(*) FROM tasks GROUP BY status"))
            reason = db.execute("SELECT frozen_reason FROM channel WHERE id=1").fetchone()[0]
            return {"tasks": tasks, "counts": counts, "frozen_reason": reason, "total": sum(counts.values())}

    def detail(self, task_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                return None
            return {**dict(row), "journal": [dict(item) for item in db.execute(
                "SELECT message,created_at FROM journal WHERE task_id=? ORDER BY id LIMIT 100", (task_id,))]}

    def add_session(self, session: str) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM ui_sessions WHERE expires<=?", (time.time(),))
            count = db.execute("SELECT count(*) FROM ui_sessions").fetchone()[0]
            if count >= 32:
                raise Conflict("打开的会话过多，请关闭窗口后重启演示服务")
            db.execute("INSERT INTO ui_sessions VALUES (?,?)", (hashlib.sha256(session.encode()).hexdigest(), time.time() + 28800))

    def valid_session(self, session: str) -> bool:
        with self.connect() as db:
            return db.execute("SELECT 1 FROM ui_sessions WHERE digest=? AND expires>?",
                              (hashlib.sha256(session.encode()).hexdigest(), time.time())).fetchone() is not None
