import asyncio
import datetime
import logging
import math

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.database import get_db
from ....core.response import ResultObject
from ....models.entities import XianyuTradeOrder
from ....schemas.order import (
    ConfirmFreeshippingReqDTO,
    ConfirmShipmentReqDTO,
    OrderListData,
    OrderQueryReqDTO,
    OrderVO,
    SoldOrderSyncReqDTO,
)
from ....services.xianyu_api_service import confirm_order_shipment, confirm_freeshipping
from ....services.xianyu_order_sync import sync_orders_for_account
from ..deps import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/order")

# 批量刷新扩展 router（前端 order.js 调用 POST /api/order/batchRefresh，参考项目无实现）
batch_refresh_router = APIRouter(prefix="/order", tags=["order-batch"])


_ORDER_SORT_FIELD_MAP = {
    "createdAt": XianyuTradeOrder.create_time,
    "buyerName": XianyuTradeOrder.buyer_name,
    "orderStatus": XianyuTradeOrder.order_status,
}


def _resolve_order_sort(req: "OrderQueryReqDTO"):
    """根据请求中的 sort_field / sort_order 构造排序条件，默认 create_time DESC。"""
    field_name = (req.sort_field or "createdAt").strip()
    column = _ORDER_SORT_FIELD_MAP.get(field_name, XianyuTradeOrder.create_time)
    order = (req.sort_order or "desc").strip().lower()
    return column.asc() if order == "asc" else column.desc()


def trade_order_to_vo(order: XianyuTradeOrder) -> OrderVO:
    return OrderVO(
        id=order.id,
        account_id=order.account_id,
        external_order_id=order.external_order_id,
        order_status=order.order_status,
        buyer_name=order.buyer_name,
        total_amount=order.total_amount,
        create_time=str(order.create_time) if order.create_time else None,
        pay_time=str(order.pay_time) if order.pay_time else None,
        xianyu_account_id=order.account_id,
        order_id=order.external_order_id,
        total_price=order.total_amount,
    )


@router.post("/list", response_model=ResultObject[OrderListData])
async def list_orders(
    req: OrderQueryReqDTO,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    try:
        page_num = max(req.page_num or 1, 1)
        page_size = max(min(req.page_size or 20, 100), 1)
        query = select(XianyuTradeOrder)
        if req.xianyu_account_id is not None:
            query = query.where(XianyuTradeOrder.account_id == req.xianyu_account_id)
        if req.xy_goods_id:
            query = query.where(XianyuTradeOrder.external_order_id == req.xy_goods_id)
        if req.order_status is not None:
            query = query.where(XianyuTradeOrder.order_status == req.order_status)

        count_query = select(func.count()).select_from(query.subquery())
        total_result = await db.execute(count_query)
        total = total_result.scalar() or 0

        offset = (page_num - 1) * page_size
        query = query.order_by(_resolve_order_sort(req)).offset(offset).limit(page_size)
        result = await db.execute(query)
        orders = result.scalars().all()

        records = [trade_order_to_vo(o) for o in orders]
        pages = math.ceil(total / page_size) if total > 0 else 0
        return ResultObject.success(
            OrderListData(
                records=records,
                total=total,
                page_num=page_num,
                page_size=page_size,
                pages=pages,
            )
        )
    except Exception as e:
        logger.error("查询订单列表失败", exc_info=True)
        return ResultObject.internal_error()


@router.post("/confirmShipment", response_model=ResultObject[str])
async def confirm_shipment(
    req: ConfirmShipmentReqDTO,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """确认发货（同步商业版能力：调用闲鱼 API 真实确认发货）。

    - 普通订单走 mtop.taobao.idle.logistic.consign.dummy（无物流虚拟发货）
    - 小刀订单（is_bargain=1）走 mtop.idle.groupon.activity.seller.freeshipping（免拼发货）
    - 已发货（ORDER_ALREADY_DELIVERY）视为幂等成功
    - 成功后更新本地 order_status=3, ship_time=now
    """
    try:
        if not req.order_id:
            return ResultObject.failed("订单ID不能为空")
        result = await db.execute(
            select(XianyuTradeOrder).where(
                XianyuTradeOrder.account_id == req.xianyu_account_id,
                XianyuTradeOrder.external_order_id == req.order_id,
                XianyuTradeOrder.deleted == 0,
            )
        )
        order = result.scalar_one_or_none()
        if not order:
            return ResultObject.failed("订单不存在")
        if order.order_status == 3:
            return ResultObject.success("订单已确认发货，无需重复操作")

        # 根据是否小刀订单选择发货方式
        is_bargain = bool(order.is_bargain)
        api_result = await asyncio.to_thread(
            confirm_order_shipment,
            account_id=req.xianyu_account_id,
            order_id=req.order_id,
            is_bargain=is_bargain,
            item_id=order.item_id,
            buyer_id=order.buyer_id,
        )

        if not api_result or not api_result.get("success"):
            error_msg = (api_result or {}).get("message") or (api_result or {}).get("error") or "闲鱼确认发货失败"
            logger.warning(
                "确认发货失败 accountId=%d orderId=%s isBargain=%s error=%s",
                req.xianyu_account_id, req.order_id, is_bargain, error_msg,
            )
            return ResultObject.failed(error_msg)

        # 闲鱼 API 成功后更新本地状态
        order.order_status = 3
        order.ship_time = datetime.datetime.now()
        await db.commit()

        ship_method = api_result.get("ship_method", "virtual" if not is_bargain else "freeshipping")
        idempotent = "（已发货幂等）" if api_result.get("idempotent") else ""
        logger.info(
            "确认发货成功 accountId=%d orderId=%s shipMethod=%s%s",
            req.xianyu_account_id, req.order_id, ship_method, idempotent,
        )
        return ResultObject.success(f"确认发货成功（{ship_method}）{idempotent}")
    except Exception as e:
        logger.error("确认发货失败", exc_info=True)
        return ResultObject.internal_error()


@router.post("/confirmFreeshipping", response_model=ResultObject[str])
async def confirm_freeshipping_endpoint(
    req: ConfirmFreeshippingReqDTO,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """免拼发货（小刀订单专用）。

    调用闲鱼 mtop.idle.groupon.activity.seller.freeshipping 接口。
    必须提供 item_id 和 buyer_id（闲鱼接口要求整数类型）。
    """
    try:
        if not req.order_id:
            return ResultObject.failed("订单ID不能为空")
        if req.item_id is None or req.buyer_id is None:
            return ResultObject.failed("免拼发货必须提供商品ID和买家ID")

        result = await db.execute(
            select(XianyuTradeOrder).where(
                XianyuTradeOrder.account_id == req.xianyu_account_id,
                XianyuTradeOrder.external_order_id == req.order_id,
                XianyuTradeOrder.deleted == 0,
            )
        )
        order = result.scalar_one_or_none()
        if not order:
            return ResultObject.failed("订单不存在")
        if order.order_status == 3:
            return ResultObject.success("订单已确认发货，无需重复操作")

        api_result = await asyncio.to_thread(
            confirm_freeshipping,
            account_id=req.xianyu_account_id,
            order_id=req.order_id,
            item_id=req.item_id,
            buyer_id=req.buyer_id,
        )

        if not api_result or not api_result.get("success"):
            error_msg = (api_result or {}).get("message") or (api_result or {}).get("error") or "免拼发货失败"
            logger.warning(
                "免拼发货失败 accountId=%d orderId=%s itemId=%s buyerId=%s error=%s",
                req.xianyu_account_id, req.order_id, req.item_id, req.buyer_id, error_msg,
            )
            return ResultObject.failed(error_msg)

        order.order_status = 3
        order.ship_time = datetime.datetime.now()
        order.is_bargain = 1
        await db.commit()

        idempotent = "（已发货幂等）" if api_result.get("idempotent") else ""
        logger.info(
            "免拼发货成功 accountId=%d orderId=%s%s",
            req.xianyu_account_id, req.order_id, idempotent,
        )
        return ResultObject.success(f"免拼发货成功{idempotent}")
    except Exception as e:
        logger.error("免拼发货失败", exc_info=True)
        return ResultObject.internal_error()


@router.post("/syncSoldOrders", response_model=ResultObject[dict])
async def sync_sold_orders(
    req: SoldOrderSyncReqDTO,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """同步闲鱼已售订单（同步商业版能力：调用真实订单同步流程）。

    通过 mtop.taobao.idle.trade.merchant.sold.get 分页拉取远程订单并 upsert 到本地。
    """
    try:
        if not req.xianyu_account_id:
            return ResultObject.failed("账号ID不能为空")

        result = await sync_orders_for_account(account_id=req.xianyu_account_id)

        if not result.get("success"):
            error_msg = result.get("error") or "订单同步失败"
            error_code = result.get("errorCode", "ORDER_SYNC_FAILED")
            logger.warning(
                "订单同步失败 accountId=%d errorCode=%s error=%s",
                req.xianyu_account_id, error_code, error_msg,
            )
            return ResultObject.failed(error_msg)

        logger.info(
            "订单同步成功 accountId=%d total=%d inserted=%d updated=%d failed=%d",
            req.xianyu_account_id,
            result.get("total", 0),
            result.get("inserted", 0),
            result.get("updated", 0),
            result.get("failed", 0),
        )
        return ResultObject.success({
            "message": "同步成功",
            "synced_count": result.get("total", 0),
            "inserted": result.get("inserted", 0),
            "updated": result.get("updated", 0),
            "failed": result.get("failed", 0),
        })
    except Exception as e:
        logger.error("同步鱼小铺卖家订单列表失败", exc_info=True)
        return ResultObject.internal_error()


# ==================== 批量刷新订单（前端 order.js 调用，参考项目无实现） ====================

@batch_refresh_router.post("/batchRefresh", response_model=ResultObject[dict])
async def batch_refresh_orders(
    data: dict = {},
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """批量刷新订单状态。开源版简化实现：重新查询并返回最新订单数据。

    前端 order.js 使用 300 秒超时，预期是长耗时操作。开源版不做远程同步，
    仅返回本地数据库最新状态作为"刷新"结果。
    """
    try:
        order_ids = data.get("orderIds") or []
        account_id = data.get("xianyuAccountId") or data.get("accountId")

        query = select(XianyuTradeOrder)
        if account_id:
            query = query.where(XianyuTradeOrder.account_id == int(account_id))
        if order_ids:
            query = query.where(XianyuTradeOrder.external_order_id.in_(order_ids))
        query = query.order_by(XianyuTradeOrder.id.desc()).limit(100)
        result = await db.execute(query)
        orders = result.scalars().all()
        records = [trade_order_to_vo(o) for o in orders]
        return ResultObject.success({
            "message": "刷新成功",
            "refreshed_count": len(records),
            "records": [r.model_dump() if hasattr(r, "model_dump") else r for r in records],
        })
    except Exception as e:
        logger.error("批量刷新订单失败", exc_info=True)
        return ResultObject.internal_error()
