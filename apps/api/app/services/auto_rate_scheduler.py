"""
自动补评价定时调度器（开源版）。

调度策略：
- 每小时第 5 分钟扫描一次（避开整点请求峰值）
- 取所有 enabled=1 且 schedule_hour=当前小时 且 deleted=0 的账号配置
- 对每个账号独立执行，互不影响

每个账号执行流程：
1. 兜底校验：账号是否仍启用 / 是否鱼小铺 / Cookie 是否有效 / 评价内容是否配置
2. 拉取最新评价列表（复用 rate_service.sync_rates_for_account）
3. 查询本地 has_seller_rate=0 且 rate_reviewable=1 的待评价订单
4. 对每条订单调用 create_rate（仅好评 rate=1，匿名 anonymous=true）
5. 写入 xianyu_auto_rate_log 执行日志
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import select, and_, desc
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.entities import (
    XianyuAccount,
    XianyuAccountAutoRateConfig,
    XianyuAutoRateLog,
    XianyuRate,
)
from .rate_service import (
    create_rate,
    sync_rates_for_account,
    verify_fish_shop_account,
    RATE_LEVEL_GOOD,
    RATE_FEEDBACK_MAX_LENGTH,
)

logger = logging.getLogger(__name__)

# 调度间隔：每小时检查一次（在第 5 分钟附近触发，避开整点峰值）
SCAN_INTERVAL_SECONDS = 60 * 60
# 启动后延迟首次扫描（秒）= 90 秒
INITIAL_DELAY_SECONDS = 90
# 每条评价之间的间隔（秒），避免风控
RATE_SUBMIT_INTERVAL_SECONDS = 1.5
# 单账号单次执行最多处理订单数（保护）
MAX_ORDERS_PER_RUN = 50

# 调度器状态
_scheduler_task: Optional[asyncio.Task] = None
_scheduler_started: bool = False
_scheduler_stop_event: Optional[asyncio.Event] = None

# 进程内执行去重锁：同账号同时只能一次自动评价
_run_locks: dict[int, asyncio.Lock] = {}
_run_locks_guard = asyncio.Lock()

# 最近一次扫描结果（供诊断接口使用）
_last_scan_result: Optional[dict] = None
_last_scan_at: Optional[datetime] = None


async def _get_run_lock(account_id: int) -> asyncio.Lock:
    async with _run_locks_guard:
        lock = _run_locks.get(account_id)
        if lock is None:
            lock = asyncio.Lock()
            _run_locks[account_id] = lock
        return lock


def _normalize_schedule_hour(value: Any) -> int:
    """规范化 schedule_hour 到 0-23，默认 9。"""
    try:
        if value is None:
            return 9
        h = int(value)
        if 0 <= h <= 23:
            return h
        return 9
    except (TypeError, ValueError):
        return 9


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n]


async def _list_due_configs(db: AsyncSession, schedule_hour: int) -> list[XianyuAccountAutoRateConfig]:
    """查询所有需要在指定小时执行的启用配置。"""
    result = await db.execute(
        select(XianyuAccountAutoRateConfig).where(
            and_(
                XianyuAccountAutoRateConfig.enabled == 1,
                XianyuAccountAutoRateConfig.schedule_hour == schedule_hour,
                XianyuAccountAutoRateConfig.deleted == 0,
            )
        )
    )
    return list(result.scalars().all())


async def _list_pending_rates(
    db: AsyncSession, account_id: int, limit: int = MAX_ORDERS_PER_RUN
) -> list[XianyuRate]:
    """查询本地待评价记录（has_seller_rate=0 且 rate_reviewable=1）。"""
    result = await db.execute(
        select(XianyuRate).where(
            and_(
                XianyuRate.account_id == account_id,
                XianyuRate.has_seller_rate == 0,
                XianyuRate.rate_reviewable == 1,
                XianyuRate.deleted == 0,
            )
        ).order_by(desc(XianyuRate.finish_time)).limit(limit)
    )
    return list(result.scalars().all())


async def _write_log(
    db: AsyncSession,
    *,
    account_id: int,
    schedule_hour: Optional[int],
    trigger_type: str,
    status: str,
    total_pending: int = 0,
    total_success: int = 0,
    total_failed: int = 0,
    total_skipped: int = 0,
    error_message: Optional[str] = None,
    details: Optional[list[dict]] = None,
    duration_seconds: float = 0.0,
) -> None:
    """写入执行日志（best-effort，失败不影响主流程）。"""
    try:
        details_json = json.dumps(details, ensure_ascii=False) if details else None
        log_entry = XianyuAutoRateLog(
            account_id=account_id,
            run_time=datetime.now(),
            schedule_hour=schedule_hour,
            trigger_type=trigger_type,
            status=status,
            total_pending=total_pending,
            total_success=total_success,
            total_failed=total_failed,
            total_skipped=total_skipped,
            error_message=_truncate(error_message or "", 500) or None,
            details_json=details_json,
            duration_seconds=float(duration_seconds),
        )
        db.add(log_entry)
        await db.commit()
    except Exception as exc:
        logger.warning("写入自动评价日志失败 accountId=%s: %s", account_id, exc)
        try:
            await db.rollback()
        except Exception:
            pass


async def _fetch_account_info(
    db: AsyncSession, account_id: int
) -> Optional[XianyuAccount]:
    """查询账号主体（用于校验是否鱼小铺）。"""
    result = await db.execute(
        select(XianyuAccount).where(
            and_(
                XianyuAccount.id == account_id,
                XianyuAccount.deleted == 0,
            )
        )
    )
    return result.scalar_one_or_none()


async def _execute_for_account(
    db: AsyncSession,
    config: XianyuAccountAutoRateConfig,
    trigger_type: str,
) -> dict:
    """对单个账号执行自动补评价。

    返回执行摘要 dict。
    """
    account_id = int(config.account_id)
    schedule_hour = _normalize_schedule_hour(config.schedule_hour)
    started_at = datetime.now()

    # 兜底 1：账号是否仍存在
    account = await _fetch_account_info(db, account_id)
    if account is None:
        await _write_log(
            db,
            account_id=account_id,
            schedule_hour=schedule_hour if trigger_type == "scheduled" else None,
            trigger_type=trigger_type,
            status="skip",
            error_message="账号不存在或已删除",
            duration_seconds=(datetime.now() - started_at).total_seconds(),
        )
        return {"status": "skip", "reason": "account_not_found"}

    # 兜底 2：是否仍为鱼小铺账号 + Cookie 是否有效
    is_fish_shop, auth, fish_err = await verify_fish_shop_account(db, account_id)
    if not is_fish_shop:
        await _write_log(
            db,
            account_id=account_id,
            schedule_hour=schedule_hour if trigger_type == "scheduled" else None,
            trigger_type=trigger_type,
            status="skip",
            error_message=fish_err or "账号不是鱼小铺",
            duration_seconds=(datetime.now() - started_at).total_seconds(),
        )
        return {"status": "skip", "reason": "not_fish_shop"}
    if auth is None:
        await _write_log(
            db,
            account_id=account_id,
            schedule_hour=schedule_hour if trigger_type == "scheduled" else None,
            trigger_type=trigger_type,
            status="skip",
            error_message=fish_err or "Cookie 未配置或已失效",
            duration_seconds=(datetime.now() - started_at).total_seconds(),
        )
        return {"status": "skip", "reason": "cookie_invalid"}

    # 兜底 3：评价内容是否已配置
    rate_type = (config.rate_type or "text").strip().lower()
    text_content = (config.text_content or "").strip()
    api_url = (config.api_url or "").strip()
    if rate_type == "text":
        if not text_content:
            await _write_log(
                db,
                account_id=account_id,
                schedule_hour=schedule_hour if trigger_type == "scheduled" else None,
                trigger_type=trigger_type,
                status="skip",
                error_message="未配置评价内容（text_content 为空）",
                duration_seconds=(datetime.now() - started_at).total_seconds(),
            )
            return {"status": "skip", "reason": "no_text_content"}
        feedback = text_content
    else:
        # rate_type == "api"：当前版本统一回退到 text_content 作为兜底
        if not api_url and not text_content:
            await _write_log(
                db,
                account_id=account_id,
                schedule_hour=schedule_hour if trigger_type == "scheduled" else None,
                trigger_type=trigger_type,
                status="skip",
                error_message="未配置 API 地址与兜底评价内容",
                duration_seconds=(datetime.now() - started_at).total_seconds(),
            )
            return {"status": "skip", "reason": "no_api_and_text"}
        feedback = text_content  # 当前版本 API 模式同样使用 text_content

    # 评价内容长度保护
    if len(feedback) > RATE_FEEDBACK_MAX_LENGTH:
        feedback = feedback[:RATE_FEEDBACK_MAX_LENGTH]

    # 步骤 1：拉取最新评价列表（复用 rate_service）
    try:
        sync_result = await sync_rates_for_account(db, account_id, force_full=False)
        if not sync_result.get("ok"):
            err = sync_result.get("error") or "评价列表同步失败"
            # TASK_ALREADY_RUNNING 视为跳过
            if err == "TASK_ALREADY_RUNNING":
                await _write_log(
                    db,
                    account_id=account_id,
                    schedule_hour=schedule_hour if trigger_type == "scheduled" else None,
                    trigger_type=trigger_type,
                    status="skip",
                    error_message="该账号正在同步中，跳过本次自动评价",
                    duration_seconds=(datetime.now() - started_at).total_seconds(),
                )
                return {"status": "skip", "reason": "sync_in_progress"}
            await _write_log(
                db,
                account_id=account_id,
                schedule_hour=schedule_hour if trigger_type == "scheduled" else None,
                trigger_type=trigger_type,
                status="failed",
                error_message=err,
                duration_seconds=(datetime.now() - started_at).total_seconds(),
            )
            return {"status": "failed", "reason": "sync_failed", "error": err}
    except Exception as exc:
        logger.exception("自动评价同步阶段异常 accountId=%s", account_id)
        await _write_log(
            db,
            account_id=account_id,
            schedule_hour=schedule_hour if trigger_type == "scheduled" else None,
            trigger_type=trigger_type,
            status="failed",
            error_message=f"同步阶段异常: {type(exc).__name__}",
            duration_seconds=(datetime.now() - started_at).total_seconds(),
        )
        return {"status": "failed", "reason": "sync_exception", "error": type(exc).__name__}

    # 步骤 2：查询本地待评价订单
    pending_rates = await _list_pending_rates(db, account_id)
    if not pending_rates:
        await _write_log(
            db,
            account_id=account_id,
            schedule_hour=schedule_hour if trigger_type == "scheduled" else None,
            trigger_type=trigger_type,
            status="success",
            total_pending=0,
            total_success=0,
            total_failed=0,
            total_skipped=0,
            duration_seconds=(datetime.now() - started_at).total_seconds(),
        )
        return {"status": "success", "total_pending": 0, "total_success": 0, "total_failed": 0}

    # 步骤 3：逐条提交评价
    details: list[dict] = []
    total_success = 0
    total_failed = 0
    total_skipped = 0

    for rate_record in pending_rates:
        order_id = rate_record.external_order_id
        # 双重校验：避免在循环中已被其他途径评价
        if rate_record.has_seller_rate == 1:
            total_skipped += 1
            details.append({"orderId": order_id, "status": "skipped", "reason": "already_rated"})
            continue
        if rate_record.rate_reviewable != 1:
            total_skipped += 1
            details.append({"orderId": order_id, "status": "skipped", "reason": "not_reviewable"})
            continue

        try:
            result = await create_rate(
                db,
                account_id=account_id,
                order_id=order_id,
                rate=RATE_LEVEL_GOOD,  # 仅好评
                feedback=feedback,
                anonymous=True,  # 默认匿名
            )
        except Exception as exc:
            logger.exception("自动评价提交异常 accountId=%s orderId=%s", account_id, order_id)
            total_failed += 1
            details.append({
                "orderId": order_id,
                "status": "failed",
                "error": f"提交异常: {type(exc).__name__}",
            })
            await asyncio.sleep(RATE_SUBMIT_INTERVAL_SECONDS)
            continue

        if result.get("ok"):
            total_success += 1
            details.append({"orderId": order_id, "status": "success"})
        else:
            err = result.get("error") or "评价失败"
            # CREATE_RATE_IN_PROGRESS 视为跳过
            if err == "CREATE_RATE_IN_PROGRESS":
                total_skipped += 1
                details.append({"orderId": order_id, "status": "skipped", "reason": "in_progress"})
            else:
                total_failed += 1
                details.append({"orderId": order_id, "status": "failed", "error": _truncate(err, 200)})

        # 评价间隔，避免风控
        await asyncio.sleep(RATE_SUBMIT_INTERVAL_SECONDS)

    # 步骤 4：汇总状态
    if total_failed == 0:
        status = "success"
    elif total_success == 0:
        status = "failed"
    else:
        status = "partial"

    await _write_log(
        db,
        account_id=account_id,
        schedule_hour=schedule_hour if trigger_type == "scheduled" else None,
        trigger_type=trigger_type,
        status=status,
        total_pending=len(pending_rates),
        total_success=total_success,
        total_failed=total_failed,
        total_skipped=total_skipped,
        error_message=None if status == "success" else f"成功 {total_success} / 失败 {total_failed} / 跳过 {total_skipped}",
        details=details,
        duration_seconds=(datetime.now() - started_at).total_seconds(),
    )

    return {
        "status": status,
        "total_pending": len(pending_rates),
        "total_success": total_success,
        "total_failed": total_failed,
        "total_skipped": total_skipped,
    }


async def run_auto_rate_for_account(
    account_id: int, trigger_type: str = "manual"
) -> dict:
    """手动触发单个账号的自动补评价（供 API 调用）。

    与定时任务共用同一执行路径，但 trigger_type=manual。
    使用进程级锁防止同一账号同时被定时任务和手动触发并发执行。
    """
    from ..core.database import async_session

    lock = await _get_run_lock(account_id)
    if lock.locked():
        return {"ok": False, "error": "RUN_IN_PROGRESS"}

    async with lock:
        async with async_session() as db:
            # 读取配置
            result = await db.execute(
                select(XianyuAccountAutoRateConfig).where(
                    and_(
                        XianyuAccountAutoRateConfig.account_id == account_id,
                        XianyuAccountAutoRateConfig.deleted == 0,
                    )
                )
            )
            config = result.scalar_one_or_none()
            if config is None:
                return {"ok": False, "error": "未配置自动评价，请先在账号配置中开启"}
            if config.enabled != 1:
                return {"ok": False, "error": "该账号未开启自动评价"}

            summary = await _execute_for_account(db, config, trigger_type=trigger_type)
            return {"ok": True, "summary": summary}


async def _scheduler_loop() -> None:
    """调度器主循环：每小时检查一次 schedule_hour 匹配的账号。"""
    global _last_scan_result, _last_scan_at

    logger.info(
        "自动补评价调度器启动，首次扫描将在 %d 秒后开始，间隔 %d 秒",
        INITIAL_DELAY_SECONDS, SCAN_INTERVAL_SECONDS,
    )

    # 启动延迟
    try:
        await asyncio.wait_for(_scheduler_stop_event.wait(), timeout=INITIAL_DELAY_SECONDS)
        logger.info("自动补评价调度器在启动延迟期收到停止信号，退出")
        return
    except asyncio.TimeoutError:
        pass

    while not _scheduler_stop_event.is_set():
        scan_start = datetime.now()
        scan_hour = scan_start.hour
        accounts_run = 0
        accounts_skip = 0
        accounts_failed = 0
        errors: list[str] = []

        try:
            from ..core.database import async_session

            # 先查询所有 due 配置，再逐个执行（避免长事务）
            async with async_session() as db:
                due_configs = await _list_due_configs(db, scan_hour)

            logger.info("自动补评价扫描: hour=%d 待执行账号数=%d", scan_hour, len(due_configs))

            for config in due_configs:
                if _scheduler_stop_event.is_set():
                    break

                account_id = int(config.account_id)

                # 进程级锁：避免与手动触发并发
                lock = await _get_run_lock(account_id)
                if lock.locked():
                    accounts_skip += 1
                    errors.append(f"accountId={account_id} 正在执行中，跳过")
                    continue

                async with lock:
                    try:
                        async with async_session() as db:
                            # 重新加载 config 以获取最新字段
                            fresh_result = await db.execute(
                                select(XianyuAccountAutoRateConfig).where(
                                    and_(
                                        XianyuAccountAutoRateConfig.id == config.id,
                                        XianyuAccountAutoRateConfig.deleted == 0,
                                    )
                                )
                            )
                            fresh_config = fresh_result.scalar_one_or_none()
                            if fresh_config is None or fresh_config.enabled != 1:
                                accounts_skip += 1
                                continue

                            summary = await _execute_for_account(db, fresh_config, trigger_type="scheduled")

                        status = summary.get("status")
                        if status == "skip":
                            accounts_skip += 1
                        elif status == "failed":
                            accounts_failed += 1
                        else:
                            accounts_run += 1
                    except Exception as exc:
                        logger.exception("自动补评价执行异常 accountId=%s", account_id)
                        accounts_failed += 1
                        errors.append(f"accountId={account_id} {type(exc).__name__}: {str(exc)[:100]}")

            _last_scan_result = {
                "hour": scan_hour,
                "due_accounts": len(due_configs),
                "accounts_run": accounts_run,
                "accounts_skip": accounts_skip,
                "accounts_failed": accounts_failed,
                "errors": errors[:10],
            }
            _last_scan_at = scan_start

            logger.info(
                "自动补评价扫描完成: hour=%d due=%d run=%d skip=%d failed=%d",
                scan_hour, len(due_configs), accounts_run, accounts_skip, accounts_failed,
            )
        except Exception as e:
            logger.exception("自动补评价调度器扫描异常: %s", e)
            _last_scan_result = {"error": str(e)}
            _last_scan_at = scan_start

        # 计算下一次扫描时间：对齐到下一个整点的第 5 分钟
        now = datetime.now()
        next_run = now.replace(minute=5, second=0, microsecond=0)
        if next_run <= now:
            # 已过了本小时的 :05，等到下一小时的 :05
            next_run = next_run + timedelta(hours=1)
        sleep_seconds = (next_run - now).total_seconds()
        # 兜底：sleep_seconds 异常时使用 SCAN_INTERVAL_SECONDS
        if sleep_seconds <= 0 or sleep_seconds > SCAN_INTERVAL_SECONDS + 60:
            sleep_seconds = SCAN_INTERVAL_SECONDS

        try:
            await asyncio.wait_for(_scheduler_stop_event.wait(), timeout=sleep_seconds)
            # stop_event 被设置，退出
            break
        except asyncio.TimeoutError:
            continue

    logger.info("自动补评价调度器已退出")


async def start_auto_rate_scheduler() -> None:
    """启动自动补评价调度器（幂等）。"""
    global _scheduler_task, _scheduler_started, _scheduler_stop_event

    if _scheduler_started and _scheduler_task and not _scheduler_task.done():
        logger.info("自动补评价调度器已在运行，跳过重复启动")
        return

    _scheduler_stop_event = asyncio.Event()
    _scheduler_task = asyncio.create_task(_scheduler_loop(), name="auto_rate_scheduler")
    _scheduler_started = True
    logger.info("自动补评价调度器已注册")


async def stop_auto_rate_scheduler() -> None:
    """停止自动补评价调度器（幂等）。"""
    global _scheduler_task, _scheduler_started, _scheduler_stop_event

    if not _scheduler_started:
        return

    if _scheduler_stop_event:
        _scheduler_stop_event.set()

    if _scheduler_task and not _scheduler_task.done():
        try:
            await asyncio.wait_for(_scheduler_task, timeout=10)
        except asyncio.TimeoutError:
            _scheduler_task.cancel()
            try:
                await _scheduler_task
            except (asyncio.CancelledError, Exception):
                pass

    _scheduler_started = False
    _scheduler_task = None
    _scheduler_stop_event = None
    logger.info("自动补评价调度器已停止")


def get_scheduler_status() -> dict:
    """获取调度器状态（供诊断接口使用）。"""
    return {
        "started": _scheduler_started,
        "running": bool(_scheduler_task and not _scheduler_task.done()),
        "last_scan_at": _last_scan_at.isoformat() if _last_scan_at else None,
        "last_scan_result": _last_scan_result,
        "scan_interval_seconds": SCAN_INTERVAL_SECONDS,
    }


async def list_auto_rate_logs(
    db: AsyncSession,
    account_id: Optional[int] = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """查询自动评价执行日志（分页）。"""
    page = max(1, page)
    page_size = max(1, min(page_size, 100))
    offset = (page - 1) * page_size

    conditions = [
        XianyuAutoRateLog.deleted == 0,
    ]
    if account_id is not None:
        conditions.append(XianyuAutoRateLog.account_id == account_id)

    # 计数
    from sqlalchemy import func as _func
    count_result = await db.execute(
        select(_func.count(XianyuAutoRateLog.id)).where(and_(*conditions))
    )
    total = int(count_result.scalar() or 0)

    # 列表
    list_result = await db.execute(
        select(XianyuAutoRateLog)
        .where(and_(*conditions))
        .order_by(desc(XianyuAutoRateLog.run_time))
        .offset(offset)
        .limit(page_size)
    )
    rows = list_result.scalars().all()

    records = []
    for row in rows:
        records.append({
            "id": int(row.id),
            "accountId": int(row.account_id),
            "runTime": row.run_time.isoformat() if row.run_time else None,
            "scheduleHour": row.schedule_hour,
            "triggerType": row.trigger_type,
            "status": row.status,
            "totalPending": int(row.total_pending),
            "totalSuccess": int(row.total_success),
            "totalFailed": int(row.total_failed),
            "totalSkipped": int(row.total_skipped),
            "errorMessage": row.error_message,
            "details": json.loads(row.details_json) if row.details_json else None,
            "durationSeconds": float(row.duration_seconds),
        })

    return {
        "records": records,
        "total": total,
        "page": page,
        "pageSize": page_size,
    }
