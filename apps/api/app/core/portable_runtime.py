"""Bootstrap application-owned runtime files strictly below program/data."""
import os
import sys
import uuid

from app.core.paths import contained_data_path, data_directory


def prepare_portable_runtime():
    root = data_directory()
    # Check before mkdir/open; never follow a pre-existing external data link.
    contained_data_path(root, "app.sqlite3")
    root.mkdir(parents=True, exist_ok=True)
    folders = [root] + [contained_data_path(root, name) for name in ("assets", "profiles", "logs", "tmp", "backups")]
    for folder in folders:
        folder.mkdir(exist_ok=True)
        probe = folder / (".write-check-" + uuid.uuid4().hex)
        try:
            with probe.open("xb") as stream:
                stream.write(b"portable")
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            probe.unlink(missing_ok=True)
    for name in ("app.sqlite3", "app.sqlite3-wal", "app.sqlite3-shm"):
        file = contained_data_path(root, name)
        if file.exists() and not file.is_file():
            raise ValueError("数据库路径必须是普通文件")
    tmp = str(contained_data_path(root, "tmp"))
    for key in ("TEMP", "TMP", "TMPDIR"):
        os.environ[key] = tmp
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    sys.dont_write_bytecode = True
    import tempfile
    tempfile.tempdir = tmp
    return root


def startup_error(exc):
    message = "无法在程序所在目录的 data 文件夹内安全保存数据，程序已停止。请将软件移至具备写权限的普通文件夹运行，并检查是否存在链接目录。"
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, "闲鱼助手启动失败", 0x10)
    raise RuntimeError(message) from exc
