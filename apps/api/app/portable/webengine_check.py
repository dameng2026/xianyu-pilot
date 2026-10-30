"""CI-only offscreen Chromium test; explicitly does not certify a system tray."""
import json
import os
import time


def check(root,paths,web,port):
    os.environ["QT_QPA_PLATFORM"]="offscreen"
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"]+=" --disable-gpu"
    from PySide6.QtCore import QTimer,QUrl
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebEngineCore import QWebEngineProfileBuilder,QWebEnginePage
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from shiboken6 import delete
    from app.core.instance_lock import SingleInstance
    from app.core.config import Settings
    from app.portable.backend import DesktopBackend
    app=QApplication([])
    result={"ok":False,"trayNativeValidated":False}
    with SingleInstance(root) as instance:
        backend=DesktopBackend(Settings(runtime_mode="desktop",desktop_port=port),web,instance)
        builder=QWebEngineProfileBuilder()
        builder.setPersistentStoragePath(str(paths["profile"]))
        builder.setCachePath(str(paths["cache"]))
        profile=builder.createProfile("portable-check",app)
        if profile is None:
            raise RuntimeError("cannot create portable profile")
        view=QWebEngineView()
        page=QWebEnginePage(profile,view)
        view.setPage(page)
        started=time.monotonic()
        closing=False
        requested=False
        def read_title(title):
            nonlocal closing
            result["ok"]=title=="发货安全工作台"
            backend.stop()
            closing=True
        def loaded(ok):
            if ok:
                page.runJavaScript("document.querySelector('h1')?.textContent",read_title)
        view.loadFinished.connect(loaded)
        def tick():
            nonlocal closing,requested
            if closing:
                if backend.stopped:
                    app.quit()
                return
            if backend.error or time.monotonic()-started>25:
                backend.stop();closing=True
            elif backend.ready and not requested:
                requested=True
                view.setUrl(QUrl(f"http://127.0.0.1:{port}/desktop/safety"))
        timer=QTimer(app)
        timer.timeout.connect(tick)
        timer.start(100)
        backend.start()
        try:
            app.exec()
        finally:
            backend.stop()
            backend.thread.join()
            timer.stop()
            delete(view)
            delete(profile)
    (root/"logs/webengine-check.json").write_text(json.dumps(result),encoding="utf-8")
    return 0 if result["ok"] else 1
