import asyncio
import csv
import io
import json
import logging
import re
import time
import unicodedata
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.config import settings
from ....core.database import get_db
from ....core.logging_security import redact_sensitive_text
from ....core.response import ResultObject
from ....core.secret_store import decrypt_secret, encrypt_secret
from ....core.unavailable_features import (
    ACCOUNT_AUTO_RATE_UNAVAILABLE,
    ACCOUNT_LOGIN_CREDENTIAL_UNAVAILABLE,
    ACCOUNT_STRATEGY_UNAVAILABLE,
    FACE_VERIFICATION_UNAVAILABLE,
    feature_unavailable,
)
from ....services.commercial_bridge import (
    CommercialBridgeCapabilityUnavailable,
    CommercialBridgeError,
    CommercialBridgeNotConfigured,
    commercial_bridge_is_configured,
    get_commercial_bridge_config,
    proxy_append_feedback_reply,
    proxy_create_ad_application,
    proxy_create_ad_payment_order,
    proxy_close_ad_payment_order,
    proxy_get_ad_plan_list,
    proxy_get_ad_payment_methods,
    proxy_get_ad_payment_order,
    proxy_get_feedback_detail,
    proxy_get_feedback_stats,
    proxy_list_feedback,
    proxy_submit_feedback,
    proxy_get_text_ad_list,
    proxy_list_ad_applications,
    require_commercial_mutation_capability,
    require_paid_ad_creation_capabilities,
)
from ....services.ad_payment_order_attempt import (
    AdPaymentOrderAttemptError,
    AdPaymentOrderCommand,
    AdPaymentOrderCoordinator,
    AdPaymentRemoteNotExecuted,
    SqlAdPaymentOrderAttemptStore,
    payment_order_terminal_state,
)
from ....services.feishu_bot import send_text_message_result
from ....services.notify_dispatcher import (
    _render_template,
    _send_dingtalk,
    _send_email,
    _send_feishu,
    _send_pushplus,
    _send_webhook,
    _send_wechat_work,
)
from ....services.notification_test_attempt import (
    NotificationTestAttemptError,
    NotificationTestCommand,
    NotificationTestCoordinator,
    NotificationTestLease,
    SqlNotificationTestAttemptStore,
)
from ....services.scheduled_task_runtime import (
    SUPPORTED_TASK_TYPES,
    ScheduledTaskError,
    ScheduledTaskInput,
    ScheduledTaskRecord,
    ScheduledTaskRuntime,
    TaskBusyError,
    TaskConflictError,
    TaskNotFoundError,
    TaskValidationError,
    get_scheduled_task_runtime,
)
from ....services.sensitive_config import (
    AD_APPLICATION_STORE_PURPOSE,
    FEEDBACK_STORE_PURPOSE,
)
from ..deps import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(tags=["frontend-compat"])

DEFAULT_TENANT_ID = 1
NOTIFICATION_SETTINGS_KEY = "frontend.notification_settings"
NOTIFICATION_DELIVERY_LOGS_KEY = "frontend.notification_delivery_logs"
FEEDBACK_STORE_KEY = "frontend.feedback_store"
MAX_LOCAL_FEEDBACK_RECORDS = 1_000
MAX_LOCAL_FEEDBACK_REPLIES = 100
MAX_FEEDBACK_TITLE_LENGTH = 200
MAX_FEEDBACK_CONTENT_LENGTH = 5_000
MAX_FEEDBACK_CONTACT_LENGTH = 200
MAX_FEEDBACK_OWNER_LENGTH = 200
MAX_SENSITIVE_STORE_PLAINTEXT_BYTES = 40_000
AD_APPLICATION_STORE_KEY = "frontend.ad_applications_store"
AD_PAYMENT_METHOD_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
AD_IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{16,128}$")
AD_ORDER_NO_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
DEFAULT_NOTIFICATION_SETTINGS = {"inApp": True, "sendMode": "single", "channels": [], "events": []}
NOTIFICATION_SECRET_FIELDS = (
    "smtpPass",
    "secret",
    "verificationToken",
    "encryptKey",
    "webhookUrl",
    "token",
    "apiKey",
    "appSecret",
    "password",
)


def _commercial_unavailable_response(
    message: str,
    *,
    status_code: int = 503,
    configured: bool = False,
    reason: str = "commercial_bridge_not_configured",
) -> JSONResponse:
    result = ResultObject(
        code=status_code,
        msg=message,
        data={
            "status": "unavailable",
            "reason": reason,
            "commercialBridgeConfigured": configured,
            "retrySafe": False,
        },
    )
    return JSONResponse(
        status_code=status_code,
        content=result.model_dump(by_alias=True),
    )


def _commercial_unknown_response(message: str, *, operation: str) -> JSONResponse:
    """Represent an issued, idempotent bridge write whose outcome is unknown."""

    result = ResultObject(
        code=502,
        msg=message,
        data={
            "status": "unknown",
            "operation": operation,
            "reason": "commercial_bridge_result_unknown",
            "commercialBridgeConfigured": True,
            "retrySafe": True,
            "replaySafe": True,
        },
    )
    return JSONResponse(
        status_code=502,
        content=result.model_dump(by_alias=True),
    )


def _bounded_text(value: Any, field_label: str, max_length: int) -> str:
    normalized = str(value or "").strip()
    if len(normalized) > max_length:
        raise ValueError(f"{field_label}不能超过 {max_length} 个字符")
    return normalized


def _https_url(value: Any, field_label: str, *, allow_upload_path: bool = False) -> str:
    normalized = _bounded_text(value, field_label, 2_048)
    if allow_upload_path and normalized.startswith("/uploads/"):
        return normalized
    parsed = urlsplit(normalized)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError(f"{field_label}必须是有效的 HTTPS 地址")
    return normalized


def _validated_ad_application_payload(payload: Any) -> dict[str, Any]:
    source = payload if isinstance(payload, dict) else {}
    position_type = _bounded_text(source.get("positionType") or "sidebar_text", "广告位类型", 32).lower()
    if position_type not in {"home_carousel", "sidebar_text"}:
        raise ValueError("广告位类型不合法")
    plan_code = _bounded_text(source.get("planCode"), "广告套餐", 100)
    if not plan_code:
        raise ValueError("请选择广告套餐")
    idempotency_key = _bounded_text(source.get("idempotencyKey"), "申请意图幂等键", 128)
    if not AD_IDEMPOTENCY_KEY_PATTERN.fullmatch(idempotency_key):
        raise ValueError("申请意图幂等键缺失或不合法")

    company_name = _bounded_text(source.get("companyName"), "公司或主体名称", 200)
    if not company_name:
        raise ValueError("公司或主体名称不能为空")

    contact_name = _bounded_text(source.get("contactName"), "联系人", 200)
    contact_phone = _bounded_text(source.get("contactPhone"), "联系电话", 50)
    contact_wechat = _bounded_text(source.get("contactWechat"), "联系微信", 100)
    contact = _bounded_text(source.get("contact"), "联系方式", 200)
    if not (contact or contact_name or contact_phone or contact_wechat):
        raise ValueError("请填写联系方式")

    title = _bounded_text(source.get("title"), "广告标题", 80)
    if position_type == "sidebar_text" and not title:
        raise ValueError("广告标题不能为空")
    landing_url = _https_url(source.get("landingUrl"), "跳转链接")
    creative_image_url = _bounded_text(source.get("creativeImageUrl"), "轮播图地址", 2_048)
    if position_type == "home_carousel":
        if not creative_image_url:
            raise ValueError("请先上传轮播图")
        creative_image_url = _https_url(
            creative_image_url,
            "轮播图地址",
            allow_upload_path=True,
        )

    return {
        "positionType": position_type,
        "planCode": plan_code,
        "companyName": company_name,
        "contact": contact,
        "contactName": contact_name,
        "contactPhone": contact_phone,
        "contactWechat": contact_wechat,
        "title": title,
        "landingUrl": landing_url,
        "creativeImageUrl": creative_image_url if position_type == "home_carousel" else "",
        "budget": _bounded_text(source.get("budget"), "预算", 100),
        "startDate": _bounded_text(source.get("startDate"), "开始日期", 20),
        "durationDays": _bounded_text(source.get("durationDays"), "投放天数", 10),
        "remark": _bounded_text(source.get("remark"), "备注", 2_000),
        "idempotencyKey": idempotency_key,
    }


def _validated_feedback_payload(payload: Any) -> dict[str, str]:
    source = payload if isinstance(payload, dict) else {}
    category = _bounded_text(source.get("category") or "other", "反馈分类", 50).lower()
    if category not in {"bug", "feature", "experience", "suggestion", "other"}:
        raise ValueError("反馈分类不合法")
    title = _bounded_text(source.get("title"), "反馈标题", MAX_FEEDBACK_TITLE_LENGTH)
    content = _bounded_text(source.get("content"), "反馈内容", MAX_FEEDBACK_CONTENT_LENGTH)
    contact = _bounded_text(source.get("contact"), "联系方式", MAX_FEEDBACK_CONTACT_LENGTH)
    if not title:
        raise ValueError("反馈标题不能为空")
    if not content:
        raise ValueError("反馈内容不能为空")
    idempotency_key = _bounded_text(source.get("idempotencyKey"), "反馈意图幂等键", 128)
    if not AD_IDEMPOTENCY_KEY_PATTERN.fullmatch(idempotency_key):
        raise ValueError("反馈意图幂等键缺失或不合法")
    return {
        "category": category,
        "title": title,
        "content": content,
        "contact": contact,
        "idempotencyKey": idempotency_key,
    }


def _now() -> datetime:
    return datetime.now()


def _dt_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value)


def _to_bool(value: Any, fallback: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return int(value) == 1
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return fallback


def _load_json(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def _dump_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _json_text_or_default(value: Any, default: str = "{}") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        text_value = value.strip()
        return text_value or default
    return _dump_json(value)


def _task_config_value(value: Any) -> Any:
    if value is None:
        return {}
    if isinstance(value, str):
        text_value = value.strip()
        if not text_value:
            return {}
        return _load_json(text_value, {})
    if isinstance(value, (dict, list)):
        return value
    return {}


async def _get_setting_json(db: AsyncSession, key: str, default: Any) -> Any:
    result = await db.execute(
        text("SELECT setting_value FROM xianyu_sys_setting WHERE setting_key = :key LIMIT 1"),
        {"key": key},
    )
    row = result.first()
    if not row or row[0] in (None, ""):
        return default
    return _load_json(row[0], default)


async def _save_setting_json(db: AsyncSession, key: str, value: Any) -> None:
    now = _now()
    await db.execute(
        text(
            """
            INSERT INTO xianyu_sys_setting (setting_key, setting_value, created_time, updated_time)
            VALUES (:key, :value, :now, :now)
            ON DUPLICATE KEY UPDATE
              setting_value = VALUES(setting_value),
              updated_time = VALUES(updated_time)
            """
        ),
        {"key": key, "value": _dump_json(value), "now": now},
    )


class SensitiveStoreBusyError(RuntimeError):
    """The single-writer lock for a local PII store is unavailable."""


class SensitiveStoreCapacityError(RuntimeError):
    """The encrypted payload would exceed the bounded TEXT storage budget."""


async def _get_sensitive_setting_json(
    db: AsyncSession,
    key: str,
    default: Any,
    *,
    purpose: str,
) -> Any:
    result = await db.execute(
        text("SELECT setting_value FROM xianyu_sys_setting WHERE setting_key = :key LIMIT 1"),
        {"key": key},
    )
    row = result.first()
    if not row or row[0] in (None, ""):
        return default
    # decrypt_secret deliberately accepts legacy plaintext. The next write
    # always passes through _save_sensitive_setting_json and migrates it.
    plaintext = decrypt_secret(str(row[0]), purpose=purpose)
    return _load_json(plaintext, default)


async def _save_sensitive_setting_json(
    db: AsyncSession,
    key: str,
    value: Any,
    *,
    purpose: str,
) -> None:
    serialized = _dump_json(value)
    if len(serialized.encode("utf-8")) > MAX_SENSITIVE_STORE_PLAINTEXT_BYTES:
        raise SensitiveStoreCapacityError("local sensitive store capacity exceeded")
    encrypted = encrypt_secret(serialized, purpose=purpose)
    now = _now()
    await db.execute(
        text(
            """
            INSERT INTO xianyu_sys_setting (setting_key, setting_value, created_time, updated_time)
            VALUES (:key, :value, :now, :now)
            ON DUPLICATE KEY UPDATE
              setting_value = VALUES(setting_value),
              updated_time = VALUES(updated_time)
            """
        ),
        {"key": key, "value": encrypted, "now": now},
    )


@asynccontextmanager
async def _sensitive_store_write_lock(db: AsyncSession, key: str):
    """Serialize read-modify-write cycles, including first-row creation.

    MySQL named locks are connection scoped. Callers must not commit inside
    this context: releasing first keeps GET_LOCK and RELEASE_LOCK on the same
    checked-out connection, then the caller commits the protected write.
    """

    lock_name = f"xya:sensitive-store:{key}"
    acquired_lock = False
    try:
        acquired = await db.execute(
            text("SELECT GET_LOCK(:lock_name, :timeout_seconds)"),
            {"lock_name": lock_name, "timeout_seconds": 3},
        )
        if int(acquired.scalar() or 0) != 1:
            raise SensitiveStoreBusyError("local sensitive store is busy")
        acquired_lock = True
        yield
    except Exception:
        if acquired_lock:
            try:
                await db.execute(
                    text("SELECT RELEASE_LOCK(:lock_name)"),
                    {"lock_name": lock_name},
                )
            except Exception:
                logger.error("Failed to release local sensitive-store lock after rollback")
        await db.rollback()
        raise
    else:
        try:
            await db.execute(
                text("SELECT RELEASE_LOCK(:lock_name)"),
                {"lock_name": lock_name},
            )
        except Exception:
            await db.rollback()
            logger.error("Failed to release local sensitive-store lock")
            raise SensitiveStoreBusyError("local sensitive-store lock release failed")


def _channel_is_configured(channel: dict[str, Any]) -> bool:
    channel_type = str(channel.get("type") or channel.get("key") or "").strip().lower()
    if channel_type == "email":
        return all(
            str(channel.get(field) or "").strip()
            for field in ("smtpHost", "smtpUser", "smtpPass", "receiver")
        )
    if channel_type == "pushplus":
        return bool(str(channel.get("receiver") or "").strip())
    if channel_type == "feishu_app":
        return all(
            str(channel.get(field) or "").strip()
            for field in ("appId", "secret", "verificationToken", "receiveId")
        )
    webhook_url = str(channel.get("webhookUrl") or "").strip().lower()
    return webhook_url.startswith("https://")


def _notification_user_id(current_user: dict[str, Any] | None) -> int:
    if not isinstance(current_user, dict):
        return DEFAULT_TENANT_ID
    raw_value = current_user.get("user_id")
    if raw_value in (None, "", 0, "0"):
        raw_value = current_user.get("userId")
    try:
        value = int(raw_value)
    except Exception:
        value = DEFAULT_TENANT_ID
    return value or DEFAULT_TENANT_ID


def _normalize_notification_settings(payload: dict[str, Any] | None) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    return {
        "inApp": _to_bool(data.get("inApp"), True),
        "sendMode": str(data.get("sendMode") or "single"),
        "channels": data.get("channels") if isinstance(data.get("channels"), list) else [],
        "events": data.get("events") if isinstance(data.get("events"), list) else [],
    }


def _notification_secret_purpose(field: str) -> str:
    return f"notification.{field.casefold()}"


def _decrypt_notification_settings(value: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_notification_settings(value)
    channels: list[dict[str, Any]] = []
    for raw_channel in normalized["channels"]:
        if not isinstance(raw_channel, dict):
            continue
        channel = dict(raw_channel)
        for field in NOTIFICATION_SECRET_FIELDS:
            if channel.get(field) not in (None, ""):
                channel[field] = decrypt_secret(
                    str(channel[field]),
                    purpose=_notification_secret_purpose(field),
                )
        channels.append(channel)
    normalized["channels"] = channels
    return normalized


def _encrypt_notification_settings(value: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_notification_settings(value)
    channels: list[dict[str, Any]] = []
    for raw_channel in normalized["channels"]:
        if not isinstance(raw_channel, dict):
            continue
        channel = dict(raw_channel)
        for field in NOTIFICATION_SECRET_FIELDS:
            if channel.get(field) not in (None, ""):
                channel[field] = encrypt_secret(
                    str(channel[field]),
                    purpose=_notification_secret_purpose(field),
                )
        channels.append(channel)
    normalized["channels"] = channels
    return normalized


def _public_notification_settings(value: dict[str, Any]) -> dict[str, Any]:
    public = _normalize_notification_settings(value)
    channels: list[dict[str, Any]] = []
    for raw_channel in public["channels"]:
        if not isinstance(raw_channel, dict):
            continue
        channel = dict(raw_channel)
        for field in NOTIFICATION_SECRET_FIELDS:
            configured = bool(str(channel.get(field) or "").strip())
            channel[f"{field}Configured"] = configured
            if field == "webhookUrl":
                channel["webhookUrlSecure"] = (
                    str(channel.get(field) or "").strip().lower().startswith("https://")
                    if configured
                    else False
                )
            channel[field] = ""
        channels.append(channel)
    public["channels"] = channels
    return public


def _notification_channel_identity(channel: dict[str, Any], index: int) -> str:
    return str(
        channel.get("key")
        or channel.get("id")
        or channel.get("type")
        or f"index:{index}"
    ).strip()


def _merge_notification_secrets(
    existing: dict[str, Any],
    incoming: dict[str, Any] | None,
) -> dict[str, Any]:
    merged = _normalize_notification_settings(incoming)
    old_channels = {
        _notification_channel_identity(channel, index): channel
        for index, channel in enumerate(existing.get("channels") or [])
        if isinstance(channel, dict)
    }
    result_channels: list[dict[str, Any]] = []
    for index, raw_channel in enumerate(merged["channels"]):
        if not isinstance(raw_channel, dict):
            continue
        channel = dict(raw_channel)
        old = old_channels.get(_notification_channel_identity(channel, index), {})
        for field in NOTIFICATION_SECRET_FIELDS:
            clear_flag = f"clear{field[0].upper()}{field[1:]}"
            incoming_value = str(channel.get(field) or "").strip()
            if _to_bool(channel.get(clear_flag), False):
                channel[field] = ""
            elif incoming_value and incoming_value not in {"******", "********", "[REDACTED]"}:
                channel[field] = incoming_value
            else:
                channel[field] = old.get(field) or ""
            channel.pop(clear_flag, None)
            channel.pop(f"{field}Configured", None)
        result_channels.append(channel)
    merged["channels"] = result_channels
    return merged


async def _load_user_notification_settings(
    db: AsyncSession,
    current_user: dict[str, Any] | None,
    *,
    reveal_secrets: bool = False,
) -> dict[str, Any]:
    user_id = _notification_user_id(current_user)
    row = (
        await db.execute(
            text(
                """
                SELECT config_json
                FROM user_notification_setting
                WHERE user_id = :user_id AND deleted = 0
                LIMIT 1
                """
            ),
            {"user_id": user_id},
        )
    ).mappings().first()
    if row and row.get("config_json") not in (None, ""):
        loaded = _decrypt_notification_settings(
            _load_json(row.get("config_json"), DEFAULT_NOTIFICATION_SETTINGS)
        )
    else:
        legacy = await _get_setting_json(db, NOTIFICATION_SETTINGS_KEY, DEFAULT_NOTIFICATION_SETTINGS)
        loaded = _decrypt_notification_settings(legacy)
    return loaded if reveal_secrets else _public_notification_settings(loaded)


async def _save_user_notification_settings(
    db: AsyncSession,
    current_user: dict[str, Any] | None,
    payload: dict[str, Any] | None,
) -> dict[str, Any]:
    user_id = _notification_user_id(current_user)
    existing = await _load_user_notification_settings(db, current_user, reveal_secrets=True)
    merged_settings = _merge_notification_secrets(existing, payload)
    for channel in merged_settings.get("channels") or []:
        if not isinstance(channel, dict):
            continue
        webhook_url = str(channel.get("webhookUrl") or "").strip()
        if webhook_url and (
            len(webhook_url) > 2_048 or not webhook_url.lower().startswith("https://")
        ):
            raise ValueError("Webhook 必须是长度不超过 2048 的公网 HTTPS 地址")
        method = str(channel.get("method") or "POST").strip().upper()
        if method not in {"GET", "POST"}:
            raise ValueError("Webhook 请求方法仅支持 GET 或 POST")
    encrypted_settings = _encrypt_notification_settings(merged_settings)
    now = _now()
    await db.execute(
        text(
            """
            INSERT INTO user_notification_setting (user_id, config_json, created_time, updated_time, deleted)
            VALUES (:user_id, :config_json, :now, :now, 0)
            ON DUPLICATE KEY UPDATE
              config_json = VALUES(config_json),
              updated_time = VALUES(updated_time),
              deleted = 0
            """
        ),
        {"user_id": user_id, "config_json": _dump_json(encrypted_settings), "now": now},
    )
    await _save_setting_json(db, NOTIFICATION_SETTINGS_KEY, encrypted_settings)
    return _public_notification_settings(merged_settings)


async def _insert_notification_delivery_log(
    db: AsyncSession,
    user_id: int,
    result: dict[str, Any],
    retry_count: int = 0,
) -> None:
    await db.execute(
        text(
            """
            INSERT INTO notification_delivery_log (
              user_id, channel_key, channel_name, event_type, success,
              status_code, cost_ms, message, request_body, response_body,
              retry_count, created_time
            ) VALUES (
              :user_id, :channel_key, :channel_name, :event_type, :success,
              :status_code, :cost_ms, :message, :request_body, :response_body,
              :retry_count, :created_time
            )
            """
        ),
        {
            "user_id": user_id,
            "channel_key": result.get("channelKey") or "",
            "channel_name": result.get("channelName") or "",
            "event_type": result.get("eventType") or "测试通知",
            "success": 1 if _to_bool(result.get("success"), False) else 0,
            "status_code": int(result.get("statusCode") or 0),
            "cost_ms": int(result.get("costMs") or 0),
            "message": redact_sensitive_text(result.get("message") or "", max_length=500),
            "request_body": redact_sensitive_text(result.get("requestBody") or "", max_length=4000),
            "response_body": redact_sensitive_text(result.get("responseBody") or "", max_length=4000),
            "retry_count": max(0, int(retry_count or 0)),
            "created_time": _now(),
        },
    )


async def _execute_test_notification(
    channel: dict[str, Any],
    title: str,
    content: str,
    template_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    channel_key = str(channel.get("key") or channel.get("type") or "webhook")
    channel_name = str(channel.get("name") or channel_key)
    channel_type = str(channel.get("type") or channel_key or "webhook").strip().lower()
    if not _channel_is_configured(channel):
        return {
            "channelKey": channel_key,
            "channelName": channel_name,
            "eventType": "测试通知",
            "success": False,
            "statusCode": 0,
            "costMs": 0,
            "message": "当前通知渠道未完成配置，请先保存有效配置后再测试",
            "requestBody": "",
            "responseBody": "",
        }

    rendered = _render_template(
        str(channel.get("template") or ""),
        title,
        content,
        context=template_context,
    )
    timeout_seconds = max(1, min(int(channel.get("timeoutSeconds") or 10), 120))

    if channel_type == "feishu_app":
        started = time.time()
        delivery = await send_text_message_result(
            str(channel.get("receiveId") or "").strip(),
            rendered,
            str(channel.get("receiveIdType") or "open_id").strip() or "open_id",
        )
        cost_ms = int((time.time() - started) * 1000)
        return {
            "channelKey": channel_key,
            "channelName": channel_name,
            "eventType": "测试通知",
            "success": bool(delivery.get("success")),
            "statusCode": int(delivery.get("status_code") or 0),
            "costMs": cost_ms,
            "message": str(delivery.get("message") or "飞书自建应用发送失败"),
            "requestBody": "",
            "responseBody": "",
            "outcomeKnown": bool(delivery.get("outcome_known")),
        }

    if channel_type == "feishu":
        delivery = await _send_feishu(channel, rendered, timeout_seconds)
    elif channel_type == "dingtalk":
        delivery = await _send_dingtalk(channel, rendered, timeout_seconds)
    elif channel_type == "wechat_work":
        delivery = await _send_wechat_work(channel, rendered, timeout_seconds)
    elif channel_type == "pushplus":
        delivery = await _send_pushplus(channel, title, rendered, timeout_seconds)
    elif channel_type == "email":
        delivery = await _send_email(channel, title, rendered, timeout_seconds)
    else:
        delivery = await _send_webhook(channel, title, rendered, timeout_seconds)

    return {
        "channelKey": channel_key,
        "channelName": channel_name,
        "eventType": "测试通知",
        "success": bool(delivery.get("success")),
        "statusCode": int(delivery.get("status_code") or 0),
        "costMs": int(delivery.get("cost_ms") or 0),
        "message": redact_sensitive_text(delivery.get("message") or "", max_length=500),
        "requestBody": redact_sensitive_text(delivery.get("request_body") or "", max_length=4000),
        "responseBody": redact_sensitive_text(delivery.get("response_body") or "", max_length=4000),
        "outcomeKnown": bool(delivery.get("outcome_known", True)),
    }


def _feedback_default_store() -> dict[str, Any]:
    return {"nextId": 1, "nextReplyId": 1, "records": []}


def _normalize_feedback_owner(value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    if not normalized or len(normalized) > MAX_FEEDBACK_OWNER_LENGTH:
        return ""
    return normalized


def _local_feedback_owner(current_user: dict[str, Any] | None) -> str:
    owner = _normalize_feedback_owner((current_user or {}).get("username"))
    if not owner:
        raise HTTPException(
            status_code=403,
            detail="无法确认本地反馈所有者，已拒绝访问。",
        )
    return owner


def _is_feedback_owned_by(record: Any, owner: str) -> bool:
    if not isinstance(record, dict):
        return False
    # Deliberately do not infer ownership from the historical ``username``
    # display field. Records written before owner isolation are fail-closed.
    stored_owner = _normalize_feedback_owner(record.get("owner"))
    return bool(stored_owner) and stored_owner == owner


async def _load_feedback_store(db: AsyncSession) -> dict[str, Any]:
    store = await _get_sensitive_setting_json(
        db,
        FEEDBACK_STORE_KEY,
        _feedback_default_store(),
        purpose=FEEDBACK_STORE_PURPOSE,
    )
    if not isinstance(store, dict):
        return _feedback_default_store()
    store.setdefault("nextId", 1)
    store.setdefault("nextReplyId", 1)
    store.setdefault("records", [])
    if not isinstance(store["records"], list):
        store["records"] = []
    return store


def _feedback_view(record: dict[str, Any]) -> dict[str, Any]:
    replies = list(record.get("replies") or [])
    latest_admin_reply = next(
        (reply for reply in reversed(replies) if reply.get("replierRole") == "admin"),
        None,
    )
    return {
        "id": record.get("id"),
        "category": record.get("category", "other"),
        "title": record.get("title", ""),
        "content": record.get("content", ""),
        "contentPreview": record.get("contentPreview")
        or str(record.get("content", "")).strip()[:120],
        "contact": record.get("contact", ""),
        "username": record.get("username", "admin"),
        "status": record.get("status", "open"),
        "createdTime": record.get("createdTime"),
        "updatedTime": record.get("updatedTime"),
        "replierUsername": latest_admin_reply.get("replierUsername") if latest_admin_reply else "",
        "replies": replies,
        "storageMode": "local",
    }


def _position_label(position_type: str) -> str:
    mapping = {
        "home_carousel": "首页轮播广告",
        "sidebar_text": "首页右侧文字广告",
    }
    return mapping.get(str(position_type or "").strip().lower(), "广告位")


def _ad_application_default_store() -> dict[str, Any]:
    return {"nextId": 1, "records": []}


def _payment_status_label(status: str) -> str:
    mapping = {
        "unpaid": "待支付",
        "pending": "待支付",
        "paid": "已支付",
        "closed": "已关闭",
        "expired": "已过期",
        "refunded": "已退款",
        "disabled": "不可用",
        "failed": "支付失败",
        "unknown": "状态未知",
    }
    return mapping.get(str(status or "").strip().lower(), "状态未知")


def _application_status_label(status: str) -> str:
    mapping = {
        "pending_payment": "待支付",
        "pending": "待审核",
        "approved": "已通过",
        "rejected": "已拒绝",
        "online": "投放中",
        "offline": "已下线",
    }
    return mapping.get(str(status or "").strip().lower(), "待审核")


def _decorate_ad_application_view(view: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    result = dict(view)
    status = str(record.get("status") or result.get("status") or "pending").strip().lower()
    raw_payment_status = str(
        record.get("paymentStatus") or result.get("paymentStatus") or ""
    ).strip().lower()
    payment_status = (
        raw_payment_status
        if raw_payment_status
        in {
            "unpaid",
            "pending",
            "paid",
            "closed",
            "expired",
            "refunded",
            "disabled",
            "failed",
            "unknown",
        }
        else "unknown"
    )
    contact = (
        str(record.get("contact") or "").strip()
        or str(record.get("contactValue") or "").strip()
        or str(record.get("contactName") or "").strip()
        or str(record.get("contactPhone") or "").strip()
        or str(record.get("contactWechat") or "").strip()
    )
    payment_amount_cent = int(record.get("paymentAmountCent") or 0)
    raw_payment_amount_yuan = str(record.get("paymentAmountYuan") or "").strip()
    payment_amount_yuan = raw_payment_amount_yuan or (
        f"{payment_amount_cent / 100:.2f}".rstrip("0").rstrip(".") if payment_amount_cent > 0 else ""
    )
    result.update(
        {
            "status": status,
            "statusLabel": _application_status_label(status),
            "contact": contact,
            "contactValue": str(record.get("contactValue") or contact).strip(),
            "creativeImageUrl": str(record.get("creativeImageUrl") or "").strip(),
            "paymentMethod": str(record.get("paymentMethod") or "").strip(),
            "paymentOrderNo": str(record.get("paymentOrderNo") or "").strip(),
            "paymentStatus": payment_status,
            "paymentStatusLabel": _payment_status_label(payment_status),
            "paymentAmountCent": payment_amount_cent,
            "paymentAmountYuan": payment_amount_yuan,
        }
    )
    return result


async def _load_ad_application_store(db: AsyncSession) -> dict[str, Any]:
    store = await _get_sensitive_setting_json(
        db,
        AD_APPLICATION_STORE_KEY,
        _ad_application_default_store(),
        purpose=AD_APPLICATION_STORE_PURPOSE,
    )
    if not isinstance(store, dict):
        return _ad_application_default_store()
    store.setdefault("nextId", 1)
    store.setdefault("records", [])
    if not isinstance(store["records"], list):
        store["records"] = []
    return store


def _ad_application_view(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": record.get("id"),
        "applicationNo": record.get("applicationNo") or "",
        "positionType": record.get("positionType") or "sidebar_text",
        "positionLabel": record.get("positionLabel") or _position_label(record.get("positionType")),
        "planCode": record.get("planCode") or "",
        "planTitle": record.get("planTitle") or "",
        "companyName": record.get("companyName") or "",
        "contactName": record.get("contactName") or "",
        "contactPhone": record.get("contactPhone") or "",
        "contactWechat": record.get("contactWechat") or "",
        "title": record.get("title") or "",
        "landingUrl": record.get("landingUrl") or "",
        "budget": record.get("budget") or "",
        "startDate": record.get("startDate") or "",
        "durationDays": record.get("durationDays") or "",
        "remark": record.get("remark") or "",
        "status": record.get("status") or "pending",
        "statusLabel": {
            "pending": "待审核",
            "approved": "已通过",
            "rejected": "已驳回",
            "online": "投放中",
            "offline": "已下线",
        }.get(str(record.get("status") or "pending"), "待审核"),
        "statusMessage": record.get("statusMessage") or "",
        "createdTime": record.get("createdTime"),
        "updatedTime": record.get("updatedTime"),
    }


@router.get("/notification-settings")
async def get_notification_settings(
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    return ResultObject.success(await _load_user_notification_settings(db, current_user))


@router.post("/notification-settings")
async def save_notification_settings(
    payload: dict = Body(default={}),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        data = await _save_user_notification_settings(db, current_user, payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await db.commit()
    return ResultObject.success(data)


@router.get("/notifications/delivery-logs")
async def get_notification_delivery_logs(
    current: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    user_id = _notification_user_id(current_user)
    total_result = await db.execute(
        text("SELECT COUNT(*) FROM notification_delivery_log WHERE user_id = :user_id"),
        {"user_id": user_id},
    )
    total = int(total_result.scalar() or 0)
    if total > 0:
        result = await db.execute(
            text(
                """
                SELECT
                  id, channel_key, channel_name, event_type, success, status_code,
                  cost_ms, message, request_body, response_body, retry_count, created_time
                FROM notification_delivery_log
                WHERE user_id = :user_id
                ORDER BY id DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            {"user_id": user_id, "limit": size, "offset": (current - 1) * size},
        )
        rows = result.mappings().all()
        return ResultObject.success({
            "records": [
                {
                    "id": row["id"],
                    "channelKey": row["channel_key"] or "",
                    "channelName": row["channel_name"] or "",
                    "eventType": row["event_type"] or "",
                    "success": _to_bool(row["success"], False),
                    "statusCode": int(row["status_code"] or 0),
                    "costMs": int(row["cost_ms"] or 0),
                    "message": redact_sensitive_text(row["message"] or "", max_length=500),
                    "requestBody": redact_sensitive_text(row["request_body"] or "", max_length=4000),
                    "responseBody": redact_sensitive_text(row["response_body"] or "", max_length=4000),
                    "retryCount": int(row["retry_count"] or 0),
                    "createdTime": _dt_text(row["created_time"]),
                }
                for row in rows
            ],
            "total": total,
            "current": current,
            "size": size,
        })

    rows = await _get_setting_json(db, NOTIFICATION_DELIVERY_LOGS_KEY, [])
    if not isinstance(rows, list):
        rows = []
    rows = sorted(rows, key=lambda item: item.get("id", 0), reverse=True)
    start = (current - 1) * size
    end = start + size
    return ResultObject.success({
        "records": [
            {
                **item,
                "message": redact_sensitive_text(item.get("message") or "", max_length=500),
                "requestBody": redact_sensitive_text(item.get("requestBody") or "", max_length=4000),
                "responseBody": redact_sensitive_text(item.get("responseBody") or "", max_length=4000),
            }
            for item in rows[start:end]
            if isinstance(item, dict)
        ],
        "total": len(rows),
        "current": current,
        "size": size,
    })


@router.get("/notifications")
async def list_notifications(
    current: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    total_result = await db.execute(
        text("SELECT COUNT(*) AS total FROM notification WHERE deleted = 0")
    )
    total = int(total_result.scalar() or 0)
    result = await db.execute(
        text(
            """
            SELECT
              id, notification_type, title, content, reference_type, reference_id,
              is_read, read_time, priority, created_time, updated_time
            FROM notification
            WHERE deleted = 0
            ORDER BY id DESC
            LIMIT :limit OFFSET :offset
            """
        ),
        {"limit": size, "offset": (current - 1) * size},
    )
    rows = result.mappings().all()
    records = [
        {
            "id": row["id"],
            "type": row["notification_type"] or "system",
            "channel": row["notification_type"] or "system",
            "title": row["title"] or "应用内通知",
            "content": row["content"] or "",
            "referenceType": row["reference_type"] or "",
            "referenceId": row["reference_id"],
            "readFlag": _to_bool(row["is_read"], False),
            "priority": int(row["priority"] or 0),
            "createdTime": _dt_text(row["created_time"]),
            "updatedTime": _dt_text(row["updated_time"]),
            "readTime": _dt_text(row["read_time"]),
        }
        for row in rows
    ]
    return ResultObject.success({
        "records": records,
        "total": total,
        "current": current,
        "size": size,
    })


def _notification_test_validation_response(message: str) -> JSONResponse:
    result = ResultObject.validate_failed(message)
    return JSONResponse(
        status_code=400,
        content=result.model_dump(by_alias=True),
    )


@router.post("/notifications/test")
async def test_notifications(
    payload: dict = Body(default={}),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    user_id = _notification_user_id(current_user)
    channel_key = str(payload.get("channelKey") or "").strip()
    idempotency_key = str(payload.get("idempotencyKey") or "").strip()
    title = str(payload.get("title") or "通知渠道测试").strip() or "通知渠道测试"
    content = str(payload.get("content") or "来自通知中心的测试消息").strip() or "来自通知中心的测试消息"
    try:
        command = NotificationTestCommand(
            user_id=user_id,
            channel_key=channel_key,
            idempotency_key=idempotency_key,
            title=title,
            content=content,
        )
    except ValueError:
        return _notification_test_validation_response(
            "必须选择一个已保存渠道并提供 16-128 位稳定通知测试幂等键"
        )

    settings = await _load_user_notification_settings(
        db,
        current_user,
        reveal_secrets=True,
    )
    configured_channels = [
        channel
        for channel in (settings.get("channels") or [])
        if isinstance(channel, dict)
        and str(channel.get("key") or "").strip() == command.channel_key
    ]
    if len(configured_channels) != 1:
        return _notification_test_validation_response(
            "通知测试只允许选择一个唯一的已保存渠道"
        )
    channel = configured_channels[0]
    if not _channel_is_configured(channel):
        return _notification_test_validation_response(
            "当前通知渠道未完成配置，请先保存有效配置后再测试"
        )

    template_context = {
        "event": "测试通知",
        "account": current_user.get("username", "admin"),
        "channel": command.channel_key,
    }

    async def remote_send(remote_command: NotificationTestCommand) -> dict[str, Any]:
        result = await _execute_test_notification(
            channel,
            remote_command.title,
            remote_command.content,
            template_context,
        )
        if result.get("outcomeKnown") is not True:
            raise RuntimeError("notification provider result unknown")
        return {
            "success": bool(result.get("success")),
            "statusCode": int(result.get("statusCode") or 0),
            "costMs": int(result.get("costMs") or 0),
        }

    async def persist_log(
        log_db: AsyncSession,
        lease: NotificationTestLease,
    ) -> None:
        safe_result = {
            "channelKey": command.channel_key,
            "channelName": str(channel.get("type") or command.channel_key)[:120],
            "eventType": "测试通知",
            "success": bool(lease.provider_success),
            "statusCode": int(lease.provider_status_code or 0),
            "costMs": int(lease.cost_ms or 0),
            "message": (
                "通知渠道明确返回发送成功"
                if lease.provider_success
                else "通知渠道明确返回发送失败"
            ),
            "requestBody": "",
            "responseBody": "",
        }
        await _insert_notification_delivery_log(log_db, user_id, safe_result)

    try:
        outcome = await NotificationTestCoordinator(
            SqlNotificationTestAttemptStore(db),
            remote_send,
            persist_log,
        ).execute(command)
    except NotificationTestAttemptError as exc:
        result = ResultObject(
            code=exc.status_code,
            msg=str(exc),
            data={
                "attemptStatus": "conflict",
                "reason": exc.code,
                "retrySafe": False,
                "replaySafe": False,
                "logPersisted": False,
            },
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=result.model_dump(by_alias=True),
        )

    response_data = {
        **outcome.response_data(),
        "channelKey": command.channel_key,
        "simulated": False,
    }
    if outcome.status == "confirmed":
        return ResultObject.success(response_data, outcome.message)

    status_code = 409 if outcome.status in {"in_progress", "blocked"} else 502
    result = ResultObject(
        code=status_code,
        msg=outcome.message,
        data=response_data,
    )
    return JSONResponse(
        status_code=status_code,
        content=result.model_dump(by_alias=True),
    )


@router.post("/notifications/test/attempts/{attempt_id}/resolve")
async def resolve_notification_test_attempt(
    attempt_id: int,
    payload: dict = Body(default={}),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        resolved = await SqlNotificationTestAttemptStore(db).resolve_unknown(
            user_id=_notification_user_id(current_user),
            channel_key=str(payload.get("channelKey") or "").strip(),
            attempt_id=attempt_id,
            idempotency_key=str(payload.get("idempotencyKey") or "").strip(),
        )
    except NotificationTestAttemptError as exc:
        result = ResultObject(
            code=exc.status_code,
            msg=str(exc),
            data={
                "attemptStatus": "conflict",
                "reason": exc.code,
                "retrySafe": False,
                "replaySafe": False,
                "resolved": False,
            },
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=result.model_dump(by_alias=True),
        )

    return ResultObject.success(
        {
            "attemptId": resolved.attempt_id,
            "attemptStatus": "resolved",
            "channelKey": resolved.channel_key,
            "retrySafe": False,
            "replaySafe": True,
            "resolved": True,
            "repeated": resolved.repeated,
            "providerCalled": False,
        },
        "未知通知测试已人工核对并关闭；系统未发送任何新通知",
    )

@router.post("/notifications/{notification_id}/read")
async def mark_notification_read(
    notification_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    now = _now()
    await db.execute(
        text(
            """
            UPDATE notification
            SET is_read = 1, read_time = :now, updated_time = :now
            WHERE id = :id
            """
        ),
        {"id": notification_id, "now": now},
    )
    await db.commit()
    return ResultObject.success({"id": notification_id, "read": True})


@router.get("/ads/text")
async def get_text_ads(
    db: AsyncSession = Depends(get_db),
):
    try:
        payload = await proxy_get_text_ad_list(db)
        return ResultObject.success(payload)
    except CommercialBridgeCapabilityUnavailable:
        return _commercial_unavailable_response(
            "广告展示已关闭：商业桥尚未证明仅已支付广告可进入展示接口",
            configured=True,
            reason="commercial_bridge_paid_placement_required",
        )
    except CommercialBridgeNotConfigured:
        return _commercial_unavailable_response(
            "广告商业服务未配置，当前不会展示伪造文字广告",
        )
    except CommercialBridgeError:
        return _commercial_unavailable_response(
            "广告商业服务暂不可用，请稍后重试并提供请求编号",
            status_code=502,
            configured=True,
            reason="commercial_bridge_unavailable",
        )


@router.get("/ads/plans")
async def get_ad_plans(
    db: AsyncSession = Depends(get_db),
):
    try:
        payload = await proxy_get_ad_plan_list(db)
        return ResultObject.success(payload)
    except CommercialBridgeNotConfigured:
        return _commercial_unavailable_response(
            "广告商业服务未配置，当前没有可提交或支付的广告套餐",
        )
    except CommercialBridgeError:
        return _commercial_unavailable_response(
            "广告商业服务暂不可用，请稍后重试并提供请求编号",
            status_code=502,
            configured=True,
            reason="commercial_bridge_unavailable",
        )


@router.get("/ads/applications")
async def list_ad_applications(
    current: int = Query(1, ge=1),
    size: int = Query(10, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    # Ad applications are correlated by the persistent instance token sent in
    # the bridge headers, not by the logged-in user. No local login required.
    try:
        payload = await proxy_list_ad_applications(
            db,
            current=current,
            size=size,
        )
        return ResultObject.success(payload)
    except CommercialBridgeNotConfigured:
        return _commercial_unavailable_response(
            "广告商业服务未配置，无法查询申请记录",
        )
    except CommercialBridgeError:
        return _commercial_unavailable_response(
            "广告商业服务暂不可用，请稍后重试并提供请求编号",
            status_code=502,
            configured=True,
            reason="commercial_bridge_unavailable",
        )


@router.post("/ads/applications")
async def create_ad_application(
    payload: dict = Body(default={}),
    db: AsyncSession = Depends(get_db),
):
    try:
        normalized_payload = _validated_ad_application_payload(payload)
    except ValueError as exc:
        return ResultObject.validate_failed(str(exc))
    try:
        # Paid advertising is one workflow.  Reject the application before the
        # bridge POST when either the application-write or payment-write
        # idempotency contract is unavailable.
        require_paid_ad_creation_capabilities()
    except CommercialBridgeCapabilityUnavailable:
        return _commercial_unavailable_response(
            "广告申请与支付已关闭：商业桥尚未同时证明申请和支付订单幂等能力",
            configured=True,
            reason="commercial_bridge_paid_ad_capabilities_required",
        )
    except CommercialBridgeNotConfigured:
        return _commercial_unavailable_response(
            "广告商业服务未配置，申请未提交、未保存，也不会进入审核",
        )
    try:
        data = await proxy_create_ad_application(db, normalized_payload)
        return ResultObject.success(data)
    except CommercialBridgeCapabilityUnavailable:
        return _commercial_unavailable_response(
            "广告申请与支付已关闭：商业桥能力在提交前变为不可用",
            configured=True,
            reason="commercial_bridge_paid_ad_capabilities_required",
        )
    except CommercialBridgeNotConfigured:
        return _commercial_unavailable_response(
            "广告商业服务未配置，申请未提交、未保存，也不会进入审核",
        )
    except CommercialBridgeError:
        return _commercial_unknown_response(
            "广告申请结果未确认；只能复用本次申请意图继续核对或重试",
            operation="ad_application_create",
        )

@router.get("/ads/payment/methods")
async def get_ad_payment_methods(
    db: AsyncSession = Depends(get_db),
):
    try:
        # The page uses payment-method availability as its write-readiness
        # gate. Do not enable the application form when the independent
        # non-payment mutation contract is still unverified.
        require_commercial_mutation_capability()
    except CommercialBridgeCapabilityUnavailable:
        return _commercial_unavailable_response(
            "广告申请已关闭：商业桥尚未证明支持广告申请幂等键",
            configured=True,
            reason="commercial_bridge_mutation_idempotency_required",
        )
    except CommercialBridgeNotConfigured:
        return _commercial_unavailable_response("广告商业服务未配置，无法加载真实支付方式")
    try:
        data = await proxy_get_ad_payment_methods(db)
        return ResultObject.success(data)
    except CommercialBridgeCapabilityUnavailable:
        return _commercial_unavailable_response(
            "真实支付已关闭：商业桥尚未证明支持支付订单幂等键",
            configured=True,
            reason="commercial_bridge_payment_idempotency_required",
        )
    except CommercialBridgeNotConfigured:
        return _commercial_unavailable_response("广告商业服务未配置，无法加载真实支付方式")
    except CommercialBridgeError:
        return _commercial_unavailable_response(
            "广告商业服务暂不可用，请稍后重试并提供请求编号",
            status_code=502,
            configured=True,
            reason="commercial_bridge_unavailable",
        )


@router.post("/ads/applications/{application_id}/payment-order")
async def create_ad_payment_order(
    application_id: int,
    payload: dict = Body(default={}),
    db: AsyncSession = Depends(get_db),
):
    try:
        payment_method = _bounded_text(payload.get("paymentMethod"), "支付方式", 64)
        idempotency_key = _bounded_text(
            payload.get("idempotencyKey"), "支付意图幂等键", 128
        )
    except ValueError as exc:
        return ResultObject.validate_failed(str(exc))
    if application_id <= 0:
        return ResultObject.validate_failed("广告申请编号不合法")
    if not AD_PAYMENT_METHOD_PATTERN.fullmatch(payment_method):
        return ResultObject.validate_failed("支付方式不合法")
    if not AD_IDEMPOTENCY_KEY_PATTERN.fullmatch(idempotency_key):
        return ResultObject.validate_failed("支付意图幂等键缺失或不合法")
    try:
        # This guard deliberately runs before the first attempt-table query or
        # insert. Disabled/unconfigured commercial payment never writes locally.
        require_paid_ad_creation_capabilities()
        command = AdPaymentOrderCommand(
            application_id=application_id,
            payment_method=payment_method,
            idempotency_key=idempotency_key,
        )

        async def remote_create(remote_command: AdPaymentOrderCommand) -> dict[str, Any]:
            try:
                return await proxy_create_ad_payment_order(
                    db,
                    remote_command.application_id,
                    {
                        "paymentMethod": remote_command.payment_method,
                        "idempotencyKey": remote_command.idempotency_key,
                    },
                )
            except (CommercialBridgeCapabilityUnavailable, CommercialBridgeNotConfigured) as exc:
                # Both errors are raised before _request_bridge can issue the
                # POST, so only this class of failure is retry-safe.
                raise AdPaymentRemoteNotExecuted(
                    "commercial_bridge_not_executed",
                    "商业支付能力在请求发出前变为不可用，请复用原支付意图重试",
                ) from exc

        outcome = await AdPaymentOrderCoordinator(
            SqlAdPaymentOrderAttemptStore(db),
            remote_create,
        ).execute(command)
        if outcome.status == "confirmed":
            return ResultObject.success(outcome.response_data(), outcome.message)
        status_code = (
            409
            if outcome.status in {"in_progress", "failed", "closed", "expired"}
            else 502
        )
        result = ResultObject(
            code=status_code,
            msg=outcome.message,
            data={**outcome.response_data(), "status": outcome.status},
        )
        return JSONResponse(
            status_code=status_code,
            content=result.model_dump(by_alias=True),
        )
    except AdPaymentOrderAttemptError as exc:
        result = ResultObject(
            code=exc.status_code,
            msg=str(exc),
            data={
                "status": "conflict",
                "reason": exc.code,
                "retrySafe": False,
                "replaySafe": False,
            },
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=result.model_dump(by_alias=True),
        )
    except CommercialBridgeCapabilityUnavailable:
        return _commercial_unavailable_response(
            "广告支付已关闭：商业桥尚未同时证明申请和支付订单幂等能力",
            configured=True,
            reason="commercial_bridge_paid_ad_capabilities_required",
        )
    except CommercialBridgeNotConfigured:
        return _commercial_unavailable_response("广告商业服务未配置，无法创建真实支付订单")
    except CommercialBridgeError:
        return _commercial_unavailable_response(
            "支付订单创建结果未确认，请勿重复提交",
            status_code=502,
            configured=True,
            reason="commercial_bridge_result_unknown",
        )


@router.get("/ads/payment/orders/{order_no}")
async def get_ad_payment_order(
    order_no: str,
    db: AsyncSession = Depends(get_db),
):
    # Historical lookup/terminal reconciliation intentionally remains
    # available when creation idempotency is disabled; it is a read-side
    # safety control and must not be gated by the create capability flag.
    try:
        normalized_order_no = _bounded_text(order_no, "支付订单号", 128)
    except ValueError as exc:
        return ResultObject.validate_failed(str(exc))
    if not AD_ORDER_NO_PATTERN.fullmatch(normalized_order_no):
        return ResultObject.validate_failed("支付订单号不合法")
    try:
        data = await proxy_get_ad_payment_order(db, normalized_order_no)
        terminal_state = payment_order_terminal_state(data)
        if terminal_state:
            try:
                await SqlAdPaymentOrderAttemptStore(db).mark_terminal_by_order_no(
                    normalized_order_no,
                    terminal_state=terminal_state,
                )
            except Exception:
                logger.error(
                    "Failed to persist verified payment terminal state",
                )
                return _commercial_unavailable_response(
                    "已核对到支付订单终态，但本地安全状态同步失败；请勿新建支付意图并重试查询",
                    status_code=502,
                    configured=True,
                    reason="payment_terminal_state_sync_failed",
                )
        return ResultObject.success(data)
    except CommercialBridgeNotConfigured:
        return _commercial_unavailable_response("广告商业服务未配置，无法查询真实支付状态")
    except CommercialBridgeError:
        return _commercial_unavailable_response(
            "支付状态暂不可用，请稍后核对商业服务",
            status_code=502,
            configured=True,
            reason="commercial_bridge_unavailable",
        )


@router.post("/ads/payment/orders/{order_no}/close")
async def close_ad_payment_order(
    order_no: str,
    db: AsyncSession = Depends(get_db),
):
    # Closing is also a remote write. The proxy requires the verified payment
    # idempotency capability and derives one stable key from this order number.
    try:
        normalized_order_no = _bounded_text(order_no, "支付订单号", 128)
    except ValueError as exc:
        return ResultObject.validate_failed(str(exc))
    if not AD_ORDER_NO_PATTERN.fullmatch(normalized_order_no):
        return ResultObject.validate_failed("支付订单号不合法")
    try:
        data = await proxy_close_ad_payment_order(db, normalized_order_no)
        terminal_state = payment_order_terminal_state(data)
        if terminal_state is None:
            result = ResultObject(
                code=502,
                msg="商业服务已受理关闭请求，但未明确返回 closed/expired；订单仍保持锁定，请先查询核对",
                data={
                    "status": "unknown",
                    "operation": "close",
                    "orderNo": normalized_order_no,
                    "reason": "payment_close_terminal_unconfirmed",
                    "retrySafe": False,
                    "replaySafe": True,
                },
            )
            return JSONResponse(
                status_code=502,
                content=result.model_dump(by_alias=True),
            )
        try:
            await SqlAdPaymentOrderAttemptStore(db).mark_terminal_by_order_no(
                normalized_order_no,
                terminal_state=terminal_state,
            )
        except Exception:
            logger.error(
                "Commercial payment order closed but local state sync failed",
            )
            result = ResultObject(
                code=502,
                msg="商业服务已返回关闭结果，但本地安全状态同步失败；请勿新建支付意图并改用查询核对",
                data={
                    "status": "unknown",
                    "operation": "close",
                    "orderNo": normalized_order_no,
                    "reason": "payment_close_state_sync_failed",
                    "retrySafe": False,
                    "replaySafe": True,
                },
            )
            return JSONResponse(
                status_code=502,
                content=result.model_dump(by_alias=True),
            )
        return ResultObject.success(data)
    except CommercialBridgeCapabilityUnavailable:
        return _commercial_unavailable_response(
            "关闭支付订单已禁用：商业桥尚未证明支持稳定关闭幂等键；请仅查询核对订单状态",
            configured=True,
            reason="commercial_bridge_payment_idempotency_required",
        )
    except CommercialBridgeNotConfigured:
        return _commercial_unavailable_response("广告商业服务未配置，无法关闭真实支付订单")
    except CommercialBridgeError:
        result = ResultObject(
            code=502,
            msg="关闭支付订单结果未确认，请先查询核对；如需再次关闭，系统只会复用同一稳定幂等键",
            data={
                "status": "unknown",
                "operation": "close",
                "orderNo": normalized_order_no,
                "reason": "commercial_bridge_result_unknown",
                "retrySafe": False,
                "replaySafe": True,
            },
        )
        return JSONResponse(
            status_code=502,
            content=result.model_dump(by_alias=True),
        )


@router.get("/feedback")
async def list_feedback(
    current: int = Query(1, ge=1),
    size: int = Query(10, ge=1, le=200),
    status: str | None = Query(default=None),
    category: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if commercial_bridge_is_configured(get_commercial_bridge_config()):
        try:
            data = await proxy_list_feedback(
                current=current,
                size=size,
                status=status or "",
                category=category or "",
            )
            return ResultObject.success(data)
        except CommercialBridgeError as exc:
            logger.warning("feedback list bridge failed, fallback to local: %s", exc)
    owner = _local_feedback_owner(current_user)
    store = await _load_feedback_store(db)
    rows = [
        _feedback_view(record)
        for record in store["records"]
        if _is_feedback_owned_by(record, owner)
    ]
    if status:
        rows = [row for row in rows if str(row.get("status")) == status]
    if category:
        rows = [row for row in rows if str(row.get("category")) == category]
    rows = sorted(rows, key=lambda item: int(item.get("id", 0)), reverse=True)
    start = (current - 1) * size
    end = start + size
    return ResultObject.success({
        "records": rows[start:end],
        "total": len(rows),
        "current": current,
        "size": size,
        "storageMode": "local",
    })


@router.get("/feedback/stats")
async def get_feedback_stats(
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if commercial_bridge_is_configured(get_commercial_bridge_config()):
        try:
            data = await proxy_get_feedback_stats()
            return ResultObject.success(data)
        except CommercialBridgeError as exc:
            logger.warning("feedback stats bridge failed, fallback to local: %s", exc)
    owner = _local_feedback_owner(current_user)
    store = await _load_feedback_store(db)
    stats = {"open": 0, "in_progress": 0, "replied": 0, "closed": 0, "total": 0}
    for record in store["records"]:
        if not _is_feedback_owned_by(record, owner):
            continue
        stats["total"] += 1
        status = str(record.get("status") or "open")
        if status in stats:
            stats[status] += 1
    return ResultObject.success({**stats, "storageMode": "local"})


@router.get("/feedback/{feedback_id}")
async def get_feedback_detail(
    feedback_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    if commercial_bridge_is_configured(get_commercial_bridge_config()):
        try:
            data = await proxy_get_feedback_detail(feedback_id)
            return ResultObject.success(data)
        except CommercialBridgeError as exc:
            logger.warning("feedback detail bridge failed, fallback to local: %s", exc)
    owner = _local_feedback_owner(current_user)
    store = await _load_feedback_store(db)
    record = next(
        (
            item
            for item in store["records"]
            if int(item.get("id", 0)) == feedback_id
            and _is_feedback_owned_by(item, owner)
        ),
        None,
    )
    if record is None:
        return ResultObject.failed("反馈记录不存在", code=404)
    return ResultObject.success(_feedback_view(record))


@router.post("/feedback")
async def create_feedback(
    payload: dict = Body(default={}),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        normalized_payload = _validated_feedback_payload(payload)
    except ValueError as exc:
        return ResultObject.validate_failed(str(exc))
    if commercial_bridge_is_configured(get_commercial_bridge_config()):
        try:
            data = await proxy_submit_feedback(normalized_payload)
            return ResultObject.success(data)
        except CommercialBridgeError as exc:
            logger.warning("feedback submit bridge failed, fallback to local: %s", exc)
    owner = _local_feedback_owner(current_user)
    try:
        async with _sensitive_store_write_lock(db, FEEDBACK_STORE_KEY):
            store = await _load_feedback_store(db)
            existing = next(
                (
                    item
                    for item in store["records"]
                    if _is_feedback_owned_by(item, owner)
                    and str(item.get("idempotencyKey") or "")
                    == normalized_payload["idempotencyKey"]
                ),
                None,
            )
            if existing is not None:
                same_intent = all(
                    str(existing.get(field) or "") == normalized_payload[field]
                    for field in ("category", "title", "content", "contact")
                )
                if not same_intent:
                    return ResultObject.failed("反馈幂等键已用于不同内容", code=409)
                return ResultObject.success(_feedback_view(existing), "已返回原反馈提交结果")
            if len(store["records"]) >= MAX_LOCAL_FEEDBACK_RECORDS:
                return ResultObject.failed(
                    "本地反馈记录已达容量上限，请由部署管理员归档后再提交",
                    code=507,
                )
            now = _dt_text(_now())
            record = {
                "id": int(store["nextId"]),
                "category": normalized_payload["category"],
                "title": normalized_payload["title"],
                "content": normalized_payload["content"],
                "contentPreview": normalized_payload["content"][:120],
                "contact": normalized_payload["contact"],
                "idempotencyKey": normalized_payload["idempotencyKey"],
                "owner": owner,
                "username": owner,
                "status": "open",
                "createdTime": now,
                "updatedTime": now,
                "replies": [],
            }
            store["nextId"] = int(store["nextId"]) + 1
            store["records"].append(record)
            await _save_sensitive_setting_json(
                db,
                FEEDBACK_STORE_KEY,
                store,
                purpose=FEEDBACK_STORE_PURPOSE,
            )
        await db.commit()
        return ResultObject.success(_feedback_view(record))
    except SensitiveStoreBusyError:
        return ResultObject.failed("本地反馈正在被其他请求更新，请稍后重试", code=409)
    except SensitiveStoreCapacityError:
        return ResultObject.failed(
            "本地加密反馈存储已达安全容量上限，请由部署管理员归档后再提交",
            code=507,
        )


@router.post("/feedback/{feedback_id}/reply")
async def append_feedback_reply(
    feedback_id: int,
    payload: dict = Body(default={}),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    content = str(payload.get("content") or "").strip()
    if not content:
        return ResultObject.validate_failed("补充内容不能为空")
    if len(content) > MAX_FEEDBACK_CONTENT_LENGTH:
        return ResultObject.validate_failed(f"补充内容不能超过 {MAX_FEEDBACK_CONTENT_LENGTH} 个字符")
    try:
        idempotency_key = _bounded_text(
            payload.get("idempotencyKey"), "反馈补充意图幂等键", 128
        )
    except ValueError as exc:
        return ResultObject.validate_failed(str(exc))
    if not AD_IDEMPOTENCY_KEY_PATTERN.fullmatch(idempotency_key):
        return ResultObject.validate_failed("反馈补充意图幂等键缺失或不合法")
    if commercial_bridge_is_configured(get_commercial_bridge_config()):
        try:
            data = await proxy_append_feedback_reply(feedback_id, {
                "content": content,
                "idempotencyKey": idempotency_key,
            })
            return ResultObject.success(data)
        except CommercialBridgeError as exc:
            logger.warning("feedback reply bridge failed, fallback to local: %s", exc)
    owner = _local_feedback_owner(current_user)
    try:
        async with _sensitive_store_write_lock(db, FEEDBACK_STORE_KEY):
            store = await _load_feedback_store(db)
            record = next(
                (
                    item
                    for item in store["records"]
                    if int(item.get("id", 0)) == feedback_id
                    and _is_feedback_owned_by(item, owner)
                ),
                None,
            )
            if record is None:
                return ResultObject.failed("反馈记录不存在", code=404)
            existing_reply = next(
                (
                    item
                    for item in (record.get("replies") or [])
                    if str(item.get("idempotencyKey") or "") == idempotency_key
                ),
                None,
            )
            if existing_reply is not None:
                if str(existing_reply.get("content") or "") != content:
                    return ResultObject.failed("反馈补充幂等键已用于不同内容", code=409)
                return ResultObject.success(_feedback_view(record), "已返回原补充结果")
            if len(record.get("replies") or []) >= MAX_LOCAL_FEEDBACK_REPLIES:
                return ResultObject.failed(
                    "本地反馈补充记录已达容量上限，请由部署管理员归档后再继续",
                    code=507,
                )

            now = _dt_text(_now())
            reply = {
                "id": int(store["nextReplyId"]),
                "content": content,
                "idempotencyKey": idempotency_key,
                "replierRole": "user",
                "replierUsername": owner,
                "createdTime": now,
            }
            store["nextReplyId"] = int(store["nextReplyId"]) + 1
            record.setdefault("replies", []).append(reply)
            record["updatedTime"] = now
            if record.get("status") == "closed":
                record["status"] = "open"

            await _save_sensitive_setting_json(
                db,
                FEEDBACK_STORE_KEY,
                store,
                purpose=FEEDBACK_STORE_PURPOSE,
            )
        await db.commit()
        return ResultObject.success(_feedback_view(record))
    except SensitiveStoreBusyError:
        return ResultObject.failed("本地反馈正在被其他请求更新，请稍后重试", code=409)
    except SensitiveStoreCapacityError:
        return ResultObject.failed(
            "本地加密反馈存储已达安全容量上限，请由部署管理员归档后再继续",
            code=507,
        )


def _operation_audit_status(operation_type: str | None) -> tuple[str, bool]:
    normalized = str(operation_type or "").upper()
    if normalized == "HTTP_MUTATION_STARTED":
        return "执行结果待核对", True
    if normalized == "HTTP_MUTATION_RESULT_UNKNOWN":
        return "执行结果未知", True
    if normalized == "HTTP_MUTATION_REJECTED":
        return "请求已拒绝", False
    if normalized == "HTTP_MUTATION_COMPLETED":
        return "请求已结束", False
    return "已记录", False


@router.get("/operation-logs")
async def list_operation_logs(
    current: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
    operation_type: str | None = Query(default=None, alias="operationType"),
    keyword: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    where_sql = ["1 = 1"]
    params: dict[str, Any] = {"limit": size, "offset": (current - 1) * size}

    if operation_type:
        where_sql.append("operation_type = :operation_type")
        params["operation_type"] = operation_type
    if keyword:
        where_sql.append(
            "(operation_desc LIKE :keyword OR operation_type LIKE :keyword OR target_type LIKE :keyword OR CAST(target_id AS CHAR) LIKE :keyword)"
        )
        params["keyword"] = f"%{keyword}%"

    where_clause = " AND ".join(where_sql)
    count_result = await db.execute(
        text(f"SELECT COUNT(*) AS total FROM operation_log WHERE {where_clause}"),
        params,
    )
    total = int(count_result.scalar() or 0)

    result = await db.execute(
        text(
            f"""
            SELECT
              id, operator, operation_type, operation_desc, ip_address, created_time, target_type, target_id
            FROM operation_log
            WHERE {where_clause}
            ORDER BY id DESC
            LIMIT :limit OFFSET :offset
            """
        ),
        params,
    )
    rows = result.mappings().all()

    records = [
        {
            "id": row["id"],
            "operator": row["operator"] or "",
            "operationType": row["operation_type"],
            "targetType": row["target_type"] or "",
            "targetId": row["target_id"],
            "description": row["operation_desc"] or "",
            "status": _operation_audit_status(row["operation_type"])[0],
            "requiresReconciliation": _operation_audit_status(row["operation_type"])[1],
            "requestParams": None,
            "responseResult": None,
            "durationMs": None,
            "ipAddress": row["ip_address"] or "",
            "createdTime": _dt_text(row["created_time"]),
        }
        for row in rows
    ]

    return ResultObject.success({
        "records": records,
        "total": total,
        "current": current,
        "size": size,
    })


@router.get("/operation-logs/export")
async def export_operation_logs(
    operation_type: str | None = Query(default=None, alias="operationType"),
    keyword: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        where_sql = ["1 = 1"]
        params: dict[str, Any] = {}

        if operation_type:
            where_sql.append("operation_type = :operation_type")
            params["operation_type"] = operation_type
        if keyword:
            where_sql.append(
                "(operation_desc LIKE :keyword OR operation_type LIKE :keyword OR target_type LIKE :keyword OR CAST(target_id AS CHAR) LIKE :keyword)"
            )
            params["keyword"] = f"%{keyword}%"

        where_clause = " AND ".join(where_sql)
        result = await db.execute(
            text(
                f"""
                SELECT operation_type, operation_desc, target_type, target_id, created_time
                FROM operation_log
                WHERE {where_clause}
                ORDER BY id DESC
                LIMIT 2001
                """
            ),
            params,
        )
        rows = result.mappings().all()
        if len(rows) > 2000:
            return ResultObject.failed(
                "匹配的审计日志超过 2000 条，请缩小筛选范围后再导出",
                code=413,
            )

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            ["操作时间", "操作类型", "目标类型", "目标ID", "记录状态", "描述"]
        )
        for row in rows:
            writer.writerow(
                [
                    _dt_text(row["created_time"]) or "",
                    row["operation_type"] or "",
                    row["target_type"] or "",
                    row["target_id"] or "",
                    _operation_audit_status(row["operation_type"])[0],
                    row["operation_desc"] or "",
                ]
            )

        csv_text = "\ufeff" + buffer.getvalue()
        return Response(
            content=csv_text,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="operation-logs.csv"'},
        )
    except Exception as exc:
        logger.error("Failed to export operation logs", exc_info=True)
        return ResultObject.internal_error()


@router.get("/scheduled-tasks")
async def list_scheduled_tasks(
    current: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
    runtime: ScheduledTaskRuntime = Depends(get_scheduled_task_runtime),
    current_user: dict = Depends(get_current_user),
):
    del current_user
    records, total = await runtime.list(current=current, size=size)
    return ResultObject.success({
        "records": [_scheduled_task_response(record) for record in records],
        "total": total,
        "current": current,
        "size": size,
    })


@router.post("/scheduled-tasks")
async def create_scheduled_task(
    payload: ScheduledTaskInput,
    runtime: ScheduledTaskRuntime = Depends(get_scheduled_task_runtime),
    current_user: dict = Depends(get_current_user),
):
    del current_user
    try:
        record = await runtime.create(payload)
    except ScheduledTaskError as exc:
        _raise_scheduled_task_http_error(exc)
    return ResultObject.success(_scheduled_task_response(record), "定时任务已创建")


@router.put("/scheduled-tasks/{task_id}")
async def update_scheduled_task(
    task_id: int,
    payload: ScheduledTaskInput,
    runtime: ScheduledTaskRuntime = Depends(get_scheduled_task_runtime),
    current_user: dict = Depends(get_current_user),
):
    del current_user
    try:
        record = await runtime.update(task_id, payload)
    except ScheduledTaskError as exc:
        _raise_scheduled_task_http_error(exc)
    return ResultObject.success(_scheduled_task_response(record), "定时任务已更新")


@router.delete("/scheduled-tasks/{task_id}")
async def delete_scheduled_task(
    task_id: int,
    runtime: ScheduledTaskRuntime = Depends(get_scheduled_task_runtime),
    current_user: dict = Depends(get_current_user),
):
    del current_user
    try:
        await runtime.delete(task_id)
    except ScheduledTaskError as exc:
        _raise_scheduled_task_http_error(exc)
    return ResultObject.success({"success": True}, "定时任务已删除")


@router.patch("/scheduled-tasks/{task_id}/enabled")
async def set_scheduled_task_enabled(
    task_id: int,
    payload: dict,
    runtime: ScheduledTaskRuntime = Depends(get_scheduled_task_runtime),
    current_user: dict = Depends(get_current_user),
):
    """切换定时任务的启用状态。

    请求体形如 ``{"enabled": 1}`` 或 ``{"enabled": true}``，
    与商业版前端 ``setScheduledTaskEnabled`` 调用约定一致。
    """

    del current_user
    raw = payload.get("enabled") if isinstance(payload, dict) else None
    if raw is None:
        raise HTTPException(status_code=400, detail="enabled 字段必填")
    if isinstance(raw, bool):
        enabled = raw
    else:
        normalized = str(raw).strip().lower()
        if normalized in {"1", "true"}:
            enabled = True
        elif normalized in {"0", "false"}:
            enabled = False
        else:
            raise HTTPException(status_code=400, detail="enabled 仅支持 true/false 或 1/0")
    try:
        record = await runtime.set_enabled(task_id, enabled=enabled)
    except ScheduledTaskError as exc:
        _raise_scheduled_task_http_error(exc)
    return ResultObject.success({
        "id": record.id,
        "enabled": 1 if record.enabled else 0,
    }, "定时任务状态已更新")

@router.post("/scheduled-tasks/{task_id}/run")
async def run_scheduled_task(
    task_id: int,
    runtime: ScheduledTaskRuntime = Depends(get_scheduled_task_runtime),
    current_user: dict = Depends(get_current_user),
):
    del current_user
    try:
        outcome = await runtime.run_manual(task_id)
    except ScheduledTaskError as exc:
        _raise_scheduled_task_http_error(exc)

    if outcome.status != "success":
        status_code = 504 if outcome.status == "timeout" else 422 if outcome.status in {
            "unsupported", "unavailable"
        } else 502
        raise HTTPException(
            status_code=status_code,
            detail=outcome.error or "定时任务执行失败",
        )
    return ResultObject.success({
        "taskId": task_id,
        "status": outcome.status,
        "result": outcome.result,
    }, "定时任务执行成功")


def _scheduled_task_response(record: ScheduledTaskRecord) -> dict[str, Any]:
    account_id = record.config.get("accountId", record.config.get("account_id"))
    return {
        "id": record.id,
        "taskType": record.task_type,
        "taskName": record.task_name,
        "accountId": account_id,
        "cronExpression": record.cron_expression,
        "configJson": record.config,
        "enabled": 1 if record.enabled else 0,
        "available": record.task_type in SUPPORTED_TASK_TYPES,
        "lastRunTime": _dt_text(record.last_run_time),
        "nextRunTime": _dt_text(record.next_run_time),
        "lastStatus": record.last_status,
        "lastResult": record.last_result,
        "createdTime": _dt_text(record.created_time),
        "updatedTime": _dt_text(record.updated_time),
    }


def _raise_scheduled_task_http_error(exc: ScheduledTaskError) -> None:
    if isinstance(exc, TaskNotFoundError):
        status_code = 404
    elif isinstance(exc, (TaskBusyError, TaskConflictError)):
        status_code = 409
    elif isinstance(exc, TaskValidationError):
        status_code = 422
    else:
        status_code = 500
    raise HTTPException(status_code=status_code, detail=str(exc)) from exc


# ---- 会话状态兼容路由（前端 messages.js 调用） ----
# PATCH /api/conversations/{id}/read     标记会话已读，清零 unread_count
# PATCH /api/conversations/{id}/status   变更会话状态（action: completed/transferred/active）
# 数据库 xianyu_conversation.status：0 进行中 / 1 已完成 / 2 已关闭（含转接）
_CONVERSATION_STATUS_MAP = {
    "active": 0,
    "reopened": 0,
    "completed": 1,
    "transferred": 2,
    "closed": 2,
}


async def _conversation_record_exists(db: AsyncSession, conversation_id: int) -> bool:
    result = await db.execute(
        text("SELECT id FROM xianyu_conversation WHERE id = :id LIMIT 1"),
        {"id": conversation_id},
    )
    return result.scalar_one_or_none() is not None


@router.patch("/conversations/{conversation_id}/read")
async def mark_conversation_read(
    conversation_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """标记会话为已读：清零 unread_count。"""
    result = await db.execute(
        text(
            """
            UPDATE xianyu_conversation
            SET unread_count = 0, updated_time = NOW()
            WHERE id = :id
            """
        ),
        {"id": conversation_id},
    )
    affected = getattr(result, "rowcount", 0) or 0
    if affected == 0 and not await _conversation_record_exists(db, conversation_id):
        raise HTTPException(status_code=404, detail="会话记录不存在。")
    await db.commit()
    return ResultObject.success({"id": conversation_id, "read": True})


@router.patch("/conversations/{conversation_id}/status")
async def update_conversation_status(
    conversation_id: int,
    payload: dict[str, Any] = Body(default={}),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """变更会话状态。

    前端 payload: {"action": "completed" | "transferred" | "active"}
    返回 data.status 为数据库 status 值（0/1/2），前端用 updated.status 更新本地 statusCode。
    """
    action = str(payload.get("action") or "").strip().lower()
    new_status = _CONVERSATION_STATUS_MAP.get(action)
    if new_status is None:
        return ResultObject.validate_failed(f"不支持的会话状态 action: {action or '(空)'}")

    result = await db.execute(
        text(
            """
            UPDATE xianyu_conversation
            SET status = :status, unread_count = 0, updated_time = NOW()
            WHERE id = :id
            """
        ),
        {"status": new_status, "id": conversation_id},
    )
    affected = getattr(result, "rowcount", 0) or 0
    if affected == 0 and not await _conversation_record_exists(db, conversation_id):
        raise HTTPException(status_code=404, detail="会话记录不存在。")
    await db.commit()
    return ResultObject.success({
        "id": conversation_id,
        "status": new_status,
        "action": action,
    })


# ===== Frontend Path Compatibility Layer =====
# Add compatibility routes for frontend API calls
# Frontend expects paths like /xianyu/accounts, /auto-reply/rules, etc.
# Backend actual paths are under /account/, /autoReplyRule/, /quickReplyTemplate/, etc.

# -----------------------------------------------------------------------------
# 0. Debug Login
# -----------------------------------------------------------------------------

@router.post("/debug-login")
async def debug_login(
    payload: dict = Body(default={}),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Minimal development-only credential diagnostic without secret echoes."""
    del current_user
    if settings.is_production_like:
        raise HTTPException(status_code=404, detail="接口不存在")
    from ....core.security import verify_password
    from .auth import load_admin_password_hash

    username = payload.get("username") or ""
    password = payload.get("password") or ""
    stored_hash = await load_admin_password_hash(db)
    password_valid = await asyncio.to_thread(
        verify_password,
        password,
        stored_hash,
    )
    return ResultObject.success({
        "configured": bool(stored_hash),
        "usernameMatch": username == settings.admin_username,
        "passwordValid": password_valid,
    })

# -----------------------------------------------------------------------------
# 1. Account Management (/xianyu/accounts -> /account)
# -----------------------------------------------------------------------------


def _compat_account_auth_state(
    *,
    auth_id: int | None,
    cookie_status: int | None,
    login_status_code: str | None,
    login_status_message: str | None,
) -> dict[str, Any]:
    """Keep missing, unchecked, invalid and confirmed auth states distinct."""
    auth_configured = auth_id is not None
    normalized_cookie_status = int(cookie_status or 0)
    raw_code = str(login_status_code or "").strip().upper()

    if not auth_configured:
        normalized_status_code = "AUTH_MISSING"
        normalized_status_message = "未配置账号授权信息，请先完成登录。"
    elif not raw_code:
        normalized_status_code = "UNCHECKED"
        normalized_status_message = "登录状态尚未校验，请执行状态检查。"
    else:
        normalized_status_code = raw_code
        normalized_status_message = str(login_status_message or "").strip()
        if not normalized_status_message:
            normalized_status_message = (
                "登录状态正常"
                if normalized_status_code == "OK"
                else "登录状态异常，请重新校验或登录。"
            )

    return {
        "cookieStatus": normalized_cookie_status,
        "loginStatusCode": normalized_status_code,
        "loginStatusMessage": normalized_status_message,
        "authConfigured": auth_configured,
        "authStatusKnown": auth_configured and normalized_status_code != "UNCHECKED",
        "authUsable": (
            auth_configured
            and normalized_cookie_status == 1
            and normalized_status_code == "OK"
        ),
    }


@router.get("/xianyu/accounts")
async def compat_xianyu_accounts_list(
    current: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Compat: GET /xianyu/accounts -> forward to /account/list (POST)"""
    from ....models.entities import XianyuAccount, XianyuAccountAuth, XianyuAccountRuntime
    from sqlalchemy import func

    latest_auth = (
        select(
            XianyuAccountAuth.account_id.label("account_id"),
            func.max(XianyuAccountAuth.id).label("latest_id"),
        )
        .where(XianyuAccountAuth.deleted == 0)
        .group_by(XianyuAccountAuth.account_id)
        .subquery()
    )
    latest_runtime = (
        select(
            XianyuAccountRuntime.account_id.label("account_id"),
            func.max(XianyuAccountRuntime.id).label("latest_id"),
        )
        .where(XianyuAccountRuntime.deleted == 0)
        .group_by(XianyuAccountRuntime.account_id)
        .subquery()
    )
    result = await db.execute(
        select(
            XianyuAccount,
            XianyuAccountAuth.id,
            XianyuAccountAuth.cookie_status,
            XianyuAccountAuth.last_login_status_code,
            XianyuAccountAuth.last_login_status_message,
            XianyuAccountAuth.last_login_check_time,
            XianyuAccountRuntime.last_sync_time,
        )
        .select_from(XianyuAccount)
        .outerjoin(latest_auth, latest_auth.c.account_id == XianyuAccount.id)
        .outerjoin(XianyuAccountAuth, XianyuAccountAuth.id == latest_auth.c.latest_id)
        .outerjoin(latest_runtime, latest_runtime.c.account_id == XianyuAccount.id)
        .outerjoin(XianyuAccountRuntime, XianyuAccountRuntime.id == latest_runtime.c.latest_id)
        .where(XianyuAccount.deleted == 0)
        .order_by(XianyuAccount.id.desc())
    )
    accounts = result.all()
    records = []
    for a, auth_id, cookie_status, login_status_code, login_status_message, login_check_time, last_sync_time in accounts:
        auth_state = _compat_account_auth_state(
            auth_id=auth_id,
            cookie_status=cookie_status,
            login_status_code=login_status_code,
            login_status_message=login_status_message,
        )
        records.append({
            "id": a.id,
            "externalUid": a.external_uid,
            "nickname": a.nickname,
            "avatarUrl": a.avatar_url,
            "remark": a.remark,
            "province": a.province,
            "city": a.city,
            "ipLocation": f"{a.province or ''} {a.city or ''}".strip(),
            "accountLevel": a.account_level,
            "status": a.status,
            "createdTime": _dt_text(a.created_time),
            "unb": a.external_uid,
            "accountNote": a.remark,
            "displayName": a.nickname,
            "avatar": a.avatar_url,
            # 闲鱼主页资料字段（与 restful_refresh_account_profile 提取逻辑对应）
            "introduction": getattr(a, "introduction", None),
            "followers": getattr(a, "followers", None),
            "following": getattr(a, "following", None),
            "soldCount": getattr(a, "sold_count", None),
            "reviewNum": getattr(a, "review_num", None),
            "sellerLevel": getattr(a, "seller_level", None),
            "praiseRatio": getattr(a, "praise_ratio", None),
            "fishShopScore": float(a.fish_shop_score) if getattr(a, "fish_shop_score", None) is not None else None,
            "fishShopUser": getattr(a, "fish_shop_user", None),
            **auth_state,
            "loginCheckTime": _dt_text(login_check_time),
            "lastSyncTime": _dt_text(last_sync_time),
        })
    total = len(records)
    start = (current - 1) * size
    end = start + size
    return ResultObject.success({
        "records": records[start:end],
        "total": total,
        "current": current,
        "size": size,
    })


@router.post("/xianyu/accounts")
async def compat_xianyu_accounts_create(
    payload: dict = Body(default={}),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Compat: POST /xianyu/accounts -> forward to /account/add"""
    from ....core.response import ResultObject
    from ....core.cookie_crypto import encrypt_cookie_for_storage
    from ....models.entities import XianyuAccount, XianyuAccountAuth, XianyuAccountRuntime
    cookie = payload.get("cookie") or ""
    if not cookie:
        return ResultObject.validate_failed("Cookie不能为空")

    def extract_unb_from_cookie(c: str) -> str:
        for part in c.split("; "):
            if part.startswith("unb="):
                return part[4:]
        return None

    def extract_m_h5_tk_from_cookie(c: str) -> str:
        import re
        match = re.search(r'_m_h5_tk=([^;]+)', c)
        return match.group(1) if match else ""

    unb = extract_unb_from_cookie(cookie)
    if not unb:
        return ResultObject.validate_failed("无法从Cookie中提取UNB信息")

    existing = await db.execute(
        select(XianyuAccount).where(XianyuAccount.external_uid == unb)
    )
    if existing.scalar_one_or_none():
        return ResultObject.failed("账号已存在")

    account = XianyuAccount(
        external_uid=unb,
        remark=payload.get("accountNote") or payload.get("remark") or "",
        status=1
    )
    db.add(account)
    await db.flush()
    await db.refresh(account)

    m_h5_tk = extract_m_h5_tk_from_cookie(cookie)
    auth = XianyuAccountAuth(
        account_id=account.id,
        encrypted_cookie=encrypt_cookie_for_storage(cookie),
        encrypted_token=encrypt_cookie_for_storage(m_h5_tk) if m_h5_tk else None,
        cookie_status=0,
        last_login_status_code="COOKIE_UPDATED",
        last_login_status_message="Cookie 已更新，等待统一登录校验",
    )
    db.add(auth)
    db.add(XianyuAccountRuntime(
        account_id=account.id,
        cookie_status=0,
        last_login_status_code="COOKIE_UPDATED",
        last_login_status_message="Cookie 已更新，等待统一登录校验",
    ))
    await db.commit()
    return ResultObject.success({"account_id": account.id, "message": "添加成功"})


@router.post("/xianyu/accounts/manual-cookie")
async def compat_xianyu_accounts_manual_cookie(
    payload: dict = Body(default={}),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Compat: POST /xianyu/accounts/manual-cookie -> forward to /account/manualAdd"""
    return await compat_xianyu_accounts_create(payload, db, current_user)


@router.get("/xianyu/accounts/face-verifications")
async def compat_xianyu_accounts_face_verifications(
    current_user: dict = Depends(get_current_user),
):
    """Retired: no verification event source or persisted read state exists."""

    feature_unavailable(FACE_VERIFICATION_UNAVAILABLE)


@router.post("/xianyu/accounts/face-verifications/{id}/read")
async def compat_xianyu_accounts_mark_face_verification_read(
    id: int,
    current_user: dict = Depends(get_current_user),
):
    """Retired with the synthetic verification reminder list."""

    feature_unavailable(FACE_VERIFICATION_UNAVAILABLE)


@router.get("/xianyu/accounts/summary")
async def compat_xianyu_accounts_summary(
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Summarize observed account, authentication and WS runtime states."""
    del current_user
    from sqlalchemy import func
    from ....models.entities import XianyuAccount, XianyuAccountAuth, XianyuAccountRuntime

    latest_auth = (
        select(
            XianyuAccountAuth.account_id.label("account_id"),
            func.max(XianyuAccountAuth.id).label("latest_id"),
        )
        .where(XianyuAccountAuth.deleted == 0)
        .group_by(XianyuAccountAuth.account_id)
        .subquery()
    )
    latest_runtime = (
        select(
            XianyuAccountRuntime.account_id.label("account_id"),
            func.max(XianyuAccountRuntime.id).label("latest_id"),
        )
        .where(XianyuAccountRuntime.deleted == 0)
        .group_by(XianyuAccountRuntime.account_id)
        .subquery()
    )
    try:
        result = await db.execute(
            select(
                XianyuAccount,
                XianyuAccountAuth.cookie_status,
                XianyuAccountAuth.last_login_status_code,
                XianyuAccountRuntime.cookie_status,
                XianyuAccountRuntime.last_login_status_code,
                XianyuAccountRuntime.ws_status,
            )
            .select_from(XianyuAccount)
            .outerjoin(latest_auth, latest_auth.c.account_id == XianyuAccount.id)
            .outerjoin(XianyuAccountAuth, XianyuAccountAuth.id == latest_auth.c.latest_id)
            .outerjoin(latest_runtime, latest_runtime.c.account_id == XianyuAccount.id)
            .outerjoin(XianyuAccountRuntime, XianyuAccountRuntime.id == latest_runtime.c.latest_id)
            .where(XianyuAccount.deleted == 0)
        )
        rows = result.all()
    except Exception as exc:
        logger.error(
            "Account summary query failed errorType=%s",
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=503,
            detail="账号状态汇总暂不可用，请稍后重试。",
        ) from exc

    normal = 0
    verify = 0
    cookie_warn = 0
    ws_online = 0
    for account, auth_cookie, auth_code, runtime_cookie, runtime_code, runtime_ws in rows:
        account_status = int(account.status or 0)
        if account_status == 1:
            normal += 1
        if account_status in {-1, -2}:
            verify += 1

        effective_cookie = auth_cookie if auth_cookie is not None else runtime_cookie
        cookie_status = int(effective_cookie or 0)
        login_code = str(
            auth_code
            or runtime_code
            or ("OK" if cookie_status == 1 else "COOKIE_INVALID")
        ).upper()
        if cookie_status != 1 or login_code != "OK":
            cookie_warn += 1
        if int(runtime_ws or 0) == 1:
            ws_online += 1

    total = len(rows)
    return ResultObject.success({
        "total": total,
        # Kept for old clients; active now means an account whose own status
        # is normal, not every row in the table.
        "active": normal,
        "normal": normal,
        "verify": verify,
        "cookieWarn": cookie_warn,
        "wsOnline": ws_online,
    })


@router.get("/xianyu/accounts/{account_id}")
async def compat_xianyu_accounts_detail(
    account_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Compat: GET /xianyu/accounts/{id} -> forward to /account/detail (POST)"""
    from ....models.entities import XianyuAccount, XianyuAccountAuth
    result = await db.execute(
        select(XianyuAccount).where(XianyuAccount.id == account_id)
    )
    account = result.scalar_one_or_none()
    if not account:
        return ResultObject.failed("账号不存在", code=404)

    auth_result = await db.execute(
        select(XianyuAccountAuth).where(
            XianyuAccountAuth.account_id == account_id,
            XianyuAccountAuth.deleted == 0
        ).order_by(XianyuAccountAuth.id.desc()).limit(1)
    )
    auth = auth_result.scalar_one_or_none()
    auth_state = _compat_account_auth_state(
        auth_id=(getattr(auth, "id", account_id) if auth is not None else None),
        cookie_status=(auth.cookie_status if auth else None),
        login_status_code=(auth.last_login_status_code if auth else None),
        login_status_message=(auth.last_login_status_message if auth else None),
    )

    data = {
        "id": account.id,
        "externalUid": account.external_uid,
        "nickname": account.nickname,
        "avatarUrl": account.avatar_url,
        "remark": account.remark,
        "province": account.province,
        "city": account.city,
        "ipLocation": f"{account.province or ''} {account.city or ''}".strip(),
        "accountLevel": account.account_level,
        "status": account.status,
        "createdTime": _dt_text(account.created_time),
        "unb": account.external_uid,
        "accountNote": account.remark,
        "displayName": account.nickname,
        "avatar": account.avatar_url,
        # 闲鱼主页资料字段（与 restful_refresh_account_profile 提取逻辑对应）
        "introduction": getattr(account, "introduction", None),
        "followers": getattr(account, "followers", None),
        "following": getattr(account, "following", None),
        "soldCount": getattr(account, "sold_count", None),
        "reviewNum": getattr(account, "review_num", None),
        "sellerLevel": getattr(account, "seller_level", None),
        "praiseRatio": getattr(account, "praise_ratio", None),
        "fishShopScore": float(account.fish_shop_score) if getattr(account, "fish_shop_score", None) is not None else None,
        "fishShopUser": getattr(account, "fish_shop_user", None),
        **auth_state,
        "loginCheckTime": _dt_text(auth.last_login_check_time if auth else None),
    }
    return ResultObject.success({"account": data})


@router.put("/xianyu/accounts/{account_id}")
async def compat_xianyu_accounts_update(
    account_id: int,
    payload: dict = Body(default={}),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Compat: PUT /xianyu/accounts/{id} -> forward to /account/update (POST)"""
    from ....models.entities import XianyuAccount
    result = await db.execute(select(XianyuAccount).where(XianyuAccount.id == account_id))
    account = result.scalar_one_or_none()
    if not account:
        return ResultObject.failed("账号不存在", code=404)

    account_note = payload.get("accountNote") or payload.get("remark")
    if account_note is not None:
        account.remark = account_note.strip()

    await db.commit()
    return ResultObject.success({"message": "更新成功"})


@router.delete("/xianyu/accounts/{account_id}")
async def compat_xianyu_accounts_delete(
    account_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Compat: DELETE /xianyu/accounts/{id} -> forward to /account/delete (POST)"""
    from ....models.entities import XianyuAccount, XianyuAccountAuth
    result = await db.execute(select(XianyuAccount).where(XianyuAccount.id == account_id))
    account = result.scalar_one_or_none()
    if not account:
        return ResultObject.failed("账号不存在", code=404)

    await db.execute(
        XianyuAccountAuth.__table__.delete().where(
            XianyuAccountAuth.account_id == account_id
        )
    )
    await db.delete(account)
    await db.commit()
    return ResultObject.success({"message": "删除成功"})


@router.post("/xianyu/accounts/{account_id}/refresh-profile")
async def compat_xianyu_accounts_refresh_profile(
    account_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Compat: POST /xianyu/accounts/{id}/refresh-profile -> forward to /account/refreshProfile (POST)"""
    from ....models.entities import XianyuAccount
    result = await db.execute(select(XianyuAccount).where(XianyuAccount.id == account_id))
    account = result.scalar_one_or_none()
    if not account:
        return ResultObject.failed("账号不存在", code=404)
    return ResultObject.success({
        "account": {
            "id": account.id,
            "externalUid": account.external_uid,
            "nickname": account.nickname,
            "avatarUrl": account.avatar_url,
            "remark": account.remark,
            "province": account.province,
            "city": account.city,
            "status": account.status,
        },
        "message": "刷新成功"
    })


@router.post("/xianyu/accounts/{account_id}/check-auth")
async def compat_xianyu_accounts_check_auth(
    account_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Perform a live platform login check for one account."""
    del db, current_user
    from ....services.cookie_token_refresher import check_cookie_login
    from ....services.ws_client import ws_manager

    check = await check_cookie_login(account_id)
    if not check.confirmed:
        if check.code == "ACCOUNT_NOT_FOUND":
            raise HTTPException(status_code=404, detail=check.message)
        if check.code == "CREDENTIAL_MISSING":
            raise HTTPException(status_code=422, detail=check.message)
        raise HTTPException(status_code=503, detail=check.message)

    client = ws_manager.get_client(account_id)
    return ResultObject.success({
        "ok": True,
        "confirmed": True,
        "authenticated": check.authenticated,
        "usable": check.authenticated,
        "cookieStatus": 1 if check.authenticated else 0,
        "loginStatusCode": check.code,
        "loginStatusMessage": check.message,
        "checkedAt": _dt_text(check.checked_at),
        "wsOnline": bool(client and getattr(client, "is_connected", False)),
        "message": check.message,
    })


async def _load_account_config(db: AsyncSession, account_id: int, config_type: str) -> dict:
    """从 xianyu_sys_setting 读取账号级 JSON 配置"""
    from ....models.entities import XianyuSysSetting
    key = f"account.{account_id}.{config_type}"
    result = await db.execute(
        select(XianyuSysSetting).where(XianyuSysSetting.setting_key == key)
    )
    setting = result.scalar_one_or_none()
    if setting and setting.setting_value:
        try:
            return json.loads(setting.setting_value)
        except (json.JSONDecodeError, TypeError):
            pass
    return {}


async def _save_account_config(db: AsyncSession, account_id: int, config_type: str, value: dict) -> None:
    """保存账号级 JSON 配置到 xianyu_sys_setting"""
    from ....models.entities import XianyuSysSetting
    key = f"account.{account_id}.{config_type}"
    json_str = json.dumps(value, ensure_ascii=False)
    result = await db.execute(
        select(XianyuSysSetting).where(XianyuSysSetting.setting_key == key)
    )
    setting = result.scalar_one_or_none()
    if setting:
        setting.setting_value = json_str
    else:
        db.add(XianyuSysSetting(setting_key=key, setting_value=json_str))
    await db.commit()


@router.get("/xianyu/accounts/{account_id}/auto-rate")
async def compat_xianyu_accounts_auto_rate_config(
    account_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """读取账号自动评价配置"""
    config = await _load_account_config(db, account_id, "auto_rate_config")
    return ResultObject.success({
        "enabled": config.get("enabled", False),
        "rateType": config.get("rateType", "text"),
        "textContent": config.get("textContent", ""),
        "apiUrl": config.get("apiUrl", ""),
    })


@router.put("/xianyu/accounts/{account_id}/auto-rate")
async def compat_xianyu_accounts_save_auto_rate_config(
    account_id: int,
    payload: dict = Body(...),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """保存账号自动评价配置"""
    enabled = bool(payload.get("enabled", False))
    rate_type = payload.get("rateType", "text")
    if rate_type not in ("text", "api"):
        rate_type = "text"
    text_content = str(payload.get("textContent", "") or "")
    api_url = str(payload.get("apiUrl", "") or "")
    await _save_account_config(db, account_id, "auto_rate_config", {
        "enabled": enabled,
        "rateType": rate_type,
        "textContent": text_content,
        "apiUrl": api_url,
    })
    return ResultObject.success({
        "enabled": enabled,
        "rateType": rate_type,
        "textContent": text_content,
        "apiUrl": api_url,
    })


@router.get("/xianyu/accounts/{account_id}/strategy-config")
async def compat_xianyu_accounts_strategy_config(
    account_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """读取账号消息等待策略配置"""
    config = await _load_account_config(db, account_id, "strategy_config")
    message_expire_time = config.get("messageExpireTime", 3600)
    try:
        message_expire_time = int(message_expire_time)
    except (TypeError, ValueError):
        message_expire_time = 3600
    return ResultObject.success({
        "messageExpireTime": message_expire_time,
        "scheduledRedelivery": config.get("scheduledRedelivery", False),
        "autoPolish": config.get("autoPolish", False),
        "requestRedFlower": config.get("requestRedFlower", False),
    })


@router.put("/xianyu/accounts/{account_id}/strategy-config")
async def compat_xianyu_accounts_save_strategy_config(
    account_id: int,
    payload: dict = Body(...),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """保存账号消息等待策略配置"""
    message_expire_time = payload.get("messageExpireTime", 3600)
    try:
        message_expire_time = int(message_expire_time)
    except (TypeError, ValueError):
        message_expire_time = 3600
    if message_expire_time < 0 or message_expire_time > 86400:
        raise HTTPException(status_code=422, detail="消息等待时间需在 0 到 86400 秒之间")
    scheduled_redelivery = bool(payload.get("scheduledRedelivery", False))
    auto_polish = bool(payload.get("autoPolish", False))
    request_red_flower = bool(payload.get("requestRedFlower", False))
    await _save_account_config(db, account_id, "strategy_config", {
        "messageExpireTime": message_expire_time,
        "scheduledRedelivery": scheduled_redelivery,
        "autoPolish": auto_polish,
        "requestRedFlower": request_red_flower,
    })
    return ResultObject.success({
        "messageExpireTime": message_expire_time,
        "scheduledRedelivery": scheduled_redelivery,
        "autoPolish": auto_polish,
        "requestRedFlower": request_red_flower,
    })


@router.get("/xianyu/accounts/{account_id}/login-credential")
async def compat_xianyu_accounts_login_credential(
    account_id: int,
    current_user: dict = Depends(get_current_user),
):
    """Retired: password credentials had no login/renewal consumer."""

    feature_unavailable(ACCOUNT_LOGIN_CREDENTIAL_UNAVAILABLE)


@router.put("/xianyu/accounts/{account_id}/login-credential")
async def compat_xianyu_accounts_save_login_credential(
    account_id: int,
    current_user: dict = Depends(get_current_user),
):
    """Retired: reject storage of unused high-risk credentials."""

    feature_unavailable(ACCOUNT_LOGIN_CREDENTIAL_UNAVAILABLE)


@router.post("/xianyu/accounts/{account_id}/cookie")
async def compat_xianyu_accounts_update_cookie(
    account_id: int,
    payload: dict = Body(default={}),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Compat: POST /xianyu/accounts/{id}/cookie -> forward to /account/updateCookie (POST)"""
    from ....core.cookie_crypto import encrypt_cookie_for_storage
    cookie = payload.get("cookie") or ""
    if not cookie:
        return ResultObject.validate_failed("accountId 和 cookie 不能为空")

    def extract_m_h5_tk_from_cookie(c: str) -> str:
        import re
        match = re.search(r'_m_h5_tk=([^;]+)', c)
        return match.group(1) if match else ""

    m_h5_tk = extract_m_h5_tk_from_cookie(cookie)

    await db.execute(text(
        """
        UPDATE xianyu_account_auth
        SET encrypted_cookie = :cookie, encrypted_token = :token,
            cookie_status = 0, last_login_status_code = :code,
            last_login_status_message = :message,
            last_login_check_time = NOW(), updated_time = NOW()
        WHERE account_id = :aid
        """),
        {
            "cookie": encrypt_cookie_for_storage(cookie),
            "token": encrypt_cookie_for_storage(m_h5_tk) if m_h5_tk else None,
            "code": "COOKIE_UPDATED",
            "message": "Cookie 已更新，等待统一登录校验",
            "aid": account_id,
        }
    )
    await db.execute(text(
        """
        UPDATE xianyu_account_runtime
        SET cookie_status = 0, last_login_status_code = :code,
            last_login_status_message = :message,
            last_login_check_time = NOW(), updated_time = NOW()
        WHERE account_id = :aid
        """),
        {
            "code": "COOKIE_UPDATED",
            "message": "Cookie 已更新，等待统一登录校验",
            "aid": account_id,
        }
    )
    await db.commit()
    return ResultObject.success({
        "message": "Cookie 更新成功",
        "accountId": account_id,
        "hasToken": bool(m_h5_tk),
    })


# -----------------------------------------------------------------------------
# 2. Auto Reply (/auto-reply/rules -> /autoReplyRule)
# -----------------------------------------------------------------------------

@router.get("/auto-reply/rules")
async def compat_auto_reply_rules_list(
    current: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Compat: GET /auto-reply/rules -> forward to /autoReplyRule/list (POST)"""
    from ....models.entities import AutoReplyRule
    from sqlalchemy import select
    result = await db.execute(select(AutoReplyRule).order_by(AutoReplyRule.id.desc()))
    rules = result.scalars().all()
    records = []
    for r in rules:
        records.append({
            "id": r.id,
            "accountId": r.account_id,
            "xianyuAccountId": r.account_id,
            "ruleName": r.rule_name or "",
            "matchType": r.match_type or "keyword",
            "matchKeywords": r.match_keywords or "",
            "replyContent": r.reply_content or "",
            "replyMode": r.reply_mode or r.match_type or "keyword",
            "status": r.status if r.status is not None else 1,
            "priority": r.priority or 0,
            "createdTime": _dt_text(r.created_time),
            "updatedTime": _dt_text(r.updated_time),
        })
    total = len(records)
    start = (current - 1) * size
    end = start + size
    return ResultObject.success({
        "records": records[start:end],
        "total": total,
        "current": current,
        "size": size,
    })


@router.post("/auto-reply/rules")
async def compat_auto_reply_rules_create(
    payload: dict = Body(default={}),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Compat: POST /auto-reply/rules -> forward to /autoReplyRule/save (POST)"""
    from ....models.entities import AutoReplyRule
    rule = AutoReplyRule(
        account_id=payload.get("xianyuAccountId"),
        rule_name=payload.get("ruleName") or "",
        match_type=payload.get("matchType") or "keyword",
        match_keywords=payload.get("matchKeywords") or "",
        reply_content=payload.get("replyContent") or "",
        status=1,
        priority=payload.get("priority") or 0,
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return ResultObject.success({
        "id": rule.id,
        "message": "创建成功"
    })


@router.put("/auto-reply/rules/{rule_id}")
async def compat_auto_reply_rules_update(
    rule_id: int,
    payload: dict = Body(default={}),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Compat: PUT /auto-reply/rules/{id} -> forward to /autoReplyRule/save (POST)"""
    from ....models.entities import AutoReplyRule
    result = await db.execute(select(AutoReplyRule).where(AutoReplyRule.id == rule_id))
    rule = result.scalar_one_or_none()
    if not rule:
        return ResultObject.failed("规则不存在", code=404)

    rule.rule_name = payload.get("ruleName") or rule.rule_name
    rule.match_type = payload.get("matchType") or rule.match_type
    rule.match_keywords = payload.get("matchKeywords") or rule.match_keywords
    rule.reply_content = payload.get("replyContent") or rule.reply_content
    rule.priority = payload.get("priority") or rule.priority
    rule.status = payload.get("status") if payload.get("status") is not None else rule.status
    rule.updated_time = _now()

    await db.commit()
    return ResultObject.success({
        "id": rule.id,
        "message": "更新成功"
    })


@router.delete("/auto-reply/rules/{rule_id}")
async def compat_auto_reply_rules_delete(
    rule_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Compat: DELETE /auto-reply/rules/{id}"""
    from ....models.entities import AutoReplyRule
    result = await db.execute(select(AutoReplyRule).where(AutoReplyRule.id == rule_id))
    rule = result.scalar_one_or_none()
    if not rule:
        return ResultObject.failed("规则不存在", code=404)
    await db.delete(rule)
    await db.commit()
    return ResultObject.success({"message": "删除成功"})


@router.post("/auto-reply/rules/preview")
async def compat_auto_reply_rules_preview(
    payload: dict = Body(default={}),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Compat: POST /auto-reply/rules/preview"""
    return ResultObject.success({
        "preview": payload.get("replyContent") or ""
    })


@router.get("/auto-reply/rules/logs")
async def compat_auto_reply_rules_logs(
    current: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
    accountId: int | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Compat: GET /auto-reply/rules/logs（同步商业版：返回真实日志数据）。"""
    from ....models.entities import AutoReplyLog
    from sqlalchemy import select, func as sa_func
    # 统计总数
    count_query = select(sa_func.count()).select_from(AutoReplyLog).where(
        AutoReplyLog.deleted == 0
    )
    if accountId is not None:
        count_query = count_query.where(AutoReplyLog.account_id == accountId)
    total = (await db.execute(count_query)).scalar() or 0
    # 分页查询
    offset = (current - 1) * size
    list_query = select(AutoReplyLog).where(
        AutoReplyLog.deleted == 0
    )
    if accountId is not None:
        list_query = list_query.where(AutoReplyLog.account_id == accountId)
    list_query = list_query.order_by(AutoReplyLog.created_time.desc()).offset(offset).limit(size)
    result = await db.execute(list_query)
    logs = result.scalars().all()
    records = []
    for log in logs:
        records.append({
            "id": log.id,
            "accountId": log.account_id,
            "conversationId": log.conversation_id,
            "ruleId": log.rule_id,
            "triggerMessage": log.trigger_message,
            "replyContent": log.reply_content,
            "hitType": log.hit_type,
            "status": log.status,
            "failReason": log.fail_reason,
            "action": log.action,
            "safetyReasons": log.safety_reasons,
            "createdTime": _dt_text(log.created_time),
        })
    return ResultObject.success({
        "records": records,
        "total": total,
        "current": current,
        "size": size,
    })


@router.get("/auto-reply/rules/stats")
async def compat_auto_reply_rules_stats(
    days: int = Query(7, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Compat: GET /auto-reply/rules/stats（同步商业版：返回真实统计数据）。"""
    from ....models.entities import AutoReplyLog, AutoReplyRule
    from sqlalchemy import func as sa_func, select, text
    from datetime import datetime, timedelta
    safe_days = max(1, min(days, 90))
    start_date = datetime.now() - timedelta(days=safe_days)
    # 规则统计
    total_rules = (await db.execute(
        select(sa_func.count()).select_from(AutoReplyRule).where(AutoReplyRule.deleted == 0)
    )).scalar() or 0
    enabled_rules = (await db.execute(
        select(sa_func.count()).select_from(AutoReplyRule).where(
            AutoReplyRule.deleted == 0,
            AutoReplyRule.status == 1,
        )
    )).scalar() or 0
    # 日志统计
    today_count = (await db.execute(
        text("SELECT COUNT(*) FROM auto_reply_log WHERE deleted=0 AND DATE(created_time) = CURDATE()")
    )).scalar() or 0
    # 每日命中统计
    daily_rows = (await db.execute(
        text(
            "SELECT DATE(created_time) AS stat_date, COUNT(*) AS cnt "
            "FROM auto_reply_log WHERE deleted=0 AND created_time >= :start "
            "GROUP BY DATE(created_time) ORDER BY stat_date ASC"
        ),
        {"start": start_date},
    )).mappings().all()
    daily = [{"date": str(r["stat_date"]), "count": int(r["cnt"])} for r in daily_rows]
    # 按动作统计
    action_rows = (await db.execute(
        text(
            "SELECT action, COUNT(*) AS cnt FROM auto_reply_log "
            "WHERE deleted=0 AND created_time >= :start GROUP BY action"
        ),
        {"start": start_date},
    )).mappings().all()
    actions = [{"action": r["action"] or "unknown", "count": int(r["cnt"])} for r in action_rows]
    return ResultObject.success({
        "days": safe_days,
        "totalRules": total_rules,
        "enabledRules": enabled_rules,
        "disabledRules": max(total_rules - enabled_rules, 0),
        "todayCount": today_count,
        "daily": daily,
        "actions": actions,
        "matchedCount": sum(d["count"] for d in daily),
        "replyCount": sum(a["count"] for a in actions if a["action"] == "auto_send_allowed"),
    })


# -----------------------------------------------------------------------------
# 3. Quick Reply (/quick-reply/templates -> /quickReplyTemplate)
# -----------------------------------------------------------------------------

@router.get("/quick-reply/templates")
async def compat_quick_reply_templates_list(
    size: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Compat: GET /quick-reply/templates -> forward to /quickReplyTemplate/list (GET)"""
    from ....models.entities import QuickReplyTemplate
    from sqlalchemy import select
    result = await db.execute(
        select(QuickReplyTemplate).where(
            QuickReplyTemplate.deleted == 0
        ).order_by(QuickReplyTemplate.sort_order.asc(), QuickReplyTemplate.id.asc())
    )
    items = result.scalars().all()
    records = []
    for t in items:
        records.append({
            "id": t.id,
            "title": t.title,
            "content": t.content,
            "text": t.content,
            "sortOrder": t.sort_order or 0,
            "status": t.status if t.status is not None else 1,
            "createdAt": _dt_text(t.created_time),
            "updatedAt": _dt_text(t.updated_time),
        })
    return ResultObject.success({
        "records": records,
        "total": len(records),
    })


@router.post("/quick-reply/templates")
async def compat_quick_reply_templates_create(
    payload: dict = Body(default={}),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Compat: POST /quick-reply/templates -> forward to /quickReplyTemplate/save (POST)"""
    from ....models.entities import QuickReplyTemplate
    template = QuickReplyTemplate(
        title=payload.get("title") or "",
        content=payload.get("content") or "",
        sort_order=payload.get("sortOrder") or 0,
        status=1,
        deleted=0,
        created_time=_now(),
        updated_time=_now(),
    )
    db.add(template)
    await db.commit()
    await db.refresh(template)
    return ResultObject.success({
        "id": template.id,
        "message": "添加成功"
    })


@router.delete("/quick-reply/templates/{template_id}")
async def compat_quick_reply_templates_delete(
    template_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Compat: DELETE /quick-reply/templates/{id} -> forward to /quickReplyTemplate/delete (POST)"""
    from ....models.entities import QuickReplyTemplate
    await db.execute(text(
        """
        UPDATE quick_reply_template SET deleted = 1, updated_time = NOW()
        WHERE id = :id
        """),
        {"id": template_id}
    )
    await db.commit()
    return ResultObject.success({"message": "删除成功"})


# -----------------------------------------------------------------------------
# 4. AI Customer Service Settings (/business-settings/ai-customer-service)
# -----------------------------------------------------------------------------

@router.get("/business-settings/ai-customer-service")
async def compat_business_settings_ai_cs(
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Compat: GET /business-settings/ai-customer-service"""
    from ....services.business_settings import load_business_setting, AI_CS_SETTING_KEY, build_default_business_setting
    config = await load_business_setting(db, AI_CS_SETTING_KEY)
    if not config:
        config = build_default_business_setting(AI_CS_SETTING_KEY)
    return ResultObject.success(config)


@router.post("/business-settings/ai-customer-service")
async def compat_business_settings_save_ai_cs(
    payload: dict = Body(default={}),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Compat: POST /business-settings/ai-customer-service"""
    from ....services.business_settings import (
        AI_CS_SETTING_KEY,
        BusinessSettingValidationError,
        save_business_setting,
    )
    try:
        await save_business_setting(db, AI_CS_SETTING_KEY, payload)
    except BusinessSettingValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    await db.commit()
    return ResultObject.success({"ok": True})


# -----------------------------------------------------------------------------
# 5. Dashboard Extra Endpoints
# -----------------------------------------------------------------------------

@router.get("/dashboard/order-message-trend")
async def compat_dashboard_order_message_trend(
    days: int = Query(default=7, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Compat: GET /dashboard/order-message-trend"""
    from datetime import date, timedelta
    from sqlalchemy import cast, Date, func, select
    from ....models.entities import XianyuMessage

    today = date.today()
    date_labels = [(today - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]
    start_date = today - timedelta(days=days - 1)

    reply_rows_result = await db.execute(
        select(
            cast(XianyuMessage.created_time, Date).label("d"),
            func.count().label("c")
        ).where(
            XianyuMessage.is_auto_reply == 1,
            cast(XianyuMessage.created_time, Date) >= start_date
        ).group_by(cast(XianyuMessage.created_time, Date))
    )
    reply_map = {str(row.d): row.c for row in reply_rows_result}

    return ResultObject.success({
        "dates": date_labels,
        "orders": [0] * days,
        "messages": [reply_map.get(d, 0) for d in date_labels],
    })


@router.get("/dashboard/account-health")
async def compat_dashboard_account_health(
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Compat: GET /dashboard/account-health"""
    from ....models.entities import XianyuAccount
    from sqlalchemy import func, select

    result = await db.execute(select(func.count()).select_from(XianyuAccount))
    total = int(result.scalar() or 0)

    return ResultObject.success({
        "total": total,
        "active": total,
        "warning": 0,
        "error": 0,
    })


# -----------------------------------------------------------------------------
# 6. Auto-Delivery Rules (compat with delivery_workflow_compat)
# -----------------------------------------------------------------------------

@router.get("/auto-delivery/rules")
async def compat_auto_delivery_rules_list(
    current: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Compat: GET /auto-delivery/rules"""
    from ....models.entities import DeliveryRule
    from sqlalchemy import select
    result = await db.execute(
        select(DeliveryRule).where(
            DeliveryRule.deleted == 0
        ).order_by(DeliveryRule.id.desc())
    )
    rules = result.scalars().all()
    records = []
    for r in rules:
        records.append({
            "id": r.id,
            "ruleName": r.rule_name or "",
            "matchType": "keyword",
            "matchKeywords": r.trigger_keyword or "",
            "replyContent": r.delivery_content or "",
            "deliveryMode": r.delivery_mode or "kami",
            "cardGroupId": r.card_group_id,
            "goodsId": r.goods_id,
            "triggerOnPay": r.trigger_on_pay,
            "status": r.status if r.status is not None else 1,
            "createdTime": _dt_text(r.created_time),
            "updatedTime": _dt_text(r.updated_time),
        })
    total = len(records)
    start = (current - 1) * size
    end = start + size
    return ResultObject.success({
        "records": records[start:end],
        "total": total,
        "current": current,
        "size": size,
    })


@router.post("/auto-delivery/rules")
async def compat_auto_delivery_rules_create(
    payload: dict = Body(default={}),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Compat: POST /auto-delivery/rules"""
    from ....models.entities import DeliveryRule
    rule = DeliveryRule(
        rule_name=payload.get("ruleName") or "",
        delivery_mode=payload.get("deliveryMode") or "kami",
        card_group_id=payload.get("cardGroupId"),
        goods_id=payload.get("goodsId"),
        delivery_content=payload.get("replyContent") or "",
        trigger_on_pay=payload.get("triggerOnPay") if payload.get("triggerOnPay") is not None else 1,
        trigger_keyword=payload.get("matchKeywords") or "",
        status=1,
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return ResultObject.success({"id": rule.id, "message": "创建成功"})


@router.put("/auto-delivery/rules/{rule_id}")
async def compat_auto_delivery_rules_update(
    rule_id: int,
    payload: dict = Body(default={}),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Compat: PUT /auto-delivery/rules/{id}"""
    from ....models.entities import DeliveryRule
    result = await db.execute(
        select(DeliveryRule).where(
            DeliveryRule.id == rule_id,
            DeliveryRule.deleted == 0
        )
    )
    rule = result.scalar_one_or_none()
    if not rule:
        return ResultObject.failed("规则不存在", code=404)

    rule.rule_name = payload.get("ruleName") or rule.rule_name
    rule.delivery_mode = payload.get("deliveryMode") or rule.delivery_mode
    rule.card_group_id = payload.get("cardGroupId") or rule.card_group_id
    rule.goods_id = payload.get("goodsId") or rule.goods_id
    rule.delivery_content = payload.get("replyContent") or rule.delivery_content
    rule.trigger_keyword = payload.get("matchKeywords") or rule.trigger_keyword
    if payload.get("triggerOnPay") is not None:
        rule.trigger_on_pay = payload.get("triggerOnPay")
    rule.status = payload.get("status") if payload.get("status") is not None else rule.status
    rule.updated_time = _now()

    await db.commit()
    return ResultObject.success({"id": rule.id, "message": "更新成功"})


@router.delete("/auto-delivery/rules/{rule_id}")
async def compat_auto_delivery_rules_delete(
    rule_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Compat: DELETE /auto-delivery/rules/{id}"""
    from ....models.entities import DeliveryRule
    result = await db.execute(
        select(DeliveryRule).where(
            DeliveryRule.id == rule_id,
            DeliveryRule.deleted == 0
        )
    )
    rule = result.scalar_one_or_none()
    if not rule:
        return ResultObject.failed("规则不存在", code=404)
    rule.deleted = 1
    rule.updated_time = _now()
    await db.commit()
    return ResultObject.success({"message": "删除成功"})


# -----------------------------------------------------------------------------
# 7. Orders (compat: POST /order/list -> forward to commerce order list)
# -----------------------------------------------------------------------------

@router.post("/order/list")
async def compat_order_list(
    payload: dict = Body(default={}),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Compat: POST /order/list -> forward to commerce order list"""
    from ....models.entities import XianyuTradeOrder
    from sqlalchemy import select, func
    page = payload.get("current") or payload.get("page") or 1
    page_size = payload.get("size") or payload.get("pageSize") or 20

    count_result = await db.execute(select(func.count()).select_from(XianyuTradeOrder))
    total = int(count_result.scalar() or 0)

    result = await db.execute(
        select(XianyuTradeOrder).order_by(XianyuTradeOrder.id.desc()).offset(
            (page - 1) * page_size
        ).limit(page_size)
    )
    orders = result.scalars().all()
    records = []
    for o in orders:
        records.append({
            "id": o.id,
            "orderId": o.external_order_id,
            "buyerNick": o.buyer_name,
            "itemId": None,
            "itemTitle": None,
            "amount": float(o.total_amount) if o.total_amount else 0,
            "status": o.order_status,
            "createdTime": _dt_text(o.created_time),
        })
    return ResultObject.success({
        "records": records,
        "total": total,
        "current": page,
        "size": page_size,
    })


# ====================================================================
# 发布地址（前端 publishAddress.js 调用，参考项目 Java PublishAddressController）
# ====================================================================
publish_address_router = APIRouter(prefix="/publish-address", tags=["publish-address"])


@publish_address_router.get("/history")
async def publish_address_history(
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """查询当前用户的常用发布地址历史。"""
    try:
        # 参考项目：从 user_publish_address 表查询，按 use_count 排序，最多 20 条
        resolved_page = max(1, int(current or page or 1))
        sql = text(
            "SELECT id, poi_name, city, area, prov, division_id, gps, poi_id, detail, use_count, "
            "updated_time FROM user_publish_address "
            "WHERE deleted = 0 ORDER BY use_count DESC, updated_time DESC LIMIT 20"
        )
        result = await db.execute(sql)
        rows = result.mappings().all()
        items = []
        for row in rows:
            d = dict(row)
            # 过滤掉字段不完整的记录
            if not (d.get("poi_name") or d.get("city")):
                continue
            d["useCount"] = d.pop("use_count", 0)
            d["poiName"] = d.pop("poi_name", None)
            d["divisionId"] = d.pop("division_id", None)
            d["poiId"] = d.pop("poi_id", None)
            d["updatedTime"] = _dt_text(d.pop("updated_time", None))
            items.append(d)
        return ResultObject.success(items)
    except Exception as exc:
        logger.error("查询发布地址历史失败 errorType=%s", type(exc).__name__)
        # 表可能不存在，返回空列表
        return ResultObject.success([])


@publish_address_router.post("/save")
async def publish_address_save(
    body: dict = Body(default_factory=dict),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """保存常用发布地址（upsert，use_count 自增）。"""
    try:
        poi_name = (body.get("poiName") or "").strip()
        city = (body.get("city") or "").strip()
        if not (poi_name and city):
            return ResultObject.validate_failed("poiName 和 city 不能为空")

        # 使用 INSERT ... ON DUPLICATE KEY UPDATE（参考项目实现）
        sql = text(
            "INSERT INTO user_publish_address "
            "(poi_name, city, area, prov, division_id, gps, poi_id, detail, use_count, created_time, updated_time, deleted) "
            "VALUES (:poi_name, :city, :area, :prov, :division_id, :gps, :poi_id, :detail, 1, NOW(), NOW(), 0) "
            "ON DUPLICATE KEY UPDATE use_count = use_count + 1, updated_time = NOW()"
        )
        await db.execute(sql, {
            "poi_name": poi_name,
            "city": city,
            "area": body.get("area") or "",
            "prov": body.get("prov") or "",
            "division_id": body.get("divisionId") or "",
            "gps": body.get("gps") or "",
            "poi_id": body.get("poiId") or "",
            "detail": body.get("detail") or "",
        })
        await db.commit()
        return ResultObject.success({"message": "保存成功"})
    except Exception as exc:
        logger.error("保存发布地址失败 errorType=%s", type(exc).__name__)
        return ResultObject.internal_error()


# ====================================================================
# 会话列表（前端 conversations.js 调用，参考项目 Java XianyuMessageController）
# ====================================================================
conversations_router = APIRouter(prefix="/conversations", tags=["conversations"])


@conversations_router.get("")
@conversations_router.get("/")
async def conversations_list(
    accountId: int = Query(default=None),
    keyword: str = Query(default=""),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    current: int = Query(default=None, ge=1),
    size: int = Query(default=None, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """分页查询会话列表。"""
    try:
        # 从 xianyu_conversation 表查询（PATCH 端点已使用此表）
        resolved_page = max(1, int(current or page or 1))
        resolved_size = max(1, min(100, int(size or page_size or 20)))
        sql = (
            "SELECT id, account_id, peer_key, external_buyer_id, peer_external_uid, "
            "buyer_name, buyer_avatar, goods_title, goods_id, goods_cover_pic, last_message_content, "
            "last_message_time, unread_count, status, updated_time "
            "FROM xianyu_conversation WHERE 1 = 1"
        )
        params = {}
        if accountId:
            sql += " AND account_id = :account_id"
            params["account_id"] = accountId
        if keyword:
            sql += " AND (buyer_name LIKE :kw OR goods_title LIKE :kw OR last_message_content LIKE :kw)"
            params["kw"] = f"%{keyword}%"

        # 计算总数
        count_sql = f"SELECT COUNT(*) FROM ({sql}) AS t"
        count_result = await db.execute(text(count_sql), params)
        total = count_result.scalar() or 0

        # 分页查询
        paged_sql = sql + " ORDER BY COALESCE(updated_time, last_message_time) DESC LIMIT :limit OFFSET :offset"
        params["limit"] = resolved_size
        params["offset"] = (resolved_page - 1) * resolved_size
        result = await db.execute(text(paged_sql), params)
        rows = result.mappings().all()

        records = []
        for row in rows:
            d = dict(row)
            d["accountId"] = d.pop("account_id", None)
            d["peerKey"] = d.pop("peer_key", None)
            d["externalBuyerId"] = d.pop("external_buyer_id", None)
            d["peerExternalUid"] = d.pop("peer_external_uid", None)
            d["buyerName"] = d.pop("buyer_name", None)
            d["peerNick"] = d["buyerName"]
            d["buyerAvatar"] = d.pop("buyer_avatar", None)
            d["goodsTitle"] = d.pop("goods_title", None)
            d["goodsId"] = d.pop("goods_id", None)
            d["goodsCoverPic"] = d.pop("goods_cover_pic", None)
            d["itemId"] = d["goodsId"]
            d["lastMessage"] = d.pop("last_message_content", None)
            d["lastMessageTime"] = _dt_text(d.pop("last_message_time", None))
            d["unreadCount"] = d.pop("unread_count", 0)
            d["conversationStatus"] = d.get("status")
            d["peerUserId"] = d["externalBuyerId"] or d["peerExternalUid"]
            d["sid"] = d["peerKey"] or d["externalBuyerId"] or d["peerExternalUid"] or str(d.get("id") or "")
            d["updatedTime"] = _dt_text(d.pop("updated_time", None))
            records.append(d)

        return ResultObject.success({
            "records": records,
            "total": total,
            "current": resolved_page,
            "size": resolved_size,
        })
    except Exception as exc:
        logger.error(
            "Conversation fallback query failed errorType=%s",
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=503,
            detail="会话数据暂不可用，请稍后重试。",
        ) from exc


@conversations_router.get("/{conversation_id}/messages")
async def conversation_messages(
    conversation_id: int,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """分页查询指定会话的消息列表。"""
    try:
        # 从 xianyu_chat_message 表查询（与 messages.py 的 /api/msg/list 同表）
        sql = text(
            "SELECT id, conversation_id, sender_id, receiver_id, content, msg_type, "
            "created_time, is_read FROM xianyu_chat_message "
            "WHERE conversation_id = :cid ORDER BY created_time ASC LIMIT :limit OFFSET :offset"
        )
        result = await db.execute(sql, {
            "cid": conversation_id,
            "limit": page_size,
            "offset": (page - 1) * page_size,
        })
        rows = result.mappings().all()
        records = []
        for row in rows:
            d = dict(row)
            d["conversationId"] = d.pop("conversation_id", None)
            d["senderId"] = d.pop("sender_id", None)
            d["receiverId"] = d.pop("receiver_id", None)
            d["msgType"] = d.pop("msg_type", None)
            d["createdAt"] = _dt_text(d.pop("created_time", None))
            d["isRead"] = d.pop("is_read", 0)
            records.append(d)

        # 总数
        count_sql = text("SELECT COUNT(*) FROM xianyu_chat_message WHERE conversation_id = :cid")
        total = (await db.execute(count_sql, {"cid": conversation_id})).scalar() or 0

        return ResultObject.success({
            "records": records,
            "total": total,
            "current": page,
            "size": page_size,
        })
    except Exception as exc:
        logger.error("查询会话消息失败 errorType=%s", type(exc).__name__)
        return ResultObject.success({"records": [], "total": 0, "current": page, "size": page_size})
