"""Path boundary and bootstrap tests; no external user/temp data is created."""
from pathlib import Path
import os
import sys
import tempfile
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core import paths
from app.core.instance_lock import SingleInstance
from app.core.portable_runtime import prepare_portable_runtime, startup_error
from app.desktop_storage import DesktopStore
from app.services.desktop_inventory import DesktopInventory


@pytest.fixture
def portable(tmp_path, monkeypatch):
    program = tmp_path / "便携程序 with spaces"
    program.mkdir()
    elsewhere = tmp_path / "shortcut-cwd"
    elsewhere.mkdir()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(program / "闲鱼助手.exe"))
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "not-the-root"), raising=False)
    monkeypatch.chdir(elsewhere)
    for name in ("TEMP", "TMP", "TMPDIR", "APPDATA", "LOCALAPPDATA", "DESKTOP_DATA_DIR", "PYTHONDONTWRITEBYTECODE"):
        monkeypatch.setenv(name, str(elsewhere))
    monkeypatch.setattr(tempfile, "tempdir", tempfile.tempdir)
    monkeypatch.setattr(sys, "dont_write_bytecode", sys.dont_write_bytecode)
    return program, elsewhere


def test_bootstrap_uses_executable_root_and_relocates_all_python_temp(portable):
    program, elsewhere = portable
    root = prepare_portable_runtime()
    assert root == program / "data"
    for name in ("assets", "profiles", "logs", "tmp", "backups"):
        assert (root / name).is_dir()
    for name in ("TEMP", "TMP", "TMPDIR"):
        assert Path(os.environ[name]) == root / "tmp"
    with tempfile.TemporaryDirectory() as temporary:
        assert Path(temporary).parent == root / "tmp"
    assert tempfile.gettempdir() == str(root / "tmp")
    assert sys.dont_write_bytecode
    assert not list(elsewhere.iterdir())
    assert not list(root.rglob(".write-check-*"))
    with SingleInstance(root):
        DesktopStore(root / "app.sqlite3").initialize()
    with DesktopInventory(root / "app.sqlite3").connection() as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 2
        assert conn.execute("PRAGMA temp_store").fetchone()[0] == 2


def test_unwritable_program_data_stops_without_temp_fallback(portable, monkeypatch):
    program, elsewhere = portable
    original = Path.open
    def denied(file, *args, **kwargs):
        if file.name.startswith(".write-check-"):
            raise PermissionError("test: no write permission")
        return original(file, *args, **kwargs)
    monkeypatch.setattr(Path, "open", denied)
    with pytest.raises(PermissionError):
        prepare_portable_runtime()
    assert Path(os.environ["TEMP"]) == elsewhere
    assert not list(elsewhere.iterdir())
    assert not (program / "data" / "app.sqlite3").exists()


def test_link_directory_is_rejected_before_lock_or_database_write(portable, monkeypatch):
    program, elsewhere = portable
    root = program / "data"
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda p: p == root or original(p))
    for action in (prepare_portable_runtime, lambda: SingleInstance(root).__enter__(), lambda: DesktopStore(root / "app.sqlite3").initialize()):
        with pytest.raises(ValueError, match="链接"):
            action()
    assert not root.exists()
    assert not list(elsewhere.iterdir())


@pytest.mark.parametrize("relative", ["../outside", "C:/secret", "/absolute", "assets/../../outside", "assets\\outside"])
def test_portable_references_reject_escape(portable, relative):
    program, _ = portable
    with pytest.raises(ValueError):
        paths.contained_data_path(program / "data", relative)


def test_unwritable_startup_shows_windows_error_and_does_not_continue(monkeypatch):
    import ctypes
    shown = []
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(user32=SimpleNamespace(MessageBoxW=lambda *args: shown.append(args))), raising=False)
    with pytest.raises(RuntimeError, match="程序已停止"):
        startup_error(PermissionError("denied"))
    assert len(shown) == 1
    assert "data" in shown[0][1]
