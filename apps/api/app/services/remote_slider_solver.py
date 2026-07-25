"""
远程滑块求解服务
================
当用户开启远程滑块求解开关时，调用商业版远程 API 进行滑块求解，
替代本地 crawler-service 的 Playwright 求解。

调用链路：
  captcha_solver.handle_captcha_for_account()
    → is_remote_slider_enabled() 为 True 时调用 try_remote_solve()
    → 否则调用 try_auto_solve()（本地 crawler-service）

远程 API 返回格式：
  {
    "ok": bool,
    "status": "success"/"fail"/"timeout"/"precheck_rejected"/"service_unavailable",
    "solved": bool,
    "captchaDetected": bool,
    "attempts": int,
    "durationMs": int,
    "cookies": str,       # 新鲜 Cookie（仅成功）
    "error": str,         # 失败原因（脱敏）
    "recordId": str,      # 请求唯一 ID
    "tokenCharged": int   # 扣费 Token 数
  }

设计原则（详见 opensource-no-commercial-exposure.md / 远程滑块求解成功率提升规则）：
  1. 远程求解失败不降级到本地求解，让用户明确感知远程 API 的使用价值
  2. 本地项目不限制求解请求频率（商业版后端已有 1 分钟冷却）
  3. service_unavailable / timeout / 网络异常不计入 fail_count（外部因素，避免 punish 加码）
  4. service_unavailable / 网络异常自动重试最多 2 次，slider_fail / precheck_rejected 不重试
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from typing import Optional

import httpx
from sqlalchemy import text

from ..core.cookie_crypto import decrypt_cookie_if_needed, encrypt_cookie_for_storage
from ..core.database import async_session
from .captcha_solver import _merge_cookies
from .remote_slider_config import (
    is_remote_slider_enabled,
    load_remote_slider_config_from_store,
    precheck_remote_slider_cached,
)
from .remote_slider_record import create_remote_solve_record, update_remote_solve_record

logger = logging.getLogger(__name__)

# 约束3：连接超时与读取超时必须分离
# - connect=5s：快速发现服务不可达
# - read=60s：求解可能较慢（Playwright 启动+拖动+二次验证）
# - write=10s：请求体较小
# - pool=5s：连接池获取超时
REMOTE_SOLVE_TIMEOUT = httpx.Timeout(connect=5.0, read=60.0, write=10.0, pool=5.0)

# 约束2：service_unavailable 自动重试配置
MAX_SERVICE_UNAVAILABLE_RETRIES = 2
RETRY_DELAY_SEC = 2.0

# 默认 targetUrl（约束6）
DEFAULT_TARGET_URL = "https://www.goofish.com/im"


# ============================================================
# 响应解析：兼容商业版两种返回格式
# ============================================================
# 商业版有两种响应格式：
#
# 格式A（扁平，service_unavailable 等错误时）：
#   {"ok": false, "status": "service_unavailable", "error": "...", "recordId": "...", "tokenCharged": 0}
#
# 格式B（嵌套，求解成功/失败时）：
#   {
#     "code": 200, "msg": "操作成功",
#     "data": {"status": "success", "solved": true, "captchaDetected": false,
#              "attempts": 1, "durationMs": 12613, "cookies": "...", "error": null},
#     "ok": false,           # 注意：顶层 ok 可能不准确，需以 data.solved 为准
#     "recordId": "...",
#     "tokenCharged": 0
#   }
#
# 解析规则：优先从 data 子对象读取 status/solved/cookies 等业务字段，
# 若 data 不存在则从顶层读取（兼容扁平格式）。
def _parse_remote_response(resp_data: dict) -> dict:
    """解析商业版响应，统一返回扁平结构。

    返回字段：
      ok, status, solved, captchaDetected, attempts, durationMs,
      cookies, error, recordId, tokenCharged, tokenChargeFailed, clientIp
    """
    if not isinstance(resp_data, dict):
        return {
            "ok": False, "status": "fail", "solved": False,
            "captchaDetected": False, "attempts": 0, "durationMs": 0,
            "cookies": "", "error": "响应格式异常", "recordId": "",
            "tokenCharged": 0, "tokenChargeFailed": False, "clientIp": "",
        }

    # 顶层字段（两种格式都有）
    top_ok = bool(resp_data.get("ok"))
    top_record_id = str(resp_data.get("recordId") or "")
    top_token_charged = int(resp_data.get("tokenCharged") or 0)
    top_token_charge_failed = bool(resp_data.get("tokenChargeFailed") or False)
    top_client_ip = str(resp_data.get("clientIp") or "")

    # 业务字段：优先从 data 子对象读取（格式B），不存在则从顶层读取（格式A）
    inner = resp_data.get("data") if isinstance(resp_data.get("data"), dict) else resp_data

    status = str(inner.get("status") or "")
    solved = bool(inner.get("solved"))
    captcha_detected = bool(inner.get("captchaDetected"))
    attempts = int(inner.get("attempts") or 0)
    duration_ms = int(inner.get("durationMs") or 0)
    cookies = inner.get("cookies") or ""
    error = inner.get("error") or resp_data.get("error") or ""

    # 关键判定：商业版格式B 中顶层 ok 可能不准确（已观察到 ok=false 但 data.solved=true）
    # 以 data.solved 为准：若 data.solved=true，则视为成功
    # 格式A 中 ok 与 solved 一致，此逻辑也兼容
    final_ok = top_ok or solved

    return {
        "ok": final_ok,
        "status": status,
        "solved": solved,
        "captchaDetected": captcha_detected,
        "attempts": attempts,
        "durationMs": duration_ms,
        "cookies": cookies,
        "error": error,
        "recordId": top_record_id,
        "tokenCharged": top_token_charged,
        "tokenChargeFailed": top_token_charge_failed,
        "clientIp": top_client_ip,
    }

# ============================================================
# 错误码常量（约束7：错误码必须细分）
# ============================================================
ERR_SLIDER_FAIL = "REMOTE_SLIDER_SLIDER_FAIL"                  # 商业版 status=fail
ERR_TIMEOUT = "REMOTE_SLIDER_TIMEOUT"                          # status=timeout
ERR_PRECHECK_REJECTED = "REMOTE_SLIDER_PRECHECK_REJECTED"      # status=precheck_rejected
ERR_SERVICE_UNAVAILABLE = "REMOTE_SLIDER_SERVICE_UNAVAILABLE"  # status=service_unavailable
ERR_NETWORK_ERROR = "REMOTE_SLIDER_NETWORK_ERROR"              # 网络异常，未收到响应
ERR_INSUFFICIENT_BALANCE = "REMOTE_SLIDER_INSUFFICIENT_BALANCE"  # 余额不足
ERR_CONFIG_LOAD_FAILED = "REMOTE_CONFIG_LOAD_FAILED"
ERR_CONFIG_INCOMPLETE = "REMOTE_CONFIG_INCOMPLETE"
ERR_PRECHECK_FAILED = "REMOTE_SOLVER_UNAVAILABLE"              # 运行时预检失败
ERR_ACCOUNT_LOAD_FAILED = "CAPTCHA_ACCOUNT_LOAD_FAILED"
ERR_COOKIE_DECRYPT_FAILED = "CAPTCHA_COOKIE_DECRYPT_FAILED"
ERR_COOKIE_MISSING_FIELDS = "CAPTCHA_COOKIE_MISSING_FIELDS"

# ============================================================
# 脱敏处理：避免商业版内部技术细节泄露给开源版用户
# ============================================================
# 匹配技术性异常原文的正则模式：
# - Java RestTemplate 异常："I/O error on POST request for "...""
# - 内部地址泄露："localhost:xxx"、"127.0.0.1:xxx"、"http://192.168..."
# - Java 异常类名："ResourceAccessException"、"Connection refused"
_TECHNICAL_ERROR_PATTERNS = [
    re.compile(r"I/O error on \w+ request", re.IGNORECASE),
    re.compile(r"localhost:\d+", re.IGNORECASE),
    re.compile(r"127\.0\.0\.1:\d+"),
    re.compile(r"192\.168\.\d+\.\d+:\d+"),
    re.compile(r"10\.\d+\.\d+\.\d+:\d+"),
    re.compile(r"ResourceAccessException|RestClientException|HttpServerErrorException", re.IGNORECASE),
    re.compile(r"Connection refused|connect timed out|read timed out", re.IGNORECASE),
    re.compile(r"nested exception is", re.IGNORECASE),
]


def _sanitize_remote_error(raw_error) -> str:
    """对商业版返回的 error 字段做脱敏处理。

    商业版后端在内部服务（如 automation-service）不可达时，会返回 Java 异常原文，
    例如 "I/O error on POST request for "http://localhost:12401/...": null"，
    这会泄露商业版内部架构（端口号、调用链路、内部地址）。
    检测到技术性异常特征时，替换为友好提示。
    """
    if not raw_error or not isinstance(raw_error, str):
        return raw_error
    for pattern in _TECHNICAL_ERROR_PATTERNS:
        if pattern.search(raw_error):
            return "远程滑块求解服务暂时不可用，请稍后重试或改为本地求解"
    return raw_error


# ============================================================
# 约束1：判断是否应计入 fail_count
# ============================================================
# service_unavailable / timeout / 网络异常是外部因素，不计入 fail_count
# 仅 slider_fail 才累加（避免商业版 punish 加码）
_NETWORK_LIKE_STATUSES = {"service_unavailable", "timeout", "precheck_rejected"}


def _should_count_failure(remote_status: str) -> bool:
    """判断该 remote_status 是否应累加 fail_count。

    约束1：service_unavailable / timeout / 网络异常 → 不计入
    仅 slider_fail（商业版 status=fail）才计入 fail_count。
    """
    return remote_status not in _NETWORK_LIKE_STATUSES


# ============================================================
# 约束7：根据 remote_status 映射 errorCode
# ============================================================
def _error_code_for_status(remote_status: str, *, network_error: bool = False) -> str:
    """根据商业版返回的 status 映射细分 errorCode。

    约束7：禁止笼统使用 REMOTE_CAPTCHA_SOLVE_FAILED。
    """
    if network_error:
        return ERR_NETWORK_ERROR
    mapping = {
        "fail": ERR_SLIDER_FAIL,
        "timeout": ERR_TIMEOUT,
        "precheck_rejected": ERR_PRECHECK_REJECTED,
        "service_unavailable": ERR_SERVICE_UNAVAILABLE,
        "insufficient_balance": ERR_INSUFFICIENT_BALANCE,
    }
    return mapping.get(remote_status, ERR_SLIDER_FAIL)


# ============================================================
# 约束4：Cookie 关键字段预检
# ============================================================
def _validate_cookie_fields(cookie_str: str) -> tuple[bool, str]:
    """预检 Cookie 是否包含闲鱼登录必需的关键字段。

    约束4：调用远程 API 前必须检查 cookie 字符串包含 unb= 和 cookie2=，
    缺失任一字段直接返回失败，不发起远程调用，不浪费配额。
    """
    if not cookie_str:
        return False, "Cookie 为空"
    # unb：用户唯一标识，缺失则无法定位账号
    # cookie2：登录态核心字段，缺失则请求会被重定向到登录页
    if "unb=" not in cookie_str:
        return False, "Cookie 缺少 unb 字段，请重新扫码登录闲鱼账号"
    if "cookie2=" not in cookie_str:
        return False, "Cookie 缺少 cookie2 字段，请重新扫码登录闲鱼账号"
    return True, ""


# ============================================================
# 核心求解逻辑
# ============================================================
async def try_remote_solve(
    account_id: int,
    target_url: Optional[str] = None,
    *,
    trigger_scene: str = "manual",
    open_reason: str = "",
    solve_reason: str = "",
) -> dict:
    """调用商业版远程滑块求解 API。

    与本地 try_auto_solve() 返回结构保持一致，便于 handle_captcha_for_account 无缝切换。

    遵循远程滑块求解成功率提升规则：
    - 约束2：service_unavailable / 网络异常自动重试最多 2 次
    - 约束3：超时分离（connect=5/read=60/write=10/pool=5）
    - 约束4：Cookie 预检 unb= / cookie2=
    - 约束5：运行时预检带 30s 缓存
    - 约束6：请求体扩展 targetUrl/triggerScene/attempt/clientRequestTs
    - 约束7：errorCode 按 status 细分
    """
    from .captcha_backoff import (
        assert_auto_solve_allowed,
        record_solve_failure,
        record_solve_success,
    )

    # 指数退避检查（保持当前实现：assert_auto_solve_allowed 始终返回 None 不阻断）
    # 约束2（设计原则2）：本地项目不限制求解请求频率
    blocked = await assert_auto_solve_allowed(account_id, force=False)
    if blocked:
        logger.warning(
            "远程滑块求解被指数退避拦截 accountId=%d", account_id
        )
        return blocked

    # 读取远程配置
    try:
        config = await load_remote_slider_config_from_store()
    except Exception as exc:
        logger.error("读取远程滑块配置失败 errorType=%s", type(exc).__name__)
        return {
            "success": False,
            "solved": False,
            "captchaDetected": False,
            "attempts": 0,
            "errorCode": ERR_CONFIG_LOAD_FAILED,
            "error": "远程滑块配置读取失败",
            "durationMs": 0,
        }

    api_url = config.get("apiUrl", "").strip()
    api_key = config.get("apiKey", "").strip()
    if not api_url or not api_key:
        return {
            "success": False,
            "solved": False,
            "captchaDetected": False,
            "attempts": 0,
            "errorCode": ERR_CONFIG_INCOMPLETE,
            "error": "远程滑块求解未配置 API 地址或密钥",
            "durationMs": 0,
        }

    # 约束5：运行时预检带 30s 缓存
    # 预检失败直接返回，不发起 120s 超时请求，让用户看到清晰的"服务不可用"记录
    try:
        precheck = await precheck_remote_slider_cached(api_url, api_key)
        if not precheck.get("ok"):
            logger.warning(
                "远程滑块预检失败 accountId=%d message=%s",
                account_id, precheck.get("message"),
            )
            return {
                "success": False,
                "solved": False,
                "captchaDetected": False,
                "attempts": 0,
                "errorCode": ERR_PRECHECK_FAILED,
                "error": precheck.get("message") or "远程滑块求解服务暂不可用",
                "durationMs": 0,
            }
    except Exception as exc:
        logger.error("远程滑块预检异常 errorType=%s", type(exc).__name__)

    # 读取账号 Cookie
    # 注意：xianyu_account 表只有 nickname 列（无 nick_name），且没有 cookie_status 列
    # （cookie_status 位于 xianyu_account_auth / xianyu_account_runtime 表）。
    try:
        async with async_session() as db:
            row = (
                await db.execute(
                    text(
                        "SELECT a.id, a.nickname, au.encrypted_cookie "
                        "FROM xianyu_account a "
                        "LEFT JOIN xianyu_account_auth au ON au.account_id = a.id AND COALESCE(au.deleted,0)=0 "
                        "WHERE a.id = :aid AND COALESCE(a.deleted,0) = 0 LIMIT 1"
                    ),
                    {"aid": account_id},
                )
            ).mappings().first()
    except Exception as exc:
        logger.error("读取滑块账号信息失败 errorType=%s", type(exc).__name__)
        return {
            "success": False,
            "solved": False,
            "captchaDetected": False,
            "attempts": 0,
            "errorCode": ERR_ACCOUNT_LOAD_FAILED,
            "error": "读取账号信息失败，请稍后重试",
            "durationMs": 0,
        }

    if not row:
        return {
            "success": False,
            "solved": False,
            "captchaDetected": False,
            "attempts": 0,
            "errorCode": ERR_ACCOUNT_LOAD_FAILED,
            "error": "账号不存在或未配置 Cookie",
            "durationMs": 0,
        }

    account_name = row.get("nickname") or ""
    # decrypt_cookie_if_needed 在密钥不匹配/数据损坏时会抛 RuntimeError，
    # 必须在此处捕获，否则会冒泡到路由层导致 500 服务器内部错误。
    try:
        cookie_str = decrypt_cookie_if_needed(row.get("encrypted_cookie") or "")
    except Exception as exc:
        logger.error(
            "远程滑块账号 Cookie 解密失败 accountId=%d errorType=%s",
            account_id, type(exc).__name__,
        )
        return {
            "success": False,
            "solved": False,
            "captchaDetected": False,
            "attempts": 0,
            "errorCode": ERR_COOKIE_DECRYPT_FAILED,
            "error": "Cookie 解密失败，请重新扫码登录闲鱼账号后再尝试求解",
            "durationMs": 0,
        }
    if not cookie_str:
        return {
            "success": False,
            "solved": False,
            "captchaDetected": False,
            "attempts": 0,
            "errorCode": ERR_COOKIE_DECRYPT_FAILED,
            "error": "Cookie 解密失败，请重新扫码登录闲鱼账号后再尝试求解",
            "durationMs": 0,
        }

    # 约束4：Cookie 关键字段预检
    # 缺失 unb= 或 cookie2= 直接返回，不发起远程调用，不浪费配额
    cookie_ok, cookie_err = _validate_cookie_fields(cookie_str)
    if not cookie_ok:
        logger.warning(
            "Cookie 关键字段缺失，跳过远程调用 accountId=%d reason=%s",
            account_id, cookie_err,
        )
        return {
            "success": False,
            "solved": False,
            "captchaDetected": False,
            "attempts": 0,
            "errorCode": ERR_COOKIE_MISSING_FIELDS,
            "error": cookie_err,
            "durationMs": 0,
        }

    # 创建远程求解记录
    request_id = await create_remote_solve_record(
        account_id=account_id,
        account_name=account_name,
        trigger_scene=trigger_scene,
        status="retrying",
    )

    # 约束6：请求体扩展
    final_target_url = target_url or DEFAULT_TARGET_URL
    started = time.time()

    # 约束2：service_unavailable / 网络异常自动重试
    # slider_fail / precheck_rejected 不重试
    last_data = None
    last_network_error = False
    last_resp_status_code = 0

    for attempt in range(MAX_SERVICE_UNAVAILABLE_RETRIES + 1):
        # 约束6：请求体包含 attempt / clientRequestTs
        request_body = {
            "cookie": cookie_str,
            "targetUrl": final_target_url,
            "triggerScene": trigger_scene,
            "attempt": attempt,
            "clientRequestTs": int(time.time() * 1000),
        }

        try:
            async with httpx.AsyncClient(
                timeout=REMOTE_SOLVE_TIMEOUT,  # 约束3：分离超时
                follow_redirects=False,
                trust_env=False,
            ) as client:
                resp = await client.post(
                    api_url,
                    json=request_body,
                    headers={
                        "Content-Type": "application/json",
                        "X-Api-Key": api_key,
                    },
                )
                last_resp_status_code = resp.status_code
                last_data = resp.json()
                last_network_error = False
        except Exception as exc:
            # 网络异常：记录并准备重试
            last_network_error = True
            last_data = None
            logger.warning(
                "远程滑块求解请求异常 accountId=%d attempt=%d errorType=%s",
                account_id, attempt, type(exc).__name__,
            )
            # 约束2：网络异常重试，最多 MAX_SERVICE_UNAVAILABLE_RETRIES 次
            if attempt < MAX_SERVICE_UNAVAILABLE_RETRIES:
                await asyncio.sleep(RETRY_DELAY_SEC)
                continue
            # 重试耗尽，跳出循环处理最终失败
            break

        # 收到响应，判断是否需要重试（约束2）
        remote_status = str(last_data.get("status") or "")
        if remote_status == "service_unavailable":
            # service_unavailable：自动重试
            logger.info(
                "远程滑块求解返回 service_unavailable，准备重试 accountId=%d attempt=%d",
                account_id, attempt,
            )
            if attempt < MAX_SERVICE_UNAVAILABLE_RETRIES:
                await asyncio.sleep(RETRY_DELAY_SEC)
                continue
            # 重试耗尽
            break
        else:
            # 其他状态（success / fail / timeout / precheck_rejected）：不重试
            break

    duration_ms = int((time.time() - started) * 1000)

    # ============================================================
    # 处理最终结果
    # ============================================================

    # 情况1：网络异常（未收到响应）
    if last_network_error or last_data is None:
        logger.error(
            "远程滑块求解网络异常（重试耗尽）accountId=%d httpStatus=%s",
            account_id, last_resp_status_code or "N/A",
        )
        # 约束1：网络异常不计入 fail_count
        await update_remote_solve_record(
            request_id,
            status="service_unavailable",
            failure_reason="network_error",
            error_message=f"远程API调用失败: 网络异常 (status={last_resp_status_code or 'N/A'})",
            duration_ms=duration_ms,
            token_charged=0,
            remote_status="network_error",
            remote_solved=False,
        )
        return {
            "success": False,
            "solved": False,
            "captchaDetected": False,
            "attempts": MAX_SERVICE_UNAVAILABLE_RETRIES + 1,
            "errorCode": ERR_NETWORK_ERROR,  # 约束7
            "error": "网络连接失败，请检查网络或服务状态后重试",
            "durationMs": duration_ms,
            "recordId": request_id,
        }

    # 情况2：收到商业版响应
    # 使用 _parse_remote_response 解析响应，兼容商业版两种返回格式：
    #   格式A（扁平，service_unavailable 等错误时）：{ok, status, error, recordId, tokenCharged}
    #   格式B（嵌套，ResultObject 包装时）：{code, msg, data: {status, solved, cookies, ...}, ok, recordId, tokenCharged}
    # 若不调用此函数直接从顶层读取，格式B 中业务字段（status/solved/cookies）位于 data 子对象，
    # 顶层永远拿不到，导致即使商业版求解成功也会被判定为失败、不合并 Cookie、计入 fail_count。
    data = _parse_remote_response(last_data)
    logger.info(
        "远程滑块求解 API 响应 accountId=%d httpStatus=%d remoteStatus=%s solved=%s",
        account_id, last_resp_status_code, data.get("status"), data.get("solved"),
    )
    remote_ok = bool(data.get("ok"))
    remote_solved = bool(data.get("solved"))
    remote_status = str(data.get("status") or "")
    token_charged = int(data.get("tokenCharged") or 0)
    token_charge_failed = bool(data.get("tokenChargeFailed") or False)
    new_cookie_str = data.get("cookies") or ""
    client_ip = str(data.get("clientIp") or "")

    # 约束1：退避状态更新
    # service_unavailable / timeout / precheck_rejected / 网络异常 → 不计入 fail_count
    # 仅 slider_fail 才累加 fail_count
    if remote_ok and remote_solved:
        await record_solve_success(account_id)
    elif _should_count_failure(remote_status):
        # 仅 slider_fail 才记录失败
        await record_solve_failure(
            account_id,
            error=_sanitize_remote_error(data.get("error")) or "远程滑块验证未通过",
        )
    # else: service_unavailable / timeout / precheck_rejected → 不调用 record_solve_failure

    # 合并 Cookie（仅成功时）
    merged_cookie = ""
    if remote_ok and remote_solved and new_cookie_str:
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
                    "远程滑块求解后 Cookie 已合并并保存 accountId=%d", account_id
                )
            except Exception as exc:
                logger.error("保存合并后的 Cookie 失败 errorType=%s", type(exc).__name__)

    # 确定本地记录状态
    if remote_ok and remote_solved:
        record_status = "success"
        failure_reason = ""
    elif remote_status == "timeout":
        record_status = "timeout"
        failure_reason = "timeout"
    elif remote_status == "precheck_rejected":
        record_status = "precheck_rejected"
        failure_reason = "precheck_rejected"
    elif remote_status == "service_unavailable":
        record_status = "service_unavailable"
        failure_reason = "service_unavailable"
    else:
        record_status = "fail"
        failure_reason = "slider_fail"

    # 更新远程求解记录（error_message 同样脱敏，避免内部地址泄露到记录表）
    sanitized_error = _sanitize_remote_error(data.get("error"))
    await update_remote_solve_record(
        request_id,
        status=record_status,
        failure_reason=failure_reason,
        error_message=sanitized_error,
        duration_ms=duration_ms,
        token_charged=token_charged if (remote_ok and remote_solved and not token_charge_failed) else 0,
        remote_status=remote_status,
        remote_solved=remote_solved,
        client_ip=client_ip,
    )

    # 约束7：errorCode 按 status 细分
    error_code = "" if remote_ok else _error_code_for_status(remote_status)

    return {
        "success": remote_ok,
        "solved": remote_solved,
        "captchaDetected": bool(data.get("captchaDetected")),
        "attempts": int(data.get("attempts") or 0),
        "errorCode": error_code,
        # 复用已脱敏的 error，避免向用户透传 Java 异常原文
        "error": sanitized_error,
        "durationMs": duration_ms,
        "cookieStr": new_cookie_str if remote_ok else "",
        "mergedCookie": merged_cookie,
        "recordId": request_id,
        "tokenCharged": token_charged if (remote_ok and remote_solved and not token_charge_failed) else 0,
        "remoteStatus": remote_status,
    }
