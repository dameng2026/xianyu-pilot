"""
Cookie/Token 自动刷新调度器
============================

实现随机间隔刷新策略，避免被检测为机器人：

- Cookie 保活：每 30 分钟调用 hasLogin 接口
- _m_h5_tk：1.5-2.5 小时随机刷新
- websocket_token：10-14 小时随机刷新
- 账号间隔：2-5 秒随机（避免同时刷新多个账号造成请求洪峰）

设计要点：
1. 三个独立的异步循环，各自维护下次刷新时间
2. 每账号独立 asyncio.Lock，避免并发刷新互相覆盖
3. 复用现有函数：check_login_status、refresh_m_h5_tk、get_ws_token_with_refreshed_m_h5_tk
4. 刷新成功后通知正在运行的 WS 客户端更新凭据
5. 刷新失败（滑块/过期）时更新 cookie_status 并触发通知
6. 整个调度器在 FastAPI lifespan 中启动，进程退出时自动停止
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from sqlalchemy import text

from ..core.cookie_crypto import decrypt_cookie_if_needed, encrypt_cookie_for_storage
from ..core.database import async_session
from ..core.background_tasks import spawn_background_task
from .notify_dispatcher import (
    clear_cookie_expired_state,
    notify_cookie_expired,
    notify_captcha_required,
)

logger = logging.getLogger(__name__)


async def _try_auto_solve_from_cookie_keepalive(account_id: int) -> None:
    """Cookie 保活触发滑块验证时，后台自动发起滑块求解。

    使用与 ws_client 相同的 10 分钟去重窗口（共享 captcha_solver 中的状态），
    避免同账号多个触发源同时发起求解。
    """
    from .captcha_solver import (
        should_auto_solve,
        mark_auto_solve_started,
        handle_captcha_for_account,
    )

    if not should_auto_solve(account_id):
        logger.info(
            "Cookie 保活触发滑块求解去重跳过 accountId=%d（10 分钟内已执行过）",
            account_id,
        )
        return
    mark_auto_solve_started(account_id)

    logger.info("Cookie 保活触发自动滑块求解 accountId=%d", account_id)
    try:
        result = await handle_captcha_for_account(
            account_id=account_id,
            response=None,
            auto_solve=True,
            trigger_scene="cookie_keepalive",
            open_reason="Cookie 保活触发滑块验证，自动求解",
            solve_reason="Cookie 保活策略检测到滑块验证",
        )
        recovered = bool(result.get("recovered"))
        if recovered:
            logger.info(
                "Cookie 保活触发滑块求解成功，Cookie 已恢复 accountId=%d",
                account_id,
            )
        else:
            logger.warning(
                "Cookie 保活触发滑块求解未恢复 accountId=%d result=%s",
                account_id,
                result.get("autoSolveResult", {}),
            )
    except Exception as exc:
        logger.warning(
            "Cookie 保活触发滑块求解异常 accountId=%d errorType=%s",
            account_id,
            type(exc).__name__,
        )


# ============================================================
# 刷新间隔配置（用户指定的随机间隔策略）
# ============================================================
COOKIE_KEEPALIVE_INTERVAL_SECONDS = 30 * 60  # 30 分钟
MH5TK_REFRESH_MIN_SECONDS = int(1.5 * 3600)  # 1.5 小时
MH5TK_REFRESH_MAX_SECONDS = int(2.5 * 3600)  # 2.5 小时
WS_TOKEN_REFRESH_MIN_SECONDS = 10 * 3600     # 10 小时
WS_TOKEN_REFRESH_MAX_SECONDS = 14 * 3600     # 14 小时
ACCOUNT_INTERVAL_MIN_SECONDS = 2             # 账号间隔最小 2 秒
ACCOUNT_INTERVAL_MAX_SECONDS = 5             # 账号间隔最大 5 秒

# 主循环检查周期（每 60 秒检查一次是否到刷新时间）
DISPATCHER_TICK_SECONDS = 60

# 单次刷新操作超时
SINGLE_REFRESH_TIMEOUT_SECONDS = 30


@dataclass
class AccountRefreshState:
    """单账号的刷新状态跟踪"""
    account_id: int
    # 三种刷新的下次执行时间戳（秒）
    next_cookie_keepalive: float = 0.0
    next_mh5tk_refresh: float = 0.0
    next_ws_token_refresh: float = 0.0
    # 最近一次刷新结果
    last_cookie_keepalive_ok: Optional[bool] = None
    last_mh5tk_refresh_ok: Optional[bool] = None
    last_ws_token_refresh_ok: Optional[bool] = None
    last_error: Optional[str] = None
    # 并发锁
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def schedule_next_cookie_keepalive(self) -> None:
        self.next_cookie_keepalive = time.time() + COOKIE_KEEPALIVE_INTERVAL_SECONDS

    def schedule_next_mh5tk_refresh(self) -> None:
        # 随机 1.5-2.5 小时
        delay = random.uniform(MH5TK_REFRESH_MIN_SECONDS, MH5TK_REFRESH_MAX_SECONDS)
        self.next_mh5tk_refresh = time.time() + delay

    def schedule_next_ws_token_refresh(self) -> None:
        # 随机 10-14 小时
        delay = random.uniform(WS_TOKEN_REFRESH_MIN_SECONDS, WS_TOKEN_REFRESH_MAX_SECONDS)
        self.next_ws_token_refresh = time.time() + delay


@dataclass(frozen=True)
class CookieLoginCheck:
    """A platform-observed login result, separate from cached DB state."""

    confirmed: bool
    authenticated: bool
    code: str
    message: str
    checked_at: Optional[datetime] = None


# ============================================================
# 全局状态
# ============================================================
_states: dict[int, AccountRefreshState] = {}
_states_lock = asyncio.Lock()
_dispatcher_task: Optional[asyncio.Task] = None
_dispatcher_running = False
_dispatcher_health_status = "unavailable"


async def _load_active_accounts() -> list[dict]:
    """加载所有启用的账号（含 cookie，未删除，cookie_status=1）"""
    async with async_session() as db:
        result = await db.execute(
            text(
                """
                SELECT a.id AS account_id, a.external_uid AS unb,
                       auth.encrypted_cookie, auth.encrypted_token, auth.cookie_status,
                       auth.last_login_status_code
                FROM xianyu_account a
                JOIN xianyu_account_auth auth
                  ON auth.account_id = a.id
                WHERE a.deleted = 0
                  AND auth.deleted = 0
                  AND auth.encrypted_cookie IS NOT NULL
                  AND auth.encrypted_cookie != ''
                  AND auth.cookie_status = 1
                ORDER BY a.id ASC
                """
            )
        )
        rows = result.mappings().all()
        return [dict(r) for r in rows]


async def _refresh_states() -> None:
    """从数据库同步账号列表，新增/移除刷新状态"""
    global _states
    accounts = await _load_active_accounts()
    seen_ids = set()

    async with _states_lock:
        for acc in accounts:
            aid = int(acc["account_id"])
            seen_ids.add(aid)
            if aid not in _states:
                state = AccountRefreshState(
                    account_id=aid,
                )
                # 新账号立即排一次 Cookie 保活，_m_h5_tk 和 ws_token 延后排
                state.next_cookie_keepalive = time.time() + random.uniform(0, 60)
                state.schedule_next_mh5tk_refresh()
                state.schedule_next_ws_token_refresh()
                _states[aid] = state
                logger.info("Cookie/Token 刷新调度器: 新增账号 accountId=%d", aid)
            else:
                pass  # 账号已存在，无需操作
        # 移除已禁用/删除的账号
        removed = [aid for aid in list(_states.keys()) if aid not in seen_ids]
        for aid in removed:
            _states.pop(aid, None)
            logger.info("Cookie/Token 刷新调度器: 移除账号 accountId=%d", aid)


async def _update_cookie_and_token_in_db(
    account_id: int,
    cookie_str: Optional[str] = None,
    m_h5_tk: Optional[str] = None,
) -> None:
    """将刷新后的 cookie / _m_h5_tk 写回数据库（同时更新时间戳）"""
    try:
        async with async_session() as db:
            if cookie_str and m_h5_tk:
                await db.execute(
                    text(
                        "UPDATE xianyu_account_auth SET encrypted_cookie = :cookie, "
                        "encrypted_token = :tk, updated_time = NOW() "
                        "WHERE account_id = :aid"
                    ),
                    {
                        "cookie": encrypt_cookie_for_storage(cookie_str),
                        "tk": encrypt_cookie_for_storage(m_h5_tk),
                        "aid": account_id,
                        
                    },
                )
            elif cookie_str:
                await db.execute(
                    text(
                        "UPDATE xianyu_account_auth SET encrypted_cookie = :cookie, updated_time = NOW() "
                        "WHERE account_id = :aid"
                    ),
                    {
                        "cookie": encrypt_cookie_for_storage(cookie_str),
                        "aid": account_id,
                        
                    },
                )
            elif m_h5_tk:
                await db.execute(
                    text(
                        "UPDATE xianyu_account_auth SET encrypted_token = :tk, updated_time = NOW() "
                        "WHERE account_id = :aid"
                    ),
                    {
                        "tk": encrypt_cookie_for_storage(m_h5_tk),
                        "aid": account_id,
                        
                    },
                )
            await db.commit()
    except Exception:
        logger.warning("更新 DB 中的 cookie/token 失败 accountId=%d", account_id, exc_info=True)


async def _update_cookie_status(
    account_id: int,
    status: int,
    code: Optional[str] = None,
    message: Optional[str] = None,
) -> bool:
    """同步更新 xianyu_account_auth 和 xianyu_account_runtime 两张表的状态"""
    try:
        async with async_session() as db:
            for table in ("xianyu_account_auth", "xianyu_account_runtime"):
                await db.execute(
                    text(
                        f"UPDATE {table} SET cookie_status = :st, "
                        f"last_login_status_code = :code, last_login_status_message = :message, "
                        f"last_login_check_time = NOW(), updated_time = NOW() "
                        f"WHERE account_id = :aid AND COALESCE(deleted, 0) = 0"
                    ),
                    {
                        "st": status,
                        "code": code,
                        "message": message,
                        "aid": account_id,
                        
                    },
                )
            await db.commit()
        return True
    except Exception:
        logger.warning("更新 cookie_status 失败 accountId=%d", account_id, exc_info=True)
        return False


def _notify_ws_client_credentials_updated(
    account_id: int,
    cookie_str: Optional[str] = None,
    m_h5_tk: Optional[str] = None,
) -> None:
    """通知正在运行的 WS 客户端更新凭据（避免下次重连仍用旧值）"""
    try:
        # 延迟导入避免循环依赖
        from .ws_client import ws_manager
        client = ws_manager.get_client(account_id)
        if client is None:
            return
        if cookie_str:
            client.cookie_str = cookie_str
        if m_h5_tk:
            client.m_h5_tk = m_h5_tk
        logger.info("已通知 WS 客户端更新凭据 accountId=%d", account_id)
    except Exception as exc:
        logger.debug(
            "通知 WS 客户端更新凭据失败 accountId=%d errorType=%s",
            account_id,
            type(exc).__name__,
        )


# ============================================================
# 三个刷新任务
# ============================================================
async def _do_cookie_keepalive(state: AccountRefreshState) -> bool:
    """Cookie 保活：直接 POST 到 passport.goofish.com/newlogin/hasLogin.do

    注：早期实现调用 mtop.taobao.idle.user.hasLogin MTOP API，但该端点
    返回 FAIL_SYS_API_NOT_FOUNDED（不存在）。当前兼容流程直接
    POST 表单到 https://passport.goofish.com/newlogin/hasLogin.do 即可
    完成保活并刷新 Cookie（响应 Set-Cookie 含新的 _m_h5_tk 等）。
    """
    try:
        result = await asyncio.wait_for(
            _call_has_login(state.account_id),
            timeout=SINGLE_REFRESH_TIMEOUT_SECONDS,
        )
        if result.get("success"):
            logger.info("Cookie 保活成功 accountId=%d", state.account_id)
            state.last_cookie_keepalive_ok = True
            state.last_error = None
            # 保活成功时恢复 cookie_status=1，避免之前被设为 0 的状态无法恢复
            # （_load_ws_credentials 校验要求 cookie_status==1 且 login_status_code=="OK"）
            try:
                status_persisted = await _update_cookie_status(
                    state.account_id, 1,
                    "OK", "Cookie 保活成功",
                )
                if not status_persisted:
                    state.last_cookie_keepalive_ok = False
                    state.last_error = "平台登录已验证，但本地状态保存失败"
                    return False
                await clear_cookie_expired_state(state.account_id)
            except Exception:
                logger.warning(
                    "Cookie recovery notification state failed accountId=%d",
                    state.account_id,
                    exc_info=True,
                )
            return True
        else:
            err = result.get("error", "未知错误")
            logger.warning("Cookie 保活失败 accountId=%d", state.account_id)
            state.last_cookie_keepalive_ok = False
            state.last_error = err
            err_str = str(err)
            if "CAPTCHA_NEEDED" in err_str or "FAIL_SYS_USER_VALIDATE" in err_str or "RGV587" in err_str:
                await _update_cookie_status(
                    state.account_id, 0,
                    "COOKIE_EXPIRED", "Cookie 保活触发滑块验证，需要人工处理",
                )
                try:
                    await notify_captcha_required(state.account_id, err_str)
                except Exception:
                    pass
                # 如果用户启用了"Cookie 保活策略"触发场景，后台发起自动滑块求解
                try:
                    from .remote_slider_config import (
                        TRIGGER_SCENE_COOKIE_KEEPALIVE,
                        is_trigger_scene_enabled,
                    )
                    if await is_trigger_scene_enabled(TRIGGER_SCENE_COOKIE_KEEPALIVE):
                        logger.info(
                            "Cookie 保活检测到滑块验证，触发自动求解 accountId=%d",
                            state.account_id,
                        )
                        spawn_background_task(
                            _try_auto_solve_from_cookie_keepalive(state.account_id),
                            name=f"cookie-keepalive.auto-solve:{state.account_id}",
                        )
                except Exception as exc:
                    logger.debug(
                        "Cookie 保活触发场景检查异常 errorType=%s",
                        type(exc).__name__,
                    )
            elif "SESSION_EXPIRED" in err_str or "登入失败" in err_str:
                await _update_cookie_status(
                    state.account_id, 0,
                    "COOKIE_EXPIRED", "Cookie 会话已过期，请重新登录",
                )
                try:
                    await notify_cookie_expired(state.account_id, 0)
                except Exception:
                    pass
            return False
    except asyncio.TimeoutError:
        logger.warning("Cookie 保活超时 accountId=%d", state.account_id)
        state.last_cookie_keepalive_ok = False
        state.last_error = "保活超时"
        return False
    except Exception:
        logger.error("Cookie 保活异常 accountId=%d", state.account_id, exc_info=True)
        state.last_cookie_keepalive_ok = False
        state.last_error = "Cookie 保活过程异常，请稍后重试"
        return False


async def _call_has_login(account_id: int) -> dict:
    """直接调用 passport.goofish.com 的 hasLogin 接口保活 Cookie。

    表单字段来自经过权属审查的旧版兼容协议；公开发布前仍须在源码权属证据中确认。
    成功后从响应 Set-Cookie 提取新 Cookie 持久化到数据库。
    """
    import re
    import httpx

    # 1. 从数据库读取账号 Cookie
    async with async_session() as db:
        row = await db.execute(
            text("""
                SELECT a.external_uid AS unb,
                       auth.encrypted_cookie AS encrypted_cookie
                FROM xianyu_account a
                JOIN xianyu_account_auth auth
                  ON auth.account_id = a.id
                WHERE a.id = :account_id
                  AND a.deleted = 0 AND COALESCE(auth.deleted, 0) = 0
                LIMIT 1
            """),
            {"account_id": account_id},
        )
        rec = row.mappings().first()
        if not rec:
            return {"success": False, "error": "ACCOUNT_NOT_FOUND"}
        # decrypt_cookie_if_needed 在密钥不匹配/数据损坏时会抛 RuntimeError，
        # 必须在此处捕获，否则会冒泡到 check_cookie_login 的兜底 except，
        # 被误归类为 UPSTREAM_UNAVAILABLE（暗示网络/上游问题，实际是本地解密失败）。
        # 常见触发场景：账号从其他环境迁移过来，COOKIE_CRYPTO_SECRET 已变更。
        # 与 captcha_solver.try_auto_solve 中的处理保持一致。
        try:
            cookie_str = decrypt_cookie_if_needed(rec.get("encrypted_cookie") or "")
        except Exception as exc:
            logger.error(
                "统一登录校验 Cookie 解密失败 accountId=%d errorType=%s",
                account_id, type(exc).__name__,
            )
            return {"success": False, "error": "CREDENTIAL_MISSING"}
        if not cookie_str:
            return {"success": False, "error": "CREDENTIAL_MISSING"}
        unb = str(rec.get("unb") or "")

    # 2. 解析 cookie 为 dict
    cookie_dict: dict[str, str] = {}
    for part in cookie_str.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            cookie_dict[k.strip()] = v.strip()

    # 3. 构造旧版兼容表单
    form_data = {
        "appName": "xianyu",
        "fromSite": "77",
        "hid": unb,
        "ltl": "true",
        "appEntrance": "web",
        "_csrf_token": cookie_dict.get("XSRF-TOKEN", ""),
        "umidToken": "",
        "hsiz": cookie_dict.get("cookie2", ""),
        "bizParams": "taobaoBizLoginFrom=web",
        "mainPage": "false",
        "isMobile": "false",
        "lang": "zh_CN",
        "returnUrl": "",
        "isIframe": "true",
        "documentReferer": "https://www.goofish.com/",
        "defaultView": "hasLogin",
        "umidTag": "SERVER",
        "deviceId": cookie_dict.get("cna", ""),
    }

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://passport.goofish.com/",
        "Origin": "https://passport.goofish.com",
        "Cookie": cookie_str,
    }

    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False, trust_env=False) as client:
            resp = await client.post(
                "https://passport.goofish.com/newlogin/hasLogin.do",
                data=form_data,
                headers=headers,
            )
    except Exception:
        return {"success": False, "error": "UPSTREAM_UNAVAILABLE"}

    body_text = ""
    try:
        body_text = resp.text or ""
    except Exception:
        pass

    # 4. 检测风控
    risk_keywords = ["RGV587_ERROR", "FAIL_SYS_RGV587_ERROR", "被挤爆啦", "FAIL_SYS_USER_VALIDATE"]
    if any(kw in body_text for kw in risk_keywords):
        return {"success": False, "error": "CAPTCHA_NEEDED"}

    if resp.status_code != 200:
        return {"success": False, "error": "UPSTREAM_UNAVAILABLE"}

    # A reachable endpoint is not proof of an authenticated session. Only
    # explicit boolean confirmation from the hasLogin response is accepted.
    truthy = r'(?:true|"true"|1|"1")'
    falsy = r'(?:false|"false"|0|"0")'
    has_login_true = re.search(rf'["\']?hasLogin["\']?\s*[:=]\s*{truthy}', body_text, re.IGNORECASE)
    has_login_false = re.search(rf'["\']?hasLogin["\']?\s*[:=]\s*{falsy}', body_text, re.IGNORECASE)
    success_false = re.search(rf'["\']?success["\']?\s*[:=]\s*{falsy}', body_text, re.IGNORECASE)
    # loginResult:"success" 是新版 passport.goofish.com 的成功标识
    login_result_success = re.search(r'["\']?loginResult["\']?\s*[:=]\s*["\']success["\']', body_text, re.IGNORECASE)
    top_level_success = False
    try:
        parsed_body = resp.json()
        if isinstance(parsed_body, dict):
            # 兼容两种响应结构：
            # 1. 顶层 {"success": true, ...}
            # 2. 嵌套 {"content": {"success": true, ...}, "hasError": false}
            top_level_success = parsed_body.get("success") is True
            if not top_level_success:
                content = parsed_body.get("content")
                if isinstance(content, dict) and content.get("success") is True:
                    top_level_success = True
            # hasError:false 也是登录成功的信号
            if not top_level_success and parsed_body.get("hasError") is False:
                top_level_success = True
    except Exception:
        top_level_success = False
    session_failure_markers = ("SESSION_EXPIRED", "NOT_LOGIN", "LOGIN_REQUIRED", "登入失败", "登录失败")
    if has_login_false or success_false or any(marker in body_text for marker in session_failure_markers):
        return {"success": False, "error": "SESSION_EXPIRED"}
    if not (has_login_true or top_level_success or login_result_success):
        # 记录未识别的响应体，便于排查
        body_preview = body_text[:500] if body_text else ""
        logger.warning(
            "hasLogin 返回未识别的响应 accountId=%d status=%d bodyPreview=%r",
            account_id, resp.status_code, body_preview,
        )
        return {"success": False, "error": "UPSTREAM_UNCONFIRMED"}

    # 5. 解析新 Cookie（从 Set-Cookie 头）
    new_cookie_parts: list[str] = []
    set_cookies = resp.headers.get_list("set-cookie") if hasattr(resp.headers, "get_list") else []
    if not set_cookies and "set-cookie" in resp.headers:
        set_cookies = [resp.headers["set-cookie"]]

    for sc in set_cookies:
        # 取每个 Set-Cookie 的第一段 key=value
        first = sc.split(";")[0].strip()
        if "=" in first:
            new_cookie_parts.append(first)

    # 6. 合并新 Cookie 到原 cookie_str
    merged = dict(cookie_dict)
    for part in new_cookie_parts:
        k, v = part.split("=", 1)
        merged[k.strip()] = v.strip()

    new_cookie_str = "; ".join(f"{k}={v}" for k, v in merged.items())

    # 7. 持久化新 Cookie（如果有变化）
    cookie_changed = new_cookie_str != cookie_str
    if cookie_changed:
        try:
            encrypted = encrypt_cookie_for_storage(new_cookie_str)
            async with async_session() as db:
                await db.execute(
                    text("""
                        UPDATE xianyu_account_auth
                        SET encrypted_cookie = :cookie,
                            updated_time = NOW()
                        WHERE account_id = :account_id
                          AND COALESCE(deleted, 0) = 0
                    """),
                    {"cookie": encrypted, "account_id": account_id},
                )
                await db.commit()
            logger.info("hasLogin 成功并更新 Cookie accountId=%d changedKeys=%s",
                        account_id, [p.split("=")[0] for p in new_cookie_parts])
        except Exception:
            logger.warning("hasLogin 持久化新 Cookie 失败 accountId=%d", account_id, exc_info=True)

    return {"success": True, "cookieUpdated": cookie_changed}


async def check_cookie_login(
    account_id: int,
    *,
    timeout_seconds: float = SINGLE_REFRESH_TIMEOUT_SECONDS,
) -> CookieLoginCheck:
    """Check login against the platform without trusting cached DB flags.

    Only a confirmed success or a confirmed authentication rejection updates
    the persisted check timestamp. Transport errors, timeouts and ambiguous
    provider responses leave the last known state untouched.
    """

    try:
        result = await asyncio.wait_for(
            _call_has_login(account_id),
            timeout=max(float(timeout_seconds), 0.01),
        )
    except asyncio.TimeoutError:
        return CookieLoginCheck(
            confirmed=False,
            authenticated=False,
            code="CHECK_TIMEOUT",
            message="平台登录校验超时，当前状态无法确认。",
        )
    except Exception:
        logger.error("Platform login check failed accountId=%d", account_id)
        return CookieLoginCheck(
            confirmed=False,
            authenticated=False,
            code="UPSTREAM_UNAVAILABLE",
            message="平台登录校验暂不可用，当前状态无法确认。",
        )

    if result.get("success") is True:
        checked_at = datetime.now()
        status_persisted = await _update_cookie_status(
            account_id,
            1,
            "OK",
            "平台登录校验通过",
        )
        if status_persisted:
            try:
                await clear_cookie_expired_state(account_id)
            except Exception as exc:
                logger.warning(
                    "Cookie alert recovery clear failed accountId=%d errorType=%s",
                    account_id,
                    type(exc).__name__,
                )
        else:
            return CookieLoginCheck(
                confirmed=False,
                authenticated=False,
                code="LOCAL_STATE_UNAVAILABLE",
                message="平台登录已验证，但本地状态保存失败；当前仍不可用于自动化，请稍后重试。",
            )
        return CookieLoginCheck(
            confirmed=True,
            authenticated=True,
            code="OK",
            message="平台登录校验通过",
            checked_at=checked_at,
        )

    error_code = str(result.get("error") or "UPSTREAM_UNCONFIRMED").upper()
    if error_code == "CAPTCHA_NEEDED":
        checked_at = datetime.now()
        status_persisted = await _update_cookie_status(
            account_id,
            0,
            "CAPTCHA_REQUIRED",
            "平台要求完成安全验证",
        )
        if not status_persisted:
            return CookieLoginCheck(
                confirmed=False,
                authenticated=False,
                code="LOCAL_STATE_UNAVAILABLE",
                message="已确认平台要求安全验证，但本地隔离状态保存失败，请停止自动化并联系管理员。",
            )
        return CookieLoginCheck(
            confirmed=True,
            authenticated=False,
            code="CAPTCHA_REQUIRED",
            message="平台要求完成安全验证，请重新验证账号。",
            checked_at=checked_at,
        )
    if error_code == "SESSION_EXPIRED":
        checked_at = datetime.now()
        status_persisted = await _update_cookie_status(
            account_id,
            0,
            "SESSION_EXPIRED",
            "Cookie 会话已过期，请重新登录",
        )
        if not status_persisted:
            return CookieLoginCheck(
                confirmed=False,
                authenticated=False,
                code="LOCAL_STATE_UNAVAILABLE",
                message="已确认 Cookie 会话过期，但本地隔离状态保存失败，请停止自动化并联系管理员。",
            )
        return CookieLoginCheck(
            confirmed=True,
            authenticated=False,
            code="SESSION_EXPIRED",
            message="Cookie 会话已过期，请重新登录。",
            checked_at=checked_at,
        )
    if error_code == "ACCOUNT_NOT_FOUND":
        message = "账号不存在。"
    elif error_code == "CREDENTIAL_MISSING":
        message = "账号没有可校验的 Cookie。"
    elif error_code == "CHECK_TIMEOUT":
        message = "平台登录校验超时，当前状态无法确认。"
    else:
        message = "平台登录状态无法确认，可能是 Cookie 已失效或平台接口变更，请尝试更新 Cookie 或重新扫码登录。"
    return CookieLoginCheck(
        confirmed=False,
        authenticated=False,
        code=error_code,
        message=message,
    )


async def _do_mh5tk_refresh(state: AccountRefreshState, cookie_str: str) -> bool:
    """_m_h5_tk 刷新：1.5-2.5 小时随机"""
    from .ws_token import refresh_m_h5_tk

    try:
        new_cookie, new_m_h5_tk = await asyncio.wait_for(
            asyncio.to_thread(refresh_m_h5_tk, cookie_str),
            timeout=SINGLE_REFRESH_TIMEOUT_SECONDS,
        )
        if new_cookie and new_m_h5_tk:
            logger.info(
                "_m_h5_tk refresh succeeded accountId=%d credentialPresent=true",
                state.account_id,
            )
            # 写回 DB
            await _update_cookie_and_token_in_db(
                state.account_id, cookie_str=new_cookie, m_h5_tk=new_m_h5_tk,
            )
            # 通知 WS 客户端
            _notify_ws_client_credentials_updated(state.account_id, new_cookie, new_m_h5_tk)
            state.last_mh5tk_refresh_ok = True
            state.last_error = None
            return True
        else:
            logger.warning("_m_h5_tk 刷新失败 accountId=%d: 返回空", state.account_id)
            state.last_mh5tk_refresh_ok = False
            state.last_error = "_m_h5_tk 刷新返回空"
            return False
    except asyncio.TimeoutError:
        logger.warning("_m_h5_tk 刷新超时 accountId=%d", state.account_id)
        state.last_mh5tk_refresh_ok = False
        state.last_error = "_m_h5_tk 刷新超时"
        return False
    except Exception:
        logger.error("_m_h5_tk 刷新异常 accountId=%d", state.account_id, exc_info=True)
        state.last_mh5tk_refresh_ok = False
        state.last_error = "_m_h5_tk 刷新过程异常，请稍后重试"
        return False


async def _do_ws_token_refresh(state: AccountRefreshState, cookie_str: str, m_h5_tk: str) -> bool:
    """websocket_token 刷新：10-14 小时随机"""
    from .ws_token import get_ws_token_with_refreshed_m_h5_tk

    try:
        access_token, effective_m_h5_tk, error_type, refreshed_cookie = await asyncio.wait_for(
            asyncio.to_thread(get_ws_token_with_refreshed_m_h5_tk, cookie_str, m_h5_tk),
            timeout=SINGLE_REFRESH_TIMEOUT_SECONDS,
        )
        if access_token:
            logger.info(
                "WS token refresh succeeded accountId=%d credentialPresent=true",
                state.account_id,
            )
            # 如果刷新过程中 cookie 也被更新了，写回 DB
            if refreshed_cookie and refreshed_cookie != cookie_str:
                await _update_cookie_and_token_in_db(
                    state.account_id, cookie_str=refreshed_cookie, m_h5_tk=effective_m_h5_tk,
                )
                _notify_ws_client_credentials_updated(
                    state.account_id, refreshed_cookie, effective_m_h5_tk,
                )
            elif effective_m_h5_tk and effective_m_h5_tk != m_h5_tk:
                await _update_cookie_and_token_in_db(
                    state.account_id, m_h5_tk=effective_m_h5_tk,
                )
                _notify_ws_client_credentials_updated(state.account_id, None, effective_m_h5_tk)
            state.last_ws_token_refresh_ok = True
            state.last_error = None
            return True
        else:
            logger.warning(
                "WS Token 刷新失败 accountId=%d, error_type=%s",
                state.account_id, error_type,
            )
            state.last_ws_token_refresh_ok = False
            state.last_error = f"WS Token 刷新失败: {error_type}"
            # 滑块验证：标记账号需要人工处理
            if error_type == "captcha":
                await _update_cookie_status(
                    state.account_id, 0,
                    "COOKIE_EXPIRED", "WS Token 刷新触发滑块验证，需要人工处理",
                )
                try:
                    await notify_captcha_required(
                        state.account_id,
                        "WS Token 刷新触发滑块验证，请到闲鱼完成验证后重试",
                    )
                except Exception:
                    pass
            elif error_type == "expired":
                await _update_cookie_status(
                    state.account_id, 0,
                    "COOKIE_EXPIRED", "Cookie 已过期，请重新登录",
                )
                try:
                    await notify_cookie_expired(state.account_id, 0)
                except Exception:
                    pass
            return False
    except asyncio.TimeoutError:
        logger.warning("WS Token 刷新超时 accountId=%d", state.account_id)
        state.last_ws_token_refresh_ok = False
        state.last_error = "WS Token 刷新超时"
        return False
    except Exception:
        logger.error("WS Token 刷新异常 accountId=%d", state.account_id, exc_info=True)
        state.last_ws_token_refresh_ok = False
        state.last_error = "WS Token 刷新过程异常，请稍后重试"
        return False


# ============================================================
# 主调度循环
# ============================================================
async def _dispatcher_loop() -> None:
    """主调度循环：每 60 秒检查一次，到点则触发对应刷新"""
    global _dispatcher_running, _dispatcher_health_status
    logger.info(
        "Cookie/Token 刷新调度器启动: Cookie保活=%d分钟, _m_h5_tk=%.1f-%.1f小时, "
        "ws_token=%d-%d小时, 账号间隔=%d-%d秒",
        COOKIE_KEEPALIVE_INTERVAL_SECONDS // 60,
        MH5TK_REFRESH_MIN_SECONDS / 3600, MH5TK_REFRESH_MAX_SECONDS / 3600,
        WS_TOKEN_REFRESH_MIN_SECONDS // 3600, WS_TOKEN_REFRESH_MAX_SECONDS // 3600,
        ACCOUNT_INTERVAL_MIN_SECONDS, ACCOUNT_INTERVAL_MAX_SECONDS,
    )

    while _dispatcher_running:
        try:
            # 同步账号列表
            await _refresh_states()
            _dispatcher_health_status = "ok"

            now = time.time()
            async with _states_lock:
                states_snapshot = list(_states.values())

            # 按 account_id 排序，避免乱序
            states_snapshot.sort(key=lambda s: s.account_id)

            for state in states_snapshot:
                if not _dispatcher_running:
                    break

                # 跳过被锁定的账号（说明正在刷新中）
                if state.lock.locked():
                    continue

                # 判断是否需要刷新
                need_cookie = now >= state.next_cookie_keepalive
                need_mh5tk = now >= state.next_mh5tk_refresh
                need_ws_token = now >= state.next_ws_token_refresh

                if not (need_cookie or need_mh5tk or need_ws_token):
                    continue

                # 重新调度（即使本次失败，下次也按周期再试）
                if need_cookie:
                    state.schedule_next_cookie_keepalive()
                if need_mh5tk:
                    state.schedule_next_mh5tk_refresh()
                if need_ws_token:
                    state.schedule_next_ws_token_refresh()

                async with state.lock:
                    # 读取最新的 cookie 和 token
                    try:
                        async with async_session() as db:
                            row = (await db.execute(
                                text(
                                    "SELECT encrypted_cookie, encrypted_token FROM xianyu_account_auth "
                                    "WHERE account_id = :aid AND deleted = 0"
                                ),
                                {"aid": state.account_id},
                            )).mappings().first()
                    except Exception:
                        logger.warning("读取账号凭据失败 accountId=%d", state.account_id, exc_info=True)
                        continue

                    if not row:
                        continue

                    cookie_str = decrypt_cookie_if_needed(row["encrypted_cookie"])
                    if not cookie_str:
                        continue

                    m_h5_tk = decrypt_cookie_if_needed(row["encrypted_token"]) if row["encrypted_token"] else None
                    if not m_h5_tk:
                        # 从 cookie 中提取
                        for part in cookie_str.split(";"):
                            part = part.strip()
                            if part.startswith("_m_h5_tk="):
                                m_h5_tk = part.split("=", 1)[1]
                                break

                    # 1. Cookie 保活
                    if need_cookie:
                        await _do_cookie_keepalive(state)

                    # 2. _m_h5_tk 刷新
                    if need_mh5tk and _dispatcher_running:
                        await _do_mh5tk_refresh(state, cookie_str)

                    # 3. WS Token 刷新
                    if need_ws_token and _dispatcher_running and m_h5_tk:
                        await _do_ws_token_refresh(state, cookie_str, m_h5_tk)

                # 账号间隔 2-5 秒随机
                delay = random.uniform(ACCOUNT_INTERVAL_MIN_SECONDS, ACCOUNT_INTERVAL_MAX_SECONDS)
                await asyncio.sleep(delay)

        except asyncio.CancelledError:
            logger.info("Cookie/Token 刷新调度器收到取消信号，退出")
            break
        except Exception:
            _dispatcher_health_status = "error"
            logger.error("Cookie/Token 刷新调度器异常", exc_info=True)

        await asyncio.sleep(DISPATCHER_TICK_SECONDS)

    _dispatcher_health_status = "unavailable"
    logger.info("Cookie/Token 刷新调度器已停止")


# ============================================================
# 公共 API
# ============================================================
async def start_dispatcher() -> None:
    """启动 Cookie/Token 刷新调度器（在 FastAPI lifespan 中调用）"""
    global _dispatcher_task, _dispatcher_running, _dispatcher_health_status
    if _dispatcher_task is not None and not _dispatcher_task.done():
        logger.warning("Cookie/Token 刷新调度器已在运行，跳过")
        return
    _dispatcher_running = True
    _dispatcher_health_status = "starting"
    _dispatcher_task = asyncio.create_task(_dispatcher_loop())
    logger.info("Cookie/Token 刷新调度器已启动")


async def stop_dispatcher() -> None:
    """停止 Cookie/Token 刷新调度器"""
    global _dispatcher_task, _dispatcher_running, _dispatcher_health_status
    _dispatcher_running = False
    if _dispatcher_task is not None:
        _dispatcher_task.cancel()
        try:
            await _dispatcher_task
        except asyncio.CancelledError:
            pass
        _dispatcher_task = None
    _dispatcher_health_status = "unavailable"
    logger.info("Cookie/Token 刷新调度器已停止")


async def force_refresh_account(account_id: int, refresh_type: str = "all") -> dict:
    """手动触发单账号刷新（供内部 API 调用）
    refresh_type: all / cookie / mh5tk / ws_token
    """
    async with _states_lock:
        state = _states.get(account_id)

    if state is None:
        # 临时创建一个状态对象
        try:
            async with async_session() as db:
                row = (await db.execute(
                    text(
                        "SELECT a.id, auth.cookie_status FROM xianyu_account a "
                        "JOIN xianyu_account_auth auth ON auth.account_id = a.id "
                        "WHERE a.id = :aid AND a.deleted = 0 AND auth.deleted = 0"
                    ),
                    {"aid": account_id},
                )).mappings().first()
            if not row:
                return {"success": False, "errorCode": "ACCOUNT_NOT_FOUND"}
            state = AccountRefreshState(
                account_id=account_id,
            )
            async with _states_lock:
                _states[account_id] = state
        except Exception:
            return {"success": False, "errorCode": "STATE_LOAD_FAILED"}

    async with state.lock:
        # 读取凭据
        try:
            async with async_session() as db:
                row = (await db.execute(
                    text(
                        "SELECT encrypted_cookie, encrypted_token FROM xianyu_account_auth "
                        "WHERE account_id = :aid AND deleted = 0"
                    ),
                    {"aid": state.account_id},
                )).mappings().first()
        except Exception:
            return {"success": False, "errorCode": "CREDENTIAL_LOAD_FAILED"}

        if not row:
            return {"success": False, "errorCode": "CREDENTIAL_MISSING"}

        cookie_str = decrypt_cookie_if_needed(row["encrypted_cookie"])
        m_h5_tk = decrypt_cookie_if_needed(row["encrypted_token"]) if row["encrypted_token"] else None
        if not m_h5_tk and cookie_str:
            for part in cookie_str.split(";"):
                part = part.strip()
                if part.startswith("_m_h5_tk="):
                    m_h5_tk = part.split("=", 1)[1]
                    break

        results = {}
        if refresh_type in ("all", "cookie"):
            ok = await _do_cookie_keepalive(state)
            results["cookie"] = "ok" if ok else "failed"
            state.schedule_next_cookie_keepalive()

        if refresh_type in ("all", "mh5tk") and cookie_str:
            ok = await _do_mh5tk_refresh(state, cookie_str)
            results["mh5tk"] = "ok" if ok else "failed"
            state.schedule_next_mh5tk_refresh()

        if refresh_type in ("all", "ws_token") and cookie_str and m_h5_tk:
            ok = await _do_ws_token_refresh(state, cookie_str, m_h5_tk)
            results["ws_token"] = "ok" if ok else "failed"
            state.schedule_next_ws_token_refresh()

        success = all(v == "ok" for v in results.values()) if results else False
        return {
            "success": success,
            "details": results,
            "errorCode": None if success else "REFRESH_FAILED",
        }


async def get_dispatcher_status() -> dict:
    """获取刷新调度器状态（供前端展示）"""
    async with _states_lock:
        states_copy = []
        for state in _states.values():
            states_copy.append({
                "accountId": state.account_id,
                "nextCookieKeepalive": datetime.fromtimestamp(state.next_cookie_keepalive).isoformat() if state.next_cookie_keepalive else None,
                "nextMh5tkRefresh": datetime.fromtimestamp(state.next_mh5tk_refresh).isoformat() if state.next_mh5tk_refresh else None,
                "nextWsTokenRefresh": datetime.fromtimestamp(state.next_ws_token_refresh).isoformat() if state.next_ws_token_refresh else None,
                "lastCookieKeepaliveOk": state.last_cookie_keepalive_ok,
                "lastMh5tkRefreshOk": state.last_mh5tk_refresh_ok,
                "lastWsTokenRefreshOk": state.last_ws_token_refresh_ok,
                "lastError": state.last_error,
            })
    task_is_live = bool(
        _dispatcher_running
        and _dispatcher_task is not None
        and not _dispatcher_task.done()
    )
    return {
        "running": task_is_live,
        "healthStatus": _dispatcher_health_status if task_is_live else "unavailable",
        "accountsCount": len(states_copy),
        "config": {
            "cookieKeepaliveIntervalMinutes": COOKIE_KEEPALIVE_INTERVAL_SECONDS // 60,
            "mh5tkRefreshMinHours": MH5TK_REFRESH_MIN_SECONDS / 3600,
            "mh5tkRefreshMaxHours": MH5TK_REFRESH_MAX_SECONDS / 3600,
            "wsTokenRefreshMinHours": WS_TOKEN_REFRESH_MIN_SECONDS // 3600,
            "wsTokenRefreshMaxHours": WS_TOKEN_REFRESH_MAX_SECONDS // 3600,
            "accountIntervalMinSeconds": ACCOUNT_INTERVAL_MIN_SECONDS,
            "accountIntervalMaxSeconds": ACCOUNT_INTERVAL_MAX_SECONDS,
        },
        "accounts": states_copy,
    }
