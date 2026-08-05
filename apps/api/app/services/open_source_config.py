from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from ..core.database import async_session
from ..core.upload_security import (
    UnsafeRemoteURLError,
    
)
from ..models.entities import ModelConfig, XianyuSysSetting
from .sensitive_config import (
    MODEL_CONFIG_API_KEY_PURPOSE,
    decrypt_runtime_secret,
    decrypt_system_config_secrets,
    encrypt_system_config_secrets,
    prepare_secret_for_storage,
)

SYSTEM_CONFIG_SETTING_KEY = "open_source.system_config"
CRAWLER_BASE_URL_SETTING_KEY = "crawler_base_url"
logger = logging.getLogger(__name__)


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


def _load_json(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def _clone_config(value: Any) -> dict[str, Any]:
    raw = _load_json(value, {}) if not isinstance(value, dict) else value
    if not isinstance(raw, dict):
        return {}
    return json.loads(json.dumps(raw))


def preserve_blank_secret_values(payload: Any, existing_config: Any) -> dict[str, Any]:
    """Keep stored credentials when a settings form submits an empty secret input."""
    merged = _clone_config(payload)
    existing = _clone_config(existing_config)

    for section in ("generalModel", "embeddingModel"):
        incoming_section = merged.get(section)
        if not isinstance(incoming_section, dict):
            incoming_section = {}
            merged[section] = incoming_section
        existing_section = existing.get(section)
        if not isinstance(existing_section, dict):
            existing_section = {}
        top_level_clear = merged.get(
            "clearGeneralModelApiKey"
            if section == "generalModel"
            else "clearEmbeddingModelApiKey"
        )
        if _as_bool(incoming_section.get("clearApiKey")) or _as_bool(top_level_clear):
            incoming_section["apiKey"] = ""
        elif not _as_text(incoming_section.get("apiKey")):
            incoming_section["apiKey"] = _as_text(existing_section.get("apiKey"))

    return merged


def build_public_open_source_config(config: Any) -> dict[str, Any]:
    """Return configuration metadata without exposing credentials to the browser."""
    public = _clone_config(config)

    for section in ("generalModel", "embeddingModel"):
        values = public.get(section)
        if not isinstance(values, dict):
            values = {}
            public[section] = values
        api_key = _as_text(values.get("apiKey"))
        values["apiKey"] = ""
        values["apiKeyConfigured"] = bool(api_key)

    return public


def default_open_source_config() -> dict[str, Any]:
    return {
        "siteName": "闲鱼助手开源版",
        "icp": "",
        "logoUrl": "/xya/brand/brand_004.png",
        "crawlerBaseUrl": settings.crawler_base_url,
        "generalModel": {
            "provider": "",
            "modelName": settings.ai_provider_model,
            "baseUrl": settings.ai_provider_base_url,
            "apiKey": settings.ai_provider_api_key,
            "requestTimeout": settings.ai_provider_timeout_seconds,
            "polishKeywords": "",
            "polishForbiddenKeywords": "",
        },
        "embeddingModel": {
            "provider": "",
            "modelName": settings.embedding_model,
            "baseUrl": settings.embedding_base_url,
            "apiKey": settings.embedding_api_key,
        },
    }


def normalize_open_source_config(payload: Any) -> dict[str, Any]:
    raw = _load_json(payload, {}) if not isinstance(payload, dict) else payload
    defaults = default_open_source_config()
    general_payload = raw.get("generalModel") if isinstance(raw, dict) else {}
    embedding_payload = raw.get("embeddingModel") if isinstance(raw, dict) else {}
    if not isinstance(general_payload, dict):
        general_payload = {}
    if not isinstance(embedding_payload, dict):
        embedding_payload = {}

    general_defaults = defaults["generalModel"]
    embedding_defaults = defaults["embeddingModel"]

    canonical_model_name = (
        _as_text(general_payload.get("modelName"))
        or _as_text(general_payload.get("realModel"))
        or general_defaults["modelName"]
    )

    return {
        "siteName": _as_text(raw.get("siteName")) or defaults["siteName"],
        "icp": _as_text(raw.get("icp")),
        "logoUrl": _as_text(raw.get("logoUrl")) or defaults["logoUrl"],
        "crawlerBaseUrl": _as_text(raw.get("crawlerBaseUrl")) or defaults["crawlerBaseUrl"],
        "generalModel": {
            "provider": _as_text(general_payload.get("provider")),
            "modelName": canonical_model_name,
            "baseUrl": _as_text(general_payload.get("baseUrl")) or general_defaults["baseUrl"],
            "apiKey": _as_text(general_payload.get("apiKey")) or general_defaults["apiKey"],
            "requestTimeout": _as_int(
                general_payload.get("requestTimeout"),
                general_defaults["requestTimeout"],
            ),
            "polishKeywords": _as_text(general_payload.get("polishKeywords")),
            "polishForbiddenKeywords": _as_text(general_payload.get("polishForbiddenKeywords")),
        },
        "embeddingModel": {
            "provider": _as_text(embedding_payload.get("provider")),
            "modelName": _as_text(embedding_payload.get("modelName"))
            or embedding_defaults["modelName"],
            "baseUrl": _as_text(embedding_payload.get("baseUrl")) or embedding_defaults["baseUrl"],
            "apiKey": _as_text(embedding_payload.get("apiKey")) or embedding_defaults["apiKey"],
        },
    }


def _endpoint_origin(value: Any) -> tuple[str, str, int] | None:
    from urllib.parse import urlparse

    parsed = urlparse(_as_text(value))
    if not parsed.scheme or not parsed.hostname:
        return None
    try:
        port = parsed.port or (443 if parsed.scheme.casefold() == "https" else 80)
    except ValueError:
        return None
    return parsed.scheme.casefold(), parsed.hostname.rstrip(".").casefold(), port


async def load_setting_value(db: AsyncSession, key: str, default: str = "") -> str:
    result = await db.execute(
        select(XianyuSysSetting).where(XianyuSysSetting.setting_key == key)
    )
    row = result.scalar_one_or_none()
    if row and row.setting_value is not None:
        return row.setting_value
    return default


async def save_setting_value(db: AsyncSession, key: str, value: str) -> None:
    result = await db.execute(
        select(XianyuSysSetting).where(XianyuSysSetting.setting_key == key)
    )
    row = result.scalar_one_or_none()
    if row:
        row.setting_value = value
    else:
        db.add(XianyuSysSetting(setting_key=key, setting_value=value))


async def _load_model_config_fallback(
    db: AsyncSession,
    model_types: tuple[str, ...],
) -> ModelConfig | None:
    try:
        result = await db.execute(
            select(ModelConfig)
            .where(
                ModelConfig.deleted == 0,
                ModelConfig.status == 1,
                ModelConfig.model_type.in_(model_types),
            )
            .order_by(ModelConfig.is_default.desc(), ModelConfig.id.desc())
        )
        return result.scalars().first()
    except Exception as exc:
        logger.warning(
            "Failed to load model_config fallback modelTypes=%s errorType=%s",
            model_types,
            type(exc).__name__,
        )
        return None


async def load_open_source_config(db: AsyncSession) -> dict[str, Any]:
    raw_value = await load_setting_value(db, SYSTEM_CONFIG_SETTING_KEY, "")
    config = normalize_open_source_config(
        decrypt_system_config_secrets(_load_json(raw_value, {}))
    )

    # The crawler receives encrypted account credentials and is therefore a
    # deployment boundary, not a browser-writable preference. Ignore legacy
    # database values and always expose the endpoint actually used at runtime.
    config["crawlerBaseUrl"] = settings.crawler_base_url
    general_model = await _load_model_config_fallback(db, ("general", "chat"))
    if general_model:
        if not config["generalModel"]["provider"]:
            config["generalModel"]["provider"] = _as_text(general_model.provider)
        if not config["generalModel"]["modelName"]:
            config["generalModel"]["modelName"] = _as_text(general_model.model_name)
        if not config["generalModel"]["baseUrl"]:
            config["generalModel"]["baseUrl"] = _as_text(general_model.base_url)
        if not config["generalModel"]["apiKey"]:
            config["generalModel"]["apiKey"] = decrypt_runtime_secret(
                general_model.api_key,
                purpose=MODEL_CONFIG_API_KEY_PURPOSE,
            )
        if not config["generalModel"]["requestTimeout"]:
            params_json = (
                general_model.params_json
                if isinstance(general_model.params_json, dict)
                else {}
            )
            config["generalModel"]["requestTimeout"] = _as_int(
                params_json.get("requestTimeout"),
                settings.ai_provider_timeout_seconds,
            )

    embedding_model = await _load_model_config_fallback(db, ("embedding",))
    if embedding_model:
        if not config["embeddingModel"]["provider"]:
            config["embeddingModel"]["provider"] = _as_text(embedding_model.provider)
        if not config["embeddingModel"]["modelName"]:
            config["embeddingModel"]["modelName"] = _as_text(embedding_model.model_name)
        if not config["embeddingModel"]["baseUrl"]:
            config["embeddingModel"]["baseUrl"] = _as_text(embedding_model.base_url)
        if not config["embeddingModel"]["apiKey"]:
            config["embeddingModel"]["apiKey"] = decrypt_runtime_secret(
                embedding_model.api_key,
                purpose=MODEL_CONFIG_API_KEY_PURPOSE,
            )

    return normalize_open_source_config(config)


async def save_open_source_config(db: AsyncSession, payload: Any) -> dict[str, Any]:
    existing_config = await load_open_source_config(db)
    raw = payload if isinstance(payload, dict) else {}
    raw_sections = {
        section: raw.get(section) if isinstance(raw.get(section), dict) else {}
        for section in ("generalModel", "embeddingModel")
    }
    config = normalize_open_source_config(
        preserve_blank_secret_values(payload, existing_config)
    )
    config["crawlerBaseUrl"] = settings.crawler_base_url
    for section in ("generalModel", "embeddingModel"):
        values = config[section]
        raw_values = raw_sections[section]
        if "baseUrl" not in raw_values:
            continue
        candidate = _as_text(values.get("baseUrl"))
        if candidate and not candidate.startswith("http"):
            raise ValueError(f"{section}.baseUrl 必须以 http:// 或 https:// 开头")

        existing_values = existing_config.get(section) or {}
        existing_origin = _endpoint_origin(existing_values.get("baseUrl"))
        candidate_origin = _endpoint_origin(candidate)
        existing_key = _as_text(existing_values.get("apiKey"))
        incoming_key = _as_text(raw_values.get("apiKey"))
        if existing_origin != candidate_origin and existing_key and not incoming_key:
            raise ValueError(
                f"{section}.baseUrl 已切换到新主机；为防止泄漏已保存密钥，请重新输入该模型的 API Key"
            )
        values["baseUrl"] = candidate
    storage_config = encrypt_system_config_secrets(config)
    await save_setting_value(
        db,
        SYSTEM_CONFIG_SETTING_KEY,
        json.dumps(storage_config, ensure_ascii=False),
    )
    # Replace any legacy browser-written value with the effective deployment
    # endpoint so exports and diagnostics cannot claim a different target.
    await save_setting_value(
        db,
        CRAWLER_BASE_URL_SETTING_KEY,
        settings.crawler_base_url,
    )
    return config


async def load_open_source_config_from_store() -> dict[str, Any]:
    async with async_session() as db:
        return await load_open_source_config(db)
