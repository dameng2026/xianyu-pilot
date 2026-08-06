from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import TypeAlias

from fastapi import APIRouter
from starlette.routing import BaseRoute, compile_path


RouteKey: TypeAlias = tuple[str, str]
IGNORED_DUPLICATE_METHODS = frozenset({"HEAD", "OPTIONS"})
RETIRED_API_SURFACE_REASON = "RETIRED_API_SURFACE"
LEGACY_AUTH_RETIRED_GUIDANCE = (
    "开源版只保留固定管理员登录，不提供用户注册、账号探测、短信验证码、"
    "自助找回密码或旧会话接口；请使用 POST /api/auth/login 登录，并由部署管理员"
    "通过受控配置和密码修改流程维护唯一管理员账号。"
)
INTERNAL_QR_RETIRED_GUIDANCE = (
    "旧 Java 网关内部扫码接口已移除，不再通过内部接口创建会话或返回原始 Cookie；"
    "管理员客户端请使用 POST /api/qrlogin/generate、POST /api/qrlogin/status/{session_id} "
    "和 POST /api/qrlogin/cleanup。扫码凭据始终只在服务端保存。"
)


@dataclass(frozen=True)
class RetiredSurface:
    method: str
    path_template: str
    guidance: str


RETIRED_SURFACES = (
    RetiredSurface(
        "POST",
        "/api/login/checkUserExists",
        LEGACY_AUTH_RETIRED_GUIDANCE,
    ),
    RetiredSurface(
        "POST",
        "/api/login/register",
        LEGACY_AUTH_RETIRED_GUIDANCE,
    ),
    RetiredSurface(
        "POST",
        "/api/login/sendSmsCode",
        LEGACY_AUTH_RETIRED_GUIDANCE,
    ),
    RetiredSurface(
        "POST",
        "/api/login/verifyResetCode",
        LEGACY_AUTH_RETIRED_GUIDANCE,
    ),
    RetiredSurface(
        "POST",
        "/api/login/resetPassword",
        LEGACY_AUTH_RETIRED_GUIDANCE,
    ),
    RetiredSurface(
        "POST",
        "/api/login/login",
        LEGACY_AUTH_RETIRED_GUIDANCE,
    ),
    RetiredSurface(
        "POST",
        "/api/login/logout",
        LEGACY_AUTH_RETIRED_GUIDANCE,
    ),
    RetiredSurface(
        "POST",
        "/api/internal/qrlogin/generate",
        INTERNAL_QR_RETIRED_GUIDANCE,
    ),
    RetiredSurface(
        "POST",
        "/api/internal/qrlogin/status/{session_id}",
        INTERNAL_QR_RETIRED_GUIDANCE,
    ),
    RetiredSurface(
        "GET",
        "/api/internal/qrlogin/cookies/{session_id}",
        INTERNAL_QR_RETIRED_GUIDANCE,
    ),
    RetiredSurface(
        "POST",
        "/api/internal/qrlogin/cookies/{session_id}",
        INTERNAL_QR_RETIRED_GUIDANCE,
    ),
    RetiredSurface(
        "POST",
        "/api/internal/qrlogin/cleanup",
        INTERNAL_QR_RETIRED_GUIDANCE,
    ),
    RetiredSurface(
        "POST",
        "/api/item/republish",
        "重新发布接口已移除；请使用 POST /api/item/publish 并提交完整商品信息。",
    ),
    RetiredSurface(
        "POST",
        "/api/item/batch/offShelf",
        "非幂等批量下架接口已移除；请逐件调用 POST /api/item/offShelf，并为每个商品提供独立且稳定的 idempotencyKey。",
    ),
    RetiredSurface(
        "GET",
        "/api/msg/audio/{message_id}",
        "音频代理接口未提供；请使用消息记录中的 media.url、fileUrl 或 downloadUrl。",
    ),
    RetiredSurface(
        "POST",
        "/api/websocket/passwordLogin",
        "密码登录接口已移除；请使用 POST /api/qrlogin/generate 发起扫码登录。",
    ),
    RetiredSurface(
        "POST",
        "/api/backup/restore-db",
        "应用内数据库恢复已移除；请按 docs/production-readiness.md 的 MySQL 恢复流程操作。",
    ),
    RetiredSurface(
        "GET",
        "/api/backup/export-db",
        "应用内数据库导出已移除；请按 docs/production-readiness.md 的 MySQL 备份流程操作。",
    ),
    RetiredSurface(
        "GET",
        "/api/excel/export/orders",
        "订单 Excel 导出尚未提供；请使用 GET /api/orders 分页读取订单。",
    ),
    RetiredSurface(
        "GET",
        "/api/business-opportunity/search",
        "商机兼容接口已移除，当前版本没有替代搜索接口，请停止重试。",
    ),
    RetiredSurface(
        "GET",
        "/api/business-opportunity/shop",
        "店铺采集兼容接口已移除，当前版本没有替代采集接口，请停止重试。",
    ),
    RetiredSurface(
        "POST",
        "/api/business-opportunity/collect-shop",
        "商机采集兼容接口已移除，当前版本没有替代采集接口，请停止重试。",
    ),
    RetiredSurface(
        "POST",
        "/api/crawler/import/goofish",
        "店铺导入接口未实现，当前 crawler 仅保留滑块求解能力，请停止重试。",
    ),
    RetiredSurface(
        "GET",
        "/api/crawler/crawl-jobs/{job_id}",
        "采集任务接口未实现，当前版本不会创建 crawl job，请停止轮询。",
    ),
    RetiredSurface(
        "GET",
        "/api/crawler/goofish/stores/{user_id}/items",
        "店铺商品采集接口未实现，当前版本没有可查询的采集结果。",
    ),
    RetiredSurface(
        "GET",
        "/api/goofish/search",
        "服务端闲鱼搜索兼容接口已移除，当前版本没有替代后端接口。",
    ),
    RetiredSurface(
        "POST",
        "/api/notification/logs",
        "旧通知日志接口已移除；请使用 GET /api/notifications/delivery-logs。",
    ),
    RetiredSurface(
        "POST",
        "/api/notification/latest",
        "旧通知列表接口已移除；请使用 GET /api/navigation/notifications。",
    ),
    RetiredSurface(
        "POST",
        "/api/notification/test",
        "旧通知测试桩已移除；请使用 POST /api/notifications/test 执行真实渠道测试。",
    ),
    RetiredSurface(
        "POST",
        "/api/order/confirmShipment",
        "仅修改本地订单状态的旧发货接口已移除；请使用 POST /api/orders/{order_id}/manual-delivery，并检查返回的真实发货状态。",
    ),
    RetiredSurface(
        "POST",
        "/api/order/syncSoldOrders",
        "返回零条结果的旧同步桩已移除；请使用 POST /api/orders/sync 执行真实订单同步。",
    ),
    RetiredSurface(
        "POST",
        "/api/order/batchRefresh",
        "仅回读本地数据的旧刷新桩已移除；请使用 POST /api/orders/sync 后重新查询订单。",
    ),
    RetiredSurface(
        "POST",
        "/api/item/batch/remoteDelete",
        "不安全的批量远程删除接口已移除；请逐件调用 POST /api/item/remoteDelete，并检查 confirmed/unknown/remote_confirmed 状态。",
    ),
    RetiredSurface(
        "POST",
        "/api/item/delete",
        "可能跨账号误删的旧本地删除接口已移除；请使用 DELETE /api/goods/{goods_id}/local。",
    ),
    RetiredSurface(
        "POST",
        "/api/item/updateStock",
        "远程库存更新尚未实现；当前版本不会修改平台库存，请停止重试。",
    ),
    RetiredSurface(
        "POST",
        "/api/item/updateAutoDeliveryStatus",
        "旧自动发货状态桩已移除；请使用 PUT /api/auto-delivery/goods/{goods_id}/config。",
    ),
    RetiredSurface(
        "POST",
        "/api/item/updateAutoConfirmShipment",
        "旧自动确认发货状态桩已移除；请使用商品级自动发货配置页面。",
    ),
    RetiredSurface(
        "POST",
        "/api/item/autoDeliveryRecords",
        "旧空记录接口已移除；请使用 GET /api/auto-delivery/records。",
    ),
    RetiredSurface(
        "POST",
        "/api/item/autoReplyRecords",
        "旧空记录接口已移除；请使用 /api/auto-reply-scope/products 与 /api/auto-reply-scope/status。",
    ),
    RetiredSurface(
        "POST",
        "/api/item/getRagAutoReplyConfig",
        "旧 RAG 配置桩已移除；请使用 /api/rag/knowledge-bases 与 /api/rag/chat。",
    ),
    RetiredSurface(
        "POST",
        "/api/item/updateRagAutoReplyConfig",
        "旧 RAG 配置桩已移除；请使用 /api/rag/knowledge-bases 管理真实知识库。",
    ),
    RetiredSurface(
        "POST",
        "/api/item/sku-specs",
        "当前版本没有 SKU 变体模型；请使用 GET /api/goods/{goods_id} 查询商品主记录。",
    ),
    RetiredSurface(
        "DELETE",
        "/api/goods/{goods_id}/remote",
        "仅修改本地状态的伪远程删除接口已移除；请使用 POST /api/item/remoteDelete。",
    ),
    RetiredSurface(
        "POST",
        "/api/data-panel/stats",
        "旧数据面板兼容接口已移除；请使用 GET /api/dashboard/summary。",
    ),
    RetiredSurface(
        "POST",
        "/api/data-panel/trend",
        "旧数据面板兼容接口已移除；请使用 GET /api/dashboard/sales-trend。",
    ),
    RetiredSurface(
        "GET",
        "/api/captcha/debug-image/latest",
        "生产接口不暴露验证码调试截图；请通过受控服务日志排查验证码流程。",
    ),
    RetiredSurface(
        "GET",
        "/api/auto-delivery/global-config",
        "未落库的全局发货配置已移除；请使用 /api/auto-delivery/goods/{goods_id}/config 配置具体商品。",
    ),
    RetiredSurface(
        "PUT",
        "/api/auto-delivery/global-config",
        "未落库的全局发货配置已移除；请使用 /api/auto-delivery/goods/{goods_id}/config 配置具体商品。",
    ),
    RetiredSurface(
        "GET",
        "/api/aiProvider/list",
        "旧 AI Provider 接口已移除；请使用 /api/model-config。",
    ),
    RetiredSurface(
        "POST",
        "/api/aiProvider/save",
        "旧 AI Provider 接口已移除；请使用 /api/model-config。",
    ),
    RetiredSurface(
        "POST",
        "/api/aiProvider/listByType",
        "旧 AI Provider 接口已移除；请使用 GET /api/model-config/list。",
    ),
    RetiredSurface(
        "POST",
        "/api/aiProvider/delete",
        "旧 AI Provider 接口已移除；请使用 DELETE /api/model-config/{id}。",
    ),
    RetiredSurface(
        "POST",
        "/api/aiProvider/activate",
        "旧 AI Provider 接口已移除；请使用 PUT /api/model-config/{id}。",
    ),
    RetiredSurface(
        "POST",
        "/api/aiProvider/test",
        "未执行连通性检查的旧接口已移除；请使用 POST /api/model-config/{id}/test。",
    ),
    RetiredSurface(
        "POST",
        "/api/aiProvider/models",
        "返回空模型列表的旧接口已移除；请使用 GET /api/model-config/list。",
    ),
    RetiredSurface(
        "POST",
        "/api/loginDevice/list",
        "当前版本没有持久化登录设备模型；请使用账户安全页面管理当前会话。",
    ),
    RetiredSurface(
        "POST",
        "/api/loginDevice/kick",
        "未执行会话撤销的旧接口已移除；请修改密码以撤销全部既有令牌。",
    ),
    RetiredSurface(
        "GET",
        "/api/dashboard/order-message-trend",
        "伪造订单零值的兼容指标已移除；请使用 /api/dashboard/summary 与 /api/dashboard/sales-trend。",
    ),
    RetiredSurface(
        "GET",
        "/api/dashboard/account-health",
        "伪造全健康账号指标已移除；请使用真实账号状态列表。",
    ),
    RetiredSurface(
        "POST",
        "/api/ads/payment/orders/{order_no}/mock-pay",
        "模拟支付接口已移除；请配置真实商业支付桥，并以商业服务返回的订单状态为准。",
    ),
    RetiredSurface(
        "GET",
        "/api/publish-address/history",
        "发布地址历史功能已移除：当前版本没有对应的持久化模型；发布时仍可通过地图搜索选择本次地址。",
    ),
    RetiredSurface(
        "POST",
        "/api/publish-address/save",
        "发布地址历史功能已移除：当前版本不会保存常用地址，请勿将本次地址视为已持久化。",
    ),
    RetiredSurface(
        "GET",
        "/api/conversations/{conversation_id}/messages",
        "旧会话消息接口已移除；请使用 POST /api/msg/context，并提交账号与会话标识读取真实消息。",
    ),
    RetiredSurface(
        "POST",
        "/api/websocket/updateToken",
        "旧 Token 更新接口从未更新平台 Token，现已移除；请更新账号 Cookie 后调用 POST /api/websocket/start。",
    ),
    RetiredSurface(
        "POST",
        "/api/websocket/refreshToken",
        "旧 Token 刷新接口从未刷新平台 Token，现已移除；请更新账号 Cookie 后调用 POST /api/websocket/start。",
    ),
    RetiredSurface(
        "POST",
        "/api/websocket/clearCaptchaWait",
        "验证码等待占位接口已移除：当前版本没有可清理的持久化等待状态。",
    ),
    RetiredSurface(
        "POST",
        "/api/websocket/confirmManualVerification",
        "人工验证确认占位接口已移除；请更新账号 Cookie，并使用 POST /api/websocket/checkLogin 校验真实状态。",
    ),
    RetiredSurface(
        "GET",
        "/api/websocket/pendingManualVerification",
        "待人工验证列表已移除：当前版本没有验证事件模型，请以账号登录校验结果为准。",
    ),
    RetiredSurface(
        "POST",
        "/api/sysSetting/testEmail",
        "旧邮件测试桩已移除；请在通知设置中配置 SMTP 渠道，并调用 POST /api/notifications/test 执行真实发送测试。",
    ),
    RetiredSurface(
        "GET",
        "/api/auto-reply/rules/logs",
        "旧自动回复日志接口未连接运行记录，现已移除；当前版本不会以固定空列表冒充真实日志。",
    ),
    RetiredSurface(
        "GET",
        "/api/auto-reply/rules/stats",
        "旧自动回复统计接口包含固定零指标，现已移除；请勿将配置规则数当作回复送达统计。",
    ),
    RetiredSurface(
        "POST",
        "/api/account/refreshProfile",
        "仅回显本地账号资料的旧刷新接口已移除；请使用 POST /api/xianyu/accounts/{account_id}/refresh-profile 执行真实平台刷新。",
    ),
    RetiredSurface(
        "POST",
        "/api/operationLog/runtime",
        "运行时日志摘要占位接口已移除；请使用 GET /api/operation-logs 查询真实审计记录。",
    ),
    RetiredSurface(
        "POST",
        "/api/operationLog/deleteOld",
        "手动审计日志清理已移除；日志仅由 worker 按 AUDIT_LOG_RETENTION_DAYS 策略分批清理。",
    ),
    RetiredSurface(
        "POST",
        "/api/operationLog/runtime/clear",
        "运行时日志批量删除接口已移除；请使用部署环境的受控日志保留、轮转和归档策略，并保留管理员操作审计记录。",
    ),
    RetiredSurface(
        "POST",
        "/api/cards/import/validate",
        "将所有输入都标记为有效的卡密校验桩已移除；请直接导入并以服务端返回的成功、重复和失败计数为准。",
    ),
)

_RETIRED_MATCHERS = tuple(
    (
        surface.method,
        compile_path(surface.path_template)[0],
        surface.guidance,
    )
    for surface in RETIRED_SURFACES
)


def route_keys(route: BaseRoute) -> frozenset[RouteKey]:
    """Return the exact HTTP method/path keys exposed by one route."""
    path = getattr(route, "path", "")
    methods = set(getattr(route, "methods", set()) or set())
    return frozenset(
        (method.upper(), path)
        for method in methods
        if method.upper() not in IGNORED_DUPLICATE_METHODS
    )


def include_router_excluding(
    target: APIRouter,
    source: APIRouter,
    *,
    excluded: Iterable[RouteKey],
) -> None:
    """Mount a router while explicitly removing deprecated or shadow routes.

    FastAPI normally mounts every route in an ``APIRouter``. Compatibility
    modules in this project contain both authoritative and obsolete paths, so
    mounting them wholesale makes registration order decide which endpoint is
    reachable. This seam requires an exact exclusion list and fails if an
    exclusion becomes stale or only removes part of a multi-method route.
    """

    expected = frozenset((method.upper(), path) for method, path in excluded)
    consumed: set[RouteKey] = set()

    for route in source.routes:
        keys = route_keys(route)
        matched = keys.intersection(expected)
        if matched:
            if matched != keys:
                raise RuntimeError(
                    "Cannot partially exclude a multi-method route: "
                    f"route={sorted(keys)!r}, excluded={sorted(matched)!r}"
                )
            consumed.update(matched)
            continue
        # Route objects already contain their source router's prefix,
        # dependencies, response metadata and tags. The aggregate router has
        # no additional defaults, so appending preserves the complete route.
        target.routes.append(route)

    missing = expected.difference(consumed)
    if missing:
        raise RuntimeError(f"Stale route exclusions: {sorted(missing)!r}")


def duplicate_routes(routes: Sequence[BaseRoute]) -> dict[RouteKey, list[str]]:
    index: dict[RouteKey, list[str]] = defaultdict(list)

    def visit(route: BaseRoute, prefix: str = "") -> None:
        # FastAPI 0.139 keeps directly included routers as lazy
        # ``_IncludedRouter`` entries. Traverse them so the deployment guard
        # still sees every effective method/path pair.
        original_router = getattr(route, "original_router", None)
        include_context = getattr(route, "include_context", None)
        if original_router is not None and include_context is not None:
            nested_prefix = f"{prefix}{include_context.prefix}"
            for nested_route in original_router.routes:
                visit(nested_route, nested_prefix)
            return

        endpoint = getattr(route, "endpoint", None)
        owner = (
            f"{getattr(endpoint, '__module__', '<unknown>')}."
            f"{getattr(route, 'name', '<unnamed>')}"
        )
        path = f"{prefix}{getattr(route, 'path', '')}"
        methods = set(getattr(route, "methods", set()) or set())
        for method in methods:
            normalized = method.upper()
            if normalized not in IGNORED_DUPLICATE_METHODS:
                index[(normalized, path)].append(owner)

    for route in routes:
        visit(route)
    return {key: owners for key, owners in index.items() if len(owners) > 1}


def assert_unique_routes(routes: Sequence[BaseRoute], *, context: str) -> None:
    duplicates = duplicate_routes(routes)
    if duplicates:
        details = "; ".join(
            f"{method} {path}: {', '.join(owners)}"
            for (method, path), owners in sorted(duplicates.items())
        )
        raise RuntimeError(f"Duplicate routes in {context}: {details}")


def retired_surface_guidance(method: str, path: str) -> str | None:
    normalized_method = str(method or "").upper()
    for expected_method, path_pattern, guidance in _RETIRED_MATCHERS:
        if normalized_method == expected_method and path_pattern.fullmatch(path):
            return guidance
    return None
