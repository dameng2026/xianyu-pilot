from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import logging
import re
import uuid
from dataclasses import dataclass
from typing import Literal, Protocol

from sqlalchemy import or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.logging_security import redact_sensitive_text
from ..models.entities import (
    CardItem,
    DeliveryRecord,
    RealtimeDeliveryAttempt,
    XianyuTradeOrder,
)


logger = logging.getLogger(__name__)
_SAFE_ERROR_CODE_RE = re.compile(r"[^a-z0-9_]+")
_ACTIVE_STATES = {"pending", "message_sending", "message_sent", "platform_confirming"}


def _now() -> dt.datetime:
    return dt.datetime.now()


def _safe_error_message(value: object, fallback: str) -> str:
    cleaned = redact_sensitive_text(str(value or "")).strip()
    return (cleaned or fallback)[:500]


def _safe_error_code(value: object, fallback: str) -> str:
    normalized = _SAFE_ERROR_CODE_RE.sub("_", str(value or "").strip().lower()).strip("_")
    return (normalized or fallback)[:64]


def build_realtime_delivery_event_key(
    *,
    account_id: int,
    external_order_id: str | None,
    source_event_id: str,
    session_id: str,
    item_id: str,
) -> str:
    """Build a stable account-scoped key without conflating orderless events."""

    if int(account_id) <= 0:
        raise ValueError("account id is required for realtime delivery")
    normalized_order = str(external_order_id or "").strip()
    normalized_event = str(source_event_id or "").strip()
    # 归一化 @goofish 后缀：闲鱼 WS 推送的同一会话消息可能带或不带 @goofish 后缀
    # （如 64799897685 vs 64799897685@goofish），若不归一化会导致同一订单生成不同
    # event_key，破坏幂等性，造成重复发货（事故级 Bug）。
    normalized_session = str(session_id or "").strip().removesuffix("@goofish")
    normalized_item = str(item_id or "").strip()
    if not normalized_session or not normalized_item:
        raise ValueError("session and item are required for realtime delivery")
    if not normalized_order and not normalized_event:
        raise ValueError("source event id is required when order id is empty")

    if normalized_order:
        # An order is the strongest identity. Context drift must cause a payload
        # conflict on the same attempt, never a second irreversible send.
        material = f"realtime-delivery:v1:{int(account_id)}:order:{normalized_order}"
    else:
        material = (
            f"realtime-delivery:v1:{int(account_id)}:source-event:{normalized_event}:"
            f"{normalized_session}:{normalized_item}"
        )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()

AttemptState = Literal[
    "pending",
    "message_sending",
    "message_sent",
    "platform_confirming",
    "success",
    "failed",
    "unknown",
]
AttemptAction = Literal["send_message", "confirm_platform", "return", "in_progress"]


@dataclass(frozen=True)
class RealtimeDeliveryCommand:
    event_key: str
    account_id: int
    external_order_id: str | None
    source_event_id: str
    session_id: str
    peer_id: str
    item_id: str
    rule_id: int | None
    delivery_mode: str
    delivery_content: str
    quantity_requested: int
    card_group_id: int | None
    auto_confirm_shipment: bool


@dataclass(frozen=True)
class ExternalDeliveryResult:
    status: Literal["confirmed", "failed", "unknown"]
    error_code: str | None = None
    message: str = ""
    retry_safe: bool = False

    @classmethod
    def confirmed(cls) -> "ExternalDeliveryResult":
        return cls(status="confirmed")

    @classmethod
    def failed(
        cls,
        error_code: str,
        message: str,
        *,
        retry_safe: bool,
    ) -> "ExternalDeliveryResult":
        return cls(
            status="failed",
            error_code=error_code,
            message=message,
            retry_safe=retry_safe,
        )

    @classmethod
    def unknown(cls, error_code: str, message: str) -> "ExternalDeliveryResult":
        return cls(
            status="unknown",
            error_code=error_code,
            message=message,
            retry_safe=False,
        )


@dataclass(frozen=True)
class PreparedDeliveryMessage:
    status: Literal["ready", "failed"]
    content: str | None = None
    error_code: str | None = None
    message: str = ""
    retry_safe: bool = False

    @classmethod
    def ready(cls, content: str) -> "PreparedDeliveryMessage":
        return cls(status="ready", content=content)

    @classmethod
    def failed(
        cls,
        error_code: str,
        message: str,
        *,
        retry_safe: bool,
    ) -> "PreparedDeliveryMessage":
        return cls(
            status="failed",
            error_code=error_code,
            message=message,
            retry_safe=retry_safe,
        )


@dataclass(frozen=True)
class RealtimeDeliveryAttemptLease:
    attempt_id: int
    event_key: str
    state: AttemptState
    action: AttemptAction
    lease_token: str | None
    account_id: int
    external_order_id: str | None
    session_id: str
    peer_id: str
    item_id: str
    delivery_mode: str
    quantity_requested: int
    auto_confirm_shipment: bool
    retry_safe: bool
    retry_scope: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    repeated: bool = False
    message_confirmed: bool = False
    platform_confirmed: bool = False


@dataclass(frozen=True)
class RealtimeDeliveryOutcome:
    status: AttemptState | Literal["in_progress"]
    attempt_id: int
    event_key: str
    retry_safe: bool
    retry_scope: str | None
    error_code: str | None
    message: str
    message_confirmed: bool
    platform_confirmed: bool
    repeated: bool = False


class RealtimeDeliveryError(Exception):
    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.public_message = message


class RealtimeDeliveryStore(Protocol):
    async def acquire(
        self,
        command: RealtimeDeliveryCommand,
    ) -> RealtimeDeliveryAttemptLease: ...

    async def prepare_message(
        self,
        lease: RealtimeDeliveryAttemptLease,
        command: RealtimeDeliveryCommand,
    ) -> PreparedDeliveryMessage: ...

    async def mark_message_confirmed(
        self,
        lease: RealtimeDeliveryAttemptLease,
    ) -> RealtimeDeliveryAttemptLease: ...

    async def mark_message_failed(
        self,
        lease: RealtimeDeliveryAttemptLease,
        result: ExternalDeliveryResult,
    ) -> RealtimeDeliveryAttemptLease: ...

    async def mark_unknown(
        self,
        lease: RealtimeDeliveryAttemptLease,
        result: ExternalDeliveryResult,
        *,
        retry_scope: str,
    ) -> RealtimeDeliveryAttemptLease: ...

    async def mark_confirming(
        self,
        lease: RealtimeDeliveryAttemptLease,
    ) -> RealtimeDeliveryAttemptLease: ...

    async def mark_confirmation_failed(
        self,
        lease: RealtimeDeliveryAttemptLease,
        result: ExternalDeliveryResult,
    ) -> RealtimeDeliveryAttemptLease: ...

    async def mark_success(
        self,
        lease: RealtimeDeliveryAttemptLease,
    ) -> RealtimeDeliveryAttemptLease: ...


class RealtimeDeliveryGateway(Protocol):
    async def send_message(
        self,
        lease: RealtimeDeliveryAttemptLease,
        content: str,
    ) -> ExternalDeliveryResult: ...

    async def confirm_shipment(
        self,
        lease: RealtimeDeliveryAttemptLease,
    ) -> ExternalDeliveryResult: ...


class SqlRealtimeDeliveryStore:
    """MySQL-backed attempt and lease adapter for the coordinator seam."""

    def __init__(self, db: AsyncSession, *, lease_seconds: int = 45) -> None:
        self._db = db
        self._lease_seconds = max(int(lease_seconds), 10)

    async def acquire(
        self,
        command: RealtimeDeliveryCommand,
    ) -> RealtimeDeliveryAttemptLease:
        content_digest = hashlib.sha256(command.delivery_content.encode("utf-8")).hexdigest()
        attempt = await self._find_locked(command.event_key)
        if attempt is None:
            try:
                return await self._create(command, content_digest)
            except IntegrityError:
                await self._db.rollback()
                attempt = await self._find_locked(command.event_key)
                if attempt is None:
                    raise

        self._validate_payload(attempt, command, content_digest)
        now = _now()
        lease_active = bool(attempt.lease_token and attempt.lease_until and attempt.lease_until > now)

        if attempt.state in {"message_sending", "platform_confirming"}:
            if lease_active:
                return self._lease(attempt, action="in_progress", repeated=True)
            retry_scope = (
                "message" if attempt.state == "message_sending" else "platform_confirm"
            )
            attempt.state = "unknown"
            attempt.retry_scope = retry_scope
            attempt.retry_safe = 0
            attempt.last_error_code = (
                "message_result_unknown_after_restart"
                if retry_scope == "message"
                else "platform_confirmation_unknown_after_restart"
            )
            attempt.error_message = (
                "进程在发送结果落库前中断，请先在闲鱼 App 核对；系统不会自动重试"
                if retry_scope == "message"
                else "进程在确认结果落库前中断，请先同步订单并在闲鱼 App 核对"
            )
            self._release(attempt)
            await self._update_delivery_record(attempt, "unknown", attempt.error_message)
            await self._db.commit()
            return self._lease(attempt, action="return", repeated=True)

        if lease_active and attempt.state in _ACTIVE_STATES:
            return self._lease(attempt, action="in_progress", repeated=True)
        if attempt.state in {"success", "unknown"}:
            return self._lease(attempt, action="return", repeated=True)
        if attempt.state == "failed" and not bool(attempt.retry_safe):
            return self._lease(attempt, action="return", repeated=True)

        if attempt.state == "message_sent":
            if attempt.retry_scope == "platform_confirm" and not bool(
                attempt.retry_safe
            ):
                return self._lease(attempt, action="return", repeated=True)
            if not bool(attempt.auto_confirm_shipment):
                attempt.state = "success"
                attempt.retry_scope = None
                attempt.retry_safe = 0
                self._release(attempt)
                await self._update_delivery_record(attempt, "success", None)
                await self._db.commit()
                return self._lease(attempt, action="return", repeated=True)
            if not attempt.external_order_id:
                return self._lease(attempt, action="return", repeated=True)
            action: AttemptAction = "confirm_platform"
        else:
            action = "send_message"

        attempt.lease_token = uuid.uuid4().hex
        attempt.lease_until = now + dt.timedelta(seconds=self._lease_seconds)
        attempt.attempt_count = int(attempt.attempt_count or 0) + 1
        await self._db.commit()
        return self._lease(attempt, action=action, repeated=True)

    async def prepare_message(
        self,
        lease: RealtimeDeliveryAttemptLease,
        command: RealtimeDeliveryCommand,
    ) -> PreparedDeliveryMessage:
        attempt = await self._locked_attempt(lease)
        mode = str(command.delivery_mode or "").strip().lower()
        if mode == "text":
            content = str(command.delivery_content or "").strip()
            if not content:
                return PreparedDeliveryMessage.failed(
                    "delivery_content_empty",
                    "文本发货内容为空，未向买家发送消息",
                    retry_safe=False,
                )
            self._mark_message_sending(attempt)
            await self._db.commit()
            return PreparedDeliveryMessage.ready(content)

        if mode not in {"card", "kami"}:
            return PreparedDeliveryMessage.failed(
                "delivery_mode_unavailable",
                "该发货模式已停用；仅支持文本或卡密发货",
                retry_safe=False,
            )
        if not command.card_group_id:
            return PreparedDeliveryMessage.failed(
                "card_group_missing",
                "未绑定卡密分组，未向买家发送消息",
                retry_safe=False,
            )

        quantity = max(int(command.quantity_requested or 1), 1)
        cards = (
            await self._db.execute(
                select(CardItem)
                .where(
                    CardItem.group_id == int(command.card_group_id),
                    CardItem.deleted == 0,
                    CardItem.status == 0,
                    CardItem.is_used == 0,
                    or_(CardItem.expire_time.is_(None), CardItem.expire_time > _now()),
                )
                .order_by(CardItem.id.asc())
                .limit(quantity)
                .with_for_update(skip_locked=True)
            )
        ).scalars().all()
        if len(cards) != quantity:
            return PreparedDeliveryMessage.failed(
                "card_inventory_insufficient",
                f"卡密库存不足，需要 {quantity} 条；本次未认领任何卡密",
                retry_safe=True,
            )

        claim_token = lease.lease_token or uuid.uuid4().hex
        rendered: list[str] = []
        template = str(command.delivery_content or "").strip() or "{卡密}"
        for card in cards:
            secret = str(card.card_value or card.card_key or "")
            if not secret:
                await self._db.rollback()
                return PreparedDeliveryMessage.failed(
                    "card_content_invalid",
                    "卡密内容无效，本次未认领任何卡密",
                    retry_safe=False,
                )
            card.status = 1
            card.realtime_attempt_id = int(attempt.id)
            card.claim_token = claim_token
            card.updated_time = _now()
            if "{卡密}" in template or "{kmKey}" in template:
                item_content = template.replace("{卡密}", secret).replace("{kmKey}", secret)
            else:
                item_content = f"{template}\n{secret}" if template else secret
            rendered.append(item_content)

        self._mark_message_sending(attempt)
        await self._db.commit()
        return PreparedDeliveryMessage.ready("\n---\n".join(rendered))

    async def mark_unknown(
        self,
        lease: RealtimeDeliveryAttemptLease,
        result: ExternalDeliveryResult,
        *,
        retry_scope: str,
    ) -> RealtimeDeliveryAttemptLease:
        attempt = await self._locked_attempt(lease)
        attempt.state = "unknown"
        attempt.retry_scope = retry_scope
        attempt.retry_safe = 0
        attempt.last_error_code = _safe_error_code(
            result.error_code,
            "delivery_result_unknown",
        )
        attempt.error_message = _safe_error_message(
            result.message,
            "执行结果未知，请人工核对；系统不会自动重试",
        )
        self._release(attempt)
        await self._update_delivery_record(attempt, "unknown", attempt.error_message)
        await self._db.commit()
        return self._lease(attempt, action="return")

    async def mark_success(
        self,
        lease: RealtimeDeliveryAttemptLease,
    ) -> RealtimeDeliveryAttemptLease:
        attempt = await self._locked_attempt(lease)
        if attempt.message_confirmed_at is None:
            raise RealtimeDeliveryError(
                "message_not_confirmed",
                "买家消息尚未确认发送，系统不会标记自动发货成功",
            )

        now = _now()
        if attempt.auto_confirm_shipment and attempt.external_order_id:
            attempt.platform_confirmed_at = attempt.platform_confirmed_at or now
            order = (
                await self._db.execute(
                    select(XianyuTradeOrder)
                    .where(
                        XianyuTradeOrder.account_id == int(attempt.account_id),
                        XianyuTradeOrder.external_order_id
                        == str(attempt.external_order_id),
                        XianyuTradeOrder.deleted == 0,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if order is not None:
                order.order_status = max(int(order.order_status or 0), 3)
                order.ship_time = order.ship_time or now
                order.updated_time = now

        attempt.state = "success"
        attempt.retry_scope = None
        attempt.retry_safe = 0
        attempt.last_error_code = None
        attempt.error_message = None
        self._release(attempt)
        await self._update_delivery_record(attempt, "success", None)
        await self._db.commit()
        return self._lease(attempt, action="return")

    async def mark_message_confirmed(
        self,
        lease: RealtimeDeliveryAttemptLease,
    ) -> RealtimeDeliveryAttemptLease:
        attempt = await self._locked_attempt(lease)
        if str(attempt.delivery_mode).lower() in {"card", "kami"}:
            claimed_cards = (
                await self._db.execute(
                    select(CardItem)
                    .where(
                        CardItem.realtime_attempt_id == int(attempt.id),
                        CardItem.status == 1,
                    )
                    .with_for_update()
                )
            ).scalars().all()
            expected = max(int(attempt.quantity_requested or 1), 1)
            if len(claimed_cards) != expected:
                attempt.state = "unknown"
                attempt.retry_scope = "message"
                attempt.retry_safe = 0
                attempt.last_error_code = "card_claim_mismatch_after_send"
                attempt.error_message = (
                    "买家消息已确认，但卡密认领数量异常；请人工核对，系统不会自动重试"
                )
                self._release(attempt)
                await self._update_delivery_record(
                    attempt,
                    "unknown",
                    attempt.error_message,
                )
                await self._db.commit()
                return self._lease(attempt, action="return")

            now = _now()
            for card in claimed_cards:
                card.status = 2
                card.is_used = 1
                card.used_time = card.used_time or now
                card.claim_token = None
                card.updated_time = now
            await self._sync_card_group_stats(attempt.card_group_id)

        now = _now()
        attempt.state = "message_sent"
        attempt.retry_scope = (
            "platform_confirm"
            if attempt.auto_confirm_shipment and attempt.external_order_id
            else None
        )
        attempt.retry_safe = 1 if attempt.retry_scope else 0
        attempt.message_confirmed_at = attempt.message_confirmed_at or now
        attempt.last_error_code = None
        attempt.error_message = None
        await self._update_delivery_record(attempt, "partial", None)
        await self._db.commit()
        return self._lease(
            attempt,
            action="confirm_platform" if attempt.retry_scope else "return",
        )

    async def mark_confirming(
        self,
        lease: RealtimeDeliveryAttemptLease,
    ) -> RealtimeDeliveryAttemptLease:
        attempt = await self._locked_attempt(lease)
        if attempt.state != "message_sent" or attempt.message_confirmed_at is None:
            raise RealtimeDeliveryError(
                "message_not_confirmed",
                "买家消息尚未确认发送，系统不会调用平台确认发货",
            )
        attempt.state = "platform_confirming"
        attempt.retry_scope = "platform_confirm"
        attempt.retry_safe = 0
        attempt.platform_confirm_started_at = (
            attempt.platform_confirm_started_at or _now()
        )
        attempt.last_error_code = None
        attempt.error_message = None
        await self._db.commit()
        return self._lease(attempt, action="confirm_platform")

    async def mark_confirmation_failed(
        self,
        lease: RealtimeDeliveryAttemptLease,
        result: ExternalDeliveryResult,
    ) -> RealtimeDeliveryAttemptLease:
        attempt = await self._locked_attempt(lease)
        attempt.state = "message_sent"
        attempt.retry_scope = "platform_confirm"
        attempt.retry_safe = 1 if result.retry_safe else 0
        attempt.last_error_code = _safe_error_code(
            result.error_code,
            "platform_confirmation_failed",
        )
        attempt.error_message = _safe_error_message(
            result.message,
            "买家消息已发送，但平台确认发货失败；重试只会执行平台确认",
        )
        self._release(attempt)
        await self._update_delivery_record(attempt, "partial", attempt.error_message)
        await self._db.commit()
        return self._lease(attempt, action="return")

    async def mark_message_failed(
        self,
        lease: RealtimeDeliveryAttemptLease,
        result: ExternalDeliveryResult,
    ) -> RealtimeDeliveryAttemptLease:
        attempt = await self._locked_attempt(lease)
        if str(attempt.delivery_mode).lower() in {"card", "kami"}:
            claimed_cards = (
                await self._db.execute(
                    select(CardItem)
                    .where(
                        CardItem.realtime_attempt_id == int(attempt.id),
                        CardItem.status == 1,
                    )
                    .with_for_update()
                )
            ).scalars().all()
            for card in claimed_cards:
                card.status = 0
                card.realtime_attempt_id = None
                card.claim_token = None
                card.used_time = None
                card.updated_time = _now()
            await self._sync_card_group_stats(attempt.card_group_id)

        attempt.state = "failed"
        attempt.retry_scope = "message" if result.retry_safe else None
        attempt.retry_safe = 1 if result.retry_safe else 0
        attempt.last_error_code = _safe_error_code(
            result.error_code,
            "message_send_failed",
        )
        attempt.error_message = _safe_error_message(
            result.message,
            "买家消息发送失败，本次未确认发货",
        )
        self._release(attempt)
        await self._update_delivery_record(attempt, "failed", attempt.error_message)
        await self._db.commit()
        return self._lease(attempt, action="return")

    async def _sync_card_group_stats(self, card_group_id: int | None) -> None:
        """Recalculate card_group statistics after card status changes.

        Keeps total_count/used_count/available_count consistent with the
        actual card_item rows, preventing inventory drift caused by crashes
        or orphaned claims.
        """
        if not card_group_id:
            return
        await self._db.execute(
            text(
                """
                UPDATE card_group cg
                SET cg.total_count = (
                        SELECT COUNT(*) FROM card_item ci
                        WHERE ci.group_id = cg.id AND ci.deleted = 0
                    ),
                    cg.used_count = (
                        SELECT COUNT(*) FROM card_item ci
                        WHERE ci.group_id = cg.id AND ci.deleted = 0
                          AND ci.status = 2
                    ),
                    cg.available_count = (
                        SELECT COUNT(*) FROM card_item ci
                        WHERE ci.group_id = cg.id AND ci.deleted = 0
                          AND ci.status = 0
                          AND ci.is_used = 0
                          AND (ci.expire_time IS NULL OR ci.expire_time > NOW())
                    ),
                    cg.updated_time = NOW()
                WHERE cg.id = :gid
                """
            ),
            {"gid": int(card_group_id)},
        )

    async def _locked_attempt(
        self,
        lease: RealtimeDeliveryAttemptLease,
    ) -> RealtimeDeliveryAttempt:
        attempt = (
            await self._db.execute(
                select(RealtimeDeliveryAttempt)
                .where(RealtimeDeliveryAttempt.id == lease.attempt_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if attempt is None:
            raise RealtimeDeliveryError(
                "attempt_not_found",
                "实时发货尝试不存在，系统未执行外部发送",
            )
        if not lease.lease_token or attempt.lease_token != lease.lease_token:
            await self._db.rollback()
            raise RealtimeDeliveryError(
                "attempt_lease_lost",
                "实时发货执行权已变化，系统未重复发送",
            )
        return attempt

    @staticmethod
    def _mark_message_sending(attempt: RealtimeDeliveryAttempt) -> None:
        attempt.state = "message_sending"
        attempt.retry_scope = "message"
        attempt.retry_safe = 0
        attempt.message_started_at = attempt.message_started_at or _now()
        attempt.last_error_code = None
        attempt.error_message = None

    async def _find_locked(self, event_key: str) -> RealtimeDeliveryAttempt | None:
        return (
            await self._db.execute(
                select(RealtimeDeliveryAttempt)
                .where(RealtimeDeliveryAttempt.event_key == event_key)
                .with_for_update()
            )
        ).scalar_one_or_none()

    async def _create(
        self,
        command: RealtimeDeliveryCommand,
        content_digest: str,
    ) -> RealtimeDeliveryAttemptLease:
        now = _now()
        lease_token = uuid.uuid4().hex
        record = DeliveryRecord(
            account_id=command.account_id,
            order_id=None,
            rule_id=command.rule_id,
            delivery_type=command.delivery_mode,
            delivery_mode=command.delivery_mode,
            content=None,
            delivery_content=None,
            receiver_info=None,
            delivery_timing="after_payment",
            status=0,
            delivery_status="pending",
            error_message=None,
            retry_count=0,
            fail_reason=None,
            quantity_requested=max(int(command.quantity_requested), 1),
            quantity_sent=0,
            result="pending",
            deleted=0,
        )
        self._db.add(record)
        await self._db.flush()

        attempt = RealtimeDeliveryAttempt(
            event_key=command.event_key,
            account_id=command.account_id,
            external_order_id=command.external_order_id or None,
            source_event_id=command.source_event_id,
            session_id=command.session_id,
            peer_id=command.peer_id,
            item_id=command.item_id,
            rule_id=command.rule_id,
            delivery_record_id=record.id,
            delivery_mode=command.delivery_mode,
            content_digest=content_digest,
            quantity_requested=max(int(command.quantity_requested), 1),
            card_group_id=command.card_group_id,
            auto_confirm_shipment=1 if command.auto_confirm_shipment else 0,
            state="pending",
            retry_scope="message",
            retry_safe=1,
            attempt_count=1,
            lease_token=lease_token,
            lease_until=now + dt.timedelta(seconds=self._lease_seconds),
        )
        self._db.add(attempt)
        await self._db.flush()
        record.receiver_info = json.dumps(
            {"realtimeAttemptId": int(attempt.id)},
            separators=(",", ":"),
        )
        await self._db.commit()
        return self._lease(attempt, action="send_message")

    @staticmethod
    def _validate_payload(
        attempt: RealtimeDeliveryAttempt,
        command: RealtimeDeliveryCommand,
        content_digest: str,
    ) -> None:
        ordered_attempt = bool(str(attempt.external_order_id or "").strip())
        expected = (
            int(attempt.account_id),
            str(attempt.external_order_id or ""),
            "" if ordered_attempt else str(attempt.source_event_id),
            str(attempt.session_id),
            str(attempt.peer_id),
            str(attempt.item_id),
            str(attempt.delivery_mode),
            str(attempt.content_digest),
            int(attempt.quantity_requested),
            int(attempt.card_group_id) if attempt.card_group_id is not None else None,
            bool(attempt.auto_confirm_shipment),
        )
        incoming = (
            int(command.account_id),
            str(command.external_order_id or ""),
            "" if ordered_attempt else str(command.source_event_id),
            str(command.session_id),
            str(command.peer_id),
            str(command.item_id),
            str(command.delivery_mode),
            content_digest,
            max(int(command.quantity_requested), 1),
            int(command.card_group_id) if command.card_group_id is not None else None,
            bool(command.auto_confirm_shipment),
        )
        if expected != incoming:
            raise RealtimeDeliveryError(
                "event_payload_conflict",
                "同一发货事件对应的账号、会话或发货内容已变化，系统已拒绝重复执行",
            )

    async def _update_delivery_record(
        self,
        attempt: RealtimeDeliveryAttempt,
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
        record.error_message = _safe_error_message(error_message, "自动发货状态已更新") if error_message else None
        record.fail_reason = record.error_message
        record.retry_count = max(int(attempt.attempt_count or 1) - 1, 0)
        record.quantity_requested = int(attempt.quantity_requested or 1)
        record.quantity_sent = (
            int(attempt.quantity_requested or 1)
            if attempt.message_confirmed_at is not None
            else 0
        )
        record.delivery_time = attempt.message_confirmed_at
        record.completed_time = _now() if status in {"success", "failed", "unknown"} else None
        record.platform_sync_time = attempt.platform_confirmed_at
        record.result = status

    @staticmethod
    def _release(attempt: RealtimeDeliveryAttempt) -> None:
        attempt.lease_token = None
        attempt.lease_until = None

    @staticmethod
    def _lease(
        attempt: RealtimeDeliveryAttempt,
        *,
        action: AttemptAction,
        repeated: bool = False,
    ) -> RealtimeDeliveryAttemptLease:
        return RealtimeDeliveryAttemptLease(
            attempt_id=int(attempt.id),
            event_key=str(attempt.event_key),
            state=attempt.state,
            action=action,
            lease_token=attempt.lease_token,
            account_id=int(attempt.account_id),
            external_order_id=str(attempt.external_order_id) if attempt.external_order_id else None,
            session_id=str(attempt.session_id),
            peer_id=str(attempt.peer_id),
            item_id=str(attempt.item_id),
            delivery_mode=str(attempt.delivery_mode),
            quantity_requested=int(attempt.quantity_requested or 1),
            auto_confirm_shipment=bool(attempt.auto_confirm_shipment),
            retry_safe=bool(attempt.retry_safe),
            retry_scope=attempt.retry_scope,
            error_code=attempt.last_error_code,
            error_message=attempt.error_message,
            repeated=repeated,
            message_confirmed=attempt.message_confirmed_at is not None,
            platform_confirmed=attempt.platform_confirmed_at is not None,
        )


async def heal_orphaned_card_claims(db: AsyncSession) -> int:
    """Recover orphaned card claims left by crashed or stuck delivery attempts.

    Cards with status=1 (claimed) linked to attempts that have reached a
    terminal state (success/failed/unknown) are reset to status=0 (available)
    so they can be reused. This prevents inventory leakage when the process
    dies between claiming a card and finalizing the attempt.

    Returns the number of cards healed.
    """
    result = await db.execute(
        text(
            """
            UPDATE card_item ci
            INNER JOIN realtime_delivery_attempt rda
              ON rda.id = ci.realtime_attempt_id
            SET ci.status = 0,
                ci.realtime_attempt_id = NULL,
                ci.claim_token = NULL,
                ci.used_time = NULL,
                ci.updated_time = NOW()
            WHERE ci.deleted = 0
              AND ci.status = 1
              AND rda.state IN ('success', 'failed', 'unknown')
            """
        )
    )
    healed = result.rowcount or 0
    if healed > 0:
        # Refresh statistics for affected card groups
        await db.execute(
            text(
                """
                UPDATE card_group cg
                SET cg.total_count = (
                        SELECT COUNT(*) FROM card_item ci
                        WHERE ci.group_id = cg.id AND ci.deleted = 0
                    ),
                    cg.used_count = (
                        SELECT COUNT(*) FROM card_item ci
                        WHERE ci.group_id = cg.id AND ci.deleted = 0
                          AND ci.status = 2
                    ),
                    cg.available_count = (
                        SELECT COUNT(*) FROM card_item ci
                        WHERE ci.group_id = cg.id AND ci.deleted = 0
                          AND ci.status = 0
                          AND ci.is_used = 0
                          AND (ci.expire_time IS NULL OR ci.expire_time > NOW())
                    ),
                    cg.updated_time = NOW()
                WHERE cg.id IN (
                    SELECT DISTINCT ci2.group_id FROM card_item ci2
                    WHERE ci2.deleted = 0 AND ci2.status = 1
                )
                """
            )
        )
        logger.info("healed orphaned card claims count=%d", healed)
    await db.commit()
    return healed


class XianyuRealtimeDeliveryGateway:
    """Adapters for the WebSocket message and platform confirmation seams."""

    async def send_message(
        self,
        lease: RealtimeDeliveryAttemptLease,
        content: str,
    ) -> ExternalDeliveryResult:
        from .ws_delivery_handler import send_delivery_message_result

        result = await send_delivery_message_result(
            lease.account_id,
            lease.session_id,
            lease.peer_id,
            content,
        )
        status = result.get("status")
        if status == "confirmed":
            return ExternalDeliveryResult.confirmed()
        if status == "unknown":
            return ExternalDeliveryResult.unknown(
                "message_ack_unknown",
                "发送结果未确认，请先在闲鱼 App 核对，系统不会自动重试",
            )
        return ExternalDeliveryResult.failed(
            _safe_error_code(result.get("errorCode"), "message_send_failed"),
            _safe_error_message(
                result.get("message"),
                "买家消息发送失败，本次未确认发货",
            ),
            retry_safe=bool(result.get("retrySafe", True)),
        )

    async def confirm_shipment(
        self,
        lease: RealtimeDeliveryAttemptLease,
    ) -> ExternalDeliveryResult:
        external_order_id = str(lease.external_order_id or "")

        # 小刀订单需调用免拼发货接口，普通订单调用确认发货接口
        is_bargain = False
        if external_order_id:
            try:
                from ..core.database import async_session
                from ..models.entities import XianyuTradeOrder
                from sqlalchemy import select

                async with async_session() as db:
                    bargain_result = await db.execute(
                        select(XianyuTradeOrder.is_bargain).where(
                            XianyuTradeOrder.account_id == lease.account_id,
                            XianyuTradeOrder.external_order_id == external_order_id,
                            XianyuTradeOrder.deleted == 0,
                        )
                    )
                    bargain_row = bargain_result.scalar_one_or_none()
                    if bargain_row:
                        is_bargain = bool(bargain_row)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "查询订单小刀状态失败，按普通订单处理 accountId=%d orderNo=%s errorType=%s",
                    lease.account_id,
                    external_order_id,
                    type(exc).__name__,
                )

        if is_bargain:
            from .xianyu_api_service import freeshipping_order

            result = await asyncio.to_thread(
                freeshipping_order,
                lease.account_id,
                external_order_id,
                str(lease.item_id or ""),
                str(lease.peer_id or ""),
            )
        else:
            from .xianyu_api_service import confirm_shipment

            result = await asyncio.to_thread(
                confirm_shipment,
                lease.account_id,
                external_order_id,
            )

        if isinstance(result, dict) and result.get("success") is True:
            return ExternalDeliveryResult.confirmed()

        error = str(result.get("error") or "") if isinstance(result, dict) else ""
        error_upper = error.upper()
        if "SESSION_EXPIRED" in error_upper:
            return ExternalDeliveryResult.failed(
                "account_session_expired",
                "账号登录状态已失效；重新登录后只重试平台确认",
                retry_safe=True,
            )
        if "CAPTCHA_NEEDED" in error_upper:
            return ExternalDeliveryResult.failed(
                "captcha_required",
                "平台要求完成滑块验证；验证后只重试平台确认",
                retry_safe=True,
            )
        if any(
            marker in error
            for marker in ("Cookie为空", "无法获取账号认证信息", "无法提取签名token")
        ):
            return ExternalDeliveryResult.failed(
                "account_auth_unavailable",
                "账号认证不可用；重新登录后只重试平台确认",
                retry_safe=True,
            )
        if result is None or any(marker in error for marker in ("超时", "timeout", "Timeout")):
            return ExternalDeliveryResult.unknown(
                "platform_confirmation_unknown",
                "平台确认结果未知，请先同步订单并在闲鱼 App 核对",
            )
        return ExternalDeliveryResult.failed(
            "platform_confirmation_rejected",
            "平台明确拒绝确认发货；修复账号状态后只重试平台确认",
            retry_safe=True,
        )


class RealtimeDeliveryCoordinator:
    """Run irreversible real-time delivery steps behind one small interface."""

    def __init__(
        self,
        *,
        store: RealtimeDeliveryStore,
        gateway: RealtimeDeliveryGateway,
    ) -> None:
        self._store = store
        self._gateway = gateway

    async def execute(
        self,
        command: RealtimeDeliveryCommand,
    ) -> RealtimeDeliveryOutcome:
        lease = await self._store.acquire(command)
        if lease.action in {"return", "in_progress"}:
            return self._outcome(lease)
        if str(command.delivery_mode or "").strip().lower() not in {
            "text",
            "card",
            "kami",
        }:
            lease = await self._store.mark_message_failed(
                lease,
                ExternalDeliveryResult.failed(
                    "delivery_mode_unavailable",
                    "API/custom 发货模式已停用；仅支持文本或卡密发货",
                    retry_safe=False,
                ),
            )
            return self._outcome(lease)
        if lease.action not in {"send_message", "confirm_platform"}:
            raise RuntimeError("unsupported realtime delivery action")

        if lease.action == "send_message":
            prepared = await self._store.prepare_message(lease, command)
            if prepared.status != "ready" or not prepared.content:
                result = ExternalDeliveryResult.failed(
                    prepared.error_code or "delivery_content_unavailable",
                    prepared.message or "发货内容不可用，未向买家发送消息",
                    retry_safe=prepared.retry_safe,
                )
                lease = await self._store.mark_message_failed(lease, result)
                return self._outcome(lease)

            try:
                result = await self._gateway.send_message(lease, prepared.content)
            except Exception as exc:
                logger.error(
                    "Realtime delivery message call ended unexpectedly attemptId=%d errorType=%s",
                    lease.attempt_id,
                    type(exc).__name__,
                )
                result = ExternalDeliveryResult.unknown(
                    "message_result_unknown",
                    "发送结果未确认，请先在闲鱼 App 核对，系统不会自动重试",
                )

            if result.status == "unknown":
                lease = await self._store.mark_unknown(
                    lease,
                    result,
                    retry_scope="message",
                )
                return self._outcome(lease)
            if result.status != "confirmed":
                lease = await self._store.mark_message_failed(lease, result)
                return self._outcome(lease)

            lease = await self._store.mark_message_confirmed(lease)
            if lease.state != "message_sent":
                return self._outcome(lease)
            if lease.auto_confirm_shipment and not lease.external_order_id:
                lease = await self._store.mark_confirmation_failed(
                    lease,
                    ExternalDeliveryResult.failed(
                        "order_id_missing_for_confirmation",
                        "买家消息已发送，但付款事件缺少订单号，无法自动确认发货",
                        retry_safe=False,
                    ),
                )
                return self._outcome(lease)
            if not lease.auto_confirm_shipment:
                lease = await self._store.mark_success(lease)
                return self._outcome(lease)

        lease = await self._store.mark_confirming(lease)
        try:
            confirmation = await self._gateway.confirm_shipment(lease)
        except Exception as exc:
            logger.error(
                "Realtime delivery confirmation call ended unexpectedly attemptId=%d errorType=%s",
                lease.attempt_id,
                type(exc).__name__,
            )
            confirmation = ExternalDeliveryResult.unknown(
                "platform_confirmation_unknown",
                "平台确认结果未知，请先同步订单并在闲鱼 App 核对",
            )

        if confirmation.status == "unknown":
            lease = await self._store.mark_unknown(
                lease,
                confirmation,
                retry_scope="platform_confirm",
            )
            return self._outcome(lease)
        if confirmation.status != "confirmed":
            lease = await self._store.mark_confirmation_failed(lease, confirmation)
            return self._outcome(lease)
        lease = await self._store.mark_success(lease)
        return self._outcome(lease)

    @staticmethod
    def _outcome(lease: RealtimeDeliveryAttemptLease) -> RealtimeDeliveryOutcome:
        status: AttemptState | Literal["in_progress"] = (
            "in_progress" if lease.action == "in_progress" else lease.state
        )
        return RealtimeDeliveryOutcome(
            status=status,
            attempt_id=lease.attempt_id,
            event_key=lease.event_key,
            retry_safe=lease.retry_safe,
            retry_scope=lease.retry_scope,
            error_code=lease.error_code,
            message=lease.error_message or "",
            message_confirmed=lease.message_confirmed,
            platform_confirmed=lease.platform_confirmed,
            repeated=lease.repeated,
        )
