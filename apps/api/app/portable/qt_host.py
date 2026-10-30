"""Qt window + Chromium + tray. Imported only after portable bootstrap."""
import ctypes
from ctypes import wintypes
import time

from PySide6.QtCore import QTimer,QUrl,Qt,QEvent
from PySide6.QtGui import QAction,QColor,QIcon,QPainter,QPixmap
from PySide6.QtWidgets import QApplication,QMainWindow,QMenu,QMessageBox,QSystemTrayIcon
from PySide6.QtWebEngineCore import QWebEnginePage,QWebEngineProfile,QWebEngineProfileBuilder,QWebEngineSettings,QWebEngineUrlRequestInterceptor
from PySide6.QtWebEngineWidgets import QWebEngineView

from app.portable.windows_events import power_event,network_connected


LABELS={"starting":"正在启动","offline":"未连接闲鱼 · 自动发货未启用","running":"自动发货运行中",
    "paused":"已暂停","reconciling":"正在重新对账","attention":"需要核查","error":"服务异常","stopping":"正在安全退出"}


def tray_icon(state):
    pixmap=QPixmap(32,32)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter=QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#22834a" if state=="running" else "#c53c35" if state=="error" else "#c18a16"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(4,4,24,24)
    painter.end()
    return QIcon(pixmap)


def local_url(url,port):
    return url.scheme()=="http" and url.host()=="127.0.0.1" and url.port()==port and not url.userInfo()


class LocalRequests(QWebEngineUrlRequestInterceptor):
    def __init__(self,port,parent):
        super().__init__(parent)
        self.port=port

    def interceptRequest(self,info):
        url=info.requestUrl()
        if not local_url(url,self.port) and url.scheme() not in ("data","blob","about"):
            info.block(True)


class ConsolePage(QWebEnginePage):
    def __init__(self,profile,port,parent):
        super().__init__(profile,parent)
        self.port=port
        self.permissionRequested.connect(lambda permission:permission.deny())

    def acceptNavigationRequest(self,url,kind,is_main_frame):
        return local_url(url,self.port) or url.toString()=="about:blank"

    def createWindow(self,_kind):
        return None

    def chooseFiles(self,*_args):
        return []

    def javaScriptConsoleMessage(self,*_args):
        pass  # Never write page contents or secrets to native logs.


class ConsoleWindow(QMainWindow):
    def __init__(self,controller):
        super().__init__()
        self.controller=controller
        self.setWindowTitle("闲鱼助手 · 发货安全工作台")
        self.resize(1180,820)
        self.setMinimumSize(780,560)

    def closeEvent(self,event):
        event.ignore()
        self.controller.hide_to_tray()

    def nativeEvent(self,event_type,message):
        try:
            if message and event_type in (b"windows_generic_MSG", "windows_generic_MSG", b"windows_dispatcher_MSG"):
                msg=ctypes.cast(int(message),ctypes.POINTER(wintypes.MSG)).contents
                event=power_event(msg.message,int(msg.wParam))
                if event=="suspend":
                    self.controller.backend.pause("suspend")
                    self.controller.render("paused")
                elif event=="resume":
                    self.controller.backend.resume("suspend")
                    self.controller.render("reconciling")
        except Exception:
            pass
        return False,0


class DesktopConsole:
    def __init__(self,backend,paths,*,smoke=False):
        self.backend,self.paths=backend,paths
        self.app=QApplication.instance() or QApplication([])  # Never forward arbitrary Chromium CLI flags.
        self.app.setApplicationName("XianyuPilot")
        self.app.setOrganizationName("XianyuPilot")
        self.app.setQuitOnLastWindowClosed(False)
        if not QSystemTrayIcon.isSystemTrayAvailable():
            raise RuntimeError("当前 Windows 会话没有系统托盘，无法安全后台驻留")
        self.exiting=False
        self.first_hide=True
        self.loaded=False
        self.connected=None
        self.last_state=None
        self.started=time.monotonic()
        self.smoke=smoke
        self.smoke_started=False
        self.smoke_ok=False
        self.window=ConsoleWindow(self)
        builder=QWebEngineProfileBuilder()
        builder.setPersistentStoragePath(str(paths["profile"]))
        builder.setCachePath(str(paths["cache"]))
        builder.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
        builder.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)
        self.profile=builder.createProfile("portable-console",self.app)
        if self.profile is None:
            raise RuntimeError("本地浏览器资料目录已占用，程序停止")
        self.profile.setDownloadPath(str(paths["downloads"]))
        self.profile.downloadRequested.connect(lambda download:download.cancel())
        self.interceptor=LocalRequests(backend.config.desktop_port,self.profile)
        self.profile.setUrlRequestInterceptor(self.interceptor)
        self.view=QWebEngineView(self.window)
        self.page=ConsolePage(self.profile,backend.config.desktop_port,self.view)
        for option in (QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls,
                       QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls,
                       QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard):
            self.page.settings().setAttribute(option,False)
        self.view.setPage(self.page)
        self.view.loadFinished.connect(self.on_load)
        self.window.setCentralWidget(self.view)
        self.menu=QMenu()
        self.status_action=QAction("正在启动",self.menu)
        self.status_action.setEnabled(False)
        self.menu.addAction(self.status_action)
        self.menu.addAction("打开控制台",self.show)
        self.menu.addAction("隐藏控制台",self.hide_to_tray)
        self.toggle=self.menu.addAction("暂停发货",self.toggle_delivery)
        self.menu.addSeparator()
        self.quit_action=self.menu.addAction("退出程序",self.begin_shutdown)
        self.tray=QSystemTrayIcon(tray_icon("starting"),self.app)
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self.activated)
        self.tray.show()
        self.timer=QTimer(self.app)
        self.timer.timeout.connect(self.tick)
        self.timer.start(500)
        self.app.commitDataRequest.connect(lambda _manager:self.begin_shutdown())

    def on_load(self,ok):
        if ok:
            self.loaded=True
        elif not self.exiting:
            self.window.statusBar().showMessage("本机控制台加载失败，请检查 data/logs/desktop.log")

    def activated(self,reason):
        if reason==QSystemTrayIcon.ActivationReason.DoubleClick:
            self.hide_to_tray() if self.window.isVisible() else self.show()

    def show(self):
        self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()

    def hide_to_tray(self):
        if self.exiting:
            return
        self.window.hide()
        if self.first_hide:
            text=("闲鱼助手已最小化到托盘，后台自动发货与监听仍在持续运行中" if self.last_state=="running"
                else "闲鱼助手已最小化到托盘，程序仍在后台运行。"+LABELS.get(self.last_state,"正在启动"))
            self.tray.showMessage("闲鱼助手",text,QSystemTrayIcon.MessageIcon.Information,8000)
            self.first_hide=False

    def toggle_delivery(self):
        if "manual" in self.backend.gate.reasons:
            self.backend.resume("manual")
            self.render("reconciling")
        else:
            self.backend.pause("manual")
            self.render("paused")

    def render(self,state):
        if self.exiting:
            state="stopping"
        if state!=self.last_state:
            self.tray.setIcon(tray_icon(state))
            self.tray.setToolTip("闲鱼助手 · "+LABELS[state])
            if state in ("paused","attention","error","reconciling") and self.last_state not in (None,state):
                self.tray.showMessage("闲鱼助手",LABELS[state]+"；原订单和卡密绑定持续保留。",QSystemTrayIcon.MessageIcon.Warning,6000)
            self.last_state=state
        self.status_action.setText(LABELS[state])
        self.window.statusBar().showMessage(LABELS[state])
        self.toggle.setText("恢复发货（先对账）" if "manual" in self.backend.gate.reasons else "暂停发货")
        self.toggle.setEnabled(bool(self.backend.runtime and not self.exiting))

    def begin_shutdown(self):
        if self.exiting:
            return
        self.exiting=True
        self.quit_action.setEnabled(False)
        self.backend.stop()
        self.render("stopping")

    def tick(self):
        if self.exiting:
            if self.backend.stopped:
                self.timer.stop()
                self.tray.hide()
                self.window.setCentralWidget(None)
                self.page.deleteLater()
                self.view.deleteLater()
                self.profile.deleteLater()
                QTimer.singleShot(100,self.app.quit)
            return
        if self.backend.error or (not self.backend.ready and time.monotonic()-self.started>30):
            self.render("error")
            QMessageBox.critical(self.window,"启动失败","本机服务未能启动，可能是端口占用或数据库异常。请检查 data/logs/desktop.log。")
            self.begin_shutdown()
            return
        connected=network_connected()
        if connected!=self.connected:
            if connected:
                self.backend.resume("network")
            else:
                self.backend.pause("network")
            self.connected=connected
        status=self.backend.poll()
        self.render(status["state"])
        if self.backend.ready and self.view.url().isEmpty():
            self.view.setUrl(QUrl(f"http://127.0.0.1:{self.backend.config.desktop_port}/desktop/safety"))
        if self.smoke and self.loaded and not self.smoke_started:
            self.smoke_started=True
            self.page.runJavaScript("document.querySelector('h1')?.textContent",self.smoke_result)
        if self.smoke and time.monotonic()-self.started>25:
            self.begin_shutdown()

    def smoke_result(self,title):
        self.window.close()
        self.smoke_ok=title=="发货安全工作台" and not self.window.isVisible() and self.backend.ready and self.tray.isVisible()
        self.begin_shutdown()

    def run(self):
        self.show()
        self.backend.start()
        result=self.app.exec()
        return result if not self.smoke else (0 if self.smoke_ok else 1)

    def dispose(self):
        # Release Chromium's files before the data-directory instance lock.
        from shiboken6 import delete,isValid
        self.timer.stop()
        self.tray.hide()
        if isValid(self.window):
            delete(self.window)
        if isValid(self.profile):
            delete(self.profile)
