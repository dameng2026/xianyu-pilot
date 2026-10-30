"""Versioned SQLite schema for the original application's desktop adaptation."""
import hashlib
from contextlib import closing
from pathlib import Path
import sqlite3
from app.core.paths import contained_data_path

APPLICATION_ID = 0x58595044
MIGRATIONS = Path(__file__).parent / "desktop_migrations"


class DesktopSchemaError(RuntimeError):
    pass


class DesktopStore:
    def __init__(self, path: Path, migration_dir: Path = MIGRATIONS):
        self.path = Path(path)
        self.migration_dir = Path(migration_dir)

    def migrations(self):
        files = sorted(self.migration_dir.glob("[0-9]*.sql"))
        if not files:
            raise DesktopSchemaError("缺少桌面数据库迁移文件")
        return [(file.name, file.read_text(encoding="utf-8")) for file in files]

    def _validate(self, connection, migrations):
        application_id = connection.execute("PRAGMA application_id").fetchone()[0]
        if application_id != APPLICATION_ID:
            raise DesktopSchemaError("数据库不属于正式桌面应用，未修改已有数据")
        applied = dict(connection.execute("SELECT version,checksum FROM desktop_schema_history"))
        expected = {name: hashlib.sha256(sql.encode()).hexdigest() for name, sql in migrations}
        if applied.keys() - expected.keys():
            raise DesktopSchemaError("数据库版本较新，请使用匹配的应用版本")
        if any(applied[name] != expected[name] for name in applied):
            raise DesktopSchemaError("桌面迁移校验失败，请保留数据并恢复匹配版本")
        return applied, expected

    def initialize(self):
        """Caller must hold the data directory's SingleInstance lock."""
        migrations = self.migrations()
        for name in (self.path.name, self.path.name + "-wal", self.path.name + "-shm"):
            contained_data_path(self.path.parent, name)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5)
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA temp_store=MEMORY")
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            if tables and "desktop_schema_history" not in tables:
                raise DesktopSchemaError("目录包含其他应用或演示数据库，未修改已有数据")
            connection.execute("BEGIN IMMEDIATE")
            try:
                if not tables:
                    if connection.execute("PRAGMA application_id").fetchone()[0] not in (0, APPLICATION_ID):
                        raise DesktopSchemaError("数据库属于其他应用")
                    connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
                    connection.execute("CREATE TABLE desktop_schema_history(version TEXT PRIMARY KEY, checksum TEXT NOT NULL)")
                applied, expected = self._validate(connection, migrations)
                for name, sql in migrations:
                    if name in applied:
                        continue
                    statement = ""
                    for line in sql.splitlines(keepends=True):
                        statement += line
                        if sqlite3.complete_statement(statement):
                            connection.execute(statement)
                            statement = ""
                    if statement.strip():
                        raise DesktopSchemaError("迁移包含未结束的 SQL")
                    connection.execute("INSERT INTO desktop_schema_history VALUES (?,?)", (name, expected[name]))
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        finally:
            connection.close()
        return self.status()

    def status(self):
        with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=5)) as connection:
            applied, expected = self._validate(connection, self.migrations())
        return {"current": applied == expected, "version": sorted(applied)[-1] if applied else None}
