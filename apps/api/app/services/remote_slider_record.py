"""
远程滑块求解记录服务
====================
提供远程滑块求解记录的创建与查询能力。
记录存储在 xianyu_remote_slider_solve_record 表。
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import text

from ..core.database import async_session

logger = logging.getLogger(__name__)


async def create_remote_solve_record(
    *,
    request_id: str | None = None,
    account_id: int | None = None,
    account_name: str | None = None,
    trigger_scene: str = "manual",
    status: str = "retrying",
    failure_reason: str = "",
    error_message: str | None = None,
    duration_ms: int = 0,
    token_charged: int = 0,
    remote_status: str = "",
    remote_solved: bool = False,
    client_ip: str = "",
) -> str:
    """Insert a remote slider solve record. Returns the request_id."""
    if not request_id:
        request_id = f"req_{uuid.uuid4().hex[:16]}"

    try:
        async with async_session() as db:
            await db.execute(
                text(
                    "INSERT INTO xianyu_remote_slider_solve_record "
                    "(request_id, account_id, account_name, trigger_scene, status, "
                    " failure_reason, error_message, duration_ms, token_charged, "
                    " remote_status, remote_solved, client_ip) "
                    "VALUES (:rid, :aid, :aname, :scene, :status, "
                    "        :freason, :emsg, :dur, :tokens, :rstatus, :rsolved, :cip)"
                ),
                {
                    "rid": request_id,
                    "aid": account_id,
                    "aname": account_name,
                    "scene": trigger_scene,
                    "status": status,
                    "freason": failure_reason,
                    "emsg": error_message,
                    "dur": duration_ms,
                    "tokens": token_charged,
                    "rstatus": remote_status,
                    "rsolved": 1 if remote_solved else 0,
                    "cip": client_ip,
                },
            )
            await db.commit()
    except Exception as exc:
        logger.error("Failed to create remote solve record: %s", type(exc).__name__)
    return request_id


async def update_remote_solve_record(
    request_id: str,
    *,
    status: str | None = None,
    failure_reason: str | None = None,
    error_message: str | None = None,
    duration_ms: int | None = None,
    token_charged: int | None = None,
    remote_status: str | None = None,
    remote_solved: bool | None = None,
    client_ip: str | None = None,
) -> None:
    """Update an existing remote solve record by request_id."""
    sets: list[str] = []
    params: dict[str, Any] = {"rid": request_id}
    if status is not None:
        sets.append("status = :status")
        params["status"] = status
    if failure_reason is not None:
        sets.append("failure_reason = :freason")
        params["freason"] = failure_reason
    if error_message is not None:
        sets.append("error_message = :emsg")
        params["emsg"] = error_message
    if duration_ms is not None:
        sets.append("duration_ms = :dur")
        params["dur"] = duration_ms
    if token_charged is not None:
        sets.append("token_charged = :tokens")
        params["tokens"] = token_charged
    if remote_status is not None:
        sets.append("remote_status = :rstatus")
        params["rstatus"] = remote_status
    if remote_solved is not None:
        sets.append("remote_solved = :rsolved")
        params["rsolved"] = 1 if remote_solved else 0
    if client_ip is not None:
        sets.append("client_ip = :cip")
        params["cip"] = client_ip
    if not sets:
        return
    sets.append("updated_at = NOW()")

    try:
        async with async_session() as db:
            await db.execute(
                text(
                    "UPDATE xianyu_remote_slider_solve_record SET "
                    + ", ".join(sets)
                    + " WHERE request_id = :rid"
                ),
                params,
            )
            await db.commit()
    except Exception as exc:
        logger.error("Failed to update remote solve record: %s", type(exc).__name__)


async def list_remote_solve_records(
    *,
    page: int = 1,
    page_size: int = 20,
    status: str = "",
    keyword: str = "",
) -> dict[str, Any]:
    """Paginated query of remote solve records."""
    page = max(1, page)
    page_size = min(100, max(1, page_size))
    offset = (page - 1) * page_size

    where_parts: list[str] = []
    params: dict[str, Any] = {}
    if status:
        where_parts.append("status = :status")
        params["status"] = status
    if keyword:
        where_parts.append("(request_id LIKE :kw OR error_message LIKE :kw OR account_name LIKE :kw)")
        params["kw"] = f"%{keyword}%"

    where_clause = (" WHERE " + " AND ".join(where_parts)) if where_parts else ""

    try:
        async with async_session() as db:
            count_row = (
                await db.execute(
                    text(f"SELECT COUNT(*) AS total FROM xianyu_remote_slider_solve_record{where_clause}"),
                    params,
                )
            ).mappings().first()
            total = int(count_row["total"]) if count_row else 0

            rows = (
                await db.execute(
                    text(
                        f"SELECT id, request_id, account_id, account_name, trigger_scene, "
                        f"status, failure_reason, error_message, duration_ms, token_charged, "
                        f"remote_status, remote_solved, client_ip, created_at, updated_at "
                        f"FROM xianyu_remote_slider_solve_record{where_clause} "
                        f"ORDER BY created_at DESC LIMIT :limit OFFSET :offset"
                    ),
                    {**params, "limit": page_size, "offset": offset},
                )
            ).mappings().all()

            list_data = []
            for row in rows:
                list_data.append({
                    "id": row["id"],
                    "requestId": row["request_id"],
                    "accountId": row["account_id"],
                    "accountName": row["account_name"],
                    "triggerScene": row["trigger_scene"],
                    "status": row["status"],
                    "failureReason": row["failure_reason"],
                    "errorMessage": row["error_message"],
                    "durationMs": row["duration_ms"],
                    "tokenCharged": row["token_charged"],
                    "remoteStatus": row["remote_status"],
                    "remoteSolved": bool(row["remote_solved"]),
                    "clientIp": row["client_ip"],
                    "createdAt": str(row["created_at"]) if row["created_at"] else "",
                    "updatedAt": str(row["updated_at"]) if row["updated_at"] else "",
                })

            return {"list": list_data, "total": total, "page": page, "pageSize": page_size}
    except Exception as exc:
        logger.error("Failed to list remote solve records: %s", type(exc).__name__)
        return {"list": [], "total": 0, "page": page, "pageSize": page_size}


async def get_remote_solve_stats(*, days: int = 7) -> dict[str, Any]:
    """Aggregate KPI stats for remote solve records."""
    try:
        async with async_session() as db:
            kpi_row = (
                await db.execute(
                    text(
                        "SELECT "
                        "COALESCE(SUM(CASE WHEN status NOT IN ('timeout','precheck_rejected') "
                        "  AND COALESCE(failure_reason,'') NOT IN ('service_unavailable','precheck_rejected','timeout','stale_terminated') "
                        "  THEN 1 ELSE 0 END), 0) AS total, "
                        "COALESCE(SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END), 0) AS success_count, "
                        "COALESCE(SUM(CASE WHEN status = 'fail' AND COALESCE(failure_reason,'') NOT IN ('service_unavailable','precheck_rejected','timeout','stale_terminated') THEN 1 ELSE 0 END), 0) AS fail_count, "
                        "COALESCE(SUM(CASE WHEN status = 'timeout' THEN 1 ELSE 0 END), 0) AS timeout_count, "
                        "COALESCE(SUM(CASE WHEN status = 'precheck_rejected' THEN 1 ELSE 0 END), 0) AS precheck_rejected_count, "
                        "COALESCE(SUM(CASE WHEN status = 'success' THEN token_charged ELSE 0 END), 0) AS charged_tokens "
                        "FROM xianyu_remote_slider_solve_record"
                    )
                )
            ).mappings().first()

            today_row = (
                await db.execute(
                    text(
                        "SELECT "
                        "COALESCE(SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END), 0) AS today_solve_count, "
                        "COALESCE(SUM(CASE WHEN status = 'success' THEN token_charged ELSE 0 END), 0) AS today_charged_tokens "
                        "FROM xianyu_remote_slider_solve_record "
                        "WHERE DATE(created_at) = CURDATE()"
                    )
                )
            ).mappings().first()

            trend_rows = (
                await db.execute(
                    text(
                        "SELECT DATE(created_at) AS date, "
                        "SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS success, "
                        "SUM(CASE WHEN status = 'fail' THEN 1 ELSE 0 END) AS fail, "
                        "SUM(CASE WHEN status = 'timeout' THEN 1 ELSE 0 END) AS timeout, "
                        "SUM(CASE WHEN status = 'success' THEN token_charged ELSE 0 END) AS tokens "
                        "FROM xianyu_remote_slider_solve_record "
                        "WHERE created_at >= DATE_SUB(CURDATE(), INTERVAL :days DAY) "
                        "GROUP BY DATE(created_at) ORDER BY date"
                    ),
                    {"days": max(1, days)},
                )
            ).mappings().all()

            trend = [
                {
                    "date": str(row["date"]),
                    "success": int(row["success"] or 0),
                    "fail": int(row["fail"] or 0),
                    "timeout": int(row["timeout"] or 0),
                    "tokens": int(row["tokens"] or 0),
                }
                for row in trend_rows
            ]

            # 数据来源说明：tokenCharged 来自商业版远程滑块求解 API 的实时返回值，
            # 仅在求解成功且商业版扣费成功时计入统计
            return {
                "kpi": {
                    "total": int(kpi_row["total"]) if kpi_row else 0,
                    "successCount": int(kpi_row["success_count"]) if kpi_row else 0,
                    "failCount": int(kpi_row["fail_count"]) if kpi_row else 0,
                    "timeoutCount": int(kpi_row["timeout_count"]) if kpi_row else 0,
                    "precheckRejectedCount": int(kpi_row["precheck_rejected_count"]) if kpi_row else 0,
                    "chargedTokens": int(kpi_row["charged_tokens"]) if kpi_row else 0,
                },
                "today": {
                    "solveCount": int(today_row["today_solve_count"]) if today_row else 0,
                    "chargedTokens": int(today_row["today_charged_tokens"]) if today_row else 0,
                },
                "trend": trend,
                "dataSource": {
                    "source": "commercial_remote_slider_api",
                    "description": "统计数据基于商业版远程滑块求解服务实时返回的扣费结果",
                    "chargeRule": "仅成功求解且扣费成功时计入 Token 消耗",
                },
            }
    except Exception as exc:
        logger.error("Failed to get remote solve stats: %s", type(exc).__name__)
        return {"kpi": {}, "today": {}, "trend": []}
