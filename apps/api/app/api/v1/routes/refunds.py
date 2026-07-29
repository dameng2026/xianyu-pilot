"""退款管理路由。

提供退款列表查询、单条详情、统计、同步能力。
- GET  /refund/list              退款分页列表（支持账号、状态、关键词筛选）
- GET  /refund/stats             退款统计（按 order_status 分组）
- GET  /refund/{refund_id}       退款详情
- POST /refund/sync              同步指定账号或全部账号的退款订单
- GET  /refund/account-states    账号同步状态列表
"""
from __future__ import annotations

import datetime
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.database import get_db
from ....core.response import ResultObject
from ....models.entities import XianyuAccount, XianyuRefundAccountState
from ....services.refund_service import (
    get_refund_detail,
    get_refund_stats,
    list_refunds,
    sync_refunds_for_account,
)
from ..deps import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/refund", tags=["refund"])


class RefundSyncReqDTO(BaseModel):
    account_id: Optional[int] = None
    scope: str = "single"  # single / all


@router.get("/list", response_model=ResultObject)
async def refund_list(
    page_num: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    account_id: Optional[int] = Query(None),
    refund_status: Optional[str] = Query(None),
    order_status: Optional[str] = Query(None),
    keyword: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """查询本地退款记录列表（分页）。"""
    try:
        data = await list_refunds(
            db,
            page_num=page_num,
            page_size=page_size,
            account_id=account_id,
            refund_status=refund_status,
            order_status=order_status,
            keyword=keyword,
        )
        return ResultObject.success(data)
    except Exception:
        logger.error("查询退款列表失败", exc_info=True)
        return ResultObject.internal_error()


@router.get("/stats", response_model=ResultObject)
async def refund_stats(
    account_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """退款统计：按 order_status 分组计数与金额合计。"""
    try:
        data = await get_refund_stats(db, account_id=account_id)
        return ResultObject.success(data)
    except Exception:
        logger.error("查询退款统计失败", exc_info=True)
        return ResultObject.internal_error()


@router.get("/account-states", response_model=ResultObject)
async def refund_account_states(
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """获取所有账号的退款同步状态。"""
    try:
        result = await db.execute(
            select(XianyuRefundAccountState).where(XianyuRefundAccountState.deleted == 0)
        )
        states = result.scalars().all()

        accounts_result = await db.execute(
            select(XianyuAccount.id, XianyuAccount.nickname).where(XianyuAccount.deleted == 0)
        )
        accounts_map = {row[0]: row[1] for row in accounts_result.all()}

        records = []
        for state in states:
            records.append({
                "account_id": state.account_id,
                "account_name": accounts_map.get(state.account_id, f"账号{state.account_id}"),
                "last_sync_time": state.last_sync_time.isoformat() if state.last_sync_time else None,
                "last_sync_status": state.last_sync_status,
                "last_sync_error": state.last_sync_error,
                "last_total_count": state.last_total_count,
                "is_syncing": bool(state.is_syncing),
                "sync_started_time": state.sync_started_time.isoformat() if state.sync_started_time else None,
                "last_full_sync_time": state.last_full_sync_time.isoformat() if state.last_full_sync_time else None,
            })
        return ResultObject.success({"records": records, "total": len(records)})
    except Exception:
        logger.error("查询退款账号状态失败", exc_info=True)
        return ResultObject.internal_error()


@router.get("/{refund_id}", response_model=ResultObject)
async def refund_detail(
    refund_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """查询单条退款详情。"""
    try:
        data = await get_refund_detail(db, refund_id=refund_id)
        if data is None:
            return ResultObject.failed("退款记录不存在", code=404)
        return ResultObject.success(data)
    except Exception:
        logger.error("查询退款详情失败 refundId=%s", refund_id, exc_info=True)
        return ResultObject.internal_error()


@router.post("/sync", response_model=ResultObject)
async def refund_sync(
    req: RefundSyncReqDTO,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """同步指定账号或全部账号的退款订单。

    - scope=single: 同步 account_id 指定的账号
    - scope=all:   同步全部有效账号
    """
    try:
        if req.scope == "all":
            accounts_result = await db.execute(
                select(XianyuAccount.id).where(
                    XianyuAccount.deleted == 0,
                    XianyuAccount.status == 1,
                )
            )
            account_ids = [row[0] for row in accounts_result.all()]
        else:
            if not req.account_id:
                return ResultObject.validate_failed("account_id 不能为空")
            account_ids = [int(req.account_id)]

        if not account_ids:
            return ResultObject.failed("没有可同步的账号")

        # 先标记为同步中
        now = datetime.datetime.now()
        for account_id in account_ids:
            state_row = await db.execute(
                select(XianyuRefundAccountState).where(
                    XianyuRefundAccountState.account_id == account_id,
                )
            )
            state = state_row.scalar_one_or_none()
            if state:
                state.is_syncing = 1
                state.sync_started_time = now
            else:
                db.add(XianyuRefundAccountState(
                    account_id=account_id,
                    is_syncing=1,
                    sync_started_time=now,
                ))
        await db.commit()

        # 执行同步（同步函数内部会自己管理事务）
        results = []
        total_new = 0
        total_updated = 0
        total_failed = 0
        for account_id in account_ids:
            try:
                result = await sync_refunds_for_account(db, account_id=account_id)
                results.append(result)
                total_new += result.get("new_count", 0)
                total_updated += result.get("updated_count", 0)
                total_failed += result.get("failed_count", 0)
            except Exception as exc:
                logger.warning("账号退款同步失败 account=%s err=%s", account_id, exc)
                results.append({
                    "account_id": account_id,
                    "new_count": 0,
                    "updated_count": 0,
                    "failed_count": 1,
                    "status": "failed",
                    "error": str(exc)[:500],
                })
                total_failed += 1

        return ResultObject.success({
            "sync_id": uuid.uuid4().hex,
            "scope": req.scope,
            "account_count": len(account_ids),
            "new_count": total_new,
            "updated_count": total_updated,
            "failed_count": total_failed,
            "results": results,
            "status": "success" if total_failed == 0 else "partial",
        })
    except Exception:
        logger.error("同步退款失败", exc_info=True)
        return ResultObject.internal_error()
