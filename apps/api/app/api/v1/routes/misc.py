import logging
import os
import asyncio
import datetime
import hashlib
import ipaddress
import json
import time
import weakref
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse
from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, select, text
from ....core.background_tasks import spawn_background_task
from ....core.database import get_db
from ....core.response import ResultObject
from ....core.config import settings
from ....core.cookie_crypto import decrypt_cookie_if_needed, encrypt_cookie_for_storage
from ....core.upload_security import (
    UnsafePathError,
    UnsafeRemoteURLError,
    UploadValidationError,
    download_public_image,
    normalize_image_bytes,
    read_upload_limited,
    resolve_upload_path,
    write_upload_bytes_atomic,
)
from ....models.entities import (
    XianyuAccount,
    XianyuAccountAuth, XianyuAccountRuntime,
    XianyuGoods,
)
from ....services.ws_client import ws_manager
from ....services.ws_storage import save_chat_message
from ....services.ws_sse import broadcaster
from ....core.xianyu_qr_login import (
    QrSessionCapacityError,
    cleanup_owner_sessions,
    generate_qrcode,
    get_session_cookies,
    get_session_status,
    mark_session_persisted,
)
from ....services.xianyu_goods_sync import (
    _get_token_from_cookie as _xianyu_token_from_cookie,
)
from ....services.auto_category import upload_image_to_xianyu as _upload_image_to_xianyu
from ....services.manual_message_attempt import (
    ManualMessageAttemptError,
    ManualMessageCommand,
    ManualMessagePreflightError,
    ManualMessageRuntime,
    ManualMessageSendResult,
    SqlManualMessageAttemptStore,
)

async def update_ws_heartbeat(db: AsyncSession, payload: dict) -> XianyuAccountRuntime:
    """Persist the authoritative WebSocket status after a connection change."""
    account_id = _parse_account_id(payload.get("accountId") or payload.get("account_id"))
    if account_id is None or account_id <= 0:
        raise ValueError("accountId 无效")
    result = await db.execute(
        select(XianyuAccountRuntime).where(
            XianyuAccountRuntime.account_id == account_id,
            XianyuAccountRuntime.deleted == 0,
        )
    )
    runtime = result.scalar_one_or_none()
    if runtime is None:
        runtime = XianyuAccountRuntime(account_id=account_id, deleted=0)
        db.add(runtime)

    now = datetime.datetime.now()
    runtime.online_status = 1 if int(payload.get("onlineStatus") or 0) == 1 else 0
    runtime.ws_status = 1 if int(payload.get("wsStatus") or 0) == 1 else 0
    try:
        runtime.ws_latency_ms = max(0, min(int(payload.get("latency") or 0), 60_000))
    except (TypeError, ValueError):
        runtime.ws_latency_ms = 0
    runtime.last_heartbeat_time = now
    if runtime.online_status == 1:
        runtime.last_online_time = now
    return runtime
from ..deps import get_current_user

logger = logging.getLogger(__name__)

_IMAGE_UPLOAD_BASE_DIR = str(resolve_upload_path("images"))


def _normalize_safe_goofish_id(value: object) -> str:
    text_value = str(value or "").strip()
    if not text_value:
        return ""
    if text_value.startswith("sid:"):
        text_value = text_value[4:]
    if text_value.endswith("@goofish"):
        text_value = text_value[:-8]
    return text_value.strip()


def _to_goofish_id(value: object) -> str:
    normalized = _normalize_safe_goofish_id(value)
    if not normalized:
        return ""
    return normalized if normalized.endswith("@goofish") else f"{normalized}@goofish"


def _parse_account_id(value: object) -> Optional[int]:
    try:
        if value is None:
            return None
        text_value = str(value).strip()
        if not text_value:
            return None
        return int(text_value)
    except Exception:
        return None


def _is_ws_auth_failure(status: dict) -> bool:
    phase = str(status.get("phase") or status.get("status") or "").lower()
    last_error = str(status.get("lastError") or status.get("last_error") or "")
    if phase in {"token_failed", "register_failed", "auth_failed", "captcha", "expired"}:
        return True
    hints = ("滑块", "验证", "captcha", "过期", "token", "cookie", "rgv587", "login", "登录")
    return any(hint.lower() in last_error.lower() for hint in hints)


def _ws_auth_failure_message(status: dict) -> str:
    extra = (status.get("lastError") or status.get("last_error") or "").strip() if isinstance(status, dict) else ""
    suffix = f"（{extra}）" if extra else ""
    return f"连接失败：检测到 Cookie/_m_h5_tk 已过期或触发滑块/验证{suffix}，请自行提供 Cookie 或扫码重新登录。"


async def _load_ws_credentials(db: AsyncSession, account_id: int):
    """从数据库读取账号最新 Cookie/Token，用于手动重连和发送前自愈。"""
    await db.execute(
        text("""
            UPDATE xianyu_account_auth auth
            JOIN xianyu_account a
              ON a.id = auth.account_id
            SET auth.deleted = 0,
                auth.updated_time = NOW()
            WHERE a.id = :account_id
              AND a.deleted = 0
              AND COALESCE(auth.deleted, 0) = 1
        """),
        {"account_id": account_id},
    )
    result = await db.execute(
        text("""
            SELECT a.external_uid AS unb,
                   auth.encrypted_cookie AS encrypted_cookie,
                   auth.encrypted_token AS encrypted_token,
                   COALESCE(auth.cookie_status, 0) AS cookie_status,
                   auth.last_login_status_code AS login_status_code
            FROM xianyu_account a
            JOIN xianyu_account_auth auth ON auth.account_id = a.id
            WHERE a.id = :account_id
              AND a.deleted = 0
              AND COALESCE(auth.deleted, 0) = 0
            LIMIT 1
        """),
        {"account_id": account_id},
    )
    row = result.mappings().first()
    if not row:
        return None, "账号未找到或未保存登录凭证"

    cookie_str = decrypt_cookie_if_needed(row.get("encrypted_cookie") or "")
    m_h5_tk = (decrypt_cookie_if_needed(row.get("encrypted_token") or "") or "").strip()
    if not m_h5_tk:
        m_h5_tk = _xianyu_token_from_cookie(cookie_str) or ""
    if not cookie_str:
        return None, "账号缺少 Cookie，请自行提供 Cookie 或扫码重新登录"
    if not m_h5_tk:
        return None, "Cookie 中缺少 _m_h5_tk，请自行提供 Cookie 或扫码重新登录"
    if int(row.get("cookie_status") or 0) != 1 or str(row.get("login_status_code") or "").upper() != "OK":
        return None, "账号 Cookie 尚未通过统一登录校验，请先在账号管理或连接管理页执行校验"
    return {
        "cookie_str": cookie_str,
        "m_h5_tk": m_h5_tk,
        "unb": row.get("unb") or "",
        "cookie_status": int(row.get("cookie_status") or 0),
        "login_status_code": row.get("login_status_code"),
    }, None


async def _restart_ws_client_from_db(db: AsyncSession, account_id: int):
    creds, error = await _load_ws_credentials(db, account_id)
    if error:
        logger.warning(
            "WS 连接凭据加载失败 accountId=%d error=%s",
            account_id,
            str(error),
        )
        # Cookie 尚未通过统一登录校验：自动调用 check_cookie_login 完成校验后重试。
        # 这样用户手动更新 Cookie 后点击"连接"即可自动校验登录，无需手动执行登录校验。
        # 对标商业版 refreshAccountAuthBeforeConnect 的预校验流程。
        if "尚未通过统一登录校验" in str(error):
            try:
                from ....services.cookie_token_refresher import check_cookie_login
                # 提交 _load_ws_credentials 中 UPDATE 产生的未决事务，
                # 避免其行锁/间隙锁阻塞 check_cookie_login 内部新建的独立 session
                # 读取 xianyu_account_auth 表（REPEATABLE READ 下曾导致 30s 死等）。
                try:
                    await db.commit()
                except Exception:
                    await db.rollback()
                logger.warning(
                    "WS 连接前自动执行登录校验 accountId=%d (cookie_status/login_status 未确认)",
                    account_id,
                )
                check = await check_cookie_login(account_id)
                logger.warning(
                    "WS 连接前登录校验完成 accountId=%d confirmed=%s authenticated=%s code=%s",
                    account_id,
                    check.confirmed,
                    check.authenticated,
                    check.code,
                )
                if check.confirmed and check.authenticated:
                    # 校验通过，cookie_status 已被 check_cookie_login 更新为 1，
                    # 重新加载凭据并继续连接
                    creds, error = await _load_ws_credentials(db, account_id)
                    # 广播 SSE cookie_status_changed 事件，让前端实时更新 Cookie 状态
                    # （check_cookie_login 内部 _update_cookie_status 只写 DB，不广播 SSE）
                    try:
                        await broadcaster.broadcast("cookie_status_changed", {
                            "accountId": account_id,
                            "cookieStatus": 1,
                        })
                    except Exception:
                        pass
                elif check.confirmed and not check.authenticated:
                    # 平台明确拒绝（滑块/会话过期）：返回平台给出的具体原因
                    return None, check.message or "平台登录校验未通过，请更新 Cookie 或重新扫码登录"
                else:
                    # 校验流程本身不可用（超时/上游不可达）：返回具体原因
                    return None, check.message or "平台登录校验暂不可用，请稍后重试"
            except Exception as exc:
                logger.error(
                    "WS 连接前自动登录校验异常 accountId=%d errorType=%s",
                    account_id,
                    type(exc).__name__,
                    exc_info=True,
                )
                return None, "自动登录校验服务暂时不可用，请稍后重试"
        if error:
            return None, error
    try:
        # 不清除 Token 缓存：缓存验证逻辑会自动检测 cookie_str/m_h5_tk 是否变更。
        # 用户点击"连接"不等于 Cookie 变了——只有重新扫码才会变。
        # 保留缓存可让 99% 的连接命中缓存，跳过 mtop API 调用，实现 3 秒内连接
        # （对标商业版 xy_token_cache 的 5-10 小时 TTL 设计）。
        # 仅在以下场景清除缓存：Token 过期(ws_client._refresh_token)、
        # 滑块验证(ws_client._refresh_token)、Cookie 真正变更(缓存自动 miss)。
        await ws_manager.start_client(
            account_id=account_id,
            cookie_str=creds["cookie_str"],
            m_h5_tk=creds["m_h5_tk"],
            unb=creds["unb"],
        )
        return ws_manager.get_client(account_id), None
    except Exception as exc:
        logger.error(
            "WebSocket client restart failed accountId=%s errorType=%s",
            account_id,
            type(exc).__name__,
        )
        return None, "WebSocket 连接服务暂不可用，请稍后重试"


def _ws_restart_http_error(error: str) -> HTTPException:
    """Map restart failures to stable HTTP states without leaking diagnostics."""
    normalized = str(error or "")
    if "未找到" in normalized:
        return HTTPException(status_code=404, detail="账号不存在或尚未配置登录凭据。")
    if "缺少" in normalized:
        return HTTPException(status_code=422, detail="账号登录凭据不完整，请重新登录。")
    if "尚未通过统一登录校验" in normalized:
        return HTTPException(status_code=409, detail="账号登录状态尚未确认，请先执行登录校验。")
    # 自动登录校验返回的平台拒绝原因（滑块/会话过期/校验未通过等）：返回 409，附带具体原因
    if any(kw in normalized for kw in (
        "平台登录校验未通过",
        "平台要求完成安全验证",
        "Cookie 会话已过期",
        "请更新 Cookie 或重新扫码登录",
    )):
        return HTTPException(status_code=409, detail=normalized)
    # 自动登录校验服务自身不可用（超时/上游不可达/异常）：返回 503
    if any(kw in normalized for kw in (
        "平台登录校验暂不可用",
        "自动登录校验服务暂时不可用",
    )):
        return HTTPException(status_code=503, detail=normalized)
    return HTTPException(status_code=503, detail="WebSocket 连接服务暂不可用，请稍后重试。")


async def _wait_ws_connect_result(account_id: int, timeout_seconds: float = 12.0):
    """等待 WS 连接结果，12 秒超时（对标商业版 12 秒内完成连接判定）。

    - Token 缓存命中 + WS 连接 + 注册 ≈ 2-3 秒
    - Token 缓存未命中 + mtop API 获取 Token + WS 连接 ≈ 5-10 秒
    - Token 获取失败 → 1-2 秒内返回 auth_failed
    - 12 秒内未连上且无验证失败 → 返回 pending（前端显示"已提交，未检测到验证"）
    """
    deadline = asyncio.get_event_loop().time() + timeout_seconds
    last_status = ws_manager.get_status(account_id)
    while asyncio.get_event_loop().time() < deadline:
        client = ws_manager.get_client(account_id)
        last_status = ws_manager.get_status(account_id)
        if client and getattr(client, "is_connected", False):
            return "connected", last_status
        if _is_ws_auth_failure(last_status):
            return "auth_failed", last_status
        await asyncio.sleep(0.2)
    return "pending", last_status


async def _resolve_ws_sid(db: AsyncSession, account_id: int, raw_cid: object) -> str:
    cid = _normalize_safe_goofish_id(raw_cid)
    if not cid:
        return ""

    logger.debug("开始解析会话标识 accountId=%d", account_id)

    direct_result = await db.execute(
        text("""
            SELECT s_id FROM xianyu_chat_message
            WHERE account_id = :account_id
              AND deleted = 0
              AND s_id COLLATE utf8mb4_unicode_ci = :cid COLLATE utf8mb4_unicode_ci
            ORDER BY message_time DESC LIMIT 1
        """),
        {"account_id": account_id, "cid": cid}
    )
    direct_row = direct_result.mappings().first()
    if direct_row and direct_row.get("s_id"):
        result = str(direct_row["s_id"])
        logger.debug("会话标识直接匹配成功")
        return result

    logger.debug("会话标识直接匹配未命中，继续关联查询")

    lookup_result = await db.execute(
        text("""
            SELECT s_id FROM xianyu_chat_message
            WHERE account_id = :account_id
              AND deleted = 0
              AND (
                  sender_user_id COLLATE utf8mb4_unicode_ci = :cid COLLATE utf8mb4_unicode_ci
                  OR receiver_user_id COLLATE utf8mb4_unicode_ci = :cid COLLATE utf8mb4_unicode_ci
                  OR peer_external_uid COLLATE utf8mb4_unicode_ci = :cid COLLATE utf8mb4_unicode_ci
              )
            ORDER BY message_time DESC LIMIT 1
        """),
        {"account_id": account_id, "cid": cid}
    )
    lookup_row = lookup_result.mappings().first()
    if lookup_row and lookup_row.get("s_id"):
        result = str(lookup_row["s_id"])
        logger.debug("会话标识关联查询成功")
        return result

    logger.debug("会话标识查询未命中，使用经过校验的原始标识")
    return cid


async def _resolve_ws_peer_id(
    db: AsyncSession, account_id: int,
    ws_sid: str,
    raw_to_id: object,
    own_id: str,
) -> str:
    bare_sid = _normalize_safe_goofish_id(ws_sid)
    direct_to_id = _normalize_safe_goofish_id(raw_to_id)
    # 如果传入的 to_id 是有效的真实用户 ID（不是 sid 本身），直接使用
    if direct_to_id and direct_to_id != bare_sid:
        return direct_to_id
    if not bare_sid:
        return ""

    conv_result = await db.execute(
        text("""
            SELECT c.external_buyer_id, c.peer_external_uid, c.peer_key
            FROM xianyu_conversation c
            WHERE c.account_id = :account_id
              AND (
                  c.peer_key COLLATE utf8mb4_unicode_ci = CONCAT('sid:', :sid) COLLATE utf8mb4_unicode_ci
                  OR c.external_buyer_id COLLATE utf8mb4_unicode_ci = CONCAT('sid:', :sid) COLLATE utf8mb4_unicode_ci
                  OR EXISTS (
                      SELECT 1 FROM xianyu_chat_message xm
                      
                        AND xm.account_id = c.account_id
                        AND xm.s_id COLLATE utf8mb4_unicode_ci = :sid COLLATE utf8mb4_unicode_ci
                  )
              )
            ORDER BY c.id DESC LIMIT 1
        """),
        {"account_id": account_id, "sid": bare_sid}
    )
    conv_row = conv_result.mappings().first()
    if conv_row:
        for key in ("peer_external_uid", "external_buyer_id", "peer_key"):
            candidate = _normalize_safe_goofish_id(conv_row.get(key))
            if candidate and candidate != own_id:
                return candidate

    msg_result = await db.execute(
        text("""
            SELECT sender_user_id, receiver_user_id, peer_external_uid
            FROM xianyu_chat_message
            WHERE account_id = :account_id
              AND s_id COLLATE utf8mb4_unicode_ci = :sid COLLATE utf8mb4_unicode_ci
              AND deleted = 0
            ORDER BY message_time DESC LIMIT 20
        """),
        {"account_id": account_id, "sid": bare_sid}
    )
    for row in msg_result.mappings().all():
        for key in ("peer_external_uid", "sender_user_id", "receiver_user_id"):
            candidate = _normalize_safe_goofish_id(row.get(key))
            if candidate and candidate != own_id:
                return candidate

    return ""


async def _resolve_ws_goods_id(
    db: AsyncSession,
    account_id: int,
    ws_sid: str,
    raw_goods_id: object,
) -> str:
    direct_goods_id = str(raw_goods_id or "").strip()
    if direct_goods_id:
        return direct_goods_id
    bare_sid = _normalize_safe_goofish_id(ws_sid)
    if not bare_sid:
        return ""

    msg_result = await db.execute(
        text("""
            SELECT xy_goods_id
            FROM xianyu_chat_message
            WHERE account_id = :account_id
              AND s_id COLLATE utf8mb4_unicode_ci IN (:sid, :sid_goofish)
              AND deleted = 0
              AND xy_goods_id IS NOT NULL
              AND xy_goods_id != ''
            ORDER BY message_time DESC, id DESC
            LIMIT 1
        """),
        {
            "account_id": account_id,
            "sid": bare_sid,
            "sid_goofish": f"{bare_sid}@goofish",
        }
    )
    msg_row = msg_result.mappings().first()
    if msg_row and msg_row.get("xy_goods_id"):
        return str(msg_row.get("xy_goods_id") or "").strip()

    conv_result = await db.execute(
        text("""
            SELECT goods_id
            FROM xianyu_conversation
            WHERE account_id = :account_id
              AND (
                  peer_key COLLATE utf8mb4_unicode_ci IN (:sid_key, :sid_key_goofish)
                  OR external_buyer_id COLLATE utf8mb4_unicode_ci IN (:sid_key, :sid_key_goofish)
              )
              AND goods_id IS NOT NULL
              AND goods_id != ''
            ORDER BY id DESC
            LIMIT 1
        """),
        {
            "account_id": account_id,
            "sid_key": f"sid:{bare_sid}",
            "sid_key_goofish": f"sid:{bare_sid}@goofish",
        }
    )
    conv_row = conv_result.mappings().first()
    if conv_row and conv_row.get("goods_id"):
        return str(conv_row.get("goods_id") or "").strip()

    return ""


def _validate_safe_https_image_url(image_url: str) -> str:
    """Return empty string when safe, otherwise a user-facing validation message."""
    if not image_url or len(image_url) > 500:
        return "图片链接不能为空且不能超过500个字符"
    parsed = urlparse(image_url)
    if parsed.scheme != "https" or not parsed.netloc:
        return "图片链接仅支持 HTTPS 地址"
    host = (parsed.hostname or "").lower()
    if host in {"localhost"} or host.endswith(".localhost"):
        return "不允许发送本机或内网图片地址"
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
            return "不允许发送本机或内网图片地址"
    except ValueError:
        pass
    return ""


def _read_uploaded_image_bytes(image_url: str) -> bytes:
    if not image_url.startswith("/uploads/"):
        raise ValueError("仅支持发送本地上传目录中的图片")
    safe_path = os.path.normpath(image_url.lstrip("/"))
    local_path = os.path.join(_IMAGE_UPLOAD_BASE_DIR, os.path.basename(safe_path))
    if not os.path.exists(local_path):
        local_path = os.path.normpath(
            os.path.join(os.path.dirname(_IMAGE_UPLOAD_BASE_DIR), safe_path)
        )
    if not os.path.exists(local_path) or not os.path.isfile(local_path):
        raise ValueError("未找到本地上传图片，请重新上传后再发送")
    with open(local_path, "rb") as file_obj:
        data = file_obj.read()
    if not data:
        raise ValueError("本地上传图片内容为空，请重新上传后再发送")
    return data


async def _resolve_outbound_image_url(
    db: AsyncSession, account_id: int,
    image_url: str,
) -> str:
    normalized = str(image_url or "").strip()
    if not normalized:
        raise ValueError("图片链接不能为空")
    if normalized.startswith("/uploads/"):
        creds, error = await _load_ws_credentials(db, account_id)
        if error:
            raise ValueError(error)
        image_data = await asyncio.to_thread(_read_uploaded_image_bytes, normalized)
        cdn_url, _, _ = await asyncio.to_thread(_upload_image_to_xianyu, creds["cookie_str"], image_data)
        url_error = _validate_safe_https_image_url(cdn_url)
        if url_error:
            raise ValueError(url_error)
        return cdn_url
    url_error = _validate_safe_https_image_url(normalized)
    if url_error:
        raise ValueError(url_error)
    return normalized

async def _save_scan_login_result(session_id: str, db: AsyncSession) -> dict:
    """保存扫码登录成功后获取到的 Cookie 到数据库。

    从扫码登录会话中提取 Cookie 数据，创建或更新 XianyuAccount 和 XianyuAccountAuth 记录。
    返回: {"account_id": int, "cookie_status": int, "expire_time": str, ...} 或 {"_error": str, "message": str}
    """
    try:
        session_data = await asyncio.to_thread(get_session_cookies, session_id)
        if not session_data:
            return {"_error": "SESSION_MISSING", "message": "会话不存在或尚未登录成功"}

        cookie_text = session_data.get("cookie_text", "")
        unb = session_data.get("unb", "")
        m_h5_tk = session_data.get("m_h5_tk", "")
        user_id = session_data.get("user_id")
        if not unb:
            return {"_error": "NO_UNB", "message": "Cookie 中未提取到 unb"}
        # 检查账号是否已存在（包含软删除的记录）
        # 先查所有记录（含 deleted=1），避免因唯一约束导致插入失败
        result = await db.execute(
            select(XianyuAccount).where(
                XianyuAccount.external_uid == unb,
            ).order_by(XianyuAccount.deleted.asc()).limit(1)
        )
        existing_account = result.scalar_one_or_none()

        if existing_account:
            account = existing_account
            if existing_account.deleted == 1:
                # 恢复软删除的账号
                existing_account.deleted = 0
                existing_account.status = 1
                logger.info(
                    "QR account persistence accountId=%d state=restored",
                    account.id,
                )
            else:
                logger.info(
                    "QR account persistence accountId=%d state=existing",
                    account.id,
                )
        else:
            account = XianyuAccount(
                platform="xianyu",
                external_uid=unb,
                status=1,
            )
            db.add(account)
            await db.commit()
            await db.refresh(account)
            logger.info(
                "QR account persistence accountId=%d state=created",
                account.id,
            )

        # 加密并保存 Cookie
        encrypted_cookie = encrypt_cookie_for_storage(cookie_text)
        encrypted_token = encrypt_cookie_for_storage(m_h5_tk) if m_h5_tk else None

        # 检查 auth 记录是否已存在
        auth_result = await db.execute(
            select(XianyuAccountAuth).where(
                XianyuAccountAuth.account_id == account.id,
            )
        )
        existing_auth = auth_result.scalar_one_or_none()

        if existing_auth:
            existing_auth.deleted = 0
            existing_auth.encrypted_cookie = encrypted_cookie
            if encrypted_token:
                existing_auth.encrypted_token = encrypted_token
            existing_auth.cookie_status = 1
            existing_auth.last_login_status_code = "OK"
            existing_auth.last_login_status_message = "账号登录状态正常"
            existing_auth.last_login_check_time = func.now()
        else:
            auth = XianyuAccountAuth(
                account_id=account.id,
                encrypted_cookie=encrypted_cookie,
                encrypted_token=encrypted_token,
                cookie_status=1,
                last_login_status_code="OK",
                last_login_status_message="账号登录状态正常",
                last_login_check_time=func.now(),
            )
            db.add(auth)

        runtime_result = await db.execute(
            select(XianyuAccountRuntime).where(
                XianyuAccountRuntime.account_id == account.id,
            )
        )
        existing_runtime = runtime_result.scalar_one_or_none()
        if existing_runtime:
            existing_runtime.deleted = 0
            existing_runtime.cookie_status = 1
            existing_runtime.last_login_status_code = "OK"
            existing_runtime.last_login_status_message = "账号登录状态正常"
            existing_runtime.last_login_check_time = func.now()
        else:
            db.add(XianyuAccountRuntime(
                account_id=account.id,
                cookie_status=1,
                last_login_status_code="OK",
                last_login_status_message="账号登录状态正常",
                last_login_check_time=func.now(),
            ))

        await db.commit()
        # A confirmed QR session is a verified credential recovery. Resolve
        # the durable expiry-alert generation only after both credential
        # tables have committed successfully.
        from ....services.notify_dispatcher import clear_cookie_expired_state

        await clear_cookie_expired_state(int(account.id))
        return {
            "account_id": account.id,
            "cookie_status": 1,
            "expire_time": None,
        }
    except Exception as exc:
        logger.error(
            "Persisting QR login result failed errorType=%s",
            type(exc).__name__,
        )
        return {
            "_error": "SAVE_FAILED",
            "message": "账号凭据保存服务暂不可用，请稍后重试。",
        }


media_router = APIRouter(prefix="/media")
image_router = APIRouter(prefix="/image")
captcha_router = APIRouter(prefix="/captcha")
address_dict_router = APIRouter(prefix="/address-dict")
backup_router = APIRouter(prefix="/backup")
excel_router = APIRouter(prefix="/excel")
goods_sku_router = APIRouter(prefix="/goods-sku")
data_panel_router = APIRouter(prefix="/data-panel")
navigation_router = APIRouter(prefix="/navigation")
qrlogin_router = APIRouter(prefix="/qrlogin")
_qr_status_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = (
    weakref.WeakValueDictionary()
)


# ---- QR Login (用户端路由, 前端直接调用) ----
def _qr_owner_key(current_user: dict) -> str:
    owner = str(current_user.get("username") or "").strip().casefold()
    if not owner:
        raise HTTPException(status_code=403, detail="无法确认扫码登录会话所有者。")
    return owner


def _qr_status_lock(session_id: str) -> asyncio.Lock:
    """Return the event-loop lock serialising persistence for one QR session."""

    lock = _qr_status_locks.get(session_id)
    if lock is None:
        lock = asyncio.Lock()
        _qr_status_locks[session_id] = lock
    return lock


async def _require_qr_session_owner(
    session_id: str,
    current_user: dict,
) -> dict | None:
    from ....core.xianyu_qr_login import get_session_context

    context = await asyncio.to_thread(get_session_context, session_id)
    if context is None:
        return None
    if str(context.get("owner_key") or "").strip().casefold() != _qr_owner_key(current_user):
        # The session identifier is a bearer secret. Do not reveal whether a
        # supplied identifier belongs to another authenticated principal.
        raise HTTPException(status_code=404, detail="扫码登录会话不存在或已过期。")
    return context


@qrlogin_router.post("/generate")
async def qrlogin_generate(
    current_user: dict = Depends(get_current_user)
):
    try:
        user_id = current_user.get("user_id")
        result = await asyncio.to_thread(
            generate_qrcode,
            user_id=user_id,
            owner_key=_qr_owner_key(current_user),
        )
        if "qrImage" in result and "qrCodeBase64" not in result:
            result["qrCodeBase64"] = result["qrImage"]
        return ResultObject.success(result)
    except QrSessionCapacityError as exc:
        raise HTTPException(
            status_code=503,
            detail="当前扫码登录人数较多，请稍后重试。",
        ) from exc
    except Exception as exc:
        logger.error(
            "QR code generation failed errorType=%s",
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=502,
            detail="扫码登录服务暂不可用，请稍后重试。",
        ) from exc


@qrlogin_router.post("/status/{session_id}")
async def qrlogin_status(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    try:
        ctx = await _require_qr_session_owner(session_id, current_user)
        if ctx is None:
            return ResultObject.success({"status": "expired", "message": "会话不存在或已过期"})

        async with _qr_status_lock(session_id):
            # The session may have been replaced while this request waited.
            ctx = await _require_qr_session_owner(session_id, current_user)
            if ctx is None:
                return ResultObject.success(
                    {"status": "expired", "message": "会话不存在或已过期"}
                )

            result = await asyncio.to_thread(get_session_status, session_id)
            logger.info(
                "QR status poll sessionId=%s status=%s persisted=%s accountId=%s",
                session_id[:12],
                result.get("status"),
                result.get("persisted"),
                result.get("accountId"),
            )
            # A successful persistence receipt is replayed directly. Keeping
            # the confirmed session until TTL makes lost-response retries
            # idempotent without ever returning raw cookies to the browser.
            if result.get("status") == "confirmed" and not result.get("persisted"):
                save_result = await _save_scan_login_result(session_id, db)
                logger.info(
                    "QR save_result sessionId=%s error=%s account_id=%s",
                    session_id[:12],
                    save_result.get("_error") if save_result else "no_result",
                    save_result.get("account_id") if save_result else None,
                )
                if not save_result or save_result.get("_error"):
                    raise HTTPException(
                        status_code=503,
                        detail="扫码已确认，但账号凭据尚未安全保存；请稍后重试，不要将本次登录视为已完成。",
                    )

                persistence_result = {
                    "accountId": save_result.get("account_id"),
                    "cookieStatus": save_result.get("cookie_status"),
                    "expireTime": save_result.get("expire_time"),
                    "message": "扫码登录成功，账号已保存",
                    "persisted": True,
                }
                result.update(persistence_result)
                await asyncio.to_thread(
                    mark_session_persisted,
                    session_id,
                    persistence_result,
                )
            logger.info(
                "QR status response sessionId=%s final_status=%s final_accountId=%s persisted=%s",
                session_id[:12],
                result.get("status"),
                result.get("accountId"),
                result.get("persisted"),
            )
            return ResultObject.success(result)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "查询二维码状态失败 errorType=%s",
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=502,
            detail="扫码登录状态暂时无法确认，请稍后重试。",
        ) from exc


@qrlogin_router.get("/cookies/{session_id}")
@qrlogin_router.post("/cookies/{session_id}")
async def qrlogin_cookies(
    session_id: str,
    current_user: dict = Depends(get_current_user)
):
    try:
        from ....core.xianyu_qr_login import get_session_cookies as _get_cookies
        if await _require_qr_session_owner(session_id, current_user) is None:
            raise HTTPException(
                status_code=404,
                detail="会话不存在、已过期或尚未登录成功。",
            )
        cookies = await asyncio.to_thread(_get_cookies, session_id)
        if cookies is None:
            raise HTTPException(
                status_code=404,
                detail="会话不存在、已过期或尚未登录成功。",
            )
        # The browser only needs to know whether server-side credentials are
        # available. The actual cookie remains server-side and is encrypted
        # before persistence.
        return ResultObject.success({"available": True})
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "获取二维码Cookie失败 errorType=%s",
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=503,
            detail="扫码登录凭据状态暂不可用，请稍后重试。",
        ) from exc


@qrlogin_router.post("/cleanup")
async def qrlogin_cleanup(
    current_user: dict = Depends(get_current_user)
):
    try:
        removed = await asyncio.to_thread(
            cleanup_owner_sessions,
            _qr_owner_key(current_user),
        )
        return ResultObject.success({"status": "ok", "removed": removed})
    except Exception as exc:
        logger.error(
            "清理二维码会话失败 errorType=%s",
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=503,
            detail="扫码登录会话清理服务暂不可用，请稍后重试。",
        ) from exc


notification_router = APIRouter(prefix="/notification")
websocket_router = APIRouter(prefix="/websocket")
operation_log_router = APIRouter(prefix="/operationLog")


# ---- 扩展 router：前端调用但原 system.py / misc.py 未覆盖的端点 ----
# captcha_ext_router：验证码调试图片 URL
captcha_ext_router = APIRouter(prefix="/captcha", tags=["captcha-ext"])
# business_opportunity_router：商机/闲鱼搜索（参考项目由 Java 代理到 Python 实现）
business_opportunity_router = APIRouter(prefix="/business-opportunity", tags=["business-opportunity"])
# crawler_router：爬虫任务（参考项目由 Java 代理到 Node.js crawler-service）
crawler_router = APIRouter(prefix="/crawler", tags=["crawler"])
# goofish_router：闲鱼商品搜索（前端 goofish.js 调用 /api/goofish/search）
goofish_router = APIRouter(prefix="/goofish", tags=["goofish"])


def get_manual_message_runtime(
    db: AsyncSession = Depends(get_db),
) -> ManualMessageRuntime:
    return ManualMessageRuntime(SqlManualMessageAttemptStore(db))


async def _safe_manual_message_rollback(db: AsyncSession) -> None:
    try:
        await db.rollback()
    except Exception as exc:
        logger.error(
            "Manual message rollback failed errorType=%s",
            type(exc).__name__,
        )


def _manual_message_digest(
    *,
    account_id: int,
    cid: object,
    to_id: object,
    message_type: str,
    payload: str,
    goods_id: object,
) -> str:
    canonical = "\x1f".join((
        "manual-message:v1",
        str(account_id),
        _normalize_safe_goofish_id(cid),
        _normalize_safe_goofish_id(to_id),
        str(message_type),
        str(goods_id or "").strip(),
        payload,
    ))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def _prepare_manual_message(
    db: AsyncSession,
    *,
    account_id: int,
    cid: object,
    to_id: object,
    goods_id: object,
) -> dict:
    client = ws_manager.get_client(account_id)
    if not client or not getattr(client, "is_connected", False):
        client, restart_error = await _restart_ws_client_from_db(db, account_id)
        if restart_error:
            raise ManualMessagePreflightError("websocket_credentials_unavailable")
        connect_outcome, status = await _wait_ws_connect_result(
            account_id,
            timeout_seconds=8.0,
        )
        if connect_outcome == "auth_failed":
            raise ManualMessagePreflightError("websocket_auth_failed")
        if connect_outcome != "connected":
            raise ManualMessagePreflightError("websocket_unavailable")
        client = ws_manager.get_client(account_id)
    if not client:
        raise ManualMessagePreflightError("websocket_unavailable")

    ws_sid = await _resolve_ws_sid(db, account_id, cid)
    if not ws_sid:
        raise ManualMessagePreflightError("conversation_context_missing")
    own_id = _normalize_safe_goofish_id(client.unb or "")
    resolved_to_id = await _resolve_ws_peer_id(
        db,
        account_id,
        ws_sid,
        to_id,
        own_id,
    )
    if not resolved_to_id:
        raise ManualMessagePreflightError("conversation_peer_missing")
    resolved_goods_id = await _resolve_ws_goods_id(
        db,
        account_id,
        ws_sid,
        goods_id,
    )
    return {
        "client": client,
        "ws_sid": ws_sid,
        "ws_cid": _to_goofish_id(ws_sid),
        "ws_to_id": _to_goofish_id(resolved_to_id),
        "goods_id": resolved_goods_id,
        "sender_id": _to_goofish_id(client.unb or ""),
        "seller_external_uid": client.unb or "",
    }


def _manual_message_send_result(result: object) -> ManualMessageSendResult:
    payload = result if isinstance(result, dict) else {}
    if int(payload.get("code") or 0) == 200:
        return ManualMessageSendResult.confirmed(payload.get("uuid"))
    if payload.get("deliveryUnknown") or payload.get("retrySafe") is False:
        return ManualMessageSendResult.unknown("message_ack_unknown")
    if payload.get("mid"):
        code = (
            "conversation_missing"
            if payload.get("errorKind") == "conversation_missing"
            else "message_rejected"
        )
        return ManualMessageSendResult.failed(code, retry_safe=True)
    if int(payload.get("code") or 0) in {422, 503}:
        return ManualMessageSendResult.failed(
            "websocket_unavailable",
            retry_safe=True,
        )
    # A transport exception may have happened after the frame was handed to
    # the socket. Without an explicit platform NACK, delivery is ambiguous.
    return ManualMessageSendResult.unknown("message_result_unknown")


@websocket_router.post("/sendMessage")
async def websocket_send_message(
    data: dict = {},
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    runtime: ManualMessageRuntime = Depends(get_manual_message_runtime),
):
    del current_user
    try:
        raw_account_id = data.get("xianyuAccountId") or data.get("accountId")
        account_id = _parse_account_id(raw_account_id)
        cid = data.get("cid") or data.get("conversationId") or data.get("sessionId") or data.get("sId") or data.get("sid")
        to_id = data.get("toId") or data.get("peerUserId") or data.get("peer_user_id")
        message_value = data.get("text") or data.get("message") or data.get("content")
        idempotency_key = str(data.get("idempotencyKey") or "").strip()
        if not account_id or not cid or not isinstance(message_value, str) or not message_value.strip():
            raise HTTPException(status_code=422, detail="accountId、cid 和 text 不能为空。")
        if len(message_value) > 1000:
            raise HTTPException(status_code=422, detail="消息不能超过 1000 字。")
        if not idempotency_key:
            raise HTTPException(status_code=422, detail="idempotencyKey 不能为空。")

        command = ManualMessageCommand(
            idempotency_key=idempotency_key,
            account_id=account_id,
            session_id=str(cid),
            peer_id=str(to_id or f"session:{cid}"),
            message_type="text",
            payload_digest=_manual_message_digest(
                account_id=account_id,
                cid=cid,
                to_id=to_id,
                message_type="text",
                payload=message_value,
                goods_id=data.get("xyGoodsId"),
            ),
        )
        event_holder: dict[str, object] = {}

        async def prepare() -> dict:
            return await _prepare_manual_message(
                db,
                account_id=account_id,
                cid=cid,
                to_id=to_id,
                goods_id=data.get("xyGoodsId"),
            )

        async def send(prepared: dict) -> ManualMessageSendResult:
            result = await prepared["client"].send_text_message(
                prepared["ws_cid"],
                prepared["ws_to_id"],
                message_value,
                persist=False,
            )
            return _manual_message_send_result(result)

        async def persist(prepared: dict, platform_message_id: str | None) -> int | None:
            out_message_time = int(datetime.datetime.now().timestamp() * 1000)
            event = {
                "accountId": account_id,
                "sId": prepared["ws_sid"],
                "sid": prepared["ws_sid"],
                "pnmId": "",
                "senderUserId": prepared["sender_id"],
                "senderUserName": "我",
                "receiverUserId": prepared["ws_to_id"],
                "peerUserId": prepared["ws_to_id"],
                "peerUserName": "",
                "peerNick": "",
                "msgContent": message_value,
                "message": message_value,
                "content": message_value,
                "contentType": 1,
                "messageTime": out_message_time,
                "direction": "OUT",
                "reminderContent": "",
                "reminderUrl": "",
                "xyGoodsId": prepared["goods_id"],
                "readStatus": 1,
            }
            local_message_id = await save_chat_message(
                db,
                account_id,
                event,
                seller_external_uid=prepared["seller_external_uid"],
            )

            # 人工发送消息后触发会话级自动回复暂停（人工干预）：
            # - auto_reply_paused=1：暂停 AI 自动回复
            # - last_manual_reply_at：记录人工回复时间戳，用于 1 分钟自动恢复判断
            # - auto_reply_manual_disabled：保持原值（用户已手动关闭则依然只能手动开启）
            try:
                normalized_sid = (
                    str(prepared["ws_sid"] or "")
                    .strip()
                    .removeprefix("sid:")
                    .removesuffix("@goofish")
                )
                if normalized_sid:
                    conv_pause_row = (await db.execute(text("""
                        SELECT id, auto_reply_manual_disabled
                        FROM xianyu_conversation
                        WHERE account_id = :account_id
                          AND (
                            REPLACE(REPLACE(COALESCE(peer_key, ''), 'sid:', ''), '@goofish', '') = :s_id
                            OR REPLACE(REPLACE(COALESCE(external_buyer_id, ''), 'sid:', ''), '@goofish', '') = :s_id
                          )
                          AND deleted = 0
                        ORDER BY last_message_time DESC, id DESC
                        LIMIT 1
                    """), {
                        "account_id": account_id,
                        "s_id": normalized_sid,
                    })).mappings().first()
                    if conv_pause_row:
                        paused_conv_id = int(conv_pause_row["id"])
                        await db.execute(text("""
                            UPDATE xianyu_conversation
                            SET auto_reply_paused = 1,
                                last_manual_reply_at = :last_manual_at,
                                updated_time = NOW()
                            WHERE id = :conversation_id
                        """), {
                            "conversation_id": paused_conv_id,
                            "last_manual_at": out_message_time,
                        })
                        logger.info(
                            "人工发送消息触发会话级暂停 accountId=%d convId=%d",
                            account_id, paused_conv_id,
                        )
                        # 广播状态变更通知前端实时更新
                        try:
                            await broadcaster.broadcast("conversation_auto_reply_state", {
                                "conversationId": paused_conv_id,
                                "accountId": account_id,
                                "peerId": prepared["ws_to_id"],
                                "sid": prepared["ws_sid"],
                                "autoReplyPaused": 1,
                                "autoReplyManualDisabled": int(conv_pause_row["auto_reply_manual_disabled"] or 0),
                                "lastManualReplyAt": out_message_time,
                                "reason": "manual_intervention",
                            })
                        except Exception:
                            logger.warning(
                                "广播会话暂停状态失败 accountId=%d convId=%d",
                                account_id, paused_conv_id,
                            )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "触发会话级暂停失败 accountId=%d errorType=%s",
                    account_id, type(exc).__name__,
                )

            # SSE 广播使用与 DB 持久化相同的 pnmId（由 save_chat_message 回写）。
            # save_chat_message 在 pnmId 为空时会生成兜底哈希并回写到 event，
            # 确保 SSE 事件与 DB 记录拥有同一身份，前端可正确去重。
            # platform_message_id 不再作为 pnmId 使用，否则会与 DB 记录的身份不一致。
            event_holder.update(event)
            return local_message_id

        outcome = await runtime.execute(command, prepare, send, persist)
        if outcome.status == "confirmed" and event_holder:
            try:
                await broadcaster.broadcast("message", event_holder)
            except Exception:
                logger.warning("SSE 广播发送消息失败 accountId=%d", account_id)
        return ResultObject.success(outcome.to_data())
    except HTTPException:
        raise
    except ValueError:
        await _safe_manual_message_rollback(db)
        raise HTTPException(status_code=422, detail="幂等键或消息参数无效。")
    except ManualMessageAttemptError as exc:
        await _safe_manual_message_rollback(db)
        if exc.error_code == "idempotency_payload_conflict":
            raise HTTPException(
                status_code=409,
                detail="幂等键已用于另一条消息；系统已拒绝发送，请刷新会话后核对。",
            )
        logger.error("Manual message attempt failed errorCode=%s", exc.error_code)
        raise HTTPException(
            status_code=503,
            detail="消息发送协调服务暂不可用，请稍后重试。",
        ) from exc
    except Exception as exc:
        await _safe_manual_message_rollback(db)
        logger.error("WS send error errorType=%s", type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail="消息发送服务暂不可用；本次未返回已送达结论，请刷新会话后核对。",
        ) from exc


@websocket_router.post("/sendImageMessage")
async def websocket_send_image_message(
    data: dict = {},
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
    runtime: ManualMessageRuntime = Depends(get_manual_message_runtime),
):
    del current_user
    try:
        raw_account_id = data.get("xianyuAccountId") or data.get("accountId")
        account_id = _parse_account_id(raw_account_id)
        cid = data.get("cid") or data.get("conversationId") or data.get("sessionId") or data.get("sId") or data.get("sid")
        to_id = data.get("toId") or data.get("peerUserId") or data.get("peer_user_id")
        image_url = str(data.get("imageUrl", "") or "").strip()
        idempotency_key = str(data.get("idempotencyKey") or "").strip()
        if not account_id or not cid or not image_url:
            raise HTTPException(status_code=422, detail="accountId、cid 和 imageUrl 不能为空。")
        if not idempotency_key:
            raise HTTPException(status_code=422, detail="idempotencyKey 不能为空。")
        command = ManualMessageCommand(
            idempotency_key=idempotency_key,
            account_id=account_id,
            session_id=str(cid),
            peer_id=str(to_id or f"session:{cid}"),
            message_type="image",
            payload_digest=_manual_message_digest(
                account_id=account_id,
                cid=cid,
                to_id=to_id,
                message_type="image",
                payload=image_url,
                goods_id=data.get("xyGoodsId"),
            ),
        )
        event_holder: dict[str, object] = {}

        async def prepare() -> dict:
            image_width, image_height = await asyncio.to_thread(
                _resolve_outbound_image_dimensions,
                image_url,
            )
            resolved_image_url = await _resolve_outbound_image_url(
                db,
                account_id,
                image_url,
            )
            prepared = await _prepare_manual_message(
                db,
                account_id=account_id,
                cid=cid,
                to_id=to_id,
                goods_id=data.get("xyGoodsId"),
            )
            prepared.update({
                "image_url": resolved_image_url,
                "image_width": image_width,
                "image_height": image_height,
            })
            return prepared

        async def send(prepared: dict) -> ManualMessageSendResult:
            result = await prepared["client"].send_image_message(
                prepared["ws_cid"],
                prepared["ws_to_id"],
                prepared["image_url"],
                width=prepared["image_width"],
                height=prepared["image_height"],
                persist=False,
            )
            return _manual_message_send_result(result)

        async def persist(prepared: dict, platform_message_id: str | None) -> int | None:
            out_image_time = int(datetime.datetime.now().timestamp() * 1000)
            event = {
                "accountId": account_id,
                "sId": prepared["ws_sid"],
                "sid": prepared["ws_sid"],
                "pnmId": "",
                "senderUserId": prepared["sender_id"],
                "senderUserName": "我",
                "receiverUserId": prepared["ws_to_id"],
                "peerUserId": prepared["ws_to_id"],
                "peerUserName": "",
                "peerNick": "",
                "msgContent": prepared["image_url"],
                "message": prepared["image_url"],
                "content": prepared["image_url"],
                "contentType": 2,
                "messageTime": out_image_time,
                "direction": "OUT",
                "reminderContent": "",
                "reminderUrl": "",
                "xyGoodsId": prepared["goods_id"],
                "readStatus": 1,
            }
            local_message_id = await save_chat_message(
                db,
                account_id,
                event,
                seller_external_uid=prepared["seller_external_uid"],
            )
            event_holder.update(event)
            return local_message_id

        outcome = await runtime.execute(command, prepare, send, persist)
        if outcome.status == "confirmed" and event_holder:
            try:
                await broadcaster.broadcast("message", event_holder)
            except Exception:
                logger.warning("SSE 广播发送图片消息失败 accountId=%d", account_id)
        return ResultObject.success(outcome.to_data())
    except HTTPException:
        raise
    except ValueError:
        await _safe_manual_message_rollback(db)
        raise HTTPException(status_code=422, detail="幂等键或图片参数无效。")
    except ManualMessageAttemptError as exc:
        await _safe_manual_message_rollback(db)
        if exc.error_code == "idempotency_payload_conflict":
            raise HTTPException(
                status_code=409,
                detail="幂等键已用于另一张图片；系统已拒绝发送，请刷新会话后核对。",
            )
        logger.error("Manual image attempt failed errorCode=%s", exc.error_code)
        raise HTTPException(
            status_code=503,
            detail="图片消息发送协调服务暂不可用，请稍后重试。",
        ) from exc
    except Exception as exc:
        await _safe_manual_message_rollback(db)
        logger.error("WS send image error errorType=%s", type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail="图片消息发送服务暂不可用；本次未返回已送达结论，请刷新会话后核对。",
        ) from exc


@websocket_router.post("/start")
async def websocket_start(
    data: dict = {},
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """启动 WebSocket 连接（对标商业版 12 秒判定 + optimistic 响应）。

    手动连接时强制从数据库重建客户端，避免旧的 token_failed/stopped 客户端导致前端一直卡在"正在连接"。
    - 12 秒内连上 → 返回 connected: true
    - 12 秒内检测到滑块/Token/Cookie 失败 → 同步求解滑块，返回 recovering 或失败
    - 12 秒内未连上且无验证失败 → 返回 optimistic: true（前端显示"已提交，未检测到验证"）
    """
    account_id = _parse_account_id(data.get("xianyuAccountId") or data.get("accountId"))
    if account_id is None or account_id <= 0:
        raise HTTPException(status_code=422, detail="accountId 必须为正整数。")
    current = ws_manager.get_client(account_id)
    if current and getattr(current, "is_connected", False):
        status = ws_manager.get_status(account_id)
        return ResultObject.success({
            "connected": True,
            "status": "already_connected",
            "hasSid": bool(status.get("hasSid")),
            "lastError": "",
        })

    try:
        client, error = await _restart_ws_client_from_db(db, account_id)
    except Exception as exc:
        logger.error(
            "WebSocket credential load failed accountId=%d errorType=%s",
            account_id,
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=503,
            detail="WebSocket 账号凭据存储暂不可用，请稍后重试。",
        ) from exc
    if error:
        raise _ws_restart_http_error(error)

    outcome, status = await _wait_ws_connect_result(account_id, timeout_seconds=12.0)
    if outcome == "connected":
        return ResultObject.success({
            "connected": True,
            "status": "connected",
            "hasSid": bool(status.get("hasSid")),
            "lastError": "",
        })
    if outcome == "auth_failed":
        # 检测到滑块/Token/Cookie 失败：将滑块求解转为后台任务，立即返回"恢复中"。
        #
        # 原实现同步阻塞 HTTP 响应等待滑块求解完成（crawler-service 最长 120s），
        # 加上 _wait_ws_connect_result 的 12s，总计可达 132s，远超前端 30s axios 超时，
        # 导致"请求超时，请稍后重试"。改为后台求解 + 立即响应，前端轮询状态确认结果。
        #
        # 去重：_refresh_token 中的 _auto_solve_captcha_after_failure 可能已触发求解
        # （10 分钟去重窗口），此处检查避免重复启动浏览器实例。
        from app.services.captcha_solver import handle_captcha_for_account
        from app.services.captcha_solver import _AUTO_SOLVE_LAST_TS

        last_solve_ts = _AUTO_SOLVE_LAST_TS.get(account_id, 0)
        solve_already_running = (time.time() - last_solve_ts) < 600

        if solve_already_running:
            logger.info(
                "滑块求解已在后台运行，跳过重复触发 accountId=%d",
                account_id,
            )
        else:
            async def _bg_captcha_recover():
                try:
                    captcha_result = await handle_captcha_for_account(
                        account_id=account_id,
                        response=None,
                        auto_solve=True,
                        trigger_scene="ws_connect",
                        open_reason="WS 连接 auth_failed 后台恢复自动触发滑块求解",
                        solve_reason="WS 连接失败后台恢复流程",
                    )
                    if captcha_result.get("recovered"):
                        logger.info(
                            "后台滑块求解成功，触发重连 accountId=%d",
                            account_id,
                        )
                        await ws_manager.restart_account(account_id)
                    else:
                        auto_solve_result = captcha_result.get("autoSolveResult") or {}
                        if auto_solve_result.get("solved") and not auto_solve_result.get("cookieVerified", True):
                            logger.warning(
                                "后台滑块求解通过但 Cookie Session 已过期 accountId=%d",
                                account_id,
                            )
                        else:
                            logger.warning(
                                "后台滑块求解未通过 accountId=%d",
                                account_id,
                            )
                except Exception as exc:
                    logger.error(
                        "后台滑块求解异常 accountId=%d errorType=%s",
                        account_id,
                        type(exc).__name__,
                        exc_info=True,
                    )

            spawn_background_task(
                _bg_captcha_recover(),
                name="misc.ws-captcha-recover",
            )

        return ResultObject.success({
            "connected": False,
            "status": "recovering",
            "hasSid": False,
            "lastError": status.get("lastError", ""),
            "message": "检测到登录失效，正在后台自动完成安全验证，请稍后刷新查看连接状态。",
        })

    # 12 秒内未连上但未检测到验证失败：返回乐观确认（对标商业版 optimistic 响应）。
    # 连接已在后台提交，前端据此显示"已提交，未检测到验证"，并延迟刷新状态确认。
    return ResultObject.success({
        "connected": True,
        "optimistic": True,
        "status": status.get("phase", "connecting"),
        "hasSid": bool(status.get("hasSid")),
        "lastError": status.get("lastError", ""),
        "message": "连接已提交，未检测到滑块/验证弹窗，系统将继续保持连接。",
    })


@websocket_router.post("/stop")
async def websocket_stop(
    data: dict = {},
    current_user: dict = Depends(get_current_user)
):
    """停止 WebSocket 连接。"""
    account_id = _parse_account_id(data.get("xianyuAccountId") or data.get("accountId"))
    if account_id is None or account_id <= 0:
        raise HTTPException(status_code=422, detail="accountId 必须为正整数。")
    client = ws_manager.get_client(account_id)
    if not client:
        return ResultObject.success({"connected": False, "status": "not_found"})
    await client.stop()
    return ResultObject.success({"connected": False, "status": "disconnected"})


@websocket_router.post("/status")
async def websocket_status(
    data: dict = {},
    current_user: dict = Depends(get_current_user)
):
    """获取 WebSocket 连接状态。"""
    account_id = _parse_account_id(data.get("xianyuAccountId") or data.get("accountId"))
    if account_id is None or account_id <= 0:
        raise HTTPException(status_code=422, detail="accountId 必须为正整数。")
    client = ws_manager.get_client(account_id)
    if not client:
        return ResultObject.success({
            "connected": False,
            "status": "not_found",
            "hasSid": False,
            "lastError": "",
        })
    status = ws_manager.get_status(account_id)
    return ResultObject.success({
        "connected": bool(getattr(client, "is_connected", False)),
        "status": status.get("phase", "unknown"),
        "hasSid": bool(status.get("hasSid")),
        "lastError": status.get("lastError", ""),
    })


# ====================================================================
# 扩展端点：前端调用但原 misc.py 未实现的部分
# ====================================================================

# ---- 通知相关（POST /api/notification/logs|latest|test） ----
# 注意：system.py 已注册 GET /api/notification/list，这里补 POST 端点
@notification_router.post("/logs")
async def notification_logs(
    data: dict = {},
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Retired: use the authoritative outbound-delivery log endpoint."""
    del data, db, current_user
    raise HTTPException(
        status_code=410,
        detail="旧通知日志接口已移除；请使用 GET /api/notifications/delivery-logs。",
    )


@notification_router.post("/latest")
async def notification_latest(
    data: dict = {},
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Retired: use the authoritative navigation-notification endpoint."""
    del data, db, current_user
    raise HTTPException(
        status_code=410,
        detail="旧通知列表接口已移除；请使用 GET /api/navigation/notifications。",
    )


@notification_router.post("/test")
async def notification_test(
    data: dict = {},
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Retired: the old endpoint never sent a notification."""
    del data, db, current_user
    raise HTTPException(
        status_code=410,
        detail="旧通知测试桩已移除；请使用 POST /api/notifications/test 执行真实渠道测试。",
    )


# ---- 操作日志扩展（POST /api/operationLog/*） ----
# 注意：system.py 已注册 GET /api/operationLog/list，这里补 POST 端点
@operation_log_router.post("/deleteOld")
async def operation_log_delete_old(
    data: dict = {},
    current_user: dict = Depends(get_current_user),
):
    """Retired: arbitrary cleanup could bypass the configured retention floor."""
    del data, current_user
    raise HTTPException(
        status_code=410,
        detail=(
            "手动审计日志清理已移除；日志仅由 worker 按 "
            "AUDIT_LOG_RETENTION_DAYS 策略分批清理。"
        ),
    )


@operation_log_router.post("/runtime")
async def operation_log_runtime(
    data: dict = {},
    current_user: dict = Depends(get_current_user),
):
    """Retired: this endpoint never returned an authoritative log stream."""
    del data, current_user
    raise HTTPException(
        status_code=410,
        detail="运行时日志摘要占位接口已移除；请使用 GET /api/operation-logs 查询真实审计记录。",
    )


def _list_runtime_log_files(log_dir: str) -> list[dict[str, object]]:
    """Return bounded metadata for regular, non-symlink log files."""

    if not os.path.isdir(log_dir):
        return []
    files: list[dict[str, object]] = []
    with os.scandir(log_dir) as entries:
        for entry in entries:
            if not entry.is_file(follow_symlinks=False):
                continue
            stat_result = entry.stat(follow_symlinks=False)
            files.append(
                {
                    "name": entry.name,
                    "size": stat_result.st_size,
                    "modified": datetime.datetime.fromtimestamp(
                        stat_result.st_mtime
                    ).isoformat(),
                }
            )
            if len(files) >= 1000:
                break
    return sorted(files, key=lambda item: str(item["name"]))


@operation_log_router.post("/runtime/files")
async def operation_log_runtime_files(
    data: dict = {},
    current_user: dict = Depends(get_current_user),
):
    """运行时日志文件列表。"""
    try:
        log_dir = os.path.normpath(os.path.join(os.path.dirname(__file__), "../../../../logs"))
        files = await asyncio.to_thread(_list_runtime_log_files, log_dir)
        return ResultObject.success(files)
    except Exception as exc:
        logger.error(
            "Runtime log file query failed errorType=%s",
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=503,
            detail="运行时日志文件暂不可用，请稍后重试。",
        ) from exc


@operation_log_router.post("/runtime/clear")
async def operation_log_runtime_clear(
    data: dict = {},
    current_user: dict = Depends(get_current_user),
):
    """Retired: bulk log deletion bypassed retention and audit controls."""
    del data, current_user
    raise HTTPException(
        status_code=410,
        detail=(
            "运行时日志批量删除接口已移除；请使用部署环境的受控日志保留、"
            "轮转和归档策略，并保留管理员操作审计记录。"
        ),
    )


# ---- 备份与导出（POST /api/backup/restore-db, GET /api/backup/export-db） ----
@backup_router.post("/restore-db")
async def backup_restore_db(
    data: dict = {},
    current_user: dict = Depends(get_current_user),
):
    """Retired: database restore must remain an audited operator workflow."""
    del data, current_user
    raise HTTPException(
        status_code=410,
        detail="应用内数据库恢复已移除；请按 docs/production-readiness.md 的 MySQL 恢复流程操作。",
    )


@backup_router.get("/export-db")
async def backup_export_db(
    current_user: dict = Depends(get_current_user),
):
    """Retired: database export must remain an audited operator workflow."""
    del current_user
    raise HTTPException(
        status_code=410,
        detail="应用内数据库导出已移除；请按 docs/production-readiness.md 的 MySQL 备份流程操作。",
    )


# ---- Excel 导出 ----
@excel_router.get("/export/orders")
async def excel_export_orders(
    current_user: dict = Depends(get_current_user),
):
    """Retired: no real spreadsheet export is implemented."""
    del current_user
    raise HTTPException(
        status_code=410,
        detail="订单 Excel 导出尚未提供；请使用 GET /api/orders 分页读取订单。",
    )


@excel_router.get("/template/kami")
async def excel_template_kami(
    current_user: dict = Depends(get_current_user),
):
    """卡密导入模板下载。返回一个简单的 CSV 模板。"""
    from fastapi.responses import Response
    csv_content = "卡号,密码,备注\n"
    return Response(
        content=csv_content.encode("utf-8-sig"),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=kami_template.csv"},
    )


# ---- 商品 SKU ----
@goods_sku_router.post("/list")
async def goods_sku_list(
    data: dict = {},
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Retired: the persistence model has no SKU variants."""
    del data, db, current_user
    raise HTTPException(
        status_code=410,
        detail="当前版本没有 SKU 变体模型；请使用 GET /api/goods/{goods_id} 查询商品主记录。",
    )


@goods_sku_router.post("/detail")
async def goods_sku_detail(
    data: dict = {},
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Retired: the persistence model has no SKU variants."""
    del data, db, current_user
    raise HTTPException(
        status_code=410,
        detail="当前版本没有 SKU 变体模型；请使用 GET /api/goods/{goods_id} 查询商品主记录。",
    )


# ---- 数据面板 ----
@data_panel_router.post("/stats")
async def data_panel_stats(
    data: dict = {},
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Retired: use the authoritative dashboard summary contract."""
    del data, db, current_user
    raise HTTPException(
        status_code=410,
        detail="旧数据面板统计接口已移除；请使用 GET /api/dashboard/summary。",
    )


@data_panel_router.post("/trend")
async def data_panel_trend(
    data: dict = {},
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Retired: use the authoritative dashboard trend contract."""
    del data, db, current_user
    raise HTTPException(
        status_code=410,
        detail="旧数据面板趋势接口已移除；请使用 GET /api/dashboard/sales-trend。",
    )


# ---- 验证码调试图片 ----
@captcha_ext_router.get("/debug-image/latest")
async def captcha_debug_image_latest(
    current_user: dict = Depends(get_current_user),
):
    """Retired: production APIs do not expose captcha debug screenshots."""
    del current_user
    raise HTTPException(
        status_code=410,
        detail="生产接口不暴露验证码调试截图；请通过受控服务日志排查验证码流程。",
    )


# ---- 商机/闲鱼搜索 ----
@business_opportunity_router.get("/search")
async def business_opportunity_search(
    keyword: str = Query(default=""),
    current_user: dict = Depends(get_current_user),
):
    """Retired: no authoritative business-opportunity provider exists."""
    del keyword, current_user
    raise HTTPException(
        status_code=410,
        detail="商机搜索兼容接口已移除；当前版本没有替代搜索接口，请停止重试。",
    )


@business_opportunity_router.get("/shop")
async def business_opportunity_shop(
    userId: str = Query(default=""),
    current_user: dict = Depends(get_current_user),
):
    """Retired: no authoritative shop collector exists."""
    del userId, current_user
    raise HTTPException(
        status_code=410,
        detail="店铺采集兼容接口已移除；当前版本没有替代采集接口，请停止重试。",
    )


@business_opportunity_router.post("/collect-shop")
async def business_opportunity_collect_shop(
    data: dict = {},
    current_user: dict = Depends(get_current_user),
):
    """Retired: no authoritative shop collector exists."""
    del data, current_user
    raise HTTPException(
        status_code=410,
        detail="商机采集兼容接口已移除；当前版本没有替代采集接口，请停止重试。",
    )


# ---- 爬虫任务（参考项目由 Node.js crawler-service 实现） ----
@crawler_router.post("/import/goofish")
async def crawler_import_goofish(
    data: dict = {},
    current_user: dict = Depends(get_current_user),
):
    """Retired: the crawler build has no durable import-job provider."""
    del data, current_user
    raise HTTPException(
        status_code=410,
        detail="店铺导入接口未实现，当前 crawler 仅保留滑块求解能力；请停止重试。",
    )


@crawler_router.get("/crawl-jobs/{job_id}")
async def crawler_crawl_job(
    job_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Retired: no crawl job is ever created by this build."""
    del job_id, current_user
    raise HTTPException(
        status_code=410,
        detail="采集任务接口未实现，当前版本不会创建 crawl job；请停止轮询。",
    )


@crawler_router.get("/goofish/stores/{user_id}/items")
async def crawler_goofish_store_items(
    user_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Retired: no persisted store-crawl results exist."""
    del user_id, current_user
    raise HTTPException(
        status_code=410,
        detail="店铺商品采集接口未实现，当前版本没有可查询的采集结果。",
    )


# ---- 闲鱼商品搜索（GET /api/goofish/search） ----
@goofish_router.get("/search")
async def goofish_search(
    keyword: str = Query(default=""),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
):
    """Retired: no server-side Goofish search provider exists."""
    del keyword, page, page_size, current_user
    raise HTTPException(
        status_code=410,
        detail="服务端闲鱼搜索兼容接口已移除；当前版本没有替代后端接口。",
    )


def _list_media_files(media_dir: Path) -> list[dict[str, object]]:
    """Build a stable media snapshot without blocking the event loop."""

    if not media_dir.exists():
        return []
    files: list[dict[str, object]] = []
    for fpath in sorted(media_dir.iterdir(), key=lambda path: path.name):
        if not fpath.is_file():
            continue
        stat_result = fpath.stat()
        files.append(
            {
                "name": fpath.name,
                "size": stat_result.st_size,
                "updatedTime": datetime.datetime.fromtimestamp(
                    stat_result.st_mtime
                ).isoformat(),
            }
        )
    return files


def _delete_media_file(path: Path) -> bool:
    """Delete one regular media file and report races as an honest miss."""

    try:
        if not path.is_file():
            return False
        path.unlink()
        return True
    except FileNotFoundError:
        return False


@media_router.post("/list")
async def media_list(
    data: dict = {},
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    try:
        media_dir = resolve_upload_path("media")
        files = await asyncio.to_thread(_list_media_files, media_dir)
        return ResultObject.success(files)
    except Exception as exc:
        logger.error(
            "Media list failed errorType=%s",
            type(exc).__name__,
            exc_info=True,
        )
        raise HTTPException(
            status_code=503,
            detail="媒体库暂不可用，请稍后重试。",
        ) from exc


@media_router.post("/delete")
async def media_delete(
    data: dict = {},
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    try:
        filename = data.get("name", "")
        if not filename:
            raise HTTPException(status_code=422, detail="name 不能为空。")
        if Path(filename).name != filename:
            raise HTTPException(status_code=422, detail="name 不是有效的媒体文件名。")
        fpath = resolve_upload_path(f"media/{filename}")
        if not await asyncio.to_thread(_delete_media_file, fpath):
            raise HTTPException(status_code=404, detail="媒体文件不存在。")
        logger.info("Deleted media: %s", filename)
        return ResultObject.success({
            "deleted": True,
            "status": "deleted",
            "name": filename,
        })
    except HTTPException:
        raise
    except UnsafePathError as exc:
        raise HTTPException(status_code=422, detail="name 不是有效的媒体文件名。") from exc
    except Exception as exc:
        logger.error(
            "Media deletion failed errorType=%s",
            type(exc).__name__,
            exc_info=True,
        )
        raise HTTPException(
            status_code=503,
            detail="媒体文件删除未完成，请稍后重试。",
        ) from exc


@image_router.post("/upload")
async def image_upload(
    accountId: int = Form(..., gt=0),
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user)
):
    try:
        raw = await read_upload_limited(file)
        content, extension, _ = await asyncio.to_thread(
            normalize_image_bytes,
            raw,
        )
        saved_name = f"img_{accountId}_{uuid4().hex}{extension}"
        save_path = resolve_upload_path(f"images/{saved_name}")
        await asyncio.to_thread(write_upload_bytes_atomic, save_path, content)
        logger.info("Image uploaded: %s (%d bytes)", saved_name, len(content))
        return ResultObject.success({
            "url": f"/uploads/images/{saved_name}",
            "name": saved_name,
            "size": len(content),
            "message": "上传成功"
        })
    except (UploadValidationError, UnsafePathError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Image upload failed", exc_info=True)
        raise HTTPException(
            status_code=503,
            detail="图片上传未完成，请稍后重试。",
        ) from exc
    finally:
        await file.close()


@image_router.post("/uploadFromUrl")
async def image_upload_from_url(
    data: dict = {},
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    try:
        image_url = data.get("url", "")
        if not image_url:
            raise HTTPException(status_code=422, detail="url 不能为空。")
        account_id = _parse_account_id(data.get("accountId"))
        if account_id is None or account_id <= 0:
            raise HTTPException(status_code=422, detail="accountId 必须为正整数。")
        raw = await download_public_image(str(image_url))
        content, extension, _ = await asyncio.to_thread(
            normalize_image_bytes,
            raw,
        )
        saved_name = f"url_img_{account_id}_{uuid4().hex}{extension}"
        save_path = resolve_upload_path(f"images/{saved_name}")
        await asyncio.to_thread(write_upload_bytes_atomic, save_path, content)
        logger.info("URL image saved: %s", saved_name)
        return ResultObject.success({
            "url": f"/uploads/images/{saved_name}",
            "name": saved_name,
            "size": len(content),
            "message": "Upload success"
        })
    except HTTPException:
        raise
    except (UnsafeRemoteURLError, UploadValidationError, UnsafePathError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Remote image upload failed", exc_info=True)
        raise HTTPException(
            status_code=502,
            detail="远程图片获取或保存未完成，请检查地址后重试。",
        ) from exc

# ---------------------------------------------------------------------------
# Address Dictionary (省/市/区 三级联动)
# ---------------------------------------------------------------------------
# 数据源：本地 china_address_dict.json 文件，首次访问时加载并在内存中
# 缓存 7 天。返回结构与商业版 china_address_dict 接口保持一致，便于前端
# PublishAddressCascader 组件复用。

_address_dict_cache: dict = {"tree": None, "expire_at": 0.0}
_ADDRESS_DICT_TTL = 7 * 24 * 3600  # 7 天


def _resolve_address_dict_path() -> Path:
    configured_path = os.getenv("ADDRESS_DICT_PATH", "").strip()
    candidates = []
    if configured_path:
        candidates.append(Path(configured_path).expanduser())
    module_path = Path(__file__).resolve()
    candidates.extend(
        [
            module_path.parents[3] / "data" / "china_address_dict.json",
            Path("/app/app/data/china_address_dict.json"),
            Path.cwd() / "app" / "data" / "china_address_dict.json",
        ]
    )
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.is_file():
            return resolved
    searched = ", ".join(str(path) for path in candidates)
    raise FileNotFoundError(f"china_address_dict.json not found; searched: {searched}")


def _load_address_dict_from_json() -> dict:
    """从本地 JSON 文件加载行政区划数据。"""
    json_path = _resolve_address_dict_path()
    with json_path.open("r", encoding="utf-8") as f:
        tree = json.load(f)
    if not isinstance(tree, dict):
        raise ValueError("china_address_dict.json must contain an object")
    return tree


@address_dict_router.get("/tree")
async def address_dict_tree(
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """返回全国省/市/区三级联动地址树，供发布商品页面选择商品位置使用。

    数据源为本地 china_address_dict.json 文件，内存缓存 7 天。
    """
    del current_user, db
    now = time.time()
    if _address_dict_cache["tree"] is not None and _address_dict_cache["expire_at"] > now:
        return ResultObject.success(_address_dict_cache["tree"])

    try:
        tree = _load_address_dict_from_json()
        provinces_count = len(tree.get("provinces") or [])
        if provinces_count == 0:
            return ResultObject(
                code=200,
                msg="地址字典数据为空",
                data={"provinces": [], "errorType": "address_dict_empty"},
            )

        _address_dict_cache["tree"] = tree
        _address_dict_cache["expire_at"] = now + _ADDRESS_DICT_TTL
        return ResultObject.success(tree)
    except Exception as exc:
        logger.error("address_dict: failed to load local JSON: %s", type(exc).__name__)
        return ResultObject(
            code=503,
            msg="地址字典加载失败，请稍后重试",
            data={"provinces": [], "errorType": "address_dict_load_failed"},
        )
