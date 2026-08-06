"""
评价管理路由（开源版）。

仅用于鱼小铺账号（xianyu_account.fish_shop_user=1）。普通闲鱼账号不调用评价接口。

路由：
- GET  /rates                   查询本地缓存的评价列表（支持 accountId/category/keyword/page/pageSize）
- POST /rates/sync              触发同步（单账号或全部鱼小铺账号）
- GET  /rates/sync-status       查询同步状态（缓存是否过期、是否正在同步）
- GET  /rates/overview          查询概览统计
- POST /rates/create            创建评价（写操作，需多重校验）
- GET  /rates/fish-shop-accounts 列出鱼小铺账号（用于下拉框）
- GET  /rates/auto-rate/logs    自动评价执行日志
- GET  /rates/auto-rate/scheduler-status  调度器状态
- POST /rates/auto-rate/run     手动触发自动评价
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.database import get_db
from ....core.response import ResultObject
from ....services.rate_service import (
    SUPPORTED_CATEGORIES,
    query_local_rates,
    sync_rates_for_account,
    sync_all_rates,
    get_sync_status,
    get_rate_overview,
    create_rate,
    list_fish_shop_accounts,
)
from ....services.auto_rate_scheduler import (
    get_scheduler_status as get_auto_rate_scheduler_status,
    list_auto_rate_logs,
    run_auto_rate_for_account,
)
from ..deps import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/rates")


@router.get("")
async def list_rates(
    accountId: Optional[int] = Query(None, description="账号ID，不传则聚合全部鱼小铺账号"),
    category: str = Query("all", description="评价分类：all/pending/done"),
    keyword: Optional[str] = Query(None, description="关键词搜索：订单号/商品ID/商品标题/买家昵称"),
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """查询本地缓存的评价列表（多账号聚合 + 分类筛选 + 关键词搜索 + 分页）。

    本接口仅查询本地数据库，不触发闲鱼请求。
    """
    del current_user
    if category not in SUPPORTED_CATEGORIES:
        return ResultObject.failed(f"不支持的评价分类：{category}", code=400)
    try:
        result = await query_local_rates(
            db,
            account_id=accountId,
            category=category,
            keyword=keyword,
            page=page,
            page_size=pageSize,
        )
        return ResultObject.success(result)
    except Exception as exc:
        logger.exception("查询评价列表失败")
        return ResultObject.failed(f"查询评价列表失败: {type(exc).__name__}")


@router.post("/sync")
async def trigger_sync(
    body: Optional[dict] = None,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """触发评价同步。

    body:
        {"accountId": 123}            同步单个账号
        {} 或不传                       同步全部鱼小铺账号
        {"forceFull": true}            强制全量同步（忽略缓存，拉取所有页）
        {"accountId": 123, "forceFull": true}
    """
    del current_user
    account_id = None
    force_full = False
    if body and isinstance(body, dict):
        account_id = body.get("accountId")
        if account_id is not None:
            try:
                account_id = int(account_id)
            except (TypeError, ValueError):
                return ResultObject.failed("accountId 参数无效", code=400)
        # forceFull 必须为明确布尔值
        raw_force_full = body.get("forceFull")
        if isinstance(raw_force_full, bool):
            force_full = raw_force_full
        elif isinstance(raw_force_full, (int, float)):
            force_full = raw_force_full != 0
        elif isinstance(raw_force_full, str):
            force_full = raw_force_full.strip().lower() in ("true", "1", "yes", "y")

    try:
        if account_id is not None:
            result = await sync_rates_for_account(db, account_id, force_full=force_full)
        else:
            result = await sync_all_rates(db, force_full=force_full)
    except Exception as exc:
        logger.exception("触发评价同步失败 accountId=%s", account_id)
        return ResultObject.failed(f"触发同步失败: {type(exc).__name__}")

    if not result.get("ok"):
        if result.get("error") == "TASK_ALREADY_RUNNING":
            return ResultObject.success(
                {"syncId": result.get("syncId"), "alreadyRunning": True},
                message="该账号正在同步中，请稍后刷新查看",
            )
        return ResultObject.failed(result.get("error") or "同步失败")

    return ResultObject.success(result, message="同步已触发")


@router.get("/sync-status")
async def get_sync_status_endpoint(
    accountId: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """查询同步状态（缓存是否过期、是否正在同步、最后同步时间）。"""
    del current_user
    try:
        result = await get_sync_status(db, account_id=accountId)
        return ResultObject.success(result)
    except Exception as exc:
        logger.exception("查询评价同步状态失败")
        return ResultObject.failed(f"查询同步状态失败: {type(exc).__name__}")


@router.get("/overview")
async def get_overview_endpoint(
    accountId: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """查询评价概览统计（总数、待评价、已评价、最近同步时间）。"""
    del current_user
    try:
        result = await get_rate_overview(db, account_id=accountId)
        return ResultObject.success(result)
    except Exception as exc:
        logger.exception("查询评价概览失败")
        return ResultObject.failed(f"查询评价概览失败: {type(exc).__name__}")


@router.post("/create")
async def create_rate_endpoint(
    body: Optional[dict] = None,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """创建评价（写操作）。

    body:
        {
            "accountId": 123,
            "orderId": "订单ID",
            "rate": 1,            # 评价等级（好评=1、中评=-1、差评=0）
            "feedback": "评价内容",
            "anonymous": true
        }

    安全要求：
    - 后端再次校验账号归属与鱼小铺权限
    - 校验 orderId 属于该账号
    - 不接受前端传入任意 Cookie
    - 同一账号同一订单同时只能存在一个评价请求
    """
    del current_user
    if not body or not isinstance(body, dict):
        return ResultObject.failed("请求参数不能为空", code=400)

    account_id = body.get("accountId")
    order_id = body.get("orderId")
    rate = body.get("rate")
    feedback = body.get("feedback")
    anonymous = body.get("anonymous")

    if account_id is None:
        return ResultObject.failed("accountId 不能为空", code=400)
    try:
        account_id = int(account_id)
    except (TypeError, ValueError):
        return ResultObject.failed("accountId 参数无效", code=400)

    if not order_id or not isinstance(order_id, str):
        return ResultObject.failed("orderId 不能为空且必须为字符串", code=400)

    if rate is None:
        return ResultObject.failed("rate 不能为空", code=400)
    try:
        rate = int(rate)
    except (TypeError, ValueError):
        return ResultObject.failed("rate 参数无效", code=400)

    if feedback is None or not isinstance(feedback, str):
        return ResultObject.failed("feedback 不能为空", code=400)

    if anonymous is None or not isinstance(anonymous, bool):
        return ResultObject.failed("anonymous 必须为明确布尔值", code=400)

    try:
        result = await create_rate(
            db, account_id, order_id, rate, feedback, anonymous
        )
    except Exception as exc:
        logger.exception(
            "创建评价异常 accountId=%s orderId=%s",
            account_id, order_id,
        )
        return ResultObject.failed(f"创建评价异常: {type(exc).__name__}")

    if not result.get("ok"):
        if result.get("error") == "CREATE_RATE_IN_PROGRESS":
            return ResultObject.success(
                {"alreadyInProgress": True},
                message="该订单正在提交评价，请稍后",
            )
        return ResultObject.failed(result.get("error") or "创建评价失败")

    return ResultObject.success(result.get("data") or {}, message=result.get("message") or "评价已提交")


@router.get("/fish-shop-accounts")
async def list_fish_shop_accounts_endpoint(
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """列出所有鱼小铺账号（用于前端账号选择下拉框）。

    普通闲鱼账号不会出现在此列表中（前端据此判断是否显示"不支持评价管理"提示）。
    """
    del current_user
    try:
        accounts = await list_fish_shop_accounts(db)
        return ResultObject.success({"accounts": accounts})
    except Exception as exc:
        logger.exception("查询鱼小铺账号列表失败")
        return ResultObject.failed(f"查询鱼小铺账号列表失败: {type(exc).__name__}")


# ============================================================
# 自动补评价：执行日志查询 + 手动触发 + 调度器状态
# ============================================================

@router.get("/auto-rate/logs")
async def list_auto_rate_logs_endpoint(
    accountId: Optional[int] = Query(None, description="账号ID，不传则查询全部账号"),
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """查询自动补评价执行日志（分页）。"""
    del current_user
    try:
        result = await list_auto_rate_logs(
            db,
            account_id=accountId,
            page=page,
            page_size=pageSize,
        )
        return ResultObject.success(result)
    except Exception as exc:
        logger.exception("查询自动评价日志失败")
        return ResultObject.failed(f"查询自动评价日志失败: {type(exc).__name__}")


@router.get("/auto-rate/scheduler-status")
async def get_auto_rate_scheduler_status_endpoint(
    current_user: dict = Depends(get_current_user),
):
    """查询自动补评价调度器运行状态（用于诊断调度是否正常）。"""
    del current_user
    try:
        return ResultObject.success(get_auto_rate_scheduler_status())
    except Exception as exc:
        logger.exception("查询自动评价调度器状态失败")
        return ResultObject.failed(f"查询调度器状态失败: {type(exc).__name__}")


@router.post("/auto-rate/run")
async def trigger_auto_rate_run_endpoint(
    body: Optional[dict] = None,
    current_user: dict = Depends(get_current_user),
):
    """手动触发单个账号的自动补评价（立即执行一次）。

    body:
        {"accountId": 123}

    执行前会再次校验：
    - 该账号已配置自动评价
    - 该账号已开启自动评价
    - 账号为鱼小铺账号且 Cookie 有效
    - 评价内容已配置
    """
    del current_user
    if not body or not isinstance(body, dict):
        return ResultObject.failed("请求参数不能为空", code=400)
    account_id = body.get("accountId")
    if account_id is None:
        return ResultObject.failed("accountId 不能为空", code=400)
    try:
        account_id = int(account_id)
    except (TypeError, ValueError):
        return ResultObject.failed("accountId 参数无效", code=400)

    try:
        result = await run_auto_rate_for_account(account_id, trigger_type="manual")
    except Exception as exc:
        logger.exception(
            "手动触发自动评价异常 accountId=%s", account_id
        )
        return ResultObject.failed(f"手动触发自动评价异常: {type(exc).__name__}")

    if not result.get("ok"):
        err = result.get("error") or "触发失败"
        # RUN_IN_PROGRESS 视为已触发
        if err == "RUN_IN_PROGRESS":
            return ResultObject.success(
                {"alreadyInProgress": True},
                message="该账号正在执行自动评价，请稍后查看日志",
            )
        return ResultObject.failed(err)

    summary = result.get("summary") or {}
    status = summary.get("status")
    if status == "skip":
        return ResultObject.success(
            summary,
            message=f"已跳过：{summary.get('reason', '未知原因')}",
        )
    if status == "failed":
        return ResultObject.failed(
            summary.get("error") or "自动评价执行失败",
        )
    # success / partial
    return ResultObject.success(
        summary,
        message=(
            f"自动评价完成：成功 {summary.get('totalSuccess', 0)} / "
            f"失败 {summary.get('totalFailed', 0)} / "
            f"跳过 {summary.get('totalSkipped', 0)}"
        ),
    )
