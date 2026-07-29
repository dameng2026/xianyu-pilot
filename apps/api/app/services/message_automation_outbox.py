"""Transactional outbox for inbound-message delivery and AI follow-ups.

The WebSocket receive callback adds two branch rows in the same transaction as
the authoritative chat message.  A continuously running, finite-lease worker
then dispatches each branch independently.  The downstream delivery and AI
attempt state machines remain the authority for external-send outcomes; in
particular, an expired outbox lease may re-enter those idempotent coordinators,
but it can never bypass an ``unknown`` send result and issue a blind retry.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import logging
import re
import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable, Literal

from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.database import async_session
from ..models.entities import MessageAutomationOutbox, XianyuChatMessage


logger = logging.getLogger(__name__)

OutboxBranch = Literal["delivery", "ai"]
DispatchDisposition = Literal["completed", "deferred"]
OutboxExecutor = Callable[["OutboxClaim"], Awaitable[DispatchDisposition]]

_BRANCHES: tuple[OutboxBranch, ...] = ("delivery", "ai")
_LEASE_SECONDS = 300
_POLL_SECONDS = 1.0
_BATCH_SIZE = 20
_SAFE_ERROR_RE = re.compile(r"[^a-z0-9_]+")

_worker_task: asyncio.Task[None] | None = None
_worker_stop: asyncio.Event | None = None
_worker_wakeup: asyncio.Event | None = None
_worker_health_status = "unavailable"


class NonRetryableOutboxError(RuntimeError):
    """The local source is invalid or missing, so retry cannot make progress."""

    def __init__(self, error_code: str) -> None:
        super().__init__(error_code)
        self.error_code = error_code


class UnknownOutboxError(RuntimeError):
    """The downstream external outcome is ambiguous and must not be retried."""

    def __init__(self, error_code: str) -> None:
        super().__init__(error_code)
        self.error_code = error_code


class RetryableOutboxError(RuntimeError):
    """A definite retry-safe downstream/local failure."""

    def __init__(self, error_code: str) -> None:
        super().__init__(error_code)
        self.error_code = error_code


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def _source_digest(account_id: int, source_message_uid: str) -> str:
    raw = f"message-automation-outbox:v1|{int(account_id)}|{source_message_uid}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _safe_error_code(exc: BaseException) -> str:
    explicit = str(getattr(exc, "error_code", "") or "").strip().lower()
    if explicit:
        normalized = _SAFE_ERROR_RE.sub("_", explicit).strip("_")
        if normalized:
            return normalized[:64]
    normalized = _SAFE_ERROR_RE.sub(
        "_", type(exc).__name__.strip().lower()
    ).strip("_")
    return (normalized or "dispatch_failed")[:64]


@dataclass(frozen=True)
class OutboxClaim:
    id: int
    account_id: int
    chat_message_id: int
    branch: OutboxBranch
    lease_token: str
    attempt_count: int


async def enqueue_message_automation(
    db: AsyncSession,
    *,
    account_id: int,
    chat_message_id: int,
    source_message_uid: str,
) -> None:
    """Stage both branches on ``db`` without committing the caller's transaction."""

    if not account_id or not chat_message_id or not source_message_uid:
        raise ValueError("message automation outbox requires stable message identity")
    digest = _source_digest(account_id, source_message_uid)
    for branch in _BRANCHES:
        db.add(
            MessageAutomationOutbox(
                account_id=int(account_id),
                chat_message_id=int(chat_message_id),
                source_message_digest=digest,
                branch=branch,
                state="pending",
                retry_safe=1,
                attempt_count=0,
            )
        )
    # A flush here is intentional: if the queue row cannot be persisted, the
    # caller rolls back the chat INSERT as well instead of committing orphaned
    # inbound data that automation can never recover.
    await db.flush()


async def _claim_batch(limit: int) -> list[OutboxClaim]:
    now = _now()
    lease_until = now + dt.timedelta(seconds=_LEASE_SECONDS)
    claims: list[OutboxClaim] = []
    async with async_session() as db:
        async with db.begin():
            rows = (
                await db.execute(
                    select(MessageAutomationOutbox)
                    .where(
                        or_(
                            and_(
                                MessageAutomationOutbox.state.in_(("pending", "failed")),
                                MessageAutomationOutbox.retry_safe == 1,
                                or_(
                                    MessageAutomationOutbox.next_attempt_at.is_(None),
                                    MessageAutomationOutbox.next_attempt_at <= now,
                                ),
                            ),
                            and_(
                                MessageAutomationOutbox.state == "processing",
                                MessageAutomationOutbox.lease_until.is_not(None),
                                MessageAutomationOutbox.lease_until <= now,
                            ),
                        )
                    )
                    .order_by(MessageAutomationOutbox.id.asc())
                    .limit(max(1, min(int(limit), 100)))
                    .with_for_update(skip_locked=True)
                )
            ).scalars().all()
            for row in rows:
                token = uuid.uuid4().hex
                row.state = "processing"
                row.retry_safe = 1
                row.attempt_count = int(row.attempt_count or 0) + 1
                row.lease_token = token
                row.lease_until = lease_until
                row.next_attempt_at = None
                row.last_error_code = None
                row.updated_time = now
                claims.append(
                    OutboxClaim(
                        id=int(row.id),
                        account_id=int(row.account_id),
                        chat_message_id=int(row.chat_message_id),
                        branch=str(row.branch),  # type: ignore[arg-type]
                        lease_token=token,
                        attempt_count=int(row.attempt_count),
                    )
                )
    return claims


async def load_claim_message(claim: OutboxClaim) -> dict | None:
    """Rehydrate one claimed message without copying it into outbox storage."""

    async with async_session() as db:
        row = (
            await db.execute(
                select(XianyuChatMessage).where(
                    XianyuChatMessage.id == claim.chat_message_id,
                    XianyuChatMessage.account_id == claim.account_id,
                    XianyuChatMessage.deleted == 0,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return None

        complete = row.complete_msg
        if isinstance(complete, str):
            try:
                complete = json.loads(complete)
            except (json.JSONDecodeError, TypeError):
                complete = {}
        msg = dict(complete) if isinstance(complete, dict) else {}
        fallbacks = {
            "pnmId": row.pnm_id,
            "sId": row.s_id,
            "contentType": row.content_type,
            "msgContent": row.msg_content,
            "senderUserId": row.sender_user_id,
            "receiverUserId": row.receiver_user_id,
            "senderUserName": row.sender_user_name,
            "xyGoodsId": row.xy_goods_id,
            "messageTime": row.message_time,
            "direction": row.direction,
            "parseStatus": row.parse_status,
            "reminderContent": row.reminder_content,
            "reminderUrl": row.reminder_url,
        }
        for key, value in fallbacks.items():
            if msg.get(key) is None:
                msg[key] = value
        msg["_persistedMessageId"] = int(row.id)
        msg["_sourceMessageUid"] = str(row.message_uid or row.pnm_id or "")
        msg["_accountId"] = claim.account_id
        msg["_automationOutboxId"] = claim.id
        msg["_automationOutboxLeaseToken"] = claim.lease_token
        msg["_automationOutboxAttemptCount"] = claim.attempt_count
        return msg


async def _finish_claim(
    claim: OutboxClaim,
    *,
    state: Literal["completed", "failed", "unknown"],
    retry_safe: bool,
    error_code: str | None = None,
) -> bool:
    now = _now()
    next_attempt_at = None
    if state == "failed" and retry_safe:
        # Bounded exponential backoff; retry remains durable indefinitely.
        delay = min(2 ** min(max(claim.attempt_count - 1, 0), 8), 300)
        next_attempt_at = now + dt.timedelta(seconds=delay)
    async with async_session() as db:
        result = await db.execute(
            update(MessageAutomationOutbox)
            .where(
                MessageAutomationOutbox.id == claim.id,
                MessageAutomationOutbox.state == "processing",
                MessageAutomationOutbox.lease_token == claim.lease_token,
            )
            .values(
                state=state,
                retry_safe=1 if retry_safe else 0,
                lease_token=None,
                lease_until=None,
                next_attempt_at=next_attempt_at,
                completed_at=now if state == "completed" else None,
                last_error_code=(error_code or None),
                updated_time=now,
            )
        )
        await db.commit()
        return bool(result.rowcount)


async def complete_deferred_messages(messages: list[dict]) -> int:
    """Complete AI rows only after their quiet-period batch has really run."""

    completed = 0
    for msg in messages:
        claim = _claim_from_message(msg, branch="ai")
        if claim is not None and await _finish_claim(
            claim,
            state="completed",
            retry_safe=False,
        ):
            completed += 1
    return completed


async def fail_deferred_messages(messages: list[dict], exc: BaseException) -> int:
    """Return an AI batch to retry-safe failed state without storing its body."""

    failed = 0
    error_code = _safe_error_code(exc)
    for msg in messages:
        claim = _claim_from_message(msg, branch="ai")
        if claim is not None and await _finish_claim(
            claim,
            state="failed",
            retry_safe=True,
            error_code=error_code,
        ):
            failed += 1
    _wake_worker()
    return failed


async def settle_deferred_messages(
    messages: list[dict],
    *,
    state: Literal["failed", "unknown"],
    retry_safe: bool,
    error_code: str,
) -> int:
    """Mirror a terminal/incomplete downstream state onto leased AI rows."""

    settled = 0
    for msg in messages:
        claim = _claim_from_message(msg, branch="ai")
        if claim is not None and await _finish_claim(
            claim,
            state=state,
            retry_safe=retry_safe,
            error_code=error_code[:64],
        ):
            settled += 1
    if retry_safe:
        _wake_worker()
    return settled


def _claim_from_message(msg: dict, *, branch: OutboxBranch) -> OutboxClaim | None:
    try:
        outbox_id = int(msg.get("_automationOutboxId") or 0)
        account_id = int(msg.get("accountId") or msg.get("_accountId") or 0)
        chat_message_id = int(msg.get("_persistedMessageId") or 0)
        lease_token = str(msg.get("_automationOutboxLeaseToken") or "")
    except (TypeError, ValueError):
        return None
    if not outbox_id or not account_id or not chat_message_id or not lease_token:
        return None
    return OutboxClaim(
        id=outbox_id,
        account_id=account_id,
        chat_message_id=chat_message_id,
        branch=branch,
        lease_token=lease_token,
        attempt_count=int(msg.get("_automationOutboxAttemptCount") or 1),
    )


async def _default_executor(claim: OutboxClaim) -> DispatchDisposition:
    # Lazy import avoids a module cycle: ws_startup owns business dispatch,
    # while this module owns persistence and leasing.
    from .ws_startup import execute_message_automation_claim

    return await execute_message_automation_claim(claim)


async def run_message_automation_outbox_once(
    *,
    limit: int = _BATCH_SIZE,
    executor: OutboxExecutor | None = None,
) -> dict[str, int]:
    """Claim and execute one bounded batch; safe for concurrent replicas."""

    execute = executor or _default_executor
    claims = await _claim_batch(limit)
    result = {
        "claimed": len(claims),
        "completed": 0,
        "deferred": 0,
        "failed": 0,
        "unknown": 0,
    }
    for claim in claims:
        try:
            disposition = await execute(claim)
            if disposition == "deferred":
                result["deferred"] += 1
                continue
            if await _finish_claim(claim, state="completed", retry_safe=False):
                result["completed"] += 1
        except UnknownOutboxError as exc:
            logger.error(
                "Message automation branch outcome unknown outboxId=%d branch=%s errorCode=%s",
                claim.id,
                claim.branch,
                _safe_error_code(exc),
            )
            if await _finish_claim(
                claim,
                state="unknown",
                retry_safe=False,
                error_code=_safe_error_code(exc),
            ):
                result["unknown"] += 1
        except NonRetryableOutboxError as exc:
            logger.error(
                "Message automation branch rejected outboxId=%d branch=%s errorType=%s",
                claim.id,
                claim.branch,
                type(exc).__name__,
            )
            if await _finish_claim(
                claim,
                state="failed",
                retry_safe=False,
                error_code=_safe_error_code(exc),
            ):
                result["failed"] += 1
        except Exception as exc:  # noqa: BLE001
            # Downstream state machines persist their external-send boundary
            # before I/O. Re-entry is therefore safe: unknown sends return an
            # existing unknown outcome and are never transmitted again.
            logger.error(
                "Message automation branch failed outboxId=%d branch=%s errorType=%s",
                claim.id,
                claim.branch,
                type(exc).__name__,
            )
            if await _finish_claim(
                claim,
                state="failed",
                retry_safe=True,
                error_code=_safe_error_code(exc),
            ):
                result["failed"] += 1
    return result


async def _worker_loop(stop: asyncio.Event, wakeup: asyncio.Event) -> None:
    global _worker_health_status
    logger.info("Message automation outbox worker started")
    try:
        while not stop.is_set():
            try:
                await run_message_automation_outbox_once()
                _worker_health_status = "ok"
            except Exception:  # noqa: BLE001
                _worker_health_status = "error"
                logger.error("Message automation outbox polling failed", exc_info=True)
            wakeup.clear()
            try:
                await asyncio.wait_for(wakeup.wait(), timeout=_POLL_SECONDS)
            except asyncio.TimeoutError:
                continue
    finally:
        _worker_health_status = "unavailable"
        logger.info("Message automation outbox worker stopped")


async def start_message_automation_outbox_worker() -> None:
    """Start one local poller; database leases coordinate API replicas."""

    global _worker_task, _worker_stop, _worker_wakeup, _worker_health_status
    if _worker_task is not None and not _worker_task.done():
        return
    _worker_health_status = "starting"
    _worker_stop = asyncio.Event()
    _worker_wakeup = asyncio.Event()
    _worker_task = asyncio.create_task(
        _worker_loop(_worker_stop, _worker_wakeup),
        name="message-automation-outbox",
    )
    _worker_wakeup.set()


async def stop_message_automation_outbox_worker() -> None:
    global _worker_task, _worker_stop, _worker_wakeup, _worker_health_status
    task = _worker_task
    if task is None:
        return
    if _worker_stop is not None:
        _worker_stop.set()
    if _worker_wakeup is not None:
        _worker_wakeup.set()
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=10)
    except asyncio.TimeoutError:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    finally:
        _worker_task = None
        _worker_stop = None
        _worker_wakeup = None
        _worker_health_status = "unavailable"


def get_worker_health_status() -> str:
    """Return a non-sensitive status derived from the actual poller task."""

    task = _worker_task
    stop = _worker_stop
    if task is None or task.done() or stop is None or stop.is_set():
        return "unavailable"
    if _worker_health_status in {"starting", "ok", "error"}:
        return _worker_health_status
    return "unavailable"


def notify_message_automation_worker() -> None:
    """Best-effort latency hint after commit; durability never depends on it."""

    _wake_worker()


def _wake_worker() -> None:
    if _worker_wakeup is not None:
        _worker_wakeup.set()
