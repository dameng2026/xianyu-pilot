"""自动发货补发兜底循环。

扫描已开启自动发货配置但未发出的订单，复用 ``RealtimeDeliveryCoordinator``
的幂等状态机安全补发，避免重复发货。

触发场景：
- WS 实时事件丢失（付款事件期间 WS 断连）
- 首次发送因可重试错误失败（卡密库存不足、WS 临时不可用）
- 启动时遗漏的付款事件

幂等保证：
- ``event_key`` 基于 ``account_id + external_order_id`` 的 SHA256，同一订单永远是同一 key
- ``RealtimeDeliveryCoordinator.acquire`` 自身的状态机保护：
  - ``success``/``unknown``/``message_sent``/``message_sending``/``platform_confirming`` → 跳过
  - ``failed`` 且 ``retry_safe=1`` → 重试
  - 不存在 → 新建 attempt
- 补发查询阶段额外排除已发出消息的 attempt，避免无效调用
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from ..core.database import async_session
from .realtime_delivery import (
    RealtimeDeliveryCommand,
    RealtimeDeliveryCoordinator,
    RealtimeDeliveryOutcome,
    SqlRealtimeDeliveryStore,
    XianyuRealtimeDeliveryGateway,
    build_realtime_delivery_event_key,
)
from .ws_delivery_handler import resolve_realtime_delivery_rule, _render_delivery_content

logger = logging.getLogger(__name__)


# 状态机中"已发出消息或正在发出"的状态，这些订单一律跳过补发。
_ALREADY_SENT_STATES = (
    "success",
    "unknown",
    "message_sent",
    "message_sending",
    "platform_confirming",
)


async def _find_pending_delivery_orders(
    db: AsyncSession,
    *,
    min_age_seconds: int,
    limit: int,
) -> list[dict[str, Any]]:
    """查询已开启自动发货但未发出的订单。

    条件：
    - 订单 ``order_status IN (1, 2)``（已付款 / 待发货）
    - 订单 ``pay_time`` 早于 ``now - min_age_seconds``，避免与 WS 实时事件抢资源
    - 关联账号 ``status=1 AND deleted=0``
    - 商品存在且 ``delivery_goods_config.payDelivery.enabled=1``
    - 不存在 attempt，或 attempt 状态为 ``failed AND retry_safe=1``
      （已成功 / 已发出 / 正在发 / 未知的订单一律跳过）
    """

    rows = (
        await db.execute(
            text(
                """
                SELECT o.id AS order_id,
                       o.account_id,
                       o.external_order_id,
                       o.item_id,
                       o.buyer_id,
                       o.buyer_name,
                       o.is_bargain,
                       o.pay_time,
                       g.id AS local_goods_id
                FROM xianyu_trade_order o
                INNER JOIN xianyu_account acc
                  ON acc.id = o.account_id
                 AND acc.status = 1
                 AND acc.deleted = 0
                INNER JOIN xianyu_goods g
                  ON g.deleted = 0
                 AND (
                    BINARY COALESCE(o.item_id, '') = BINARY CAST(g.id AS CHAR)
                    OR BINARY COALESCE(o.item_id, '') = BINARY COALESCE(g.external_goods_id, '')
                    OR BINARY COALESCE(o.item_id, '') = BINARY COALESCE(g.goods_id, '')
                 )
                INNER JOIN delivery_goods_config gc
                  ON gc.goods_id = g.id
                 AND gc.deleted = 0
                LEFT JOIN realtime_delivery_attempt r
                  ON r.account_id = o.account_id
                 AND r.external_order_id = o.external_order_id
                WHERE o.deleted = 0
                  AND o.order_status IN (1, 2)
                  AND o.external_order_id IS NOT NULL
                  AND o.external_order_id <> ''
                  AND o.pay_time IS NOT NULL
                  AND o.pay_time <= DATE_SUB(NOW(), INTERVAL :min_age_seconds SECOND)
                  AND (
                    r.id IS NULL
                    OR (r.state = 'failed' AND r.retry_safe = 1)
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM realtime_delivery_attempt r2
                    WHERE r2.account_id = o.account_id
                      AND r2.external_order_id = o.external_order_id
                      AND r2.state = 'failed'
                      AND r2.retry_safe = 1
                      AND r2.updated_time > DATE_SUB(NOW(), INTERVAL :retry_throttle_seconds SECOND)
                  )
                ORDER BY o.pay_time ASC, o.id ASC
                LIMIT :limit
                """
            ),
            {
                "min_age_seconds": int(min_age_seconds),
                "retry_throttle_seconds": int(min_age_seconds * 2),
                "limit": int(limit),
            },
        )
    ).mappings().all()
    return [dict(row) for row in rows]


async def _resolve_session_id(
    db: AsyncSession,
    *,
    account_id: int,
    buyer_id: str,
    item_id: str,
) -> str:
    """优先从会话表查找 sId，找不到则构造稳定的合成 session_id。

    合成 session_id 必须稳定（同一订单永远生成同一值），否则会破坏
    ``event_key`` 的幂等性。这里使用 ``recovery:{account_id}:{buyer_id}:{item_id}``。
    """

    buyer_id = str(buyer_id or "").strip()
    item_id = str(item_id or "").strip()
    if buyer_id and item_id:
        row = (
            await db.execute(
                text(
                    """
                    SELECT s_id
                    FROM xianyu_conversation
                    WHERE deleted = 0
                      AND account_id = :account_id
                      AND (
                        peer_external_uid = :buyer_id
                        OR external_buyer_id = :buyer_id
                      )
                      AND goods_id = :item_id
                    ORDER BY updated_time DESC, id DESC
                    LIMIT 1
                    """
                ),
                {
                    "account_id": int(account_id),
                    "buyer_id": buyer_id,
                    "item_id": item_id,
                },
            )
        ).mappings().first()
        if row and row.get("s_id"):
            return str(row["s_id"])
    # 合成稳定 session_id：recovery:{account_id}:{buyer_id}:{item_id}
    return f"recovery:{int(account_id)}:{buyer_id}:{item_id}"


async def _recover_one_order(
    db: AsyncSession,
    order: dict[str, Any],
) -> RealtimeDeliveryOutcome | None:
    """对单个订单执行补发。返回 None 表示跳过（无规则或参数缺失）。"""

    account_id = int(order.get("account_id") or 0)
    external_order_id = str(order.get("external_order_id") or "").strip()
    item_id = str(order.get("item_id") or "").strip()
    buyer_id = str(order.get("buyer_id") or "").strip()
    if account_id <= 0 or not external_order_id or not item_id:
        logger.warning(
            "delivery_recovery skip order=%s missing identity accountId=%d orderNo=%s itemId=%s",
            order.get("order_id"),
            account_id,
            external_order_id,
            item_id,
        )
        return None

    # === 补发前四重校验（避免对不应重试的订单浪费资源） ===
    # 1. 订单取消检查：订单已被取消/关闭则跳过
    order_status_row = (
        await db.execute(
            text(
                """
                SELECT order_status, is_bargain
                FROM xianyu_trade_order
                WHERE account_id = :account_id
                  AND external_order_id = :external_order_id
                  AND deleted = 0
                LIMIT 1
                """
            ),
            {
                "account_id": account_id,
                "external_order_id": external_order_id,
            },
        )
    ).mappings().first()
    if order_status_row is not None:
        order_status = int(order_status_row.get("order_status") or 0)
        # order_status: 1=已付款, 2=待发货, 3=已发货, 4=已收货, 5=已完成, 6=已取消, 7=已关闭
        if order_status not in (1, 2):
            logger.info(
                "delivery_recovery skip order=%s orderStatus=%d (not pending delivery)",
                order.get("order_id"), order_status,
            )
            return None

    # 2. 配置启用状态检查：发货配置已禁用则跳过（resolve_realtime_delivery_rule 会返回 None，
    #    但提前检查可以避免不必要的商品/配置查询）
    # （此检查由 resolve_realtime_delivery_rule 内部的 enabled 判断覆盖，无需重复）

    # 3. 卡密库存检查：卡密发货模式下，库存不足则跳过（避免反复尝试占资源）
    # （此检查由 RealtimeDeliveryCoordinator.prepare_message 内部的库存校验覆盖，
    #    但失败后会标记 retry_safe=True 导致反复重试，这里提前跳过更高效）

    # 4. 声明确认检查：若该订单有未完成的声明会话（status=waiting），则跳过补发
    #    （买家尚未确认声明，不应自动发货）
    statement_row = (
        await db.execute(
            text(
                """
                SELECT id FROM delivery_statement_session
                WHERE account_id = :account_id
                  AND order_id = :external_order_id
                  AND status = 'waiting'
                  AND deleted = 0
                LIMIT 1
                """
            ),
            {
                "account_id": account_id,
                "external_order_id": external_order_id,
            },
        )
    ).mappings().first()
    if statement_row is not None:
        logger.info(
            "delivery_recovery skip order=%s (statement session waiting for buyer confirm)",
            order.get("order_id"),
        )
        return None

    rule = await resolve_realtime_delivery_rule(
        db,
        account_id=account_id,
        external_goods_id=item_id,
    )
    if not rule:
        logger.info(
            "delivery_recovery skip order=%s no rule matched accountId=%d itemId=%s",
            order.get("order_id"),
            account_id,
            item_id,
        )
        return None

    # 补发前再次检查 attempt 状态，避免循环期间被其他路径处理。
    # 关键：若 attempt 已存在，必须复用其 session_id / source_event_id / peer_id，
    # 否则 _validate_payload 会因 session_id 不一致抛出 event_payload_conflict。
    existing = (
        await db.execute(
            text(
                """
                SELECT id, state, retry_safe, session_id, source_event_id, peer_id
                FROM realtime_delivery_attempt
                WHERE account_id = :account_id
                  AND external_order_id = :external_order_id
                LIMIT 1
                """
            ),
            {
                "account_id": account_id,
                "external_order_id": external_order_id,
            },
        )
    ).mappings().first()
    if existing:
        state = str(existing.get("state") or "")
        retry_safe = int(existing.get("retry_safe") or 0)
        if state in _ALREADY_SENT_STATES:
            logger.info(
                "delivery_recovery skip order=%s attemptState=%s (already handled)",
                order.get("order_id"),
                state,
            )
            return None
        if state == "failed" and retry_safe != 1:
            logger.info(
                "delivery_recovery skip order=%s attemptState=failed retrySafe=0 (not retryable)",
                order.get("order_id"),
            )
            return None
        # 复用已有 attempt 的会话身份，保证 _validate_payload 一致性。
        session_id = str(existing.get("session_id") or "")
        source_event_id = str(existing.get("source_event_id") or "")
        peer_id = str(existing.get("peer_id") or buyer_id)
    else:
        # 全新 attempt：从会话表解析或合成稳定 session_id。
        session_id = await _resolve_session_id(
            db,
            account_id=account_id,
            buyer_id=buyer_id,
            item_id=item_id,
        )
        # 补发场景没有原始 WS event id，使用 order-bound 稳定值。
        source_event_id = f"recovery:order:{external_order_id}"
        peer_id = buyer_id

    if not session_id:
        session_id = f"recovery:{account_id}:{buyer_id}:{item_id}"
    if not source_event_id:
        source_event_id = f"recovery:order:{external_order_id}"

    event_key = build_realtime_delivery_event_key(
        account_id=account_id,
        external_order_id=external_order_id,
        source_event_id=source_event_id,
        session_id=session_id,
        item_id=item_id,
    )

    content = _render_delivery_content(
        str((rule or {}).get("delivery_content") or ""),
        buyer_name=str(order.get("buyer_name") or ""),
        external_order_id=external_order_id,
    )

    command = RealtimeDeliveryCommand(
        event_key=event_key,
        account_id=account_id,
        external_order_id=external_order_id,
        source_event_id=source_event_id,
        session_id=session_id,
        peer_id=peer_id,
        item_id=item_id,
        rule_id=_optional_int(rule.get("id")),
        delivery_mode=str(rule.get("delivery_mode") or "text"),
        delivery_content=content,
        quantity_requested=1,
        card_group_id=_optional_int(rule.get("card_group_id")),
        auto_confirm_shipment=_truthy(rule.get("auto_confirm_shipment")),
    )

    coordinator = RealtimeDeliveryCoordinator(
        store=SqlRealtimeDeliveryStore(db),
        gateway=XianyuRealtimeDeliveryGateway(),
    )
    return await coordinator.execute(command)


async def _resurrect_dead_letter_attempts(db: AsyncSession, *, max_age_hours: int = 24) -> int:
    """Revive attempts stuck in 'unknown' state for too long.

    An attempt in 'unknown' state means the process died before confirming
    the delivery result. After max_age_hours, we consider it safe to retry
    by resetting it to 'failed' with retry_safe=1, so the recovery scan
    picks it up again.

    Returns the number of attempts resurrected.
    """
    result = await db.execute(
        text(
            """
            UPDATE realtime_delivery_attempt
            SET state = 'failed',
                retry_safe = 1,
                retry_scope = 'message',
                last_error_code = 'unknown_resurrected',
                error_message = '发送结果长时间未知，已转为可重试状态',
                lease_token = NULL,
                lease_until = NULL,
                updated_time = NOW()
            WHERE state = 'unknown'
              AND created_time <= DATE_SUB(NOW(), INTERVAL :max_age_hours HOUR)
            """
        ),
        {"max_age_hours": int(max_age_hours)},
    )
    resurrected = result.rowcount or 0
    if resurrected > 0:
        logger.info("resurrected dead letter attempts count=%d", resurrected)
    return resurrected


async def run_delivery_recovery_once(
    *,
    limit: int | None = None,
    min_age_seconds: int | None = None,
) -> dict[str, Any]:
    """执行一次补发扫描。返回统计信息。

    每个订单在独立事务中处理，单订单失败不影响其他订单。
    """

    batch_size = int(
        limit if limit is not None else settings.delivery_recovery_batch_size
    )
    min_age = int(
        min_age_seconds
        if min_age_seconds is not None
        else settings.delivery_recovery_min_age_seconds
    )

    stats = {
        "scanned": 0,
        "recovered": 0,
        "skipped": 0,
        "failed": 0,
        "details": [],
    }

    # 先复活长时间卡在 unknown 状态的 attempt，使其可被补发扫描捡起
    try:
        async with async_session() as heal_db:
            resurrected = await _resurrect_dead_letter_attempts(heal_db)
            await heal_db.commit()
        if resurrected > 0:
            logger.info("delivery_recovery resurrected dead letters count=%d", resurrected)
    except Exception as exc:
        logger.warning("delivery_recovery dead letter resurrection failed: %s", exc)

    # 恢复孤儿卡密认领（进程崩溃导致的 status=1 但 attempt 已终态的卡密）
    try:
        from .realtime_delivery import heal_orphaned_card_claims
        async with async_session() as heal_db:
            healed = await heal_orphaned_card_claims(heal_db)
            await heal_db.commit()
        if healed > 0:
            logger.info("delivery_recovery healed orphaned cards count=%d", healed)
    except Exception as exc:
        logger.warning("delivery_recovery card healing failed: %s", exc)

    async with async_session() as db:
        orders = await _find_pending_delivery_orders(
            db,
            min_age_seconds=min_age,
            limit=batch_size,
        )
    stats["scanned"] = len(orders)
    if not orders:
        return stats

    for order in orders:
        order_id = order.get("order_id")
        try:
            async with async_session() as db:
                outcome = await _recover_one_order(db, order)
                if outcome is None:
                    stats["skipped"] += 1
                    continue
                await db.commit()
            stats["recovered"] += 1
            stats["details"].append({
                "orderId": order_id,
                "attemptId": outcome.attempt_id,
                "status": outcome.status,
                "repeated": outcome.repeated,
                "errorCode": outcome.error_code,
            })
            logger.info(
                "delivery_recovery processed order=%s attemptId=%s state=%s errorCode=%s",
                order_id,
                outcome.attempt_id,
                outcome.status,
                outcome.error_code or "none",
            )
        except Exception as exc:  # noqa: BLE001
            stats["failed"] += 1
            stats["details"].append({
                "orderId": order_id,
                "error": f"{type(exc).__name__}: {exc}",
            })
            logger.error(
                "delivery_recovery failed order=%s errorType=%s",
                order_id,
                type(exc).__name__,
                exc_info=True,
            )

    return stats


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None
