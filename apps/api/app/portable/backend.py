"""Own the API event loop; GUI controls never race async executor state."""
import asyncio
import logging
import socket
import threading

from app.services.delivery_gate import DeliveryGate


class DesktopBackend:
    def __init__(self,config,web_dir,instance_lock,platform_runtime=None):
        self.config,self.web_dir,self.instance_lock=config,web_dir,instance_lock
        self.runtime=platform_runtime
        self.gate=platform_runtime.monitor.gate if platform_runtime else DeliveryGate()
        self.gate.block("host_starting")
        self.loop=self.server=self.app=None
        self.error=None
        self.thread=None
        self._status_future=None
        self._pending_resumes=set()
        self.status={"state":"starting","pauseReasons":[],"accountCount":0}

    def start(self):
        self.thread=threading.Thread(target=self._run,name="desktop-api",daemon=False)
        self.thread.start()

    def _run(self):
        try:
            asyncio.run(self._serve())
        except BaseException as exc:
            self.error=type(exc).__name__
            logging.getLogger(__name__).error("Desktop API failed: %s",self.error)

    async def _serve(self):
        import uvicorn
        from app.desktop_app import create_desktop_app
        self.loop=asyncio.get_running_loop()
        self.app=create_desktop_app(self.config,web_dir=self.web_dir,platform_runtime=self.runtime,instance_lock=self.instance_lock)
        self.server=uvicorn.Server(uvicorn.Config(self.app,host="127.0.0.1",port=self.config.desktop_port,
            loop="asyncio",http="h11",ws="none",log_config=None,access_log=False,lifespan="on"))
        if "shutdown" in self.gate.reasons:
            return
        # Never silently open the console of another process occupying the port.
        with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as listener:
            if hasattr(socket,"SO_EXCLUSIVEADDRUSE"):
                listener.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
            listener.bind(("127.0.0.1",self.config.desktop_port))
            listener.listen(128)
            await self.server.serve(sockets=[listener])

    @property
    def ready(self):
        return bool(self.app and self.app.state.ready and not self.error)

    def pause(self,reason="manual"):
        # Synchronous thread-safe gate closes before the GUI action returns.
        self.gate.block(reason)
        self._pending_resumes.discard(reason)
        self.status={**self.status,"state":"paused","pauseReasons":list(self.gate.reasons)}

    def resume(self,reason="manual"):
        if reason=="shutdown":
            return False
        if not self.ready:
            self._pending_resumes.add(reason)
            return False
        self._pending_resumes.discard(reason)
        self.status={**self.status,"state":"reconciling"}
        self._status_future=None
        revision=self.gate.revision(reason)
        asyncio.run_coroutine_threadsafe(self._resume(reason,revision),self.loop)
        return True

    async def _resume(self,reason,revision):
        if self.runtime:
            await self.runtime.resume(reason,expected_revision=revision)
        else:
            self.gate.clear(reason,expected_revision=revision)

    async def _status(self):
        if self.runtime:
            return await self.runtime.desktop_status()
        return {"state":"offline","pauseReasons":list(self.gate.reasons),"accountCount":0}

    def poll(self):
        if not self.ready:
            return {**self.status,"state":"error" if self.error else "starting"}
        for reason in tuple(self._pending_resumes):
            self.resume(reason)
        if "host_starting" in self.gate.reasons:
            self.resume("host_starting")
        if self._status_future and self._status_future.done():
            try:
                self.status=self._status_future.result()
            except Exception:
                self.status={**self.status,"state":"attention"}
            self._status_future=None
        if self._status_future is None and self.loop and self.loop.is_running():
            try:
                self._status_future=asyncio.run_coroutine_threadsafe(self._status(),self.loop)
            except (RuntimeError, AttributeError):
                pass
        # A stale successful status must never hide an immediate pause.
        if self.gate.blocked:
            return {**self.status,"state":"paused","pauseReasons":list(self.gate.reasons)}
        return self.status

    def stop(self):
        self.pause("shutdown")
        if self.server:
            self.server.should_exit=True

    @property
    def stopped(self):
        return self.thread is not None and not self.thread.is_alive()
