import asyncio
import hashlib
import ipaddress
import logging
import re
import time
import uuid
from typing import Any

import bcrypt
import jwt
from datetime import datetime, timedelta, timezone
from fastapi import Request
from app.core.config import settings
from app.core.redis_client import redis_delete, redis_exists, redis_get, redis_incr, redis_set

logger = logging.getLogger(__name__)


def hash_password(password: str) -> str:
    encoded = (password or "").encode("utf-8")
    if not encoded or len(encoded) > 72:
        raise ValueError("密码 UTF-8 长度必须在 1-72 字节之间")
    return bcrypt.hashpw(encoded, bcrypt.gensalt(rounds=12)).decode("ascii")


def verify_password(plain: str, hashed: str) -> bool:
    if not plain or not hashed or not hashed.startswith(("$2a$", "$2b$", "$2y$")):
        return False
    try:
        encoded = plain.encode("utf-8")
        if len(encoded) > 72:
            return False
        return bcrypt.checkpw(encoded, hashed.encode("ascii"))
    except (ValueError, TypeError, UnicodeError):
        return False


def validate_password_strength(password: str, username: str = "") -> str | None:
    """Return a user-facing validation error, or None for an acceptable password."""
    value = password or ""
    byte_length = len(value.encode("utf-8"))
    if len(value) < 6:
        return "新密码至少需要 6 个字符"
    if byte_length > 72:
        return "新密码 UTF-8 长度不能超过 72 字节"
    normalized = value.casefold()
    if normalized in {
        "123456789012", "password123!", "admin123456!", "qwerty123456!",
    }:
        return "新密码过于常见，请更换更难猜测的密码"
    user = (username or "").strip().casefold()
    if len(user) >= 3 and user in normalized:
        return "新密码不能包含管理员用户名"
    return None


def create_token(username: str) -> str:
    now = datetime.now(timezone.utc)
    jti = uuid.uuid4().hex
    payload = {
        "sub": "admin",
        "username": username,
        "role": "admin",
        "jti": jti,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": now,
        "nbf": now,
        "auth_time": time.time(),
        "exp": now + timedelta(milliseconds=settings.jwt_expiration_ms),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict:
    return jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=[settings.jwt_algorithm],
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
        leeway=30,
        options={
            "require": ["sub", "username", "role", "jti", "iss", "aud", "iat", "nbf", "exp"],
        },
    )


# ============================================================
# Token 黑名单（登出后吊销 jti）
# ============================================================
_BLACKLIST_PREFIX = "jwt_blacklist:"
_TOKENS_VALID_AFTER_KEY = "jwt_tokens_valid_after:admin"
_LOGIN_ATTEMPT_PREFIX = "login_attempts:"
# 内存回退：jti -> expiry(epoch seconds)
_mem_blacklist: dict = {}
_mem_blacklist_lock = asyncio.Lock()


def _allow_security_memory_fallback() -> bool:
    """Only deterministic tests may replace shared security state with memory."""

    return (settings.app_env or "").strip().casefold() == "test"


def _cleanup_mem_blacklist() -> None:
    """清理内存中过期的黑名单项。"""
    now = time.time()
    expired = [j for j, exp in _mem_blacklist.items() if exp <= now]
    for j in expired:
        _mem_blacklist.pop(j, None)


async def is_token_blacklisted(jti: str) -> bool:
    """检查 jti 是否在黑名单中。"""
    if not jti:
        return False
    allow_memory_fallback = _allow_security_memory_fallback()
    if await redis_exists(
        _BLACKLIST_PREFIX + jti,
        allow_memory_fallback=allow_memory_fallback,
    ):
        return True
    if not allow_memory_fallback:
        return False
    async with _mem_blacklist_lock:
        _cleanup_mem_blacklist()
        if jti in _mem_blacklist:
            # 命中内存黑名单
            return True
    return False


async def blacklist_token(jti: str, exp: int) -> None:
    """将 jti 加入黑名单，TTL 到 token 过期时间（exp 为 epoch seconds）。"""
    if not jti or not exp:
        return
    now = time.time()
    ttl = max(int(exp - now), 1)
    allow_memory_fallback = _allow_security_memory_fallback()
    await redis_set(
        _BLACKLIST_PREFIX + jti,
        "1",
        ex=ttl,
        allow_memory_fallback=allow_memory_fallback,
    )
    if not allow_memory_fallback:
        return
    async with _mem_blacklist_lock:
        _cleanup_mem_blacklist()
        _mem_blacklist[jti] = float(exp)


async def revoke_token_payload(payload: dict[str, Any]) -> None:
    """Revoke one JWT until its natural expiry."""
    try:
        exp = int(payload.get("exp") or 0)
    except (TypeError, ValueError):
        exp = 0
    await blacklist_token(str(payload.get("jti") or ""), exp)


async def revoke_all_tokens() -> None:
    """Invalidate all tokens issued before this instant (e.g. password change)."""
    await redis_set(
        _TOKENS_VALID_AFTER_KEY,
        repr(time.time()),
        allow_memory_fallback=_allow_security_memory_fallback(),
    )


async def is_token_revoked(payload: dict[str, Any]) -> bool:
    if await is_token_blacklisted(str(payload.get("jti") or "")):
        return True
    raw_cutoff = await redis_get(
        _TOKENS_VALID_AFTER_KEY,
        allow_memory_fallback=_allow_security_memory_fallback(),
    )
    if not raw_cutoff:
        return False
    try:
        issued_at = float(payload.get("auth_time") or payload.get("iat") or 0)
        return issued_at <= float(raw_cutoff)
    except (TypeError, ValueError):
        return True


async def authenticate_token(token: str) -> dict[str, Any] | None:
    try:
        payload = decode_token(token)
    except jwt.PyJWTError:
        return None
    if payload.get("sub") != "admin" or payload.get("role") != "admin":
        return None
    if payload.get("username") != settings.admin_username:
        return None
    if await is_token_revoked(payload):
        return None
    return payload


def _is_trusted_proxy(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    for value in settings.trusted_proxy_list:
        try:
            if ip in ipaddress.ip_network(value, strict=False):
                return True
        except ValueError:
            logger.warning("Ignoring invalid TRUSTED_PROXY_IPS entry: %s", value)
    return False


def request_client_ip(request: Request) -> str:
    """Resolve one client IP from the single, explicitly trusted Web adapter.

    The Web proxy replaces the incoming forwarding chain with one normalized
    address.  Treating this as a single-hop interface keeps malformed or
    attacker-supplied chains from influencing authentication throttles or
    audit records.
    """
    peer = str(request.client.host if request.client else "unknown").strip()
    if not _is_trusted_proxy(peer):
        return peer[:64] or "unknown"

    forwarded = str(request.headers.get("X-Forwarded-For", ""))
    candidate = forwarded.strip()
    if not candidate or len(forwarded) > 64 or "," in forwarded:
        return peer[:64] or "unknown"
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return peer[:64] or "unknown"


def _login_attempt_key(request: Request) -> str:
    client_hash = hashlib.sha256(request_client_ip(request).encode("utf-8")).hexdigest()
    return f"{_LOGIN_ATTEMPT_PREFIX}{client_hash}"


async def login_retry_after(request: Request) -> int:
    raw = await redis_get(
        _login_attempt_key(request),
        allow_memory_fallback=_allow_security_memory_fallback(),
    )
    try:
        attempts = int(raw or 0)
    except (TypeError, ValueError):
        attempts = 0
    return settings.login_lock_minutes * 60 if attempts >= settings.login_max_attempts else 0


async def record_login_failure(request: Request) -> int:
    return await redis_incr(
        _login_attempt_key(request),
        expire=settings.login_lock_minutes * 60,
        allow_memory_fallback=_allow_security_memory_fallback(),
    )


async def clear_login_failures(request: Request) -> None:
    await redis_delete(
        _login_attempt_key(request),
        allow_memory_fallback=_allow_security_memory_fallback(),
    )
