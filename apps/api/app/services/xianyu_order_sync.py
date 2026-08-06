"""
闲鱼订单同步服务。

参考商业版 automation_runtime.sync_sold_orders_for_account 实现：
1. 调用 mtop.taobao.idle.trade.merchant.sold.get 分页拉取卖家已售订单
2. 解析 commonData / buyerInfoVO / priceVO / itemInfoVO 四个子对象
3. 按 (account_id, external_order_id) upsert 到 xianyu_trade_order / xianyu_trade_order_item
4. 可选拉取退款订单补充状态（mtop.taobao.idle.merchant.refund.list）
"""
import asyncio
import datetime
import logging
from typing import Any, Optional

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.database import async_session
from ..models.entities import XianyuTradeOrder, XianyuTradeOrderItem
from .xianyu_api_service import fetch_sold_orders_page

logger = logging.getLogger(__name__)

# 闲鱼订单状态文本 → 内部状态码（与商业版 _map_remote_order_status 一致）
# 0待付款 1已付款 2待发货 3已发货 4已完成 5已关闭
_ORDER_STATUS_MAP = {
    "待付款": 0,
    "已付款": 1,
    "待发货": 2,
    "已发货": 3,
    "交易成功": 4,
    "交易关闭": 5,
    "退款中": 2,
    "退款成功": 5,
    "已退款": 5,
    "退款关闭": 5,
}


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _safe_int(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        try:
            return int(float(value))
        except (ValueError, TypeError):
            return default


def _map_order_status(raw_status: Any, in_refund: bool = False) -> int:
    """将闲鱼返回的订单状态文本映射为内部状态码。"""
    if in_refund:
        return 2
    status_text = _text(raw_status)
    return _ORDER_STATUS_MAP.get(status_text, 1)


def _parse_order_time(value: Any) -> Optional[datetime.datetime]:
    """解析闲鱼订单时间字段，支持 'YYYY-MM-DD HH:MM:SS' 和 'YYYY-MM-DD'。"""
    text_val = _text(value)
    if not text_val:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(text_val, fmt)
        except ValueError:
            continue
    return None


def _parse_order_amount(total_price: Any, unit_price: Any, quantity: int) -> str:
    """解析订单金额，优先用 totalPrice，其次 unit_price * quantity。"""
    total_text = _text(total_price)
    if total_text:
        try:
            return f"{float(total_text):.2f}"
        except (ValueError, TypeError):
            pass
    unit_text = _text(unit_price)
    if unit_text:
        try:
            return f"{float(unit_text) * max(quantity, 1):.2f}"
        except (ValueError, TypeError):
            pass
    return "0.00"


def _detect_bargain_from_buttons(item: dict[str, Any]) -> bool:
    """检测订单是否为小刀订单：btnList 中存在 tradeAction=SKIP_PIN。

    参考商业版 order_service._parse_sold_order_item 的小刀判断逻辑。
    递归搜索整个订单数据，不依赖固定的层级位置。
    """
    def search(obj: Any) -> bool:
        if isinstance(obj, dict):
            btn_list = obj.get("btnList")
            if isinstance(btn_list, list):
                for btn in btn_list:
                    if isinstance(btn, dict) and btn.get("tradeAction") == "SKIP_PIN":
                        return True
            for v in obj.values():
                if search(v):
                    return True
        elif isinstance(obj, list):
            for v in obj:
                if search(v):
                    return True
        return False
    return search(item)


def _parse_remote_order_item(item: dict[str, Any]) -> Optional[dict[str, Any]]:
    """解析闲鱼 mtop 返回的单条订单数据，参考商业版 _parse_remote_sold_order_item。"""
    if not isinstance(item, dict):
        return None

    common = item.get("commonData") if isinstance(item.get("commonData"), dict) else {}
    buyer_info = item.get("buyerInfoVO") if isinstance(item.get("buyerInfoVO"), dict) else {}
    price_vo = item.get("priceVO") if isinstance(item.get("priceVO"), dict) else {}
    item_info = item.get("itemInfoVO") if isinstance(item.get("itemInfoVO"), dict) else {}
    item_buy_info = common.get("itemBuyInfo") if isinstance(common.get("itemBuyInfo"), dict) else {}

    external_order_id = _text(common.get("orderId"))
    if not external_order_id:
        return None

    quantity = _safe_int(price_vo.get("buyNum"), 1)
    goods_id_raw = _text(
        common.get("itemId") or item_info.get("itemId") or item_buy_info.get("itemId")
    )
    # XianyuTradeOrderItem.goods_id 是 BigInteger 列，需要数值类型而非字符串
    goods_id = _safe_int(goods_id_raw, 0) if goods_id_raw else 0
    goods_title = _text(
        item.get("itemTitle")
        or common.get("itemTitle")
        or item_info.get("title")
        or item_buy_info.get("title")
    ) or (f"商品 {goods_id}" if goods_id else "订单商品")
    goods_image = _text(
        common.get("itemMainPic")
        or item_info.get("itemPic")
        or item_buy_info.get("itemPic")
        or item.get("itemMainPic")
    )
    goods_price = _text(price_vo.get("auctionPrice") or price_vo.get("unitPrice"))
    total_amount = _parse_order_amount(price_vo.get("totalPrice"), goods_price, quantity)

    in_refund = bool(common.get("inRefund"))
    order_status = _map_order_status(common.get("orderStatus"), in_refund)

    return {
        "external_order_id": external_order_id,
        "order_status": order_status,
        "total_amount": total_amount,
        "buyer_name": _text(buyer_info.get("userNick") or buyer_info.get("name")),
        "buyer_id": _text(buyer_info.get("buyerId")),
        "create_time": _parse_order_time(common.get("createTime")),
        "pay_time": _parse_order_time(common.get("payTime") or common.get("paymentTime")),
        "ship_time": _parse_order_time(
            common.get("deliveryTime") or common.get("consignTime") or common.get("shipTime")
        ),
        "confirm_time": _parse_order_time(common.get("endTime") or common.get("confirmTime")),
        "buyer_message": _text(common.get("buyerMessage") or common.get("leaveMessage")),
        "item_id": goods_id_raw,  # String 列，保留原始字符串
        "is_bargain": _detect_bargain_from_buttons(item),
        # 订单项
        "items": [
            {
                "goods_id": goods_id,  # BigInteger 列，已转换为 int
                "goods_title": goods_title,
                "goods_image": goods_image,
                "goods_price": goods_price,
                "goods_count": quantity,
                "quantity": quantity,
            }
        ],
    }


async def _upsert_order(db: AsyncSession, account_id: int, parsed: dict[str, Any]) -> tuple[str, str]:
    """按 (account_id, external_order_id) upsert 订单，返回 ("inserted"|"updated", external_order_id)。"""
    external_order_id = parsed["external_order_id"]

    result = await db.execute(
        select(XianyuTradeOrder).where(
            XianyuTradeOrder.account_id == account_id,
            XianyuTradeOrder.external_order_id == external_order_id,
            XianyuTradeOrder.deleted == 0,
        )
    )
    order = result.scalar_one_or_none()

    if order is None:
        order = XianyuTradeOrder(
            account_id=account_id,
            external_order_id=external_order_id,
            order_status=parsed["order_status"],
            total_amount=parsed["total_amount"],
            buyer_name=parsed["buyer_name"],
            buyer_id=parsed["buyer_id"],
            create_time=parsed["create_time"],
            pay_time=parsed["pay_time"],
            ship_time=parsed["ship_time"],
            confirm_time=parsed["confirm_time"],
            buyer_message=parsed["buyer_message"],
            item_id=parsed["item_id"],
            is_bargain=1 if parsed.get("is_bargain") else 0,
            is_rated=0,
            is_red_flower=0,
            deleted=0,
        )
        db.add(order)
        await db.flush()
        action = "inserted"
    else:
        order.order_status = parsed["order_status"]
        order.total_amount = parsed["total_amount"]
        if parsed["buyer_name"]:
            order.buyer_name = parsed["buyer_name"]
        if parsed["buyer_id"]:
            order.buyer_id = parsed["buyer_id"]
        # 时间字段用 COALESCE 语义：远程有值才覆盖，避免清空已有数据
        if parsed["create_time"]:
            order.create_time = parsed["create_time"]
        if parsed["pay_time"]:
            order.pay_time = parsed["pay_time"]
        if parsed["ship_time"]:
            order.ship_time = parsed["ship_time"]
        if parsed["confirm_time"]:
            order.confirm_time = parsed["confirm_time"]
        if parsed["buyer_message"]:
            order.buyer_message = parsed["buyer_message"]
        if parsed["item_id"]:
            order.item_id = parsed["item_id"]
        # 小刀标记：只置 True 不回退（参考商业版 order_service 逻辑）
        if parsed.get("is_bargain") and not order.is_bargain:
            order.is_bargain = 1
        order.updated_time = datetime.datetime.now()
        action = "updated"

    # 替换订单项：先软删除旧的，再插入新的
    await db.execute(
        text(
            "UPDATE xianyu_trade_order_item SET deleted = 1, updated_time = NOW() "
            "WHERE order_id = :oid AND deleted = 0"
        ),
        {"oid": order.id},
    )
    for item_data in parsed.get("items", []):
        db.add(XianyuTradeOrderItem(
            order_id=order.id,
            goods_id=item_data.get("goods_id"),
            goods_title=item_data.get("goods_title"),
            goods_name=item_data.get("goods_title"),
            goods_image=item_data.get("goods_image"),
            goods_price=item_data.get("goods_price"),
            goods_count=item_data.get("goods_count", 1),
            quantity=item_data.get("quantity", 1),
            deleted=0,
        ))

    return action, external_order_id


async def sync_orders_for_account(
    account_id: int,
    max_pages: int = 20,
    page_size: int = 30,
) -> dict[str, Any]:
    """同步指定账号的闲鱼已售订单。

    流程：
    1. 分页调用 mtop.taobao.idle.trade.merchant.sold.get 拉取订单
    2. 逐条解析并 upsert 到本地数据库
    3. 返回同步统计

    Returns:
        {"success": True, "total": N, "inserted": N, "updated": N, "failed": N}
        或 {"success": False, "error": "..."}
    """
    logger.info("开始同步订单: accountId=%d", account_id)

    total_fetched = 0
    inserted = 0
    updated = 0
    failed = 0
    sold_item_ids_to_relist: list = []  # 新售订单对应的商品 ID，用于触发售整自动上架
    page_number = 1

    try:
        while page_number <= max_pages:
            # 闲鱼 API 是同步 HTTP 调用，用 to_thread 避免阻塞事件循环
            page_result = await asyncio.to_thread(
                fetch_sold_orders_page,
                account_id=account_id,
                page_number=page_number,
                page_size=page_size,
            )

            if not page_result or not page_result.get("success"):
                error_msg = (page_result or {}).get("error", "订单平台接口暂不可用")
                error_code = (page_result or {}).get("errorCode", "ORDER_SYNC_UNAVAILABLE")
                logger.warning(
                    "订单分页拉取失败 accountId=%d page=%d errorCode=%s",
                    account_id,
                    page_number,
                    error_code,
                )
                if page_number == 1 and total_fetched == 0:
                    return {
                        "success": False,
                        "error": error_msg,
                        "errorCode": error_code,
                    }
                break

            data = page_result.get("data", {})
            items = data.get("items", [])
            has_next = data.get("nextPage", False)
            total_count = data.get("totalCount", 0)

            if not items:
                break

            async with async_session() as db:
                for item in items:
                    try:
                        parsed = _parse_remote_order_item(item)
                        if not parsed:
                            failed += 1
                            continue
                        action, _ = await _upsert_order(db, account_id, parsed)
                        if action == "inserted":
                            inserted += 1
                            # 新售订单插入成功，记录商品 ID 用于触发售整自动上架钩子
                            sold_item_id = str(parsed.get("item_id") or "").strip()
                            if sold_item_id:
                                sold_item_ids_to_relist.append(sold_item_id)
                        else:
                            updated += 1
                        total_fetched += 1
                    except Exception as exc:
                        failed += 1
                        logger.warning(
                            "订单入库失败 accountId=%d errorType=%s",
                            account_id,
                            type(exc).__name__,
                        )
                await db.commit()

            logger.info(
                "订单同步分页完成: accountId=%d page=%d 本页=%d 累计=%d 总数=%s",
                account_id, page_number, len(items), total_fetched, total_count,
            )

            if not has_next or len(items) < page_size:
                break

            page_number += 1
            # 风控间隔
            await asyncio.sleep(0.5)

        logger.info(
            "订单同步完成: accountId=%d total=%d inserted=%d updated=%d failed=%d",
            account_id, total_fetched, inserted, updated, failed,
        )

        # 售整自动上架钩子：新售订单触发后立即异步重发
        # 失败不影响订单同步主流程；relist_scheduler 也会每 3 分钟兜底扫描
        if sold_item_ids_to_relist:
            try:
                from .relist_service import relist_sold_item
                for sold_item_id in sold_item_ids_to_relist:
                    asyncio.create_task(
                        relist_sold_item(account_id, sold_item_id),
                        name=f"relist_hook_{account_id}_{sold_item_id}",
                    )
            except Exception as exc:
                logger.warning(
                    "触发售整自动上架失败 accountId=%d errorType=%s",
                    account_id, type(exc).__name__,
                )
        return {
            "success": True,
            "total": total_fetched,
            "inserted": inserted,
            "updated": updated,
            "failed": failed,
        }
    except Exception as exc:
        logger.error(
            "订单同步异常 accountId=%d errorType=%s",
            account_id,
            type(exc).__name__,
        )
        return {
            "success": False,
            "error": "订单同步未完成，请检查账号状态后重试",
            "errorCode": "ORDER_SYNC_FAILED",
        }
