"""
评价管理服务（开源版）。

参考商业版 automation-service/app/services/rate_service.py，
适配开源版单管理员架构（移除 tenant_id 维度）。

关键约束：
- 仅鱼小铺账号（xianyu_account.fish_shop_user=1）允许调用评价接口
- 评价记录以 (account_id, external_order_id) 唯一
- 创建评价属于写操作，必须二次确认 + 后端再次校验账号归属与鱼小铺权限
- 仅允许经过白名单的 MTOP API（mtop.taobao.idle.merchant.rate.list / .create）
- 不在日志/响应中暴露 Cookie / _m_h5_tk / sign

评价等级映射（已通过真实接口样本确认）：
- 好评 rate=1、中评 rate=-1、差评 rate=0
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import select, and_, update, func, desc, or_
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.entities import (
    XianyuAccount,
    XianyuAccountAuth,
    XianyuRate,
    XianyuRateSyncTask,
    XianyuRateAccountState,
)
from .xianyu_api_service import (
    _post_mtop_with_token_retry,
    _get_account_auth,
    _decrypt_value,
)

logger = logging.getLogger(__name__)

# ============================================================
# 常量与配置
# ============================================================

# 评价列表接口
RATE_LIST_API = "mtop.taobao.idle.merchant.rate.list"
# 创建评价接口（白名单）
RATE_CREATE_API = "mtop.taobao.idle.merchant.rate.create"
# 允许执行的 MTOP 操作 API 白名单
ALLOWED_MTOP_ACTION_APIS = frozenset({RATE_CREATE_API})

# 全部订单查询的 queryCode
QUERY_CODE_ALL = "ALL"
# queryType 当前确认值
QUERY_TYPE_ORDER = "ORDER"

# 分页保护：单账号单轮同步最大页数
MAX_PAGES_PER_ACCOUNT = 50
# 默认每页大小
DEFAULT_PAGE_SIZE = 20

# 缓存过期策略：单账号 60 秒，全部账号 120 秒
SINGLE_ACCOUNT_CACHE_TTL_SECONDS = 60
ALL_ACCOUNTS_CACHE_TTL_SECONDS = 120
# 完整同步间隔：30 分钟
FULL_SYNC_INTERVAL_SECONDS = 30 * 60

# 多账号并发控制
MAX_CONCURRENT_ACCOUNTS = 3
# 单账号分页请求间隔（避免风控）
PAGE_REQUEST_INTERVAL_SECONDS = 0.5

# 评价等级映射（已通过真实接口样本确认）
RATE_LEVEL_GOOD = 1  # 好评
RATE_LEVEL_NEUTRAL = -1  # 中评
RATE_LEVEL_BAD = 0  # 差评
# 已确认的可提交等级集合
CONFIRMED_RATE_LEVELS: frozenset[int] = frozenset({
    RATE_LEVEL_GOOD,
    RATE_LEVEL_NEUTRAL,
    RATE_LEVEL_BAD,
})

# 评价内容最大长度
RATE_FEEDBACK_MAX_LENGTH = 500

# 进程内同步任务去重锁（同账号同时只能一轮同步）
_account_sync_locks: dict[int, asyncio.Lock] = {}
_locks_guard = asyncio.Lock()

# 创建评价幂等锁（同账号同订单同时只能一个创建请求）
_create_rate_locks: dict[tuple[int, str], asyncio.Lock] = {}
_create_locks_guard = asyncio.Lock()


# ============================================================
# 工具函数
# ============================================================

def _safe_int(value: Any, default: int = 0) -> int:
    """安全解析整数，支持字符串形式（如 '27' / 'true'）。"""
    if value is None:
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).strip().lower()
    if not s:
        return default
    try:
        return int(s)
    except (ValueError, TypeError):
        return default


def _parse_bool_string(value: Any) -> bool:
    """安全解析字符串布尔值：'true' → True, 'false' → False。"""
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    s = str(value).strip().lower()
    return s in ("true", "1", "yes", "y")


def _parse_datetime(value: Any) -> Optional[datetime]:
    """解析时间字符串/数字，支持毫秒时间戳和 ISO 字符串。"""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        try:
            ts = float(value)
            if ts > 1e12:  # 毫秒
                ts = ts / 1000
            return datetime.fromtimestamp(ts)
        except (ValueError, OSError):
            return None
    s = str(value).strip()
    if not s:
        return None
    try:
        clean = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean)
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        return dt
    except (ValueError, TypeError):
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _mask_buyer_nick(nick: Any) -> Optional[str]:
    """脱敏买家昵称：保留首尾字符，中间用 * 替换。"""
    if not nick or not isinstance(nick, str):
        return None
    s = nick.strip()
    if not s:
        return None
    if len(s) <= 2:
        return s[0] + "*" if len(s) == 2 else s
    return s[0] + "*" * (len(s) - 2) + s[-1]


def _mask_mail_no(mail_no: Any) -> Optional[str]:
    """脱敏物流单号：保留前4后4，中间用 * 替换。"""
    if not mail_no or not isinstance(mail_no, str):
        return None
    s = mail_no.strip()
    if not s:
        return None
    if len(s) <= 8:
        return s
    return s[:4] + "*" * (len(s) - 8) + s[-4:]


def _to_str(value: Any) -> Optional[str]:
    """将值转为字符串，None/空 保持 None。"""
    if value is None:
        return None
    s = str(value).strip()
    return s if s else None


# ============================================================
# 评价记录字段映射
# ============================================================

def _extract_rate_fields(raw_item: dict, account_id: int) -> Optional[dict]:
    """从闲鱼评价列表接口单条记录提取标准化字段。

    字段结构：
    - merchantBuyerVO: buyerId / userIcon / userNick
    - merchantCommonData: orderId / itemId / orderStatus / consignTime / createTime /
                         finishTime / paySuccessTime / sellerRateStatus / inRefund /
                         companyName / mailNo
    - merchantItemVO: itemPicUrl / title / itemInfoLines
    - rateItemVOList: feedBack / gmtCreate / illegal / main / pictCdnUrlList /
                     rate / rateId / raterHeadImg / seller
    """
    if not isinstance(raw_item, dict):
        return None

    buyer_vo = raw_item.get("merchantBuyerVO") or {}
    common_data = raw_item.get("merchantCommonData") or {}
    item_vo = raw_item.get("merchantItemVO") or {}
    rate_list = raw_item.get("rateItemVOList") or []
    if not isinstance(rate_list, list):
        rate_list = []

    # orderId 必须存在（用于唯一标识）
    order_id = common_data.get("orderId") or raw_item.get("orderId")
    if order_id is None or order_id == "":
        return None
    order_id_str = str(order_id)

    item_id = common_data.get("itemId") or raw_item.get("itemId")
    item_id_str = _to_str(item_id)

    # 买家信息
    buyer_id = _to_str(buyer_vo.get("buyerId"))
    buyer_nick_raw = buyer_vo.get("userNick")
    buyer_nick = _mask_buyer_nick(buyer_nick_raw)
    buyer_icon = buyer_vo.get("userIcon")

    # 商品信息
    item_title = item_vo.get("title") or raw_item.get("title")
    item_pic_url = item_vo.get("itemPicUrl")
    item_info_lines = item_vo.get("itemInfoLines")
    if item_info_lines is not None and not isinstance(item_info_lines, str):
        item_info_lines = json.dumps(item_info_lines, ensure_ascii=False)

    # 订单与交易信息
    order_status = _to_str(common_data.get("orderStatus"))
    seller_rate_status = _to_str(common_data.get("sellerRateStatus"))
    in_refund = _to_str(common_data.get("inRefund"))
    consign_time = _parse_datetime(common_data.get("consignTime"))
    order_create_time = _parse_datetime(common_data.get("createTime"))
    pay_success_time = _parse_datetime(common_data.get("paySuccessTime"))
    finish_time = _parse_datetime(common_data.get("finishTime"))
    company_name = _to_str(common_data.get("companyName"))
    mail_no = _mask_mail_no(common_data.get("mailNo"))

    # 解析 rateItemVOList：区分买家评价（seller=false）和卖家评价（seller=true）
    buyer_rate_content = None
    buyer_rate_level = None
    buyer_rate_time = None
    buyer_rate_images = None
    seller_rate_content = None
    seller_rate_level = None
    seller_rate_time = None
    seller_rate_images = None
    seller_rate_id = None
    has_seller_rate = 0

    for rate_item in rate_list:
        if not isinstance(rate_item, dict):
            continue
        # seller 字段必须做类型转换（字符串 "false" 不能当作 True）
        seller_flag = _parse_bool_string(rate_item.get("seller"))
        feed_back = rate_item.get("feedBack")
        gmt_create = _parse_datetime(rate_item.get("gmtCreate"))
        rate_level = _to_str(rate_item.get("rate"))
        rate_id = _to_str(rate_item.get("rateId"))
        pict_urls = rate_item.get("pictCdnUrlList")
        if pict_urls is None:
            pict_urls = []
        if not isinstance(pict_urls, list):
            pict_urls = []

        if seller_flag:
            # 卖家评价
            has_seller_rate = 1
            seller_rate_content = feed_back
            seller_rate_level = rate_level
            seller_rate_time = gmt_create
            seller_rate_images = json.dumps(pict_urls, ensure_ascii=False) if pict_urls else None
            seller_rate_id = rate_id
        else:
            # 买家评价
            buyer_rate_content = feed_back
            buyer_rate_level = rate_level
            buyer_rate_time = gmt_create
            buyer_rate_images = json.dumps(pict_urls, ensure_ascii=False) if pict_urls else None

    # 评价可否判断：
    # - 已存在 seller=true 评价 → 不可评价
    # - 否则暂标记为"可评价"（rate_reviewable=1）
    rate_reviewable = 0 if has_seller_rate == 1 else 1

    # 脱敏后的原始 JSON
    raw_json_for_storage = {
        "merchantBuyerVO": {
            "buyerId": buyer_id,
            "userNick": buyer_nick,
            "userIcon": buyer_icon,
        },
        "merchantCommonData": {
            "orderId": order_id_str,
            "itemId": item_id_str,
            "orderStatus": order_status,
            "sellerRateStatus": seller_rate_status,
            "inRefund": in_refund,
            "consignTime": common_data.get("consignTime"),
            "createTime": common_data.get("createTime"),
            "paySuccessTime": common_data.get("paySuccessTime"),
            "finishTime": common_data.get("finishTime"),
            "companyName": company_name,
            "mailNo": _mask_mail_no(common_data.get("mailNo")),
            "showDetail": common_data.get("showDetail"),
        },
        "merchantItemVO": {
            "itemPicUrl": item_pic_url,
            "title": item_title,
            "itemInfoLines": item_vo.get("itemInfoLines"),
        },
        "rateItemVOList": rate_list,
    }

    return {
        "account_id": account_id,
        "external_order_id": order_id_str,
        "external_item_id": item_id_str,
        "buyer_id": buyer_id,
        "buyer_nick": buyer_nick,
        "buyer_icon": buyer_icon,
        "item_title": item_title,
        "item_pic_url": item_pic_url,
        "item_info_lines": item_info_lines,
        "order_status": order_status,
        "seller_rate_status": seller_rate_status,
        "in_refund": in_refund,
        "consign_time": consign_time,
        "order_create_time": order_create_time,
        "pay_success_time": pay_success_time,
        "finish_time": finish_time,
        "logistics_company": company_name,
        "logistics_mail_no": mail_no,
        "buyer_rate_content": buyer_rate_content,
        "buyer_rate_level": buyer_rate_level,
        "buyer_rate_time": buyer_rate_time,
        "buyer_rate_images": buyer_rate_images,
        "seller_rate_content": seller_rate_content,
        "seller_rate_level": seller_rate_level,
        "seller_rate_time": seller_rate_time,
        "seller_rate_images": seller_rate_images,
        "seller_rate_id": seller_rate_id,
        "has_seller_rate": has_seller_rate,
        "rate_reviewable": rate_reviewable,
        "raw_json": json.dumps(raw_json_for_storage, ensure_ascii=False),
        "sync_status": "synced",
        "last_synced_time": datetime.now(),
    }


# ============================================================
# 闲鱼评价列表接口调用
# ============================================================

def fetch_rate_list_page(
    account_id: int,
    page_number: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    query_code: str = QUERY_CODE_ALL,
    timeout: int = 20,
) -> Optional[dict]:
    """拉取闲鱼评价列表的单页数据。

    data 结构：
        {
          "pageNumber": 1,
          "rowsPerPage": 20,
          "queryType": "ORDER",
          "rateSearchParam": {"queryCode": "ALL"}
        }
    """
    auth = _get_account_auth(account_id)
    if not auth:
        return {"success": False, "error": "无法获取账号认证信息"}

    cookie_str = _decrypt_value(auth.get("encrypted_cookie") or "")
    if not cookie_str:
        return {"success": False, "error": "Cookie为空"}

    data_obj = {
        "pageNumber": page_number,
        "rowsPerPage": page_size,
        "queryType": QUERY_TYPE_ORDER,
        "rateSearchParam": {
            "queryCode": query_code,
        },
    }
    data_str = json.dumps(data_obj, separators=(",", ":"), ensure_ascii=False)

    result = _post_mtop_with_token_retry(
        account_id, cookie_str, RATE_LIST_API, data_str, timeout
    )
    if not result.get("success"):
        return {
            "success": False,
            "error": result.get("error") or "评价列表接口调用失败",
            "ret": result.get("ret"),
        }

    # 响应结构：data.module.items
    outer_data = result.get("data") or {}
    if not isinstance(outer_data, dict):
        return {"success": False, "error": "评价列表返回结构异常", "ret": result.get("ret")}

    module = outer_data.get("module") or {}
    if not isinstance(module, dict):
        return {"success": False, "error": "评价列表返回结构异常", "ret": result.get("ret")}

    items = module.get("items") or []
    if not isinstance(items, list):
        items = []

    # 分页字段可能是字符串形式（"true"/"27"/"0"），需安全规范化
    next_page = _parse_bool_string(module.get("nextPage"))
    total_count = _safe_int(module.get("totalCount"))
    last_end_row = _safe_int(module.get("lastEndRow"))

    return {
        "success": True,
        "data": {
            "items": items,
            "nextPage": next_page,
            "totalCount": total_count,
            "lastEndRow": last_end_row,
        },
    }


def call_create_rate(
    account_id: int,
    order_id: str,
    rate: int,
    feedback: str,
    anonymous: bool,
    timeout: int = 20,
) -> dict:
    """调用创建评价接口（mtop.taobao.idle.merchant.rate.create）。

    参数：
        {
          "tradeIdList": ["orderId"],
          "imageUrls": [],
          "rate": 1,
          "feedback": "评价内容",
          "anonymous": true
        }
    """
    if not order_id or not isinstance(order_id, str):
        return {"success": False, "error": "orderId 不能为空"}

    auth = _get_account_auth(account_id)
    if not auth:
        return {"success": False, "error": "无法获取账号认证信息"}

    cookie_str = _decrypt_value(auth.get("encrypted_cookie") or "")
    if not cookie_str:
        return {"success": False, "error": "Cookie为空"}

    data_obj = {
        "tradeIdList": [order_id],
        "imageUrls": [],
        "rate": rate,
        "feedback": feedback,
        "anonymous": bool(anonymous),
    }
    data_str = json.dumps(data_obj, separators=(",", ":"), ensure_ascii=False)

    result = _post_mtop_with_token_retry(
        account_id, cookie_str, RATE_CREATE_API, data_str, timeout
    )
    if not result.get("success"):
        return {
            "success": False,
            "error": result.get("error") or "创建评价接口调用失败",
            "ret": result.get("ret"),
        }

    return {"success": True, "data": result.get("data") or {}}


# ============================================================
# 创建评价成功判断
# ============================================================

def judge_create_rate_success(
    api_result: dict, order_id: str
) -> tuple[bool, str, Optional[dict]]:
    """判断创建评价是否真正成功。

    不能只判断 HTTP 200，至少检查：
    - ret 中是否包含 SUCCESS
    - data.module.success 是否为 true
    - successOrderIds 是否包含当前 orderId
    - failOrderInfos 中是否不存在当前订单失败信息
    """
    if not api_result.get("success"):
        return False, api_result.get("error") or "创建评价接口调用失败", None

    outer_data = api_result.get("data") or {}
    if not isinstance(outer_data, dict):
        return False, "创建评价返回结构异常", None

    module = outer_data.get("module") or {}
    if not isinstance(module, dict) or not module:
        return False, "创建评价返回 module 为空", None

    # module.success 必须为 true
    module_success = _parse_bool_string(module.get("success"))
    if not module_success:
        err_msg = "服务端返回 success=false"
        fail_infos = module.get("failOrderInfos") or []
        if isinstance(fail_infos, list) and fail_infos:
            for fi in fail_infos:
                if isinstance(fi, dict) and str(fi.get("orderId")) == str(order_id):
                    err_msg = f"服务端返回失败: {fi.get('failReason') or fi.get('message') or '未知原因'}"
                    break
        return False, err_msg, module

    # successOrderIds 必须包含当前 orderId
    success_ids = module.get("successOrderIds") or []
    if not isinstance(success_ids, list):
        success_ids = []
    success_ids_str = [str(x) for x in success_ids]

    if order_id not in success_ids_str:
        fail_infos = module.get("failOrderInfos") or []
        if isinstance(fail_infos, list) and fail_infos:
            for fi in fail_infos:
                if isinstance(fi, dict) and str(fi.get("orderId")) == str(order_id):
                    return False, f"订单评价失败: {fi.get('failReason') or fi.get('message') or '未知原因'}", module
        # 既不在成功列表也不在失败列表，按未确认处理
        return False, "订单未出现在成功列表中，评价结果未确认", module

    fail_infos = module.get("failOrderInfos") or []
    if isinstance(fail_infos, list) and fail_infos:
        for fi in fail_infos:
            if isinstance(fi, dict) and str(fi.get("orderId")) == str(order_id):
                return False, f"订单评价失败: {fi.get('failReason') or fi.get('message') or '未知原因'}", module

    return True, "评价成功", module


# ============================================================
# 鱼小铺账号校验
# ============================================================

async def verify_fish_shop_account(
    db: AsyncSession, account_id: int
) -> tuple[bool, Optional[XianyuAccountAuth], str]:
    """校验账号是否为鱼小铺账号，并返回 auth。

    复用项目现有权限逻辑：xianyu_account.fish_shop_user=1 表示鱼小铺账号（商家）。
    评价管理接口（mtop.taobao.idle.merchant.rate.*）为商家接口。

    返回 (is_fish_shop, auth, error_msg)。
    """
    result = await db.execute(
        select(XianyuAccount).where(
            and_(
                XianyuAccount.id == account_id,
                XianyuAccount.deleted == 0,
            )
        )
    )
    account = result.scalar_one_or_none()
    if not account:
        return False, None, "账号不存在或已删除"
    if not bool(account.fish_shop_user):
        return False, None, "当前闲鱼账号不支持评价管理，只有鱼小铺账号可以使用"

    result = await db.execute(
        select(XianyuAccountAuth).where(
            XianyuAccountAuth.account_id == account_id,
        )
    )
    auth = result.scalar_one_or_none()
    if not auth or not auth.encrypted_cookie:
        return True, None, "账号 Cookie 未配置或已失效，请先登录账号"

    return True, auth, ""


async def list_fish_shop_accounts(
    db: AsyncSession
) -> list[dict]:
    """列出所有鱼小铺账号（用于全部账号聚合与下拉框）。"""
    result = await db.execute(
        select(XianyuAccount).where(
            and_(
                XianyuAccount.fish_shop_user == 1,
                XianyuAccount.deleted == 0,
            )
        ).order_by(XianyuAccount.id)
    )
    accounts = result.scalars().all()
    return [
        {"id": a.id, "nickname": a.nickname, "external_uid": a.external_uid}
        for a in accounts
    ]


# ============================================================
# 同步任务去重锁
# ============================================================

async def _get_account_sync_lock(account_id: int) -> asyncio.Lock:
    """获取账号级同步锁（同账号同时只能一轮同步）。"""
    async with _locks_guard:
        lock = _account_sync_locks.get(account_id)
        if lock is None:
            lock = asyncio.Lock()
            _account_sync_locks[account_id] = lock
        return lock


async def _try_mark_account_syncing(db: AsyncSession, account_id: int) -> bool:
    """尝试标记账号为同步中（数据库级去重）。

    返回 True 表示获锁成功，False 表示已有同步任务在运行。
    """
    existing = await db.execute(
        select(XianyuRateAccountState).where(
            XianyuRateAccountState.account_id == account_id,
        )
    )
    state = existing.scalar_one_or_none()
    now = datetime.now()
    if state is None:
        new_state = XianyuRateAccountState(
            account_id=account_id,
            is_syncing=1,
            sync_started_time=now,
        )
        db.add(new_state)
        try:
            await db.commit()
            return True
        except Exception:
            await db.rollback()
            return False

    # 已存在：检查是否真的在同步（5 分钟超时保护）
    if state.is_syncing == 1:
        if state.sync_started_time and (now - state.sync_started_time) < timedelta(minutes=5):
            return False  # 真的在同步
        # 超时，强制抢占
    await db.execute(
        update(XianyuRateAccountState).where(
            XianyuRateAccountState.account_id == account_id,
        ).values(is_syncing=1, sync_started_time=now)
    )
    await db.commit()
    return True


async def _mark_account_sync_done(
    db: AsyncSession, account_id: int,
    status: str, error: Optional[str], total_count: Optional[int],
    is_full_sync: bool = False,
) -> None:
    """标记账号同步完成。"""
    now = datetime.now()
    values = {
        "is_syncing": 0,
        "sync_started_time": None,
        "last_sync_time": now,
        "last_sync_status": status,
        "last_sync_error": (error or "")[:500] if error else None,
        "last_total_count": total_count,
    }
    if status == "success" and is_full_sync:
        values["last_full_sync_time"] = now
    await db.execute(
        update(XianyuRateAccountState).where(
            XianyuRateAccountState.account_id == account_id,
        ).values(**values)
    )
    await db.commit()


# ============================================================
# 评价记录持久化（upsert，按 account_id + orderId 去重）
# ============================================================

async def _upsert_rate_records(
    db: AsyncSession, account_id: int,
    parsed_records: list[dict],
) -> tuple[int, int]:
    """upsert 评价记录。返回 (new_count, updated_count)。

    按 (account_id, external_order_id) 唯一。
    评价历史不物理删除，只更新字段。
    """
    new_count = 0
    updated_count = 0
    for record in parsed_records:
        existing_result = await db.execute(
            select(XianyuRate).where(
                and_(
                    XianyuRate.account_id == account_id,
                    XianyuRate.external_order_id == record["external_order_id"],
                )
            )
        )
        existing = existing_result.scalar_one_or_none()
        if existing:
            # 更新（保留 sync_status 不强制覆盖，由调用方决定）
            update_values = {k: v for k, v in record.items() if k != "sync_status"}
            await db.execute(
                update(XianyuRate).where(XianyuRate.id == existing.id).values(**update_values)
            )
            updated_count += 1
        else:
            new_record = XianyuRate(**record)
            db.add(new_record)
            new_count += 1
    await db.commit()
    return new_count, updated_count


async def _mark_rate_pending_refresh(
    db: AsyncSession, account_id: int, order_id: str
) -> None:
    """将指定评价标记为待刷新（创建评价成功后调用）。"""
    await db.execute(
        update(XianyuRate).where(
            and_(
                XianyuRate.account_id == account_id,
                XianyuRate.external_order_id == order_id,
            )
        ).values(sync_status="pending_refresh")
    )
    await db.commit()


# ============================================================
# 同步任务追踪
# ============================================================

async def _create_sync_task(
    db: AsyncSession, account_id: Optional[int], scope: str
) -> str:
    sync_id = f"rate-{uuid.uuid4().hex[:16]}"
    task = XianyuRateSyncTask(
        sync_id=sync_id,
        account_id=account_id,
        scope=scope,
        status="running",
        progress=0,
        started_time=datetime.now(),
    )
    db.add(task)
    await db.commit()
    return sync_id


async def _update_sync_task(
    db: AsyncSession, sync_id: str, **fields
) -> None:
    """Best-effort 更新同步任务状态。"""
    try:
        await db.execute(
            update(XianyuRateSyncTask).where(
                XianyuRateSyncTask.sync_id == sync_id
            ).values(**fields)
        )
        await db.commit()
    except Exception as exc:
        logger.warning("更新评价同步任务状态失败 syncId=%s errorType=%s", sync_id, type(exc).__name__)


async def _persist_sync_task_done(
    db: AsyncSession, sync_id: str, status: str,
    total: int, new_count: int, updated_count: int,
    failed: int, succeeded: int, duration: float, error: Optional[str],
) -> None:
    await _update_sync_task(
        db, sync_id,
        status=status,
        progress=100,
        total_count=total,
        new_count=new_count,
        updated_count=updated_count,
        failed_count=failed,
        succeeded_count=succeeded,
        duration_seconds=duration,
        error_message=(error or "")[:2000] if error else None,
        finished_time=datetime.now(),
    )


# ============================================================
# 单账号评价同步
# ============================================================

async def sync_rates_for_account(
    db: AsyncSession, account_id: int,
    force_full: bool = False,
) -> dict:
    """同步单个鱼小铺账号的评价数据。

    策略：
    - 首次同步（无缓存）：完整分页同步
    - 后续快速刷新：仅请求第一页，发现 totalCount 变化或新 orderId 时继续获取剩余页
    - force_full=True 或超过 FULL_SYNC_INTERVAL_SECONDS 未完整同步：强制完整同步
    """
    # 1. 校验鱼小铺
    is_fish_shop, auth, err = await verify_fish_shop_account(db, account_id)
    if not is_fish_shop:
        return {"ok": False, "error": err or "账号不是鱼小铺"}
    if auth is None:
        return {"ok": False, "error": err or "账号 Cookie 未配置"}

    # 2. 获取账号级同步锁（同账号同时只能一轮同步）
    lock = await _get_account_sync_lock(account_id)
    if lock.locked():
        return {"ok": False, "error": "TASK_ALREADY_RUNNING", "syncId": None}

    async with lock:
        # 3. 数据库级去重
        acquired = await _try_mark_account_syncing(db, account_id)
        if not acquired:
            return {"ok": False, "error": "TASK_ALREADY_RUNNING", "syncId": None}

        sync_id = await _create_sync_task(db, account_id, "single")
        started_at = datetime.now()

        try:
            # 4. 判断是否需要完整同步
            state_result = await db.execute(
                select(XianyuRateAccountState).where(
                    XianyuRateAccountState.account_id == account_id,
                )
            )
            state = state_result.scalar_one_or_none()
            need_full = force_full
            if state is None or state.last_sync_time is None:
                need_full = True  # 首次同步
            elif state.last_full_sync_time is None:
                need_full = True  # 从未完整同步
            elif (datetime.now() - state.last_full_sync_time).total_seconds() > FULL_SYNC_INTERVAL_SECONDS:
                need_full = True  # 超过间隔

            # 5. 请求第一页
            page_result = await asyncio.to_thread(
                fetch_rate_list_page, account_id, 1, DEFAULT_PAGE_SIZE, QUERY_CODE_ALL
            )
            if not page_result.get("success"):
                await _mark_account_sync_done(db, account_id, "failed", page_result.get("error"), None)
                await _persist_sync_task_done(
                    db, sync_id, "failed", 0, 0, 0, 1, 0, 0.0, page_result.get("error")
                )
                return {"ok": False, "syncId": sync_id, "error": page_result.get("error")}

            first_page_data = page_result["data"]
            first_items = first_page_data["items"]
            total_count = first_page_data["totalCount"]
            next_page = first_page_data["nextPage"]

            # 6. 解析并 upsert 第一页
            parsed_first = []
            for raw in first_items:
                parsed = _extract_rate_fields(raw, account_id)
                if parsed:
                    parsed_first.append(parsed)

            new_count, updated_count = await _upsert_rate_records(db, account_id, parsed_first)
            synced_order_ids = {p["external_order_id"] for p in parsed_first}

            # 更新进度
            await _update_sync_task(db, sync_id, progress=30, total_count=total_count, new_count=new_count, updated_count=updated_count)

            # 7. 判断是否需要继续获取剩余页
            should_continue = False
            if need_full:
                should_continue = next_page
            else:
                # 快速刷新：发现新 orderId 或 totalCount 变化时继续
                if state and state.last_total_count is not None and total_count != state.last_total_count:
                    should_continue = next_page
                if not should_continue and parsed_first:
                    existing_ids_result = await db.execute(
                        select(XianyuRate.external_order_id).where(
                            and_(
                                XianyuRate.account_id == account_id,
                                XianyuRate.external_order_id.in_([p["external_order_id"] for p in parsed_first]),
                            )
                        )
                    )
                    existing_ids = {r[0] for r in existing_ids_result.fetchall()}
                    new_ids_in_first_page = synced_order_ids - existing_ids
                    if new_ids_in_first_page:
                        should_continue = next_page

            # 8. 继续获取剩余页
            page_num = 2
            while should_continue and page_num <= MAX_PAGES_PER_ACCOUNT:
                await asyncio.sleep(PAGE_REQUEST_INTERVAL_SECONDS)  # 请求间隔
                page_result = await asyncio.to_thread(
                    fetch_rate_list_page, account_id, page_num, DEFAULT_PAGE_SIZE, QUERY_CODE_ALL
                )
                if not page_result.get("success"):
                    # 单页失败不中断整体（已获取的数据保留）
                    logger.warning(
                        "评价同步第 %s 页失败 accountId=%s error=%s",
                        page_num, account_id, page_result.get("error"),
                    )
                    break

                page_data = page_result["data"]
                page_items = page_data["items"]
                if not page_items:
                    break

                page_parsed = []
                for raw in page_items:
                    parsed = _extract_rate_fields(raw, account_id)
                    if parsed:
                        page_parsed.append(parsed)

                # 按 orderId 去重
                new_in_page = []
                for p in page_parsed:
                    if p["external_order_id"] not in synced_order_ids:
                        new_in_page.append(p)
                        synced_order_ids.add(p["external_order_id"])

                if new_in_page:
                    n_new, n_upd = await _upsert_rate_records(db, account_id, new_in_page)
                    new_count += n_new
                    updated_count += n_upd

                # 分页终止条件
                if not page_data["nextPage"]:
                    break
                if total_count > 0 and len(synced_order_ids) >= total_count:
                    break
                progress = 30 + int(70 * min(page_num / max(MAX_PAGES_PER_ACCOUNT, 1), 1.0))
                await _update_sync_task(db, sync_id, progress=progress, new_count=new_count, updated_count=updated_count)
                page_num += 1

            # 9. 汇总
            unique_count = len(synced_order_ids)
            duration = (datetime.now() - started_at).total_seconds()

            # 10. 标记完成
            await _mark_account_sync_done(
                db, account_id, "success", None, unique_count, is_full_sync=need_full
            )
            await _persist_sync_task_done(
                db, sync_id, "success", unique_count, new_count, updated_count, 0, 1, duration, None
            )

            return {
                "ok": True,
                "syncId": sync_id,
                "total": unique_count,
                "new": new_count,
                "updated": updated_count,
                "totalCount": total_count,
                "isFullSync": need_full,
            }

        except Exception as exc:
            logger.exception("评价同步异常 accountId=%s", account_id)
            duration = (datetime.now() - started_at).total_seconds()
            err_msg = f"同步异常: {type(exc).__name__}"
            await _mark_account_sync_done(db, account_id, "failed", err_msg, None)
            await _persist_sync_task_done(db, sync_id, "failed", 0, 0, 0, 1, 0, duration, err_msg)
            return {"ok": False, "syncId": sync_id, "error": err_msg}


# ============================================================
# 全部账号聚合同步（受控并发）
# ============================================================

async def sync_all_rates(
    db: AsyncSession,
    force_full: bool = False,
) -> dict:
    """全部账号模式：受控并发刷新所有鱼小铺账号。

    同时刷新的账号数量受 MAX_CONCURRENT_ACCOUNTS 限制。
    某一个账号失败不影响其他账号。
    """
    fish_shop_accounts = await list_fish_shop_accounts(db)
    if not fish_shop_accounts:
        return {"ok": True, "syncId": None, "total": 0, "succeeded": 0, "failed": 0, "details": [], "message": "当前没有鱼小铺账号"}

    sync_id = await _create_sync_task(db, None, "all")
    started_at = datetime.now()

    sem = asyncio.Semaphore(MAX_CONCURRENT_ACCOUNTS)

    async def _sync_one(account_info: dict, index: int) -> dict:
        # 抖动：错开请求时间，避免所有账号同一毫秒请求
        await asyncio.sleep(min(index * 0.3, 2.0))
        async with sem:
            session_factory = _get_async_session()
            async with session_factory() as sub_db:
                result = await sync_rates_for_account(
                    sub_db, account_info["id"], force_full=force_full
                )
                return {
                    "accountId": account_info["id"],
                    "nickname": account_info.get("nickname"),
                    "ok": result.get("ok", False),
                    "total": result.get("total", 0),
                    "totalCount": result.get("totalCount", 0),
                    "error": result.get("error"),
                }

    # 并发执行
    tasks = [_sync_one(acc, idx) for idx, acc in enumerate(fish_shop_accounts)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    succeeded = 0
    failed = 0
    total_rates = 0
    details = []
    for r in results:
        if isinstance(r, Exception):
            failed += 1
            details.append({"ok": False, "error": str(r)})
        elif r.get("ok"):
            succeeded += 1
            total_rates += r.get("total", 0)
            details.append(r)
        else:
            failed += 1
            details.append(r)

    duration = (datetime.now() - started_at).total_seconds()
    await _persist_sync_task_done(
        db, sync_id, "completed",
        total_rates, 0, 0, failed, succeeded, duration, None,
    )

    return {
        "ok": failed == 0,
        "syncId": sync_id,
        "total": total_rates,
        "succeeded": succeeded,
        "failed": failed,
        "details": details,
    }


# 延迟导入 async_session，避免循环引用
def _get_async_session():
    from ..core.database import async_session
    return async_session


# ============================================================
# 本地评价数据查询（多账号聚合 + 分页 + 筛选）
# ============================================================

# 评价状态分类（本地筛选，不发明新 queryCode）
# - all: 全部
# - pending: 待评价（has_seller_rate=0 且 rate_reviewable=1）
# - done: 已评价（has_seller_rate=1）
# - good: 卖家好评（has_seller_rate=1 且 seller_rate_level='1'）
# - neutral: 卖家中评（has_seller_rate=1 且 seller_rate_level='-1'）
# - bad: 卖家差评（has_seller_rate=1 且 seller_rate_level='0'）
SUPPORTED_CATEGORIES = ["all", "pending", "done", "good", "neutral", "bad"]


async def query_local_rates(
    db: AsyncSession,
    account_id: Optional[int] = None,
    category: str = "all",
    keyword: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """查询本地缓存的评价记录（多账号聚合 + 分类筛选 + 关键词搜索 + 分页）。

    排序：按订单完成时间倒序（finish_time，缺失回退 order_create_time）。
    """
    page = max(1, page)
    page_size = max(1, min(page_size, 100))

    # 构建基础查询条件
    conditions = [
        XianyuRate.deleted == 0,
    ]
    if account_id is not None:
        conditions.append(XianyuRate.account_id == account_id)

    # 分类筛选（等级筛选只匹配 seller=true 的卖家评价）
    if category == "pending":
        conditions.append(XianyuRate.has_seller_rate == 0)
        conditions.append(XianyuRate.rate_reviewable == 1)
    elif category == "done":
        conditions.append(XianyuRate.has_seller_rate == 1)
    elif category == "good":
        conditions.append(XianyuRate.has_seller_rate == 1)
        conditions.append(XianyuRate.seller_rate_level == "1")
    elif category == "neutral":
        conditions.append(XianyuRate.has_seller_rate == 1)
        conditions.append(XianyuRate.seller_rate_level == "-1")
    elif category == "bad":
        conditions.append(XianyuRate.has_seller_rate == 1)
        conditions.append(XianyuRate.seller_rate_level == "0")
    # all 不加条件

    # 关键词搜索（本地搜索）
    if keyword and keyword.strip():
        kw = f"%{keyword.strip()}%"
        conditions.append(
            or_(
                XianyuRate.external_order_id.like(kw),
                XianyuRate.external_item_id.like(kw),
                XianyuRate.item_title.like(kw),
                XianyuRate.buyer_nick.like(kw),
            )
        )

    # 计算总数
    count_stmt = select(func.count(XianyuRate.id)).where(and_(*conditions))
    total_result = await db.execute(count_stmt)
    total = total_result.scalar() or 0

    # 查询列表（按订单完成时间倒序，缺失回退 order_create_time）
    order_expr = func.coalesce(XianyuRate.finish_time, XianyuRate.order_create_time)
    list_stmt = (
        select(XianyuRate)
        .where(and_(*conditions))
        .order_by(desc(order_expr), desc(XianyuRate.id))
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    list_result = await db.execute(list_stmt)
    records = list_result.scalars().all()

    # 同时查询账号信息（用于展示所属账号）
    account_ids = list({r.account_id for r in records})
    account_map: dict[int, dict] = {}
    if account_ids:
        acc_result = await db.execute(
            select(XianyuAccount).where(
                XianyuAccount.id.in_(account_ids),
            )
        )
        for acc in acc_result.scalars().all():
            account_map[acc.id] = {
                "id": acc.id,
                "nickname": acc.nickname,
                "externalUid": acc.external_uid,
            }

    items = []
    for r in records:
        acc_info = account_map.get(r.account_id, {})
        items.append({
            "id": r.id,
            "accountId": r.account_id,
            "accountNickname": acc_info.get("nickname"),
            "externalOrderId": r.external_order_id,
            "externalItemId": r.external_item_id,
            "buyerId": r.buyer_id,
            "buyerNick": r.buyer_nick,
            "buyerIcon": r.buyer_icon,
            "itemTitle": r.item_title,
            "itemPicUrl": r.item_pic_url,
            "itemInfoLines": r.item_info_lines,
            "orderStatus": r.order_status,
            "sellerRateStatus": r.seller_rate_status,
            "inRefund": r.in_refund,
            "consignTime": r.consign_time.isoformat() if r.consign_time else None,
            "orderCreateTime": r.order_create_time.isoformat() if r.order_create_time else None,
            "paySuccessTime": r.pay_success_time.isoformat() if r.pay_success_time else None,
            "finishTime": r.finish_time.isoformat() if r.finish_time else None,
            "logisticsCompany": r.logistics_company,
            "logisticsMailNo": r.logistics_mail_no,
            "buyerRateContent": r.buyer_rate_content,
            "buyerRateLevel": r.buyer_rate_level,
            "buyerRateTime": r.buyer_rate_time.isoformat() if r.buyer_rate_time else None,
            "buyerRateImages": _parse_json_field(r.buyer_rate_images),
            "sellerRateContent": r.seller_rate_content,
            "sellerRateLevel": r.seller_rate_level,
            "sellerRateTime": r.seller_rate_time.isoformat() if r.seller_rate_time else None,
            "sellerRateImages": _parse_json_field(r.seller_rate_images),
            "sellerRateId": r.seller_rate_id,
            "hasSellerRate": bool(r.has_seller_rate),
            "rateReviewable": bool(r.rate_reviewable),
            "syncStatus": r.sync_status,
            "lastSyncedTime": r.last_synced_time.isoformat() if r.last_synced_time else None,
        })

    return {
        "items": items,
        "total": total,
        "page": page,
        "pageSize": page_size,
        "category": category,
    }


def _parse_json_field(value: Optional[str]) -> list:
    """安全解析 JSON 字段，失败返回空列表。"""
    if not value:
        return []
    try:
        result = json.loads(value)
        return result if isinstance(result, list) else []
    except (ValueError, TypeError):
        return []


# ============================================================
# 同步状态查询
# ============================================================

async def get_sync_status(
    db: AsyncSession, account_id: Optional[int] = None
) -> dict:
    """查询同步状态（用于前端判断缓存是否过期、是否正在同步）。"""
    if account_id is not None:
        # 单账号状态
        result = await db.execute(
            select(XianyuRateAccountState).where(
                XianyuRateAccountState.account_id == account_id,
            )
        )
        state = result.scalar_one_or_none()
        if state is None:
            return {
                "hasCache": False,
                "isSyncing": False,
                "lastSyncTime": None,
                "lastSyncStatus": None,
                "lastTotalCount": None,
                "cacheExpired": True,
            }
        now = datetime.now()
        cache_expired = (
            state.last_sync_time is None
            or (now - state.last_sync_time).total_seconds() > SINGLE_ACCOUNT_CACHE_TTL_SECONDS
        )
        return {
            "hasCache": state.last_sync_time is not None,
            "isSyncing": bool(state.is_syncing),
            "lastSyncTime": state.last_sync_time.isoformat() if state.last_sync_time else None,
            "lastSyncStatus": state.last_sync_status,
            "lastTotalCount": state.last_total_count,
            "cacheExpired": cache_expired,
            "lastFullSyncTime": state.last_full_sync_time.isoformat() if state.last_full_sync_time else None,
        }

    # 全部账号模式：聚合所有鱼小铺账号状态
    fish_shop_accounts = await list_fish_shop_accounts(db)
    if not fish_shop_accounts:
        return {
            "hasCache": False,
            "isSyncing": False,
            "lastSyncTime": None,
            "cacheExpired": True,
            "accountCount": 0,
        }

    account_ids = [a["id"] for a in fish_shop_accounts]
    result = await db.execute(
        select(XianyuRateAccountState).where(
            XianyuRateAccountState.account_id.in_(account_ids),
        )
    )
    states = result.scalars().all()

    if not states:
        return {
            "hasCache": False,
            "isSyncing": False,
            "lastSyncTime": None,
            "cacheExpired": True,
            "accountCount": len(fish_shop_accounts),
        }

    any_syncing = any(s.is_syncing for s in states)
    last_sync_times = [s.last_sync_time for s in states if s.last_sync_time]
    latest = max(last_sync_times) if last_sync_times else None
    now = datetime.now()
    cache_expired = (
        latest is None
        or (now - latest).total_seconds() > ALL_ACCOUNTS_CACHE_TTL_SECONDS
    )
    return {
        "hasCache": latest is not None,
        "isSyncing": any_syncing,
        "lastSyncTime": latest.isoformat() if latest else None,
        "cacheExpired": cache_expired,
        "accountCount": len(fish_shop_accounts),
    }


# ============================================================
# 创建评价
# ============================================================

async def _get_create_rate_lock(account_id: int, order_id: str) -> asyncio.Lock:
    """获取创建评价幂等锁（同账号同订单同时只能一个创建请求）。"""
    key = (account_id, order_id)
    async with _create_locks_guard:
        lock = _create_rate_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _create_rate_locks[key] = lock
        return lock


async def create_rate(
    db: AsyncSession, account_id: int, order_id: str,
    rate: int, feedback: str, anonymous: bool,
) -> dict:
    """创建评价（写操作，需多重校验）。

    校验：
    1. 当前用户有权操作该闲鱼账号
    2. 订单属于该账号
    3. 账号会话有效
    4. 订单尚未完成卖家评价
    5. 订单状态允许评价
    6. 评价等级已得到可靠映射
    7. 评价内容符合现有校验
    8. anonymous 为明确布尔值

    幂等性：
    - 同一账号、同一 orderId 同时只能存在一个评价请求
    - 网络超时且结果不确定时，不立即无条件重试创建接口
    """
    if not order_id or not isinstance(order_id, str):
        return {"ok": False, "error": "orderId 不能为空"}

    # 6. 评价等级校验
    if rate not in CONFIRMED_RATE_LEVELS:
        return {
            "ok": False,
            "error": "评价等级不合法，仅支持好评(1)、中评(-1)、差评(0)。",
        }

    # 7. 评价内容校验
    feedback = (feedback or "").strip()
    if len(feedback) > RATE_FEEDBACK_MAX_LENGTH:
        return {"ok": False, "error": f"评价内容超过最大长度 {RATE_FEEDBACK_MAX_LENGTH}"}

    # 1-3. 校验鱼小铺账号
    is_fish_shop, auth, err = await verify_fish_shop_account(db, account_id)
    if not is_fish_shop:
        return {"ok": False, "error": err or "账号不是鱼小铺"}
    if auth is None:
        return {"ok": False, "error": err or "账号 Cookie 未配置"}

    # 2. 校验订单归属（防止跨账号评价）
    rate_result = await db.execute(
        select(XianyuRate).where(
            and_(
                XianyuRate.account_id == account_id,
                XianyuRate.external_order_id == order_id,
                XianyuRate.deleted == 0,
            )
        )
    )
    rate_record = rate_result.scalar_one_or_none()
    if rate_record is None:
        return {"ok": False, "error": "评价记录不存在或不属于该账号"}

    # 4. 校验尚未完成卖家评价
    if rate_record.has_seller_rate == 1:
        return {"ok": False, "error": "该订单已完成卖家评价，不可重复评价"}

    # 5. 订单状态允许评价（保守判断：仅 rate_reviewable=1 允许）
    if rate_record.rate_reviewable != 1:
        return {"ok": False, "error": "当前订单状态不允许评价"}

    # 幂等锁（同一账号同一订单同时只能一个评价请求）
    lock = await _get_create_rate_lock(account_id, order_id)
    if lock.locked():
        return {"ok": False, "error": "CREATE_RATE_IN_PROGRESS"}

    async with lock:
        # 标记为待刷新（防止重复提交）
        await _mark_rate_pending_refresh(db, account_id, order_id)

        # 调用创建评价接口
        try:
            api_result = await asyncio.to_thread(
                call_create_rate, account_id, order_id, rate, feedback, anonymous
            )
        except Exception as exc:
            logger.exception("创建评价调用异常 accountId=%s orderId=%s", account_id, order_id)
            # 异常时不自动重试
            await db.execute(
                update(XianyuRate).where(
                    and_(
                        XianyuRate.account_id == account_id,
                        XianyuRate.external_order_id == order_id,
                    )
                ).values(sync_status="synced")
            )
            await db.commit()
            return {"ok": False, "error": f"创建评价调用异常: {type(exc).__name__}"}

        # 判断真正成功
        success, message, module = judge_create_rate_success(api_result, order_id)

        if not success:
            # 失败：不改变本地状态，清除 pending_refresh 标记
            await db.execute(
                update(XianyuRate).where(
                    and_(
                        XianyuRate.account_id == account_id,
                        XianyuRate.external_order_id == order_id,
                    )
                ).values(sync_status="synced")
            )
            await db.commit()
            return {"ok": False, "error": message or "创建评价失败"}

        # 成功后：更新本地订单为已评价
        await db.execute(
            update(XianyuRate).where(
                and_(
                    XianyuRate.account_id == account_id,
                    XianyuRate.external_order_id == order_id,
                )
            ).values(
                has_seller_rate=1,
                rate_reviewable=0,
                seller_rate_content=feedback,
                seller_rate_level=str(rate),
                seller_rate_time=datetime.now(),
                sync_status="synced",
            )
        )
        await db.commit()

        # 后台刷新该账号最新评价数据，进行服务器校准
        try:
            asyncio.create_task(_background_refresh_account(account_id))
        except Exception:
            pass

        return {
            "ok": True,
            "data": {
                "orderId": order_id,
                "rate": rate,
                "feedback": feedback,
                "anonymous": anonymous,
                "module": module,
            },
            "message": "评价已提交",
        }


async def _background_refresh_account(account_id: int) -> None:
    """后台刷新账号评价数据（创建评价成功后调用，服务器校准）。"""
    try:
        session_factory = _get_async_session()
        async with session_factory() as db:
            await sync_rates_for_account(db, account_id, force_full=False)
    except Exception as exc:
        logger.warning(
            "创建评价后后台刷新失败 accountId=%s errorType=%s",
            account_id, type(exc).__name__,
        )


# ============================================================
# 缓存过期判断
# ============================================================

def is_single_account_cache_expired(last_sync_time: Optional[datetime]) -> bool:
    """单账号缓存是否过期。"""
    if last_sync_time is None:
        return True
    return (datetime.now() - last_sync_time).total_seconds() > SINGLE_ACCOUNT_CACHE_TTL_SECONDS


def is_all_accounts_cache_expired(latest_sync_time: Optional[datetime]) -> bool:
    """全部账号缓存是否过期。"""
    if latest_sync_time is None:
        return True
    return (datetime.now() - latest_sync_time).total_seconds() > ALL_ACCOUNTS_CACHE_TTL_SECONDS


# ============================================================
# 概览统计
# ============================================================

async def get_rate_overview(
    db: AsyncSession, account_id: Optional[int] = None,
) -> dict:
    """获取评价概览统计（用于概览卡片）。

    展示已确认的统计：
    - 评价记录总数
    - 待评价数量
    - 已评价数量
    - 好评 / 中评 / 差评 数量（仅统计 seller=true 的卖家评价）
    - 最近同步时间
    """
    conditions = [
        XianyuRate.deleted == 0,
    ]
    if account_id is not None:
        conditions.append(XianyuRate.account_id == account_id)

    # 总数
    total_result = await db.execute(
        select(func.count(XianyuRate.id)).where(and_(*conditions))
    )
    total = total_result.scalar() or 0

    # 待评价数量
    pending_conditions = list(conditions)
    pending_conditions.append(XianyuRate.has_seller_rate == 0)
    pending_conditions.append(XianyuRate.rate_reviewable == 1)
    pending_result = await db.execute(
        select(func.count(XianyuRate.id)).where(and_(*pending_conditions))
    )
    pending = pending_result.scalar() or 0

    # 已评价数量（以存在卖家评价记录为准）
    done_conditions = list(conditions)
    done_conditions.append(XianyuRate.has_seller_rate == 1)
    done_result = await db.execute(
        select(func.count(XianyuRate.id)).where(and_(*done_conditions))
    )
    done = done_result.scalar() or 0

    # 好评 / 中评 / 差评 数量（仅统计 seller=true 的卖家评价）
    good_conditions = list(conditions)
    good_conditions.append(XianyuRate.has_seller_rate == 1)
    good_conditions.append(XianyuRate.seller_rate_level == "1")
    good_result = await db.execute(
        select(func.count(XianyuRate.id)).where(and_(*good_conditions))
    )
    good = good_result.scalar() or 0

    neutral_conditions = list(conditions)
    neutral_conditions.append(XianyuRate.has_seller_rate == 1)
    neutral_conditions.append(XianyuRate.seller_rate_level == "-1")
    neutral_result = await db.execute(
        select(func.count(XianyuRate.id)).where(and_(*neutral_conditions))
    )
    neutral = neutral_result.scalar() or 0

    bad_conditions = list(conditions)
    bad_conditions.append(XianyuRate.has_seller_rate == 1)
    bad_conditions.append(XianyuRate.seller_rate_level == "0")
    bad_result = await db.execute(
        select(func.count(XianyuRate.id)).where(and_(*bad_conditions))
    )
    bad = bad_result.scalar() or 0

    # 最近同步时间
    if account_id is not None:
        state_result = await db.execute(
            select(XianyuRateAccountState).where(
                XianyuRateAccountState.account_id == account_id,
            )
        )
        state = state_result.scalar_one_or_none()
        last_sync_time = state.last_sync_time.isoformat() if state and state.last_sync_time else None
    else:
        fish_shop_accounts = await list_fish_shop_accounts(db)
        account_ids = [a["id"] for a in fish_shop_accounts]
        last_sync_time = None
        if account_ids:
            states_result = await db.execute(
                select(XianyuRateAccountState).where(
                    XianyuRateAccountState.account_id.in_(account_ids),
                )
            )
            states = states_result.scalars().all()
            sync_times = [s.last_sync_time for s in states if s.last_sync_time]
            if sync_times:
                last_sync_time = max(sync_times).isoformat()

    return {
        "total": total,
        "pending": pending,
        "done": done,
        "good": good,
        "neutral": neutral,
        "bad": bad,
        "lastSyncTime": last_sync_time,
    }
