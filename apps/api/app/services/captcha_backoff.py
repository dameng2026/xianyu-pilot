"""全自动滑块求解冷却机制（统一 60 秒）
=====================================
策略（与商业版规则对齐）：
- 成功：清空 fail_count，允许立即再求
- 失败：fail_count += 1，冷却 = 60 秒（统一，不累进）
- 手动触发 (manual / manual_retry) 跳过冷却（force=True），立即处理
- 临时性错误（浏览器崩溃/超时/网络错误）：skip_backoff=True，只记录 last_error，
  不累加 fail_count、不设置冷却，账号可立即再次求解（2026-08-04 对齐商业版）

冷却机制的存在目的：避免瞬时高频触发 Baxia 风控的"保护性间隔"，不是对账号的"惩罚"。
最大 1 分钟，超过 1 分钟会阻止 Cookie 有效账号快速重连 WS，违背持久化目标。
累进冷却已废弃：不得恢复基于 fail_count 的累进冷却（10/30/60 分钟）。

状态持久化到 xianyu_captcha_backoff，进程重启不丢失。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import text

from ..core.database import async_session

logger = logging.getLogger(__name__)

# 统一 60 秒冷却（与商业版规则对齐）
# 冷却的唯一目的是避免瞬时高频触发 Baxia 风控的"保护性间隔"，不是对账号的"惩罚"。
# 最大 1 分钟，超过 1 分钟会阻止 Cookie 有效账号快速重连 WS，违背持久化目标。
MAX_COOLDOWN_SEC = 60                # 所有失败原因统一 60 秒冷却
_ENSURABLE = False


def _cooldown_seconds(fail_count: int, failure_reason: str = "") -> int:
    """统一 60 秒冷却（与商业版规则对齐）。

    所有失败原因（slider_fail/cookie_invalid/timeout/browser_crashed/service_unavailable/其他）
    统一 60 秒冷却，不得设置超过 1 分钟的冷却时间。
    累进冷却已废弃：不得恢复基于 fail_count 的累进冷却（10/30/60 分钟），
    累进冷却会让频繁失败的账号陷入长冷却，与 WS 持久化目标冲突。

    Args:
        fail_count: 失败次数
        failure_reason: 失败原因（保留参数，与商业版对齐；不再差异化冷却）
    """
    if fail_count <= 0:
        return 0
    return MAX_COOLDOWN_SEC


async def ensure_backoff_table() -> None:
    """幂等建表，避免迁移未跑导致退避失效。"""
    global _ENSURABLE
    if _ENSURABLE:
        return
    try:
        async with async_session() as db:
            await db.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS xianyu_captcha_backoff (
                      account_id BIGINT NOT NULL,
                      fail_count INT NOT NULL DEFAULT 0,
                      next_allowed_at DATETIME NULL,
                      last_fail_at DATETIME NULL,
                      last_success_at DATETIME NULL,
                      last_error VARCHAR(512) DEFAULT '',
                      updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                        ON UPDATE CURRENT_TIMESTAMP,
                      PRIMARY KEY (account_id),
                      KEY idx_cb_next_allowed (next_allowed_at)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                    """
                )
            )
            await db.commit()
        _ENSURABLE = True
    except Exception:
        logger.warning("ensure_captcha_backoff_table 失败", exc_info=True)


async def get_backoff_status(account_id: int) -> dict[str, Any]:
    await ensure_backoff_table()
    try:
        async with async_session() as db:
            row = (
                await db.execute(
                    text(
                        "SELECT fail_count, next_allowed_at, last_fail_at, last_success_at, last_error "
                        "FROM xianyu_captcha_backoff "
                        "WHERE account_id = :aid LIMIT 1"
                    ),
                    {"aid": account_id},
                )
            ).mappings().first()
        if not row:
            return {
                "failCount": 0,
                "allowed": True,
                "nextAllowedAt": None,
                "remainingSec": 0,
                "lastError": "",
            }
        next_at: Optional[datetime] = row.get("next_allowed_at")
        now = datetime.now()
        remaining = 0
        allowed = True
        if next_at and next_at > now:
            allowed = False
            remaining = int((next_at - now).total_seconds())
        return {
            "failCount": int(row.get("fail_count") or 0),
            "allowed": allowed,
            "nextAllowedAt": str(next_at) if next_at else None,
            "remainingSec": remaining,
            "lastError": str(row.get("last_error") or ""),
        }
    except Exception:
        logger.warning("get_captcha_backoff 失败 accountId=%d", account_id, exc_info=True)
        # 读失败时不阻断（fail-open），避免表异常导致永不可求
        return {
            "failCount": 0,
            "allowed": True,
            "nextAllowedAt": None,
            "remainingSec": 0,
            "lastError": "",
        }


async def assert_auto_solve_allowed(
    account_id: int,
    *,
    force: bool = False,
) -> Optional[dict[str, Any]]:
    """若处于冷却期返回阻断信息 dict；允许则返回 None。

    统一 60 秒冷却（与商业版规则对齐）：
    - 自动触发场景在冷却期内跳过入队，冷却期过后立即允许再次求解
    - 手动触发场景（force=True）跳过冷却，立即处理
    - 冷却的唯一目的是避免瞬时高频触发 Baxia 风控，服务于 WS 持久化目标
    """
    if force:
        return None
    await ensure_backoff_table()
    try:
        async with async_session() as db:
            row = (
                await db.execute(
                    text(
                        "SELECT fail_count, next_allowed_at, last_error "
                        "FROM xianyu_captcha_backoff "
                        "WHERE account_id = :aid LIMIT 1"
                    ),
                    {"aid": account_id},
                )
            ).mappings().first()
        if not row:
            return None
        next_at: Optional[datetime] = row.get("next_allowed_at")
        if not next_at:
            return None
        now = datetime.now()
        if next_at > now:
            remaining = int((next_at - now).total_seconds())
            return {
                "blocked": True,
                "reason": "cooldown",
                "failCount": int(row.get("fail_count") or 0),
                "nextAllowedAt": str(next_at),
                "remainingSec": remaining,
                "lastError": str(row.get("last_error") or ""),
            }
        return None
    except Exception:
        logger.warning("assert_auto_solve_allowed 失败 accountId=%d", account_id, exc_info=True)
        # 读失败时不阻断（fail-open），避免表异常导致永不可求
        return None


async def record_solve_success(account_id: int) -> None:
    await ensure_backoff_table()
    try:
        async with async_session() as db:
            await db.execute(
                text(
                    """
                    INSERT INTO xianyu_captcha_backoff
                      (account_id, fail_count, next_allowed_at, last_success_at, last_error, updated_at)
                    VALUES (:aid, 0, NULL, NOW(), '', NOW())
                    ON DUPLICATE KEY UPDATE
                      fail_count = 0,
                      next_allowed_at = NULL,
                      last_success_at = NOW(),
                      last_error = '',
                      updated_at = NOW()
                    """
                ),
                {"aid": account_id},
            )
            await db.commit()
        logger.info("滑块退避已重置(成功) accountId=%d", account_id)
    except Exception:
        logger.warning("record_captcha_backoff_success 失败 accountId=%d", account_id, exc_info=True)


async def record_solve_failure(
    account_id: int,
    error: str = "",
    *,
    skip_backoff: bool = False,
    failure_reason: str = "",
) -> dict[str, Any]:
    """记录失败并计算下次允许时间，返回退避状态。

    Args:
        account_id: 账号 ID
        error: 错误消息
        skip_backoff: 是否跳过退避累加（仅记录 last_error，不累加 fail_count、不设置 next_allowed_at）。
            用于浏览器崩溃（browser_crashed）等临时性错误：这类错误重试一次可能就成功，
            不应让账号进入 60 秒冷却期导致后续求解被阻断。
            2026-07-29 事故修复（与商业版对齐）：浏览器崩溃（Page crashed / browserContext closed）
            原先被归为 service_unavailable 并累加退避，导致 WS 每次重连触发求解时
            都被 assert_auto_solve_allowed 拦截，账号长时间无法自动求解。
        failure_reason: 失败原因（用于错误分类，与商业版对齐）。
            slider_fail：60 秒冷却（Baxia 风控状态需要时间恢复）
            cookie_invalid：60 秒冷却（Cookie 已失效，等用户重新登录）
            browser_crashed：跳过退避（临时性错误，不累加 fail_count）
            其他：60 秒冷却（临时性错误快速重试）
    """
    await ensure_backoff_table()
    err = (error or "")[:500]

    if skip_backoff:
        # 仅记录 last_error，不累加 fail_count、不设置 next_allowed_at
        # 账号仍可立即再次求解（assert_auto_solve_allowed 不会被拦截）
        try:
            async with async_session() as db:
                await db.execute(
                    text(
                        """
                        INSERT INTO xianyu_captcha_backoff
                          (account_id, fail_count, next_allowed_at, last_fail_at, last_error, updated_at)
                        VALUES (:aid, 0, NULL, NOW(), :err, NOW())
                        ON DUPLICATE KEY UPDATE
                          last_fail_at = NOW(),
                          last_error = :err,
                          updated_at = NOW()
                        """
                    ),
                    {"aid": account_id, "err": err},
                )
                await db.commit()
            logger.info(
                "滑块失败已记录(跳过退避) accountId=%d error=%s — 临时性错误，不累加冷却",
                account_id, err[:120],
            )
        except Exception:
            logger.warning(
                "record_captcha_backoff_failure_skip 失败 accountId=%d",
                account_id, exc_info=True,
            )
        return {
            "failCount": 0,
            "cooldownSec": 0,
            "nextAllowedAt": None,
            "allowed": True,
            "remainingSec": 0,
            "lastError": err,
        }

    st = await get_backoff_status(account_id)
    fail_count = int(st.get("failCount") or 0) + 1
    cool = _cooldown_seconds(fail_count, failure_reason)
    next_at = datetime.now() + timedelta(seconds=cool)
    try:
        async with async_session() as db:
            await db.execute(
                text(
                    """
                    INSERT INTO xianyu_captcha_backoff
                      (account_id, fail_count, next_allowed_at, last_fail_at, last_error, updated_at)
                    VALUES (:aid, :fc, :na, NOW(), :err, NOW())
                    ON DUPLICATE KEY UPDATE
                      fail_count = :fc,
                      next_allowed_at = :na,
                      last_fail_at = NOW(),
                      last_error = :err,
                      updated_at = NOW()
                    """
                ),
                {
                    "aid": account_id,
                    "fc": fail_count,
                    "na": next_at,
                    "err": err,
                },
            )
            await db.commit()
        logger.warning(
            "滑块退避已更新(失败) accountId=%d failCount=%d cooldownSec=%d next=%s",
            account_id, fail_count, cool, next_at.isoformat(sep=" ", timespec="seconds"),
        )
    except Exception:
        logger.warning("record_captcha_backoff_failure 失败 accountId=%d", account_id, exc_info=True)
    return {
        "failCount": fail_count,
        "cooldownSec": cool,
        "nextAllowedAt": next_at.isoformat(sep=" ", timespec="seconds"),
        "allowed": False,
        "remainingSec": cool,
        "lastError": err,
    }
