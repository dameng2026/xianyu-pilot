import asyncio
import logging
from contextlib import asynccontextmanager
from urllib.parse import quote
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.core.config import settings
from app.core.audit_middleware import MutationAuditMiddleware
from app.core.background_tasks import (
    begin_background_task_shutdown,
    shutdown_background_tasks,
    start_background_task_runtime,
)
from app.core.database import async_session, engine
from app.core.logging_security import install_log_redaction, redact_sensitive_text
from app.core.middleware import (
    RequestBodyLimitMiddleware,
    RequestContextMiddleware,
    ResultEnvelopeStatusMiddleware,
)
from app.core.redis_client import close_redis, is_redis_available
from app.core.response import ResultObject
from app.core.route_registry import (
    RETIRED_API_SURFACE_REASON,
    assert_unique_routes,
    retired_surface_guidance,
)
from app.core.upload_security import (
    UnsafePathError,
    UnsafeRemoteURLError,
    UploadValidationError,
    download_trusted_origin_image,
    load_safe_local_image,
    resolve_upload_path,
    write_upload_bytes_atomic,
)
from app.migrations import assert_schema_current, get_schema_status

install_log_redaction()
logger = logging.getLogger(__name__)
UPLOADS_DIR = settings.uploads_path

@asynccontextmanager
async def lifespan(app: FastAPI):
    start_background_task_runtime()
    assert_unique_routes(app.routes, context="application startup")
    # Schema changes are an explicit deployment step. API replicas only perform
    # a read-only compatibility check and fail before starting automation when
    # a migration is missing or belongs to a newer application build.
    await assert_schema_current()

    # Load or create the persistent instance token used for commercial bridge
    # correlation.  Failures are non-fatal; the bridge will fall back to a
    # transient in-memory value.
    try:
        from app.core.instance import ensure_instance_token
        async with async_session() as session:
            await ensure_instance_token(session)
    except Exception:
        logger.warning("Failed to load instance token on startup", exc_info=True)
    
    # 启动 WebSocket
    try:
        from app.services.ws_startup import auto_start_all
        await auto_start_all()
    except Exception:
        logger.warning("WebSocket auto-start failed", exc_info=True)
    
    # 启动 Token 刷新
    try:
        from app.services.cookie_token_refresher import start_dispatcher
        await start_dispatcher()
    except Exception:
        logger.warning("Token refresher start failed", exc_info=True)
    
    yield

    # 先拒绝新的辅助任务，再依次停止长期生产者；Redis/DB 最后关闭。
    begin_background_task_shutdown()
    try:
        from app.services.cookie_token_refresher import stop_dispatcher
        await stop_dispatcher()
    except Exception:
        logger.warning("Token refresher shutdown failed", exc_info=True)
    try:
        from app.api.v1.routes.items import shutdown_item_sync_tasks
        await shutdown_item_sync_tasks()
    except Exception:
        logger.warning("Item sync shutdown failed", exc_info=True)
    try:
        from app.services.xianyu_goods_sync import shutdown_detail_sync_tasks
        await shutdown_detail_sync_tasks()
    except Exception:
        logger.warning("Item detail sync shutdown failed", exc_info=True)
    try:
        from app.services.ws_startup import stop_all
        await stop_all()
    except Exception:
        logger.warning("WebSocket shutdown failed", exc_info=True)
    try:
        await shutdown_background_tasks()
    except Exception:
        logger.warning("Auxiliary task shutdown failed", exc_info=True)
    await close_redis()
    await engine.dispose()

app = FastAPI(
    title=settings.app_name,
    lifespan=lifespan,
    docs_url="/docs" if settings.docs_enabled else None,
    redoc_url="/redoc" if settings.docs_enabled else None,
    openapi_url="/openapi.json" if settings.docs_enabled else None,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
if settings.trusted_hosts_list:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_hosts_list)
app.add_middleware(RequestBodyLimitMiddleware, max_bytes=settings.max_request_body_bytes)
app.add_middleware(ResultEnvelopeStatusMiddleware)
app.add_middleware(MutationAuditMiddleware)
app.add_middleware(RequestContextMiddleware)

# 路由（延迟导入避免循环依赖）
def register_routes():
    from app.api.v1.api import api_router
    app.include_router(api_router, prefix="/api")

register_routes()

# 静态文件
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

# 轮播图等静态资源的浏览器缓存策略：文件名在上传时已含时间戳+UUID（唯一），
# 因此可安全使用 immutable，浏览器命中缓存后不再发起网络请求。
_UPLOAD_CACHE_HEADERS = {"Cache-Control": "public, max-age=86400, immutable"}


# Custom /uploads handler: serve local files first, fall back to commercial backend.
# 从商业版（中国服务后端）下载的文件会原子写入本地 uploads 目录，后续请求直接读本地磁盘，
# 避免每次都跨网回源下载 1.5MB+ 的轮播图。
@app.get("/uploads/{file_path:path}")
async def bridge_uploads_fallback(file_path: str):
    try:
        local_path = resolve_upload_path(file_path)
    except UnsafePathError:
        return Response(status_code=404)
    if await asyncio.to_thread(local_path.is_file):
        try:
            content, content_type = await asyncio.to_thread(
                load_safe_local_image,
                local_path,
            )
        except UploadValidationError:
            return Response(status_code=404)
        return Response(
            content=content,
            media_type=content_type,
            headers=_UPLOAD_CACHE_HEADERS,
        )

    # File not found locally - try commercial backend
    commercial_base = getattr(settings, "commercial_backend_base_url", "").rstrip("/")
    if commercial_base:
        safe_remote_path = "/".join(
            quote(part, safe="") for part in file_path.replace("\\", "/").split("/")
        )
        commercial_url = f"{commercial_base}/uploads/{safe_remote_path}"
        try:
            content, content_type = await download_trusted_origin_image(
                commercial_url,
                max_bytes=settings.max_upload_bytes,
                timeout_seconds=settings.commercial_backend_timeout_seconds,
            )
            if content:
                # 原子写入本地缓存，避免并发请求读到半截文件。
                try:
                    await asyncio.to_thread(
                        write_upload_bytes_atomic,
                        local_path,
                        content,
                    )
                except Exception:
                    pass  # 缓存写入失败不影响本次返回
                return Response(
                    content=content,
                    media_type=content_type,
                    headers=_UPLOAD_CACHE_HEADERS,
                )
        except FileNotFoundError:
            return Response(status_code=404)
        except (UnsafeRemoteURLError, UploadValidationError):
            return Response(status_code=502)
        except Exception as exc:
            logger.warning(
                "Commercial upload bridge failed errorType=%s",
                type(exc).__name__,
            )
            return Response(status_code=502)

    # Final fallback: return 404
    from fastapi import HTTPException
    raise HTTPException(status_code=404, detail="File not found")

# 健康检查
@app.get("/health")
@app.get("/health/live")
async def health_live():
    return {"status": "ok"}


async def _background_runtime_components() -> dict[str, str]:
    """Return fail-closed health for core in-process background runtimes."""

    components = {
        "messageAutomation": "unavailable",
        "tokenRefresher": "unavailable",
    }
    try:
        from app.services.message_automation_outbox import get_worker_health_status

        components["messageAutomation"] = get_worker_health_status()
    except Exception:
        logger.warning("Message automation health probe failed", exc_info=True)
    try:
        from app.services.cookie_token_refresher import get_dispatcher_status

        status = await get_dispatcher_status()
        components["tokenRefresher"] = str(status.get("healthStatus") or "unavailable")
    except Exception:
        logger.warning("Token refresher health probe failed", exc_info=True)
    return components


async def _readiness_snapshot() -> tuple[bool, dict[str, str]]:
    components: dict[str, str] = {
        "database": "unavailable",
        "schemaMigration": "unavailable",
    }

    async def _db_probe() -> None:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        components["database"] = "ok"
        schema_status = await get_schema_status()
        components["schemaMigration"] = "ok" if schema_status.current else "pending"

    try:
        await asyncio.wait_for(
            _db_probe(),
            timeout=settings.mysql_connect_timeout_seconds + 2,
        )
    except Exception:
        logger.warning("Readiness database probe failed", exc_info=True)

    components.update(await _background_runtime_components())

    # Authentication revocation and throttling fail closed on shared Redis in
    # every runtime except the explicitly bounded test adapter. Readiness must
    # use the same boundary, otherwise development/local can report ready while
    # login immediately returns 503.
    if (settings.app_env or "").strip().casefold() != "test":
        try:
            redis_ok = await is_redis_available()
        except Exception:
            redis_ok = False
        components["redis"] = "ok" if redis_ok else "unavailable"

    ready = all(value == "ok" for value in components.values())
    return ready, components


@app.get("/health/ready")
async def health_ready():
    ready, components = await _readiness_snapshot()
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"status": "ready" if ready else "not_ready", "components": components},
    )


@app.get("/health/edge-ready")
async def health_edge_ready():
    """Coarse readiness for the public TLS edge without topology disclosure."""

    ready, _components = await _readiness_snapshot()
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"status": "ready" if ready else "not_ready"},
    )


assert_unique_routes(app.routes, context="application import")

# 异常处理
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    request_id = getattr(request.state, "request_id", "")
    retired_guidance = (
        retired_surface_guidance(request.method, request.url.path)
        if exc.status_code in {404, 405}
        else None
    )
    status_code = 410 if retired_guidance else exc.status_code
    message = redact_sensitive_text(retired_guidance or str(exc.detail))
    result = ResultObject.failed(message, code=status_code).model_dump(by_alias=True)
    if status_code == 410:
        result["data"] = {
            "reason": RETIRED_API_SURFACE_REASON,
            "migration": message,
        }
    result["requestId"] = request_id
    headers = dict(exc.headers or {})
    if request_id:
        headers["X-Request-ID"] = request_id
    return JSONResponse(status_code=status_code, content=result, headers=headers)

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    request_id = getattr(request.state, "request_id", "")
    errors = [
        {
            "loc": list(error.get("loc", ())),
            "msg": redact_sensitive_text(error.get("msg", "参数无效")),
            "type": error.get("type", "value_error"),
        }
        for error in exc.errors()[:20]
    ]
    result = ResultObject.validate_failed("请求参数校验失败").model_dump(by_alias=True)
    result["data"] = {"errors": errors}
    result["requestId"] = request_id
    return JSONResponse(
        status_code=422,
        content=result,
        headers={"X-Request-ID": request_id} if request_id else None,
    )

@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    request_id = getattr(request.state, "request_id", "")
    logger.exception(
        "Unhandled exception request_id=%s method=%s path=%s",
        request_id,
        request.method,
        request.url.path,
    )
    result = ResultObject.failed("服务器内部错误，请联系管理员并提供请求编号").model_dump(by_alias=True)
    result["requestId"] = request_id
    return JSONResponse(
        status_code=500,
        content=result,
        headers={"X-Request-ID": request_id} if request_id else None,
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=settings.server_port)
