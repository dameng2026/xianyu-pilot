from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import logging
import re
import uuid
import dataclasses
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from sqlalchemy import or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.logging_security import redact_sensitive_text
from ..models.entities import DeliveryRecord, ManualDeliveryAttempt, XianyuTradeOrder


logger = logging.getLogger(__name__)
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")
_SAFE_ERROR_CODE_RE = re.compile(r"[^a-z0-9_]+")
_ACTIVE_STATES = {"pending", "message_sent"}


AttemptState = Literal["pending", "message_sent", "success", "failed", "unknown"]
AttemptAction = Literal["send_message", "confirm_platform", "return", "in_progress"]


@dataclass(frozen=True)
class ManualDeliveryCommand:
    delivery_mode: str
    delivery_content: str
    quantity_requested: int
    idempotency_key: str | None = None
    source_id: int | None = None
    delivery_timing: str | None = None


@dataclass(frozen=True)
class DeliveryContext:
    order_id: int
    external_order_id: str
    account_id: int
    buyer_id: str
    item_id: str
    session_id: str
    peer_id: str


@dataclass(frozen=True)
class ExternalStepResult:
    status: Literal["confirmed", "failed", "unknown"]
    error_code: str | None = None
    message: str = ""
    retry_safe: bool = False

    @classmethod
    def confirmed(cls) -> "ExternalStepResult":
        return cls(status="confirmed")

    @classmethod
    def failed(
        cls,
        error_code: str,
        message: str,
        *,
        retry_safe: bool,
    ) -> "ExternalStepResult":
        return cls(
            status="failed",
            error_code=error_code,
            message=message,
            retry_safe=retry_safe,
        )

    @classmethod
    def unknown(cls, error_code: str, message: str) -> "ExternalStepResult":
        return cls(
            status="unknown",
            error_code=error_code,
            message=message,
            retry_safe=False,
        )


@dataclass(frozen=True)
class AttemptLease:
    attempt_id: int
    idempotency_key: str
    state: AttemptState
    action: AttemptAction
    lease_token: str | None
    context: DeliveryContext
    retry_safe: bool
    retry_scope: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    repeated: bool = False
    message_confirmed: bool = False
    platform_confirmed: bool = False


@dataclass(frozen=True)
class ManualDeliveryOutcome:
    status: AttemptState | Literal["in_progress"]
    message: str
    attempt_id: int
    order_id: int
    idempotency_key: str
    retry_safe: bool
    retry_scope: str | None
    error_code: str | None
    message_confirmed: bool
    platform_confirmed: bool
    repeated: bool = False

    def to_data(self) -> dict[str, object]:
        return {
            "status": self.status,
            "message": self.message,
            "attemptId": self.attempt_id,
            "orderId": self.order_id,
            "idempotencyKey": self.idempotency_key,
            "retrySafe": self.retry_safe,
            "retryScope": self.retry_scope,
            "errorCode": self.error_code,
            "messageConfirmed": self.message_confirmed,
            "platformConfirmed": self.platform_confirmed,
            "repeated": self.repeated,
        }


class ManualDeliveryError(Exception):
    def __init__(
        self,
        http_status: int,
        error_code: str,
        message: str,
        *,
        data: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.http_status = http_status
        self.error_code = error_code
        self.public_message = message
        self.data = data or {}


class AttemptStore(Protocol):
    async def resolve_source(self, source_id: int) -> dict[str, Any] | None: ...

    async def acquire(
        self,
        order_id: int,
        command: ManualDeliveryCommand,
        idempotency_key: str,
        content_digest: str,
    ) -> AttemptLease: ...

    async def mark_message_started(self, lease: AttemptLease) -> None: ...

    async def mark_message_sent(self, lease: AttemptLease) -> AttemptLease: ...

    async def mark_failed(
        self,
        lease: AttemptLease,
        result: ExternalStepResult,
        *,
        retry_scope: str,
    ) -> AttemptLease: ...

    async def mark_unknown(
        self,
        lease: AttemptLease,
        result: ExternalStepResult,
        *,
        retry_scope: str,
    ) -> AttemptLease: ...

    async def mark_platform_failed(
        self,
        lease: AttemptLease,
        result: ExternalStepResult,
    ) -> AttemptLease: ...

    async def mark_success(
        self,
        lease: AttemptLease,
        command: ManualDeliveryCommand,
    ) -> AttemptLease: ...


class DeliveryGateway(Protocol):
    async def send_message(
        self,
        context: DeliveryContext,
        content: str,
    ) -> ExternalStepResult: ...

    async def confirm_shipment(self, context: DeliveryContext) -> ExternalStepResult: ...


class ManualDeliveryCoordinator:
    """Execute the two irreversible delivery steps behind one small interface."""

    def __init__(self, *, store: AttemptStore, gateway: DeliveryGateway) -> None:
        self._store = store
        self._gateway = gateway

    async def execute(
        self,
        order_id: int,
        command: ManualDeliveryCommand,
    ) -> ManualDeliveryOutcome:
        if command.source_id:
            command = await self._resolve_source_command(command)
        content_digest = hashlib.sha256(command.delivery_content.encode("utf-8")).hexdigest()
        idempotency_key = command.idempotency_key or hashlib.sha256(
            f"manual-delivery:v1:{order_id}:{command.delivery_mode}:{command.quantity_requested}:{content_digest}".encode()
        ).hexdigest()
        lease = await self._store.acquire(order_id, command, idempotency_key, content_digest)

        if lease.action == "in_progress":
            return self._outcome(lease, status="in_progress")

        if lease.action == "send_message":
            await self._store.mark_message_started(lease)
            try:
                result = await self._gateway.send_message(lease.context, command.delivery_content)
            except Exception:  # An exception after network write is necessarily unknown.
                logger.error(
                    "Manual delivery message call ended unexpectedly attemptId=%d",
                    lease.attempt_id,
                    exc_info=True,
                )
                result = ExternalStepResult.unknown(
                    "message_result_unknown",
                    "发送结果未确认，请先在闲鱼 App 核对，避免重复发送",
                )
            if result.status == "unknown":
                lease = await self._store.mark_unknown(
                    lease,
                    result,
                    retry_scope="message",
                )
                return self._outcome(lease)
            if result.status != "confirmed":
                lease = await self._store.mark_failed(
                    lease,
                    result,
                    retry_scope="message",
                )
                return self._outcome(lease)
            lease = await self._store.mark_message_sent(lease)

        if lease.action == "confirm_platform":
            try:
                result = await self._gateway.confirm_shipment(lease.context)
            except Exception:
                logger.error(
                    "Manual delivery platform confirmation ended unexpectedly attemptId=%d",
                    lease.attempt_id,
                    exc_info=True,
                )
                result = ExternalStepResult.unknown(
                    "platform_result_unknown",
                    "平台确认结果未知，请先同步订单并在闲鱼 App 核对",
                )
            if result.status == "unknown":
                lease = await self._store.mark_unknown(
                    lease,
                    result,
                    retry_scope="platform_confirm",
                )
                return self._outcome(lease)
            if result.status != "confirmed":
                lease = await self._store.mark_platform_failed(lease, result)
                return self._outcome(lease)
            lease = await self._store.mark_success(lease, command)

        return self._outcome(lease)

    async def _resolve_source_command(
        self,
        command: ManualDeliveryCommand,
    ) -> ManualDeliveryCommand:
        """货源库发货：从 delivery_text_source 表读取内容，返回解析后的 command。"""
        source = await self._store.resolve_source(command.source_id)
        if not source:
            raise ManualDeliveryError(
                422,
                "source_not_found",
                "所选货源不存在或已被删除",
                data={"retrySafe": False},
            )
        content = str(source.get("content") or "").strip()
        if not content:
            raise ManualDeliveryError(
                422,
                "source_empty",
                "货源内容为空，请先在货源库中补充内容",
                data={"retrySafe": False},
            )
        mode = "card" if str(source.get("delivery_mode") or "").lower() == "card" else "text"
        return dataclasses.replace(
            command,
            delivery_content=content,
            delivery_mode=mode,
        )

    @staticmethod
    def _outcome(
        lease: AttemptLease,
        *,
        status: AttemptState | Literal["in_progress"] | None = None,
    ) -> ManualDeliveryOutcome:
        resolved_status = status or lease.state
        messages = {
            "pending": "手动发货正在执行，请勿重复操作",
            "in_progress": "同一订单的手动发货正在执行，请勿重复操作",
            "message_sent": "买家消息已确认发送，但平台确认发货尚未完成；重试只会执行平台确认",
            "success": "买家消息与平台确认发货均已完成",
            "failed": lease.error_message or "手动发货失败，可在排除问题后安全重试",
            "unknown": lease.error_message or "发送结果无法确认，请先在闲鱼 App 核对，避免重复发送",
        }
        return ManualDeliveryOutcome(
            status=resolved_status,
            message=messages[resolved_status],
            attempt_id=lease.attempt_id,
            order_id=lease.context.order_id,
            idempotency_key=lease.idempotency_key,
            retry_safe=False if resolved_status == "in_progress" else lease.retry_safe,
            retry_scope=lease.retry_scope,
            error_code=lease.error_code,
            message_confirmed=lease.message_confirmed,
            platform_confirmed=lease.platform_confirmed,
            repeated=lease.repeated,
        )


def _now() -> dt.datetime:
    return dt.datetime.now()


def _normalize_identifier(value: object) -> str:
    normalized = str(value or "").strip()
    if normalized.startswith("sid:"):
        normalized = normalized[4:]
    if normalized.endswith("@goofish"):
        normalized = normalized[:-8]
    if not _SAFE_ID_RE.fullmatch(normalized):
        return ""
    return normalized


def _raw_platform_identifier(value: object) -> str:
    normalized = str(value or "").strip()
    return normalized if _SAFE_ID_RE.fullmatch(normalized) else ""


def _safe_error_code(value: object, default: str) -> str:
    normalized = _SAFE_ERROR_CODE_RE.sub("_", str(value or "").strip().lower()).strip("_")
    return (normalized or default)[:64]


def _safe_error_message(value: object, default: str) -> str:
    normalized = redact_sensitive_text(str(value or "").strip())
    return (normalized or default)[:500]


class SqlManualDeliveryAttemptStore:
    """MySQL adapter implementing durable claims and atomic local success."""

    def __init__(self, db: AsyncSession, *, lease_seconds: int = 90) -> None:
        self._db = db
        self._lease_seconds = max(30, min(int(lease_seconds), 300))

    async def resolve_source(self, source_id: int) -> dict[str, Any] | None:
        """从 delivery_text_source 表读取货源内容（id/title/content/delivery_mode）。"""
        row = (
            await self._db.execute(
                text(
                    """
                    SELECT id, title, content, delivery_mode
                    FROM delivery_text_source
                    WHERE id = :sid AND deleted = 0
                    LIMIT 1
                    """
                ),
                {"sid": int(source_id)},
            )
        ).mappings().first()
        return dict(row) if row else None

    async def acquire(
        self,
        order_id: int,
        command: ManualDeliveryCommand,
        idempotency_key: str,
        content_digest: str,
    ) -> AttemptLease:
        for race_attempt in range(2):
            try:
                return await self._acquire_once(
                    order_id,
                    command,
                    idempotency_key,
                    content_digest,
                )
            except IntegrityError:
                await self._db.rollback()
                if race_attempt:
                    raise
        raise RuntimeError("unreachable")

    async def _acquire_once(
        self,
        order_id: int,
        command: ManualDeliveryCommand,
        idempotency_key: str,
        content_digest: str,
    ) -> AttemptLease:
        order = (
            await self._db.execute(
                select(XianyuTradeOrder)
                .where(XianyuTradeOrder.id == order_id, XianyuTradeOrder.deleted == 0)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if order is None:
            await self._db.rollback()
            raise ManualDeliveryError(404, "order_not_found", "订单不存在")

        existing = (
            await self._db.execute(
                select(ManualDeliveryAttempt)
                .where(
                    or_(
                        ManualDeliveryAttempt.order_id == order_id,
                        ManualDeliveryAttempt.idempotency_key == idempotency_key,
                    )
                )
                .with_for_update()
            )
        ).scalars().first()
        if existing is not None:
            if int(existing.order_id) != int(order_id):
                await self._db.rollback()
                raise ManualDeliveryError(
                    409,
                    "idempotency_key_conflict",
                    "幂等键已用于其他订单，请刷新页面后重试",
                )
            self._validate_existing_command(existing, command, content_digest)
            return await self._claim_existing(existing)

        if int(order.order_status or 0) >= 3:
            await self._db.rollback()
            raise ManualDeliveryError(
                409,
                "order_already_shipped",
                "订单已标记为已发货；请先同步订单并核对闲鱼平台状态，系统不会重复发送发货内容",
            )

        context = await self._resolve_context(order)
        delivery_record = DeliveryRecord(
            account_id=context.account_id,
            order_id=context.order_id,
            delivery_type="manual",
            delivery_mode=command.delivery_mode,
            content=command.delivery_content,
            delivery_content=command.delivery_content,
            delivery_timing=command.delivery_timing or "manual_immediate",
            status=0,
            delivery_status="pending",
            retry_count=0,
            quantity_requested=command.quantity_requested,
            quantity_sent=0,
            result="pending",
            deleted=0,
        )
        self._db.add(delivery_record)
        await self._db.flush()

        now = _now()
        lease_token = uuid.uuid4().hex
        attempt = ManualDeliveryAttempt(
            order_id=context.order_id,
            account_id=context.account_id,
            external_order_id=context.external_order_id,
            idempotency_key=idempotency_key,
            content_digest=content_digest,
            delivery_record_id=delivery_record.id,
            delivery_mode=command.delivery_mode,
            quantity_requested=command.quantity_requested,
            session_id=context.session_id,
            peer_id=context.peer_id,
            item_id=context.item_id,
            state="pending",
            retry_scope="message",
            retry_safe=1,
            attempt_count=1,
            lease_token=lease_token,
            lease_until=now + dt.timedelta(seconds=self._lease_seconds),
        )
        self._db.add(attempt)
        await self._db.flush()
        await self._db.commit()
        return self._lease(attempt, action="send_message")

    def _validate_existing_command(
        self,
        attempt: ManualDeliveryAttempt,
        command: ManualDeliveryCommand,
        content_digest: str,
    ) -> None:
        if (
            attempt.content_digest != content_digest
            or attempt.delivery_mode != command.delivery_mode
            or int(attempt.quantity_requested or 1) != int(command.quantity_requested)
        ):
            raise ManualDeliveryError(
                409,
                "delivery_payload_conflict",
                "该订单已有不同内容的发货尝试；为避免重复发送，请刷新订单并核对既有发货状态",
                data={"status": attempt.state, "retrySafe": False},
            )

    async def _claim_existing(self, attempt: ManualDeliveryAttempt) -> AttemptLease:
        now = _now()
        if attempt.state == "success":
            await self._db.commit()
            return self._lease(attempt, action="return", repeated=True)
        if attempt.state == "unknown" or (
            attempt.state == "failed" and not bool(attempt.retry_safe)
        ):
            await self._db.commit()
            return self._lease(attempt, action="return", repeated=True)
        if attempt.state in _ACTIVE_STATES and attempt.lease_until and attempt.lease_until > now:
            await self._db.commit()
            return self._lease(attempt, action="in_progress", repeated=True)

        # A process may have died after writing the frame but before persisting
        # its ACK. Retrying that message could duplicate credentials/content.
        if (
            attempt.state == "pending"
            and attempt.message_started_at is not None
            and attempt.message_confirmed_at is None
        ):
            attempt.state = "unknown"
            attempt.retry_safe = 0
            attempt.retry_scope = "message"
            attempt.last_error_code = "message_result_unknown_after_recovery"
            attempt.error_message = "上次发送在确认前中断，请先在闲鱼 App 核对，系统已禁止自动重发"
            attempt.lease_token = None
            attempt.lease_until = None
            await self._update_delivery_record(attempt, "unknown", attempt.error_message)
            await self._db.commit()
            return self._lease(attempt, action="return", repeated=True)

        action: AttemptAction = (
            "confirm_platform"
            if attempt.message_confirmed_at is not None or attempt.state == "message_sent"
            else "send_message"
        )
        attempt.state = "message_sent" if action == "confirm_platform" else "pending"
        attempt.retry_scope = "platform_confirm" if action == "confirm_platform" else "message"
        attempt.retry_safe = 1
        attempt.attempt_count = int(attempt.attempt_count or 0) + 1
        attempt.lease_token = uuid.uuid4().hex
        attempt.lease_until = now + dt.timedelta(seconds=self._lease_seconds)
        attempt.last_error_code = None
        attempt.error_message = None
        await self._update_delivery_record(
            attempt,
            "partial" if action == "confirm_platform" else "pending",
            None,
        )
        await self._db.commit()
        return self._lease(attempt, action=action, repeated=True)

    async def mark_message_started(self, lease: AttemptLease) -> None:
        attempt = await self._locked_attempt(lease)
        if attempt.state != "pending":
            raise ManualDeliveryError(409, "attempt_state_conflict", "发货状态已变化，请刷新订单")
        attempt.message_started_at = attempt.message_started_at or _now()
        await self._db.commit()

    async def mark_message_sent(self, lease: AttemptLease) -> AttemptLease:
        attempt = await self._locked_attempt(lease)
        now = _now()
        attempt.state = "message_sent"
        attempt.retry_scope = "platform_confirm"
        attempt.retry_safe = 1
        attempt.message_confirmed_at = attempt.message_confirmed_at or now
        attempt.last_error_code = None
        attempt.error_message = None
        await self._update_delivery_record(attempt, "partial", None)
        await self._db.commit()
        return self._lease(attempt, action="confirm_platform")

    async def mark_failed(
        self,
        lease: AttemptLease,
        result: ExternalStepResult,
        *,
        retry_scope: str,
    ) -> AttemptLease:
        attempt = await self._locked_attempt(lease)
        attempt.state = "failed"
        attempt.retry_scope = retry_scope
        attempt.retry_safe = 1 if result.retry_safe else 0
        attempt.last_error_code = _safe_error_code(result.error_code, "delivery_failed")
        attempt.error_message = _safe_error_message(result.message, "手动发货失败")
        self._release(attempt)
        await self._update_delivery_record(attempt, "failed", attempt.error_message)
        await self._db.commit()
        return self._lease(attempt, action="return")

    async def mark_unknown(
        self,
        lease: AttemptLease,
        result: ExternalStepResult,
        *,
        retry_scope: str,
    ) -> AttemptLease:
        attempt = await self._locked_attempt(lease)
        attempt.state = "unknown"
        attempt.retry_scope = retry_scope
        attempt.retry_safe = 0
        attempt.last_error_code = _safe_error_code(result.error_code, "delivery_result_unknown")
        attempt.error_message = _safe_error_message(
            result.message,
            "执行结果未知，请先在闲鱼 App 核对，系统不会自动重试",
        )
        self._release(attempt)
        await self._update_delivery_record(attempt, "unknown", attempt.error_message)
        await self._db.commit()
        return self._lease(attempt, action="return")

    async def mark_platform_failed(
        self,
        lease: AttemptLease,
        result: ExternalStepResult,
    ) -> AttemptLease:
        attempt = await self._locked_attempt(lease)
        attempt.state = "message_sent"
        attempt.retry_scope = "platform_confirm"
        attempt.retry_safe = 1 if result.retry_safe else 0
        attempt.last_error_code = _safe_error_code(result.error_code, "platform_confirmation_failed")
        attempt.error_message = _safe_error_message(
            result.message,
            "买家消息已发送，但平台确认发货失败；重试只会执行平台确认",
        )
        self._release(attempt)
        await self._update_delivery_record(attempt, "partial", attempt.error_message)
        await self._db.commit()
        return self._lease(attempt, action="return")

    async def mark_success(
        self,
        lease: AttemptLease,
        command: ManualDeliveryCommand,
    ) -> AttemptLease:
        # Keep the same lock order as acquire(): order first, then attempt.
        # This prevents a duplicate request and the success transition from
        # deadlocking while each waits for the other's row lock.
        order = (
            await self._db.execute(
                select(XianyuTradeOrder)
                .where(
                    XianyuTradeOrder.id == lease.context.order_id,
                    XianyuTradeOrder.deleted == 0,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if order is None:
            await self._db.rollback()
            raise ManualDeliveryError(409, "order_missing_during_delivery", "订单已不存在，无法确认本地发货状态")
        attempt = await self._locked_attempt(lease)
        if attempt.message_confirmed_at is None:
            raise ManualDeliveryError(
                409,
                "message_not_confirmed",
                "买家消息尚未确认发送，订单不会标记为已发货",
            )

        now = _now()
        attempt.state = "success"
        attempt.retry_scope = None
        attempt.retry_safe = 0
        attempt.platform_confirmed_at = attempt.platform_confirmed_at or now
        attempt.last_error_code = None
        attempt.error_message = None
        self._release(attempt)

        # This is the only transition allowed to mutate local shipment state.
        order.order_status = max(int(order.order_status or 0), 3)
        order.ship_time = order.ship_time or now
        order.buyer_message = command.delivery_content
        order.updated_time = now
        await self._update_delivery_record(attempt, "success", None)
        await self._db.commit()
        return self._lease(attempt, action="return")

    async def _locked_attempt(self, lease: AttemptLease) -> ManualDeliveryAttempt:
        attempt = (
            await self._db.execute(
                select(ManualDeliveryAttempt)
                .where(ManualDeliveryAttempt.id == lease.attempt_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if attempt is None:
            raise ManualDeliveryError(404, "attempt_not_found", "发货尝试不存在，请刷新订单")
        if not lease.lease_token or attempt.lease_token != lease.lease_token:
            await self._db.rollback()
            raise ManualDeliveryError(409, "attempt_lease_lost", "发货执行权已变化，请刷新订单状态")
        return attempt

    async def _update_delivery_record(
        self,
        attempt: ManualDeliveryAttempt,
        status: str,
        error_message: str | None,
    ) -> None:
        if not attempt.delivery_record_id:
            return
        record = (
            await self._db.execute(
                select(DeliveryRecord)
                .where(DeliveryRecord.id == attempt.delivery_record_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if record is None:
            return
        record.delivery_status = status
        record.status = {
            "pending": 0,
            "partial": 1,
            "success": 2,
            "failed": 3,
            "unknown": 5,
        }.get(status, 0)
        record.error_message = error_message
        record.fail_reason = error_message
        record.retry_count = max(int(attempt.attempt_count or 1) - 1, 0)
        record.quantity_requested = int(attempt.quantity_requested or 1)
        record.quantity_sent = (
            int(attempt.quantity_requested or 1)
            if attempt.message_confirmed_at is not None
            else 0
        )
        record.delivery_time = attempt.message_confirmed_at
        record.completed_time = (
            _now() if status in {"success", "failed", "unknown"} else None
        )
        record.platform_sync_time = attempt.platform_confirmed_at
        record.result = status

    async def _resolve_context(self, order: XianyuTradeOrder) -> DeliveryContext:
        missing = []
        if not order.account_id:
            missing.append("accountId")
        external_order_id = _raw_platform_identifier(order.external_order_id)
        if not external_order_id:
            missing.append("externalOrderId")
        if not _normalize_identifier(order.buyer_id):
            missing.append("buyerId")
        if not _normalize_identifier(order.item_id):
            missing.append("itemId")
        if missing:
            await self._db.rollback()
            raise ManualDeliveryError(
                422,
                "order_context_incomplete",
                f"订单缺少 {', '.join(missing)}；请先同步订单并建立买家会话后再发货",
                data={"recovery": "sync_order_and_open_conversation", "retrySafe": False},
            )

        account_id = int(order.account_id)
        buyer_id = _normalize_identifier(order.buyer_id)
        item_id = _normalize_identifier(order.item_id)
        message_rows = (
            await self._db.execute(
                text(
                    """
                    SELECT s_id, sender_user_id, receiver_user_id, peer_external_uid, xy_goods_id
                    FROM xianyu_chat_message
                    WHERE account_id = :account_id
                      AND deleted = 0
                      AND s_id IS NOT NULL AND s_id != ''
                      AND REPLACE(REPLACE(TRIM(COALESCE(xy_goods_id, '')), 'sid:', ''), '@goofish', '') = :item_id
                    ORDER BY message_time DESC, id DESC
                    LIMIT 100
                    """
                ),
                {"account_id": account_id, "item_id": item_id},
            )
        ).mappings().all()
        resolved = self._context_from_messages(message_rows, buyer_id)

        if resolved is None:
            conversation_rows = (
                await self._db.execute(
                    text(
                        """
                        SELECT external_buyer_id, peer_external_uid, peer_key, goods_id
                        FROM xianyu_conversation
                        WHERE account_id = :account_id
                          AND REPLACE(REPLACE(TRIM(COALESCE(goods_id, '')), 'sid:', ''), '@goofish', '') = :item_id
                        ORDER BY last_message_time DESC, id DESC
                        LIMIT 50
                        """
                    ),
                    {"account_id": account_id, "item_id": item_id},
                )
            ).mappings().all()
            resolved = self._context_from_conversations(conversation_rows, buyer_id)

        if resolved is None:
            await self._db.rollback()
            raise ManualDeliveryError(
                422,
                "conversation_context_missing",
                "无法安全确定买家会话与接收者；请先同步订单，并在消息页与该买家建立会话后重试",
                data={"recovery": "sync_order_and_open_conversation", "retrySafe": False},
            )

        session_id, peer_id = resolved
        return DeliveryContext(
            order_id=int(order.id),
            external_order_id=external_order_id,
            account_id=account_id,
            buyer_id=buyer_id,
            item_id=item_id,
            session_id=session_id,
            peer_id=peer_id,
        )

    @staticmethod
    def _context_from_messages(rows, buyer_id: str) -> tuple[str, str] | None:
        for row in rows:
            session_id = _normalize_identifier(row.get("s_id"))
            candidates = [
                _normalize_identifier(row.get("peer_external_uid")),
                _normalize_identifier(row.get("sender_user_id")),
                _normalize_identifier(row.get("receiver_user_id")),
            ]
            if not session_id or buyer_id not in candidates:
                continue
            peer_id = next((candidate for candidate in candidates if candidate == buyer_id), "")
            if peer_id and peer_id != session_id:
                return session_id, peer_id
        return None

    @staticmethod
    def _context_from_conversations(rows, buyer_id: str) -> tuple[str, str] | None:
        for row in rows:
            external_buyer = _normalize_identifier(row.get("external_buyer_id"))
            peer_external = _normalize_identifier(row.get("peer_external_uid"))
            peer_key_raw = str(row.get("peer_key") or "").strip()
            peer_key = _normalize_identifier(peer_key_raw)
            if buyer_id not in {external_buyer, peer_external, peer_key}:
                continue
            session_id = peer_key if peer_key_raw.startswith("sid:") else ""
            peer_id = peer_external or (external_buyer if not str(row.get("external_buyer_id") or "").startswith("sid:") else "")
            if session_id and peer_id and session_id != peer_id:
                return session_id, peer_id
        return None

    @staticmethod
    def _release(attempt: ManualDeliveryAttempt) -> None:
        attempt.lease_token = None
        attempt.lease_until = None

    @staticmethod
    def _lease(
        attempt: ManualDeliveryAttempt,
        *,
        action: AttemptAction,
        repeated: bool = False,
    ) -> AttemptLease:
        return AttemptLease(
            attempt_id=int(attempt.id),
            idempotency_key=str(attempt.idempotency_key),
            state=attempt.state,
            action=action,
            lease_token=attempt.lease_token,
            context=DeliveryContext(
                order_id=int(attempt.order_id),
                external_order_id=str(attempt.external_order_id),
                account_id=int(attempt.account_id),
                buyer_id=str(attempt.peer_id),
                item_id=str(attempt.item_id),
                session_id=str(attempt.session_id),
                peer_id=str(attempt.peer_id),
            ),
            retry_safe=bool(attempt.retry_safe),
            retry_scope=attempt.retry_scope,
            error_code=attempt.last_error_code,
            error_message=attempt.error_message,
            repeated=repeated,
            message_confirmed=attempt.message_confirmed_at is not None,
            platform_confirmed=attempt.platform_confirmed_at is not None,
        )


class XianyuManualDeliveryGateway:
    """Adapters for the authoritative WS message and Xianyu shipment calls."""

    async def send_message(
        self,
        context: DeliveryContext,
        content: str,
    ) -> ExternalStepResult:
        from .ws_delivery_handler import send_delivery_message_result

        result = await send_delivery_message_result(
            context.account_id,
            context.session_id,
            context.peer_id,
            content,
        )
        status = result.get("status")
        if status == "confirmed":
            return ExternalStepResult.confirmed()
        if status == "unknown":
            return ExternalStepResult.unknown(
                "message_ack_unknown",
                "发送结果未确认，请先在闲鱼 App 核对，避免重复发送",
            )
        return ExternalStepResult.failed(
            _safe_error_code(result.get("errorCode"), "message_send_failed"),
            _safe_error_message(
                result.get("message"),
                "买家消息发送失败，请检查账号连接后重试",
            ),
            retry_safe=bool(result.get("retrySafe", True)),
        )

    async def confirm_shipment(self, context: DeliveryContext) -> ExternalStepResult:
        from .xianyu_api_service import confirm_shipment

        result = await asyncio.to_thread(
            confirm_shipment,
            context.account_id,
            context.external_order_id,
        )
        if isinstance(result, dict) and result.get("success") is True:
            return ExternalStepResult.confirmed()

        error = str(result.get("error") or "") if isinstance(result, dict) else ""
        error_upper = error.upper()
        if "SESSION_EXPIRED" in error_upper:
            return ExternalStepResult.failed(
                "account_session_expired",
                "账号登录状态已失效，请重新登录后仅重试平台确认",
                retry_safe=True,
            )
        if "CAPTCHA_NEEDED" in error_upper:
            return ExternalStepResult.failed(
                "captcha_required",
                "平台要求完成滑块验证；验证后仅重试平台确认",
                retry_safe=True,
            )
        if any(marker in error for marker in ("Cookie为空", "无法获取账号认证信息", "无法提取签名token")):
            return ExternalStepResult.failed(
                "account_auth_unavailable",
                "账号认证不可用，请重新登录后仅重试平台确认",
                retry_safe=True,
            )
        if "超时" in error or result is None:
            return ExternalStepResult.unknown(
                "platform_confirmation_unknown",
                "平台确认结果未知，请先同步订单并在闲鱼 App 核对",
            )
        if isinstance(result, dict) and result.get("ret") is not None:
            return ExternalStepResult.failed(
                "platform_confirmation_rejected",
                "平台明确拒绝确认发货；排除账号或订单问题后仅重试平台确认",
                retry_safe=True,
            )
        return ExternalStepResult.unknown(
            "platform_confirmation_unknown",
            "平台确认结果未知，请先同步订单并在闲鱼 App 核对",
        )
