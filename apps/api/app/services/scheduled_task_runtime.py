"""Reliable scheduled-task validation, execution and MySQL persistence.

The public module interface is intentionally small: callers submit a validated
``ScheduledTaskInput`` and use ``ScheduledTaskRuntime`` for CRUD/manual/worker
operations.  MySQL details and Xianyu adapters stay behind that seam.
"""

from __future__ import annotations

import json
import asyncio
import logging
import os
import socket
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Protocol

from croniter import CroniterBadCronError, croniter
from pydantic import ConfigDict, Field, field_validator, model_validator

from ..core.camel import CamelModel
from sqlalchemy import text

from ..core.logging_security import redact_sensitive, redact_sensitive_text

SUPPORTED_TASK_TYPES = frozenset({"sync_goods", "sync_orders"})
logger = logging.getLogger(__name__)

CookieLoader = Callable[[int], Awaitable[str]]
TaskSyncAdapter = Callable[..., Awaitable[dict[str, Any]]]


@dataclass(frozen=True, slots=True)
class ScheduledTaskRecord:
    id: int
    task_name: str
    task_type: str
    cron_expression: str
    config: dict[str, Any]
    enabled: bool
    last_run_time: datetime | None = None
    next_run_time: datetime | None = None
    last_status: str | None = None
    last_result: dict[str, Any] | None = None
    lease_token: str | None = None
    lease_until: datetime | None = None
    lease_owner: str | None = None
    created_time: datetime | None = None
    updated_time: datetime | None = None


@dataclass(frozen=True, slots=True)
class TaskExecutionOutcome:
    status: str
    result: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class ScheduledTaskError(RuntimeError):
    """Base error safe to translate at the HTTP seam."""


class TaskNotFoundError(ScheduledTaskError):
    pass


class TaskBusyError(ScheduledTaskError):
    pass


class TaskConflictError(ScheduledTaskError):
    pass


class TaskValidationError(ScheduledTaskError):
    pass


class ScheduledTaskStore(Protocol):
    async def account_is_active(self, account_id: int) -> bool: ...

    async def list(self, *, current: int, size: int) -> tuple[list[ScheduledTaskRecord], int]: ...

    async def create(
        self,
        task: ScheduledTaskInput,
        *,
        next_run_time: datetime | None,
    ) -> ScheduledTaskRecord: ...

    async def update(
        self,
        task_id: int,
        task: ScheduledTaskInput,
        *,
        next_run_time: datetime | None,
    ) -> ScheduledTaskRecord: ...

    async def delete(self, task_id: int) -> None: ...


    async def set_enabled(
        self,
        task_id: int,
        *,
        enabled: bool,
    ) -> ScheduledTaskRecord:
        now = datetime.now()
        async with self._session_factory() as db:
            existing = await self._load_locked_or_plain(db, task_id, for_update=True)
            if existing is None:
                await db.rollback()
                raise TaskNotFoundError("定时任务不存在")
            if existing["lease_until"] and existing["lease_until"] > now:
                await db.rollback()
                raise TaskBusyError("任务正在运行，暂不能切换启用状态")
            new_status = 1 if enabled else 0
            if enabled:
                try:
                    next_run = next_cron_time(str(existing["cron_expr"] or ""), now)
                except ValueError as exc:
                    await db.rollback()
                    raise TaskValidationError(
                        redact_sensitive_text(str(exc))[:1000]
                    ) from exc
            else:
                next_run = None
            await db.execute(
                text(
                    """
                    UPDATE scheduled_task
                    SET status = :status,
                        next_run_time = :next_run_time,
                        updated_time = :now
                    WHERE id = :id AND deleted = 0
                    """
                ),
                {
                    "id": task_id,
                    "status": new_status,
                    "next_run_time": next_run,
                    "now": now,
                },
            )
            row = await self._load_locked_or_plain(db, task_id)
            await db.commit()
        if row is None:
            raise TaskConflictError("定时任务启用状态更新后无法读取")
        return _record_from_row(row)

    async def claim_manual(
        self,
        task_id: int,
        lease_token: str,
        *,
        now: datetime,
        lease_until: datetime,
        lease_owner: str,
    ) -> ScheduledTaskRecord: ...

    async def claim_due(
        self,
        *,
        limit: int,
        now: datetime,
        lease_until: datetime,
        lease_owner: str,
    ) -> list[ScheduledTaskRecord]: ...

    async def finish(
        self,
        task: ScheduledTaskRecord,
        outcome: TaskExecutionOutcome,
        *,
        finished_at: datetime,
        next_run_time: datetime | None,
        disable: bool,
    ) -> ScheduledTaskRecord: ...


class TaskExecutor(Protocol):
    async def execute(self, task: ScheduledTaskRecord) -> TaskExecutionOutcome: ...


class ScheduledTaskRuntime:
    """Coordinate leases, validation, timeout and durable outcomes."""

    def __init__(
        self,
        *,
        store: ScheduledTaskStore,
        executor: TaskExecutor,
        timeout_seconds: int,
        worker_id: str | None = None,
        lease_seconds: int | None = None,
    ) -> None:
        if timeout_seconds < 1:
            raise ValueError("timeout_seconds must be positive")
        self.store = store
        self.executor = executor
        self.timeout_seconds = timeout_seconds
        self.lease_seconds = max(lease_seconds or timeout_seconds + 60, timeout_seconds + 30)
        self.worker_id = worker_id or f"runtime-{uuid.uuid4().hex[:12]}"

    async def list(
        self,
        *,
        current: int,
        size: int,
    ) -> tuple[list[ScheduledTaskRecord], int]:
        return await self.store.list(current=current, size=size)

    async def create(self, task: ScheduledTaskInput) -> ScheduledTaskRecord:
        await self._require_active_account(task.account_id)
        next_run = next_cron_time(task.cron_expression) if task.enabled else None
        return await self.store.create(task, next_run_time=next_run)

    async def update(self, task_id: int, task: ScheduledTaskInput) -> ScheduledTaskRecord:
        await self._require_active_account(task.account_id)
        next_run = next_cron_time(task.cron_expression) if task.enabled else None
        return await self.store.update(task_id, task, next_run_time=next_run)

    async def delete(self, task_id: int) -> None:
        await self.store.delete(task_id)

    async def set_enabled(self, task_id: int, *, enabled: bool) -> ScheduledTaskRecord:
        """Toggle a task's enabled flag without rewriting its full payload.

        Disabling clears next_run_time; enabling recomputes it from the
        stored cron expression so the worker can resume scheduling.
        """

        record = await self.store.set_enabled(task_id, enabled=enabled)
        return record

    async def _require_active_account(self, account_id: int) -> None:
        if not await self.store.account_is_active(account_id):
            raise TaskValidationError("关联账号不存在或已禁用")

    async def run_manual(self, task_id: int) -> TaskExecutionOutcome:
        now = datetime.now()
        lease_token = uuid.uuid4().hex
        task = await self.store.claim_manual(
            task_id,
            lease_token,
            now=now,
            lease_until=now + timedelta(seconds=self.lease_seconds),
            lease_owner=self.worker_id,
        )
        return await self._execute_claimed(task)

    async def run_due_once(self, limit: int = 20) -> dict[str, Any]:
        bounded_limit = max(1, min(int(limit), 100))
        now = datetime.now()
        tasks = await self.store.claim_due(
            limit=bounded_limit,
            now=now,
            lease_until=now + timedelta(seconds=self.lease_seconds),
            lease_owner=self.worker_id,
        )
        results: list[dict[str, Any]] = []
        for task in tasks:
            try:
                outcome = await self._execute_claimed(task)
                results.append({"taskId": task.id, "status": outcome.status})
            except Exception:
                # A persistence/connection failure for one claimed task must
                # not prevent later claims from completing. Its finite lease
                # makes it eligible for safe retry after recovery.
                logger.error(
                    "Scheduled task finalization failed task_id=%s",
                    task.id,
                    exc_info=True,
                )
                results.append({"taskId": task.id, "status": "persistence_failed"})
        return {"processed": len(tasks), "results": results}

    async def _execute_claimed(self, task: ScheduledTaskRecord) -> TaskExecutionOutcome:
        disable = False
        try:
            account_id = _account_id_from_config(task.config)
            if task.task_type not in SUPPORTED_TASK_TYPES:
                disable = True
                outcome = TaskExecutionOutcome(
                    status="unsupported",
                    error="该任务类型已不受支持，任务已自动禁用",
                )
            elif not await self.store.account_is_active(account_id):
                disable = True
                outcome = TaskExecutionOutcome(
                    status="unavailable",
                    error="关联账号不存在或已禁用，任务已自动禁用",
                )
            else:
                outcome = await asyncio.wait_for(
                    self.executor.execute(task),
                    timeout=self.timeout_seconds,
                )
                outcome = _sanitized_outcome(outcome)
                disable = outcome.status == "unsupported"
        except asyncio.TimeoutError:
            outcome = TaskExecutionOutcome(
                status="timeout",
                error=f"任务执行超过 {self.timeout_seconds} 秒，已中止等待",
            )
        except ValueError as exc:
            disable = True
            outcome = TaskExecutionOutcome(
                status="unavailable",
                error=redact_sensitive_text(str(exc))[:1000],
            )
        except Exception as exc:
            logger.error(
                "Scheduled task execution failed task_id=%s task_type=%s",
                task.id,
                task.task_type,
                exc_info=True,
            )
            outcome = TaskExecutionOutcome(
                status="failed",
                error=redact_sensitive_text(str(exc) or "任务执行失败")[:1000],
            )

        finished_at = datetime.now()
        next_run = None
        if task.enabled and not disable:
            try:
                next_run = next_cron_time(task.cron_expression, finished_at)
            except ValueError as exc:
                disable = True
                outcome = TaskExecutionOutcome(
                    status="unavailable",
                    error=redact_sensitive_text(str(exc))[:1000],
                )

        await self.store.finish(
            task,
            outcome,
            finished_at=finished_at,
            next_run_time=next_run,
            disable=disable,
        )
        return outcome


class ScheduledTaskExecutor:
    """Execute the two supported task types through injectable adapters."""

    def __init__(
        self,
        *,
        cookie_loader: CookieLoader,
        goods_sync: TaskSyncAdapter,
        orders_sync: TaskSyncAdapter,
    ) -> None:
        self._cookie_loader = cookie_loader
        self._goods_sync = goods_sync
        self._orders_sync = orders_sync

    async def execute(self, task: ScheduledTaskRecord) -> TaskExecutionOutcome:
        if task.task_type not in SUPPORTED_TASK_TYPES:
            return TaskExecutionOutcome(
                status="unsupported",
                error="该任务类型已不受支持，请删除后创建同步商品或同步订单任务",
            )

        account_id = _account_id_from_config(task.config)
        if task.task_type == "sync_goods":
            cookie = await self._cookie_loader(account_id)
            result = await self._goods_sync(
                account_id=account_id,
                cookie_str=cookie,
                sync_id=f"scheduled-{task.id}-{uuid.uuid4().hex[:12]}",
                db_session_factory=None,
                # The existing sync adapter starts detail work in a detached
                # background task when true. A scheduled run only reports work
                # that has actually completed, so list synchronization is used.
                async_fetch_detail=False,
            )
        else:
            result = await self._orders_sync(account_id=account_id)
            if not result.get("success", False):
                return TaskExecutionOutcome(
                    status="failed",
                    result=_json_safe_result(result),
                    error=str(result.get("error") or "订单同步失败"),
                )

        return TaskExecutionOutcome(status="success", result=_json_safe_result(result))


def _account_id_from_config(config: dict[str, Any]) -> int:
    value = config.get("accountId", config.get("account_id"))
    try:
        account_id = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("任务配置缺少有效 accountId") from exc
    if account_id <= 0:
        raise ValueError("任务配置缺少有效 accountId")
    return account_id


def _json_safe_result(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"value": redact_sensitive_text(str(value), max_length=2000)}
    # Round-trip also prevents ORM/custom objects from leaking through the API
    # or failing a MySQL JSON write. The persisted result is capped later.
    serializable = json.loads(json.dumps(value, ensure_ascii=False, default=str))
    return redact_sensitive(serializable)


def _sanitized_outcome(outcome: TaskExecutionOutcome) -> TaskExecutionOutcome:
    allowed_statuses = {"success", "failed", "timeout", "unsupported", "unavailable"}
    status = outcome.status if outcome.status in allowed_statuses else "failed"
    return TaskExecutionOutcome(
        status=status,
        result=_json_safe_result(outcome.result),
        error=(
            redact_sensitive_text(outcome.error, max_length=1000)
            if outcome.error
            else None
        ),
    )


_TASK_COLUMNS = """
    id, task_name, task_type, cron_expr, config, status,
    last_run_time, next_run_time, last_status, last_result,
    lease_token, lease_until, lease_owner, created_time, updated_time
"""


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return _json_safe_result(value)
    if value in (None, ""):
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return _json_safe_result(parsed) if isinstance(parsed, dict) else {}


def _record_from_row(row: Any) -> ScheduledTaskRecord:
    return ScheduledTaskRecord(
        id=int(row["id"]),
        task_name=str(row["task_name"] or ""),
        task_type=str(row["task_type"] or "").strip().lower(),
        cron_expression=str(row["cron_expr"] or ""),
        config=_json_object(row["config"]),
        enabled=int(row["status"] or 0) == 1,
        last_run_time=row["last_run_time"],
        next_run_time=row["next_run_time"],
        last_status=str(row["last_status"] or "") or None,
        last_result=_json_object(row["last_result"]) or None,
        lease_token=str(row["lease_token"] or "") or None,
        lease_until=row["lease_until"],
        lease_owner=str(row["lease_owner"] or "") or None,
        created_time=row["created_time"],
        updated_time=row["updated_time"],
    )


def _outcome_json(outcome: TaskExecutionOutcome) -> str:
    payload = redact_sensitive(
        {
            "result": _json_safe_result(outcome.result),
            "error": redact_sensitive_text(outcome.error, max_length=1000) if outcome.error else None,
        }
    )
    encoded = json.dumps(payload, ensure_ascii=False, default=str)
    if len(encoded.encode("utf-8")) > 16_384:
        encoded = json.dumps(
            {
                "result": {"truncated": True},
                "error": payload.get("error"),
            },
            ensure_ascii=False,
        )
    return encoded


class MySQLScheduledTaskStore:
    """Raw-SQL adapter matching ``migrations/001`` + runtime migration 002."""

    def __init__(self, session_factory: Callable[[], Any]) -> None:
        self._session_factory = session_factory

    async def account_is_active(self, account_id: int) -> bool:
        async with self._session_factory() as db:
            result = await db.execute(
                text(
                    "SELECT 1 FROM xianyu_account "
                    "WHERE id = :account_id AND status = 1 AND deleted = 0 LIMIT 1"
                ),
                {"account_id": account_id},
            )
            return result.first() is not None

    async def list(
        self,
        *,
        current: int,
        size: int,
    ) -> tuple[list[ScheduledTaskRecord], int]:
        offset = (current - 1) * size
        async with self._session_factory() as db:
            total_result = await db.execute(
                text("SELECT COUNT(*) FROM scheduled_task WHERE deleted = 0")
            )
            total = int(total_result.scalar() or 0)
            rows_result = await db.execute(
                text(
                    f"SELECT {_TASK_COLUMNS} FROM scheduled_task "
                    "WHERE deleted = 0 ORDER BY id DESC LIMIT :limit OFFSET :offset"
                ),
                {"limit": size, "offset": offset},
            )
            records = [_record_from_row(row) for row in rows_result.mappings().all()]
        return records, total

    async def create(
        self,
        task: ScheduledTaskInput,
        *,
        next_run_time: datetime | None,
    ) -> ScheduledTaskRecord:
        now = datetime.now()
        async with self._session_factory() as db:
            insert_result = await db.execute(
                text(
                    """
                    INSERT INTO scheduled_task (
                      task_name, task_type, cron_expr, config, status,
                      last_run_time, next_run_time, last_status, last_result,
                      lease_token, lease_until, lease_owner,
                      created_time, updated_time, deleted
                    ) VALUES (
                      :task_name, :task_type, :cron_expr, :config, :status,
                      NULL, :next_run_time, NULL, NULL,
                      NULL, NULL, NULL, :now, :now, 0
                    )
                    """
                ),
                {
                    "task_name": task.task_name,
                    "task_type": task.task_type,
                    "cron_expr": task.cron_expression,
                    "config": json.dumps(task.config, ensure_ascii=False),
                    "status": 1 if task.enabled else 0,
                    "next_run_time": next_run_time,
                    "now": now,
                },
            )
            task_id = int(insert_result.lastrowid)
            row = await self._load_locked_or_plain(db, task_id)
            await db.commit()
        if row is None:
            raise TaskConflictError("定时任务创建后无法读取")
        return _record_from_row(row)

    async def update(
        self,
        task_id: int,
        task: ScheduledTaskInput,
        *,
        next_run_time: datetime | None,
    ) -> ScheduledTaskRecord:
        now = datetime.now()
        async with self._session_factory() as db:
            existing = await self._load_locked_or_plain(db, task_id, for_update=True)
            if existing is None:
                await db.rollback()
                raise TaskNotFoundError("定时任务不存在")
            if existing["lease_until"] and existing["lease_until"] > now:
                await db.rollback()
                raise TaskBusyError("任务正在运行，暂不能修改")
            await db.execute(
                text(
                    """
                    UPDATE scheduled_task
                    SET task_name = :task_name,
                        task_type = :task_type,
                        cron_expr = :cron_expr,
                        config = :config,
                        status = :status,
                        next_run_time = :next_run_time,
                        last_status = NULL,
                        last_result = NULL,
                        lease_token = NULL,
                        lease_until = NULL,
                        lease_owner = NULL,
                        updated_time = :now
                    WHERE id = :id AND deleted = 0
                    """
                ),
                {
                    "id": task_id,
                    "task_name": task.task_name,
                    "task_type": task.task_type,
                    "cron_expr": task.cron_expression,
                    "config": json.dumps(task.config, ensure_ascii=False),
                    "status": 1 if task.enabled else 0,
                    "next_run_time": next_run_time,
                    "now": now,
                },
            )
            row = await self._load_locked_or_plain(db, task_id)
            await db.commit()
        if row is None:
            raise TaskConflictError("定时任务更新后无法读取")
        return _record_from_row(row)

    async def delete(self, task_id: int) -> None:
        now = datetime.now()
        async with self._session_factory() as db:
            existing = await self._load_locked_or_plain(db, task_id, for_update=True)
            if existing is None:
                await db.rollback()
                raise TaskNotFoundError("定时任务不存在")
            if existing["lease_until"] and existing["lease_until"] > now:
                await db.rollback()
                raise TaskBusyError("任务正在运行，暂不能删除")
            await db.execute(
                text(
                    """
                    UPDATE scheduled_task
                    SET deleted = 1, status = 0, next_run_time = NULL,
                        lease_token = NULL, lease_until = NULL, lease_owner = NULL,
                        updated_time = :now
                    WHERE id = :id AND deleted = 0
                    """
                ),
                {"id": task_id, "now": now},
            )
            await db.commit()

    async def claim_manual(
        self,
        task_id: int,
        lease_token: str,
        *,
        now: datetime,
        lease_until: datetime,
        lease_owner: str,
    ) -> ScheduledTaskRecord:
        async with self._session_factory() as db:
            row = await self._load_locked_or_plain(db, task_id, for_update=True)
            if row is None:
                await db.rollback()
                raise TaskNotFoundError("定时任务不存在")
            if row["lease_until"] and row["lease_until"] > now:
                await db.rollback()
                raise TaskBusyError("任务正在运行，请等待本次执行结束")
            await self._write_claim(
                db,
                task_id=task_id,
                lease_token=lease_token,
                lease_until=lease_until,
                lease_owner=lease_owner,
                now=now,
            )
            await db.commit()
        return _record_from_row(
            {
                **row,
                "last_status": "running",
                "last_result": None,
                "last_run_time": now,
                "lease_token": lease_token,
                "lease_until": lease_until,
                "lease_owner": lease_owner,
                "updated_time": now,
            }
        )

    async def claim_due(
        self,
        *,
        limit: int,
        now: datetime,
        lease_until: datetime,
        lease_owner: str,
    ) -> list[ScheduledTaskRecord]:
        claimed: list[ScheduledTaskRecord] = []
        async with self._session_factory() as db:
            result = await db.execute(
                text(
                    f"""
                    SELECT {_TASK_COLUMNS}
                    FROM scheduled_task
                    WHERE deleted = 0
                      AND status = 1
                      AND next_run_time IS NOT NULL
                      AND next_run_time <= :now
                      AND (lease_until IS NULL OR lease_until <= :now)
                    ORDER BY next_run_time ASC, id ASC
                    LIMIT :limit
                    FOR UPDATE SKIP LOCKED
                    """
                ),
                {"now": now, "limit": limit},
            )
            rows = result.mappings().all()
            for row in rows:
                lease_token = uuid.uuid4().hex
                task_id = int(row["id"])
                await self._write_claim(
                    db,
                    task_id=task_id,
                    lease_token=lease_token,
                    lease_until=lease_until,
                    lease_owner=lease_owner,
                    now=now,
                )
                claimed.append(
                    _record_from_row(
                        {
                            **row,
                            "last_status": "running",
                            "last_result": None,
                            "last_run_time": now,
                            "lease_token": lease_token,
                            "lease_until": lease_until,
                            "lease_owner": lease_owner,
                            "updated_time": now,
                        }
                    )
                )
            await db.commit()
        return claimed

    async def finish(
        self,
        task: ScheduledTaskRecord,
        outcome: TaskExecutionOutcome,
        *,
        finished_at: datetime,
        next_run_time: datetime | None,
        disable: bool,
    ) -> ScheduledTaskRecord:
        if not task.lease_token:
            raise TaskConflictError("任务租约缺失，拒绝写入执行结果")
        async with self._session_factory() as db:
            result = await db.execute(
                text(
                    """
                    UPDATE scheduled_task
                    SET last_status = :last_status,
                        last_result = :last_result,
                        next_run_time = :next_run_time,
                        status = CASE WHEN :disable = 1 THEN 0 ELSE status END,
                        lease_token = NULL,
                        lease_until = NULL,
                        lease_owner = NULL,
                        updated_time = :finished_at
                    WHERE id = :id AND deleted = 0 AND lease_token = :lease_token
                    """
                ),
                {
                    "id": task.id,
                    "lease_token": task.lease_token,
                    "last_status": outcome.status,
                    "last_result": _outcome_json(outcome),
                    "next_run_time": None if disable else next_run_time,
                    "disable": 1 if disable else 0,
                    "finished_at": finished_at,
                },
            )
            if int(result.rowcount or 0) != 1:
                await db.rollback()
                raise TaskConflictError("任务租约已失效，执行结果未覆盖其他 worker")
            row = await self._load_locked_or_plain(db, task.id)
            await db.commit()
        if row is None:
            raise TaskConflictError("任务完成后无法读取")
        return _record_from_row(row)

    @staticmethod
    async def _write_claim(
        db: Any,
        *,
        task_id: int,
        lease_token: str,
        lease_until: datetime,
        lease_owner: str,
        now: datetime,
    ) -> None:
        await db.execute(
            text(
                """
                UPDATE scheduled_task
                SET last_status = 'running',
                    last_result = NULL,
                    last_run_time = :now,
                    lease_token = :lease_token,
                    lease_until = :lease_until,
                    lease_owner = :lease_owner,
                    updated_time = :now
                WHERE id = :id AND deleted = 0
                """
            ),
            {
                "id": task_id,
                "lease_token": lease_token,
                "lease_until": lease_until,
                "lease_owner": lease_owner[:128],
                "now": now,
            },
        )

    @staticmethod
    async def _load_locked_or_plain(
        db: Any,
        task_id: int,
        *,
        for_update: bool = False,
    ) -> Any | None:
        suffix = " FOR UPDATE" if for_update else ""
        result = await db.execute(
            text(
                f"SELECT {_TASK_COLUMNS} FROM scheduled_task "
                f"WHERE id = :id AND deleted = 0 LIMIT 1{suffix}"
            ),
            {"id": task_id},
        )
        return result.mappings().first()


async def _load_active_account_cookie(account_id: int) -> str:
    from ..core.cookie_crypto import decrypt_cookie_if_needed
    from ..core.database import async_session

    async with async_session() as db:
        result = await db.execute(
            text(
                """
                SELECT auth.encrypted_cookie
                FROM xianyu_account_auth AS auth
                INNER JOIN xianyu_account AS account ON account.id = auth.account_id
                WHERE account.id = :account_id
                  AND account.status = 1
                  AND account.deleted = 0
                  AND auth.deleted = 0
                  AND auth.cookie_status = 1
                  AND auth.encrypted_cookie IS NOT NULL
                  AND auth.encrypted_cookie <> ''
                ORDER BY auth.updated_time DESC, auth.id DESC
                LIMIT 1
                """
            ),
            {"account_id": account_id},
        )
        row = result.first()
    if not row or not row[0]:
        raise ValueError("关联账号未登录或 Cookie 已失效，请先重新登录")
    cookie = decrypt_cookie_if_needed(row[0])
    if not cookie:
        raise ValueError("关联账号 Cookie 不可用，请先重新登录")
    return cookie


def build_scheduled_task_runtime(*, worker_id: str | None = None) -> ScheduledTaskRuntime:
    from ..core.config import settings
    from ..core.database import async_session
    from .xianyu_goods_sync import sync_goods_for_account
    from .xianyu_order_sync import sync_orders_for_account

    resolved_worker_id = worker_id or (
        f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    )
    store = MySQLScheduledTaskStore(async_session)
    executor = ScheduledTaskExecutor(
        cookie_loader=_load_active_account_cookie,
        goods_sync=sync_goods_for_account,
        orders_sync=sync_orders_for_account,
    )
    return ScheduledTaskRuntime(
        store=store,
        executor=executor,
        timeout_seconds=int(getattr(settings, "scheduler_task_timeout_seconds", 300)),
        lease_seconds=int(getattr(settings, "scheduler_lease_seconds", 360)),
        worker_id=resolved_worker_id,
    )


_default_runtime: ScheduledTaskRuntime | None = None


def get_scheduled_task_runtime() -> ScheduledTaskRuntime:
    """FastAPI dependency and worker factory with one runtime per process."""

    global _default_runtime
    if _default_runtime is None:
        _default_runtime = build_scheduled_task_runtime()
    return _default_runtime


def normalize_cron_expression(value: str) -> str:
    """Return a canonical supported cron expression or raise ``ValueError``.

    Five-field UNIX cron and six-field cron with seconds first are accepted.
    Macros and seven-field/year expressions are deliberately excluded so the
    UI, API and worker all use the same unambiguous contract.
    """

    expression = " ".join(str(value or "").strip().split())
    fields = expression.split(" ") if expression else []
    if len(fields) not in {5, 6}:
        raise ValueError("Cron 表达式必须是 5 段，或以秒开头的 6 段格式")
    second_at_beginning = len(fields) == 6
    try:
        valid = croniter.is_valid(
            expression,
            second_at_beginning=second_at_beginning,
            strict=True,
        )
    except (CroniterBadCronError, KeyError, TypeError, ValueError) as exc:
        raise ValueError("Cron 表达式无效") from exc
    if not valid:
        raise ValueError("Cron 表达式无效")
    return expression


def next_cron_time(expression: str, base_time: datetime | None = None) -> datetime:
    """Calculate the first run strictly after ``base_time``."""

    normalized = normalize_cron_expression(expression)
    base = base_time or datetime.now()
    try:
        return croniter(
            normalized,
            base,
            ret_type=datetime,
            second_at_beginning=len(normalized.split()) == 6,
            max_years_between_matches=5,
        ).get_next(datetime)
    except (CroniterBadCronError, KeyError, TypeError, ValueError) as exc:
        raise ValueError("Cron 表达式无法计算下次执行时间") from exc


class ScheduledTaskInput(CamelModel):
    """Validated create/update input shared by FastAPI and the runtime."""

    task_name: str = Field(min_length=1, max_length=200)
    task_type: str
    cron_expression: str
    config_json: dict[str, Any] | str = Field(default_factory=dict)
    account_id: int = Field(gt=0)
    enabled: bool = True

    model_config = ConfigDict(
        alias_generator=CamelModel.model_config["alias_generator"],
        populate_by_name=True,
        extra="forbid",
    )

    @field_validator("task_name")
    @classmethod
    def normalize_task_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("任务名称不能为空")
        return normalized

    @field_validator("task_type")
    @classmethod
    def validate_task_type(cls, value: str) -> str:
        normalized = str(value or "").strip().lower()
        if normalized not in SUPPORTED_TASK_TYPES:
            raise ValueError("仅支持同步商品和同步订单任务")
        return normalized

    @field_validator("cron_expression")
    @classmethod
    def validate_cron_expression(cls, value: str) -> str:
        return normalize_cron_expression(value)

    @model_validator(mode="after")
    def normalize_config(self) -> "ScheduledTaskInput":
        config: Any = self.config_json
        if isinstance(config, str):
            try:
                config = json.loads(config or "{}")
            except json.JSONDecodeError as exc:
                raise ValueError("configJson 必须是有效 JSON") from exc
        if not isinstance(config, dict):
            raise ValueError("configJson 必须是 JSON 对象")

        config_account_id = config.get("accountId", config.get("account_id"))
        if config_account_id not in (None, ""):
            try:
                normalized_config_account_id = int(config_account_id)
            except (TypeError, ValueError) as exc:
                raise ValueError("configJson.accountId 必须是正整数") from exc
            if normalized_config_account_id <= 0:
                raise ValueError("configJson.accountId 必须是正整数")
            if normalized_config_account_id != self.account_id:
                raise ValueError("accountId 与 configJson.accountId 必须一致")

        normalized_config = dict(config)
        normalized_config.pop("account_id", None)
        normalized_config["accountId"] = self.account_id
        self.config_json = normalized_config
        return self

    @property
    def config(self) -> dict[str, Any]:
        return dict(self.config_json) if isinstance(self.config_json, dict) else {}
