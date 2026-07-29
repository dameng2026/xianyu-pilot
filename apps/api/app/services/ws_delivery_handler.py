"""Event-driven, duplicate-resistant real-time automatic delivery."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.database import async_session
from .realtime_delivery import (
    RealtimeDeliveryCommand,
    RealtimeDeliveryCoordinator,
    RealtimeDeliveryOutcome,
    SqlRealtimeDeliveryStore,
    XianyuRealtimeDeliveryGateway,
    build_realtime_delivery_event_key,
)
from .ws_client import ws_manager


logger = logging.getLogger(__name__)

MODE_TEXT = "text"
MODE_CARD = "card"
MODE_KAMI = "kami"
MODE_CUSTOM = "custom"
MODE_API = "api"
DELIVERY_TIMING_AFTER_PAYMENT = "after_payment"
_SAFE_LOG_CODE_RE = re.compile(r"[^a-z0-9_]+")
_MAX_REALTIME_QUANTITY = 100

# 付款触发节流：防止闲鱼 WS 周期性推送付款消息导致重复触发发货（事故级 Bug 修复）
# key: f"{account_id}:{source_event_id}"，value: 上次触发时间戳
_payment_trigger_throttle: dict[str, float] = {}
_PAYMENT_THROTTLE_SECONDS = 60  # 同一 account_id + pnm_id 在 60 秒内只触发一次

# 付款事件正向关键词：reminder 文本命中任一即视为已付款
_PAYMENT_POSITIVE_KEYWORDS = (
    "等待你发货",
    "已付款",
    "买家已付款",
    "买家付款成功",
    "付款成功",
    "支付成功",
    "已支付",
    "买家已支付",
    "等待发货",
    "待发货",
    "买家已拍下并付款",
)
# 付款事件负向关键词：命中任一即视为未付款（优先级高于正向）
_PAYMENT_NEGATIVE_KEYWORDS = (
    "待付款",
    "拍下待付",
    "待买家付款",
    "等待买家付款",
    "未付款",
    "尚未付款",
)


def extract_order_id_from_url(url: str) -> Optional[str]:
    """Extract a non-empty order identifier from a reminder URL."""

    if not url:
        return None
    query = parse_qs(urlparse(url).query or "")
    for key in ("orderId", "tradeId", "id"):
        for value in query.get(key) or []:
            normalized = str(value).strip()
            if normalized:
                return normalized
    return None


def extract_goods_id_from_url(url: str) -> Optional[str]:
    """Extract a non-empty marketplace goods identifier from a reminder URL."""

    if not url:
        return None
    query = parse_qs(urlparse(url).query or "")
    for key in ("itemId", "id"):
        for value in query.get(key) or []:
            normalized = str(value).strip()
            if normalized:
                return normalized
    return None


def extract_peer_user_id_from_url(url: str) -> Optional[str]:
    """Extract the chat peer identifier without logging or persisting the URL."""

    if not url:
        return None
    query = parse_qs(urlparse(url).query or "")
    for key in ("peerUserId", "buyerUserId", "toId"):
        for value in query.get(key) or []:
            normalized = str(value).strip()
            if normalized:
                return normalized
    return None


def extract_buy_quantity_from_msg(msg: dict) -> int:
    complete_msg = msg.get("complete_msg") or msg.get("completeMsg") or {}
    if isinstance(complete_msg, str):
        try:
            complete_msg = json.loads(complete_msg)
        except (json.JSONDecodeError, TypeError):
            complete_msg = {}
    if isinstance(complete_msg, dict):
        quantity = complete_msg.get("quantity") or complete_msg.get("buyQuantity")
        try:
            if quantity is not None:
                return max(int(quantity), 1)
        except (TypeError, ValueError):
            pass

    reminder = str(msg.get("reminderContent") or msg.get("reminder_content") or "")
    match = re.search(r"[x×](\d+)", reminder)
    if match:
        try:
            return max(int(match.group(1)), 1)
        except (TypeError, ValueError):
            pass
    return 1


def is_payment_message(msg: dict) -> bool:
    """仅在买家真正付款后才返回 True，避免"拍下待付款"等未付款状态触发发货。

    闲鱼订单系统通知（contentType=26）覆盖"拍下待付款""已付款""确认收货"等
    多种状态。把所有 contentType=26 一律视为付款，会导致买家拍下未付款就发货，
    并在真正付款后再发一次。这里显式排除"待付款"，并要求提醒文本明确表示已付款。
    """
    content_type = msg.get("contentType") or msg.get("content_type") or 0
    try:
        content_type_int = int(content_type)
    except (TypeError, ValueError):
        content_type_int = 0
    reminder = str(msg.get("reminderContent") or msg.get("reminder_content") or "")
    reminder_preview = reminder[:120]

    # 负向关键词优先：明确表示未付款，必须跳过
    for neg in _PAYMENT_NEGATIVE_KEYWORDS:
        if neg in reminder:
            logger.info(
                "is_payment_message denied by negative keyword='%s' contentType=%d reminder=%s",
                neg, content_type_int, reminder_preview,
            )
            return False

    # 正向关键词：明确表示已付款或等待发货
    for pos in _PAYMENT_POSITIVE_KEYWORDS:
        if pos in reminder:
            logger.info(
                "is_payment_message accepted by positive keyword='%s' contentType=%d",
                pos, content_type_int,
            )
            return True

    logger.debug(
        "is_payment_message denied no keyword matched contentType=%d reminder=%s",
        content_type_int, reminder_preview,
    )
    return False


def is_bargain_success_message(msg: dict) -> bool:
    reminder = str(msg.get("reminderContent") or msg.get("reminder_content") or "")
    return "小刀成功" in reminder or "我已成功小刀" in reminder


def is_bargain_waiting_message(msg: dict) -> bool:
    reminder = str(msg.get("reminderContent") or msg.get("reminder_content") or "")
    return "小刀" in reminder and "待刀成" in reminder


async def _handle_bargain_waiting_message(
    account_id: int,
    msg: dict,
) -> None:
    """处理"我已小刀，待刀成"消息：标记小刀订单 + 调用免拼接口。

    买家发起小刀后，系统自动调用免拼接口（mtop.idle.groupon.activity.seller.freeshipping）
    促成小刀成交。此步骤不发送发货信息，完整发货由后续"小刀成功"消息触发。
    """
    s_id = str(msg.get("sId") or "")
    reminder_url = str(msg.get("reminderUrl") or msg.get("reminder_url") or "")

    order_id = extract_order_id_from_url(reminder_url)
    xy_goods_id = str(msg.get("xyGoodsId") or "")
    if not xy_goods_id:
        xy_goods_id = extract_goods_id_from_url(reminder_url) or ""
    buyer_user_id = str(msg.get("senderUserId") or "")
    if not buyer_user_id:
        buyer_user_id = extract_peer_user_id_from_url(reminder_url) or ""

    logger.info(
        "检测到待刀成消息: accountId=%d orderId=%s xyGoodsId=%s buyer=%s sId=%s",
        account_id, order_id, xy_goods_id, buyer_user_id, s_id,
    )

    if not xy_goods_id or not buyer_user_id:
        logger.warning(
            "待刀成消息缺少商品ID或买家ID，跳过免拼: accountId=%d xyGoodsId=%s buyer=%s",
            account_id, xy_goods_id, buyer_user_id,
        )
        return

    async with async_session() as db:
        try:
            # 1. 标记订单为小刀订单
            if order_id:
                await _mark_order_as_bargain(db, account_id, order_id, xy_goods_id)

            # 2. 商品归属检查：若商品不属于本账号或未配置发货规则，则跳过免拼
            rule = await _match_delivery_rule(db, account_id, xy_goods_id)
            if not rule:
                logger.info(
                    "待刀成消息：商品不属于本账号或未配置发货规则，跳过免拼: accountId=%d xyGoodsId=%s",
                    account_id, xy_goods_id,
                )
                await db.commit()
                return

            # 3. 调用免拼接口（freeshipping_order 已支持幂等）
            from .xianyu_api_service import freeshipping_order
            result = await asyncio.to_thread(
                freeshipping_order,
                account_id, order_id or "", xy_goods_id, buyer_user_id,
            )

            if result and result.get("success"):
                logger.info(
                    "待刀成消息：免拼接口调用成功: accountId=%d orderId=%s itemId=%s buyerId=%s",
                    account_id, order_id, xy_goods_id, buyer_user_id,
                )
            else:
                logger.warning(
                    "待刀成消息：免拼接口调用失败: accountId=%d orderId=%s error=%s",
                    account_id, order_id,
                    result.get("error", "") if result else "NONE",
                )

            await db.commit()
        except Exception as e:
            await db.rollback()
            logger.error(
                "待刀成消息处理失败: accountId=%d error=%s",
                account_id, e, exc_info=True,
            )


async def _mark_order_as_bargain(
    db: AsyncSession,
    account_id: int,
    order_id: str,
    xy_goods_id: str,
) -> None:
    """标记订单为小刀订单（is_bargain=1）。

    优先按 external_order_id 精确匹配；若未命中（订单可能尚未同步），
    按 account_id + external_goods_id 匹配最近一笔订单兜底。
    """
    # 1. 按 external_order_id 精确匹配
    result = await db.execute(
        text("""
            UPDATE xianyu_trade_order
            SET is_bargain = 1, updated_time = NOW()
            WHERE account_id = :account_id
              AND external_order_id = :external_order_id
              AND deleted = 0
        """),
        {
            "account_id": account_id,
            "external_order_id": order_id,
        },
    )

    # 2. 若未命中，按 xy_goods_id 兜底
    if result.rowcount == 0 and xy_goods_id:
        await db.execute(
            text("""
                UPDATE xianyu_trade_order
                SET is_bargain = 1, updated_time = NOW()
                WHERE account_id = :account_id
                  AND external_goods_id = :xy_goods_id
                  AND deleted = 0
                  AND id = (
                      SELECT id FROM (
                          SELECT id FROM xianyu_trade_order
                          WHERE account_id = :account_id
                            AND external_goods_id = :xy_goods_id
                            AND deleted = 0
                          ORDER BY created_time DESC
                          LIMIT 1
                      ) AS t
                  )
            """),
            {
                "account_id": account_id,
                "xy_goods_id": xy_goods_id,
            },
        )


async def handle_incoming_message_for_delivery(
    account_id: int,
    msg: dict,
) -> None:
    """Process one stored WebSocket event through the durable state machine."""

    # "待刀成"消息：标记小刀订单 + 调免拼接口（促成小刀成交），不触发完整发货
    if is_bargain_waiting_message(msg):
        await _handle_bargain_waiting_message(account_id, msg)
        return

    payment_event = is_payment_message(msg)
    bargain_event = is_bargain_success_message(msg)
    if not payment_event and not bargain_event:
        # 记录跳过原因，便于排查"收到付款通知但未触发发货"
        reminder = str(msg.get("reminderContent") or msg.get("reminder_content") or "")
        content_type = msg.get("contentType") or msg.get("content_type") or 0
        logger.debug(
            "delivery event skipped accountId=%d payment=%s bargain=%s contentType=%s reminder=%s",
            account_id, payment_event, bargain_event, content_type, reminder[:80],
        )
        return

    # 付款兜底节流：同一 account_id + pnm_id 在 60 秒内只触发一次，防止 WS 周期性推送死循环
    if payment_event and _should_skip_payment_trigger(account_id, msg):
        logger.debug(
            "付款触发节流跳过 accountId=%d pnmId=%s（60秒内已触发）",
            account_id,
            str(msg.get("pnmId") or msg.get("pnm_id") or "")[:32],
        )
        return

    logger.info(
        "Realtime delivery event accepted accountId=%d payment=%s bargain=%s",
        account_id, payment_event, bargain_event,
    )
    async with async_session() as db:
        try:
            outcome = await _process_delivery(db, account_id, msg)
            if outcome is not None and payment_event and not outcome.repeated:
                try:
                    from .notify_dispatcher import notify_new_order

                    await notify_new_order(account_id, msg)
                except Exception as exc:
                    logger.warning(
                        "New-order notification failed accountId=%d errorType=%s",
                        account_id,
                        type(exc).__name__,
                    )
            await db.commit()
        except Exception as exc:
            await db.rollback()
            error_code = _safe_log_code(getattr(exc, "error_code", "delivery_handler_failed"))
            logger.error(
                "Realtime delivery stopped accountId=%d errorCode=%s errorType=%s",
                account_id,
                error_code,
                type(exc).__name__,
            )


async def _process_delivery(
    db: AsyncSession,
    account_id: int,
    msg: dict,
) -> RealtimeDeliveryOutcome | None:
    session_id = str(msg.get("sId") or msg.get("sid") or "").strip()
    reminder_url = str(msg.get("reminderUrl") or msg.get("reminder_url") or "")
    external_order_id = extract_order_id_from_url(reminder_url)
    item_id = str(msg.get("xyGoodsId") or "").strip()
    if not item_id:
        item_id = extract_goods_id_from_url(reminder_url) or ""
    peer_id = str(msg.get("senderUserId") or "").strip()
    if not peer_id:
        peer_id = extract_peer_user_id_from_url(reminder_url) or ""
    source_event_id = _extract_source_event_id(msg)
    if not source_event_id and external_order_id:
        source_event_id = f"order:{external_order_id}"

    missing = [
        name
        for name, value in (
            ("session", session_id),
            ("item", item_id),
            ("peer", peer_id),
            ("sourceEvent", source_event_id),
        )
        if not value
    ]
    if missing:
        logger.warning(
            "Realtime delivery denied accountId=%d missingContext=%s reminderUrl=%s itemId=%s peerId=%s",
            account_id,
            ",".join(missing),
            reminder_url[:120],
            item_id,
            peer_id,
        )
        return None

    quantity = extract_buy_quantity_from_msg(msg)
    if quantity > _MAX_REALTIME_QUANTITY:
        logger.warning(
            "Realtime delivery denied accountId=%d errorCode=quantity_limit_exceeded quantity=%d",
            account_id,
            quantity,
        )
        return None

    event_key = build_realtime_delivery_event_key(
        account_id=account_id,
        external_order_id=external_order_id,
        source_event_id=source_event_id,
        session_id=session_id,
        item_id=item_id,
    )

    # 数据库层去重第一道防线：查询 delivery_record 表（包括失败记录 1 小时窗口）
    # 防止 event_key 不同但实际是同一发货场景的重复触发（事故级 Bug 修复）
    if await _has_existing_realtime_delivery(db, account_id, session_id, item_id, peer_id):
        logger.info(
            "数据库层去重命中，跳过发货 accountId=%d orderId=%s itemId=%s sid=%s",
            account_id, external_order_id, item_id, session_id[:32],
        )
        return None

    # 数据库层去重第二道防线：xianyu_trade_order.order_status=3（已发货）
    if external_order_id and await _check_order_already_shipped(db, account_id, external_order_id):
        logger.info(
            "订单已发货（order_status=3），跳过发货 accountId=%d orderId=%s",
            account_id, external_order_id,
        )
        return None

    rule = await resolve_realtime_delivery_rule(
        db,
        account_id=account_id,
        external_goods_id=item_id,
    )
    if not rule:
        logger.warning(
            "Realtime delivery no rule matched accountId=%d itemId=%s orderId=%s",
            account_id, item_id, external_order_id,
        )
    else:
        logger.info(
            "Realtime delivery rule matched accountId=%d itemId=%s ruleId=%s mode=%s",
            account_id, item_id, rule.get("id"), rule.get("delivery_mode"),
        )
    mode = _normalize_delivery_mode(rule.get("delivery_mode") if rule else "unconfigured")
    content = str((rule or {}).get("delivery_content") or "")
    content = _render_delivery_content(
        content,
        buyer_name=str(msg.get("senderUserName") or ""),
        external_order_id=external_order_id,
    )
    # 发货声明流程：若声明开关开启，发送声明并创建 waiting 会话，不立即发货
    try:
        from .ws_statement_handler import should_send_statement
        if await should_send_statement(db, account_id):
            from .ws_statement_handler import send_statement_and_create_session
            statement_sent = await send_statement_and_create_session(
                db,
                account_id=account_id,
                msg=msg,
                order_id=external_order_id,
                xy_goods_id=item_id,
                s_id=session_id,
                pnm_id=source_event_id,
                buyer_user_id=peer_id,
                buyer_user_name=str(msg.get("senderUserName") or ""),
                goods_title=str((rule or {}).get("source_title") or ""),
            )
            if statement_sent:
                logger.info(
                    "声明已发送，暂停实时发货 accountId=%d orderId=%s",
                    account_id, external_order_id,
                )
                return None
    except Exception as exc:
        logger.warning(
            "声明流程异常，回退到直接发货 accountId=%d errorType=%s",
            account_id, type(exc).__name__,
        )
    command = RealtimeDeliveryCommand(
        event_key=event_key,
        account_id=account_id,
        external_order_id=external_order_id,
        source_event_id=source_event_id,
        session_id=session_id,
        peer_id=peer_id,
        item_id=item_id,
        rule_id=_optional_int((rule or {}).get("id")),
        delivery_mode=mode,
        delivery_content=content,
        quantity_requested=quantity,
        card_group_id=_optional_int((rule or {}).get("card_group_id")),
        auto_confirm_shipment=_truthy((rule or {}).get("auto_confirm_shipment")),
    )
    coordinator = RealtimeDeliveryCoordinator(
        store=SqlRealtimeDeliveryStore(db),
        gateway=XianyuRealtimeDeliveryGateway(),
    )
    outcome = await coordinator.execute(command)
    logger.info(
        "Realtime delivery state accountId=%d attemptId=%d state=%s errorCode=%s",
        account_id,
        outcome.attempt_id,
        outcome.status,
        _safe_log_code(outcome.error_code or "none"),
    )
    if outcome.status in {"failed", "unknown", "message_sent"}:
        await _notify_realtime_delivery_attention(db, account_id, outcome)
    return outcome


async def resolve_realtime_delivery_rule(
    db: AsyncSession,
    *,
    account_id: int,
    external_goods_id: str,
) -> Optional[dict]:
    """Resolve a delivery rule without ever crossing the account seam."""

    return await _match_delivery_rule(db, account_id, external_goods_id)


async def _match_delivery_rule(
    db: AsyncSession,
    account_id: int,
    external_goods_id: str,
) -> Optional[dict]:
    goods = await _find_goods_for_delivery(db, account_id, external_goods_id)
    if goods:
        goods_rule = await _load_goods_delivery_rule(db, goods)
        if goods_rule:
            return goods_rule

    local_goods_id = int(goods["id"]) if goods and goods.get("id") is not None else 0
    rows = (
        await db.execute(
            text(
                """
                SELECT id, account_id, goods_id, rule_name, delivery_mode,
                       card_group_id, delivery_content, status
                FROM delivery_rule
                WHERE deleted = 0
                  AND status = 1
                  AND (account_id IS NULL OR account_id = :account_id)
                  AND (goods_id IS NULL OR goods_id = 0 OR goods_id = :goods_id)
                ORDER BY
                  CASE
                    WHEN account_id = :account_id AND goods_id = :goods_id THEN 0
                    WHEN account_id = :account_id AND (goods_id IS NULL OR goods_id = 0) THEN 1
                    WHEN account_id IS NULL AND goods_id = :goods_id THEN 2
                    ELSE 3
                  END,
                  id DESC
                LIMIT 1
                """
            ),
            {"account_id": account_id, "goods_id": local_goods_id},
        )
    ).mappings().all()
    if not rows:
        return None
    rule = dict(rows[0])
    rule["delivery_mode"] = _normalize_delivery_mode(rule.get("delivery_mode"))
    # 旧版 delivery_rule 表无 auto_confirm_shipment 字段，无法自动确认发货；
    # 自动确认发货仅在 delivery_goods_config（新版配置）路径下生效。
    rule["auto_confirm_shipment"] = 0
    return rule


async def _find_goods_for_delivery(
    db: AsyncSession,
    account_id: int,
    external_goods_id: str,
) -> Optional[dict]:
    if not external_goods_id:
        return None
    row = (
        await db.execute(
            text(
                """
                SELECT id, account_id, external_goods_id, title
                FROM xianyu_goods
                WHERE deleted = 0
                  AND account_id = :account_id
                  AND external_goods_id = :external_goods_id
                ORDER BY id DESC
                LIMIT 1
                """
            ),
            {
                "account_id": account_id,
                "external_goods_id": external_goods_id,
            },
        )
    ).mappings().first()

    if not row:
        # 商品不存在时，尝试从订单项表补全最小商品记录，确保发货配置可命中。
        # 场景：WS 实时收到付款消息，但商品尚未同步到 xianyu_goods 表。
        await _ensure_goods_from_order_items(db, account_id, external_goods_id)
        row = (
            await db.execute(
                text(
                    """
                    SELECT id, account_id, external_goods_id, title
                    FROM xianyu_goods
                    WHERE deleted = 0
                      AND account_id = :account_id
                      AND external_goods_id = :external_goods_id
                    ORDER BY id DESC
                    LIMIT 1
                    """
                ),
                {
                    "account_id": account_id,
                    "external_goods_id": external_goods_id,
                },
            )
        ).mappings().first()

    return dict(row) if row else None


async def _ensure_goods_from_order_items(
    db: AsyncSession,
    account_id: int,
    external_goods_id: str,
) -> bool:
    """实时发货时发现商品不存在，从订单项表补全最小商品记录。

    确保实时路径和批量路径都能在商品未同步时补全占位记录。
    """
    if not external_goods_id:
        return False

    # 从订单项表获取商品信息
    item_row = (
        await db.execute(
            text("""
                SELECT oi.goods_title, oi.goods_image, oi.goods_price
                FROM xianyu_trade_order_item oi
                JOIN xianyu_trade_order o ON o.id = oi.order_id
                WHERE oi.deleted = 0
                  AND oi.goods_id = :goods_id
                  AND o.account_id = :account_id
                ORDER BY oi.id DESC LIMIT 1
            """),
            {
                "account_id": account_id,
                "goods_id": int(external_goods_id) if external_goods_id.isdigit() else 0,
            },
        )
    ).mappings().first()

    title = ""
    image_url = ""
    price = "0"
    if item_row:
        title = str(item_row.get("goods_title") or "")
        image_url = str(item_row.get("goods_image") or "")
        price = str(item_row.get("goods_price") or "0")

    # 防御性查重：INSERT 前确认该商品在任意 deleted 状态下都不存在
    existing_row = (
        await db.execute(
            text("""
                SELECT id FROM xianyu_goods
                WHERE account_id = :account_id
                  AND external_goods_id = :external_goods_id
                LIMIT 1
            """),
            {
                "account_id": account_id,
                "external_goods_id": external_goods_id,
            },
        )
    ).mappings().first()
    if existing_row:
        return False

    try:
        await db.execute(
            text("""
                INSERT INTO xianyu_goods (
                    account_id, external_goods_id, goods_id, title,
                    price, sold_price, cover_pic, image_url, status,
                    deleted, created_time, updated_time
                ) VALUES (
                    :account_id, :external_goods_id, :goods_id, :title,
                    :price, :price, :image_url, :image_url, 1,
                    0, NOW(), NOW()
                )
            """),
            {
                "account_id": account_id,
                "external_goods_id": external_goods_id,
                "goods_id": external_goods_id,
                "title": (title or f"商品 {external_goods_id}")[:255],
                "price": (price or "0")[:32],
                "image_url": image_url or None,
            },
        )
        logger.info(
            "实时发货自动补全商品占位记录 accountId=%d externalGoodsId=%s",
            account_id, external_goods_id
        )
        return True
    except Exception:
        return False


async def _load_goods_delivery_rule(
    db: AsyncSession,
    goods: dict,
) -> Optional[dict]:
    row = (
        await db.execute(
            text(
                """
                SELECT id, goods_id, config_json
                FROM delivery_goods_config
                WHERE goods_id = :goods_id
                  AND deleted = 0
                LIMIT 1
                """
            ),
            {"goods_id": goods.get("id")},
        )
    ).mappings().first()
    if not row:
        return None

    config = _json_object(row.get("config_json"))
    timing_config = config.get("payDelivery")
    if not isinstance(timing_config, dict) or not _truthy(timing_config.get("enabled")):
        return None

    mode = _normalize_delivery_mode(timing_config.get("mode"))
    header = str(timing_config.get("header") or "")
    footer = str(timing_config.get("footer") or "")
    source_id = timing_config.get("sourceId")
    source_title = str(timing_config.get("sourceTitle") or "")
    if mode == MODE_TEXT:
        content = str(timing_config.get("content") or "")
        if source_id:
            source = await _load_text_source(db, source_id)
            if source:
                content = content or str(source.get("content") or "")
                source_title = source_title or str(source.get("title") or "")
        delivery_content = _build_delivery_content(header, content, footer)
        if not delivery_content.strip():
            return None
    elif mode == MODE_CARD:
        card_template = str(timing_config.get("cardTemplate") or "{卡密}")
        delivery_content = _build_delivery_content(header, card_template, footer)
    else:
        # Persist the retired mode as failed; never read or call its URL fields.
        delivery_content = ""

    return {
        "id": row.get("id"),
        "goods_id": goods.get("id"),
        "delivery_mode": mode,
        "delivery_content": delivery_content,
        "delivery_timing": DELIVERY_TIMING_AFTER_PAYMENT,
        "source_id": source_id,
        "source_title": source_title,
        "card_group_id": timing_config.get("cardGroupId"),
        "auto_confirm_shipment": timing_config.get("autoConfirmShipment")
        or timing_config.get("auto_confirm_shipment")
        or 0,
    }


async def _load_text_source(db: AsyncSession, source_id: Any) -> Optional[dict]:
    row = (
        await db.execute(
            text(
                """
                SELECT id, title, content
                FROM delivery_text_source
                WHERE id = :source_id
                  AND deleted = 0
                LIMIT 1
                """
            ),
            {"source_id": source_id},
        )
    ).mappings().first()
    return dict(row) if row else None


async def _ensure_ws_connected(account_id: int, timeout: float = 12.0) -> bool:
    """确保账号的 WebSocket 已连接，未连接时自动从 DB 重启并等待注册完成。

    用于手动发货等需要即时发送消息的场景，避免因 WS 临时断开导致发货直接失败。
    """
    client = ws_manager.get_client(account_id)
    if client and client.is_connected:
        return True

    logger.info("手动发货前 WS 未连接，尝试自动重启 accountId=%d", account_id)
    try:
        restarted = await ws_manager.restart_account(account_id)
    except Exception:
        logger.error("手动发货前 WS 重启异常 accountId=%d", account_id)
        return False
    if not restarted:
        logger.warning("手动发货前 WS 重启失败（账号不存在或凭据缺失）accountId=%d", account_id)
        return False

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        await asyncio.sleep(0.5)
        client = ws_manager.get_client(account_id)
        if client and client.is_connected:
            logger.info("手动发货前 WS 自动重连成功 accountId=%d", account_id)
            return True
    logger.warning("手动发货前 WS 自动重连超时 accountId=%d", account_id)
    return False


async def send_delivery_message_result(
    account_id: int,
    s_id: str,
    buyer_user_id: str,
    content: str,
) -> dict:
    """Send once while preserving confirmed/failed/unknown ACK semantics."""

    client = ws_manager.get_client(account_id)
    if not client or not client.is_connected:
        if not await _ensure_ws_connected(account_id):
            return {
                "status": "failed",
                "errorCode": "websocket_unavailable",
                "message": "账号消息连接不可用，请等待重连后重试",
                "retrySafe": True,
            }
        client = ws_manager.get_client(account_id)
    if not client._sid:
        return {
            "status": "failed",
            "errorCode": "websocket_not_registered",
            "message": "账号消息连接尚未完成注册，请稍后重试",
            "retrySafe": True,
        }

    cid = s_id if s_id.endswith("@goofish") else f"{s_id}@goofish"
    to_id = (
        buyer_user_id
        if buyer_user_id.endswith("@goofish")
        else f"{buyer_user_id}@goofish"
    )
    result = await client.send_text_message(cid=cid, to_id=to_id, text=content)
    if not isinstance(result, dict):
        return {
            "status": "unknown",
            "errorCode": "message_ack_unknown",
            "message": "发送结果未确认，请先在闲鱼 App 核对，避免重复发送",
            "retrySafe": False,
        }
    try:
        code = int(result.get("code", 500))
    except (TypeError, ValueError):
        code = 500
    if code == 200:
        return {"status": "confirmed", "retrySafe": False}
    if code == 503 and not result.get("mid") and not result.get("deliveryUnknown"):
        return {
            "status": "failed",
            "errorCode": "websocket_unavailable",
            "message": "账号消息连接不可用，请等待重连后重试",
            "retrySafe": True,
        }
    if result.get("deliveryUnknown") or (code >= 500 and not result.get("mid")):
        return {
            "status": "unknown",
            "errorCode": "message_ack_unknown",
            "message": "发送结果未确认，请先在闲鱼 App 核对，避免重复发送",
            "retrySafe": False,
        }
    return {
        "status": "failed",
        "errorCode": "message_rejected",
        "message": "平台明确拒绝买家消息，请检查会话与账号状态后重试",
        "retrySafe": True,
    }


async def _send_delivery_message(
    account_id: int,
    s_id: str,
    buyer_user_id: str,
    content: str,
) -> bool:
    result = await send_delivery_message_result(account_id, s_id, buyer_user_id, content)
    return result.get("status") == "confirmed"


async def _notify_realtime_delivery_attention(
    db: AsyncSession,
    account_id: int,
    outcome: RealtimeDeliveryOutcome,
) -> None:
    try:
        from .automation_runtime import insert_notification

        message = (
            "实时自动发货需要人工核对。"
            f"本地尝试编号：{outcome.attempt_id}；"
            f"状态：{outcome.status}；"
            f"错误代码：{_safe_log_code(outcome.error_code or 'review_required')}。"
            "请在闲鱼 App 核对消息与订单状态，未知结果不要重复发送。"
        )
        await insert_notification(
            db,
            None,
            "自动发货核对提醒",
            message,
            "自动发货核对提醒",
            "warn",
        )
    except Exception as exc:
        logger.warning(
            "Realtime delivery reminder failed accountId=%d attemptId=%d errorType=%s",
            account_id,
            outcome.attempt_id,
            type(exc).__name__,
        )


def _extract_source_event_id(msg: dict) -> str:
    for key in ("pnmId", "messageId", "msgId", "mid", "eventId"):
        value = str(msg.get(key) or "").strip()
        if value:
            return value
    complete = msg.get("complete_msg") or msg.get("completeMsg")
    if isinstance(complete, str):
        try:
            complete = json.loads(complete)
        except (json.JSONDecodeError, TypeError):
            complete = None
    if isinstance(complete, dict):
        for key in ("pnmId", "messageId", "msgId", "mid", "eventId"):
            value = str(complete.get(key) or "").strip()
            if value:
                return value
    return ""


def _render_delivery_content(
    content: str,
    *,
    buyer_name: str,
    external_order_id: str | None,
) -> str:
    return (
        str(content or "")
        .replace("{buyerUserName}", buyer_name or "买家")
        .replace("{orderId}", external_order_id or "")
        .replace("{goodsTitle}", "")
        .replace("{deliveryTime}", time.strftime("%Y-%m-%d %H:%M:%S"))
    )


def _build_delivery_content(header: str, content: str, footer: str) -> str:
    return "\n".join(
        part
        for part in (
            str(header or "").strip(),
            str(content or "").strip(),
            str(footer or "").strip(),
        )
        if part
    )


def _json_object(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return {}
    try:
        loaded = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _normalize_delivery_mode(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {MODE_CARD, MODE_KAMI}:
        return MODE_CARD
    if normalized == MODE_TEXT:
        return MODE_TEXT
    return normalized or "unconfigured"


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _safe_log_code(value: Any) -> str:
    normalized = _SAFE_LOG_CODE_RE.sub("_", str(value or "").lower()).strip("_")
    return (normalized or "unknown")[:64]


async def _trigger_delivery_for_confirmed_statement(
    db: AsyncSession,
    account_id: int,
    *,
    order_id: Optional[str],
    xy_goods_id: str,
    buyer_user_id: str,
    s_id: str,
    goods_title: str,
    session_id_stmt: int,
) -> None:
    """买家确认发货声明后，触发该订单的发货流程。

    复用 _process_delivery 的状态机逻辑，通过构造虚拟消息触发发货。
    安全保障由 RealtimeDeliveryCoordinator 的 event_key 幂等性保证：
    同一订单的 event_key 相同，不会重复发货。
    """
    logger.info(
        "声明确认后触发发货: accountId=%d orderId=%s xyGoodsId=%s stmtSessionId=%d",
        account_id, order_id, xy_goods_id, session_id_stmt,
    )

    # 构造虚拟消息复用 _process_delivery
    fake_msg = {
        "sId": s_id,
        "senderUserId": buyer_user_id,
        "senderUserName": "",
        "xyGoodsId": xy_goods_id,
        "reminderContent": "买家已确认发货声明",
        "reminderUrl": f"orderId={order_id}&itemId={xy_goods_id}&buyerUserId={buyer_user_id}" if order_id else "",
        "pnmId": f"statement-confirm:{session_id_stmt}",
    }
    await _process_delivery(db, account_id, fake_msg)


# ==================== 商业版同步：数据库层去重保护 ====================


async def _has_existing_realtime_delivery(
    db: AsyncSession,
    account_id: int,
    session_id: str,
    item_id: str,
    peer_id: str,
) -> bool:
    """数据库层去重：查询 delivery_record 表判断是否已有发货记录。

    双层时间窗口（事故级 Bug 修复）：
    - 成功记录（status IN 1,2）：10 分钟窗口内视为重复
    - 失败记录（status=3）：1 小时窗口内视为重复，避免 WS 周期性推送导致死循环

    Args:
        db: 数据库会话
        account_id: 闲鱼账号ID
        session_id: 会话ID（已归一化去 @goofish 后缀）
        item_id: 商品ID
        peer_id: 买家ID

    Returns:
        True 表示已有发货记录，应跳过本次发货
    """
    if not session_id or not item_id or not peer_id:
        return False

    # 归一化 sid：去掉 @goofish 后缀
    normalized_sid = str(session_id).removesuffix("@goofish")

    # 查询 delivery_record 表，按 receiver_info JSON 中的 sid/buyerUserId/xyGoodsId 匹配
    # 双层时间窗口：成功记录 10 分钟 / 失败记录 1 小时
    query = text("""
        SELECT COUNT(*) AS cnt
        FROM delivery_record
        WHERE account_id = :account_id
          AND deleted = 0
          AND delivery_timing = 'after_payment'
          AND (
            (status IN (1, 2) AND created_time >= DATE_SUB(NOW(), INTERVAL 10 MINUTE))
            OR
            (status = 3 AND created_time >= DATE_SUB(NOW(), INTERVAL 1 HOUR))
          )
          AND (
            JSON_EXTRACT(receiver_info, '$.sid') = :sid
            OR JSON_EXTRACT(receiver_info, '$.sId') = :sid
          )
          AND JSON_EXTRACT(receiver_info, '$.xyGoodsId') = :item_id
          AND (
            JSON_EXTRACT(receiver_info, '$.buyerUserId') = :peer_id
            OR JSON_EXTRACT(receiver_info, '$.buyerId') = :peer_id
          )
    """)

    try:
        result = await db.execute(query, {
            "account_id": account_id,
            "sid": normalized_sid,
            "item_id": str(item_id),
            "peer_id": str(peer_id),
        })
        count = result.scalar() or 0
        return count > 0
    except Exception as exc:
        logger.warning(
            "数据库层去重查询失败，降级为不跳过 accountId=%d errorType=%s",
            account_id,
            type(exc).__name__,
        )
        return False


async def _check_order_already_shipped(
    db: AsyncSession,
    account_id: int,
    external_order_id: str,
) -> bool:
    """第二道防线：检查 xianyu_trade_order.order_status 是否为 3（已发货）。

    即使 delivery_record 无记录，若订单已标记为已发货，也跳过本次发货。
    """
    if not external_order_id:
        return False
    query = text("""
        SELECT order_status
        FROM xianyu_trade_order
        WHERE account_id = :account_id
          AND external_order_id = :order_id
          AND deleted = 0
        LIMIT 1
    """)
    try:
        result = await db.execute(query, {
            "account_id": account_id,
            "order_id": str(external_order_id),
        })
        row = result.fetchone()
        if row and row[0] == 3:
            return True
        return False
    except Exception as exc:
        logger.warning(
            "订单已发货检查失败，降级为不跳过 accountId=%d orderId=%s errorType=%s",
            account_id, external_order_id, type(exc).__name__,
        )
        return False


def _should_skip_payment_trigger(account_id: int, msg: dict) -> bool:
    """付款兜底节流：同一 account_id + pnm_id 在 60 秒内只触发一次。

    防止闲鱼 WS 周期性推送付款消息（同一付款事件不同消息）导致重复触发发货。
    """
    pnm_id = str(msg.get("pnmId") or msg.get("pnm_id") or "").strip()
    if not pnm_id:
        # 没有 pnm_id 时无法节流，允许触发（由其他去重机制防护）
        return False

    throttle_key = f"{account_id}:{pnm_id}"
    now = time.time()
    last_trigger = _payment_trigger_throttle.get(throttle_key)
    if last_trigger and (now - last_trigger) < _PAYMENT_THROTTLE_SECONDS:
        return True

    # 记录本次触发时间
    _payment_trigger_throttle[throttle_key] = now

    # 清理过期条目（超过 5 分钟的），避免字典无限增长
    expired_keys = [
        k for k, v in _payment_trigger_throttle.items()
        if (now - v) > 300
    ]
    for k in expired_keys:
        _payment_trigger_throttle.pop(k, None)

    return False
