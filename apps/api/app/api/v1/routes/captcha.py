"""
滑块验证处理路由
================
提供前端调用的滑块验证接口：
- POST /captcha/detect   检测 API 响应是否需要滑块验证
- POST /captcha/instructions   获取操作指引
- POST /captcha/auto-solve   调用 Playwright 自动求解（统一走 handle 综合处理，落库记录 + SSE 广播）
- POST /captcha/handle   综合处理：检测 + 通知 + 自动求解
- GET  /captcha/records  分页查询滑块求解记录

单租户版：无 tenant_id，API 直接通过 /api/captcha/... 暴露（无 Java 网关拆包）。
"""
import logging
from typing import Optional
from fastapi import APIRouter, Depends

from ....core.response import ResultObject
from ..deps import get_current_user
from ....services.captcha_solver import (
    detect_captcha_from_response,
    build_captcha_instructions,
    handle_captcha_for_account,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/captcha", tags=["captcha"])


@router.post("/detect", response_model=ResultObject[dict])
async def detect_captcha(
    data: dict = {},
    current_user: dict = Depends(get_current_user),
):
    """检测 API 响应是否包含滑块验证需求。

    请求体: {"response": <dict 或 str>, "accountId": 1}
    """
    try:
        response = data.get("response")
        account_id = data.get("accountId")

        result = detect_captcha_from_response(response)
        return ResultObject.success({
            "detected": result.detected,
            "captchaUrl": result.captcha_url,
            "reason": result.reason,
            "accountId": account_id,
        })
    except Exception as exc:
        logger.error("滑块检测失败 errorType=%s", type(exc).__name__)
        return ResultObject.internal_error()


@router.post("/instructions", response_model=ResultObject[dict])
async def get_instructions(
    data: dict = {},
    current_user: dict = Depends(get_current_user),
):
    """获取滑块验证操作指引。

    请求体: {"accountId": 1, "captchaUrl": "https://...", "accountName": "xxx"}
    """
    try:
        account_id = int(data.get("accountId") or 0)
        captcha_url = data.get("captchaUrl")
        account_name = data.get("accountName")

        instructions = build_captcha_instructions(account_id, captcha_url, account_name)
        return ResultObject.success({
            "accountId": instructions.account_id,
            "captchaUrl": instructions.captcha_url,
            "title": instructions.title,
            "steps": instructions.steps,
            "message": instructions.message,
            "autoSolveAvailable": instructions.auto_solve_available,
            "manualFallbackUrl": instructions.manual_fallback_url,
        })
    except Exception as exc:
        logger.error("获取滑块指引失败 errorType=%s", type(exc).__name__)
        return ResultObject.internal_error()


@router.post("/auto-solve", response_model=ResultObject[dict])
async def auto_solve_captcha(
    data: dict = {},
    current_user: dict = Depends(get_current_user),
):
    """调用 Playwright 自动求解滑块，并写入滑块求解记录。

    请求体: {
        "accountId": 1,
        "targetUrl": "https://www.goofish.com/",  # 可选
        "headless": false,  # 可选，默认 false（前台手动求解时弹出浏览器窗口便于观察）
        "maxRetries": 3,    # 可选
        "triggerScene": "manual",
        "openReason": "",
        "solveReason": ""
    }

    说明：统一走 handle_captcha_for_account(auto_solve=True)，保证每次求解
    （手动/重试/自动）都落库到 xianyu_captcha_solve_record，可在记录页查看。

    headless 字段说明：
    - 未传（None）：使用 try_auto_solve 默认（True，无头模式，不弹窗），适用于自动触发场景
    - false：有头模式，会弹出 Chrome 窗口，适用于前台手动求解
    - true：强制无头模式
    注意：crawler-service 容器的 CRAWLER_FORCE_HEADLESS=true 会强制覆盖此参数。
    """
    try:
        account_id = int(data.get("accountId") or 0)
        if not account_id:
            return ResultObject.validate_failed("accountId 不能为空")

        trigger_scene = str(data.get("triggerScene") or "manual")
        open_reason = str(data.get("openReason") or "")
        solve_reason = str(data.get("solveReason") or "")

        # headless 字段：未传时为 None（沿用默认无头），传 false 时弹窗
        headless_value = data.get("headless")
        headless: Optional[bool] = None
        if headless_value is not None:
            headless = bool(headless_value)

        # 统一走综合处理：创建记录 + 求解 + 更新记录 + SSE
        handled = await handle_captcha_for_account(
            account_id=account_id,
            response=None,
            auto_solve=True,
            trigger_scene=trigger_scene,
            open_reason=open_reason,
            solve_reason=solve_reason,
            headless=headless,
        )
        result = handled.get("autoSolveResult") or {}
        if not result.get("success") and not result.get("solved"):
            return ResultObject.failed(
                result.get("error") or "滑块自动求解暂不可用，请按人工指引处理",
                503,
            )
        # 附带 handle 元信息，便于前端判断 recovered
        result = {
            **result,
            "recovered": bool(handled.get("recovered")),
            "detected": bool(handled.get("detected")),
        }
        return ResultObject.success(result)
    except Exception as exc:
        logger.error("自动求解滑块失败 errorType=%s", type(exc).__name__)
        return ResultObject.internal_error()


@router.post("/handle", response_model=ResultObject[dict])
async def handle_captcha(
    data: dict = {},
    current_user: dict = Depends(get_current_user),
):
    """综合处理滑块验证场景：检测 + 通知 + 自动求解。

    请求体: {
        "accountId": 1,
        "response": <dict 或 str>,    # 可选，用于检测
        "autoSolve": true,              # 是否自动求解
        "headless": false,              # 可选，默认 false（前台手动求解弹窗，便于观察）
        "triggerScene": "manual",       # 触发场景
        "openReason": "",               # 开启原因（为什么打开滑块求解流程）
        "solveReason": ""               # 求解原因（为什么进行滑块求解）
    }

    headless 字段说明：
    - 未传（None）：使用 try_auto_solve 默认（True，无头模式，不弹窗），适用于自动触发场景
    - false：有头模式，会弹出 Chrome 窗口，适用于前台手动求解
    - true：强制无头模式
    注意：crawler-service 容器的 CRAWLER_FORCE_HEADLESS=true 会强制覆盖此参数。
    """
    try:
        account_id = int(data.get("accountId") or 0)
        response = data.get("response")
        auto_solve = bool(data.get("autoSolve", False))
        trigger_scene = str(data.get("triggerScene") or "manual")
        open_reason = str(data.get("openReason") or "")
        solve_reason = str(data.get("solveReason") or "")

        # headless 字段：未传时为 None（沿用默认无头），传 false 时弹窗
        headless_value = data.get("headless")
        headless: Optional[bool] = None
        if headless_value is not None:
            headless = bool(headless_value)

        if not account_id:
            return ResultObject.validate_failed("accountId 不能为空")

        result = await handle_captcha_for_account(
            account_id=account_id,
            response=response,
            auto_solve=auto_solve,
            trigger_scene=trigger_scene,
            open_reason=open_reason,
            solve_reason=solve_reason,
            headless=headless,
        )
        return ResultObject.success(result)
    except Exception as exc:
        logger.error("综合处理滑块失败 errorType=%s", type(exc).__name__)
        return ResultObject.internal_error()


@router.get("/records", response_model=ResultObject[dict])
async def list_captcha_records(
    page: int = 1,
    pageSize: int = 20,
    accountId: int = 0,
    status: str = "",
    triggerScene: str = "",
    current_user: dict = Depends(get_current_user),
):
    """分页查询滑块求解记录。

    查询参数:
        page: 页码（默认1）
        pageSize: 每页条数（默认20）
        accountId: 账号ID筛选（可选）
        status: 状态筛选（可选: retrying/success/fail）
        triggerScene: 触发场景筛选（可选: manual/manual_retry/ws_connect/cookie_keepalive/token_refresh）
    """
    try:
        from ....services.captcha_solve_record import list_solve_records
        result = await list_solve_records(
            account_id=accountId,
            status=status,
            trigger_scene=triggerScene,
            page=max(1, page),
            page_size=min(100, max(1, pageSize)),
        )
        return ResultObject.success(result)
    except Exception as exc:
        logger.error("查询滑块记录失败 errorType=%s", type(exc).__name__)
        return ResultObject.internal_error()


# ============================================================
# 远程滑块求解记录
# ============================================================

@router.get("/remote-solve-records", response_model=ResultObject[dict])
async def list_remote_solve_records_route(
    page: int = 1,
    pageSize: int = 20,
    status: str = "",
    keyword: str = "",
    current_user: dict = Depends(get_current_user),
):
    """分页查询远程滑块求解记录。"""
    try:
        from ....services.remote_slider_record import list_remote_solve_records
        result = await list_remote_solve_records(
            page=max(1, page),
            page_size=min(100, max(1, pageSize)),
            status=status,
            keyword=keyword,
        )
        return ResultObject.success(result)
    except Exception as exc:
        logger.error("查询远程滑块记录失败 errorType=%s", type(exc).__name__)
        return ResultObject.internal_error()


@router.get("/remote-solve-stats", response_model=ResultObject[dict])
async def get_remote_solve_stats_route(
    days: int = 7,
    current_user: dict = Depends(get_current_user),
):
    """获取远程滑块求解统计数据。"""
    try:
        from ....services.remote_slider_record import get_remote_solve_stats
        result = await get_remote_solve_stats(days=max(1, min(90, days)))
        return ResultObject.success(result)
    except Exception as exc:
        logger.error("获取远程滑块统计失败 errorType=%s", type(exc).__name__)
        return ResultObject.internal_error()

# ============================================================
# 滑块求解活跃记录 & 重试（同步商业版前端调用约定）
# ============================================================

# 活跃状态定义：未到达终态的求解记录（success/fail 为终态）
ACTIVE_STATUSES = ("solving", "queued", "retrying", "timeout", "precheck_rejected")


@router.get("/solve-records", response_model=ResultObject[dict])
async def list_active_solve_records(
    accountId: int = 0,
    status: str = "",
    current_user: dict = Depends(get_current_user),
):
    """查询滑块求解记录（兼容商业版前端调用约定）。

    查询参数:
        accountId: 账号ID筛选（可选）
        status: 状态筛选（可选）。
                传 "active" 时返回所有未到达终态的活跃记录（solving/queued/retrying/timeout/precheck_rejected）；
                传其他具体状态值时按该状态精确过滤。
    """
    try:
        from sqlalchemy import text
        from ....core.database import async_session

        where_clauses = ["deleted = 0"]
        params: dict = {}

        if accountId:
            where_clauses.append("account_id = :aid")
            params["aid"] = int(accountId)

        if status == "active":
            placeholders = ",".join([f":s{i}" for i in range(len(ACTIVE_STATUSES))])
            where_clauses.append(f"status IN ({placeholders})")
            for i, s in enumerate(ACTIVE_STATUSES):
                params[f"s{i}"] = s
        elif status:
            where_clauses.append("status = :status")
            params["status"] = status

        where_sql = " AND ".join(where_clauses)

        async with async_session() as db:
            rows = (await db.execute(
                text(
                    "SELECT id, account_id, account_name, event_desc, open_reason, solve_reason, "
                    "trigger_scene, result, status, engine, retry_count, error_message, "
                    "created_at, updated_at "
                    f"FROM xianyu_captcha_solve_record WHERE {where_sql} "
                    "ORDER BY created_at DESC, id DESC LIMIT 100"
                ),
                params,
            )).mappings().all()

            items = []
            for row in rows:
                items.append({
                    "id": row["id"],
                    "accountId": row["account_id"],
                    "accountName": row["account_name"],
                    "eventDesc": row["event_desc"],
                    "openReason": row.get("open_reason") or "",
                    "solveReason": row.get("solve_reason") or "",
                    "triggerScene": row["trigger_scene"],
                    "result": row["result"],
                    "status": row["status"],
                    "engine": row["engine"],
                    "retryCount": row["retry_count"],
                    "errorMessage": row["error_message"],
                    "createdAt": str(row["created_at"]) if row["created_at"] else "",
                    "updatedAt": str(row["updated_at"]) if row["updated_at"] else "",
                })

            return ResultObject.success({"list": items, "total": len(items)})
    except Exception as exc:
        logger.error("查询活跃求解记录失败 errorType=%s", type(exc).__name__)
        return ResultObject.internal_error()


@router.post("/solve-retry", response_model=ResultObject[dict])
async def retry_captcha_solve(
    data: dict = {},
    current_user: dict = Depends(get_current_user),
):
    """重试滑块求解。

    请求体: {"accountId": 1, "recordId": 123 (可选)}
    行为：调用 handle_captcha_for_account 重新触发一次求解（triggerScene=manual_retry），
          落库新记录并通过 SSE 广播状态。
    """
    try:
        account_id = int(data.get("accountId") or 0)
        if not account_id:
            return ResultObject.validate_failed("accountId 不能为空")

        record_id = data.get("recordId")
        solve_reason = "用户在账号页面点击重试求解按钮"
        if record_id:
            solve_reason = f"{solve_reason}（关联记录 #{record_id}）"

        handled = await handle_captcha_for_account(
            account_id=account_id,
            response=None,
            auto_solve=True,
            trigger_scene="manual_retry",
            open_reason="用户点击重试求解按钮",
            solve_reason=solve_reason,
        )
        result = handled.get("autoSolveResult") or {}
        return ResultObject.success({
            "recovered": bool(handled.get("recovered")),
            "detected": bool(handled.get("detected")),
            "result": result,
            "message": result.get("error") or "重试求解已提交",
        })
    except Exception as exc:
        logger.error("重试滑块求解失败 errorType=%s", type(exc).__name__)
        return ResultObject.internal_error()
