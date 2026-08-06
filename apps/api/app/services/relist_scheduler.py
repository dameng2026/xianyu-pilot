"""
售整自动上架定时调度器。

每 3 分钟轮询一次，调用 relist_service.scan_and_relist() 扫描所有符合重发条件的商品并执行重发。

调度策略：
- 单实例运行：通过 _scheduler_started 标志防止重复启动；
- 异常容错：单次扫描异常不影响下一次；
- 优雅关闭：通过 _scheduler_stop_event 通知退出；
- 启动延迟：服务启动后 60 秒开始首次扫描，避免与启动初始化竞争。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# 扫描间隔（秒）= 3 分钟
SCAN_INTERVAL_SECONDS = 180

# 启动后延迟首次扫描（秒）= 60 秒
INITIAL_DELAY_SECONDS = 60

# 调度器状态
_scheduler_task: Optional[asyncio.Task] = None
_scheduler_started: bool = False
_scheduler_stop_event: Optional[asyncio.Event] = None

# 最近一次扫描结果
_last_scan_result: Optional[dict] = None
_last_scan_at: Optional[datetime] = None


async def _scheduler_loop() -> None:
    """调度器主循环。"""
    global _last_scan_result, _last_scan_at

    logger.info(
        "售整自动上架调度器启动，首次扫描将在 %d 秒后开始，间隔 %d 秒",
        INITIAL_DELAY_SECONDS, SCAN_INTERVAL_SECONDS,
    )

    # 启动延迟
    try:
        await asyncio.wait_for(_scheduler_stop_event.wait(), timeout=INITIAL_DELAY_SECONDS)
        # 如果 stop_event 在延迟期内被设置，直接退出
        logger.info("售整自动上架调度器在启动延迟期收到停止信号，退出")
        return
    except asyncio.TimeoutError:
        pass  # 正常超时继续执行

    while not _scheduler_stop_event.is_set():
        start_time = datetime.now()
        try:
            # 延迟导入避免循环依赖
            from .relist_service import scan_and_relist
            result = await scan_and_relist()
            _last_scan_result = result
            _last_scan_at = start_time
            if result.get("total_relisted") or result.get("total_failed"):
                logger.info(
                    "售整自动上架扫描完成: accounts=%d, relisted=%d, failed=%d",
                    result.get("scanned_accounts", 0),
                    result.get("total_relisted", 0),
                    result.get("total_failed", 0),
                )
        except Exception as e:
            logger.exception("售整自动上架调度器扫描异常: %s", e)
            _last_scan_result = {"error": str(e)}
            _last_scan_at = start_time

        # 等待下一次扫描，支持提前退出
        try:
            await asyncio.wait_for(_scheduler_stop_event.wait(), timeout=SCAN_INTERVAL_SECONDS)
            # stop_event 被设置，退出
            break
        except asyncio.TimeoutError:
            continue

    logger.info("售整自动上架调度器已退出")


async def start_relist_scheduler() -> None:
    """启动售整自动上架调度器（幂等）。"""
    global _scheduler_task, _scheduler_started, _scheduler_stop_event

    if _scheduler_started and _scheduler_task and not _scheduler_task.done():
        logger.info("售整自动上架调度器已在运行，跳过重复启动")
        return

    _scheduler_stop_event = asyncio.Event()
    _scheduler_task = asyncio.create_task(_scheduler_loop(), name="relist_scheduler")
    _scheduler_started = True
    logger.info("售整自动上架调度器已注册")


async def stop_relist_scheduler() -> None:
    """停止售整自动上架调度器（幂等）。"""
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
    logger.info("售整自动上架调度器已停止")


def get_scheduler_status() -> dict:
    """获取调度器状态（供诊断接口使用）。"""
    return {
        "started": _scheduler_started,
        "running": bool(_scheduler_task and not _scheduler_task.done()),
        "last_scan_at": _last_scan_at.isoformat() if _last_scan_at else None,
        "last_scan_result": _last_scan_result,
        "scan_interval_seconds": SCAN_INTERVAL_SECONDS,
    }
