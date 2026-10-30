import asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path
import secrets
from typing import Callable

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from app.services.qq_bridge.client_mock import MockConnector
from app.services.qq_bridge.contracts import Binding, Query, SCENARIOS
from app.services.qq_bridge.policy import validate_query

from .security import COOKIE_NAME, LocalBoundary
from .storage import Conflict, Store
from .worker import DemoWorker
from .workflow_routes import workflow_router
from .workflow_store import WorkflowStore
from .workflow_worker import WorkflowWorker
from .vault import CredentialError
from .qq_connector import ConnectorError, accept_event
from .mcp_server import NativeMCP


class TaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_request_id: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    text: str = Field(min_length=1, max_length=500)
    scenario: str = "normal"


def create_app(store: Store, *, port: int, web_dir: Path, delay: float = 0.45,
               on_shutdown: Callable[[], None] | None = None, vault=None) -> FastAPI:
    """Store must already be migrated under the host's instance lock."""
    binding = Binding()
    connector = MockConnector(binding, delay)
    worker = DemoWorker(store, connector, binding)
    repository = WorkflowStore(store, vault=vault)
    workflow_worker = WorkflowWorker(repository, delay)
    csrf = secrets.token_urlsafe(32)

    @asynccontextmanager
    async def lifespan(app):
        await asyncio.to_thread(store.recover)
        await asyncio.to_thread(repository.recover)
        task = asyncio.create_task(worker.run(), name="desktop-mock-queue")
        workflow_task = asyncio.create_task(workflow_worker.run(), name="desktop-workflow-queue")
        app.state.worker_task = task
        app.state.workflow_task = workflow_task
        try:
            yield
        finally:
            task.cancel()
            workflow_task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            with suppress(asyncio.CancelledError):
                await workflow_task
            await connector.close()

    app = FastAPI(title="闲鱼助手 · 本地演示", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.worker = worker
    app.state.workflow_worker = workflow_worker
    app.add_middleware(LocalBoundary, origin=f"http://127.0.0.1:{port}", store=store, csrf=csrf)
    mcp = NativeMCP(repository, port)
    app.include_router(workflow_router(repository, workflow_worker, mcp))
    app.include_router(mcp.router())

    @app.post("/api/qq/events/{identity}")
    async def qq_event(identity: str, request: Request):
        try:
            await asyncio.to_thread(accept_event, repository, identity, await request.body(), request.headers.get("x-signature", ""))
            from fastapi.responses import Response
            return Response(status_code=204)
        except (ConnectorError, Conflict, ValueError):
            raise HTTPException(403, "QQ 事件签名、归属或内容校验失败") from None

    @app.exception_handler(CredentialError)
    @app.exception_handler(ConnectorError)
    async def integration_error(_request, error):
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": str(error)}, status_code=422)

    @app.exception_handler(Conflict)
    async def conflict(_request, error):
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": str(error)}, status_code=409)

    @app.get("/health")
    def health():
        ready = not worker.failure and not workflow_worker.failure and not app.state.worker_task.done() and not app.state.workflow_task.done()
        if not ready:
            raise HTTPException(503, "演示处理器已停止，请重启本地服务并查看任务状态")
        with store.connect() as db:
            real_qq = bool(db.execute("SELECT 1 FROM wf_accounts WHERE tenant_id='local' AND driver='onebot' AND verified=1 AND status='online' LIMIT 1").fetchone())
        return {"status": "ready", "mode": "desktop-workflow", "real_connections": real_qq, "real_xianyu_connected": False}

    @app.get("/api/state")
    def state():
        return {**store.snapshot(), "mode": "mock", "scenarios": SCENARIOS,
                "connector": connector.health(), "capabilities": connector.capabilities(),
                "worker_healthy": not worker.failure}

    @app.post("/api/tasks", status_code=201)
    def create_task(body: TaskInput):
        if worker.failure:
            raise HTTPException(503, "处理器已停止，请先重启服务")
        text = body.text.strip()
        try:
            validate_query(Query(body.client_request_id, text, body.scenario))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        task, created = store.create_task(body.client_request_id, text, body.scenario)
        return {"task": task, "created": created}

    @app.get("/api/tasks/{task_id}")
    def detail(task_id: str):
        task = store.detail(task_id)
        if task is None:
            raise HTTPException(404, "未找到任务")
        return task

    @app.post("/api/channel/resume")
    def resume():
        store.resume()
        return {"resumed": True, "uncertain_tasks_retried": False}

    @app.post("/api/host/stop")
    def stop_host(background: BackgroundTasks):
        if on_shutdown is None:
            raise HTTPException(409, "当前宿主不支持从页面停止")
        background.add_task(on_shutdown)
        return {"stopping": True, "records_retained": True}

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        page = web_dir / "index.html"
        if not page.is_file():
            raise HTTPException(503, "前端尚未构建，请通过 start-desktop-demo.bat 启动")
        session = request.cookies.get(COOKIE_NAME, "")
        if not session or len(session) > 128 or not store.valid_session(session):
            session = secrets.token_urlsafe(32)
            store.add_session(session)
        response = HTMLResponse(page.read_text(encoding="utf-8").replace("__DESKTOP_CSRF__", csrf))
        response.set_cookie(COOKIE_NAME, session, httponly=True, samesite="strict", max_age=28800, path="/")
        return response

    assets = web_dir / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")
    return app
