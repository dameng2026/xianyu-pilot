"""
滑块验证处理服务
================

实现四部分能力：

1. **智能检测**：从 MTOP API 响应中检测滑块/人机验证需求
   - 关键词：FAIL_SYS_USER_VALIDATE / RGV587_ERROR / 被挤爆啦 / CAPTCHA_NEEDED
   - 提取验证 URL（data.url）

2. **操作指引**：为前端提供详细的分步操作指引
   - 检测到滑块后返回结构化指引数据
   - 包含：访问 URL、操作步骤、Cookie 更新提示

3. **自动拖动**：调用 crawler-service 的 Playwright 滑块求解接口
   - 通过 HTTP 调用 crawler-service 的 /api/goofish/slide-solve
   - 失败时回退到人工处理指引
   - 集成指数退避：失败累加冷却，成功清零

4. **综合处理**：检测 + 通知 + 自动求解 + 求解记录 + SSE 广播
   - 每次求解都落库到 xianyu_captcha_solve_record
   - 通过 SSE 广播 captcha_solve 事件，前端实时刷新
   - 同步更新 cookie_status，前端账号列表实时反映状态

调用方式：
- detect_captcha(response) -> 检测是否需要滑块
- build_instructions(account_id, captcha_url) -> 构建操作指引
- try_auto_solve(account_id) -> 调用 Playwright 自动求解
- handle_captcha_for_account(...) -> 综合处理（含记录/SSE/状态更新）

单租户版：所有 SQL 与函数签名均不含 tenant_id。
broadcaster.broadcast(event_type, data) 仅两参数。
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx
from sqlalchemy import text

from ..core.config import settings
from ..core.cookie_crypto import decrypt_cookie_if_needed, encrypt_cookie_for_storage
from ..core.database import async_session

logger = logging.getLogger(__name__)

# === 自动滑块求解去重（跨模块共享） ===
# 同账号 10 分钟内只自动求解一次，避免断线重连循环 / Cookie 保活 / 心跳停跳
# 等多个触发源同时发起求解。
# ws_client._auto_solve_captcha_after_failure 和 cookie_token_refresher 均使用此状态。
_AUTO_SOLVE_LAST_TS: dict[int, float] = {}
_AUTO_SOLVE_DEDUP_SECONDS = 600  # 10 分钟


def should_auto_solve(account_id: int) -> bool:
    """检查该账号是否允许自动求解（未在去重窗口内）。"""
    now_ts = time.time()
    last_solve_ts = _AUTO_SOLVE_LAST_TS.get(account_id, 0)
    if now_ts - last_solve_ts < _AUTO_SOLVE_DEDUP_SECONDS:
        return False
    return True


def mark_auto_solve_started(account_id: int) -> None:
    """标记该账号已开始自动求解（更新去重时间戳）。"""
    _AUTO_SOLVE_LAST_TS[account_id] = time.time()


def _merge_cookies(old_str: str, new_str: str) -> str:
    """增量合并两个 Cookie 字符串，新 Cookie 覆盖同名旧字段。

    浏览器中的 Cookie 只刷新了部分字段（如 cna/isg/x5sec/_m_h5_tk），
    登录态相关 Cookie（如 unb/cookie2/_tb_token_）仍需保留旧的。
    """
    merged = {}
    for part in (old_str or "").split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            merged[k.strip()] = v.strip()
    for part in (new_str or "").split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            merged[k.strip()] = v.strip()  # 新覆盖旧
    return "; ".join(f"{k}={v}" for k, v in merged.items())


# ============================================================
# 滑块验证检测关键词
# ============================================================
CAPTCHA_KEYWORDS = (
    "FAIL_SYS_USER_VALIDATE",
    "RGV587_ERROR",
    "被挤爆啦",
    "CAPTCHA_NEEDED",
    "xcaptcha",
    "baxia",
    "punish",
)

CAPTCHA_URL_KEYWORDS = (
    "url",
    "captcha_url",
    "captchaUrl",
    "verifyUrl",
    "verify_url",
)


@dataclass
class CaptchaDetectResult:
    """滑块检测结果"""
    detected: bool
    captcha_url: Optional[str] = None
    reason: Optional[str] = None
    raw_response: Optional[dict] = None


@dataclass
class CaptchaInstructions:
    """操作指引"""
    account_id: int
    captcha_url: Optional[str]
    steps: list[str] = field(default_factory=list)
    title: str = "检测到账号需要完成滑块验证"
    auto_solve_available: bool = True
    manual_fallback_url: str = "https://www.goofish.com/"
    message: str = ""


# ============================================================
# 1. 智能检测
# ============================================================
def detect_captcha_from_response(response: dict | str | None) -> CaptchaDetectResult:
    """从 MTOP API 响应中检测滑块验证需求。

    支持两种输入：
    - dict: 完整的 MTOP 响应 JSON
    - str: 响应文本（直接搜索关键词）

    返回 CaptchaDetectResult，包含验证 URL（如果有的话）。
    """
    if not response:
        return CaptchaDetectResult(detected=False)

    if isinstance(response, str):
        text_lower = response.lower()
        for kw in CAPTCHA_KEYWORDS:
            if kw.lower() in text_lower:
                return CaptchaDetectResult(
                    detected=True,
                    reason=f"响应包含关键词: {kw}",
                )
        return CaptchaDetectResult(detected=False)

    # dict 类型：先查 ret 字段，再查 data.url
    ret_list = response.get("ret") or []
    if isinstance(ret_list, list):
        ret_str = " | ".join(str(r) for r in ret_list)
    else:
        ret_str = str(ret_list)

    for kw in CAPTCHA_KEYWORDS:
        if kw in ret_str:
            # 尝试提取验证 URL
            data = response.get("data") or {}
            captcha_url = None
            if isinstance(data, dict):
                for url_key in CAPTCHA_URL_KEYWORDS:
                    if url_key in data and data[url_key]:
                        captcha_url = str(data[url_key])
                        break

            return CaptchaDetectResult(
                detected=True,
                captcha_url=captcha_url,
                reason=f"ret 包含 {kw}",
                raw_response=response,
            )

    # 兜底：检查整个响应的字符串形式
    response_str = str(response)
    for kw in CAPTCHA_KEYWORDS:
        if kw in response_str:
            return CaptchaDetectResult(
                detected=True,
                reason=f"响应包含关键词: {kw}",
                raw_response=response if isinstance(response, dict) else None,
            )

    return CaptchaDetectResult(detected=False)


# ============================================================
# 2. 操作指引
# ============================================================
def build_captcha_instructions(
    account_id: int,
    captcha_url: Optional[str] = None,
    account_name: Optional[str] = None,
) -> CaptchaInstructions:
    """构建详细的滑块验证操作指引。

    返回的指引包含：
    - 自动求解：调用 Playwright 自动拖动（auto_solve_available=True）
    - 人工兜底：4 步指引（访问闲鱼→完成验证→复制 Cookie→更新 Cookie）
    """
    steps = [
        '【方案一·自动求解】点击下方"自动求解"按钮，系统将启动浏览器自动拖动滑块完成验证（推荐先尝试）',
        "【方案二·人工处理】如果自动求解失败，请按以下步骤操作：",
        "  1. 点击访问闲鱼主页 https://www.goofish.com/ 并登录（如已登录可跳过）",
        "  2. 在闲鱼页面完成滑块验证（通常出现在消息、商品发布等场景）",
        "  3. 验证通过后，按 F12 打开开发者工具 → Application → Cookies → 复制 .goofish.com 域下的完整 Cookie",
        '  4. 返回本页面，点击"手动更新 Cookie"按钮，粘贴复制的 Cookie 并保存',
        '  5. 保存后点击"启动连接"，系统会自动刷新 WebSocket Token 并重连',
    ]

    name_prefix = f"账号 {account_name or account_id} " if account_id else ""
    return CaptchaInstructions(
        account_id=account_id,
        captcha_url=captcha_url,
        steps=steps,
        title=f"{name_prefix}检测到滑块验证",
        auto_solve_available=True,
        message=(
            "检测到账号需要完成滑块验证。请先尝试自动求解；"
            "如自动求解失败，请按指引手动完成验证并更新 Cookie。"
            "更新 Cookie 后点击启动连接，会自动刷新 Token，"
            "滑块校验生效会延迟，稍等片刻会自动连接。"
        ),
    )


# ============================================================
# 3. Playwright 自动求解
# ============================================================
# 闲鱼消息页面 URL（对标商业版 xianyu_slider_stealth.py:124）
# 关键：只有消息页面才会出现滑块弹窗，首页本身不会弹出弹窗
CAPTCHA_TARGET_URL = "https://www.goofish.com/im"

def _resolve_internal_api_token() -> str:
    return str(getattr(settings, "internal_api_token", "") or "").strip()


async def try_auto_solve(
    account_id: int,
    target_url: Optional[str] = None,
    headless: bool = True,
    max_retries: int = 2,
    *,
    force: bool = False,
) -> dict:
    """调用 crawler-service 的 Playwright 滑块求解接口。

    Args:
        account_id: 账号 ID（用于读取 Cookie）
        target_url: 目标页面 URL（默认闲鱼消息页面 https://www.goofish.com/im）
        headless: 是否无头模式（默认 true，在后端运行不弹出浏览器窗口）
        max_retries: 最大重试次数
        force: 是否跳过指数退避（默认 False，全自动遵守退避）

    Returns:
        {
            "success": bool,
            "solved": bool,
            "captchaDetected": bool,
            "attempts": int,
            "error": Optional[str],
            "durationMs": int,
        }
    """
    # 全自动指数退避：冷却期内直接拒绝，避免 punish 加码
    from .captcha_backoff import (
        assert_auto_solve_allowed,
        record_solve_failure,
        record_solve_success,
    )

    blocked = await assert_auto_solve_allowed(account_id, force=force)
    if blocked:
        logger.warning(
            "滑块求解被指数退避拦截 accountId=%d error=%s",
            account_id, blocked.get("error"),
        )
        return blocked

    # 读取账号 Cookie
    try:
        async with async_session() as db:
            row = (await db.execute(
                text(
                    "SELECT encrypted_cookie FROM xianyu_account_auth "
                    "WHERE account_id = :aid AND COALESCE(deleted, 0) = 0 LIMIT 1"
                ),
                {"aid": account_id},
            )).mappings().first()
    except Exception as exc:
        logger.error(
            "读取滑块账号 Cookie 失败 errorType=%s",
            type(exc).__name__,
        )
        return {
            "success": False,
            "solved": False,
            "captchaDetected": False,
            "attempts": 0,
            "errorCode": "CAPTCHA_ACCOUNT_LOAD_FAILED",
            "error": "读取账号信息失败，请稍后重试",
            "durationMs": 0,
        }

    if not row:
        return {
            "success": False,
            "solved": False,
            "captchaDetected": False,
            "attempts": 0,
            "error": "账号不存在或未配置 Cookie",
            "durationMs": 0,
        }

    # decrypt_cookie_if_needed 在密钥不匹配/数据损坏时会抛 RuntimeError，
    # 必须在此处捕获，否则会一路冒泡到路由层导致 500 服务器内部错误。
    # 常见触发场景：账号从其他环境迁移过来，COOKIE_CRYPTO_SECRET 已变更。
    try:
        cookie_str = decrypt_cookie_if_needed(row["encrypted_cookie"])
    except Exception as exc:
        logger.error(
            "滑块账号 Cookie 解密失败 accountId=%d errorType=%s",
            account_id, type(exc).__name__,
        )
        return {
            "success": False,
            "solved": False,
            "captchaDetected": False,
            "attempts": 0,
            "errorCode": "CAPTCHA_COOKIE_DECRYPT_FAILED",
            "error": "Cookie 解密失败，请重新扫码登录闲鱼账号后再尝试求解",
            "durationMs": 0,
        }

    if not cookie_str:
        return {
            "success": False,
            "solved": False,
            "captchaDetected": False,
            "attempts": 0,
            "error": "Cookie 解密失败",
            "durationMs": 0,
        }

    # 调用 crawler-service
    crawler_url = settings.crawler_base_url
    endpoint = f"{crawler_url.rstrip('/')}/api/goofish/slide-solve"

    # 内部 token
    internal_token = _resolve_internal_api_token()
    payload = {
        "cookie": cookie_str,
        "targetUrl": target_url or CAPTCHA_TARGET_URL,
        "headless": headless,
        "maxRetries": max_retries,
        "timeoutMs": 30000,
    }

    headers = {
        "Content-Type": "application/json",
        "X-Internal-Token": internal_token,
    }

    started = time.time()
    try:
        async with httpx.AsyncClient(
            timeout=120.0,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            resp = await client.post(endpoint, json=payload, headers=headers)
            data = resp.json()
    except Exception as exc:
        logger.error(
            "调用 crawler-service 滑块求解失败 errorType=%s",
            type(exc).__name__,
        )
        await record_solve_failure(
            account_id, error="滑块求解服务暂时不可用",
        )
        return {
            "success": False,
            "solved": False,
            "captchaDetected": False,
            "attempts": 0,
            "errorCode": "CAPTCHA_SOLVER_UNAVAILABLE",
            "error": "滑块求解服务暂时不可用，请稍后重试或改为手动处理",
            "durationMs": int((time.time() - started) * 1000),
        }

    duration_ms = int((time.time() - started) * 1000)
    new_cookie_str = data.get("cookieStr") or ""
    merged_cookie = ""

    # 退避状态：成功清零 / 失败累加（含 captchaDetected 但未通过）
    if data.get("ok") and data.get("solved"):
        await record_solve_success(account_id)
    else:
        await record_solve_failure(
            account_id,
            error=data.get("error") or "滑块验证未通过",
        )

    # 如果过滑块成功且获取了新 Cookie，增量合并到数据库
    # 关键：浏览器中的 Cookie 包含 cna/isg/x5sec 等 JS 写入的风控字段，
    # 这些字段是 requests 无法获取的。必须合并回数据库才能让 Token API 通过。
    if data.get("ok") and data.get("solved") and new_cookie_str:
        merged_cookie = _merge_cookies(cookie_str, new_cookie_str)
        if merged_cookie and merged_cookie != cookie_str:
            try:
                merged_encrypted = encrypt_cookie_for_storage(merged_cookie)
                async with async_session() as db:
                    await db.execute(
                        text(
                            "UPDATE xianyu_account_auth "
                            "SET encrypted_cookie = :enc, updated_time = NOW() "
                            "WHERE account_id = :aid AND COALESCE(deleted, 0) = 0"
                        ),
                        {"enc": merged_encrypted, "aid": account_id},
                    )
                    await db.commit()
                logger.info(
                    "滑块求解后 Cookie 已合并并保存到数据库 accountId=%d newFields=%d",
                    account_id,
                    len(new_cookie_str.split(";")),
                )
            except Exception as exc:
                logger.error(
                    "保存合并后的 Cookie 失败 errorType=%s",
                    type(exc).__name__,
                )

    return {
        "success": bool(data.get("ok")),
        "solved": bool(data.get("solved")),
        "captchaDetected": bool(data.get("captchaDetected")),
        "attempts": int(data.get("attempts") or 0),
        "errorCode": "" if data.get("ok") else "CAPTCHA_SOLVE_FAILED",
        "error": data.get("error"),
        "screenshotPath": data.get("screenshotPath"),
        "durationMs": duration_ms,
        "cookieStr": new_cookie_str if data.get("ok") else "",
        "mergedCookie": merged_cookie,
    }


# ============================================================
# 4. 综合处理：检测 + 通知 + 自动求解 + 记录 + SSE
# ============================================================
async def _update_cookie_status_for_captcha(
    account_id: int,
    cookie_status: int,
    status_code: str,
    status_message: str,
) -> None:
    """滑块求解过程中同步更新 Cookie 状态（xianyu_account_auth + xianyu_account_runtime）。

    同时通过 SSE 广播 cookie_status_changed 事件，让前端账号列表实时刷新 Cookie 状态列。

    Args:
        cookie_status: 0=不可用/验证中, 1=可用
        status_code: last_login_status_code（VERIFYING/SESSION_EXPIRED/CAPTCHA_REQUIRED/OK）
        status_message: last_login_status_message

    注意：滑块求解失败不再使用 CAPTCHA_FAILED 状态码，因为这会造成假性"需验证"
    并阻断 WS 连接。求解失败时保持 cookie_status 不变，由统一登录校验确认真实状态。
    """
    try:
        async with async_session() as db:
            for table in ("xianyu_account_auth", "xianyu_account_runtime"):
                await db.execute(
                    text(
                        f"UPDATE {table} SET cookie_status = :cs, "
                        f"last_login_status_code = :sc, "
                        f"last_login_status_message = :sm, "
                        f"last_login_check_time = NOW(), updated_time = NOW() "
                        f"WHERE account_id = :aid"
                    ),
                    {
                        "cs": cookie_status,
                        "sc": status_code,
                        "sm": status_message,
                        "aid": account_id,
                    },
                )
            await db.commit()
    except Exception:
        logger.warning(
            "update_cookie_status_for_captcha 失败 accountId=%d",
            account_id,
            exc_info=True,
        )

    # 通过 SSE 广播 cookie_status_changed 事件，前端账号列表实时刷新 Cookie 状态列
    try:
        from .ws_sse import broadcaster
        await broadcaster.broadcast("cookie_status_changed", {
            "accountId": account_id,
            "cookieStatus": cookie_status,
            "loginStatusCode": status_code,
            "loginStatusMessage": status_message,
        })
        logger.info(
            "SSE 已广播 cookie_status_changed（滑块求解）: accountId=%d, status=%d, code=%s",
            account_id, cookie_status, status_code,
        )
    except Exception:
        logger.warning(
            "broadcast_captcha_cookie_status 失败 accountId=%d",
            account_id,
            exc_info=True,
        )


async def _verify_cookie_via_token_api(account_id: int) -> bool:
    """滑块求解成功后，调用 Token API 二次验证 Cookie 是否真正可用。

    滑块求解器在 Cookie Session 已过期时会误报成功（页面跳转到登录页，
    没有滑块组件）。此处通过 Token API 的实际响应来确认 Cookie 真实可用性。

    Args:
        account_id: 账号 ID
    Returns:
        True: Cookie 可用，Token API 返回 SUCCESS
        False: Cookie 已过期（FAIL_SYS_SESSION_EXPIRED）或不可用
    """
    try:
        async with async_session() as db:
            row = (await db.execute(
                text(
                    "SELECT encrypted_cookie, encrypted_token "
                    "FROM xianyu_account_auth "
                    "WHERE account_id = :aid AND COALESCE(deleted, 0) = 0 LIMIT 1"
                ),
                {"aid": account_id},
            )).mappings().first()
        if not row:
            logger.warning("_verify_cookie_via_token_api: 账号不存在 accountId=%d", account_id)
            return False

        cookie_str = decrypt_cookie_if_needed(row["encrypted_cookie"])
        if not cookie_str:
            logger.warning("_verify_cookie_via_token_api: Cookie 为空 accountId=%d", account_id)
            return False

        m_h5_tk = None
        if row["encrypted_token"]:
            m_h5_tk = decrypt_cookie_if_needed(row["encrypted_token"])

        # 调用 ws_token 模块的完整 Token 获取流程
        # （会自动尝试 cookie 中的 _m_h5_tk、DB 中的 _m_h5_tk、刷新 _m_h5_tk 三种路径）
        # 单租户版 ws_token.get_ws_token_with_refreshed_m_h5_tk 为同步函数，无需 await
        from .ws_token import get_ws_token_with_refreshed_m_h5_tk
        access_token, _, error_type, _ = get_ws_token_with_refreshed_m_h5_tk(cookie_str, m_h5_tk)
        if access_token:
            logger.info(
                "_verify_cookie_via_token_api: Cookie 验证通过 accountId=%d, accessToken长度=%d",
                account_id, len(access_token),
            )
            return True
        logger.warning(
            "_verify_cookie_via_token_api: Cookie 不可用 accountId=%d, error_type=%s",
            account_id, error_type,
        )
        return False
    except Exception:
        logger.error("_verify_cookie_via_token_api 异常 accountId=%d", account_id, exc_info=True)
        return False


async def handle_captcha_for_account(
    account_id: int,
    response: dict | str | None = None,
    auto_solve: bool = False,
    trigger_scene: str = "manual",
    open_reason: str = "",
    solve_reason: str = "",
    headless: Optional[bool] = None,
) -> dict:
    """综合处理账号的滑块验证场景。

    1. 如果提供了 response，先检测是否真的需要滑块
    2. 如果检测到，写入 cookie_status=0 并通知用户
    3. 如果 auto_solve=True，尝试自动求解
    4. 自动求解成功后，刷新 token 并恢复 cookie_status

    Args:
        trigger_scene: 触发场景 (ws_connect/cookie_keepalive/heartbeat_stop/token_refresh/manual)，
                       用于写入求解记录和 SSE 广播
        open_reason: 开启原因（为什么打开滑块求解流程）
        solve_reason: 求解原因（为什么进行滑块求解，具体业务原因）
        headless: 是否无头模式运行 Playwright。
                  None=沿用 try_auto_solve 默认（True，无头，不弹窗）；
                  False=有头模式，会弹出 Chrome 窗口便于人工观察；
                  True=强制无头（与默认相同）。
                  注意：crawler-service 端的 CRAWLER_FORCE_HEADLESS 若为 true
                  会强制覆盖此参数为无头模式。

    Returns:
        {
            "detected": bool,
            "captchaUrl": Optional[str],
            "instructions": dict,
            "autoSolveResult": Optional[dict],
            "recovered": bool,  # 是否成功恢复
        }
    """
    from .notify_dispatcher import notify_captcha_required
    from .captcha_backoff import assert_auto_solve_allowed
    from .captcha_solve_record import (
        broadcast_captcha_solve,
        create_solve_record,
        update_solve_record,
        _lookup_account_name,
    )

    detected = False
    captcha_url = None
    if response is not None:
        result = detect_captcha_from_response(response)
        detected = result.detected
        captcha_url = result.captcha_url

    instructions = build_captcha_instructions(account_id, captcha_url)
    auto_solve_result = None
    recovered = False
    solve_record_id = None

    if detected or auto_solve:
        # 通知用户
        try:
            await notify_captcha_required(
                account_id,
                scene=f"账号触发滑块验证（自动求解={'开启' if auto_solve else '关闭'}）",
            )
        except Exception:
            logger.debug("notify_captcha_required 异常，忽略", exc_info=True)

    if auto_solve and (detected or response is None):
        logger.info("开始为账号 %d 自动求解滑块 (scene=%s)", account_id, trigger_scene)

        # 先查指数退避：冷却中则直接落库失败记录，不启动浏览器
        account_name = await _lookup_account_name(account_id)
        blocked = await assert_auto_solve_allowed(account_id, force=False)
        if blocked:
            solve_record_id = await create_solve_record(
                account_id, trigger_scene=trigger_scene,
                open_reason=open_reason or "全自动冷却拦截",
                solve_reason=solve_reason or blocked.get("error") or "指数退避冷却中",
            )
            await update_solve_record(
                solve_record_id, status="fail", result="slider_fail",
                error_message=blocked.get("error") or "全自动滑块冷却中",
                engine="Backoff",
            )
            await broadcast_captcha_solve(
                account_id, account_name,
                status="fail", result="slider_fail",
                reason=blocked.get("error") or "全自动滑块冷却中",
                record_id=solve_record_id,
            )
            return {
                "detected": detected,
                "captchaUrl": captcha_url,
                "instructions": {
                    "title": instructions.title,
                    "steps": instructions.steps,
                    "message": instructions.message,
                    "autoSolveAvailable": instructions.auto_solve_available,
                    "manualFallbackUrl": instructions.manual_fallback_url,
                },
                "autoSolveResult": blocked,
                "recovered": False,
            }

        # 创建求解记录 + 广播"求解中"状态
        solve_record_id = await create_solve_record(
            account_id, trigger_scene=trigger_scene,
            open_reason=open_reason, solve_reason=solve_reason,
        )
        await broadcast_captcha_solve(
            account_id, account_name,
            status="retrying", reason=f"正在求解滑块（{trigger_scene}）",
            record_id=solve_record_id,
        )

        # 仅更新状态消息为"验证中"，不改变 cookie_status
        # 避免在求解尚未完成前误判 Cookie 失效，导致前端显示异常
        # cookie_status 的最终变更由求解结果决定
        try:
            async with async_session() as db:
                # 读取当前 cookie_status，保持不变
                row = (await db.execute(
                    text("SELECT cookie_status FROM xianyu_account_auth WHERE account_id = :aid LIMIT 1"),
                    {"aid": account_id},
                )).first()
                current_cookie_status = int(row[0]) if row else 1
            await _update_cookie_status_for_captcha(
                account_id,
                cookie_status=current_cookie_status,
                status_code="VERIFYING",
                status_message=f"正在自动求解滑块验证（{trigger_scene}）",
            )
        except Exception:
            logger.warning("更新 VERIFYING 状态失败 accountId=%d", account_id, exc_info=True)

        # 远程滑块求解开关：开启时调用商业版远程 API，否则使用本地 crawler-service
        from .remote_slider_config import is_remote_slider_enabled
        use_remote = await is_remote_slider_enabled()
        solve_engine = "Remote" if use_remote else "Playwright"
        if use_remote:
            from .remote_slider_solver import try_remote_solve
            auto_solve_result = await try_remote_solve(
                account_id,
                trigger_scene=trigger_scene,
                open_reason=open_reason,
                solve_reason=solve_reason,
            )
        else:
            # 仅当显式指定 headless 时透传，否则使用 try_auto_solve 默认（True 无头）
            solve_kwargs = {"account_id": account_id}
            if headless is not None:
                solve_kwargs["headless"] = headless
            auto_solve_result = await try_auto_solve(**solve_kwargs)

        if auto_solve_result.get("solved"):
            logger.info("账号 %d 滑块自动求解成功", account_id)

            # === 关键二次验证：调用 Token API 确认 Cookie 真实可用 ===
            # 滑块求解器在 Cookie Session 已过期时会误报成功（页面跳转到登录页，
            # 没有滑块组件）。此处用 Token API 做最终验证，避免错误恢复 cookie_status=1
            # 导致后续 ws_client 校验失败。
            # 手动触发的求解：跳过 Token API 二次验证
            # 原因：用户刚重新扫码后 Cookie 是新鲜的，但 _m_h5_tk 可能尚未生成，
            # Token API 会因此返回失败，导致误判 Cookie 过期，错误地更改状态。
            # 手动触发场景下，信任求解器结果即可。
            skip_token_verify = trigger_scene in ("manual", "manual_retry")
            cookie_valid = True
            if not skip_token_verify:
                cookie_valid = await _verify_cookie_via_token_api(account_id)
            else:
                logger.info(
                    "手动触发滑块求解，跳过 Token API 二次验证 accountId=%d", account_id,
                )
            if not cookie_valid:
                logger.warning(
                    "账号 %d 滑块求解器报告成功，但 Token API 验证 Cookie 仍不可用，"
                    "Cookie Session 已真正过期，需要用户重新扫码登录",
                    account_id,
                )
                # 不恢复 cookie_status=1，保持 0 状态，并附带明确错误信息
                await _update_cookie_status_for_captcha(
                    account_id,
                    cookie_status=0,
                    status_code="SESSION_EXPIRED",
                    status_message="Cookie Session 已过期，请重新扫码登录闲鱼账号",
                )

                # Cookie 已失效，主动断开 WS 连接，避免前端显示"WS 在线但 Cookie 失败"的矛盾状态
                try:
                    from .ws_client import ws_manager
                    await ws_manager.stop_client(account_id)
                    logger.info("Cookie 验证失败，已断开 WS 连接 accountId=%d", account_id)
                except Exception:
                    logger.warning(
                        "stop_captcha_ws_client 失败 accountId=%d",
                        account_id,
                        exc_info=True,
                    )

                # 在 auto_solve_result 中附加验证失败标记，让前端能展示真实原因
                auto_solve_result["cookieVerified"] = False
                auto_solve_result["error"] = (
                    "滑块已通过，但 Cookie Session 已真正过期（FAIL_SYS_SESSION_EXPIRED），"
                    "请前往账号管理页重新扫码登录闲鱼账号获取新 Cookie"
                )
                # 更新求解记录为失败 + 广播失败事件
                await update_solve_record(
                    solve_record_id, status="fail", result="slider_success",
                    error_message="Cookie Session 已过期，需重新扫码登录",
                    retry_count=int(auto_solve_result.get("attempts") or 0),
                    duration_ms=int(auto_solve_result.get("durationMs") or 0),
                    screenshot_path=str(auto_solve_result.get("screenshotPath") or ""),
                    engine=solve_engine,
                )
                await broadcast_captcha_solve(
                    account_id, account_name,
                    status="fail", result="slider_success",
                    reason="滑块已通过但 Cookie Session 已过期，需重新扫码登录",
                    record_id=solve_record_id,
                )
                # 不标记 recovered=True，让前端引导用户重新登录
            else:
                logger.info("账号 %d Cookie 二次验证通过，恢复 cookie_status=1", account_id)
                recovered = True

                # 滑块验证已通过，清除账号 Cookie 失效通知去重标记，
                # 以便下次再次失效时能重新发送通知。
                try:
                    from .notify_dispatcher import clear_cookie_expired_state
                    await clear_cookie_expired_state(account_id)
                except Exception:
                    pass

                # 恢复 cookie_status=1，last_login_status_code 必须为 'OK'
                # （_load_ws_credentials 校验要求 login_status_code == "OK"）
                await _update_cookie_status_for_captcha(
                    account_id,
                    cookie_status=1,
                    status_code="OK",
                    status_message="滑块验证已通过（自动求解+Token API 二次验证）",
                )

                # 触发 _m_h5_tk 刷新（不触发 ws_token 刷新，避免 Session 过期时 ws_client 覆盖 cookie_status=0）
                try:
                    from .cookie_token_refresher import force_refresh_account
                    await force_refresh_account(account_id, "mh5tk")
                except Exception:
                    logger.warning(
                        "refresh_captcha_account_token 失败 accountId=%d",
                        account_id,
                        exc_info=True,
                    )

                # 更新求解记录为成功 + 广播成功事件
                # 成功时不把 duration/screenshot 写入 error_message，避免前端误判为失败信息
                await update_solve_record(
                    solve_record_id, status="success", result="slider_success",
                    retry_count=int(auto_solve_result.get("attempts") or 0),
                    engine=solve_engine,
                    error_message=(
                        f"[durationMs={int(auto_solve_result.get('durationMs') or 0)}] 滑块求解成功"
                    ),
                )
                await broadcast_captcha_solve(
                    account_id, account_name,
                    status="success", result="slider_success",
                    reason="滑块求解成功，Cookie 已恢复",
                    record_id=solve_record_id,
                )
        else:
            # 滑块求解失败
            logger.warning("账号 %d 滑块自动求解失败", account_id)
            error_msg = auto_solve_result.get("error") or "滑块验证未通过"

            # 关键修复：滑块求解失败不代表 Cookie 失效。
            # 求解失败可能由 crawler-service 不可用、Playwright 异常、网络问题等导致，
            # 与 Cookie 本身有效性无关。直接置 cookie_status=0 + status_code=CAPTCHA_FAILED
            # 会造成假性"需验证"，并阻断 WS 连接（_load_ws_credentials 要求
            # login_status_code == "OK"）。
            #
            # 对于手动触发的求解（manual/manual_retry）：调用统一登录校验
            # （check_cookie_login → hasLogin API）确认 Cookie 真实状态，
            # 而不是直接信任缓存的 cookie_status。避免因历史残留的 cookie_status=0
            # 造成假性"Cookie 已失效"提示。hasLogin API 不依赖 _m_h5_tk，
            # 规避了手动场景下 Token API 误判过期的风险。
            #
            # 对于自动触发的求解：保持 cookie_status 不变，避免引入 30s 延迟。
            # Cookie 真实状态由后续的统一登录校验或 WS 重连确认。
            is_manual_trigger = trigger_scene in ("manual", "manual_retry")
            login_check_confirmed = False
            cookie_actually_valid = False
            if is_manual_trigger:
                try:
                    from .cookie_token_refresher import check_cookie_login
                    login_check = await check_cookie_login(account_id)
                    login_check_confirmed = login_check.confirmed
                    cookie_actually_valid = login_check.authenticated
                    logger.info(
                        "手动求解失败后执行统一登录校验 accountId=%d "
                        "confirmed=%s authenticated=%s code=%s",
                        account_id, login_check.confirmed,
                        login_check.authenticated, login_check.code,
                    )
                except Exception:
                    logger.warning(
                        "手动求解失败后登录校验异常 accountId=%d",
                        account_id, exc_info=True,
                    )

            if login_check_confirmed:
                # 登录校验已确认 Cookie 真实状态（check_cookie_login 已更新 DB + SSE）
                # 此处仅补充包含滑块求解失败上下文的状态消息，避免覆盖已确认的状态码
                if cookie_actually_valid:
                    await _update_cookie_status_for_captcha(
                        account_id,
                        cookie_status=1,
                        status_code="OK",
                        status_message=(
                            f"滑块求解失败，但经统一登录校验 Cookie 状态正常：{error_msg}"
                            f"（不影响消息接收，可稍后重试或改为手动处理）"
                        ),
                    )
                else:
                    await _update_cookie_status_for_captcha(
                        account_id,
                        cookie_status=0,
                        status_code="SESSION_EXPIRED",
                        status_message=(
                            f"滑块求解失败，且经统一登录校验确认 Cookie 已失效：{error_msg}"
                            f"（建议重新扫码登录闲鱼账号）"
                        ),
                    )
            else:
                # 未执行登录校验（自动触发）或校验未确认（超时/上游不可用）：
                # 回退到读取缓存状态，仅恢复 VERIFYING 之前的状态码并记录失败原因
                try:
                    async with async_session() as db:
                        row = (await db.execute(
                            text(
                                "SELECT cookie_status FROM xianyu_account_auth "
                                "WHERE account_id = :aid LIMIT 1"
                            ),
                            {"aid": account_id},
                        )).first()
                    current_cookie_status = int(row[0]) if row else 1
                    if current_cookie_status == 1:
                        # Cookie 仍可用：恢复 status_code="OK"，确保 WS 可正常连接
                        await _update_cookie_status_for_captcha(
                            account_id,
                            cookie_status=1,
                            status_code="OK",
                            status_message=(
                                f"滑块求解失败，但 Cookie 状态正常：{error_msg}"
                                f"（可稍后重试或改为手动处理，不影响消息接收）"
                            ),
                        )
                    else:
                        # Cookie 原本就不可用：保持失效状态，仅更新失败原因
                        await _update_cookie_status_for_captcha(
                            account_id,
                            cookie_status=0,
                            status_code="SESSION_EXPIRED",
                            status_message=(
                                f"滑块求解失败：{error_msg}"
                                f"（Cookie 可能已失效，建议重新扫码登录）"
                            ),
                        )
                except Exception:
                    logger.warning(
                        "恢复 Cookie 状态失败 accountId=%d", account_id, exc_info=True,
                    )
            await update_solve_record(
                solve_record_id, status="fail", result="slider_fail",
                error_message=error_msg,
                retry_count=int(auto_solve_result.get("attempts") or 0),
                duration_ms=int(auto_solve_result.get("durationMs") or 0),
                screenshot_path=str(auto_solve_result.get("screenshotPath") or ""),
                engine=solve_engine,
            )
            await broadcast_captcha_solve(
                account_id, account_name,
                status="fail", result="slider_fail",
                reason=error_msg,
                record_id=solve_record_id,
            )

    return {
        "detected": detected,
        "captchaUrl": captcha_url,
        "instructions": {
            "title": instructions.title,
            "steps": instructions.steps,
            "message": instructions.message,
            "autoSolveAvailable": instructions.auto_solve_available,
            "manualFallbackUrl": instructions.manual_fallback_url,
        },
        "autoSolveResult": auto_solve_result,
        "recovered": recovered,
    }
