"""
通知分发器：负责在事件触发时向用户配置的外部渠道推送消息。

支持的渠道：
- webhook     通用 Webhook（自定义 JSON）
- feishu      飞书自定义机器人（msg_type:text，可选签名校验）
- feishu_app  飞书自建应用（通过 Open API 发送文本消息，支持双向对话）
- dingtalk    钉钉自定义机器人（msgtype:text，可选加签）
- wechat_work 企业微信群机器人（msgtype:text）
- pushplus    PushPlus（向 pushplus.plus/send 发 token+title+content）
- email       邮箱 SMTP（向收件人发送邮件）

设计要点：
- 直接在 Python 端发送 HTTP/SMTP，低延迟、无需跨服务调用。
- 读取 user_notification_setting.config_json，按渠道类型格式化消息体。
- 发送结果写入 notification_delivery_log 表，便于审计与排错。
- 所有异常被捕获并记录日志，绝不影响主业务流程。
"""
import asyncio
import base64
import hashlib
import hmac
import json
import logging
import smtplib
import ssl
import time
import urllib.parse
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.database import async_session
from ..core.secret_store import decrypt_secret
from ..core.upload_security import request_public_https
from .feishu_bot import send_text_message_result as _feishu_app_send
from .notification_event_attempt import (
    NotificationDispatchOutcome,
    NotificationEventCommand,
    NotificationEventCoordinator,
    SqlNotificationEventAttemptStore,
)
from .ws_delivery_handler import extract_goods_id_from_url, extract_order_id_from_url

logger = logging.getLogger(__name__)

_NOTIFICATION_SECRET_FIELDS = (
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


def _decrypt_notification_channels(channels: object) -> list[dict]:
    """Materialize channel credentials only inside the delivery runtime.

    The settings API stores each credential with purpose-bound AES-GCM. This
    loader is the last boundary before a background delivery uses the values;
    callers outside this module continue to receive redacted configuration.
    A corrupt value raises so delivery fails closed instead of attempting a
    request with ciphertext as the URL or credential.
    """

    if not isinstance(channels, list):
        return []
    runtime_channels: list[dict] = []
    for raw_channel in channels:
        if not isinstance(raw_channel, dict):
            continue
        channel = dict(raw_channel)
        for field in _NOTIFICATION_SECRET_FIELDS:
            value = channel.get(field)
            if value in (None, ""):
                continue
            channel[field] = decrypt_secret(
                str(value),
                purpose=f"notification.{field.casefold()}",
            ) or ""
        runtime_channels.append(channel)
    return runtime_channels


# ============================================================
# 事件显示名常量（与前端 NotifySettings.vue 的 events 列表保持一致）
# ============================================================
EVENT_COOKIE_EXPIRED = "Cookie 到期"
EVENT_NEW_ORDER = "新订单提醒"
EVENT_AUTO_DELIVERY_SUCCESS = "自动发货成功"
EVENT_AUTO_DELIVERY_FAILURE = "自动发货失败"
EVENT_ACCOUNT_OFFLINE = "账号掉线"
EVENT_CAPTCHA_REQUIRED = "人机验证"
EVENT_CAPTCHA_SUCCESS = "人机验证成功"


_NEW_ORDER_NOTIFIED_TTL_SECONDS = 15 * 60
_EVENT_KEY_COOKIE_EXPIRED = "cookie_expired"
_EVENT_KEY_NEW_ORDER = "new_order"


def _notification_target_digest(identity: object) -> str:
    """Return an irreversible target identity without retaining its inputs."""

    canonical = json.dumps(
        identity,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


_COOKIE_EXPIRED_TARGET_DIGEST = _notification_target_digest(
    {"kind": "cookie_invalid", "version": 1}
)
_EVENT_COORDINATOR = NotificationEventCoordinator(
    SqlNotificationEventAttemptStore(async_session)
)


async def clear_cookie_expired_state(account_id: int) -> int:
    """Resolve durable Cookie alert state after verified credential recovery."""

    try:
        return await _EVENT_COORDINATOR.resolve(
            _EVENT_KEY_COOKIE_EXPIRED,
            account_id,
            _COOKIE_EXPIRED_TARGET_DIGEST,
            resolution_code="verified_recovery",
        )
    except Exception:
        logger.warning(
            "Cookie notification recovery state could not be persisted account=%d",
            account_id,
            exc_info=True,
        )
        return 0


def _build_new_order_dedup_token(account_id: int, msg: dict) -> str:
    """Build only a SHA-256 identity; raw order/message fields stay transient."""

    reminder_url = str(msg.get("reminderUrl") or msg.get("reminder_url") or "")
    order_id = extract_order_id_from_url(reminder_url) or ""
    goods_id = str(msg.get("xyGoodsId") or extract_goods_id_from_url(reminder_url) or "").strip()
    buyer = str(msg.get("senderUserId") or msg.get("buyerUserId") or "").strip()
    sid = str(msg.get("sId") or msg.get("sid") or "").strip()
    pnm_id = str(msg.get("pnmId") or msg.get("pnm_id") or "").strip()
    reminder = str(msg.get("reminderContent") or msg.get("reminder_content") or "").strip()

    if order_id:
        identity: dict[str, object] = {"kind": "order", "order": order_id}
    elif sid or pnm_id:
        identity = {
            "kind": "conversation_order",
            "sid": sid,
            "pnm": pnm_id,
            "goods": goods_id,
            "buyer": buyer,
        }
    elif goods_id or buyer:
        identity = {
            "kind": "buyer_goods_order",
            "goods": goods_id,
            "buyer": buyer,
        }
    elif reminder:
        identity = {"kind": "reminder_fallback", "reminder": reminder}
    else:
        identity = {"kind": "unidentified_order", "version": 1}

    identity["account"] = int(account_id)
    return _notification_target_digest(identity)


async def clear_new_order_state(account_id: int, msg: Optional[dict] = None) -> int:
    """Explicitly resolve one or all durable new-order notification targets."""

    try:
        digest = (
            None if msg is None else _build_new_order_dedup_token(account_id, msg)
        )
        return await _EVENT_COORDINATOR.resolve(
            _EVENT_KEY_NEW_ORDER,
            account_id,
            digest,
            resolution_code="explicit_clear",
        )
    except Exception:
        logger.warning(
            "New-order notification clear state could not be persisted account=%d",
            account_id,
            exc_info=True,
        )
        return 0


async def _lookup_account_name(account_id: int) -> str:
    """查询账号昵称，找不到时回退为账号ID字符串。"""
    try:
        async with async_session() as db:
            row = (await db.execute(
                text(
                    "SELECT nickname FROM xianyu_account "
                    "WHERE id = :aid AND deleted = 0 LIMIT 1"
                ),
                {"aid": account_id},
            )).mappings().first()
            if row and row.get("nickname"):
                return str(row["nickname"])
    except Exception:
        logger.debug("查询账号昵称失败，回退为账号ID", exc_info=True)
    return str(account_id)


# ============================================================
# 内部工具函数
# ============================================================

def _render_template(
    template: str,
    title: str,
    content: str,
    context: Optional[dict[str, object]] = None,
) -> str:
    """渲染消息模板，支持 {title}、{content}、{time} 变量。"""
    if not template:
        template = "{{title}}\n{{content}}"

    values: dict[str, str] = {
        "title": title or "",
        "content": content or "",
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "event": "",
        "account": "",
        "channel": "",
    }
    if isinstance(context, dict):
        for key, value in context.items():
            values[str(key)] = "" if value is None else str(value)

    rendered = template
    for key, value in values.items():
        rendered = rendered.replace(f"{{{{{key}}}}}", value)
        rendered = rendered.replace(f"{{{key}}}", value)
    return rendered


def _gen_feishu_sign(timestamp: int, secret: str) -> str:
    """飞书自定义机器人签名：HMAC-SHA256(key=timestamp\nsecret, msg=空) → base64。"""
    string_to_sign = f"{timestamp}\n{secret}"
    hmac_code = hmac.new(
        string_to_sign.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()
    return base64.b64encode(hmac_code).decode("utf-8")


def _gen_dingtalk_sign(timestamp: int, secret: str) -> str:
    """钉钉加签：HMAC-SHA256(key=secret, msg=timestamp\nsecret) → base64 → URLEncode。"""
    string_to_sign = f"{timestamp}\n{secret}"
    hmac_code = hmac.new(
        secret.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return urllib.parse.quote_plus(base64.b64encode(hmac_code).decode("utf-8"))


def _is_channel_ready(channel: dict) -> bool:
    """渠道是否已启用且必要字段已配置。"""
    if not channel or not channel.get("enabled"):
        return False
    ctype = str(channel.get("type") or "")
    if ctype in ("webhook", "feishu", "dingtalk", "wechat_work"):
        return bool(str(channel.get("webhookUrl") or "").strip())
    if ctype == "feishu_app":
        return all(
            bool(str(channel.get(k) or "").strip())
            for k in ("appId", "secret", "verificationToken", "receiveId")
        )
    if ctype == "pushplus":
        return bool(str(channel.get("receiver") or "").strip())
    if ctype == "email":
        return all(str(channel.get(k) or "").strip() for k in ("smtpHost", "smtpUser", "smtpPass", "receiver"))
    return False


def _select_target_channels(channels: list, send_mode: str) -> list:
    """根据发送模式筛选目标渠道列表。

    - single 模式：仅发送给第一个可用渠道。
    - multi 模式：发送给所有可用渠道。
    """
    if not isinstance(channels, list):
        return []
    ready = [c for c in channels if _is_channel_ready(c)]
    if not ready:
        return []
    if send_mode == "single":
        return [ready[0]]
    return ready


def _is_event_enabled(events: list, event_display_name: str) -> bool:
    """检查指定事件是否启用。配置中找不到时默认启用。"""
    if not isinstance(events, list):
        return True
    for e in events:
        if e and e.get("event") == event_display_name:
            return bool(e.get("enabled", True))
    return True


async def _load_notify_config(db: AsyncSession) -> Optional[dict]:
    """读取租户的通知配置。返回 {channels, events, sendMode, user_id} 或 None。"""
    row = (await db.execute(
        text(
            "SELECT user_id, config_json FROM user_notification_setting "
            "WHERE deleted = 0 "
            "ORDER BY updated_time DESC LIMIT 1"
        ),
        {},
    )).mappings().first()
    if not row:
        return None
    try:
        config = json.loads(row["config_json"]) if isinstance(row["config_json"], str) else row["config_json"]
    except Exception:
        return None
    if not isinstance(config, dict):
        return None
    try:
        channels = _decrypt_notification_channels(config.get("channels") or [])
    except Exception as exc:
        # Do not include the ciphertext (or a provider credential) in logs.
        # A wrong encryption key is an operator-recoverable unavailable state,
        # never a reason to attempt delivery with the stored envelope.
        logger.error(
            "Notification channel credentials are unavailable errorType=%s",
            type(exc).__name__,
        )
        return None
    return {
        "user_id": row["user_id"],
        "channels": channels,
        "events": config.get("events") or [],
        "sendMode": config.get("sendMode") or "single",
    }


async def _insert_delivery_log(
    db: AsyncSession,
    user_id: Optional[int],
    channel_key: str,
    channel_name: str,
    event_type: str,
    success: bool,
    status_code: Optional[int],
    cost_ms: int,
    message: str,
    request_body: str,
    response_body: str,
) -> None:
    """写入通知投递日志。失败时仅记录调试日志，不影响主流程。"""
    try:
        await db.execute(
            text(
                """
                INSERT INTO notification_delivery_log(user_id, channel_key, channel_name, event_type,
                    success, status_code, cost_ms, message, request_body, response_body,
                    retry_count, created_time
                ) VALUES(
                    :user_id, :channel_key, :channel_name, :event_type,
                    :success, :status_code, :cost_ms, :message, :request_body, :response_body,
                    0, NOW()
                )
                """
            ),
            {
                "user_id": user_id,
                "channel_key": channel_key,
                "channel_name": channel_name,
                "event_type": event_type,
                "success": 1 if success else 0,
                "status_code": status_code,
                "cost_ms": cost_ms,
                "message": (message or "")[:500],
                "request_body": request_body,
                "response_body": response_body,
            },
        )
        await db.commit()
    except Exception:
        logger.debug("写入 notification_delivery_log 失败，忽略", exc_info=True)


# ============================================================
# 各渠道发送实现：统一返回 dict {success, status_code, cost_ms, message, request_body, response_body}
# ============================================================


def _byte_summary(label: str, value: str | bytes | None) -> str:
    raw = value if isinstance(value, bytes) else str(value or "").encode("utf-8")
    return f"{label}_bytes={len(raw)}"


def _delivery_result(
    *,
    success: bool,
    status_code: Optional[int],
    cost_ms: int,
    message: str,
    request_body: str | bytes | None,
    response_body: str | bytes | None,
    outcome_known: bool,
) -> dict:
    """Build a delivery result without retaining notification content or PII."""

    return {
        "success": bool(success),
        "status_code": status_code,
        "cost_ms": max(0, int(cost_ms)),
        "message": (message or "通知发送失败")[:500],
        "request_body": _byte_summary("request", request_body),
        "response_body": _byte_summary("response", response_body),
        "outcome_known": bool(outcome_known),
    }

async def _insert_in_app_notification(
    db: AsyncSession,
    account_id: Optional[int],
    event_type: str,
    title: str,
    content: str,
    level: str = "warning",
    priority: int = 2,
) -> None:
    """向 notification 表写入站内提醒，便于前端在账号页直接展示。"""
    try:
        await db.execute(
            text(
                """
                INSERT INTO notification(user_id, account_id, notice_type, notification_type,
                    title, content, level, priority, is_read, created_time, updated_time, deleted
                ) VALUES(
                    NULL, :account_id, :event_type, :event_type,
                    :title, :content, :level, :priority, 0, NOW(), NOW(), 0
                )
                """
            ),
            {
                "account_id": account_id,
                "event_type": event_type,
                "title": (title or "")[:200],
                "content": content or "",
                "level": level or "warning",
                "priority": priority,
            },
        )
        await db.commit()
    except Exception:
        logger.debug("写入 notification 失败，忽略", exc_info=True)


async def _send_webhook(channel: dict, title: str, rendered: str, timeout_seconds: int) -> dict:
    url = str(channel.get("webhookUrl") or "").strip()
    method = str(channel.get("method") or "POST").upper()
    body = json.dumps(
        {"title": title, "content": rendered, "channel": channel.get("key"), "time": time.strftime("%Y-%m-%d %H:%M:%S")},
        ensure_ascii=False,
    )
    started = time.time()
    try:
        resp = await request_public_https(
            url,
            method=method,
            content=body if method == "POST" else None,
            headers={"Content-Type": "application/json"},
            timeout_seconds=timeout_seconds,
        )
        cost_ms = int((time.time() - started) * 1000)
        success = 200 <= resp.status_code < 300
        return _delivery_result(
            success=success,
            status_code=resp.status_code,
            cost_ms=cost_ms,
            message="Webhook 发送成功" if success else f"Webhook 返回 HTTP {resp.status_code}",
            request_body=body,
            response_body=resp.body,
            outcome_known=(
                success or 400 <= int(resp.status_code) < 500
            ),
        )
    except Exception:
        return _err_result(body, "Webhook 请求失败", int((time.time() - started) * 1000))


async def _send_feishu(channel: dict, rendered: str, timeout_seconds: int) -> dict:
    url = str(channel.get("webhookUrl") or "").strip()
    secret = str(channel.get("secret") or "").strip()
    body = json.dumps({"msg_type": "text", "content": {"text": rendered}}, ensure_ascii=False)
    if secret:
        ts = int(time.time())
        sign = _gen_feishu_sign(ts, secret)
        url = f"{url}{'&' if '?' in url else '?'}timestamp={ts}&sign={sign}"
    return await _http_post(url, body, timeout_seconds, parse_key="code", parse_success=0,
                            ok_msg="飞书发送成功", fail_prefix="飞书发送失败")


async def _send_feishu_app(channel: dict, rendered: str) -> dict:
    """通过飞书自建应用 Open API 发送文本消息。"""
    receive_id = str(channel.get("receiveId") or "").strip()
    receive_id_type = str(channel.get("receiveIdType") or "open_id").strip() or "open_id"
    try:
        result = await _feishu_app_send(receive_id, rendered, receive_id_type)
    except Exception as exc:
        return {
            "success": False,
            "status_code": 0,
            "cost_ms": 0,
            "message": f"飞书自建应用发送异常：{exc}",
            "request_body": "",
            "response_body": "",
            "outcome_known": False,
        }
    return {
        "success": bool(result.get("success")),
        "status_code": int(result.get("status_code") or 0),
        "cost_ms": int(result.get("cost_ms") or 0),
        "message": str(result.get("message") or ("飞书自建应用发送成功" if result.get("success") else "飞书自建应用发送失败")),
        "request_body": "",
        "response_body": str(result.get("response_body") or ""),
        "outcome_known": bool(result.get("outcome_known", True)),
    }


async def _send_dingtalk(channel: dict, rendered: str, timeout_seconds: int) -> dict:
    url = str(channel.get("webhookUrl") or "").strip()
    secret = str(channel.get("secret") or "").strip()
    body = json.dumps({"msgtype": "text", "text": {"content": rendered}}, ensure_ascii=False)
    if secret.startswith("SEC"):
        ts = int(time.time() * 1000)
        sign = _gen_dingtalk_sign(ts, secret)
        url = f"{url}{'&' if '?' in url else '?'}timestamp={ts}&sign={sign}"
    return await _http_post(url, body, timeout_seconds, parse_key="errcode", parse_success=0,
                            ok_msg="钉钉发送成功", fail_prefix="钉钉发送失败")


async def _send_wechat_work(channel: dict, rendered: str, timeout_seconds: int) -> dict:
    url = str(channel.get("webhookUrl") or "").strip()
    body = json.dumps({"msgtype": "text", "text": {"content": rendered}}, ensure_ascii=False)
    return await _http_post(url, body, timeout_seconds, parse_key="errcode", parse_success=0,
                            ok_msg="企业微信发送成功", fail_prefix="企业微信发送失败")


async def _send_pushplus(channel: dict, title: str, rendered: str, timeout_seconds: int) -> dict:
    token = str(channel.get("receiver") or "").strip()
    body = json.dumps({"token": token, "title": title, "content": rendered, "template": "txt"}, ensure_ascii=False)
    return await _http_post("https://www.pushplus.plus/send", body, timeout_seconds,
                            parse_key="code", parse_success=200,
                            ok_msg="PushPlus 发送成功", fail_prefix="PushPlus 发送失败")


def _smtp_send_sync(
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_pass: str,
    from_email: str,
    to_email: str,
    message: str,
    timeout_seconds: int,
) -> dict:
    """Perform blocking SMTP I/O; callers must run this in a worker thread."""

    if smtp_port == 465:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(
            smtp_host,
            smtp_port,
            timeout=max(timeout_seconds, 10),
            context=context,
        ) as smtp:
            smtp.login(smtp_user, smtp_pass)
            return smtp.sendmail(from_email, [to_email], message)

    with smtplib.SMTP(
        smtp_host,
        smtp_port,
        timeout=max(timeout_seconds, 10),
    ) as smtp:
        smtp.starttls()
        smtp.login(smtp_user, smtp_pass)
        return smtp.sendmail(from_email, [to_email], message)


async def _send_email(channel: dict, title: str, rendered: str, timeout_seconds: int) -> dict:
    smtp_host = str(channel.get("smtpHost") or "").strip()
    smtp_port = int(channel.get("smtpPort") or 465)
    smtp_user = str(channel.get("smtpUser") or "").strip()
    smtp_pass = str(channel.get("smtpPass") or "").strip()
    from_email = str(channel.get("fromEmail") or "").strip() or smtp_user
    to_email = str(channel.get("receiver") or "").strip()
    body_log = (
        f"recipient_configured={bool(to_email)}&"
        f"subject_bytes={len(title.encode('utf-8'))}&body_bytes={len(rendered.encode('utf-8'))}"
    )
    started = time.time()
    try:
        msg = MIMEText(rendered, "plain", "utf-8")
        msg["Subject"] = title
        msg["From"] = formataddr(("闲鱼助手", from_email))
        msg["To"] = to_email
        msg["Date"] = formatdate(localtime=True)
        refused_recipients = await asyncio.to_thread(
            _smtp_send_sync,
            smtp_host,
            smtp_port,
            smtp_user,
            smtp_pass,
            from_email,
            to_email,
            msg.as_string(),
            timeout_seconds,
        )
        cost_ms = int((time.time() - started) * 1000)
        if refused_recipients:
            return _delivery_result(
                success=False,
                status_code=550,
                cost_ms=cost_ms,
                message="SMTP 拒绝收件人",
                request_body=body_log,
                response_body=b"",
                outcome_known=True,
            )
        return _delivery_result(
            success=True,
            status_code=250,
            cost_ms=cost_ms,
            message="邮箱发送成功",
            request_body=body_log,
            response_body="SMTP sent OK",
            outcome_known=True,
        )
    except Exception:
        return _err_result(body_log, "SMTP 请求失败", int((time.time() - started) * 1000))


async def _http_post(url: str, body: str, timeout_seconds: int,
                     parse_key: str, parse_success: int,
                     ok_msg: str, fail_prefix: str) -> dict:
    """通用 POST 发送 + 响应解析。parse_key 为响应 JSON 中标识成功的字段名。"""
    started = time.time()
    try:
        resp = await request_public_https(
            url,
            method="POST",
            content=body,
            headers={"Content-Type": "application/json"},
            timeout_seconds=timeout_seconds,
        )
        cost_ms = int((time.time() - started) * 1000)
        status_code = int(resp.status_code)
        if 200 <= status_code < 300:
            try:
                resp_json = resp.json()
            except Exception:
                return _delivery_result(
                    success=False,
                    status_code=status_code,
                    cost_ms=cost_ms,
                    message=f"{fail_prefix}: 响应格式无效",
                    request_body=body,
                    response_body=resp.body,
                    outcome_known=False,
                )
            if not isinstance(resp_json, dict) or parse_key not in resp_json:
                return _delivery_result(
                    success=False,
                    status_code=status_code,
                    cost_ms=cost_ms,
                    message=f"{fail_prefix}: 响应格式无效",
                    request_body=body,
                    response_body=resp.body,
                    outcome_known=False,
                )
            code = resp_json.get(parse_key)
            if str(code) != str(parse_success):
                return _delivery_result(
                    success=False,
                    status_code=status_code,
                    cost_ms=cost_ms,
                    message=f"{fail_prefix}: 服务方拒绝请求",
                    request_body=body,
                    response_body=resp.body,
                    outcome_known=True,
                )
            return _delivery_result(
                success=True,
                status_code=status_code,
                cost_ms=cost_ms,
                message=ok_msg,
                request_body=body,
                response_body=resp.body,
                outcome_known=True,
            )

        return _delivery_result(
            success=False,
            status_code=status_code,
            cost_ms=cost_ms,
            message=f"{fail_prefix}: HTTP {status_code}",
            request_body=body,
            response_body=resp.body,
            outcome_known=400 <= status_code < 500,
        )
    except Exception:
        return _err_result(body, f"{fail_prefix}: 请求失败", int((time.time() - started) * 1000))


def _err_result(body: str, err: str, cost_ms: int) -> dict:
    return _delivery_result(
        success=False,
        status_code=None,
        cost_ms=cost_ms,
        message=err or "通知请求失败",
        request_body=body,
        response_body=b"",
        outcome_known=False,
    )


# ============================================================
# 对外核心 API
# ============================================================

async def dispatch_notification_detailed(
    event_display_name: str,
    title: str,
    content: str,
    template_context: Optional[dict[str, object]] = None,
) -> NotificationDispatchOutcome:
    """Dispatch once and report whether a provider call and outcome are known.

    Missing/disabled configuration is a known local non-call. A transport
    exception is unknown unless another selected channel already confirmed
    delivery.
    """
    called = False
    delivered = False
    all_outcomes_known = True
    try:
        async with async_session() as db:
            config = await _load_notify_config(db)
            if not config:
                return NotificationDispatchOutcome(False, False, True)
            if not _is_event_enabled(config["events"], event_display_name):
                return NotificationDispatchOutcome(False, False, True)
            targets = _select_target_channels(config["channels"], config["sendMode"])
            if not targets:
                return NotificationDispatchOutcome(False, False, True)
            user_id = config.get("user_id")
            for channel in targets:
                ctype = str(channel.get("type") or "webhook")
                context = {"event": event_display_name}
                if isinstance(template_context, dict):
                    context.update(template_context)
                rendered = _render_template(channel.get("template"), title, content, context=context)
                timeout_seconds = int(channel.get("timeoutSeconds") or 10)
                called = True
                try:
                    if ctype == "feishu":
                        result = await _send_feishu(channel, rendered, timeout_seconds)
                    elif ctype == "feishu_app":
                        result = await _send_feishu_app(channel, rendered)
                    elif ctype == "dingtalk":
                        result = await _send_dingtalk(channel, rendered, timeout_seconds)
                    elif ctype == "wechat_work":
                        result = await _send_wechat_work(channel, rendered, timeout_seconds)
                    elif ctype == "pushplus":
                        result = await _send_pushplus(channel, title, rendered, timeout_seconds)
                    elif ctype == "email":
                        result = await _send_email(channel, title, rendered, timeout_seconds)
                    else:
                        result = await _send_webhook(channel, title, rendered, timeout_seconds)
                    channel_delivered = bool(result["success"])
                    channel_outcome_known = bool(result.get("outcome_known", True))
                except Exception:
                    all_outcomes_known = False
                    logger.warning(
                        "Notification transport result unknown event=%s type=%s",
                        event_display_name,
                        ctype,
                        exc_info=True,
                    )
                    break

                delivered = delivered or channel_delivered
                all_outcomes_known = (
                    all_outcomes_known and channel_outcome_known
                )

                await _insert_delivery_log(
                    db, user_id,
                    channel_key=str(channel.get("key") or ctype),
                    channel_name=str(channel.get("name") or ctype),
                    event_type=event_display_name,
                    success=result["success"],
                    status_code=result["status_code"],
                    cost_ms=result["cost_ms"],
                    message=result["message"],
                    request_body=result["request_body"],
                    response_body=result["response_body"],
                )
                if channel_delivered:
                    logger.info(
                        "通知发送成功 event=%s type=%s",
                        event_display_name,
                        ctype,
                    )
                else:
                    logger.warning(
                        "通知发送失败 event=%s type=%s status=%s",
                        event_display_name,
                        ctype,
                        result["status_code"],
                    )
            return NotificationDispatchOutcome(
                called,
                delivered,
                delivered or all_outcomes_known,
            )
    except Exception:
        logger.warning(
            "dispatch_notification 异常，忽略: event=%s",
            event_display_name,
            exc_info=True,
        )
        return NotificationDispatchOutcome(
            called,
            delivered,
            delivered or all_outcomes_known,
        )


async def dispatch_notification(
    event_display_name: str,
    title: str,
    content: str,
    template_context: Optional[dict[str, object]] = None,
) -> bool:
    """Backward-compatible delivery-only view of the detailed dispatcher."""

    outcome = await dispatch_notification_detailed(
        event_display_name,
        title,
        content,
        template_context,
    )
    return outcome.delivered


# ============================================================
# 高层便捷 API —— 供事件触发点调用
# ============================================================

async def notify_cookie_expired(account_id: int, cookie_status: int) -> None:
    """Send one durable alert until verified Cookie recovery resolves it."""

    async def send() -> NotificationDispatchOutcome:
        status_text = "已失效" if cookie_status == 0 else f"状态变更({cookie_status})"
        account_name = await _lookup_account_name(account_id)
        content = (
            f"账号名称：{account_name}\n"
            f"状态：{status_text}\n"
            f"时间：{time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"掉线期间买家消息无法接收、自动回复停止。\n"
            f"请在闲鱼助手后台「账号管理」重新扫码登录该账号。"
        )
        return await dispatch_notification_detailed(
            event_display_name=EVENT_COOKIE_EXPIRED,
            title="⚠️ Cookie 失效告警",
            content=content,
        )

    outcome = await _EVENT_COORDINATOR.execute(
        NotificationEventCommand(
            event_type=_EVENT_KEY_COOKIE_EXPIRED,
            account_id=account_id,
            target_digest=_COOKIE_EXPIRED_TARGET_DIGEST,
        ),
        send,
    )
    if outcome.repeated:
        logger.info(
            "Cookie notification reused durable state account=%d status=%s",
            account_id,
            outcome.status,
        )


async def notify_new_order(account_id: int, msg: dict) -> None:
    """Send one durable order alert generation per fifteen-minute window."""

    target_digest = _build_new_order_dedup_token(account_id, msg)

    async def send() -> NotificationDispatchOutcome:
        reminder = str(msg.get("reminderContent") or msg.get("reminder_content") or "")
        reminder_url = str(msg.get("reminderUrl") or msg.get("reminder_url") or "")
        goods_id = str(msg.get("xyGoodsId") or "")
        buyer = str(msg.get("senderUserName") or "")
        order_id = extract_order_id_from_url(reminder_url) or ""
        content = (
            f"账号ID：{account_id}\n"
            f"商品ID：{goods_id or '未知'}\n"
            f"订单号：{order_id or '未知'}\n"
            f"买家：{buyer or '未知'}\n"
            f"提醒：{reminder or '有新订单需处理'}\n"
            f"时间：{time.strftime('%Y-%m-%d %H:%M:%S')}"
        )
        return await dispatch_notification_detailed(
            event_display_name=EVENT_NEW_ORDER,
            title="🛒 新订单提醒",
            content=content,
        )

    outcome = await _EVENT_COORDINATOR.execute(
        NotificationEventCommand(
            event_type=_EVENT_KEY_NEW_ORDER,
            account_id=account_id,
            target_digest=target_digest,
            generation_ttl_seconds=_NEW_ORDER_NOTIFIED_TTL_SECONDS,
        ),
        send,
    )
    if outcome.repeated:
        logger.info(
            "New-order notification reused durable state account=%d status=%s generation=%d",
            account_id,
            outcome.status,
            outcome.generation,
        )


async def notify_account_offline(account_id: int, reason: str = "") -> None:
    """账号掉线通知。"""
    content = (
        f"账号ID：{account_id}\n"
        f"原因：{reason or '连接断开'}\n"
        f"时间：{time.strftime('%Y-%m-%d %H:%M:%S')}"
    )
    await dispatch_notification(
        event_display_name=EVENT_ACCOUNT_OFFLINE,
        title="📡 账号掉线提醒",
        content=content,
    )


async def notify_captcha_required(account_id: int, scene: str = "") -> None:
    """人机验证提醒。同时写入站内提醒便于前端展示。"""
    account_name = await _lookup_account_name(account_id)
    content = (
        f"账号名称：{account_name}\n"
        f"场景：{scene or '触发风控验证'}\n"
        f"时间：{time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"请尽快处理，否则可能影响自动化任务。"
    )
    try:
        async with async_session() as db:
            await _insert_in_app_notification(
                db,
                account_id=account_id,
                event_type=EVENT_CAPTCHA_REQUIRED,
                title="人机验证提醒",
                content=content,
                level="warning",
                priority=2,
            )
    except Exception:
        logger.debug("写入人机验证站内提醒失败，忽略", exc_info=True)
    await dispatch_notification(
        event_display_name=EVENT_CAPTCHA_REQUIRED,
        title="🤖 人机验证提醒",
        content=content,
    )



async def notify_auto_delivery(account_id: int, success: bool, order_id: str = "", detail: str = "") -> None:
    """自动发货结果通知。success=True 发成功通知，否则发失败通知。"""
    if success:
        event = EVENT_AUTO_DELIVERY_SUCCESS
        title = "✅ 自动发货成功"
    else:
        event = EVENT_AUTO_DELIVERY_FAILURE
        title = "❌ 自动发货失败"
    content = (
        f"账号ID：{account_id}\n"
        f"订单号：{order_id or '未知'}\n"
        f"详情：{detail or ('发货成功' if success else '发货失败')}\n"
        f"时间：{time.strftime('%Y-%m-%d %H:%M:%S')}"
    )
    await dispatch_notification(
        event_display_name=event,
        title=title,
        content=content,
    )
