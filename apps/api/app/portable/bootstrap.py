"""Set all writable locations before importing Qt or application services."""
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys

from app.core.paths import contained_data_path,program_root
from app.core.portable_runtime import prepare_portable_runtime


def configure_environment():
    root=prepare_portable_runtime()
    paths={name:contained_data_path(root,relative) for name,relative in {
        "roaming":"profiles/system/roaming","local":"profiles/system/local",
        "profile":"profiles/console","cache":"profiles/console/cache",
        "crashes":"logs/chromium","qml":"tmp/qml","downloads":"tmp/downloads",
    }.items()}
    for path in paths.values():
        path.mkdir(parents=True,exist_ok=True)
    os.environ.update({
        "RUNTIME_MODE":"desktop","APPDATA":str(paths["roaming"]),"LOCALAPPDATA":str(paths["local"]),
        "XDG_CONFIG_HOME":str(paths["roaming"]),"XDG_CACHE_HOME":str(paths["cache"]),
        "XDG_DATA_HOME":str(paths["local"]),"QML_DISK_CACHE_PATH":str(paths["qml"]),
        "QTWEBENGINE_CHROMIUM_FLAGS":' '.join([
            f'--user-data-dir="{paths["profile"]}"',f'--disk-cache-dir="{paths["cache"]}"',
            f'--crash-dumps-dir="{paths["crashes"]}"',"--disable-breakpad",
            "--disable-gpu-shader-disk-cache","--no-first-run",
        ]),
    })
    # Never inherit a debugging listener or a disabled Chromium sandbox.
    for key in ("QTWEBENGINE_REMOTE_DEBUGGING","QTWEBENGINE_DISABLE_SANDBOX"):
        os.environ.pop(key,None)
    return root,paths


def bundled_web_dir():
    if getattr(sys,"frozen",False):
        # This is read-only package content, never a writable data root.
        internal=Path(sys._MEIPASS).resolve()
        if internal!=(program_root()/"_internal").resolve():
            raise ValueError("便携版必须使用完整的目录式运行包")
        return internal/"web"
    return program_root()/"apps/web/dist"


def configure_logging(root):
    from app.core.logging_security import install_log_redaction
    install_log_redaction()
    logger=logging.getLogger()
    logger.setLevel(logging.INFO)
    handler=RotatingFileHandler(contained_data_path(root,"logs/desktop.log"),maxBytes=2_000_000,backupCount=4,encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logger.addHandler(handler)
    return handler


def portable_self_check(root,web):
    """Frozen CI probe; no GUI, account, external request or order fabrication."""
    from app.core.instance_lock import SingleInstance
    from app.desktop_storage import DesktopStore
    from app.services.desktop_delivery import DesktopDeliveryStore
    if not (web/"desktop-safety.html").is_file() or not (web/"assets").is_dir():
        raise RuntimeError("便携包缺少内嵌前端")
    with SingleInstance(root):
        schema=DesktopStore(root/"app.sqlite3").initialize()
        DesktopDeliveryStore(root/"app.sqlite3").recover_interrupted()
        with DesktopDeliveryStore(root/"app.sqlite3").connection(write=True) as conn:
            # Durable non-business sentinel proves that a moved bundle reuses
            # its original database. No fake orders enter a distributable build.
            conn.execute("CREATE TABLE IF NOT EXISTS portable_self_check(id INTEGER PRIMARY KEY CHECK(id=1),token TEXT NOT NULL)")
            import uuid
            conn.execute("INSERT OR IGNORE INTO portable_self_check VALUES(1,?)",(uuid.uuid4().hex,))
            token=conn.execute("SELECT token FROM portable_self_check WHERE id=1").fetchone()[0]
    report={"ok":True,"programRoot":str(program_root()),"dataRoot":str(root),"schema":schema,"storageToken":token,
        "frozen":bool(getattr(sys,"frozen",False)),"uiBuilt":True,"automaticDeliveryEnabled":False}
    contained_data_path(root,"logs/portable-self-check.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    return report
