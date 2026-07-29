"""Standalone reliable scheduler worker.

Run with ``python -m app.worker``. The API process never starts an implicit
scheduler, so production has exactly the replicas declared by deployment.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import tempfile
import time
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from .core.atomic_file import atomic_replace_with_retry
from .core.config import settings
from .core.database import async_session
from .core.logging_security import install_log_redaction
from .migrations import assert_schema_current
from .services.audit_retention import run_audit_retention_once
from .services.delivery_recovery import run_delivery_recovery_once
from .services.scheduled_task_runtime import get_scheduled_task_runtime

logger = logging.getLogger(__name__)


def _heartbeat_path() -> Path:
    configured = (os.getenv("SCHEDULER_HEARTBEAT_PATH") or "").strip()
    if configured:
        return Path(configured)
    return Path(tempfile.gettempdir()) / "xianyu-scheduler-heartbeat"


def _write_heartbeat(path: Path, *, status: str = "ok") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {"status": status, "updatedAt": time.time()},
        ensure_ascii=True,
        separators=(",", ":"),
    )
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(payload, encoding="ascii")
        atomic_replace_with_retry(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def heartbeat_is_fresh(
    path: Path | None = None,
    *,
    max_age_seconds: int | None = None,
) -> bool:
    heartbeat = path or _heartbeat_path()
    max_age = max_age_seconds or int(os.getenv("SCHEDULER_HEARTBEAT_MAX_AGE_SECONDS", "60"))
    try:
        age = time.time() - heartbeat.stat().st_mtime
        payload = json.loads(heartbeat.read_text(encoding="ascii"))
    except OSError:
        return False
    except (UnicodeError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict) or payload.get("status") not in {"ok", "running"}:
        return False
    return -5 <= age <= max_age


async def run_once(limit: int | None = None) -> dict[str, Any]:
    """Atomically claim and execute one bounded batch of due tasks."""

    batch_size = limit if limit is not None else settings.scheduler_batch_size
    return await get_scheduled_task_runtime().run_due_once(limit=batch_size)


async def run_maintenance_once() -> dict[str, int | bool]:
    """Run bounded, durably throttled maintenance under its own transaction."""
    async with async_session() as db:
        return await run_audit_retention_once(
            db,
            retention_days=settings.audit_log_retention_days,
        )


async def run_delivery_recovery_forever(
    interval_seconds: int | None = None,
    *,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Poll pending auto-delivery orders and safely resend via the idempotent
    realtime delivery state machine.

    One order failure never terminates the loop. The interval defaults to
    ``settings.delivery_recovery_interval_seconds`` (10 minutes) and is bound
    to [60, 86400] seconds. Disabled via ``settings.delivery_recovery_enabled``.
    """

    if not settings.delivery_recovery_enabled:
        logger.info("delivery_recovery disabled by config; loop not started")
        return

    interval = int(
        interval_seconds
        if interval_seconds is not None
        else settings.delivery_recovery_interval_seconds
    )
    interval = max(60, min(interval, 86_400))
    stop = stop_event or asyncio.Event()
    logger.info(
        "delivery_recovery loop started interval=%ss batch=%s minAge=%ss",
        interval,
        settings.delivery_recovery_batch_size,
        settings.delivery_recovery_min_age_seconds,
    )
    while not stop.is_set():
        try:
            result = await run_delivery_recovery_once()
            if result.get("scanned"):
                logger.info("delivery_recovery batch: %s", result)
        except Exception:
            logger.error("delivery_recovery loop failed", exc_info=True)
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            continue


async def _heartbeat_loop(
    stop_event: asyncio.Event,
    path: Path,
    status_provider: Callable[[], str],
) -> None:
    interval = max(5, min(settings.scheduler_poll_interval_seconds, 15))
    while not stop_event.is_set():
        try:
            await asyncio.to_thread(
                _write_heartbeat,
                path,
                status=status_provider(),
            )
        except OSError:
            logger.error("Unable to update scheduler heartbeat", exc_info=True)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            continue


async def run_forever(
    interval_seconds: int | None = None,
    *,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Poll until stopped; one task failure never terminates the worker."""

    interval = interval_seconds or settings.scheduler_poll_interval_seconds
    interval = max(1, min(int(interval), 300))
    stop = stop_event or asyncio.Event()
    heartbeat_path = _heartbeat_path()
    health = {"status": "starting"}
    heartbeat_task = asyncio.create_task(
        _heartbeat_loop(stop, heartbeat_path, lambda: health["status"])
    )
    logger.info(
        "scheduler worker started interval=%ss batch=%s timeout=%ss",
        interval,
        settings.scheduler_batch_size,
        settings.scheduler_task_timeout_seconds,
    )
    try:
        while not stop.is_set():
            health["status"] = (
                "recovering" if health["status"] in {"error", "recovering"} else "running"
            )
            await asyncio.to_thread(
                _write_heartbeat,
                heartbeat_path,
                status=health["status"],
            )
            try:
                result = await run_once()
                retention = await run_maintenance_once()
                health["status"] = "ok"
                await asyncio.to_thread(
                    _write_heartbeat,
                    heartbeat_path,
                    status="ok",
                )
                if result["processed"]:
                    logger.info("scheduler worker completed batch: %s", result)
                if retention.get("deleted"):
                    logger.info("scheduler worker completed maintenance: %s", retention)
            except Exception:
                health["status"] = "error"
                try:
                    await asyncio.to_thread(
                        _write_heartbeat,
                        heartbeat_path,
                        status="error",
                    )
                except OSError:
                    logger.error("Unable to persist failed scheduler health state", exc_info=True)
                logger.error("scheduler worker polling failed", exc_info=True)

            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                continue
    finally:
        stop.set()
        await heartbeat_task
        try:
            heartbeat_path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Unable to remove scheduler heartbeat", exc_info=True)
        logger.info("scheduler worker stopped")


async def _serve() -> None:
    # Workers never mutate schema. A deployment must complete the explicit
    # migration job before any scheduler replica may claim external work.
    await assert_schema_current()
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stop_event.set)
        except (NotImplementedError, RuntimeError):
            # Windows development shells do not implement loop signal handlers.
            pass
    # 补发货兜底循环与调度任务循环独立运行，单循环失败不影响另一个。
    recovery_task = asyncio.create_task(
        run_delivery_recovery_forever(stop_event=stop_event),
        name="delivery-recovery-loop",
    )
    try:
        await run_forever(stop_event=stop_event)
    finally:
        stop_event.set()
        await asyncio.gather(recovery_task, return_exceptions=True)
        from .core.database import engine

        await engine.dispose()


def _configure_logging() -> None:
    install_log_redaction()
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def main() -> int:
    _configure_logging()
    parser = argparse.ArgumentParser(description="Xianyu scheduled-task worker")
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit zero only when the worker heartbeat is fresh",
    )
    args = parser.parse_args()
    if args.check:
        return 0 if heartbeat_is_fresh() else 1

    asyncio.run(_serve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
