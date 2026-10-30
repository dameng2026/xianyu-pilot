"""Browser boundary for the demo's limited, mock-only local capabilities."""
import asyncio
import secrets

from starlette.requests import Request
from starlette.responses import JSONResponse


COOKIE_NAME = "xya_demo_session"


class LocalBoundary:
    def __init__(self, app, *, origin, store, csrf):
        self.app, self.origin, self.store, self.csrf = app, origin, store, csrf
        self.host = origin.removeprefix("http://")

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request = Request(scope)

        async def reject(code, message):
            await JSONResponse({"detail": message}, status_code=code)(scope, receive, send)

        if request.headers.get("host") != self.host:
            return await reject(403, "仅允许通过本机启动地址访问")
        if scope.get("client") and scope["client"][0] not in {"127.0.0.1", "::1", "testclient"}:
            return await reject(403, "仅允许本机连接")
        if request.headers.get("sec-fetch-site") == "cross-site":
            return await reject(403, "不接受其他网站发起的请求")
        origin = request.headers.get("origin")
        if origin is not None and origin != self.origin:
            return await reject(403, "来源地址不匹配")
        if request.method not in {"GET", "POST"}:
            return await reject(405, "不支持的请求方法")
        is_api = request.url.path.startswith("/api/")
        connector_request = request.url.path.startswith("/api/qq/events/")
        mcp_request = request.url.path == "/mcp"
        if is_api and not connector_request:
            session = request.cookies.get(COOKIE_NAME, "")
            if not session or len(session) > 128 or not await asyncio.to_thread(self.store.valid_session, session):
                return await reject(401, "页面会话已过期，请刷新页面")
        body = b""
        if request.method == "POST":
            if not (connector_request or mcp_request) and (not is_api or origin != self.origin or not secrets.compare_digest(
                request.headers.get("x-demo-csrf", "").encode(), self.csrf.encode()
            )):
                return await reject(403, "请求校验失败，请刷新页面")
            if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
                return await reject(415, "需要 JSON 请求")
            try:
                async with asyncio.timeout(5):
                    while True:
                        message = await receive()
                        if message["type"] == "http.disconnect":
                            return
                        body += message.get("body", b"")
                        if len(body) > (65536 if connector_request else 32768):
                            return await reject(413, "请求内容过大")
                        if not message.get("more_body", False):
                            break
            except TimeoutError:
                return await reject(408, "请求读取超时")

        sent_body = False

        async def bounded_receive():
            nonlocal sent_body
            if not sent_body:
                sent_body = True
                return {"type": "http.request", "body": body}
            return await receive()

        async def safe_send(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend([
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"cache-control", b"no-store"),
                    (b"content-security-policy", b"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"),
                ])
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, bounded_receive if request.method == "POST" else receive, safe_send)
