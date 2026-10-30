"""Read-only original order/delivery routes. No mock payment/send HTTP API."""
from fastapi import APIRouter, Query

from app.core.response import ResultObject
from app.desktop_inventory_api import Inventory, Id, Current, Size
from app.services.desktop_delivery import DesktopDeliveryStore
from app.services.desktop_inventory import InventoryError, page

router = APIRouter(prefix="/api")


@router.get("/orders")
def orders(repo: Inventory, accountId: int | None = Query(default=None, gt=0, le=2**63-1),
           buyerId: str | None = Query(default=None, max_length=200), current: Current = 1, size: Size = 20):
    with repo.connection() as conn:
        where, params = "deleted=0", []
        if accountId is not None:
            where += " AND account_id=?"
            params.append(accountId)
        if buyerId:
            where += " AND buyer_id=?"
            params.append(buyerId)
        total = conn.execute(f"SELECT COUNT(*) FROM xianyu_trade_order WHERE {where}", params).fetchone()[0]
        rows = conn.execute(f"SELECT * FROM xianyu_trade_order WHERE {where} ORDER BY id DESC LIMIT ? OFFSET ?", (*params, size, (current-1)*size))
        return ResultObject.success(page([order_record(row) for row in rows], total, current, size))


def order_record(row):
    return {"id": row["id"], "accountId": row["account_id"], "externalOrderId": row["external_order_id"],
            "buyerId": row["buyer_id"], "orderStatus": row["order_status"], "totalAmount": row["total_amount"],
            "verificationSource": row["verification_source"], "createdTime": row["created_time"]}


@router.get("/orders/{order_id}")
def order_detail(order_id: Id, repo: Inventory):
    with repo.connection() as conn:
        order = conn.execute("SELECT * FROM xianyu_trade_order WHERE id=? AND deleted=0", (order_id,)).fetchone()
        if order is None:
            raise InventoryError("订单不存在", 404)
        record = order_record(order)
        record["items"] = [dict(row) for row in conn.execute("SELECT id,goods_id AS goodsId,goods_title AS goodsTitle,quantity,sku_id AS skuId FROM xianyu_trade_order_item WHERE order_id=? AND deleted=0", (order_id,))]
        record["deliveryIds"] = [row[0] for row in conn.execute("SELECT id FROM delivery_record WHERE order_id=? AND desktop_managed=1", (order_id,))]
        return ResultObject.success(record)


@router.get("/auto-delivery/records")
def records(repo: Inventory, current: Current = 1, size: Size = 20, needsReview: bool = False):
    return ResultObject.success(DesktopDeliveryStore(repo.path).list_records(current, size, needsReview))


@router.get("/auto-delivery/records/{record_id}")
def record_detail(record_id: Id, repo: Inventory):
    return ResultObject.success(DesktopDeliveryStore(repo.path).detail(record_id))
