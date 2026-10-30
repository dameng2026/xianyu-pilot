from __future__ import annotations

import hashlib
import hmac
import logging
import re
import time
import uuid
from collections import deque
from typing import Awaitable, Callable

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .config import settings
from .redis_client import RedisUnavailableError, redis_incr
from .response import ResultObject
from .security import authenticate_token, request_client_ip

logger = logging.getLogger(__name__)

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_PUBLIC_API_PATHS = {
    "/api/auth/login",
    "/api/feishu/webhook",
    "/api/sse/subscribe",
}

# Removed self-service authentication endpoints stay public only so old clients
# receive the authoritative HTTP 410 migration contract instead of an unrelated
# authentication challenge. No handler is registered for any of these paths.
_PUBLIC_RETIRED_AUTH_PATHS = {
    "/api/login/login",
    "/api/login/logout",
    "/api/login/checkUserExists",
    "/api/login/register",
    "/api/login/sendSmsCode",
    "/api/login/verifyResetCode",
    "/api/login/resetPassword",
}


_ENVELOPE_FIRST_KEY_RE = re.compile(br'^\s*\{\s*"([^"\\]+)"\s*:')
_ENVELOPE_CODE_RE = re.compile(br'^\s*\{\s*"code"\s*:\s*(-?\d+)')


class ResultEnvelopeStatusMiddleware:
    """Keep the HTTP status aligned with the top-level API result envelope.

    Legacy handlers return ``ResultObject`` values directly.  Without this
    adapter FastAPI serialises even ``code=400``/``code=500`` as HTTP 200,
    which makes proxies, health telemetry and retry policies report failures
    as successes.  The middleware only inspects the small prefix containing
    the first JSON key; successful responses and non-JSON/streaming responses
    are passed through unchanged.
    """

    _MAX_PREFIX_BYTES = 512

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = str(scope.get("path") or "")
        if scope.get("type") != "http" or not (
            path == "/api" or path.startswith("/api/") or path.startswith("/ai/")
        ):
            await self.app(scope, receive, send)
            return

        pending_start: Message | None = None
        pending_bodies: list[Message] = []
        prefix = bytearray()

        async def flush_pending(code: int | None = None) -> None:
            nonlocal pending_start
            if pending_start is None:
                return
            start = dict(pending_start)
            if code is not None and 400 <= code <= 599:
                start["status"] = code
            await send(start)
            pending_start = None
            for body_message in pending_bodies:
                await send(body_message)
            pending_bodies.clear()

        async def send_with_envelope_status(message: Message) -> None:
            nonlocal pending_start
            if message["type"] == "http.response.start":
                status = int(message.get("status") or 0)
                headers = {
                    key.lower(): value.lower()
                    for key, value in message.get("headers", [])
                }
                content_type = headers.get(b"content-type", b"")
                is_json = b"application/json" in content_type or b"+json" in content_type
                if status == 200 and is_json:
                    pending_start = message
                    return
                await send(message)
                return

            if message["type"] != "http.response.body" or pending_start is None:
                await send(message)
                return

            pending_bodies.append(message)
            body = bytes(message.get("body", b""))
            if len(prefix) < self._MAX_PREFIX_BYTES:
                prefix.extend(body[: self._MAX_PREFIX_BYTES - len(prefix)])

            code_match = _ENVELOPE_CODE_RE.match(prefix)
            if code_match:
                await flush_pending(int(code_match.group(1)))
                return

            first_key_match = _ENVELOPE_FIRST_KEY_RE.match(prefix)
            definitely_not_envelope = bool(
                first_key_match and first_key_match.group(1) != b"code"
            )
            complete = not bool(message.get("more_body", False))
            if definitely_not_envelope or complete or len(prefix) >= self._MAX_PREFIX_BYTES:
                await flush_pending()

        await self.app(scope, receive, send_with_envelope_status)
        # HEAD/204 implementations may legally finish without a body message.
        if pending_start is not None:
            await flush_pending()


def _request_id(request: Request) -> str:
    candidate = (request.headers.get("X-Request-ID") or "").strip()
    if _REQUEST_ID_RE.fullmatch(candidate):
        return candidate
    return uuid.uuid4().hex


def _requires_authentication(path: str, method: str) -> bool:
    if method == "OPTIONS":
        return False
    if not (path.startswith("/api/") or path == "/api" or path.startswith("/ai/")):
        return False
    if path in _PUBLIC_API_PATHS or path in _PUBLIC_RETIRED_AUTH_PATHS:
        return False
    # Every internal route performs its own constant-time X-Internal-Token
    # validation. It intentionally does not accept ordinary public traffic.
    if path.startswith("/api/internal/"):
        return False
    return True


def _bearer_token(request: Request) -> str:
    value = (request.headers.get("Authorization") or "").strip()
    scheme, separator, token = value.partition(" ")
    if not separator or scheme.lower() != "bearer":
        return ""
    return token.strip()


def _json_error(status_code: int, message: str, request_id: str) -> JSONResponse:
    payload = ResultObject.failed(message, code=status_code).model_dump(by_alias=True)
    payload["requestId"] = request_id
    headers = {"X-Request-ID": request_id}
    if status_code == 401:
        headers["WWW-Authenticate"] = "Bearer"
    return JSONResponse(status_code=status_code, content=payload, headers=headers)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Default-deny API auth, rate limiting, request IDs and security headers."""

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable]):
        started = time.perf_counter()
        request_id = _request_id(request)
        request.state.request_id = request_id
        path = request.url.path

        if (path.startswith("/api/") or path.startswith("/ai/")) and settings.api_rate_limit_per_minute:
            client_ip = request_client_ip(request)
            bucket = int(time.time() // 60)
            digest = hashlib.sha256(client_ip.encode("utf-8")).hexdigest()
            count = await redis_incr(
                f"api_rate:{digest}:{bucket}",
                expire=120,
            )
            if count > settings.api_rate_limit_per_minute:
                response = _json_error(429, "请求过于频繁，请稍后重试", request_id)
                response.headers["Retry-After"] = "60"
                return response

        if path.startswith("/api/internal/"):
            expected = (settings.internal_api_token or "").strip()
            supplied = (request.headers.get("X-Internal-Token") or "").strip()
            if not expected:
                return _json_error(
                    503,
                    "内部认证服务尚未配置",
                    request_id,
                )
            if not supplied:
                return _json_error(401, "缺少内部访问令牌", request_id)
            if not hmac.compare_digest(supplied, expected):
                return _json_error(403, "内部访问令牌无效", request_id)
            # The inner audit middleware can now persist a durable intent
            # before an internal POST reaches route dependencies/business I/O.
            request.state.current_user = {
                "user_id": -1,
                "username": "internal-service",
                "role": "internal",
            }
        elif _requires_authentication(path, request.method):
            try:
                payload = await authenticate_token(_bearer_token(request))
            except RedisUnavailableError:
                logger.warning(
                    "authentication security state unavailable request_id=%s route=%s",
                    request_id,
                    path,
                )
                return _json_error(
                    503,
                    "认证安全状态暂不可用，请稍后重试",
                    request_id,
                )
            if payload is None:
                return _json_error(401, "暂未登录或登录已过期", request_id)
            request.state.auth_payload = payload
            request.state.current_user = {
                "user_id": 0,
                "username": payload["username"],
                "role": "admin",
                "jti": payload["jti"],
                "exp": payload["exp"],
            }

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'none'; frame-ancestors 'none'",
        )
        if path.startswith("/api/") or path.startswith("/ai/"):
            response.headers.setdefault("Cache-Control", "no-store")
        if settings.is_production_like:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")

        elapsed_ms = (time.perf_counter() - started) * 1000
        route = request.scope.get("route")
        route_template = str(getattr(route, "path", "unmatched"))
        client_digest = hashlib.sha256(request_client_ip(request).encode("utf-8")).hexdigest()[:12]
        logger.info(
            "request_complete request_id=%s method=%s route=%s status=%s duration_ms=%.1f client_hash=%s",
            request_id,
            request.method,
            route_template,
            response.status_code,
            elapsed_ms,
            client_digest,
        )
        return response


class RequestBodyLimitMiddleware:
    """Reject oversized fixed-length and chunked HTTP request bodies."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        raw_length = headers.get(b"content-length", b"")
        try:
            content_length = int(raw_length) if raw_length else None
        except ValueError:
            content_length = None
        if content_length is not None and content_length > self.max_bytes:
            await self._send_too_large(scope, receive, send)
            return

        # Buffer only requests with an unknown length. This closes the chunked
        # transfer bypass without forcing ordinary uploads to be copied twice.
        if content_length is not None:
            await self.app(scope, receive, send)
            return

        buffered: deque[Message] = deque()
        total = 0
        while True:
            message = await receive()
            buffered.append(message)
            if message["type"] == "http.request":
                total += len(message.get("body", b""))
                if total > self.max_bytes:
                    await self._send_too_large(scope, receive, send)
                    return
                if not message.get("more_body", False):
                    break
            elif message["type"] == "http.disconnect":
                break

        async def replay_receive() -> Message:
            if buffered:
                return buffered.popleft()
            return await receive()

        await self.app(scope, replay_receive, send)

    @staticmethod
    async def _send_too_large(scope: Scope, receive: Receive, send: Send) -> None:
        response = JSONResponse(
            status_code=413,
            content=ResultObject.failed("请求内容过大", code=413).model_dump(by_alias=True),
        )
        await response(scope, receive, send)
