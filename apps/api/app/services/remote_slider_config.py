"""
远程滑块求解配置服务
====================
管理开源版对接商业版远程滑块求解 API 的配置。

配置存储在 xianyu_sys_setting 表，key = "remote_slider.config"。
JSON 结构: {
    "enabled": bool,           # 是否启用远程滑块求解
    "apiUrl": str,             # 远程 API 地址（由部署方/服务方提供，开源版不内置商业版域名）
    "apiKey": str,             # 对接密钥（加密存储）
    "triggerScenes": list[str],# 自动触发滑块求解的场景列表
    "apiUrlConfigured": bool,  # 是否已配置 API 地址（公开返回时用）
    "apiKeyConfigured": bool   # 是否已配置密钥（公开返回时用）
}

triggerScenes 可选值：
    "ws_failure"        — WS 失效（Token API 返回 captcha/expired），默认且必选，不可移除
    "cookie_keepalive"  — Cookie 保活策略检测到滑块验证时触发
    "heartbeat_stop"    — WebSocket 心跳停跳（45秒无消息）时触发

单租户版：无 tenant_id。
"""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.database import async_session
from ..models.entities import XianyuSysSetting
from .sensitive_config import (
    REMOTE_SLIDER_API_KEY_PURPOSE,
    decrypt_runtime_secret,
    prepare_secret_for_storage,
)

import httpx
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

REMOTE_SLIDER_CONFIG_KEY = "remote_slider.config"

# 默认值留空，由部署方/服务方提供；开源版不内置商业版域名，避免暴露后台地址
DEFAULT_REMOTE_API_URL = ""

# 触发场景常量
TRIGGER_SCENE_WS_FAILURE = "ws_failure"
TRIGGER_SCENE_COOKIE_KEEPALIVE = "cookie_keepalive"
TRIGGER_SCENE_HEARTBEAT_STOP = "heartbeat_stop"

# 所有合法的触发场景
VALID_TRIGGER_SCENES: set[str] = {
    TRIGGER_SCENE_WS_FAILURE,
    TRIGGER_SCENE_COOKIE_KEEPALIVE,
    TRIGGER_SCENE_HEARTBEAT_STOP,
}

# 默认触发场景：ws_failure 为必选项
DEFAULT_TRIGGER_SCENES: list[str] = [TRIGGER_SCENE_WS_FAILURE]

# 约束5：运行时预检结果缓存（30 秒）
# 避免每次 try_remote_solve 都发预检请求，减少不必要的网络开销
# 缓存 key = (api_url, api_key)，value = (precheck_result, expired_at_timestamp)
_PRECHECK_CACHE: dict[tuple[str, str], tuple[dict, float]] = {}
_PRECHECK_CACHE_TTL_SEC = 30.0


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


def _normalize_trigger_scenes(raw: Any) -> list[str]:
    """规范化触发场景列表，确保 ws_failure 始终存在且去重。"""
    if not isinstance(raw, (list, tuple)):
        scenes: list[str] = []
    else:
        scenes = [str(s).strip() for s in raw if str(s).strip()]

    # 过滤掉非法值，保持顺序去重
    seen: set[str] = set()
    result: list[str] = []
    for s in scenes:
        if s in VALID_TRIGGER_SCENES and s not in seen:
            seen.add(s)
            result.append(s)

    # ws_failure 为必选项，始终包含
    if TRIGGER_SCENE_WS_FAILURE not in seen:
        result.insert(0, TRIGGER_SCENE_WS_FAILURE)

    return result


def _validate_api_url(url: str) -> tuple[bool, str]:
    """校验 API URL 格式，返回 (是否合法, 错误信息)。"""
    if not url:
        return False, "API 链接不能为空"
    try:
        parsed = urlparse(url)
    except Exception:
        return False, "API 链接格式无效"
    if parsed.scheme not in ("http", "https"):
        return False, "API 链接必须以 http:// 或 https:// 开头"
    if not parsed.hostname:
        return False, "API 链接缺少有效的主机名"
    if not parsed.path or parsed.path == "/":
        return False, "API 链接必须包含完整路径（如 /api/v1/slider/solve）"
    return True, ""


async def precheck_remote_slider(api_url: str, api_key: str) -> dict[str, Any]:
    """预检验远程滑块求解服务的连通性与凭证有效性。

    返回结构：
    {
        "ok": bool,           # 是否通过预检验
        "reachable": bool,    # 服务是否可达
        "authed": bool,       # 凭证是否有效
        "message": str,       # 提示信息
        "statusCode": int,    # HTTP 状态码（0 表示未收到响应）
    }
    """
    # 1. URL 格式校验
    url_ok, url_err = _validate_api_url(api_url)
    if not url_ok:
        return {"ok": False, "reachable": False, "authed": False,
                "message": url_err, "statusCode": 0}

    # 2. API Key 非空校验
    if not api_key or not api_key.strip():
        return {"ok": False, "reachable": False, "authed": False,
                "message": "对接密钥不能为空", "statusCode": 0}

    # 3. 连通性测试：发送 GET 请求到 apiUrl
    #    - 200/400：服务在线
    #    - 401/403：服务在线但凭证无效（Key 错误或权限不足）
    #    - 405：服务在线（方法不允许，但说明端点存在）
    #    - 连接超时/拒绝：服务不可达
    try:
        async with httpx.AsyncClient(
            timeout=10.0,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            resp = await client.get(
                api_url,
                headers={
                    "X-Api-Key": api_key.strip(),
                    "X-Precheck": "true",
                },
            )
            status_code = resp.status_code
    except httpx.ConnectTimeout:
        return {"ok": False, "reachable": False, "authed": False,
                "message": "连接超时，远程滑块求解服务不可达，请检查 API 链接是否正确",
                "statusCode": 0}
    except httpx.ConnectError:
        return {"ok": False, "reachable": False, "authed": False,
                "message": "无法连接到远程滑块求解服务，请检查 API 链接和网络",
                "statusCode": 0}
    except Exception as exc:
        return {"ok": False, "reachable": False, "authed": False,
                "message": f"预检验请求失败：{type(exc).__name__}",
                "statusCode": 0}

    # 4. 根据状态码判断
    reachable = True
    if status_code in (200, 400, 405):
        # 服务在线，凭证可能有效（无法完全确认，但至少端点存在）
        return {"ok": True, "reachable": True, "authed": True,
                "message": "预检验通过，远程滑块求解服务可正常连接",
                "statusCode": status_code}
    elif status_code in (401, 403):
        # 服务在线但凭证无效
        return {"ok": False, "reachable": True, "authed": False,
                "message": "远程服务在线，但对接密钥无效或权限不足，请检查 API Key",
                "statusCode": status_code}
    elif status_code == 404:
        return {"ok": False, "reachable": True, "authed": False,
                "message": "API 链接路径不存在（404），请检查 URL 是否正确",
                "statusCode": status_code}
    else:
        return {"ok": False, "reachable": True, "authed": False,
                "message": f"预检验返回异常状态码：{status_code}",
                "statusCode": status_code}


def default_remote_slider_config() -> dict[str, Any]:
    return {
        "enabled": False,
        "apiUrl": DEFAULT_REMOTE_API_URL,
        "apiKey": "",
        "triggerScenes": list(DEFAULT_TRIGGER_SCENES),
    }


def normalize_remote_slider_config(payload: Any) -> dict[str, Any]:
    raw = payload if isinstance(payload, dict) else {}
    defaults = default_remote_slider_config()
    return {
        "enabled": _as_bool(raw.get("enabled")),
        "apiUrl": _as_text(raw.get("apiUrl")) or defaults["apiUrl"],
        "apiKey": _as_text(raw.get("apiKey")),
        "triggerScenes": _normalize_trigger_scenes(raw.get("triggerScenes")),
    }


def build_public_remote_slider_config(config: Any) -> dict[str, Any]:
    """Return config without exposing the API key to the browser."""
    public = normalize_remote_slider_config(config)
    api_key = _as_text(public.get("apiKey"))
    public["apiKey"] = ""
    public["apiKeyConfigured"] = bool(api_key)
    public["apiUrlConfigured"] = bool(_as_text(public.get("apiUrl")))
    # triggerScenes 已在 normalize 中规范化，直接保留
    return public


async def _load_raw_config(db: AsyncSession) -> str:
    result = await db.execute(
        select(XianyuSysSetting).where(XianyuSysSetting.setting_key == REMOTE_SLIDER_CONFIG_KEY)
    )
    row = result.scalar_one_or_none()
    if row and row.setting_value is not None:
        return row.setting_value
    return ""


async def load_remote_slider_config(db: AsyncSession) -> dict[str, Any]:
    """Load and decrypt the remote slider config."""
    raw_value = await _load_raw_config(db)
    try:
        stored = json.loads(raw_value) if raw_value else {}
    except Exception:
        stored = {}

    config = normalize_remote_slider_config(stored)
    # Decrypt API key
    encrypted_key = _as_text(stored.get("apiKey"))
    if encrypted_key:
        try:
            config["apiKey"] = decrypt_runtime_secret(
                encrypted_key,
                purpose=REMOTE_SLIDER_API_KEY_PURPOSE,
            )
        except Exception as exc:
            logger.warning("Failed to decrypt remote slider API key: %s", type(exc).__name__)
            config["apiKey"] = ""
    return config


async def save_remote_slider_config(db: AsyncSession, payload: Any) -> dict[str, Any]:
    """Save the remote slider config, encrypting the API key."""
    existing = await load_remote_slider_config(db)
    raw = payload if isinstance(payload, dict) else {}
    config = normalize_remote_slider_config(raw)

    # Preserve existing API key if incoming is empty (don't overwrite with blank)
    incoming_key = _as_text(config.get("apiKey"))
    if not incoming_key:
        config["apiKey"] = _as_text(existing.get("apiKey"))

    # 开启远程滑块求解时，强制预检验 URL、密钥、连通性
    if config["enabled"]:
        url_ok, url_err = _validate_api_url(config["apiUrl"])
        if not url_ok:
            raise ValueError(url_err)
        if not config["apiKey"].strip():
            raise ValueError("开启远程滑块求解时，对接密钥不能为空")
        # 连通性预检验
        precheck = await precheck_remote_slider(config["apiUrl"], config["apiKey"])
        if not precheck["ok"]:
            raise ValueError(precheck["message"])

    # Encrypt API key for storage
    storage_config = {
        "enabled": config["enabled"],
        "apiUrl": config["apiUrl"],
        "apiKey": prepare_secret_for_storage(
            incoming=config["apiKey"],
            purpose=REMOTE_SLIDER_API_KEY_PURPOSE,
        )
        or "",
        "triggerScenes": config["triggerScenes"],
    }

    raw_value = await _load_raw_config(db)
    result = await db.execute(
        select(XianyuSysSetting).where(XianyuSysSetting.setting_key == REMOTE_SLIDER_CONFIG_KEY)
    )
    row = result.scalar_one_or_none()
    if row:
        row.setting_value = json.dumps(storage_config, ensure_ascii=False)
    else:
        db.add(
            XianyuSysSetting(
                setting_key=REMOTE_SLIDER_CONFIG_KEY,
                setting_value=json.dumps(storage_config, ensure_ascii=False),
            )
        )
    return config


async def load_remote_slider_config_from_store() -> dict[str, Any]:
    async with async_session() as db:
        return await load_remote_slider_config(db)


async def is_remote_slider_enabled() -> bool:
    """Check if remote slider solve is enabled (runtime check for captcha_solver)."""
    try:
        config = await load_remote_slider_config_from_store()
        return bool(config.get("enabled") and config.get("apiUrl") and config.get("apiKey"))
    except Exception as exc:
        logger.warning("Failed to check remote slider enabled: %s", type(exc).__name__)
        return False


async def is_trigger_scene_enabled(scene: str) -> bool:
    """检查指定触发场景是否已启用（运行时检查）。

    ws_failure 始终返回 True（必选项），即使配置中缺失也会兜底为 True。
    其他场景需在 triggerScenes 列表中才会返回 True。
    """
    if scene == TRIGGER_SCENE_WS_FAILURE:
        return True
    try:
        config = await load_remote_slider_config_from_store()
        scenes = config.get("triggerScenes") or DEFAULT_TRIGGER_SCENES
        return scene in scenes
    except Exception as exc:
        logger.warning("Failed to check trigger scene '%s': %s", scene, type(exc).__name__)
        return False


async def precheck_remote_slider_cached(api_url: str, api_key: str) -> dict[str, Any]:
    """约束5：运行时预检带 30s 缓存。

    try_remote_solve 入口调用此函数：
    - 30 秒内复用预检结果，避免每次求解都发预检请求
    - 预检失败直接返回，不发起 120s 超时请求，让用户看到清晰的"服务不可用"记录

    缓存 key 为 (api_url, api_key)，配置变更时缓存自然过期。
    """
    import time as _time

    cache_key = (api_url, api_key)
    now = _time.time()

    # 命中缓存且未过期
    cached = _PRECHECK_CACHE.get(cache_key)
    if cached:
        result, expired_at = cached
        if now < expired_at:
            logger.debug("远程滑块预检命中缓存 api_url=%s", api_url)
            return result

    # 未命中或已过期，执行真实预检
    result = await precheck_remote_slider(api_url, api_key)

    # 仅缓存成功或明确的失败（不缓存异常，异常时下次重新预检）
    _PRECHECK_CACHE[cache_key] = (result, now + _PRECHECK_CACHE_TTL_SEC)
    logger.debug(
        "远程滑块预检已缓存 api_url=%s ok=%s ttl=%ss",
        api_url, result.get("ok"), _PRECHECK_CACHE_TTL_SEC,
    )
    return result
