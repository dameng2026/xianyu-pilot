"""发货声明流程处理器

职责：
1. 收到付款消息后，若发货声明开关开启，发送声明文案并创建声明会话（status=waiting）
2. 收到买家回复"确认/取消"后，更新声明会话状态并触发后续动作
   - 确认 → 触发该订单发货（调用 ws_delivery_handler._trigger_delivery_for_confirmed_statement）
   - 取消 → 通知卖家 + 向买家回复取消提示

设计要点：
- 声明会话按订单粒度跟踪（account_id + order_id）
- 幂等：同一订单已有 waiting 会话时不重复创建
- 兜底：买家发"确认"但无匹配声明会话时，静默忽略+记日志，AI 自动回复照常
- 与 AI 自动回复解耦：本模块仅处理声明会话状态，不抑制 AI 自动回复

调用入口：
- ws_delivery_handler._process_delivery 收到付款消息后调用 should_send_statement / send_statement_and_create_session
- ws_startup.on_message_callback 收到买家消息后调用 handle_buyer_statement_reply
"""
import logging
import re
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.database import async_session

logger = logging.getLogger(__name__)

# ============================================================
# 固定声明文案（用户不可编辑，仅 {订单编号} 会被替换）
# ============================================================
STATEMENT_TEMPLATE = (
    "订单编号：{订单编号}\n\n"
    "您好，该订单包含的商品为虚拟商品，发货后不支持退换。"
    "如无异议，请回复【确认】。\n\n"
    "如有异议，请回复【取消】，这边帮您转人工客服，进行退款操作"
)

# 买家回复"取消"后，向买家发送的提示文案
BUYER_CANCEL_REPLY = "已为您转人工客服，请耐心等待，客服会尽快与您联系处理退款事宜。"

# 买家确认/取消关键词识别（精确匹配，去除标点空格后比对）
CONFIRM_KEYWORDS = {"确认", "确定", "好的", "好", "可以", "同意", "确认发货"}
CANCEL_KEYWORDS = {"取消", "不要了", "退款", "退货", "拒绝", "不同意", "取消发货"}


# ============================================================
# 声明开关查询
# ============================================================
async def is_statement_enabled(db: AsyncSession) -> bool:
    """查询发货声明是否开启（全局配置，无 tenant_id）"""
    try:
        row = (
            await db.execute(
                text("SELECT enabled FROM delivery_statement WHERE deleted=0 ORDER BY id DESC LIMIT 1"),
            )
        ).first()
        if row is None:
            return False
        return int(row[0] or 0) == 1
    except Exception as e:
        logger.warning("查询发货声明开关失败 error=%s", e)
        return False


async def should_send_statement(db: AsyncSession, account_id: int) -> bool:
    """判断是否需要发送发货声明（开关开启 + 该订单无未完成声明会话）。

    此函数仅做快速判断，实际的幂等创建在 send_statement_and_create_session 中。
    """
    return await is_statement_enabled(db)


# ============================================================
# 发送声明 + 创建会话
# ============================================================
async def send_statement_and_create_session(
    db: AsyncSession,
    *,
    account_id: int,
    msg: dict,
    order_id: Optional[str],
    xy_goods_id: str,
    s_id: str,
    pnm_id: str,
    buyer_user_id: str,
    buyer_user_name: str,
    goods_title: str = "",
) -> bool:
    """发送发货声明并创建声明会话。

    Returns:
        True=已发送声明并创建会话（调用方应停止发货流程）
        False=未发送声明（开关关闭/已有未完成会话/发送失败），调用方应继续原发货流程
    """
    if not order_id:
        # 无订单号无法跟踪声明会话，跳过声明流程
        return False

    # 幂等：同一订单已有 waiting 会话时不重复创建
    existing = (
        await db.execute(
            text(
                """
                SELECT id, status FROM delivery_statement_session
                WHERE account_id=:aid AND order_id=:oid
                  AND status IN ('declaring','waiting') AND deleted=0
                ORDER BY created_time DESC LIMIT 1
                """
            ),
            {"aid": account_id, "oid": order_id},
        )
    ).first()
    if existing is not None:
        logger.info(
            "声明会话已存在，跳过重复发送 accountId=%d orderId=%s sessionId=%s status=%s",
            account_id, order_id, existing[0], existing[1],
        )
        return True  # 已有未完成会话，停止发货流程

    # 变量替换
    content = STATEMENT_TEMPLATE.replace("{订单编号}", order_id)

    # 发送声明消息（复用 ws_delivery_handler 的发送逻辑）
    send_ok = await _send_statement_message(account_id, s_id, buyer_user_id, content)
    if not send_ok:
        logger.warning(
            "发送声明失败，跳过创建会话，按原流程发货 accountId=%d orderId=%s",
            account_id, order_id,
        )
        return False  # 发送失败，回退到原发货流程

    # 创建声明会话（status=waiting，已发送）
    await db.execute(
        text(
            """
            INSERT INTO delivery_statement_session(
                account_id, order_id, buyer_id, buyer_nick,
                xy_goods_id, goods_title, s_id, pnm_id,
                statement_content, status, sent_at,
                created_time, updated_time, deleted
            ) VALUES(
                :aid, :oid, :bid, :bnick,
                :gid, :gtitle, :sid, :pnm,
                :content, 'waiting', NOW(),
                NOW(), NOW(), 0
            )
            """
        ),
        {
            "aid": account_id,
            "oid": order_id,
            "bid": buyer_user_id,
            "bnick": buyer_user_name,
            "gid": xy_goods_id,
            "gtitle": goods_title,
            "sid": s_id,
            "pnm": pnm_id,
            "content": content,
        },
    )
    logger.info(
        "已发送发货声明并创建会话 accountId=%d orderId=%s xyGoodsId=%s sId=%s buyer=%s",
        account_id, order_id, xy_goods_id, s_id, buyer_user_id,
    )
    return True


async def _send_statement_message(
    account_id: int,
    s_id: str,
    buyer_user_id: str,
    content: str,
) -> bool:
    """通过 WebSocket 发送声明文案给买家。"""
    try:
        from .ws_delivery_handler import send_delivery_message_result

        result = await send_delivery_message_result(
            account_id, s_id, buyer_user_id, content
        )
        status = str(result.get("status") or "").lower()
        if status == "confirmed":
            return True
        logger.warning(
            "发送声明消息失败: accountId=%d status=%s errorCode=%s",
            account_id, status, result.get("errorCode", ""),
        )
        return False
    except Exception as e:
        logger.error("发送声明消息异常: accountId=%d error=%s", account_id, e)
        return False


# ============================================================
# 买家回复识别（确认/取消）
# ============================================================
def classify_buyer_reply(msg: dict) -> Optional[str]:
    """识别买家回复是否为"确认"或"取消"。

    Returns:
        "confirm"=确认 / "cancel"=取消 / None=非声明回复（普通消息）
    """
    # 仅处理文本消息（contentType=1）
    content_type = msg.get("contentType") or msg.get("content_type") or 0
    try:
        content_type = int(content_type)
    except (ValueError, TypeError):
        return None
    if content_type != 1:
        return None

    # 提取消息文本
    text_content = str(msg.get("msgContent") or "").strip()
    if not text_content:
        return None

    # 标准化：去除空格、标点（【】[] () （）等）
    normalized = re.sub(r"[\s【】\[\]()（）.,，。!！?？:：;；\-_/\\]", "", text_content)
    normalized_lower = normalized.lower()

    # 优先匹配取消（"取消"优先于"确认"，避免"确认取消"被误判为确认）
    for kw in CANCEL_KEYWORDS:
        if kw in normalized:
            return "cancel"
    for kw in CONFIRM_KEYWORDS:
        if kw in normalized:
            return "confirm"
    # 英文兜底
    if "cancel" in normalized_lower:
        return "cancel"
    if "confirm" in normalized_lower or "yes" in normalized_lower or "ok" in normalized_lower:
        return "confirm"
    return None


async def handle_buyer_statement_reply(
    account_id: int,
    msg: dict,
) -> Optional[str]:
    """处理买家对声明会话的回复。

    在 ws_startup.on_message_callback 中收到买家消息后调用。
    流程：
    1. 识别回复类型（confirm/cancel/None）
    2. 若为 None，返回 None（普通消息，AI 自动回复照常）
    3. 查询该会话是否有 waiting 状态的声明会话
    4. 无匹配会话：静默忽略+记日志，返回 None（AI 自动回复照常）
    5. 有匹配会话：
       - confirm → 更新会话 confirmed → 触发发货 → 返回 "confirm_handled"
       - cancel → 更新会话 cancelled → 通知卖家+回复买家 → 返回 "cancel_handled"

    Returns:
        "confirm_handled" / "cancel_handled" / None
    """
    reply_type = classify_buyer_reply(msg)
    if reply_type is None:
        return None  # 非声明回复

    s_id = str(msg.get("sId") or msg.get("sid") or "")
    if not s_id:
        return None

    reply_msg_id = str(msg.get("pnmId") or msg.get("msgId") or "")

    async with async_session() as db:
        try:
            # 查询该会话 waiting 状态的声明会话（取最早一条，FIFO）
            session = (
                await db.execute(
                    text(
                        """
                        SELECT id, order_id, xy_goods_id, buyer_id, goods_title
                        FROM delivery_statement_session
                        WHERE account_id=:aid
                          AND s_id=:sid AND status='waiting' AND deleted=0
                        ORDER BY created_time ASC LIMIT 1
                        """
                    ),
                    {"aid": account_id, "sid": s_id},
                )
            ).first()

            if session is None:
                # 无匹配声明会话：静默忽略+记日志，AI 自动回复照常
                logger.debug(
                    "买家回复%s但无匹配声明会话，忽略 accountId=%d sId=%s",
                    reply_type, account_id, s_id,
                )
                await db.commit()
                return None

            session_id = session[0]
            order_id = session[1]
            xy_goods_id = session[2]
            buyer_id = session[3]
            goods_title = session[4]

            if reply_type == "confirm":
                await _handle_confirm(
                    db, account_id, session_id,
                    order_id, xy_goods_id, buyer_id, goods_title,
                    s_id, reply_msg_id,
                )
            else:
                await _handle_cancel(
                    db, account_id, session_id,
                    order_id, xy_goods_id, buyer_id, goods_title,
                    s_id, reply_msg_id,
                )
            await db.commit()
            return "confirm_handled" if reply_type == "confirm" else "cancel_handled"
        except Exception as e:
            await db.rollback()
            logger.error(
                "处理买家声明回复失败 accountId=%d sId=%s replyType=%s error=%s",
                account_id, s_id, reply_type, e, exc_info=True,
            )
            return None


async def _handle_confirm(
    db: AsyncSession,
    account_id: int,
    session_id: int,
    order_id: str,
    xy_goods_id: str,
    buyer_id: str,
    goods_title: str,
    s_id: str,
    reply_msg_id: str,
) -> None:
    """处理买家确认：更新会话 confirmed → 触发发货"""
    # 1. 更新会话状态
    result = await db.execute(
        text(
            """
            UPDATE delivery_statement_session
            SET status='confirmed', confirmed_at=NOW(), confirm_source='buyer',
                reply_msg_id=:rmid, updated_time=NOW()
            WHERE id=:id AND status='waiting'
            """
        ),
        {"id": session_id, "rmid": reply_msg_id},
    )
    if result.rowcount == 0:
        logger.warning(
            "买家确认但会话状态已变更，跳过 accountId=%d sessionId=%d",
            account_id, session_id,
        )
        return

    logger.info(
        "买家确认声明，触发发货 accountId=%d orderId=%s sessionId=%d",
        account_id, order_id, session_id,
    )

    # 2. 触发发货：复用 ws_delivery_handler 的发货流程
    try:
        from .ws_delivery_handler import _trigger_delivery_for_confirmed_statement

        await _trigger_delivery_for_confirmed_statement(
            db,
            account_id,
            order_id=order_id,
            xy_goods_id=xy_goods_id,
            buyer_user_id=buyer_id,
            s_id=s_id,
            goods_title=goods_title,
            session_id_stmt=session_id,
        )
    except Exception as e:
        logger.error(
            "买家确认后触发发货失败 accountId=%d orderId=%s error=%s",
            account_id, order_id, e, exc_info=True,
        )


async def _handle_cancel(
    db: AsyncSession,
    account_id: int,
    session_id: int,
    order_id: str,
    xy_goods_id: str,
    buyer_id: str,
    goods_title: str,
    s_id: str,
    reply_msg_id: str,
) -> None:
    """处理买家取消：更新会话 cancelled → 通知卖家 + 回复买家"""
    result = await db.execute(
        text(
            """
            UPDATE delivery_statement_session
            SET status='cancelled', cancelled_at=NOW(), cancel_source='buyer',
                reply_msg_id=:rmid, updated_time=NOW()
            WHERE id=:id AND status='waiting'
            """
        ),
        {"id": session_id, "rmid": reply_msg_id},
    )
    if result.rowcount == 0:
        logger.warning(
            "买家取消但会话状态已变更，跳过 accountId=%d sessionId=%d",
            account_id, session_id,
        )
        return

    logger.info(
        "买家取消声明 accountId=%d orderId=%s sessionId=%d",
        account_id, order_id, session_id,
    )

    # 1. 向买家发送取消提示
    await _send_statement_message(account_id, s_id, buyer_id, BUYER_CANCEL_REPLY)

    # 2. 通知卖家（飞书/站内）
    try:
        from .notify_dispatcher import notify_new_order

        # 复用新订单通知（标记为取消），失败不影响主流程
        await notify_new_order(
            account_id,
            {
                "reminderContent": f"买家取消发货声明，已转人工客服。商品：{goods_title or xy_goods_id}",
                "orderId": order_id,
            },
        )
    except Exception:
        logger.debug("买家取消声明的通知异常，忽略", exc_info=True)
