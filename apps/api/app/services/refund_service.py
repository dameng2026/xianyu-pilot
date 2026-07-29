"""退款管理服务。

提供退款列表拉取、同步、本地查询能力。
- 调用 mtop.taobao.idle.merchant.refund.list 拉取闲鱼退款订单
- 将原始响应标准化后 upsert 到 xianyu_refund 表
- 支持按账号、状态筛选与分页查询

参考商业版 automation-service/app/services/refund_service.py 与
core-api 中的退款管理逻辑，适配开源版单管理员架构（移除 tenant_id 维度）。
"""
from __future__ import annotations

import asyncio
import datetime
import json
import logging
import re
from typing import Any, Optional

from sqlalchemy import and_, desc, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import entities as models
from .xianyu_api_service import fetch_refund_orders_page

logger = logging.getLogger(__name__)


# 退款接口的 disputeStatus：1/2/3=退款中，5=退款成功
REFUND_DISPUTE_STATUSES = ["1", "2", "3", "5"]

# 单次同步最大页数，避免极端情况下拉取过多
MAX_PAGES_PER_SYNC = 20
REFUND_PAGE_SIZE = 20


def _safe_text(value: Any, max_len: int = 0) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if max_len and len(text) > max_len:
        text = text[:max_len]
    return text


def _safe_decimal(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_datetime(value: Any) -> Optional[datetime.datetime]:
    if not value:
        return None
    # 闲鱼返回的毫秒时间戳
    if isinstance(value, (int, float)):
        try:
            return datetime.datetime.fromtimestamp(int(value) / 1000)
        except (OSError, ValueError, OverflowError):
            return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            return datetime.datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _parse_refund_item(raw: dict) -> dict:
    """将 MTOP 退款列表的单条响应标准化为本地存储字段。

    退款列表接口返回结构较复杂，关键字段嵌套在 bizOrder.refundOrder 中。
    """
    biz_order = raw.get("bizOrder") or {}
    refund_order = biz_order.get("refundOrder") or raw.get("refundOrder") or {}

    refund_fee = _safe_decimal(refund_order.get("refundFee") or biz_order.get("refundFee"))
    auction_price = _safe_decimal(refund_order.get("auctionPrice"))

    main = biz_order.get("main") or {}
    item = main.get("item") or {}
    buyer = main.get("buyer") or {}
    seller = main.get("seller") or {}

    refund_id = (
        _safe_text(refund_order.get("refundId"))
        or _safe_text(raw.get("refundId"))
        or _safe_text(biz_order.get("refundId"))
    )
    order_id = (
        _safe_text(biz_order.get("orderId"))
        or _safe_text(main.get("orderId"))
        or _safe_text(raw.get("orderId"))
    )

    item_info_lines = refund_order.get("itemInfoLines") or []
    if isinstance(item_info_lines, list):
        item_info_text = "; ".join(
            str(line.get("text", "")) if isinstance(line, dict) else str(line)
            for line in item_info_lines
        )
    else:
        item_info_text = _safe_text(item_info_lines)

    right_buttons = refund_order.get("rightButtons") or []
    buttons_serialized = []
    if isinstance(right_buttons, list):
        for btn in right_buttons:
            if isinstance(btn, dict):
                buttons_serialized.append({
                    "text": _safe_text(btn.get("text")),
                    "action": _safe_text(btn.get("action")),
                })

    return {
        "external_refund_id": refund_id,
        "external_order_id": order_id,
        "external_item_id": _safe_text(item.get("itemId")),
        "item_title": _safe_text(item.get("title"), 500),
        "item_pic_url": _safe_text(item.get("picUrl")),
        "item_info_lines": _safe_text(item_info_text, 1000),
        "buy_num": _safe_text(item.get("buyAmount") or main.get("buyAmount")),
        "refund_fee": refund_fee,
        "auction_price": auction_price,
        "order_status": _safe_text(refund_order.get("orderStatus") or biz_order.get("orderStatus"), 64),
        "order_simple_remark": _safe_text(refund_order.get("orderSimpleRemark"), 255),
        "refund_status": _safe_text(refund_order.get("refundStatus"), 64),
        "refund_status_desc": _safe_text(refund_order.get("refundStatusDesc"), 500),
        "common_refund_status": _safe_text(refund_order.get("commonRefundStatus"), 64),
        "refund_reason": _safe_text(refund_order.get("refundReason"), 500),
        "cs_status": _safe_text(refund_order.get("csStatus"), 64),
        "logistics_company": _safe_text(refund_order.get("logisticsCompany"), 128),
        "logistics_mail_no": _safe_text(refund_order.get("logisticsMailNo"), 128),
        "consign_time": _safe_datetime(refund_order.get("consignTime")),
        "refund_create_time": _safe_datetime(refund_order.get("refundCreateTime")),
        "common_create_time": _safe_datetime(main.get("createTime") or biz_order.get("createTime")),
        "buyer_nick": _safe_text(buyer.get("nick"), 255),
        "right_buttons_json": json.dumps(buttons_serialized, ensure_ascii=False) if buttons_serialized else None,
        "ext_total_refund_fee": _safe_decimal(raw.get("extTotalRefundFee") or biz_order.get("extTotalRefundFee")),
        "raw_json": json.dumps(raw, ensure_ascii=False),
    }


async def _upsert_refund(db: AsyncSession, account_id: int, parsed: dict) -> str:
    """upsert 一条退款记录到 xianyu_refund 表。

    Returns:
        "inserted" / "updated" / "skipped"
    """
    external_refund_id = parsed.get("external_refund_id")
    if not external_refund_id:
        return "skipped"

    # 查询是否已有记录
    existing = await db.execute(
        select(models.XianyuRefund).where(
            models.XianyuRefund.account_id == account_id,
            models.XianyuRefund.external_refund_id == external_refund_id,
            models.XianyuRefund.deleted == 0,
        )
    )
    existing_row = existing.scalar_one_or_none()

    # 准备更新字段（排除主键和创建时间）
    update_fields = {
        "external_order_id": parsed.get("external_order_id"),
        "external_item_id": parsed.get("external_item_id"),
        "item_title": parsed.get("item_title"),
        "item_pic_url": parsed.get("item_pic_url"),
        "item_info_lines": parsed.get("item_info_lines"),
        "buy_num": parsed.get("buy_num"),
        "refund_fee": parsed.get("refund_fee"),
        "auction_price": parsed.get("auction_price"),
        "order_status": parsed.get("order_status"),
        "order_simple_remark": parsed.get("order_simple_remark"),
        "refund_status": parsed.get("refund_status"),
        "refund_status_desc": parsed.get("refund_status_desc"),
        "common_refund_status": parsed.get("common_refund_status"),
        "refund_reason": parsed.get("refund_reason"),
        "cs_status": parsed.get("cs_status"),
        "logistics_company": parsed.get("logistics_company"),
        "logistics_mail_no": parsed.get("logistics_mail_no"),
        "consign_time": parsed.get("consign_time"),
        "refund_create_time": parsed.get("refund_create_time"),
        "common_create_time": parsed.get("common_create_time"),
        "buyer_nick": parsed.get("buyer_nick"),
        "right_buttons_json": parsed.get("right_buttons_json"),
        "ext_total_refund_fee": parsed.get("ext_total_refund_fee"),
        "raw_json": parsed.get("raw_json"),
        "sync_status": "synced",
        "last_synced_time": datetime.datetime.now(),
        "updated_time": datetime.datetime.now(),
    }

    if existing_row:
        await db.execute(
            update(models.XianyuRefund)
            .where(models.XianyuRefund.id == existing_row.id)
            .values(**update_fields)
        )
        return "updated"

    new_row = models.XianyuRefund(
        account_id=account_id,
        **update_fields,
    )
    db.add(new_row)
    return "inserted"


async def sync_refunds_for_account(
    db: AsyncSession,
    account_id: int,
    max_pages: int = MAX_PAGES_PER_SYNC,
) -> dict:
    """同步指定账号的退款订单。

    拉取所有 dispute_status 的退款订单并写入 xianyu_refund 表。
    """
    total_new = 0
    total_updated = 0
    total_failed = 0
    first_error: Optional[str] = None

    for dispute_status in REFUND_DISPUTE_STATUSES:
        for page_num in range(1, max_pages + 1):
            try:
                result = await asyncio.to_thread(
                    fetch_refund_orders_page,
                    account_id,
                    dispute_status,
                    page_num,
                    REFUND_PAGE_SIZE,
                )
                if not result or not result.get("success"):
                    error_msg = (result or {}).get("error") or "退款接口调用失败"
                    total_failed += 1
                    if first_error is None:
                        first_error = error_msg
                    logger.warning(
                        "拉取退款失败 account=%s status=%s page=%s err=%s",
                        account_id, dispute_status, page_num, error_msg,
                    )
                    break  # 当前 status 出错则跳出，继续下一个 status

                items = (result.get("data") or {}).get("items") or []
                if not items:
                    break  # 无更多数据

                for raw in items:
                    try:
                        parsed = _parse_refund_item(raw or {})
                        action = await _upsert_refund(db, account_id, parsed)
                        if action == "inserted":
                            total_new += 1
                        elif action == "updated":
                            total_updated += 1
                    except Exception:
                        total_failed += 1
                        logger.warning("解析/写入退款记录失败 account=%s", account_id, exc_info=True)

                await db.commit()

                if len(items) < REFUND_PAGE_SIZE:
                    break  # 已到最后一页
            except Exception:
                total_failed += 1
                logger.warning(
                    "拉取退款异常 account=%s status=%s page=%s",
                    account_id, dispute_status, page_num,
                    exc_info=True,
                )
                break

    # 更新账号同步状态
    await _update_account_state(
        db,
        account_id,
        status="success" if total_failed == 0 else "partial",
        total_count=total_new + total_updated,
        error=first_error,
    )

    return {
        "account_id": account_id,
        "new_count": total_new,
        "updated_count": total_updated,
        "failed_count": total_failed,
        "status": "success" if total_failed == 0 else "partial",
        "error": first_error,
    }


async def _update_account_state(
    db: AsyncSession,
    account_id: int,
    status: str,
    total_count: int,
    error: Optional[str],
) -> None:
    """更新 xianyu_refund_account_state 表的同步状态。"""
    try:
        existing = await db.execute(
            select(models.XianyuRefundAccountState).where(
                models.XianyuRefundAccountState.account_id == account_id,
            )
        )
        row = existing.scalar_one_or_none()
        now = datetime.datetime.now()
        if row:
            row.last_sync_time = now
            row.last_sync_status = status
            row.last_sync_error = error[:500] if error else None
            row.last_total_count = total_count
            row.is_syncing = 0
        else:
            new_state = models.XianyuRefundAccountState(
                account_id=account_id,
                last_sync_time=now,
                last_sync_status=status,
                last_sync_error=error[:500] if error else None,
                last_total_count=total_count,
                is_syncing=0,
            )
            db.add(new_state)
        await db.commit()
    except Exception:
        logger.warning("更新退款账号状态失败 account=%s", account_id, exc_info=True)


async def list_refunds(
    db: AsyncSession,
    page_num: int = 1,
    page_size: int = 20,
    account_id: Optional[int] = None,
    refund_status: Optional[str] = None,
    order_status: Optional[str] = None,
    keyword: Optional[str] = None,
) -> dict:
    """查询本地退款记录列表（分页）。"""
    query = select(models.XianyuRefund).where(models.XianyuRefund.deleted == 0)
    if account_id:
        query = query.where(models.XianyuRefund.account_id == int(account_id))
    if refund_status:
        query = query.where(models.XianyuRefund.refund_status == refund_status)
    if order_status:
        query = query.where(models.XianyuRefund.order_status == order_status)
    if keyword:
        like = f"%{keyword}%"
        query = query.where(
            models.XianyuRefund.item_title.like(like)
            | models.XianyuRefund.buyer_nick.like(like)
            | models.XianyuRefund.external_order_id.like(like)
            | models.XianyuRefund.external_refund_id.like(like)
        )

    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    offset = max(page_num - 1, 0) * page_size
    query = query.order_by(desc(models.XianyuRefund.refund_create_time), desc(models.XianyuRefund.id)).offset(offset).limit(page_size)
    result = await db.execute(query)
    rows = result.scalars().all()

    records = [_refund_to_vo(r) for r in rows]
    pages = (total + page_size - 1) // page_size if total > 0 else 0
    return {
        "records": records,
        "total": total,
        "page_num": page_num,
        "page_size": page_size,
        "pages": pages,
    }


def _refund_to_vo(row: models.XianyuRefund) -> dict:
    """将退款记录转换为前端展示用 VO。"""
    return {
        "id": row.id,
        "account_id": row.account_id,
        "external_refund_id": row.external_refund_id,
        "external_order_id": row.external_order_id,
        "external_item_id": row.external_item_id,
        "item_title": row.item_title,
        "item_pic_url": row.item_pic_url,
        "item_info_lines": row.item_info_lines,
        "buy_num": row.buy_num,
        "refund_fee": float(row.refund_fee) if row.refund_fee is not None else None,
        "auction_price": float(row.auction_price) if row.auction_price is not None else None,
        "order_status": row.order_status,
        "order_simple_remark": row.order_simple_remark,
        "refund_status": row.refund_status,
        "refund_status_desc": row.refund_status_desc,
        "common_refund_status": row.common_refund_status,
        "refund_reason": row.refund_reason,
        "cs_status": row.cs_status,
        "logistics_company": row.logistics_company,
        "logistics_mail_no": row.logistics_mail_no,
        "consign_time": row.consign_time.isoformat() if row.consign_time else None,
        "refund_create_time": row.refund_create_time.isoformat() if row.refund_create_time else None,
        "common_create_time": row.common_create_time.isoformat() if row.common_create_time else None,
        "buyer_nick": row.buyer_nick,
        "right_buttons": json.loads(row.right_buttons_json) if row.right_buttons_json else [],
        "ext_total_refund_fee": float(row.ext_total_refund_fee) if row.ext_total_refund_fee is not None else None,
        "sync_status": row.sync_status,
        "last_synced_time": row.last_synced_time.isoformat() if row.last_synced_time else None,
        "created_time": row.created_time.isoformat() if row.created_time else None,
        "updated_time": row.updated_time.isoformat() if row.updated_time else None,
    }


async def get_refund_detail(db: AsyncSession, refund_id: int) -> Optional[dict]:
    """查询单条退款详情（含原始 raw_json）。"""
    result = await db.execute(
        select(models.XianyuRefund).where(
            models.XianyuRefund.id == refund_id,
            models.XianyuRefund.deleted == 0,
        )
    )
    row = result.scalar_one_or_none()
    if not row:
        return None
    vo = _refund_to_vo(row)
    try:
        vo["raw"] = json.loads(row.raw_json) if row.raw_json else None
    except (TypeError, ValueError):
        vo["raw"] = None
    return vo


async def get_refund_stats(db: AsyncSession, account_id: Optional[int] = None) -> dict:
    """退款统计：按 order_status 分组计数与金额合计。"""
    query = select(
        models.XianyuRefund.order_status,
        func.count(models.XianyuRefund.id),
        func.sum(models.XianyuRefund.refund_fee),
    ).where(models.XianyuRefund.deleted == 0)
    if account_id:
        query = query.where(models.XianyuRefund.account_id == int(account_id))
    query = query.group_by(models.XianyuRefund.order_status)
    result = await db.execute(query)
    rows = result.all()
    breakdown = []
    total_count = 0
    total_fee = 0.0
    for order_status, count, fee in rows:
        count = int(count or 0)
        fee = float(fee or 0)
        breakdown.append({
            "order_status": order_status or "unknown",
            "count": count,
            "total_fee": fee,
        })
        total_count += count
        total_fee += fee
    return {
        "total_count": total_count,
        "total_fee": round(total_fee, 4),
        "breakdown": breakdown,
    }
