import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.config import settings
from ....core.database import get_db
from ....core.redis_client import get_redis, is_redis_available
from ....core.response import ResultObject
from ....core.security import request_client_ip
from ....models.entities import (
    Notification,
    XianyuAiProvider,
    XianyuOperationLog,
    XianyuSysSetting,
)
from ....services.ai_provider import invalidate_model_config_cache, is_ai_configured
from ....services.commercial_bridge import (
    default_about_content,
    get_commercial_bridge_runtime,
    proxy_get_about_content,
)
from ....services.open_source_config import (
    build_public_open_source_config,
    load_open_source_config,
    save_open_source_config,
)
from ....services.sensitive_config import (
    AI_PROVIDER_API_KEY_PURPOSE,
    is_sensitive_setting_key,
    prepare_secret_for_storage,
)
from ....services.remote_slider_config import (
    build_public_remote_slider_config,
    load_remote_slider_config,
    precheck_remote_slider,
    save_remote_slider_config,
)
from ....services.update_info import build_update_info
from ....schemas.auth import ChangePasswordReqDTO
from ....schemas.common import (
    AiProviderReqDTO,
    AiProviderRespDTO,
    GetSettingReqDTO,
    GetSettingRespDTO,
    SaveSettingReqDTO,
)
from ..deps import get_current_user
from .auth import (
    update_admin_password,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sysSetting")
operation_log_router = APIRouter(prefix="/operationLog")
notification_router = APIRouter(prefix="/notification")
system_info_router = APIRouter(prefix="/system")


def build_public_ai_provider(provider: XianyuAiProvider) -> AiProviderRespDTO:
    """Serialize legacy rows without ever exposing the stored credential."""

    return AiProviderRespDTO(
        id=provider.id,
        provider_name=provider.provider_name,
        api_key="",
        api_key_configured=bool((provider.api_key or "").strip()),
        base_url=provider.base_url,
        model_name=provider.model_name,
        status=provider.status,
    )


async def save_ai_provider(
    req: AiProviderReqDTO,
    db: AsyncSession,
    current_user: dict,
) -> ResultObject[str]:
    """Compatibility helper for encrypted legacy-row migration; not an API route."""

    del current_user
    provider = XianyuAiProvider(
        provider_name=req.provider_name,
        api_key=prepare_secret_for_storage(
            incoming=req.api_key,
            purpose=AI_PROVIDER_API_KEY_PURPOSE,
        ),
        base_url=req.base_url,
        model_name=req.model_name,
        status=1,
    )
    db.add(provider)
    await db.commit()
    return ResultObject.success("保存成功")


def get_client_ip(request: Request) -> str:
    return request_client_ip(request)


@router.post("/get", response_model=ResultObject[GetSettingRespDTO])
async def get_setting(
    req: GetSettingReqDTO,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    result = await db.execute(
        select(XianyuSysSetting).where(XianyuSysSetting.setting_key == req.setting_key)
    )
    setting = result.scalar_one_or_none()
    sensitive = is_sensitive_setting_key(req.setting_key)
    return ResultObject.success(GetSettingRespDTO(
        setting_key=req.setting_key,
        setting_value=None if sensitive else (setting.setting_value if setting else None),
        configured=(
            bool(str(setting.setting_value or "").strip())
            if sensitive and setting
            else (False if sensitive else None)
        ),
    ))


@router.post("/save", response_model=ResultObject[str])
async def save_setting(
    req: SaveSettingReqDTO,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if is_sensitive_setting_key(req.setting_key):
        return ResultObject.failed(
            "Sensitive settings must be managed through their dedicated endpoint",
            code=403,
        )
    result = await db.execute(
        select(XianyuSysSetting).where(XianyuSysSetting.setting_key == req.setting_key)
    )
    setting = result.scalar_one_or_none()
    if setting:
        setting.setting_value = req.setting_value
    else:
        db.add(XianyuSysSetting(setting_key=req.setting_key, setting_value=req.setting_value))
    await db.commit()
    return ResultObject.success("保存成功")


@router.post("/list")
async def list_settings(
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    result = await db.execute(select(XianyuSysSetting).order_by(XianyuSysSetting.setting_key.asc()))
    settings_rows = result.scalars().all()
    payload = []
    for item in settings_rows:
        if is_sensitive_setting_key(item.setting_key):
            payload.append(
                {
                    "setting_key": item.setting_key,
                    "setting_value": None,
                    "configured": bool(str(item.setting_value or "").strip()),
                }
            )
        else:
            payload.append(
                {"setting_key": item.setting_key, "setting_value": item.setting_value}
            )
    return ResultObject.success(payload)


@router.post("/delete")
async def delete_setting(
    req: dict = {},
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    setting_key = req.get("setting_key") or req.get("settingKey")
    if is_sensitive_setting_key(setting_key):
        return ResultObject.failed(
            "Sensitive settings must be cleared through their dedicated endpoint",
            code=403,
        )
    if setting_key:
        result = await db.execute(
            select(XianyuSysSetting).where(XianyuSysSetting.setting_key == setting_key)
        )
        setting = result.scalar_one_or_none()
        if setting:
            await db.delete(setting)
            await db.commit()
    return ResultObject.success("删除成功")


@operation_log_router.get("/list", response_model=ResultObject[list])
async def list_operation_logs(
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    result = await db.execute(
        select(XianyuOperationLog)
        .order_by(XianyuOperationLog.id.desc())
        .limit(100)
    )
    logs = result.scalars().all()
    return ResultObject.success([
        {
            "id": item.id,
            "operator": item.operator,
            "operationType": item.operation_type,
            "operationDesc": item.operation_desc,
            "targetType": item.target_type,
            "targetId": item.target_id,
            "ipAddress": item.ip_address,
            "createdTime": str(item.created_time) if item.created_time else None,
        }
        for item in logs
    ])


@operation_log_router.post("/list", response_model=ResultObject[list])
async def list_operation_logs_post(
    req: dict = {},
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """POST /api/operationLog/list - 前端 operationLogs.js 使用 POST 调用"""
    page = int(req.get("page") or req.get("current") or 1)
    page_size = int(req.get("pageSize") or req.get("size") or 20)
    page_size = max(1, min(page_size, 200))
    offset = (page - 1) * page_size
    result = await db.execute(
        select(XianyuOperationLog)
        .order_by(XianyuOperationLog.id.desc())
        .offset(offset)
        .limit(page_size)
    )
    logs = result.scalars().all()
    return ResultObject.success([
        {
            "id": item.id,
            "operator": item.operator,
            "operationType": item.operation_type,
            "operationDesc": item.operation_desc,
            "targetType": item.target_type,
            "targetId": item.target_id,
            "ipAddress": item.ip_address,
            "createdTime": str(item.created_time) if item.created_time else None,
        }
        for item in logs
    ])


@notification_router.get("/list", response_model=ResultObject[list])
async def list_notifications(
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    result = await db.execute(
        select(Notification).order_by(Notification.id.desc()).limit(50)
    )
    notifications = result.scalars().all()
    return ResultObject.success([
        {
            "id": item.id,
            "channel": item.notification_type,
            "title": item.title,
            "content": item.content,
            "status": item.is_read,
            "createdTime": str(item.created_time) if item.created_time else None,
        }
        for item in notifications
    ])


@system_info_router.get("/info", response_model=ResultObject[dict])
async def get_system_info():
    return ResultObject.success({
        "version": "1.0.0-python",
        "language": "Python",
        "framework": "FastAPI",
        "database": "MySQL",
        "port": settings.server_port,
        "mode": "open-source-single-admin",
    })


@system_info_router.get("/runtime-config", response_model=ResultObject[dict])
async def get_runtime_config():
    return ResultObject.success({
        "appEnv": settings.app_env,
        "docsEnabled": settings.docs_enabled,
        "openSource": True,
        "authMode": "single-admin",
    })


@system_info_router.get("/about-content", response_model=ResultObject[dict])
async def get_about_content(
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    del db, current_user
    content = await proxy_get_about_content()
    return ResultObject.success(content)


@system_info_router.get("/open-source-config", response_model=ResultObject[dict])
async def get_open_source_config(
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    config = await load_open_source_config(db)
    return ResultObject.success(build_public_open_source_config(config))


@system_info_router.put("/open-source-config", response_model=ResultObject[dict])
async def update_open_source_config(
    payload: dict | None = None,
    request: Request = None,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        config = await save_open_source_config(db, payload or {})
        db.add(XianyuOperationLog(
            operator=current_user.get("username", settings.admin_username),
            operation_type="update_open_source_config",
            operation_desc="更新开源版系统配置",
            target_type="system",
            target_id="open_source_config",
            ip_address=get_client_ip(request) if request else "127.0.0.1",
        ))
        await db.commit()
        invalidate_model_config_cache()
        return ResultObject.success(build_public_open_source_config(config))
    except ValueError as exc:
        logger.warning(
            "Rejected unsafe open-source config update errorType=%s",
            type(exc).__name__,
        )
        await db.rollback()
        return ResultObject.validate_failed(str(exc))
    except Exception as exc:
        logger.error("Failed to update open-source config", exc_info=True)
        await db.rollback()
        return ResultObject.internal_error()


@system_info_router.get("/runtime-status", response_model=ResultObject[dict])
async def get_runtime_status(
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    config = await load_open_source_config(db)
    del current_user
    commercial_runtime = await get_commercial_bridge_runtime()

    db_connected = False
    db_version = ""
    try:
        version_result = await db.execute(text("SELECT VERSION()"))
        db_version = str(version_result.scalar() or "")
        db_connected = True
    except Exception as exc:
        logger.warning("Failed to read MySQL runtime status errorType=%s", type(exc).__name__)

    redis_connected = False
    redis_memory = ""
    redis_mode = "unavailable"
    try:
        if await is_redis_available():
            redis_client = await get_redis()
        else:
            redis_client = None
        if redis_client is not None:
            redis_connected = True
            redis_mode = "redis"
            try:
                redis_info = await redis_client.info("memory")
                redis_memory = str(redis_info.get("used_memory_human") or redis_info.get("used_memory") or "")
            except Exception:
                redis_memory = ""
    except Exception as exc:
        logger.warning("Failed to read Redis runtime status errorType=%s", type(exc).__name__)

    general_model = (config or {}).get("generalModel") or {}
    embedding_model = (config or {}).get("embeddingModel") or {}
    return ResultObject.success({
        "dbConnected": db_connected,
        "dbVersion": db_version,
        "redisConnected": redis_connected,
        "redisMemory": redis_memory,
        "redisMode": redis_mode,
        "crawlerBaseUrl": settings.crawler_base_url,
        "commercialBridgeConfigured": bool(commercial_runtime.get("commercialBridgeConfigured")),
        "commercialBridgeConnected": bool(commercial_runtime.get("commercialBridgeConnected")),
        "commercialBridgeMode": commercial_runtime.get("commercialBridgeMode") or "local-fallback",
        "commercialBridgeHealthOk": bool(commercial_runtime.get("commercialBridgeHealthOk")),
        "commercialAdminHealthOk": bool(commercial_runtime.get("commercialAdminHealthOk")),
        "commercialUserHealthOk": bool(commercial_runtime.get("commercialUserHealthOk")),
        "commercialPaidAdPlacementEnforced": bool(
            commercial_runtime.get("commercialPaidAdPlacementEnforced")
        ),
        "commercialBridgeSiteCode": commercial_runtime.get("commercialBridgeSiteCode") or "",
        "commercialBridgeMessage": commercial_runtime.get("commercialBridgeMessage") or "",
        "commercialFrontendUrl": commercial_runtime.get("commercialFrontendUrl") or "",
        "generalModelConfigured": is_ai_configured(general_model),
        "embeddingModelConfigured": is_ai_configured(embedding_model),
    })


@system_info_router.post("/currentUser", response_model=ResultObject[dict])
async def get_current_user_info(
    current_user: dict = Depends(get_current_user),
):
    return ResultObject.success({
        "userId": current_user.get("user_id", 0),
        "username": current_user.get("username", settings.admin_username),
        "role": current_user.get("role", "admin"),
        "avatar": "",
    })


@system_info_router.post("/changePassword", response_model=ResultObject[str])
async def change_password(
    req: ChangePasswordReqDTO,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    error = await update_admin_password(
        db,
        old_password=req.old_password,
        new_password=req.new_password,
        operator=current_user.get("username", settings.admin_username),
        ip_address=request_client_ip(request),
        operation_desc="管理员修改登录密码",
        target_type="auth",
    )
    if error:
        return ResultObject.validate_failed(error)
    return ResultObject.success("密码修改成功")


@system_info_router.get("/update-info", response_model=ResultObject[dict])
async def get_update_info(
    current_user: dict = Depends(get_current_user),
):
    del current_user
    # APP_VERSION is injected by Vite on the frontend; the backend uses the
    # package version read from app metadata if available, else "1.0.0".
    current_version = "1.0.0"
    try:
        from importlib.metadata import PackageNotFoundError, version
        try:
            current_version = version("xianyu-assistant")
        except PackageNotFoundError:
            pass
    except ImportError:
        pass
    try:
        payload = await build_update_info(current_version)
        return ResultObject.success(payload)
    except Exception:
        logger.error("Failed to build update info", exc_info=True)
        return ResultObject.internal_error()


@system_info_router.post("/update-feedback", response_model=ResultObject[str])
async def report_update_feedback(
    payload: dict | None = None,
    request: Request = None,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if not isinstance(payload, dict):
        payload = {}
    success = bool(payload.get("success"))
    from_version = str(payload.get("fromVersion") or "")
    to_version = str(payload.get("toVersion") or "")
    deployment_mode = str(payload.get("deploymentMode") or "")
    note = str(payload.get("note") or "")[:500]
    operator = current_user.get("username", settings.admin_username)
    desc = f"用户反馈更新执行结果: {from_version}->{to_version}, mode={deployment_mode}, success={success}"
    if note:
        desc += f", note={note}"
    db.add(XianyuOperationLog(
        operator=operator,
        operation_type="update_pull",
        operation_desc=desc,
        target_type="system",
        target_id="update",
        ip_address=request_client_ip(request) if request else "127.0.0.1",
    ))
    try:
        await db.commit()
    except Exception:
        logger.error("Failed to persist update feedback", exc_info=True)
        await db.rollback()
        return ResultObject.internal_error()
    return ResultObject.success("反馈已记录")


# ============================================================
# 远程滑块求解配置
# ============================================================

@system_info_router.get("/remote-slider-config", response_model=ResultObject[dict])
async def get_remote_slider_config_route(
    current_user: dict = Depends(get_current_user),
):
    """获取远程滑块求解配置（不返回明文 API Key）。"""
    try:
        from ....core.database import get_db as _get_db
        async for db in _get_db():
            config = await load_remote_slider_config(db)
            return ResultObject.success(build_public_remote_slider_config(config))
    except Exception as exc:
        logger.error("获取远程滑块求解配置失败 errorType=%s", type(exc).__name__)
        return ResultObject.internal_error()


@system_info_router.post("/remote-slider-config", response_model=ResultObject[dict])
async def update_remote_slider_config_route(
    data: dict = {},
    current_user: dict = Depends(get_current_user),
):
    """保存远程滑块求解配置。"""
    try:
        from ....core.database import get_db as _get_db
        async for db in _get_db():
            config = await save_remote_slider_config(db, data)
            await db.commit()
            return ResultObject.success(build_public_remote_slider_config(config))
    except ValueError as exc:
        # 预检验失败：返回明确的错误信息，不开启远程滑块
        logger.warning("远程滑块求解预检验失败: %s", str(exc))
        return ResultObject.failed(str(exc), code=400)
    except Exception as exc:
        logger.error("保存远程滑块求解配置失败 errorType=%s", type(exc).__name__)
        return ResultObject.internal_error()


@system_info_router.post("/remote-slider-precheck", response_model=ResultObject[dict])
async def precheck_remote_slider_route(
    data: dict = {},
    current_user: dict = Depends(get_current_user),
):
    """预检验远程滑块求解服务的连通性与凭证有效性。"""
    try:
        api_url = str(data.get("apiUrl") or "").strip()
        api_key = str(data.get("apiKey") or "").strip()
        # 如果未传入 apiKey，尝试从已保存的配置中读取
        if not api_key:
            from ....core.database import get_db as _get_db
            async for db in _get_db():
                existing = await load_remote_slider_config(db)
                api_key = existing.get("apiKey") or ""
                break
        result = await precheck_remote_slider(api_url, api_key)
        return ResultObject.success(result)
    except Exception as exc:
        logger.error("远程滑块预检验失败 errorType=%s", type(exc).__name__)
        return ResultObject.internal_error()
