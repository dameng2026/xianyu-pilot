from sqlalchemy import Column, Integer, BigInteger, String, Date, DateTime, Text, Boolean, ForeignKey, Float, DECIMAL, SmallInteger, JSON, Index, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from ..core.database import Base


# ============================================================
# 与 MySQL 表定义（单租户精简版，移除 tenant_id / user_id）
# ============================================================

class XianyuAccount(Base):
    __tablename__ = "xianyu_account"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    platform = Column(String(50), nullable=True, default="xianyu", comment="平台: xianyu")
    external_uid = Column(String(200), nullable=True, comment="闲鱼external_uid")
    nickname = Column(String(200), nullable=True)
    avatar_url = Column(Text, nullable=True)
    province = Column(String(100), nullable=True)
    city = Column(String(100), nullable=True)
    account_level = Column(String(50), nullable=True)
    introduction = Column(Text, nullable=True, comment="简介")
    followers = Column(Integer, nullable=True, comment="粉丝数")
    following = Column(Integer, nullable=True, comment="关注数")
    sold_count = Column(Integer, nullable=True, comment="已售数")
    review_num = Column(Integer, nullable=True, comment="评价数")
    seller_level = Column(String(50), nullable=True, comment="卖家等级")
    praise_ratio = Column(String(20), nullable=True, comment="好评率")
    fish_shop_score = Column(DECIMAL(3, 1), nullable=True, comment="鱼小铺分数")
    fish_shop_user = Column(SmallInteger, default=0, comment="是否开通鱼小铺")
    remark = Column(Text, nullable=True)
    status = Column(SmallInteger, default=1, comment="1正常 0禁用")
    deleted = Column(SmallInteger, default=0)
    created_time = Column(DateTime, default=func.now())
    updated_time = Column(DateTime, default=func.now(), onupdate=func.now())


class XianyuAccountAuth(Base):
    __tablename__ = "xianyu_account_auth"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    account_id = Column(BigInteger, nullable=False)
    encrypted_cookie = Column(Text, nullable=True, comment="加密Cookie")
    encrypted_token = Column(Text, nullable=True, comment="加密Token")
    login_username = Column(String(255), nullable=True)
    encrypted_login_password = Column(Text, nullable=True)
    show_browser = Column(Boolean, default=False)
    cookie_status = Column(SmallInteger, default=0, comment="1正常 0待校验/失效 2过期")
    ws_token = Column(Text, nullable=True)
    token_expire_time = Column(DateTime, nullable=True)
    last_login_status_code = Column(String(64), nullable=True)
    last_login_status_message = Column(String(255), nullable=True)
    last_login_check_time = Column(DateTime, nullable=True)
    deleted = Column(SmallInteger, default=0)
    created_time = Column(DateTime, default=func.now())
    updated_time = Column(DateTime, default=func.now(), onupdate=func.now())


class XianyuAccountRuntime(Base):
    __tablename__ = "xianyu_account_runtime"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    account_id = Column(BigInteger, nullable=False)
    online_status = Column(SmallInteger, default=0, comment="1在线 0离线")
    ws_status = Column(SmallInteger, default=0, comment="1在线 0离线")
    ws_latency_ms = Column(Integer, default=0)
    cookie_status = Column(SmallInteger, default=0, comment="1正常 0待校验/失效 2过期")
    last_login_status_code = Column(String(64), nullable=True)
    last_login_status_message = Column(String(255), nullable=True)
    last_login_check_time = Column(DateTime, nullable=True)
    last_login_time = Column(DateTime, nullable=True)
    last_heartbeat_time = Column(DateTime, nullable=True)
    last_online_time = Column(DateTime, nullable=True)
    last_sync_time = Column(DateTime, nullable=True)
    deleted = Column(SmallInteger, default=0)
    created_time = Column(DateTime, default=func.now())
    updated_time = Column(DateTime, default=func.now(), onupdate=func.now())


class XianyuGoods(Base):
    __tablename__ = "xianyu_goods"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    account_id = Column(BigInteger, nullable=True)
    goods_id = Column(String(100), nullable=True, comment="兼容旧商品ID字段")
    external_goods_id = Column(String(100), nullable=True, comment="闲鱼商品ID")
    title = Column(String(500), nullable=True, comment="商品标题")
    price = Column(String(50), nullable=True, comment="价格")
    sold_price = Column(String(50), nullable=True, comment="售价")
    cover_pic = Column(Text, nullable=True, comment="封面图URL")
    image_url = Column(Text, nullable=True, comment="图片URL")
    image_urls = Column(JSON, nullable=True, comment="图片URL列表")
    stock = Column(Integer, default=0, comment="库存")
    quantity = Column(Integer, default=0, comment="库存数量")
    exposure_count = Column(Integer, default=0, comment="曝光次数")
    view_count = Column(Integer, default=0, comment="浏览次数")
    want_count = Column(Integer, default=0, comment="想要人数")
    detail_url = Column(Text, nullable=True, comment="详情页URL")
    detail_info = Column(Text, nullable=True, comment="详情描述文字")
    description = Column(Text, nullable=True, comment="描述")
    raw_payload = Column(JSON, nullable=True, comment="原始商品数据快照")
    category = Column(String(100), nullable=True, comment="分类")
    sort_order = Column(Integer, default=0, comment="排序序号")
    status = Column(SmallInteger, default=1, comment="1在售 0下架 2已售")
    deleted = Column(SmallInteger, default=0)
    auto_reply_enabled = Column(SmallInteger, nullable=True, default=None, comment="NULL继承账号全局 0强制关 1强制开")
    created_time = Column(DateTime, default=func.now())
    updated_time = Column(DateTime, default=func.now(), onupdate=func.now())


class XianyuGoodsSyncTask(Base):
    """商品同步任务表（单租户精简版，无 tenant_id）。"""
    __tablename__ = "xianyu_goods_sync_task"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    sync_id = Column(String(80), nullable=False, unique=True, comment="同步任务ID")
    account_id = Column(BigInteger, nullable=False)
    status = Column(String(30), nullable=False, default="queued", comment="queued/running/completed/failed")
    progress = Column(Integer, default=0)
    total_count = Column(Integer, default=0)
    new_count = Column(Integer, default=0)
    updated_count = Column(Integer, default=0)
    skipped_count = Column(Integer, default=0)
    off_shelf_count = Column(Integer, default=0)
    detail_synced_count = Column(Integer, default=0)
    duration_seconds = Column(Float, default=0)
    error_message = Column(Text, nullable=True)
    started_time = Column(DateTime, nullable=True)
    finished_time = Column(DateTime, nullable=True)
    deleted = Column(SmallInteger, default=0)
    created_time = Column(DateTime, default=func.now())
    updated_time = Column(DateTime, default=func.now(), onupdate=func.now())


class XianyuTradeOrder(Base):
    __tablename__ = "xianyu_trade_order"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    account_id = Column(BigInteger, nullable=True)
    external_order_id = Column(String(200), nullable=True, comment="闲鱼订单ID")
    order_status = Column(SmallInteger, default=0, comment="0待付款 1已付款 2待发货 3已发货 4已完成 5已关闭")
    total_amount = Column(String(50), nullable=True)
    buyer_name = Column(String(200), nullable=True)
    buyer_id = Column(String(200), nullable=True)
    create_time = Column(DateTime, nullable=True)
    pay_time = Column(DateTime, nullable=True)
    ship_time = Column(DateTime, nullable=True)
    confirm_time = Column(DateTime, nullable=True)
    buyer_message = Column(Text, nullable=True)
    item_id = Column(String(100), nullable=True, comment="商品ID")
    is_bargain = Column(SmallInteger, default=0, comment="是否小刀")
    is_rated = Column(SmallInteger, default=0, comment="是否已评价")
    is_red_flower = Column(SmallInteger, default=0, comment="是否已求小红花")
    deleted = Column(SmallInteger, default=0)
    created_time = Column(DateTime, default=func.now())
    updated_time = Column(DateTime, default=func.now(), onupdate=func.now())


class XianyuTradeOrderItem(Base):
    __tablename__ = "xianyu_trade_order_item"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    order_id = Column(BigInteger, nullable=False)
    goods_id = Column(BigInteger, nullable=True)
    goods_name = Column(String(300), nullable=True)
    goods_title = Column(String(500), nullable=True)
    goods_image = Column(Text, nullable=True)
    goods_price = Column(DECIMAL(12, 2), nullable=True)
    price_cent = Column(BigInteger, default=0)
    goods_count = Column(Integer, default=1)
    quantity = Column(Integer, default=1)
    subtotal_cent = Column(BigInteger, default=0)
    sku_id = Column(String(100), nullable=True)
    sku_name = Column(String(200), nullable=True)
    deleted = Column(SmallInteger, default=0)
    created_time = Column(DateTime, default=func.now())
    updated_time = Column(DateTime, default=func.now(), onupdate=func.now())


class DeliveryRule(Base):
    __tablename__ = "delivery_rule"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    account_id = Column(BigInteger, nullable=True)
    rule_name = Column(String(200), nullable=True)
    goods_id = Column(BigInteger, nullable=True)
    delivery_mode = Column(String(50), default="kami")
    card_group_id = Column(BigInteger, nullable=True)
    delivery_content = Column(Text, nullable=True)
    trigger_on_pay = Column(SmallInteger, default=1)
    trigger_keyword = Column(String(200), nullable=True)
    max_delivery_per_day = Column(Integer, default=0)
    status = Column(SmallInteger, default=1)
    deleted = Column(SmallInteger, default=0)
    created_time = Column(DateTime, default=func.now())
    updated_time = Column(DateTime, default=func.now(), onupdate=func.now())


class CardGroup(Base):
    __tablename__ = "card_group"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    group_name = Column(String(200), nullable=False)
    group_type = Column(String(50), default="kami")
    total_count = Column(Integer, default=0)
    used_count = Column(Integer, default=0)
    available_count = Column(Integer, default=0)
    remark = Column(Text, nullable=True)
    status = Column(SmallInteger, default=1)
    deleted = Column(SmallInteger, default=0)
    created_time = Column(DateTime, default=func.now())
    updated_time = Column(DateTime, default=func.now(), onupdate=func.now())


class CardItem(Base):
    __tablename__ = "card_item"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    group_id = Column(BigInteger, nullable=False)
    card_key = Column(Text, nullable=False)
    card_value = Column(Text, nullable=True)
    extra_info = Column(Text, nullable=True)
    status = Column(SmallInteger, default=0)
    used_order_id = Column(BigInteger, nullable=True)
    realtime_attempt_id = Column(BigInteger, nullable=True)
    claim_token = Column(String(64), nullable=True)
    is_used = Column(SmallInteger, default=0)
    used_time = Column(DateTime, nullable=True)
    used_by_order_id = Column(BigInteger, nullable=True)
    used_by_user = Column(String(200), nullable=True)
    expire_time = Column(DateTime, nullable=True)
    remark = Column(Text, nullable=True)
    deleted = Column(SmallInteger, default=0)
    created_time = Column(DateTime, default=func.now())
    updated_time = Column(DateTime, default=func.now(), onupdate=func.now())


class DeliveryRecord(Base):
    """发货记录实体，用于统计发货成功/失败/待处理"""
    __tablename__ = "delivery_record"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    account_id = Column(BigInteger, nullable=True, comment="关联xianyu_account.id")
    order_id = Column(BigInteger, nullable=True, comment="关联xianyu_trade_order.id")
    rule_id = Column(BigInteger, nullable=True, comment="关联delivery_rule.id")
    delivery_type = Column(String(50), nullable=True)
    delivery_mode = Column(String(50), nullable=True)
    content = Column(Text, nullable=True)
    delivery_content = Column(Text, nullable=True)
    receiver_info = Column(Text, nullable=True)
    delivery_timing = Column(String(50), nullable=True)
    status = Column(SmallInteger, default=0)
    delivery_status = Column(String(50), default="pending", comment="发货状态 pending/success/failed")
    error_message = Column(Text, nullable=True)
    retry_count = Column(Integer, default=0)
    fail_reason = Column(Text, nullable=True)
    delivery_time = Column(DateTime, nullable=True)
    completed_time = Column(DateTime, nullable=True)
    quantity_requested = Column(Integer, default=1)
    quantity_sent = Column(Integer, default=0)
    platform_sync_time = Column(DateTime, nullable=True)
    result = Column(Text, nullable=True)
    deleted = Column(SmallInteger, default=0)
    created_time = Column(DateTime, default=func.now())
    updated_time = Column(DateTime, default=func.now(), onupdate=func.now())


class Notification(Base):
    """系统通知实体"""
    __tablename__ = "notification"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    notification_type = Column(String(50), nullable=True, comment="通知类型")
    title = Column(String(300), nullable=True)
    content = Column(Text, nullable=True)
    reference_type = Column(String(100), nullable=True)
    reference_id = Column(BigInteger, nullable=True)
    is_read = Column(SmallInteger, default=0, comment="0未读 1已读")
    read_time = Column(DateTime, nullable=True)
    priority = Column(SmallInteger, default=0, comment="优先级")
    deleted = Column(SmallInteger, default=0)
    created_time = Column(DateTime, default=func.now())
    updated_time = Column(DateTime, default=func.now(), onupdate=func.now())


class XianyuConversation(Base):
    __tablename__ = "xianyu_conversation"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    account_id = Column(BigInteger, nullable=True)
    seller_external_uid = Column(String(64), nullable=True, comment="闲鱼卖家真实UID/unb")
    external_buyer_id = Column(String(200), nullable=True)
    peer_external_uid = Column(String(64), nullable=True, comment="买家UID（稳定）")
    peer_key = Column(String(128), nullable=True, comment="对端唯一标识（用于去重合并会话）")
    buyer_name = Column(String(200), nullable=True)
    buyer_avatar = Column(Text, nullable=True)
    goods_title = Column(String(500), nullable=True)
    goods_id = Column(String(200), nullable=True)
    goods_cover_pic = Column(Text, nullable=True, comment="商品封面图URL")
    s_id = Column(String(200), nullable=True, comment="闲鱼会话sId（头像查询稳定匹配键）")
    status = Column(SmallInteger, default=0, comment="0进行中 1已完成 2已关闭")
    last_message_time = Column(DateTime, nullable=True)
    last_message_content = Column(Text, nullable=True)
    unread_count = Column(Integer, default=0)
    # 会话级自动回复状态机（同步商业版 V1.13）
    # auto_reply_paused: 0=运行中 1=已暂停（人工干预或手动关闭触发）
    # auto_reply_manual_disabled: 0=可自动恢复 1=手动关闭（禁止自动恢复，仅用户手动开启）
    # last_manual_reply_at: 最后人工回复时间戳（毫秒），用于1分钟自动恢复判断
    # last_auto_reply_at: 最后 AI 自动回复时间戳（毫秒）
    auto_reply_paused = Column(SmallInteger, default=0, comment="会话级自动回复是否暂停 0否 1是（人工干预或手动关闭触发）")
    auto_reply_manual_disabled = Column(SmallInteger, default=0, comment="是否被用户手动关闭 0否 1是（1时不允许自动恢复，仅手动开启）")
    last_manual_reply_at = Column(BigInteger, nullable=True, comment="最后一次人工回复时间戳（毫秒），用于1分钟自动恢复判断")
    last_auto_reply_at = Column(BigInteger, nullable=True, comment="最后一次 AI 自动回复时间戳（毫秒）")
    created_time = Column(DateTime, default=func.now())
    updated_time = Column(DateTime, default=func.now(), onupdate=func.now())


class XianyuMessage(Base):
    __tablename__ = "xianyu_message"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    account_id = Column(BigInteger, nullable=True)
    conversation_id = Column(BigInteger, nullable=True)
    session_id = Column(String(200), nullable=True, comment="会话session ID，用于关联xianyu_chat_message.s_id")
    from_user_id = Column(String(200), nullable=True)
    to_user_id = Column(String(200), nullable=True)
    content = Column(Text, nullable=True)
    message_type = Column(String(50), default="text", comment="text/image/card")
    direction = Column(String(20), default="received", comment="sent/received")
    is_auto_reply = Column(SmallInteger, default=0, comment="0否 1是")
    deleted = Column(SmallInteger, default=0)
    created_time = Column(DateTime, default=func.now())


class XianyuChatMessage(Base):
    """闲鱼 WebSocket 实时聊天消息（去重存储，含完整原始消息体）"""
    __tablename__ = "xianyu_chat_message"
    __table_args__ = (
        Index("idx_chat_msg_lookup", "account_id", "deleted", "s_id", "message_time"),
    )
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    account_id = Column(BigInteger, nullable=False, comment="闲鱼账号ID")
    seller_external_uid = Column(String(64), nullable=True, comment="闲鱼卖家真实UID/unb")
    pnm_id = Column(String(200), nullable=True, comment="消息唯一ID（去重）")
    message_uid = Column(String(128), nullable=True, comment="稳定消息唯一ID（用于去重）")
    s_id = Column(String(200), nullable=True, comment="会话ID")
    content_type = Column(Integer, default=1, comment="消息类型:1文本 2图片 14砍价 25已拍下 26已付款 28已发货 32已读")
    msg_content = Column(Text, nullable=True, comment="消息文本内容")
    sender_user_id = Column(String(200), nullable=True, comment="发送者ID")
    receiver_user_id = Column(String(64), nullable=True, comment="接收者用户ID")
    sender_user_name = Column(String(200), nullable=True, comment="发送者昵称")
    peer_external_uid = Column(String(64), nullable=True, comment="买家UID")
    xy_goods_id = Column(String(200), nullable=True, comment="关联商品ID")
    message_time = Column(BigInteger, default=0, comment="消息时间戳(毫秒)")
    direction = Column(String(20), default="IN", comment="IN/OUT")
    parse_status = Column(String(16), default="ok", comment="解析状态 ok/partial/failed")
    reminder_content = Column(Text, nullable=True, comment="提醒内容")
    reminder_url = Column(String(500), nullable=True, comment="提醒链接")
    complete_msg = Column(JSON, nullable=True, comment="完整原始消息体")
    raw_payload = Column(JSON, nullable=True, comment="原始消息payload（用于调试和重新解析）")
    read_status = Column(SmallInteger, default=0, comment="0未读 1已读")
    deleted = Column(SmallInteger, default=0)
    created_time = Column(DateTime, default=func.now())
    updated_time = Column(DateTime, default=func.now(), onupdate=func.now())


class MessageAutomationOutbox(Base):
    """Durable follow-up work created atomically with an inbound chat row.

    The outbox deliberately stores only a reference to the existing chat row
    and a one-way source digest.  Buyer content remains in the authoritative
    chat table instead of being copied into operational queues.
    """

    __tablename__ = "message_automation_outbox"
    __table_args__ = (
        UniqueConstraint(
            "account_id",
            "source_message_digest",
            "branch",
            name="uk_message_automation_source_branch",
        ),
        UniqueConstraint(
            "chat_message_id",
            "branch",
            name="uk_message_automation_message_branch",
        ),
        Index(
            "idx_message_automation_claim",
            "state",
            "retry_safe",
            "next_attempt_at",
            "lease_until",
        ),
        Index(
            "idx_message_automation_account_created",
            "account_id",
            "created_time",
        ),
    )

    id = Column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    account_id = Column(BigInteger, nullable=False)
    chat_message_id = Column(BigInteger, nullable=False)
    source_message_digest = Column(String(64), nullable=False)
    branch = Column(String(16), nullable=False)
    state = Column(String(32), nullable=False, default="pending")
    retry_safe = Column(SmallInteger, nullable=False, default=1)
    attempt_count = Column(Integer, nullable=False, default=0)
    lease_token = Column(String(64), nullable=True)
    lease_until = Column(DateTime, nullable=True)
    next_attempt_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    last_error_code = Column(String(64), nullable=True)
    created_time = Column(DateTime, default=func.now())
    updated_time = Column(DateTime, default=func.now(), onupdate=func.now())


class AutoReplyRule(Base):
    __tablename__ = "auto_reply_rule"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    account_id = Column(BigInteger, nullable=True)
    rule_name = Column(String(200), nullable=True)
    match_type = Column(String(50), default="keyword", comment="keyword/ai/all")
    match_keywords = Column(Text, nullable=True)
    reply_content = Column(Text, nullable=True)
    reply_mode = Column(String(50), default="keyword", comment="keyword/ai")
    status = Column(SmallInteger, default=1, comment="1启用 0禁用")
    priority = Column(Integer, default=0)
    deleted = Column(SmallInteger, default=0)
    created_time = Column(DateTime, default=func.now())
    updated_time = Column(DateTime, default=func.now(), onupdate=func.now())


class QuickReplyTemplate(Base):
    """快捷回复模板：人工点击即插入到输入框的常用语，与自动回复规则解耦"""
    __tablename__ = "quick_reply_template"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    account_id = Column(BigInteger, nullable=True, comment="NULL 表示通用")
    title = Column(String(200), nullable=False, comment="模板标题")
    content = Column(Text, nullable=False, comment="模板内容")
    sort_order = Column(Integer, default=0, comment="排序，越小越靠前")
    status = Column(SmallInteger, default=1, comment="1启用 0禁用")
    deleted = Column(SmallInteger, default=0)
    created_time = Column(DateTime, default=func.now())
    updated_time = Column(DateTime, default=func.now(), onupdate=func.now())


class XianyuOperationLog(Base):
    __tablename__ = "operation_log"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    operator = Column(String(100), nullable=True, comment="操作人")
    operation_type = Column(String(100), nullable=True)
    operation_desc = Column(Text, nullable=True)
    target_type = Column(String(100), nullable=True)
    target_id = Column(String(100), nullable=True)
    ip_address = Column(String(50), nullable=True)
    created_time = Column(DateTime, default=func.now())


# ============================================================
# 后台动态配置模块
# ============================================================

class XianyuSysSetting(Base):
    __tablename__ = "xianyu_sys_setting"
    id = Column(Integer, primary_key=True, autoincrement=True)
    setting_key = Column(String(100), nullable=True)
    setting_value = Column(Text, nullable=True)
    created_time = Column(DateTime, default=func.now())
    updated_time = Column(DateTime, default=func.now(), onupdate=func.now())


class XianyuRemoteSliderSolveRecord(Base):
    """远程滑块求解记录：调用商业版远程 API 的求解记录。"""
    __tablename__ = "xianyu_remote_slider_solve_record"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    request_id = Column(String(64), nullable=False, unique=True)
    account_id = Column(BigInteger, nullable=True)
    account_name = Column(String(128), nullable=True)
    trigger_scene = Column(String(32), nullable=False, default="manual")
    status = Column(String(32), nullable=False, default="retrying")
    failure_reason = Column(String(64), nullable=False, default="")
    error_message = Column(Text, nullable=True)
    duration_ms = Column(Integer, nullable=False, default=0)
    token_charged = Column(Integer, nullable=False, default=0)
    remote_status = Column(String(32), nullable=False, default="")
    remote_solved = Column(SmallInteger, nullable=False, default=0)
    client_ip = Column(String(64), nullable=False, default="")
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

class XianyuAiProvider(Base):
    __tablename__ = "xianyu_ai_provider"
    id = Column(Integer, primary_key=True, autoincrement=True)
    provider_name = Column(String(100), nullable=True)
    api_key = Column(Text, nullable=True)
    base_url = Column(String(500), nullable=True)
    model_name = Column(String(200), nullable=True)
    status = Column(Integer, default=1)
    created_time = Column(DateTime, default=func.now())
    updated_time = Column(DateTime, default=func.now(), onupdate=func.now())


# ============================================================
# AI 模型配置模块
# ============================================================

class ModelConfig(Base):
    """AI 模型配置（聊天 / 文生图 等多模态模型统一管理）"""
    __tablename__ = "model_config"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    model_name = Column(String(200), nullable=False, comment="模型显示名称")
    provider = Column(String(100), nullable=True, comment="供应商: openai/azure/qwen/doubao 等")
    model_type = Column(String(50), default="chat", comment="chat/image/embedding")
    api_key = Column(Text, nullable=True)
    base_url = Column(String(500), nullable=True)
    real_model = Column(String(200), nullable=True, comment="实际调用模型名")
    params_json = Column(JSON, nullable=True, comment="调用参数(temperature/max_tokens 等)")
    is_default = Column(SmallInteger, default=0, comment="1默认 0非默认")
    status = Column(SmallInteger, default=1, comment="1启用 0禁用")
    remark = Column(Text, nullable=True)
    deleted = Column(SmallInteger, default=0)
    created_time = Column(DateTime, default=func.now())
    updated_time = Column(DateTime, default=func.now(), onupdate=func.now())


class ModelConfigImagePrompt(Base):
    """文生图模型配套提示词模板"""
    __tablename__ = "model_config_image_prompt"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    model_config_id = Column(BigInteger, nullable=False, comment="关联 model_config.id")
    prompt_name = Column(String(200), nullable=False, comment="提示词模板名称")
    prompt_content = Column(Text, nullable=True, comment="正向提示词")
    negative_prompt = Column(Text, nullable=True, comment="负向提示词")
    params_json = Column(JSON, nullable=True, comment="生成参数(size/quality/seed 等)")
    status = Column(SmallInteger, default=1, comment="1启用 0禁用")
    deleted = Column(SmallInteger, default=0)
    created_time = Column(DateTime, default=func.now())
    updated_time = Column(DateTime, default=func.now(), onupdate=func.now())


# ============================================================
# RAG 知识库模块
# ============================================================

class RagKnowledgeBase(Base):
    """RAG 知识库"""
    __tablename__ = "rag_knowledge_base"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False, comment="知识库名称")
    description = Column(Text, nullable=True, comment="描述说明")
    embedding_model = Column(String(200), nullable=True, comment="向量模型名")
    embedding_api_key = Column(Text, nullable=True)
    embedding_base_url = Column(String(500), nullable=True)
    doc_count = Column(Integer, default=0, comment="文档数")
    chunk_count = Column(Integer, default=0, comment="分块数")
    status = Column(SmallInteger, default=1, comment="1启用 0禁用")
    deleted = Column(SmallInteger, default=0)
    created_time = Column(DateTime, default=func.now())
    updated_time = Column(DateTime, default=func.now(), onupdate=func.now())


class RagDocument(Base):
    """RAG 文档"""
    __tablename__ = "rag_document"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    kb_id = Column(BigInteger, nullable=False, comment="关联 rag_knowledge_base.id")
    file_name = Column(String(500), nullable=True, comment="文件名")
    file_url = Column(Text, nullable=True, comment="文件URL")
    file_type = Column(String(50), nullable=True, comment="文件类型: pdf/txt/md/docx 等")
    file_size = Column(BigInteger, default=0, comment="文件大小(字节)")
    chunk_count = Column(Integer, default=0, comment="分块数")
    parse_status = Column(String(30), default="pending", comment="pending/parsing/ready/failed")
    error_message = Column(Text, nullable=True)
    deleted = Column(SmallInteger, default=0)
    created_time = Column(DateTime, default=func.now())
    updated_time = Column(DateTime, default=func.now(), onupdate=func.now())


class RagChunk(Base):
    """RAG 文档分块及向量"""
    __tablename__ = "rag_chunk"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    kb_id = Column(BigInteger, nullable=False, comment="关联 rag_knowledge_base.id")
    doc_id = Column(BigInteger, nullable=False, comment="关联 rag_document.id")
    chunk_index = Column(Integer, default=0, comment="分块序号")
    content = Column(Text, nullable=True, comment="分块文本内容")
    embedding = Column(Text, nullable=True, comment="向量(JSON 数组序列化)")
    token_count = Column(Integer, default=0, comment="token 数")
    created_time = Column(DateTime, default=func.now())


# ============================================================
# 敏感词模块
# ============================================================

class SensitiveWord(Base):
    """敏感词库（消息内容过滤）"""
    __tablename__ = "sensitive_word"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    word = Column(String(200), nullable=False, comment="敏感词")
    category = Column(String(50), nullable=True, comment="分类: politics/porn/ad/privacy 等")
    replace_to = Column(String(200), nullable=True, comment="替换为（为空则直接拦截）")
    status = Column(SmallInteger, default=1, comment="1启用 0禁用")
    deleted = Column(SmallInteger, default=0)
    created_time = Column(DateTime, default=func.now())
    updated_time = Column(DateTime, default=func.now(), onupdate=func.now())


# ============================================================
# 定时任务模块（替代原工作流调度）
# ============================================================

class ScheduledTask(Base):
    """定时任务表映射。

    运行时使用原子 raw SQL claim；此映射仍必须与版本化 migration 完全一致，
    API/Worker 启动只校验版本，不会自动修复模型与数据库之间的偏差。
    """
    __tablename__ = "scheduled_task"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    task_name = Column(String(200), nullable=True, comment="任务名称")
    task_type = Column(String(80), nullable=True, comment="sync_goods/sync_orders")
    cron_expr = Column(String(120), nullable=True, comment="cron 表达式")
    config = Column(JSON, nullable=True, comment="任务参数配置")
    last_run_time = Column(DateTime, nullable=True, comment="上次执行时间")
    next_run_time = Column(DateTime, nullable=True, comment="下次执行时间")
    last_status = Column(String(32), nullable=True, comment="最近执行状态")
    last_result = Column(JSON, nullable=True, comment="最近执行结果（已脱敏）")
    lease_token = Column(String(64), nullable=True, comment="执行租约令牌")
    lease_until = Column(DateTime, nullable=True, comment="租约到期时间")
    lease_owner = Column(String(128), nullable=True, comment="持有租约的 worker")
    status = Column(SmallInteger, default=1, comment="1启用 0禁用")
    deleted = Column(SmallInteger, default=0)
    created_time = Column(DateTime, default=func.now())
    updated_time = Column(DateTime, default=func.now(), onupdate=func.now())


class ManualDeliveryAttempt(Base):
    """Persistent state machine for duplicate-resistant manual delivery."""

    __tablename__ = "manual_delivery_attempt"
    __table_args__ = (
        UniqueConstraint("order_id", name="uk_manual_delivery_attempt_order"),
        UniqueConstraint("idempotency_key", name="uk_manual_delivery_attempt_key"),
        Index("idx_manual_delivery_attempt_state_lease", "state", "lease_until"),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    order_id = Column(BigInteger, nullable=False)
    account_id = Column(BigInteger, nullable=False)
    external_order_id = Column(String(200), nullable=False)
    idempotency_key = Column(String(128), nullable=False)
    content_digest = Column(String(64), nullable=False)
    delivery_record_id = Column(BigInteger, nullable=True)
    delivery_mode = Column(String(32), nullable=False, default="text")
    quantity_requested = Column(Integer, nullable=False, default=1)
    session_id = Column(String(200), nullable=False)
    peer_id = Column(String(200), nullable=False)
    item_id = Column(String(200), nullable=False)
    state = Column(String(32), nullable=False, default="pending")
    retry_scope = Column(String(32), nullable=True)
    retry_safe = Column(SmallInteger, nullable=False, default=1)
    attempt_count = Column(Integer, nullable=False, default=0)
    lease_token = Column(String(64), nullable=True)
    lease_until = Column(DateTime, nullable=True)
    message_started_at = Column(DateTime, nullable=True)
    message_confirmed_at = Column(DateTime, nullable=True)
    platform_confirmed_at = Column(DateTime, nullable=True)
    last_error_code = Column(String(64), nullable=True)
    error_message = Column(String(500), nullable=True)
    created_time = Column(DateTime, default=func.now())
    updated_time = Column(DateTime, default=func.now(), onupdate=func.now())


class ManualMessageAttempt(Base):
    """Durable single-flight state for operator-triggered chat messages."""

    __tablename__ = "manual_message_attempt"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key",
            name="uk_manual_message_attempt_key",
        ),
        Index("idx_manual_message_attempt_state_lease", "state", "lease_until"),
        Index("idx_manual_message_attempt_account_created", "account_id", "created_time"),
    )

    id = Column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    idempotency_key = Column(String(128), nullable=False)
    account_id = Column(BigInteger, nullable=False)
    session_digest = Column(String(64), nullable=False)
    peer_digest = Column(String(64), nullable=False)
    payload_digest = Column(String(64), nullable=False)
    message_type = Column(String(16), nullable=False)
    state = Column(String(32), nullable=False, default="pending")
    retry_safe = Column(SmallInteger, nullable=False, default=1)
    attempt_count = Column(Integer, nullable=False, default=0)
    lease_token = Column(String(64), nullable=True)
    lease_until = Column(DateTime, nullable=True)
    send_started_at = Column(DateTime, nullable=True)
    confirmed_at = Column(DateTime, nullable=True)
    local_message_id = Column(BigInteger, nullable=True)
    platform_message_digest = Column(String(64), nullable=True)
    last_error_code = Column(String(64), nullable=True)
    created_time = Column(DateTime, default=func.now())
    updated_time = Column(DateTime, default=func.now(), onupdate=func.now())


class RealtimeDeliveryAttempt(Base):
    """Durable single-flight state for real-time automatic delivery."""

    __tablename__ = "realtime_delivery_attempt"
    __table_args__ = (
        UniqueConstraint("event_key", name="uk_realtime_delivery_attempt_event"),
        Index("idx_realtime_delivery_attempt_state_lease", "state", "lease_until"),
        Index(
            "idx_realtime_delivery_attempt_account_order",
            "account_id",
            "external_order_id",
        ),
        Index("idx_realtime_delivery_attempt_record", "delivery_record_id"),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    event_key = Column(String(64), nullable=False)
    account_id = Column(BigInteger, nullable=False)
    external_order_id = Column(String(200), nullable=True)
    source_event_id = Column(String(200), nullable=False)
    session_id = Column(String(200), nullable=False)
    peer_id = Column(String(200), nullable=False)
    item_id = Column(String(200), nullable=False)
    rule_id = Column(BigInteger, nullable=True)
    delivery_record_id = Column(BigInteger, nullable=True)
    delivery_mode = Column(String(32), nullable=False)
    content_digest = Column(String(64), nullable=False)
    quantity_requested = Column(Integer, nullable=False, default=1)
    card_group_id = Column(BigInteger, nullable=True)
    auto_confirm_shipment = Column(SmallInteger, nullable=False, default=0)
    state = Column(String(32), nullable=False, default="pending")
    retry_scope = Column(String(32), nullable=True)
    retry_safe = Column(SmallInteger, nullable=False, default=1)
    attempt_count = Column(Integer, nullable=False, default=0)
    lease_token = Column(String(64), nullable=True)
    lease_until = Column(DateTime, nullable=True)
    message_started_at = Column(DateTime, nullable=True)
    message_confirmed_at = Column(DateTime, nullable=True)
    platform_confirm_started_at = Column(DateTime, nullable=True)
    platform_confirmed_at = Column(DateTime, nullable=True)
    last_error_code = Column(String(64), nullable=True)
    error_message = Column(String(500), nullable=True)
    created_time = Column(DateTime, default=func.now())
    updated_time = Column(DateTime, default=func.now(), onupdate=func.now())


class AiAutoReplyAttempt(Base):
    """Durable single-flight state for buyer-facing AI replies."""

    __tablename__ = "ai_auto_reply_attempt"
    __table_args__ = (
        UniqueConstraint("event_key", name="uk_ai_auto_reply_attempt_event"),
        UniqueConstraint(
            "account_id",
            "source_message_digest",
            name="uk_ai_auto_reply_attempt_source",
        ),
        Index("idx_ai_auto_reply_state_lease", "state", "lease_until"),
        Index("idx_ai_auto_reply_account_created", "account_id", "created_time"),
    )

    id = Column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    event_key = Column(String(64), nullable=False)
    account_id = Column(BigInteger, nullable=False)
    source_message_digest = Column(String(64), nullable=False)
    request_digest = Column(String(64), nullable=False)
    session_id = Column(String(200), nullable=False)
    peer_id = Column(String(200), nullable=False)
    goods_id = Column(String(200), nullable=True)
    seller_external_uid = Column(String(200), nullable=True)
    state = Column(String(32), nullable=False, default="pending")
    retry_scope = Column(String(32), nullable=True)
    retry_safe = Column(SmallInteger, nullable=False, default=1)
    attempt_count = Column(Integer, nullable=False, default=0)
    lease_token = Column(String(64), nullable=True)
    lease_until = Column(DateTime, nullable=True)
    generation_started_at = Column(DateTime, nullable=True)
    message_started_at = Column(DateTime, nullable=True)
    message_confirmed_at = Column(DateTime, nullable=True)
    local_confirmed_at = Column(DateTime, nullable=True)
    reply_digest = Column(String(64), nullable=True)
    encrypted_reply = Column(Text, nullable=True)
    local_message_id = Column(BigInteger, nullable=True)
    quota_date = Column(Date, nullable=True)
    quota_status = Column(
        String(16),
        nullable=True,
        comment="reserved/consumed/released",
    )
    policy_timezone = Column(String(64), nullable=True)
    last_error_code = Column(String(64), nullable=True)
    error_message = Column(String(500), nullable=True)
    created_time = Column(DateTime, default=func.now())
    updated_time = Column(DateTime, default=func.now(), onupdate=func.now())


class AiAutoReplyDailyQuota(Base):
    """Atomic per-account, policy-local-day AI reply quota counters."""

    __tablename__ = "ai_auto_reply_daily_quota"
    __table_args__ = (
        Index("idx_ai_reply_quota_date", "quota_date"),
    )

    account_id = Column(BigInteger, primary_key=True)
    quota_date = Column(Date, primary_key=True)
    occupied_count = Column(Integer, nullable=False, default=0)
    consumed_count = Column(Integer, nullable=False, default=0)
    released_count = Column(Integer, nullable=False, default=0)
    created_time = Column(DateTime, default=func.now())
    updated_time = Column(DateTime, default=func.now(), onupdate=func.now())


class RemoteGoodsDeleteAttempt(Base):
    """Durable single-flight state for irreversible platform deletion."""

    __tablename__ = "remote_goods_delete_attempt"
    __table_args__ = (
        UniqueConstraint("goods_id", name="uk_remote_goods_delete_attempt_goods"),
        UniqueConstraint("idempotency_key", name="uk_remote_goods_delete_attempt_key"),
        Index("idx_remote_goods_delete_attempt_state_lease", "state", "lease_until"),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    goods_id = Column(BigInteger, nullable=False)
    account_id = Column(BigInteger, nullable=False)
    external_goods_id = Column(String(200), nullable=False)
    idempotency_key = Column(String(128), nullable=False)
    state = Column(String(32), nullable=False, default="pending")
    retry_safe = Column(SmallInteger, nullable=False, default=1)
    attempt_count = Column(Integer, nullable=False, default=0)
    lease_token = Column(String(64), nullable=True)
    lease_until = Column(DateTime, nullable=True)
    remote_started_at = Column(DateTime, nullable=True)
    remote_confirmed_at = Column(DateTime, nullable=True)
    local_deleted_at = Column(DateTime, nullable=True)
    last_error_code = Column(String(64), nullable=True)
    error_message = Column(String(500), nullable=True)
    created_time = Column(DateTime, default=func.now())
    updated_time = Column(DateTime, default=func.now(), onupdate=func.now())


class GoodsOffShelfAttempt(Base):
    """Durable state for duplicate-resistant platform off-shelf operations."""

    __tablename__ = "goods_off_shelf_attempt"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uk_goods_off_shelf_attempt_key"),
        Index("idx_goods_off_shelf_goods_created", "goods_id", "created_time"),
        Index("idx_goods_off_shelf_state_lease", "state", "lease_until"),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    goods_id = Column(BigInteger, nullable=False)
    account_id = Column(BigInteger, nullable=False)
    external_goods_id = Column(String(200), nullable=False)
    idempotency_key = Column(String(128), nullable=False)
    state = Column(String(32), nullable=False, default="pending")
    retry_safe = Column(SmallInteger, nullable=False, default=1)
    attempt_count = Column(Integer, nullable=False, default=0)
    lease_token = Column(String(64), nullable=True)
    lease_until = Column(DateTime, nullable=True)
    remote_started_at = Column(DateTime, nullable=True)
    remote_confirmed_at = Column(DateTime, nullable=True)
    local_confirmed_at = Column(DateTime, nullable=True)
    last_error_code = Column(String(64), nullable=True)
    error_message = Column(String(500), nullable=True)
    created_time = Column(DateTime, default=func.now())
    updated_time = Column(DateTime, default=func.now(), onupdate=func.now())



class ExternalOperationAttempt(Base):
    """Durable state for duplicate-resistant publish and price operations."""

    __tablename__ = "external_operation_attempt"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uk_external_operation_attempt_key"),
        Index("idx_external_operation_state_lease", "operation_type", "state", "lease_until"),
        Index(
            "idx_external_operation_target_created",
            "operation_type",
            "target_local_id",
            "created_time",
            "id",
        ),
    )

    id = Column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    operation_type = Column(String(32), nullable=False)
    account_id = Column(BigInteger, nullable=False)
    idempotency_key = Column(String(128), nullable=False)
    request_digest = Column(String(64), nullable=False)
    target_local_id = Column(BigInteger, nullable=True)
    remote_reference_id = Column(String(200), nullable=True)
    remote_reference_url = Column(Text, nullable=True)
    state = Column(String(32), nullable=False, default="pending")
    retry_scope = Column(String(32), nullable=True)
    retry_safe = Column(SmallInteger, nullable=False, default=1)
    attempt_count = Column(Integer, nullable=False, default=0)
    lease_token = Column(String(64), nullable=True)
    lease_until = Column(DateTime, nullable=True)
    remote_started_at = Column(DateTime, nullable=True)
    remote_confirmed_at = Column(DateTime, nullable=True)
    local_confirmed_at = Column(DateTime, nullable=True)
    local_result_id = Column(BigInteger, nullable=True)
    last_error_code = Column(String(64), nullable=True)
    error_message = Column(String(500), nullable=True)
    created_time = Column(DateTime, default=func.now())
    updated_time = Column(DateTime, default=func.now(), onupdate=func.now())


class AdPaymentOrderTargetMutex(Base):
    """One lock row per commercial application across payment generations."""

    __tablename__ = "ad_payment_order_target_mutex"
    __table_args__ = (
        Index("idx_ad_payment_target_latest", "latest_attempt_id"),
    )

    application_id = Column(BigInteger, primary_key=True)
    latest_attempt_id = Column(BigInteger, nullable=True)
    created_time = Column(DateTime, default=func.now())
    updated_time = Column(DateTime, default=func.now(), onupdate=func.now())


class AdPaymentOrderAttempt(Base):
    """Durable generation state for commercial payment-order creation."""

    __tablename__ = "ad_payment_order_attempt"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key",
            name="uk_ad_payment_order_attempt_key",
        ),
        UniqueConstraint(
            "remote_order_no",
            name="uk_ad_payment_order_attempt_remote_order",
        ),
        Index(
            "idx_ad_payment_order_application_created",
            "application_id",
            "created_time",
            "id",
        ),
        Index("idx_ad_payment_order_state_lease", "state", "lease_until"),
    )

    id = Column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    application_id = Column(BigInteger, nullable=False)
    idempotency_key = Column(String(128), nullable=False)
    payload_digest = Column(String(64), nullable=False)
    payment_method = Column(String(64), nullable=False)
    state = Column(String(32), nullable=False, default="pending")
    retry_safe = Column(SmallInteger, nullable=False, default=1)
    attempt_count = Column(Integer, nullable=False, default=0)
    lease_token = Column(String(64), nullable=True)
    lease_until = Column(DateTime, nullable=True)
    remote_started_at = Column(DateTime, nullable=True)
    remote_confirmed_at = Column(DateTime, nullable=True)
    remote_order_no = Column(String(128), nullable=True)
    remote_status = Column(String(32), nullable=True)
    last_error_code = Column(String(64), nullable=True)
    error_message = Column(String(500), nullable=True)
    created_time = Column(DateTime, default=func.now())
    updated_time = Column(DateTime, default=func.now(), onupdate=func.now())


class NotificationTestTargetMutex(Base):
    """Serialize test sends for one saved notification channel."""

    __tablename__ = "notification_test_target_mutex"
    __table_args__ = (
        Index("idx_notification_test_target_latest", "latest_attempt_id"),
    )

    user_id = Column(BigInteger, primary_key=True)
    channel_key = Column(String(80), primary_key=True)
    latest_attempt_id = Column(
        BigInteger,
        ForeignKey(
            "notification_test_attempt.id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=True,
    )
    created_time = Column(DateTime, default=func.now())
    updated_time = Column(DateTime, default=func.now(), onupdate=func.now())


class NotificationTestAttempt(Base):
    """Metadata-only durable state for operator-triggered notification tests."""

    __tablename__ = "notification_test_attempt"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key",
            name="uk_notification_test_attempt_key",
        ),
        Index(
            "idx_notification_test_target_created",
            "user_id",
            "channel_key",
            "created_time",
            "id",
        ),
        Index("idx_notification_test_state_lease", "state", "lease_until"),
    )

    id = Column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    user_id = Column(BigInteger, nullable=False)
    channel_key = Column(String(80), nullable=False)
    idempotency_key = Column(String(128), nullable=False)
    payload_digest = Column(String(64), nullable=False)
    state = Column(String(32), nullable=False, default="pending")
    retry_safe = Column(SmallInteger, nullable=False, default=1)
    attempt_count = Column(Integer, nullable=False, default=0)
    lease_token = Column(String(64), nullable=True)
    lease_until = Column(DateTime, nullable=True)
    send_started_at = Column(DateTime, nullable=True)
    confirmed_at = Column(DateTime, nullable=True)
    provider_success = Column(SmallInteger, nullable=True)
    provider_status_code = Column(Integer, nullable=True)
    cost_ms = Column(Integer, nullable=True)
    result_code = Column(String(64), nullable=True)
    log_persisted = Column(SmallInteger, nullable=False, default=0)
    last_error_code = Column(String(64), nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    resolution_code = Column(String(64), nullable=True)
    created_time = Column(DateTime, default=func.now())
    updated_time = Column(DateTime, default=func.now(), onupdate=func.now())


class NotificationEventAttempt(Base):
    """Metadata-only durable generation for automatic event delivery."""

    __tablename__ = "notification_event_attempt"
    __table_args__ = (
        UniqueConstraint(
            "event_type",
            "account_id",
            "target_digest",
            "generation",
            name="uk_notification_event_generation",
        ),
        Index(
            "idx_notification_event_target_created",
            "event_type",
            "account_id",
            "target_digest",
            "created_time",
            "id",
        ),
        Index("idx_notification_event_state_lease", "state", "lease_until"),
        Index("idx_notification_event_retry", "state", "next_retry_at"),
    )

    id = Column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    event_type = Column(String(64), nullable=False)
    account_id = Column(BigInteger, nullable=False)
    target_digest = Column(String(64), nullable=False)
    generation = Column(Integer, nullable=False)
    state = Column(String(32), nullable=False, default="pending")
    attempt_count = Column(Integer, nullable=False, default=0)
    lease_token = Column(String(64), nullable=True)
    lease_until = Column(DateTime, nullable=True)
    send_started_at = Column(DateTime, nullable=True)
    confirmed_at = Column(DateTime, nullable=True)
    generation_expires_at = Column(DateTime, nullable=True)
    next_retry_at = Column(DateTime, nullable=True)
    provider_called = Column(SmallInteger, nullable=True)
    delivered = Column(SmallInteger, nullable=True)
    outcome_known = Column(SmallInteger, nullable=True)
    last_error_code = Column(String(64), nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    resolution_code = Column(String(64), nullable=True)
    created_time = Column(DateTime, default=func.now())
    updated_time = Column(DateTime, default=func.now(), onupdate=func.now())


class NotificationEventTargetMutex(Base):
    """Serialize one automatic event target across retained generations."""

    __tablename__ = "notification_event_target_mutex"
    __table_args__ = (
        Index("idx_notification_event_target_latest", "latest_attempt_id"),
    )

    event_type = Column(String(64), primary_key=True)
    account_id = Column(BigInteger, primary_key=True)
    target_digest = Column(String(64), primary_key=True)
    latest_attempt_id = Column(
        BigInteger,
        ForeignKey(
            "notification_event_attempt.id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        nullable=True,
    )
    created_time = Column(DateTime, default=func.now())
    updated_time = Column(DateTime, default=func.now(), onupdate=func.now())


class AdminUser(Base):
    """管理员账号表，支持邮箱验证码登录、注册与重置密码。

    与 legacy 的 admin_password_hash 设置共存：迁移 025 会在表为空时
    根据已配置的 admin_password_hash 自动种入默认超级管理员。
    """

    __tablename__ = "admin_user"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    username = Column(String(100), nullable=False, unique=True, index=True)
    email = Column(String(200), nullable=True, unique=True, index=True)
    password_hash = Column(String(255), nullable=False)
    is_super = Column(SmallInteger, nullable=True, default=0, comment="1=超级管理员")
    status = Column(SmallInteger, nullable=True, default=1, comment="1=启用 0=禁用")
    created_time = Column(DateTime, default=func.now())
    updated_time = Column(DateTime, default=func.now(), onupdate=func.now())
