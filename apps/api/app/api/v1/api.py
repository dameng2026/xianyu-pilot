"""
API 路由聚合
============
注册所有业务路由模块到 api_router。
单租户精简版：不包含 workflow/opportunity/ai_transaction 等模块。
"""
from fastapi import APIRouter

from ...core.route_registry import assert_unique_routes, include_router_excluding

from .routes import (
    account,
    admin_data_compat,
    ai_tools,
    auth,
    auto_category,
    auto_reply_scope,
    business_settings_compat,
    captcha,
    client_errors,
    commerce,
    dashboard,
    delivery_workflow_compat,
    feishu,
    frontend_compat,
    home_content,
    internal,
    items,
    kami,
    messages,
    misc,
    module,
    navigation,
    order,
    profile,
    refunds,
    quick_reply,
    rag,
    restful,
    sse,
    system,
    websocket_control,
)

api_router = APIRouter()

# 账号与登录
include_router_excluding(
    api_router,
    account.router,
    excluded={
        ("POST", "/account/refreshProfile"),
    },
)
api_router.include_router(auth.router)
api_router.include_router(client_errors.router)
api_router.include_router(profile.router)

# 商品与同步
include_router_excluding(
    api_router,
    items.router,
    excluded={
        ("POST", "/item/delete"),
        ("POST", "/item/batch/remoteDelete"),
    },
)
api_router.include_router(auto_category.router)
api_router.include_router(auto_category.categories_router)
api_router.include_router(ai_tools.router)
api_router.include_router(home_content.router)
api_router.include_router(navigation.router)
include_router_excluding(
    api_router,
    commerce.router,
    excluded={
        ("DELETE", "/goods/{goods_id}/remote"),
    },
)
# 订单管理（启用商业版同步过来的能力：确认发货、同步订单）
api_router.include_router(order.router)
# 退款管理（新增模块，参考商业版实现）
api_router.include_router(refunds.router)

# 消息与会话
api_router.include_router(messages.router)
api_router.include_router(misc.qrlogin_router)
api_router.include_router(misc.websocket_router)
api_router.include_router(misc.media_router)
api_router.include_router(misc.image_router)
api_router.include_router(misc.address_dict_router)
include_router_excluding(
    api_router,
    misc.excel_router,
    excluded={
        ("GET", "/excel/export/orders"),
    },
)
include_router_excluding(
    api_router,
    misc.operation_log_router,
    excluded={
        ("POST", "/operationLog/deleteOld"),
        ("POST", "/operationLog/runtime"),
        ("POST", "/operationLog/runtime/clear"),
    },
)

# 卡密与发货
api_router.include_router(kami.router)
include_router_excluding(
    api_router,
    restful.router,
    excluded={
        ("GET", "/xianyu/accounts"),
        ("POST", "/xianyu/accounts"),
        ("GET", "/xianyu/accounts/summary"),
        ("GET", "/xianyu/accounts/face-verifications"),
        ("GET", "/xianyu/accounts/{account_id}"),
        ("PUT", "/xianyu/accounts/{account_id}"),
        ("DELETE", "/xianyu/accounts/{account_id}"),
        ("POST", "/item/publish"),
    },
)

# 系统配置
api_router.include_router(system.router)
api_router.include_router(system.notification_router)
api_router.include_router(system.operation_log_router)
api_router.include_router(system.system_info_router)
api_router.include_router(dashboard.router)
# 自动回复
api_router.include_router(auto_reply_scope.router)
api_router.include_router(quick_reply.router)
# business_settings_compat 与 frontend_compat 的 auto-reply/rules/* 路由重复，保持排除
include_router_excluding(
    api_router,
    business_settings_compat.router,
    excluded={
        ("GET", "/auto-reply/rules/logs"),
        ("GET", "/auto-reply/rules/stats"),
    },
)

# 验证码与滑块
api_router.include_router(captcha.router)

# 飞书
api_router.include_router(feishu.router)

# 内部接口
api_router.include_router(internal.router)

# SSE 推流
api_router.include_router(sse.router)

# 模块元数据与配置（Phase 5-6）
api_router.include_router(module.router)
# frontend_compat 与 order.router 的 /order/list 重复，与 business_settings_compat 的
# /auto-reply/rules/* 重复，保持原排除（避免路由冲突）
include_router_excluding(
    api_router,
    frontend_compat.router,
    excluded={
        ("GET", "/auto-reply/rules"),
        ("POST", "/auto-reply/rules"),
        ("PUT", "/auto-reply/rules/{rule_id}"),
        ("DELETE", "/auto-reply/rules/{rule_id}"),
        ("POST", "/auto-reply/rules/preview"),
        ("GET", "/auto-reply/rules/logs"),
        ("GET", "/auto-reply/rules/stats"),
        ("GET", "/quick-reply/templates"),
        ("POST", "/quick-reply/templates"),
        ("DELETE", "/quick-reply/templates/{template_id}"),
        ("POST", "/order/list"),
        ("GET", "/dashboard/order-message-trend"),
        ("GET", "/dashboard/account-health"),
        ("POST", "/xianyu/accounts/{account_id}/refresh-profile"),
    },
)
include_router_excluding(
    api_router,
    frontend_compat.publish_address_router,
    excluded={
        ("GET", "/publish-address/history"),
        ("POST", "/publish-address/save"),
    },
)
include_router_excluding(
    api_router,
    frontend_compat.conversations_router,
    excluded={
        ("GET", "/conversations/{conversation_id}/messages"),
    },
)
api_router.include_router(admin_data_compat.router)
# 启用卡密导入校验（商业版同步）
api_router.include_router(delivery_workflow_compat.router)
# 自动发货规则路由与 frontend_compat 重复，保持排除；global-config 路由仅在此处提供，启用
include_router_excluding(
    api_router,
    delivery_workflow_compat.delivery_rules_router,
    excluded={
        ("GET", "/auto-delivery/rules"),
    },
)

# RAG 知识库
api_router.include_router(rag.router)


# WebSocket 控制
include_router_excluding(
    api_router,
    websocket_control.router,
    excluded={
        ("POST", "/websocket/passwordLogin"),
        ("POST", "/websocket/updateToken"),
        ("POST", "/websocket/refreshToken"),
        ("POST", "/websocket/clearCaptchaWait"),
        ("POST", "/websocket/confirmManualVerification"),
        ("GET", "/websocket/pendingManualVerification"),
    },
)

# A duplicate method/path is always a deployment error. FastAPI otherwise
# silently keeps both routes and dispatches according to registration order.
assert_unique_routes(api_router.routes, context="/api router")
