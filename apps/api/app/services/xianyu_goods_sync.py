"""
闲鱼商品同步服务模块。
负责调用闲鱼 mtop API 获取商品列表和详情，解析并入库。
"""

import hashlib
import io
import json
import logging
import random
import time
import threading
import asyncio
from datetime import datetime
from typing import Any, Optional
from urllib.parse import urlencode

import re
import requests
from PIL import Image

from ..core.upload_security import (
    UnsafePathError,
    UnsafeRemoteURLError,
    UploadValidationError,
    download_public_image,
    load_safe_local_image,
    resolve_upload_path,
)

logger = logging.getLogger(__name__)


def _safe_price_to_cent(price: Any) -> int:
    """将价格安全转换为分（int）。

    处理以下情况：
    - 数字：7 / 7.5 / "7" / "7.5"
    - 含货币符号：¥7 / ￥7 / RMB 7 / $7
    - 含单位：7元 / 7块钱 / 7.5 元
    - 范围价格：7-15 / 7~15（取最低价）
    - 空值：返回 0

    抛出 ValueError 当无法提取数字时。
    """
    if price is None or price == "":
        return 0
    if isinstance(price, (int, float)):
        return int(float(price) * 100)
    s = str(price).strip()
    if not s:
        return 0
    # 去除货币符号和单位
    cleaned = re.sub(r'[¥￥$￥RMBrmb元块毛分]', ' ', s)
    # 处理范围价格：取最低价
    cleaned = re.split(r'[~\-—到]', cleaned)[0]
    # 提取第一个数字（支持小数）
    m = re.search(r'\d+(?:\.\d+)?', cleaned)
    if not m:
        raise ValueError(f"无法从价格字符串提取数字: {price!r}")
    return int(float(m.group(0)) * 100)


# ==================== 常量 ====================

APP_KEY = "34839810"
H5_API_BASE = "https://h5api.m.goofish.com/h5"

# 商品列表 API
ITEM_LIST_API = "mtop.idle.web.xyh.item.list"
ITEM_LIST_URL = f"{H5_API_BASE}/{ITEM_LIST_API}/1.0/"

# 商品详情 API
ITEM_DETAIL_API = "mtop.taobao.idle.pc.detail"
ITEM_DETAIL_URL = f"{H5_API_BASE}/{ITEM_DETAIL_API}/1.0/"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://www.goofish.com/",
    "Origin": "https://www.goofish.com",
}

# 同步状态跟踪
_sync_tasks: dict[str, dict] = {}
_sync_lock = threading.Lock()
_SYNC_TASK_TTL_SECONDS = 24 * 60 * 60
_SYNC_TASK_MAX_ENTRIES = 512
# 商品详情同步可能持续较久，使用显式 owner 管理并在应用关闭时统一等待。
_detail_sync_tasks: set[asyncio.Task[None]] = set()


def _sync_task_started_at(task: dict) -> float:
    try:
        return datetime.fromisoformat(str(task.get("started_at") or "")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _prune_sync_tasks_locked(*, now: float | None = None) -> None:
    """Bound terminal compatibility snapshots; durable rows remain in MySQL."""

    current = time.time() if now is None else float(now)
    terminal = {
        sync_id: task
        for sync_id, task in _sync_tasks.items()
        if str(task.get("status") or "") not in {"queued", "running"}
    }
    for sync_id, task in terminal.items():
        started_at = _sync_task_started_at(task)
        if started_at <= 0 or current - started_at > _SYNC_TASK_TTL_SECONDS:
            _sync_tasks.pop(sync_id, None)

    overflow = len(_sync_tasks) - _SYNC_TASK_MAX_ENTRIES
    if overflow <= 0:
        return
    oldest_terminal = sorted(
        (
            (_sync_task_started_at(task), sync_id)
            for sync_id, task in _sync_tasks.items()
            if str(task.get("status") or "") not in {"queued", "running"}
        ),
        key=lambda item: (item[0], item[1]),
    )
    for _started_at, sync_id in oldest_terminal[:overflow]:
        _sync_tasks.pop(sync_id, None)


def _detail_sync_done(task: asyncio.Task[None]) -> None:
    _detail_sync_tasks.discard(task)
    if task.cancelled():
        return
    exception = task.exception()
    if exception is not None:
        logger.error(
            "商品详情同步后台任务异常 errorType=%s",
            type(exception).__name__,
        )


async def shutdown_detail_sync_tasks() -> None:
    """Cancel and drain explicitly owned long-running detail sync jobs."""

    tasks = tuple(_detail_sync_tasks)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
        _detail_sync_tasks.difference_update(tasks)

# 风控错误码
RGV587 = "RGV587"
# 闲鱼 MTOP 接口实际返回的拼写为 FAIL_SYS_TOKEN_EXOIRED（存在拼写错误，多了一个 I）
# 同时兼容正确拼写 FAIL_SYS_TOKEN_EXPIRED，便于上游统一按 Cookie 失效处理
TOKEN_EXPIRED = "FAIL_SYS_TOKEN_EXOIRED"
TOKEN_EXPIRED_ALIAS = "FAIL_SYS_TOKEN_EXPIRED"
SESSION_EXPIRED = "FAIL_SYS_SESSION_EXPIRED"

# 闲鱼发布 API 常见业务错误码 → 中文友好提示
# 用于把 ret_msg 中的 FAIL_XXX::描述 翻译成用户能看懂的原因
PUBLISH_REJECT_CODE_MAP = {
    "FAIL_BIZ_ITEM_PICTURE_VIOLATION": "商品图片涉嫌违规（涉黄/涉暴/涉政/水印等）",
    "FAIL_BIZ_ITEM_TITLE_VIOLATION": "商品标题含有违禁词或敏感词",
    "FAIL_BIZ_ITEM_DESC_VIOLATION": "商品描述含有违禁词或敏感词",
    "FAIL_BIZ_ITEM_TITLE_REQUIRED": "商品标题不能为空",
    "FAIL_BIZ_ITEM_DESC_REQUIRED": "商品描述不能为空",
    "FAIL_BIZ_ITEM_PRICE_REQUIRED": "商品价格不能为空",
    "FAIL_BIZ_ITEM_PRICE_INVALID": "商品价格异常，请检查价格区间",
    "FAIL_BIZ_SKU_PRICE_ILLEGAL": "商品价格未设置或为 0，请检查商品来源是否带价格",
    "FAIL_BIZ_ITEM_IMAGE_REQUIRED": "请至少上传一张商品图片",
    "FAIL_BIZ_ITEM_EDIT_INVALID_MAP_LOCATION": "发布地址信息不完整，请补全省市区、GPS 和 POI 信息",
    "FAIL_BIZ_ITEM_CATEGORY_NOT_MATCH": "商品分类与内容不匹配，请重新选择分类",
    "FAIL_BIZ_ITEM_CATEGORY_INVALID": "商品分类无效，请重新选择",
    "FAIL_BIZ_ITEM_QUANTITY_INVALID": "商品库存数量异常",
    "FAIL_BIZ_USER_NOT_LOGIN": "闲鱼登录已失效，请重新扫码登录",
    "FAIL_BIZ_USER_BAN_PUBLISH": "账号被限制发布，请到闲鱼 App 查看账号状态",
    "FAIL_BIZ_USER_PUBLISH_LIMIT": "今日发布数量已达上限，请明天再试",
    "FAIL_BIZ_ITEM_DUPLICATE": "检测到重复发布相同商品，请修改后重试",
    "FAIL_BIZ_ITEM_RISK_CONTENT": "商品内容被风控判定为风险内容",
    "FAIL_BIZ_ITEM_BRAND_VIOLATION": "商品涉嫌品牌侵权，请修改后重试",
    "FAIL_BIZ_ITEM_FAKE_VIOLATION": "商品涉嫌售假，请修改后重试",
    "FAIL_SYS_USER_VALIDATE": "触发了闲鱼安全验证，请稍后重试或在闲鱼 App 中完成验证",
    "FAIL_SYS_PARAM_ERROR": "请求参数有误，请检查商品信息后重试",
    "FAIL_SYS_ILLEGAL_ACCESS": "访问被拒绝，请稍后重试",
    "FAIL_SYS_API_NOT_FOUNDED": "闲鱼接口已下线，请联系管理员",
}


def _explain_publish_rejection(ret_msg: str, data: object) -> str:
    """把闲鱼发布接口返回的 ret_msg 翻译成用户能看懂的中文原因。

    ret 格式通常为 ``"FAIL_XXX::中文描述"``，也可能仅为 ``"FAIL_XXX"``。
    当无法识别错误码时，至少把原始描述透传出来，避免前端只看到"平台暂未接受该商品"。

    Args:
        ret_msg: 闲鱼 ret 数组首项
        data: 闲鱼 data 字段，可能携带额外 subMsg / message

    Returns:
        用户可读的中文错误说明
    """
    raw = str(ret_msg or "").strip()
    # 提取错误码与平台描述：FAIL_XXX::desc 或 FAIL_XXX[]::desc
    code_part = raw
    desc_part = ""
    if "::" in raw:
        code_part, _, desc_part = raw.partition("::")
        code_part = code_part.strip()
        desc_part = desc_part.strip()
    # 兼容形如 "FAIL_XXX[xxx]" 的带参错误码
    code_key = code_part.split("[", 1)[0].strip()

    # data 里偶尔会带更具体的 subMsg / message / msg
    extra_desc = ""
    if isinstance(data, dict):
        for k in ("subMsg", "message", "msg", "errorMessage"):
            v = data.get(k)
            if isinstance(v, str) and v.strip():
                extra_desc = v.strip()
                break

    friendly = PUBLISH_REJECT_CODE_MAP.get(code_key)
    parts = ["商品发布被平台拒绝"]
    if friendly:
        parts.append(friendly)
    elif desc_part:
        parts.append(desc_part)
    elif extra_desc:
        parts.append(extra_desc)
    else:
        parts.append("平台暂未接受该商品，请检查内容后重试")
    msg = "：".join(parts[:2])

    # 末尾附原始错误码便于排查（仅当代码能识别时）
    if code_key and code_key not in ("None", "", "[]"):
        msg = f"{msg}（错误码：{code_key}）"
    return msg



async def _resolve_account_cookie(
    db: "AsyncSession",
    account_id: Optional[int],
    current_user: dict,
) -> tuple[Optional[str], Optional[str]]:
    """根据 accountId 解析账号 Cookie 和 _m_h5_tk，返回 (cookie_str, error_msg)。"""
    from sqlalchemy import select
    from ..models.entities import XianyuAccountAuth
    from ..core.cookie_crypto import decrypt_cookie_if_needed

    try:
        if account_id:
            result = await db.execute(
                select(XianyuAccountAuth).where(
                    XianyuAccountAuth.account_id == account_id,
                    XianyuAccountAuth.deleted == 0,
                )
            )
            auth = result.scalar_one_or_none()
            logger.info(
                "Account credential lookup accountId=%d credentialPresent=%s",
                account_id,
                bool(auth and auth.encrypted_cookie),
            )
        else:
            result = await db.execute(
                select(XianyuAccountAuth)
                .where(XianyuAccountAuth.deleted == 0,
                )
                .order_by(XianyuAccountAuth.updated_time.desc())
                .limit(1)
            )
            auth = result.scalar_one_or_none()

        if not auth or not auth.encrypted_cookie:
            logger.warning(
                "Account credential unavailable accountId=%d authRecordPresent=%s",
                account_id or -1,
                auth is not None,
            )
            return None, "账号未登录或Cookie已失效，请先到「账号管理」扫码登录闲鱼账号"

        cookie_str = decrypt_cookie_if_needed(auth.encrypted_cookie)
        token = _get_token_from_cookie(cookie_str)
        if not token:
            return None, "Cookie 中缺少 _m_h5_tk，请重新登录闲鱼账号"

        return cookie_str, None
    except Exception:
        logger.error("解析账号 Cookie 失败", exc_info=True)
        return None, "读取账号登录凭据失败，请稍后重试"


async def _persist_sync_task(sync_id: str, **fields) -> None:
    """Best-effort persist of sync task state so progress survives process restarts."""
    try:
        from ..core.database import async_session
        from ..models.entities import XianyuGoodsSyncTask
        from sqlalchemy import select, update

        async with async_session() as db:
            result = await db.execute(select(XianyuGoodsSyncTask).where(XianyuGoodsSyncTask.sync_id == sync_id))
            task = result.scalar_one_or_none()
            now = datetime.now()
            db_fields = {
                "status": fields.get("status"),
                "progress": fields.get("progress"),
                "total_count": fields.get("total"),
                "new_count": fields.get("new"),
                "updated_count": fields.get("updated"),
                "skipped_count": fields.get("skipped"),
                "off_shelf_count": fields.get("off_shelf"),
                "detail_synced_count": fields.get("detail_synced"),
                "duration_seconds": fields.get("duration_seconds"),
                "error_message": fields.get("error"),
                "finished_time": now if fields.get("status") in {"completed", "failed"} else None,
                "updated_time": now,
            }
            db_fields = {k: v for k, v in db_fields.items() if v is not None}
            if task:
                await db.execute(update(XianyuGoodsSyncTask).where(XianyuGoodsSyncTask.sync_id == sync_id).values(**db_fields))
            else:
                db.add(XianyuGoodsSyncTask(
                    sync_id=sync_id,
                    account_id=int(fields.get("account_id") or 0),
                    status=str(fields.get("status") or "queued"),
                    progress=int(fields.get("progress") or 0),
                    total_count=int(fields.get("total") or 0),
                    new_count=int(fields.get("new") or 0),
                    updated_count=int(fields.get("updated") or 0),
                    skipped_count=int(fields.get("skipped") or 0),
                    off_shelf_count=int(fields.get("off_shelf") or 0),
                    detail_synced_count=int(fields.get("detail_synced") or 0),
                    duration_seconds=float(fields.get("duration_seconds") or 0),
                    error_message=fields.get("error"),
                    started_time=now,
                    finished_time=now if fields.get("status") in {"completed", "failed"} else None,
                    deleted=0,
                    created_time=now,
                    updated_time=now,
                ))
            await db.commit()
    except Exception as exc:
        logger.warning(
            "同步任务状态落库失败 syncId=%s errorType=%s",
            sync_id,
            type(exc).__name__,
        )


def _task_snapshot(sync_id: str) -> Optional[dict]:
    with _sync_lock:
        _prune_sync_tasks_locked()
        task = _sync_tasks.get(sync_id)
        return dict(task) if task else None


def _build_sign(token: str, timestamp: int, data_json: str) -> str:
    """构建 MD5 签名：MD5(token + "&" + timestamp + "&" + APP_KEY + "&" + dataJson)"""
    raw = f"{token}&{timestamp}&{APP_KEY}&{data_json}"
    return hashlib.md5(raw.encode()).hexdigest()


def _refresh_m_h5_tk(cookie_str: str) -> str:
    """
    刷新 _m_h5_tk 令牌。
    
    _m_h5_tk 具有时效性，扫码登录保存后可能已过期。
    此函数用存储的 cookie 重建会话，执行 3 步刷新流程获取新令牌。
    
    流程（同 xianyu_qr_login._get_m_h5_tk）:
    1. GET h5api → 获取 cookie2
    2. POST 空 token → 触发服务端下发 _m_h5_tk
    3. POST 真实 token → 刷新并激活令牌
    
    返回: 包含新 _m_h5_tk 的 cookie 字符串
    """
    session = requests.Session()
    session.trust_env = False
    # 将存储的 cookie 还原到会话
    for part in cookie_str.split(";"):
        part = part.strip()
        if "=" in part:
            key, _, value = part.partition("=")
            session.cookies.set(key.strip(), value.strip(), domain=".goofish.com")

    try:
        # Step 1: GET 获取初始 Cookie
        session.get(ITEM_DETAIL_URL.replace(ITEM_DETAIL_API + "/1.0/", "mtop.gaia.nodejs.gaia.idle.data.gw.v2.index.get/1.0/"),
                    headers=HEADERS, timeout=15)

        # Step 2: 空 token POST — 触发 _m_h5_tk 下发
        t_ms1 = int(time.time() * 1000)
        data_str = '{"bizScene":"home"}'
        empty_sign = hashlib.md5(f"&{t_ms1}&{APP_KEY}&{data_str}".encode()).hexdigest()
        refresh_url = f"{H5_API_BASE}/mtop.gaia.nodejs.gaia.idle.data.gw.v2.index.get/1.0/"

        session.post(refresh_url, headers=HEADERS, data={
            "jsv": "2.7.2", "appKey": APP_KEY, "t": str(t_ms1), "sign": empty_sign,
            "v": "1.0", "type": "originaljson", "dataType": "json",
            "timeout": "20000", "api": "mtop.gaia.nodejs.gaia.idle.data.gw.v2.index.get",
            "data": data_str,
        }, timeout=15)

        # 提取新 _m_h5_tk
        m_h5_tk = session.cookies.get("_m_h5_tk")
        if not m_h5_tk:
            logger.warning("刷新 _m_h5_tk 失败：服务器未下发新令牌，继续使用原 cookie")
            return cookie_str

        token = m_h5_tk.split("_")[0]

        # Step 3: 真实 token POST — 激活令牌
        t_ms2 = int(time.time() * 1000)
        real_sign = hashlib.md5(f"{token}&{t_ms2}&{APP_KEY}&{data_str}".encode()).hexdigest()

        session.post(refresh_url, headers=HEADERS, data={
            "jsv": "2.7.2", "appKey": APP_KEY, "t": str(t_ms2), "sign": real_sign,
            "v": "1.0", "type": "originaljson", "dataType": "json",
            "timeout": "20000", "api": "mtop.gaia.nodejs.gaia.idle.data.gw.v2.index.get",
            "data": data_str,
        }, timeout=15)

        # 将会话中的所有 cookie 合并到 cookie 字符串
        updated_cookies = _parse_cookie(cookie_str)
        for c in session.cookies:
            updated_cookies[c.name] = c.value

        new_cookie_str = "; ".join(f"{k}={v}" for k, v in updated_cookies.items())
        logger.info("_m_h5_tk refresh succeeded credentialPresent=true")
        return new_cookie_str

    except Exception:
        logger.warning("刷新 _m_h5_tk 异常，继续使用原 cookie", exc_info=True)
        return cookie_str
    finally:
        session.close()


def _parse_cookie(cookie_str: str) -> dict:
    """将 Cookie 字符串解析为 dict"""
    if not cookie_str:
        return {}
    cookies = {}
    for part in cookie_str.split(";"):
        part = part.strip()
        if "=" in part:
            key, _, value = part.partition("=")
            cookies[key.strip()] = value.strip()
    return cookies


def _get_token_from_cookie(cookie_str: str) -> Optional[str]:
    """从 Cookie 中提取 _m_h5_tk 的 token 部分"""
    cookies = _parse_cookie(cookie_str)
    m_h5_tk = cookies.get("_m_h5_tk", "")
    if not m_h5_tk:
        return None
    return m_h5_tk.split("_")[0]


def _make_api_request(
    cookie_str: str,
    api_name: str,
    data: dict,
    timeout: int = 30,
    extra_form: Optional[dict] = None,
) -> dict:
    """
    调用闲鱼 mtop API。
    返回解析后的 JSON 响应体。
    """
    token = _get_token_from_cookie(cookie_str)
    if not token:
        raise RuntimeError("Cookie 中缺少 _m_h5_tk，无法签名")

    t_ms = int(time.time() * 1000)
    data_json = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
    sign = _build_sign(token, t_ms, data_json)

    url = f"{H5_API_BASE}/{api_name}/1.0/"

    session = requests.Session()
    session.trust_env = False
    # 设置 Cookie，指定 domain 确保发送到 goofish.com
    for part in cookie_str.split(";"):
        part = part.strip()
        if "=" in part:
            key, _, value = part.partition("=")
            session.cookies.set(key.strip(), value.strip(), domain=".goofish.com")

    form_data = {
        "jsv": "2.7.2",
        "appKey": APP_KEY,
        "t": str(t_ms),
        "sign": sign,
        "v": "1.0",
        "type": "originaljson",
        "accountSite": "xianyu",
        "dataType": "json",
        "timeout": str(timeout * 1000),
        "api": api_name,
        "sessionOption": "AutoLoginOnly",
        "data": data_json,
    }
    if extra_form:
        form_data.update(extra_form)

    try:
        resp = session.post(url, headers=HEADERS, data=form_data, timeout=timeout + 10)
    finally:
        session.close()
    resp.raise_for_status()

    result = resp.json()
    return result


def _parse_item_list_response(response: dict) -> list[dict]:
    """
    解析商品列表 API 响应，提取商品数据列表。
    响应结构: { ret: ["SUCCESS::调用成功"], data: { cardList: [{ cardData: {...} }, ...] } }
    """
    ret = response.get("ret", [])
    if isinstance(ret, list) and ret:
        ret_msg = ret[0] if ret else ""
    else:
        ret_msg = str(ret)

    if ret_msg == RGV587:
        raise RuntimeError("触发风控(RGV587)，请稍后再试")
    if TOKEN_EXPIRED in ret_msg or TOKEN_EXPIRED_ALIAS in ret_msg:
        raise RuntimeError("Token 已过期，请重新登录闲鱼账号")

    if "SUCCESS" not in ret_msg:
        raise RuntimeError("闲鱼平台暂未接受商品同步请求，请稍后重试")

    data = response.get("data", {})
    if not isinstance(data, dict):
        return []

    card_list = data.get("cardList", [])
    if not isinstance(card_list, list):
        return []

    items = []
    for card in card_list:
        if not isinstance(card, dict):
            continue
        card_data = card.get("cardData", {})
        if isinstance(card_data, dict):
            items.append(card_data)

    return items


def _parse_item_detail_response(response: dict) -> dict:
    """解析商品详情 API 响应，提取详情数据"""
    ret = response.get("ret", [])
    if isinstance(ret, list) and ret:
        ret_msg = ret[0] if ret else ""
    else:
        ret_msg = str(ret)

    if "SUCCESS" not in ret_msg:
        return {}

    data = response.get("data", {})
    if not isinstance(data, dict):
        return {}

    return data


def _safe_get_nested(d: dict, *keys, default=""):
    """安全地从嵌套字典中取值，如 _safe_get_nested(d, 'priceInfo', 'price')"""
    current = d
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
        if current is None:
            return default
    return current if current is not None else default


def _parse_want_count_from_labels(card_data: dict) -> int:
    """
    从商品卡片标签数据中解析"X人想要"的数字。

    闲鱼列表 API 的 want_count 不直接返回，而是埋在
    itemLabelDataVO.labelData.{r1,r2,r3}.tagList[].data.content 文字中。
    形如 "2人想要"。我们遍历所有 r 槽位的 tagList 找匹配。
    """
    # 兼容直接传 want_count 字段（详情 API 路径）
    direct = card_data.get("wantCount") or card_data.get("wantCnt") or card_data.get("wishCount") or card_data.get("favorCount")
    if direct:
        try:
            return int(direct)
        except (TypeError, ValueError):
            pass

    label_vo = card_data.get("itemLabelDataVO")
    if not isinstance(label_vo, dict):
        return 0
    label_data = label_vo.get("labelData")
    if not isinstance(label_data, dict):
        return 0

    pattern = re.compile(r"(\d+)\s*人想要")
    # 标签按 r1/r2/r3 排序，优先 r3（通常想要数在 r3），但任何一个槽位命中即可
    for slot_key in ("r3", "r2", "r1"):
        slot = label_data.get(slot_key)
        if not isinstance(slot, dict):
            continue
        tag_list = slot.get("tagList")
        if not isinstance(tag_list, list):
            continue
        for tag in tag_list:
            if not isinstance(tag, dict):
                continue
            tag_data = tag.get("data")
            if not isinstance(tag_data, dict):
                continue
            content_text = tag_data.get("content")
            if not content_text or not isinstance(content_text, str):
                continue
            match = pattern.search(content_text)
            if match:
                try:
                    return int(match.group(1))
                except (TypeError, ValueError):
                    continue
    return 0


def _parse_card_to_goods(card_data: dict, account_id: int) -> dict:
    """
    将闲鱼 API 返回的 cardData 解析为统一的商品字典。
    映射关系基于闲鱼 mtop.idle.web.xyh.item.list 接口的实际返回字段。
    
    闲鱼 API 返回结构:
        cardData = {
            "id": "商品ID",
            "title": "商品标题",
            "itemStatus": 0,          # 0=在售, 1=下架, 2=已售
            "priceInfo": { "price": "99.00", "preText": "¥" },
            "picInfo": { "picUrl": "https://..." },
            "detailParams": { "itemId": "xxx", "soldPrice": "99.00", "picUrl": "..." },
            "detailUrl": "https://...",
            "quantity": 999,
            "exposureCount": 100,
            "viewCount": 50,
            "wantCount": 10,
        }
    """
    # 兼容两套状态枚举：
    # 新版: 0=在售, 1=下架, 2=已售
    # 旧版: 1=在售, 2=下架, 3=已售
    raw_item_status = card_data.get("itemStatus", 0)
    if raw_item_status in (0, 1, 2) and ("priceInfo" in card_data or "picInfo" in card_data or "id" in card_data):
        status_map = {0: 0, 1: 1, 2: 2}
    else:
        status_map = {1: 0, 2: 1, 3: 2}
    status = status_map.get(raw_item_status, 1)

    # 商品ID: 顶层 id 字段 / detailParams.itemId / 兼容旧字段 itemId
    item_id = str(card_data.get("id", "") or _safe_get_nested(card_data, "detailParams", "itemId") or card_data.get("itemId", ""))

    # 价格: 新结构 priceInfo.price / detailParams.soldPrice，兼容旧字段 soldPrice/price
    price = _safe_get_nested(card_data, "priceInfo", "price") or card_data.get("price", "") or card_data.get("soldPrice", "")
    sold_price = _safe_get_nested(card_data, "detailParams", "soldPrice") or card_data.get("soldPrice", "") or price

    # 封面图: 新结构 picInfo.picUrl / detailParams.picUrl，兼容多种旧字段
    # 优先取嵌套结构（picInfo.picUrl 是闲鱼新版列表 API 的标准字段）
    cover_pic = (
        _safe_get_nested(card_data, "picInfo", "picUrl")
        or _safe_get_nested(card_data, "picInfo", "url")
        or _safe_get_nested(card_data, "detailParams", "picUrl")
        or _safe_get_nested(card_data, "detailParams", "picUrlNew")
        or card_data.get("coverPic", "")
        or card_data.get("imageUrl", "")
        or card_data.get("mainImageUrl", "")
        or card_data.get("mainPic", "")
        or card_data.get("mainPicUrl", "")
        or card_data.get("coverUrl", "")
        or card_data.get("thumbUrl", "")
        or card_data.get("picUrl", "")
        or card_data.get("pic", "")
        or card_data.get("image", "")
    )
    # 协议兜底：将 http:// 转为 https://，避免被前端 https-only CSP 拒绝
    if cover_pic and isinstance(cover_pic, str) and cover_pic.lower().startswith("http://"):
        cover_pic = "https://" + cover_pic[len("http://"):]
    elif cover_pic and isinstance(cover_pic, str) and cover_pic.startswith("//"):
        cover_pic = "https:" + cover_pic

    goods = {
        "account_id": account_id,
        "external_goods_id": item_id,
        "title": card_data.get("title", "") or card_data.get("itemName", ""),
        "price": str(price),
        "sold_price": str(sold_price),
        "cover_pic": cover_pic,
        "image_url": cover_pic,
        "stock": str(card_data.get("quantity", "") or card_data.get("stock", "")),
        "quantity": int(card_data.get("quantity", 0) or card_data.get("stock", 0)),
        # 统计字段：列表 API 仅返回想要数（埋在 itemLabelDataVO.labelData 的标签文字中），
        # 曝光数 / 浏览数 需要详情 API（详见 _merge_detail_info），但详情 API 经常被风控拦截。
        "exposure_count": int(card_data.get("exposureCount", 0) or card_data.get("exposureNum", 0) or card_data.get("exposure", 0) or 0),
        "view_count": int(card_data.get("viewCount", 0) or card_data.get("browseCnt", 0) or card_data.get("browseCount", 0) or card_data.get("pv", 0) or card_data.get("uv", 0) or 0),
        "want_count": _parse_want_count_from_labels(card_data),
        "detail_url": card_data.get("detailUrl", "") or card_data.get("itemUrl", ""),
        "detail_info": card_data.get("detailInfo", "") or card_data.get("desc", ""),
        "description": card_data.get("detailInfo", "") or card_data.get("desc", ""),
        "category": str(card_data.get("categoryId", "") or card_data.get("category", "") or card_data.get("cateName", "")),
        "sort_order": int(card_data.get("sortOrder", 0) or 0),
        "status": status,
        "deleted": 0,
    }

    return goods


def _merge_detail_info(goods_dict: dict, detail_data: dict):
    """将详情 API 返回的数据合并到商品字典中"""
    if not detail_data:
        return

    # The detail endpoint has returned both ``data.item`` and ``data.itemDO``
    # in production.  Preferring itemDO matters because its description is
    # otherwise silently skipped, leaving the local product cache with only a
    # title and making an AI customer-service reply under-informed.
    item_info = (
        detail_data.get("itemDO")
        or detail_data.get("item")
        or detail_data.get("itemInfo")
        or detail_data
    )
    if not isinstance(item_info, dict):
        return

    desc = item_info.get("desc") or item_info.get("description") or ""
    if desc:
        goods_dict["detail_info"] = str(desc)
        goods_dict["description"] = str(desc)

    detail_url = item_info.get("detailUrl", "") or item_info.get("itemUrl", "")
    if detail_url:
        goods_dict["detail_url"] = str(detail_url)

    image_urls = []
    # 兼容多套字段命名：images/imageList/picList/albumPics 是常见键
    for key in ("images", "imageList", "picList", "albumPics", "picUrls", "imageUrls", "imgs"):
        candidates = item_info.get(key)
        if isinstance(candidates, list):
            for candidate in candidates:
                if isinstance(candidate, str) and candidate.strip():
                    image_urls.append(candidate.strip())
                elif isinstance(candidate, dict):
                    url = (
                        candidate.get("url")
                        or candidate.get("picUrl")
                        or candidate.get("imageUrl")
                        or candidate.get("imgUrl")
                        or candidate.get("pic")
                    )
                    if url:
                        image_urls.append(str(url).strip())
        if image_urls:
            break
    # 兜底：单图字段（detailData.itemDO.picUrl / coverPic 等）
    if not image_urls:
        for single_key in ("picUrl", "coverPic", "imageUrl", "mainPic", "mainPicUrl", "coverUrl"):
            single = item_info.get(single_key)
            if isinstance(single, str) and single.strip():
                image_urls.append(single.strip())
                break

    if image_urls:
        deduped = []
        seen = set()
        for url in image_urls:
            if url and url not in seen:
                seen.add(url)
                deduped.append(url)
        goods_dict["image_urls"] = deduped
        goods_dict["cover_pic"] = goods_dict.get("cover_pic") or deduped[0]
        goods_dict["image_url"] = goods_dict.get("image_url") or deduped[0]

    quantity = item_info.get("quantity")
    try:
        if quantity is not None and str(quantity).strip() != "":
            quantity_int = int(quantity)
            goods_dict["quantity"] = quantity_int
            goods_dict["stock"] = quantity_int
    except (ValueError, TypeError):
        pass

    # 提取商品统计字段：浏览量、想要数
    # 兼容多套字段命名：itemDO.browseCnt/wantCnt（主路径）
    #                itemStats.viewCount/wishCount（新版结构）
    #                gpageInfo.pv/uv（旧版结构）
    stat_sources = [
        item_info,
        item_info.get("itemStats") if isinstance(item_info.get("itemStats"), dict) else {},
        item_info.get("gpageInfo") if isinstance(item_info.get("gpageInfo"), dict) else {},
        detail_data.get("gpageInfo") if isinstance(detail_data.get("gpageInfo"), dict) else {},
        detail_data.get("itemStats") if isinstance(detail_data.get("itemStats"), dict) else {},
    ]
    for src in stat_sources:
        if not src:
            continue
        for src_key, dst_key in (
            ("browseCnt", "view_count"),
            ("browseCount", "view_count"),
            ("viewCount", "view_count"),
            ("pv", "view_count"),
            ("uv", "view_count"),
            ("ipv", "view_count"),
            ("wantCnt", "want_count"),
            ("wishCount", "want_count"),
            ("wantCount", "want_count"),
            ("favorCount", "want_count"),
            ("want", "want_count"),
            ("exposureCount", "exposure_count"),
            ("exposureNum", "exposure_count"),
            ("exposure", "exposure_count"),
            ("impression", "exposure_count"),
            ("impressionCount", "exposure_count"),
        ):
            stat_val = src.get(src_key)
            if stat_val is None:
                continue
            try:
                val = int(stat_val)
                if val > 0:
                    goods_dict[dst_key] = val
            except (ValueError, TypeError):
                pass

    sold_price = (
        item_info.get("soldPrice")
        or _safe_get_nested(item_info, "priceInfo", "price")
        or item_info.get("price")
    )
    if sold_price not in (None, ""):
        goods_dict["sold_price"] = str(sold_price)
        goods_dict["price"] = str(sold_price)

    goods_dict["raw_payload"] = detail_data


def _normalize_image_urls(image_urls: Any) -> list[str]:
    if not isinstance(image_urls, list):
        return []
    deduped = []
    seen = set()
    for item in image_urls:
        if item is None:
            continue
        url = str(item).strip()
        if not url or url in seen:
            continue
        seen.add(url)
        deduped.append(url)
    return deduped


def _clean_goods_update_values(goods_dict: dict, *, partial: bool) -> dict:
    # 售整自动上架相关字段与开关字段由发布/编辑/重发链路显式维护，
    # 即使同步接口返回这些字段也必须忽略，避免被同步值意外清零。
    protected_fields = {
        "auto_relist_enabled",
        "auto_reply_enabled",
        "next_relist_goods_id",
        "relist_source_goods_id",
        "last_relist_at",
        "has_snapshot",
        "original_quantity",
    }
    values = {}
    for key, value in goods_dict.items():
        if key in {"account_id"}:
            continue
        if key in protected_fields:
            continue
        if value is None:
            continue
        if partial and isinstance(value, str) and value.strip() == "":
            continue
        if partial and isinstance(value, list) and not value:
            continue
        if partial and isinstance(value, dict) and not value:
            continue
        values[key] = value
    return values


def _build_goods_insert_values(goods_dict: dict) -> dict:
    values = _clean_goods_update_values(goods_dict, partial=False)
    ext_id = str(goods_dict.get("external_goods_id") or goods_dict.get("goods_id") or "").strip()
    values["account_id"] = goods_dict.get("account_id")
    values["goods_id"] = ext_id or values.get("goods_id")
    values["external_goods_id"] = ext_id or values.get("external_goods_id")
    values["image_urls"] = _normalize_image_urls(values.get("image_urls"))
    if "status" in values:
        values["status"] = 1 if int(values["status"]) == 0 else 0 if int(values["status"]) == 1 else 2
    if "quantity" in values:
        try:
            qty = int(values["quantity"])
            # 列表 API 不返回库存：新增商品默认 999（闲鱼常见库存值），
            # 详情同步成功后会覆盖为真实值；避免本地库存为 0 导致 AI 客服误报"没库存"
            values["quantity"] = qty if qty > 0 else 999
        except (ValueError, TypeError):
            values["quantity"] = 999
    if "stock" in values:
        try:
            st = int(values["stock"])
            values["stock"] = st if st > 0 else 999
        except (ValueError, TypeError):
            values["stock"] = 999
    if values.get("detail_info") and not values.get("description"):
        values["description"] = values["detail_info"]
    if values.get("description") and not values.get("detail_info"):
        values["detail_info"] = values["description"]
    if not values.get("cover_pic"):
        if values.get("image_urls"):
            values["cover_pic"] = values["image_urls"][0]
        elif values.get("image_url"):
            values["cover_pic"] = values["image_url"]
    if not values.get("image_url") and values.get("cover_pic"):
        values["image_url"] = values["cover_pic"]
    values["deleted"] = 0
    values["created_time"] = datetime.now()
    values["updated_time"] = datetime.now()
    return values


def _build_goods_update_values(existing, goods_dict: dict, *, partial: bool) -> dict:
    values = _clean_goods_update_values(goods_dict, partial=partial)

    ext_id = str(goods_dict.get("external_goods_id") or goods_dict.get("goods_id") or "").strip()
    if ext_id:
        values["goods_id"] = ext_id
        values["external_goods_id"] = ext_id

    if "status" in values:
        values["status"] = 1 if int(values["status"]) == 0 else 0 if int(values["status"]) == 1 else 2

    if "quantity" in values:
        try:
            qty = int(values["quantity"])
            # 列表 API 不返回库存：当远程库存为 0 时默认 999（与新增商品保持一致），
            # 详情同步成功后会覆盖为真实值；避免本地库存为 0 导致 AI 客服误报"没库存"
            values["quantity"] = qty if qty > 0 else 999
        except (ValueError, TypeError):
            values["quantity"] = 999

    if "stock" in values:
        try:
            st = int(values["stock"])
            values["stock"] = st if st > 0 else 999
        except (ValueError, TypeError):
            values["stock"] = 999

    if "image_urls" in values:
        values["image_urls"] = _normalize_image_urls(values["image_urls"])
        if partial and not values["image_urls"]:
            values.pop("image_urls", None)

    if partial:
        for text_key in ("detail_info", "description", "detail_url", "category", "cover_pic", "image_url"):
            if text_key in values and isinstance(values[text_key], str) and not values[text_key].strip():
                values.pop(text_key, None)

    if "cover_pic" not in values:
        candidate_cover = getattr(existing, "cover_pic", None)
        if not candidate_cover and values.get("image_urls"):
            candidate_cover = values["image_urls"][0]
        if not candidate_cover:
            candidate_cover = values.get("image_url")
        if candidate_cover:
            values["cover_pic"] = candidate_cover

    if "image_url" not in values:
        candidate_image = values.get("cover_pic") or getattr(existing, "image_url", None)
        if candidate_image:
            values["image_url"] = candidate_image

    if "detail_info" in values and "description" not in values:
        values["description"] = values["detail_info"]
    if "description" in values and "detail_info" not in values:
        values["detail_info"] = values["description"]

    values["deleted"] = 0
    values["updated_time"] = datetime.now()
    return values


def _is_goods_changed(existing: dict, new_data: dict) -> bool:
    """
    比较商品是否有变化。
    比较关键字段：标题、价格、封面图、状态、库存、曝光、浏览、想要数。
    """
    compare_fields = [
        "title", "sold_price", "cover_pic", "status",
        "quantity", "exposure_count", "view_count", "want_count",
        "detail_info",
    ]
    for field in compare_fields:
        old_val = str(existing.get(field, "")) if existing.get(field) is not None else ""
        new_val = str(new_data.get(field, "")) if new_data.get(field) is not None else ""
        if old_val != new_val:
            return True
    return False



def fetch_goods_list(
    cookie_str: str,
    page_size: int = 20,
    max_pages: int = 50
) -> list[dict]:
    """
    分页获取闲鱼商品列表。
    按最小请求模型调用：pageNumber/pageSize/needGroupInfo/userId。
    """
    cookies_dict = _parse_cookie(cookie_str)
    user_id = cookies_dict.get("unb", "")
    if not user_id:
        raise RuntimeError("Cookie 中缺少 unb，无法同步商品")

    all_items = []
    page_num = 1

    while page_num <= max_pages:
        data = {
            "pageNumber": page_num,
            "pageSize": page_size,
            "needGroupInfo": True,
            "userId": user_id,
        }

        try:
            response = _make_api_request(cookie_str, ITEM_LIST_API, data)
            items = _parse_item_list_response(response)

            if not items:
                break

            all_items.extend(items)
            logger.info(
                "获取商品列表 page=%d, 本页=%d, 累计=%d",
                page_num, len(items), len(all_items)
            )

            # 如果本页数量少于 pageSize，说明是最后一页
            if len(items) < page_size:
                break

            page_num += 1

            # 请求间隔，避免触发风控。该函数保持同步，便于单元测试与脚本复用；
            # 异步调用方应通过 asyncio.to_thread 调用，避免阻塞事件循环。
            time.sleep(random.uniform(0.5, 1.5))

        except Exception as exc:
            logger.error(
                "获取商品列表失败 page=%d errorType=%s",
                page_num,
                type(exc).__name__,
            )
            raise

    return all_items


def fetch_item_detail(
    cookie_str: str,
    item_id: str,
) -> dict:
    """
    获取单个商品详情。
    
    参数:
        cookie_str: 闲鱼账号 Cookie
        item_id: 商品 ID
    
    返回: 商品详情数据
    """
    # 从 Cookie 中提取 unb
    cookies_dict = _parse_cookie(cookie_str)
    user_id = cookies_dict.get("unb", "")
    if not user_id:
        logger.warning("Cookie 中缺少 unb，无法获取商品详情")
        return {}

    data = {
        "itemId": item_id,
        "userId": user_id,
    }

    try:
        response = _make_api_request(cookie_str, ITEM_DETAIL_API, data)
        result = _parse_item_detail_response(response)
        if not result:
            ret = response.get("ret", []) if isinstance(response, dict) else "?"
            ret_msg = ret[0] if isinstance(ret, list) and ret else str(ret)
            # 检测风控：触发 Baxia 验证或 RGV587 时抛异常，让上层停止详情同步
            if "FAIL_SYS_USER_VALIDATE" in ret_msg or "RGV587" in ret_msg:
                raise RuntimeError("详情接口触发风控，请完成验证后重试")
            logger.warning("详情 API 返回空结果")
        return result
    except RuntimeError:
        raise  # 风控异常向上抛，让详情同步停止
    except Exception:
        logger.error("获取商品详情失败", exc_info=True)
        return {}


async def sync_goods_for_account(
    account_id: int,
    cookie_str: str,
    sync_id: str,
    db_session_factory,
    async_fetch_detail: bool = True,
) -> dict:
    """
    为指定账号执行完整商品同步流程。
    
    流程:
    1. 分页获取全部商品列表（在售+已售）
    2. 增量保存：比对已有数据，只更新变化的商品
    3. 标记本地多余商品为下架
    4. 异步获取商品详情（如有变化）
    
    返回: 同步结果摘要
    """
    start_time = time.time()

    # 一次性数据修复：把账号下所有库存为 0 或缺失的商品批量设为 999
    # 闲鱼商品不会真正"0库存"（0库存会被自动下架），所有 stock<=0 的本地记录
    # 都是同步链路历史遗留的脏数据。详情同步拿到真实库存后会覆盖为真实值。
    try:
        from ..core.database import async_session as _repair_session
        from ..models.entities import XianyuGoods as _repair_model
        from sqlalchemy import update as _repair_update, and_ as _repair_and, or_ as _repair_or
        async with _repair_session() as repair_db:
            repair_stmt = (
                _repair_update(_repair_model)
                .where(
                    _repair_and(
                        _repair_model.account_id == account_id,
                        _repair_model.deleted == 0,
                        _repair_or(
                            _repair_model.quantity <= 0,
                            _repair_model.quantity.is_(None),
                            _repair_model.stock <= 0,
                            _repair_model.stock.is_(None),
                        ),
                    )
                )
                .values(quantity=999, stock=999, updated_time=datetime.now())
            )
            repair_result = await repair_db.execute(repair_stmt)
            repaired_count = repair_result.rowcount or 0
            if repaired_count > 0:
                await repair_db.commit()
                logger.info("一次性库存修复: account_id=%d, 修复 %d 条 stock<=0 的商品为 999", account_id, repaired_count)
    except Exception as repair_exc:
        logger.warning("一次性库存修复失败: account_id=%d, errorType=%s", account_id, type(repair_exc).__name__)

    # 更新任务状态
    with _sync_lock:
        _prune_sync_tasks_locked()
        _sync_tasks[sync_id] = {
            "status": "running",
            "progress": 0,
            "total": 0,
            "updated": 0,
            "skipped": 0,
            "new": 0,
            "off_shelf": 0,
            "account_id": account_id,
            "started_at": datetime.now().isoformat(),
        }
    await _persist_sync_task(sync_id, account_id=account_id, status="running", progress=0)

    try:
        # Step 0: 刷新 _m_h5_tk 令牌，确保同步时使用有效令牌
        logger.info("开始同步商品: account_id=%d, 正在刷新令牌...", account_id)
        cookie_str = await asyncio.to_thread(_refresh_m_h5_tk, cookie_str)

        # Step 1: 直接按最小模型分页获取商品列表
        all_items = await asyncio.to_thread(fetch_goods_list, cookie_str)
        total_count = len(all_items)
        logger.info("商品列表获取完成: %d 件", total_count)

        with _sync_lock:
            _sync_tasks[sync_id]["total"] = total_count
            _sync_tasks[sync_id]["progress"] = 10
        await _persist_sync_task(sync_id, account_id=account_id, status="running", progress=10, total=total_count)

        if total_count == 0:
            with _sync_lock:
                _sync_tasks[sync_id]["status"] = "completed"
                _sync_tasks[sync_id]["progress"] = 100
            await _persist_sync_task(sync_id, account_id=account_id, status="completed", progress=100, total=0, new=0, updated=0, skipped=0, off_shelf=0, duration_seconds=round(time.time() - start_time, 1))
            return {
                "sync_id": sync_id,
                "total": 0,
                "updated": 0,
                "skipped": 0,
                "new": 0,
                "off_shelf": 0,
                "duration_seconds": round(time.time() - start_time, 1),
            }

        # Step 3: 使用同步数据库会话进行入库
        from ..core.database import async_session
        from ..models.entities import XianyuGoods
        from sqlalchemy import select, update, and_, desc

        async def _do_sync():
            updated_count = 0
            new_count = 0
            synced_ids = set()

            async with async_session() as db:
                for i, card_data in enumerate(all_items):
                    goods_dict = _parse_card_to_goods(card_data, account_id)
                    ext_id = goods_dict["external_goods_id"]
                    synced_ids.add(ext_id)

                    # 查询是否已存在
                    result = await db.execute(
                        select(XianyuGoods)
                        .where(
                            and_(
                                XianyuGoods.account_id == account_id,
                                XianyuGoods.external_goods_id == ext_id,
                            )
                        )
                        .order_by(desc(XianyuGoods.updated_time), desc(XianyuGoods.id))
                    )
                    existing = result.scalars().first()

                    if existing:
                        # 直接更新已有商品（不跳过任何商品，确保所有同步商品都在列表中展示）
                        # 闲鱼列表 API 不返回库存：当远程库存为 0 时保留本地 stock，
                        # 避免把"发布时填写的库存"或"详情同步填入的真实库存"清零。
                        update_values = _build_goods_update_values(existing, goods_dict, partial=True)
                        if "quantity" in update_values and int(update_values["quantity"]) <= 0:
                            update_values.pop("quantity", None)
                        if "stock" in update_values and int(update_values["stock"]) <= 0:
                            update_values.pop("stock", None)
                        # 列表 API 不返回库存：若本地库存仍为 0 或缺失（详情同步尚未完成或失败），
                        # 设为 999（闲鱼常见库存值）兜底，避免 AI 客服误报"没库存"。
                        # 详情同步成功后会覆盖为真实值；若本地已有真实库存（>0）则保留。
                        local_qty = int(getattr(existing, "quantity", 0) or 0)
                        local_st = int(getattr(existing, "stock", 0) or 0)
                        if local_qty <= 0 and "quantity" not in update_values:
                            update_values["quantity"] = 999
                        if local_st <= 0 and "stock" not in update_values:
                            update_values["stock"] = 999
                        stmt = (
                            update(XianyuGoods)
                            .where(XianyuGoods.id == existing.id)
                            .values(**update_values)
                        )
                        await db.execute(stmt)
                        updated_count += 1
                    else:
                        # 新增
                        new_goods = XianyuGoods(**_build_goods_insert_values(goods_dict))
                        db.add(new_goods)
                        new_count += 1

                    # 更新进度
                    progress = 10 + int((i + 1) / total_count * 70)
                    with _sync_lock:
                        _sync_tasks[sync_id]["progress"] = min(progress, 80)
                        _sync_tasks[sync_id]["updated"] = updated_count
                        _sync_tasks[sync_id]["skipped"] = 0
                        _sync_tasks[sync_id]["new"] = new_count
                    if (i + 1) == total_count or (i + 1) % 10 == 0:
                        await _persist_sync_task(sync_id, account_id=account_id, status="running", progress=min(progress, 80), total=total_count, updated=updated_count, skipped=0, new=new_count)

                # Step 4: 标记本地多余商品为下架
                off_shelf_count = 0
                if synced_ids:
                    # 查找本地有但远程没有的商品（在售状态）
                    local_result = await db.execute(
                        select(XianyuGoods).where(
                            and_(
                                XianyuGoods.account_id == account_id,
                                XianyuGoods.deleted == 0,
                            )
                        )
                    )
                    local_goods = local_result.scalars().all()

                    for local_g in local_goods:
                        if local_g.external_goods_id not in synced_ids:
                            stmt = (
                                update(XianyuGoods)
                                .where(XianyuGoods.id == local_g.id)
                                .values(
                                    deleted=1,
                                    status=0 if local_g.status != 2 else 2,
                                    updated_time=datetime.now(),
                                )
                            )
                            await db.execute(stmt)
                            off_shelf_count += 1

                await db.commit()

                with _sync_lock:
                    _sync_tasks[sync_id]["off_shelf"] = off_shelf_count
                    _sync_tasks[sync_id]["progress"] = 90
                await _persist_sync_task(sync_id, account_id=account_id, status="running", progress=90, total=total_count, updated=updated_count, skipped=0, new=new_count, off_shelf=off_shelf_count)

                return {
                    "updated": updated_count,
                    "skipped": 0,
                    "new": new_count,
                    "off_shelf": off_shelf_count,
                    "synced_ids": synced_ids,
                }

        sync_result = await _do_sync()

        # Step 5: 异步获取详情（如果有变化的商品）
        # 修复：只要有任何商品（新增或更新）就触发详情同步
        # 原逻辑仅 updated > 0 触发，导致首次同步全是新商品时（updated=0）跳过详情同步，
        # 新商品库存永远为 0（列表 API 不返回库存字段）
        detail_synced = 0
        total_changed = sync_result.get("updated", 0) + sync_result.get("new", 0)
        if async_fetch_detail and total_changed > 0:
            logger.info("创建详情同步任务: account_id=%d, items_count=%d, updated=%d, new=%d",
                        account_id, len(all_items), sync_result.get("updated", 0), sync_result.get("new", 0))
            task = asyncio.create_task(
                _async_fetch_details(cookie_str, all_items, account_id, sync_id),
                name="goods.detail-sync",
            )
            _detail_sync_tasks.add(task)
            task.add_done_callback(_detail_sync_done)
            detail_synced = total_changed
        else:
            logger.info("跳过详情同步: async_fetch_detail=%s, updated=%s, new=%s",
                        async_fetch_detail, sync_result.get("updated", 0), sync_result.get("new", 0))

        duration = round(time.time() - start_time, 1)

        with _sync_lock:
            _sync_tasks[sync_id]["status"] = "completed"
            _sync_tasks[sync_id]["progress"] = 100
            _sync_tasks[sync_id]["detail_synced"] = detail_synced
            _sync_tasks[sync_id]["duration_seconds"] = duration
        await _persist_sync_task(sync_id, account_id=account_id, status="completed", progress=100, total=total_count, new=sync_result["new"], updated=sync_result["updated"], skipped=sync_result["skipped"], off_shelf=sync_result["off_shelf"], detail_synced=detail_synced, duration_seconds=duration)

        logger.info(
            "商品同步完成: account_id=%d, total=%d, new=%d, updated=%d, skipped=%d, off_shelf=%d, duration=%.1fs",
            account_id, total_count,
            sync_result["new"], sync_result["updated"],
            sync_result["skipped"], sync_result["off_shelf"],
            duration,
        )

        return {
            "sync_id": sync_id,
            "total": total_count,
            "new": sync_result["new"],
            "updated": sync_result["updated"],
            "skipped": sync_result["skipped"],
            "off_shelf": sync_result["off_shelf"],
            "detail_synced": detail_synced,
            "duration_seconds": duration,
        }

    except Exception:
        logger.error("商品同步失败: account_id=%d", account_id, exc_info=True)
        public_error = "商品同步失败，请检查账号登录状态后重试"
        with _sync_lock:
            _sync_tasks[sync_id]["status"] = "failed"
            _sync_tasks[sync_id]["error"] = public_error
            _sync_tasks[sync_id]["progress"] = 0
        await _persist_sync_task(sync_id, account_id=account_id, status="failed", progress=0, error=public_error, duration_seconds=round(time.time() - start_time, 1))
        raise


async def _async_fetch_details(
    cookie_str: str,
    items: list[dict],
    account_id: int,
    sync_id: str,
):
    """
    异步获取商品详情（后台线程）。
    延迟策略：在售商品 1.5~4s，已售商品 5~10s。
    """
    import asyncio
    from ..core.database import async_session
    from ..models.entities import XianyuGoods
    from sqlalchemy import select, update, and_

    logger.info("详情同步任务启动: account_id=%d, sync_id=%s, items_count=%d", account_id, sync_id, len(items))

    async def _do_detail_sync():
        detail_count = 0
        logger.info("详情同步循环开始: account_id=%d, items=%d", account_id, len(items))
        for i, card_data in enumerate(items):
            item_id = str(card_data.get("id", "") or _safe_get_nested(card_data, "detailParams", "itemId") or card_data.get("itemId", ""))
            if not item_id:
                logger.warning("详情同步: 跳过无 item_id 的商品 (index=%d)", i)
                continue

            item_status = card_data.get("itemStatus", 0)

            # 延迟策略（增大延迟，降低风控触发概率）
            if item_status == 0:  # 在售
                delay = 3.0 + random.uniform(0, 3.0)
            else:  # 已售/已下架
                delay = 6.0 + random.uniform(0, 6.0)

            await asyncio.sleep(delay)

            try:
                # 用 asyncio.to_thread 包装同步 HTTP 调用，避免阻塞事件循环
                detail_data = await asyncio.to_thread(fetch_item_detail, cookie_str, item_id)
                if not detail_data:
                    logger.warning("详情同步: itemId=%s 返回空数据 (index=%d/%d)", item_id, i + 1, len(items))
                    continue

                # 兼容详情数据的不同结构：data.itemDO / data.item / 顶层
                item_info = detail_data.get("itemDO", {}) or detail_data.get("item", {}) or detail_data
                if not isinstance(item_info, dict):
                    item_info = detail_data

                desc = item_info.get("desc", "") or item_info.get("description", "")

                # 提取真实库存：优先 itemDO.quantity，兜底用 SKU 库存求和
                # 闲鱼列表 API 不返回库存，详情 API 才有 data.itemDO.quantity
                remote_quantity = 0
                try:
                    remote_quantity = int(item_info.get("quantity", 0) or 0)
                except (ValueError, TypeError):
                    remote_quantity = 0
                if remote_quantity <= 0:
                    sku_list = item_info.get("skuList") or item_info.get("idleItemSkuList") or []
                    if isinstance(sku_list, list):
                        sku_sum = 0
                        for sku in sku_list:
                            if isinstance(sku, dict):
                                try:
                                    sku_sum += int(sku.get("quantity", 0) or 0)
                                except (ValueError, TypeError):
                                    pass
                        if sku_sum > 0:
                            remote_quantity = sku_sum

                # 提取商品统计字段：浏览量、想要数
                # 兼容多套字段命名与嵌套结构（itemDO.browseCnt / itemStats.viewCount / gpageInfo.pv）
                stat_sources = [
                    item_info,
                    item_info.get("itemStats") if isinstance(item_info.get("itemStats"), dict) else {},
                    item_info.get("gpageInfo") if isinstance(item_info.get("gpageInfo"), dict) else {},
                    detail_data.get("gpageInfo") if isinstance(detail_data.get("gpageInfo"), dict) else {},
                    detail_data.get("itemStats") if isinstance(detail_data.get("itemStats"), dict) else {},
                ]
                remote_view_count = 0
                remote_want_count = 0
                remote_exposure_count = 0
                for src in stat_sources:
                    if not src:
                        continue
                    if remote_view_count <= 0:
                        for vk in ("browseCnt", "browseCount", "viewCount", "pv", "uv", "ipv"):
                            v = src.get(vk)
                            if v is None:
                                continue
                            try:
                                v_int = int(v)
                                if v_int > 0:
                                    remote_view_count = v_int
                                    break
                            except (ValueError, TypeError):
                                pass
                    if remote_want_count <= 0:
                        for wk in ("wantCnt", "wishCount", "wantCount", "favorCount", "want"):
                            w = src.get(wk)
                            if w is None:
                                continue
                            try:
                                w_int = int(w)
                                if w_int > 0:
                                    remote_want_count = w_int
                                    break
                            except (ValueError, TypeError):
                                pass
                    if remote_exposure_count <= 0:
                        for ek in ("exposureCount", "exposureNum", "exposure", "impression", "impressionCount"):
                            e = src.get(ek)
                            if e is None:
                                continue
                            try:
                                e_int = int(e)
                                if e_int > 0:
                                    remote_exposure_count = e_int
                                    break
                            except (ValueError, TypeError):
                                pass

                # 有描述、库存或统计字段任一可更新时才写库
                if desc or remote_quantity > 0 or remote_exposure_count > 0 or remote_view_count > 0 or remote_want_count > 0:
                    async with async_session() as db:
                        goods_result = await db.execute(
                            select(XianyuGoods).where(
                                and_(
                                    XianyuGoods.account_id == account_id,
                                    XianyuGoods.external_goods_id == item_id,
                                    XianyuGoods.deleted == 0,
                                )
                            )
                        )
                        existing = goods_result.scalar_one_or_none()
                        if existing:
                            detail_goods_dict = {
                                "external_goods_id": item_id,
                                "detail_info": str(desc) if desc else "",
                                "description": str(desc) if desc else "",
                                "quantity": remote_quantity if remote_quantity > 0 else None,
                                "stock": remote_quantity if remote_quantity > 0 else None,
                                "exposure_count": remote_exposure_count if remote_exposure_count > 0 else None,
                                "view_count": remote_view_count if remote_view_count > 0 else None,
                                "want_count": remote_want_count if remote_want_count > 0 else None,
                            }
                            _merge_detail_info(detail_goods_dict, detail_data)
                            update_values = _build_goods_update_values(existing, detail_goods_dict, partial=True)
                            stmt = (
                                update(XianyuGoods)
                                .where(XianyuGoods.id == existing.id)
                                .values(**update_values)
                            )
                            await db.execute(stmt)
                            await db.commit()

                    detail_count += 1
                    logger.info(
                        "详情同步: itemId=%s, quantity=%s, exposure=%d, view=%d, want=%d (%d/%d)",
                        item_id, remote_quantity or "-", remote_exposure_count, remote_view_count, remote_want_count,
                        detail_count, len(items)
                    )

            except RuntimeError as exc:
                if "风控" in str(exc):
                    logger.warning(
                        "详情同步因风控停止 accountId=%d processed=%d total=%d",
                        account_id,
                        i + 1,
                        len(items),
                    )
                    break
                logger.error("详情同步失败 errorType=%s", type(exc).__name__)
            except Exception as exc:
                logger.error("详情同步失败 errorType=%s", type(exc).__name__)

            # 更新进度
            with _sync_lock:
                if sync_id in _sync_tasks:
                    detail_progress = 90 + int((i + 1) / len(items) * 10)
                    _sync_tasks[sync_id]["detail_progress"] = min(detail_progress, 100)
                    _sync_tasks[sync_id]["detail_count"] = detail_count

        logger.info("详情同步循环结束: account_id=%d, 成功=%d, 总计=%d", account_id, detail_count, len(items))
        with _sync_lock:
            if sync_id in _sync_tasks:
                _sync_tasks[sync_id]["detail_completed"] = True
                _sync_tasks[sync_id]["detail_count"] = detail_count
        await _persist_sync_task(sync_id, account_id=account_id, detail_synced=detail_count)

    try:
        await _do_detail_sync()
    except Exception as exc:
        logger.error("异步详情同步失败 errorType=%s", type(exc).__name__)


async def upsert_goods_record(
    db,
    *,
    account_id: int,
    goods_dict: dict,
    partial: bool = False,
):
    from ..models.entities import XianyuGoods
    from sqlalchemy import select, and_, desc

    ext_id = str(goods_dict.get("external_goods_id") or goods_dict.get("goods_id") or "").strip()
    if not ext_id:
        return None, False

    result = await db.execute(
        select(XianyuGoods)
        .where(
            and_(
                XianyuGoods.account_id == account_id,
                XianyuGoods.external_goods_id == ext_id,
            )
        )
        .order_by(desc(XianyuGoods.updated_time), desc(XianyuGoods.id))
    )
    existing = result.scalars().first()

    if existing:
        update_values = _build_goods_update_values(existing, goods_dict, partial=partial)
        for key, value in update_values.items():
            setattr(existing, key, value)
        return existing, False

    insert_values = _build_goods_insert_values(
        {
            **goods_dict,
            "account_id": account_id,
        }
    )
    new_goods = XianyuGoods(**insert_values)
    db.add(new_goods)
    return new_goods, True


async def persist_published_goods(
    db,
    *,
    account_id: int,
    cookie_str: str,
    publish_result: dict,
    publish_payload: dict,
    target_goods_id: int | None = None,
) -> Optional[dict]:
    item_id = str(publish_result.get("itemId") or "").strip()
    if not item_id:
        return None

    image_urls = publish_payload.get("imageUrls") or []
    primary_image = image_urls[0] if image_urls else ""
    publish_description = str(
        publish_payload.get("desc") or publish_payload.get("description") or ""
    ).strip()
    goods_dict = {
        "account_id": account_id,
        "goods_id": item_id,
        "external_goods_id": item_id,
        "title": str(publish_payload.get("title") or "").strip(),
        "price": str(publish_payload.get("price") or ""),
        "sold_price": str(publish_payload.get("price") or ""),
        "cover_pic": primary_image,
        "image_url": primary_image,
        "image_urls": image_urls,
        "stock": int(publish_payload.get("quantity") or 0),
        "quantity": int(publish_payload.get("quantity") or 0),
        "detail_info": publish_description,
        "description": publish_description,
        "category": str((publish_payload.get("category") or {}).get("catName") or ""),
        "detail_url": publish_result.get("itemUrl") or "",
        "status": 0,
        "deleted": 0,
        "raw_payload": {
            "publishPayload": publish_payload,
            "publishResult": publish_result,
        },
    }

    # The product is already published at this point.  A risk-control response
    # from the optional detail API must not roll back the title/description
    # supplied on the publish form; save those local facts first and let a
    # later sync enrich them when the remote endpoint is available.
    detail_data: dict = {}
    try:
        detail_data = await asyncio.to_thread(fetch_item_detail, cookie_str, item_id)
    except RuntimeError:
        logger.warning("发布后商品详情拉取被跳过")
    except Exception:  # noqa: BLE001
        logger.warning("发布后商品详情拉取失败")
    if detail_data:
        _merge_detail_info(goods_dict, detail_data)
        raw_payload = goods_dict.get("raw_payload") or {}
        raw_payload["detailData"] = detail_data
        goods_dict["raw_payload"] = raw_payload

    if target_goods_id is not None:
        from sqlalchemy import select
        from ..models.entities import XianyuGoods

        target = (
            await db.execute(
                select(XianyuGoods)
                .where(XianyuGoods.id == target_goods_id, XianyuGoods.deleted == 0)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if target is None:
            raise RuntimeError("target local goods is no longer available")
        if target.account_id is not None and int(target.account_id) != int(account_id):
            raise RuntimeError("target local goods account changed")
        target.account_id = account_id
        for key, value in _build_goods_update_values(target, goods_dict, partial=False).items():
            setattr(target, key, value)
        goods = target
    else:
        goods, _ = await upsert_goods_record(
            db,
            account_id=account_id,
            goods_dict=goods_dict,
            partial=False,
        )
    # Flush inside the local phase so constraint/storage failures are captured
    # as remote_confirmed and the coordinator can safely retry local work only.
    flush = getattr(db, "flush", None)
    if callable(flush):
        await flush()
    return {
        "itemId": item_id,
        "localId": int(goods.id) if goods is not None and goods.id is not None else target_goods_id,
        "title": getattr(goods, "title", None) if goods else goods_dict.get("title"),
        "detailFetched": bool(detail_data),
    }


def get_sync_progress(sync_id: str) -> Optional[dict]:
    """获取同步任务进度"""
    with _sync_lock:
        _prune_sync_tasks_locked()
        task = _sync_tasks.get(sync_id)
        return dict(task) if task else None


def is_account_syncing(account_id: int) -> bool:
    """检查指定账号是否正在同步"""
    with _sync_lock:
        _prune_sync_tasks_locked()
        for task in _sync_tasks.values():
            if task.get("account_id") == account_id and task.get("status") == "running":
                return True
    return False


# ==================== 商品操作（下架/删除） ====================


def extract_token_from_cookie(cookie_str: str) -> Optional[str]:
    """
    从 cookie 字符串中提取 _m_h5_tk 的值，取 _ 前面的部分作为 token。
    """
    for part in cookie_str.split(";"):
        part = part.strip()
        if part.startswith("_m_h5_tk="):
            return part.split("=", 1)[1].split("_")[0]
    return None


class XianyuItemOperator:
    """
    闲鱼商品操作器。
    支持下架、删除等操作，根据账号类型（普通账号/鱼小铺）使用不同的 API。
    """

    # 擦亮 API（mtop.taobao.idle.item.polish 是闲鱼官方擦亮接口，已通过实测定可用）
    # 注：曾经存在的备用 API "mtop.idle.item.polish" 已被官方下线（返回 FAIL_SYS_API_NOT_FOUNDED），故移除
    # 普通账号 API
    NORMAL_OFF_SHELF_API = "mtop.taobao.idle.item.downshelf"
    NORMAL_OFF_SHELF_VERSION = "2.0"
    NORMAL_DELETE_API = "com.taobao.idle.item.delete"
    NORMAL_DELETE_VERSION = "1.1"

    # 鱼小铺 API
    SELLER_OFF_SHELF_API = "mtop.alibaba.idle.seller.pc.item.offline"
    SELLER_OFF_SHELF_VERSION = "1.0"
    SELLER_DELETE_API = "mtop.alibaba.idle.seller.pc.item.delete"
    SELLER_DELETE_VERSION = "1.0"
    SELLER_SEARCH_API = "mtop.alibaba.idle.seller.pc.common.item.search"
    SELLER_SEARCH_VERSION = "1.0"
    SELLER_UPDATE_API = "mtop.alibaba.idle.seller.pc.item.info.update"
    SELLER_UPDATE_VERSION = "1.0"

    def __init__(self, cookie_str: str, is_fish_shop: bool = False):
        self.cookie_str = cookie_str
        self.is_seller = is_fish_shop
        self.token = extract_token_from_cookie(cookie_str)
        if not self.token:
            raise RuntimeError("Cookie 中缺少 _m_h5_tk，无法签名")

    def _build_sign(self, t_ms: str, data_json: str) -> str:
        """构建 MD5 签名"""
        raw = f"{self.token}&{t_ms}&{APP_KEY}&{data_json}"
        return hashlib.md5(raw.encode()).hexdigest()

    def _build_url(self, api_name: str, version: str, t_ms: str, sign: str) -> str:
        """构建请求 URL"""
        params = {
            "jsv": "2.7.2",
            "appKey": APP_KEY,
            "t": t_ms,
            "sign": sign,
            "v": version,
            "type": "json" if self.is_seller else "originaljson",
            "dataType": "json",
            "accountSite": "xianyu",
            "timeout": "20000",
            "api": api_name,
        }
        if self.is_seller:
            params["sessionOption"] = "AutoLoginOnly"
            params["spm_cnt"] = "a21ybx.item.0.0"

        query = urlencode(params)
        return f"{H5_API_BASE}/{api_name}/{version}/?{query}"

    def _get_headers(self) -> dict:
        """构建请求头"""
        if self.is_seller:
            return {
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                "Cookie": self.cookie_str,
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Origin": "https://seller.goofish.com",
                "Referer": "https://seller.goofish.com/",
                "idle_site_biz_code": "COMMONPRO",
                "idle_user_group_member_id": "",
            }
        else:
            return {
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                "Cookie": self.cookie_str,
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Origin": "https://www.goofish.com",
                "Referer": "https://www.goofish.com/",
            }

    def _call_api(self, api_name: str, version: str, data: dict) -> dict:
        """
        调用闲鱼 mtop API。
        返回解析后的 JSON 响应。
        """
        t_ms = str(int(time.time() * 1000))
        data_json = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
        sign = self._build_sign(t_ms, data_json)

        url = self._build_url(api_name, version, t_ms, sign)
        headers = self._get_headers()

        with requests.Session() as session:
            session.trust_env = False
            resp = session.post(
                url,
                headers=headers,
                data={"data": data_json},
                timeout=30,
            )
        resp.raise_for_status()

        result = resp.json()

        # 检查响应
        ret = result.get("ret", [])
        ret_msg = ret[0] if isinstance(ret, list) and ret else str(ret)
        is_polish = False  # polish feature removed; keep variable for minimal diff
        if RGV587 in str(ret_msg):
            raise RuntimeError("触发风控(RGV587)，请稍后再试")
        if TOKEN_EXPIRED in str(ret_msg) or TOKEN_EXPIRED_ALIAS in str(ret_msg) or SESSION_EXPIRED in str(ret_msg):
            raise RuntimeError("登录已过期，请重新登录闲鱼账号")
        if not any("SUCCESS" in str(r) for r in ret):
            raise RuntimeError("闲鱼平台暂未接受商品操作请求，请稍后重试")

        # 鱼小铺接口额外检查 data.data
        if self.is_seller:
            data_body = result.get("data", {})
            if isinstance(data_body, dict) and data_body.get("data") is False:
                msg = data_body.get("msg", "未知错误")
                raise RuntimeError(f"鱼小铺操作失败: {msg}")

        return result



    def off_shelf(self, item_id: str) -> bool:
        """
        下架商品。
        返回 True 表示操作成功。
        """
        if self.is_seller:
            api = self.SELLER_OFF_SHELF_API
            version = self.SELLER_OFF_SHELF_VERSION
        else:
            api = self.NORMAL_OFF_SHELF_API
            version = self.NORMAL_OFF_SHELF_VERSION

        data = {"itemId": item_id}
        self._call_api(api, version, data)
        return True

    def delete(self, item_id: str) -> bool:
        """
        从闲鱼删除商品。
        返回 True 表示操作成功。
        """
        if self.is_seller:
            api = self.SELLER_DELETE_API
            version = self.SELLER_DELETE_VERSION
        else:
            api = self.NORMAL_DELETE_API
            version = self.NORMAL_DELETE_VERSION

        data = {"itemId": item_id}
        if self.is_seller:
            data["draftId"] = None

        self._call_api(api, version, data)
        return True

    def off_shelf_batch(self, item_ids: list[str]) -> dict[str, bool]:
        """
        批量下架商品。
        返回 { item_id: success_status } 字典。
        """
        results = {}
        for item_id in item_ids:
            try:
                self.off_shelf(item_id)
                results[item_id] = True
                logger.info("下架成功: itemId=%s", item_id)
            except Exception as exc:
                logger.error("下架失败 errorType=%s", type(exc).__name__)
                results[item_id] = False
            # 避免触发风控
            time.sleep(random.uniform(0.5, 1.5))
        return results

    def delete_batch(self, item_ids: list[str]) -> dict[str, bool]:
        """
        批量删除商品。
        返回 { item_id: success_status } 字典。
        """
        results = {}
        for item_id in item_ids:
            try:
                self.delete(item_id)
                results[item_id] = True
                logger.info("删除成功: itemId=%s", item_id)
            except Exception as exc:
                logger.error("删除失败 errorType=%s", type(exc).__name__)
                results[item_id] = False
            # 避免触发风控
            time.sleep(random.uniform(0.5, 1.5))
        return results

    # ==================== 改价相关方法（仅鱼小铺） ====================

    @staticmethod
    def _seller_search_payload(item_id: str, item_status: str | None = None) -> dict:
        """
        构建卖家工作台商品搜索请求 payload。
        
        Args:
            item_id: 闲鱼商品ID
            item_status: 商品状态筛选，None=不限，'0,-9'=在售，'1'=下架
        """
        search_request = json.dumps({"itemId": item_id}, ensure_ascii=False, separators=(',', ':'))
        payload = {
            "pageNo": 1,
            "pageSize": 20,
            "bizType": "commonPro",
            "searchRequest": search_request,
        }
        if item_status is not None:
            payload["itemStatus"] = item_status
        return payload

    def _find_seller_item(self, item_id: str) -> dict:
        """
        在卖家工作台搜索指定商品，获取完整商品信息（含 SKU 数据）。
        
        按不同状态（不限/在售/下架）搜索，找到即返回。
        改价接口需要商品完整信息（包括 idleItemSkuList），不能仅传 itemId。
        
        Returns:
            商品的完整 JSON 数据节点
            
        Raises:
            RuntimeError: 在所有状态中都未找到该商品
        """
        payloads = [
            self._seller_search_payload(item_id, None),       # 不限状态
            self._seller_search_payload(item_id, "0,-9"),     # 在售/出售中
            self._seller_search_payload(item_id, "1"),        # 已下架
        ]

        for payload in payloads:
            try:
                response = self._call_api(self.SELLER_SEARCH_API, self.SELLER_SEARCH_VERSION, payload)
                ret = response.get("ret", [])
                if not any("SUCCESS" in str(r) for r in ret):
                    continue

                data_body = response.get("data", {})
                if not isinstance(data_body, dict):
                    continue

                item_list = data_body.get("data", {})
                if isinstance(item_list, dict):
                    search_response_list = item_list.get("itemSearchResponseList", [])
                    if isinstance(search_response_list, list):
                        for item in search_response_list:
                            if isinstance(item, dict) and str(item.get("itemId", "")) == str(item_id):
                                logger.info("在卖家工作台找到目标商品")
                                return item
            except Exception as exc:
                logger.debug(
                    "搜索卖家商品异常 itemStatus=%s errorType=%s",
                    payload.get("itemStatus", "null"),
                    type(exc).__name__,
                )
                continue

        raise RuntimeError("鱼小铺工作台未找到目标商品，无法改价")

    @staticmethod
    def _safe_quantity(seller_item: dict) -> int:
        """安全读取商品库存，兜底返回 0"""
        try:
            raw = seller_item.get("quantity", 0)
            if raw is None:
                return 0
            return int(raw)
        except (ValueError, TypeError):
            return 0

    def _build_seller_price_update_payload(self, seller_item: dict, price: str) -> dict:
        """
        构建卖家工作台改价请求 payload。
        
        有 SKU 的商品需要同时更新每个 SKU 的价格（itemSkuListStr），
        无 SKU 的商品直接设置 quantity 和 price。
        """
        item_id = seller_item.get("itemId", "")
        if not item_id:
            raise RuntimeError("卖家商品数据中缺少 itemId")

        data = {"itemId": item_id}

        sku_list = seller_item.get("idleItemSkuList", [])
        if isinstance(sku_list, list) and len(sku_list) > 0:
            # 有SKU：构建 itemSkuListStr
            items = []
            for sku in sku_list:
                if not isinstance(sku, dict):
                    continue
                sku_id = sku.get("skuId", "")
                if not sku_id:
                    continue
                items.append({
                    "skuId": str(sku_id),
                    "quantity": self._safe_quantity(sku),
                    "price": price,
                })
            if items:
                data["itemSkuListStr"] = json.dumps(items, ensure_ascii=False, separators=(',', ':'))
                return data

        # 无SKU：直接设置库存和价格
        data["quantity"] = self._safe_quantity(seller_item)
        data["price"] = price
        return data

    def update_price(self, item_id: str, price: str) -> bool:
        """
        修改闲鱼商品价格（仅鱼小铺账号支持）。
        
        流程：
        1. 在卖家工作台搜索商品，获取完整信息（含 SKU）
        2. 构建改价请求 payload（有SKU则更新每个SKU的价格）
        3. 调用卖家工作台改价 API
        
        Args:
            item_id: 闲鱼商品ID
            price: 新价格（字符串，如 "99.99"）
            
        Returns:
            True 表示操作成功
            
        Raises:
            RuntimeError: 如果不是鱼小铺账号、未找到商品或 API 调用失败
        """
        if not self.is_seller:
            raise RuntimeError("当前账号不是鱼小铺，无法改价")

        # Step 1: 在卖家工作台搜索商品
        seller_item = self._find_seller_item(item_id)

        # Step 2: 构建改价请求参数
        payload = self._build_seller_price_update_payload(seller_item, price)

        # Step 3: 调用改价 API
        self._call_api(self.SELLER_UPDATE_API, self.SELLER_UPDATE_VERSION, payload)
        logger.info("商品改价平台调用已确认")
        return True

    def update_price_batch(self, item_ids: list[str], price: str) -> dict[str, bool]:
        """
        批量修改商品价格。
        返回 { item_id: success_status } 字典。
        """
        results = {}
        for item_id in item_ids:
            try:
                self.update_price(item_id, price)
                results[item_id] = True
                logger.info("批量改价单项已确认")
            except Exception:
                logger.error("批量改价单项失败")
                results[item_id] = False
            time.sleep(random.uniform(1.0, 3.0))
        return results


class XianyuItemPublisher:
    """
    闲鱼商品发布器（增强版）。
    流程：
      Step 1: 类目推荐 (mtop.taobao.idle.kgraph.property.recommend)
      Step 2: 构建发布数据 (mtop.idle.pc.idleitem.publish)
      Step 3: 发布调用与响应解析

    API: mtop.idle.pc.idleitem.publish v1.0
    """

    PUBLISH_API = "mtop.idle.pc.idleitem.publish"
    PUBLISH_VERSION = "1.0"

    CATEGORY_RECOMMEND_API = "mtop.taobao.idle.kgraph.property.recommend"
    CATEGORY_RECOMMEND_VERSION = "2.0"

    # 图片上传 API（闲鱼 stream-upload）
    IMAGE_UPLOAD_URL = "https://stream-upload.goofish.com/api/upload.api"
    IMAGE_UPLOAD_APPKEY = "xy_chat"

    # 默认类目（软件安装包/序列号/激活码）
    DEFAULT_CAT_ID = "50025461"
    DEFAULT_CAT_NAME = "软件安装包/序列号/激活码"
    DEFAULT_CHANNEL_CAT_ID = "201449620"
    DEFAULT_TB_CAT_ID = "50003316"

    def __init__(self, cookie_str: str):
        self.cookie_str = cookie_str
        self.token = extract_token_from_cookie(cookie_str)
        if not self.token:
            raise RuntimeError("Cookie 中缺少 _m_h5_tk，无法签名")

    # ---- 签名 & 请求 ----

    def _build_sign(self, t_ms: str, data_json: str) -> str:
        raw = f"{self.token}&{t_ms}&{APP_KEY}&{data_json}"
        return hashlib.md5(raw.encode()).hexdigest()

    def _build_url(self, api_name: str, version: str, t_ms: str, sign: str) -> str:
        params = {
            "jsv": "2.7.2",
            "appKey": APP_KEY,
            "t": t_ms,
            "sign": sign,
            "v": version,
            "type": "originaljson",
            "dataType": "json",
            "timeout": "30000",
            "api": api_name,
            "spm_cnt": "a21ybx.item.0.0",
        }
        query = urlencode(params)
        return f"{H5_API_BASE}/{api_name}/{version}/?{query}"

    def _get_headers(self) -> dict:
        return {
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "Cookie": self.cookie_str,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Origin": "https://www.goofish.com",
            "Referer": "https://www.goofish.com/",
        }

    def _call_api(self, api_name: str, version: str, data: dict) -> dict:
        """统一调用闲鱼 MTop API"""
        t_ms = str(int(time.time() * 1000))
        data_json = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
        sign = self._build_sign(t_ms, data_json)

        url = self._build_url(api_name, version, t_ms, sign)
        headers = self._get_headers()

        logger.info(
            "调用闲鱼 API: api=%s, version=%s, payload_bytes=%d",
            api_name,
            version,
            len(data_json.encode("utf-8")),
        )

        with requests.Session() as session:
            session.trust_env = False
            resp = session.post(url, headers=headers, data={"data": data_json}, timeout=60)
        resp.raise_for_status()

        result = resp.json()
        ret = result.get("ret", [])
        ret_msg = ret[0] if isinstance(ret, list) and ret else str(ret)

        if RGV587 in str(ret_msg):
            raise RuntimeError("触发风控(RGV587)，请稍后再试")
        if TOKEN_EXPIRED in str(ret_msg) or TOKEN_EXPIRED_ALIAS in str(ret_msg) or SESSION_EXPIRED in str(ret_msg):
            raise RuntimeError("登录已过期，请重新登录闲鱼账号")

        logger.info("闲鱼 API 返回: api=%s success=%s", api_name, "SUCCESS" in str(ret_msg))
        return result

    # ---- 图片压缩 ----

    @staticmethod
    def _compress_image(img_data: bytes, max_size: int = 5 * 1024 * 1024) -> bytes:
        """
        压缩图片：缩放到 ≤1920×1920，转 JPEG，文件 ≤ max_size。
        参考闲鱼平台图片上传要求。
        """
        try:
            img = Image.open(io.BytesIO(img_data))

            # 转换为 RGB（去除 alpha 通道）
            if img.mode in ("RGBA", "P", "LA"):
                background = Image.new("RGB", img.size, (255, 255, 255))
                if img.mode == "P":
                    img = img.convert("RGBA")
                background.paste(img, mask=img.split()[-1] if img.mode == "RGBA" else None)
                img = background
            elif img.mode != "RGB":
                img = img.convert("RGB")

            # 尺寸缩放：超过 1920px 时等比缩小
            max_width, max_height = 1920, 1920
            width, height = img.size
            if width > max_width or height > max_height:
                scale = min(max_width / width, max_height / height)
                new_size = (int(width * scale), int(height * scale))
                img = img.resize(new_size, Image.Resampling.LANCZOS)
                logger.info("图片缩放: %dx%d → %dx%d", width, height, new_size[0], new_size[1])

            # JPEG 压缩，逐步降低质量直到文件 ≤ max_size
            quality = 85
            for attempt in range(3):
                out = io.BytesIO()
                img.save(out, format="JPEG", quality=quality, optimize=True)
                compressed = out.getvalue()
                if len(compressed) <= max_size:
                    logger.info("图片压缩完成: %d bytes (quality=%d)", len(compressed), quality)
                    return compressed
                quality = max(30, quality - 25)
                logger.info("图片仍过大 %d bytes, 降低 quality 至 %d", len(compressed), quality)

            # 最后一次尝试
            out = io.BytesIO()
            img.save(out, format="JPEG", quality=quality, optimize=True)
            compressed = out.getvalue()
            logger.info("图片压缩完成(最低质量): %d bytes (quality=%d)", len(compressed), quality)
            return compressed

        except Exception:
            logger.warning("图片压缩失败，使用原始图片")
            return img_data

    # ---- 图片上传到闲鱼 CDN ----

    def upload_image_to_xianyu(self, image_url: str) -> str:
        """
        上传单张图片到闲鱼 CDN。
        使用 stream-upload API，只需 Cookie 鉴权，无需复杂签名。
        上传前自动压缩图片（缩放/转 JPEG/控制文件大小）。
        如果 Cookie 过期（302 重定向），自动尝试刷新后重试。
        """
        cookie_for_upload = self.cookie_str

        for attempt in range(2):  # 最多 2 次（Cookie 过期时重试一次）
            try:
                # 1. 读取图片数据
                if image_url.startswith('/uploads/') or image_url.startswith('uploads/'):
                    relative_path = image_url.lstrip('/')[len('uploads/'):]
                    local_path = resolve_upload_path(relative_path)
                    img_data, _ = load_safe_local_image(local_path)
                    logger.info("从本地读取待上传图片 (%d bytes)", len(img_data))
                else:
                    img_data = asyncio.run(download_public_image(image_url))

                # 2. 压缩图片
                img_data = self._compress_image(img_data)

                # 3. 构建 multipart 请求体
                boundary = "----WebKitFormBoundary" + hashlib.md5(str(time.time()).encode()).hexdigest()[:16]
                filename = f"publish_{int(time.time())}.jpg"
                body = (
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                    f"Content-Type: image/jpeg\r\n\r\n"
                ).encode("utf-8")
                body += img_data
                body += f"\r\n--{boundary}--\r\n".encode("utf-8")

                # 4. 上传到闲鱼 CDN
                upload_url = f"{self.IMAGE_UPLOAD_URL}?floderId=0&appkey={self.IMAGE_UPLOAD_APPKEY}"
                headers = {
                    "Cookie": cookie_for_upload,
                    "Content-Type": f"multipart/form-data; boundary={boundary}",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Referer": "https://www.goofish.com/",
                    "x-requested-with": "XMLHttpRequest",
                    "Accept": "application/json",
                }

                with requests.Session() as session:
                    session.trust_env = False
                    resp = session.post(
                        upload_url,
                        data=body,
                        headers=headers,
                        timeout=30,
                        allow_redirects=False,
                    )

                # 5. Cookie 过期检测（302 重定向）
                if resp.status_code in (302, 301):
                    if attempt == 0:
                        logger.warning("图片上传 Cookie 已过期，尝试刷新...")
                        cookie_for_upload = _refresh_m_h5_tk(self.cookie_str)
                        continue
                    else:
                        logger.error("图片上传 Cookie 刷新后仍然过期")
                        raise RuntimeError("图片上传登录状态已过期，请重新登录后重试")

                resp.raise_for_status()

                # 6. 解析响应获取 CDN URL
                result = resp.json()
                logger.info("图片上传响应已接收")

                # 兼容多种响应格式
                cdn_url = ""
                if isinstance(result, dict):
                    cdn_url = (
                        result.get("url", "")
                        or result.get("data", {}).get("url", "") if isinstance(result.get("data"), dict) else ""
                        or result.get("object", {}).get("url", "") if isinstance(result.get("object"), dict) else ""
                        or result.get("result", {}).get("url", "") if isinstance(result.get("result"), dict) else ""
                    )
                elif isinstance(result, list) and len(result) > 0 and isinstance(result[0], dict):
                    cdn_url = result[0].get("url", "")

                if cdn_url:
                    cdn_url = cdn_url.strip()
                    logger.info("图片上传到闲鱼 CDN 成功")
                    return cdn_url

                logger.warning("图片上传返回成功但未解析到 CDN URL")
                raise RuntimeError("图片上传结果无效，请稍后重试")

            except (UnsafePathError, UnsafeRemoteURLError, UploadValidationError):
                raise
            except requests.exceptions.Timeout:
                logger.warning("图片上传超时 (attempt %d/2)", attempt + 1)
                if attempt == 0:
                    time.sleep(1)
                    continue
                raise RuntimeError("图片上传超时，请稍后重试")
            except RuntimeError:
                raise
            except Exception as exc:
                logger.warning("图片上传异常 (attempt %d/2)", attempt + 1)
                if attempt == 0:
                    time.sleep(1)
                    continue
                raise RuntimeError("图片上传失败，请稍后重试") from exc

        raise RuntimeError("图片上传失败，请稍后重试")

    def upload_images_to_xianyu(self, image_urls: list[str]) -> list[str]:
        """批量上传图片到闲鱼 CDN"""
        xianyu_urls = []
        for url in image_urls:
            result_url = self.upload_image_to_xianyu(url)
            xianyu_urls.append(result_url)
            time.sleep(random.uniform(0.3, 0.8))
        return xianyu_urls

    # ---- Step 1: 类目推荐 ----

    def category_recommend(self, title: str, desc: str, image_urls: list[str]) -> dict:
        """
        调用类目推荐 API 获取推荐类目和标签。

        返回:
        {
            "catId": str,
            "catName": str,
            "channelCatId": str,
            "tbCatId": str,
            "cardList": list[dict],   # 推荐标签，可用于 itemLabelExtList
        }
        """
        try:
            data = {
                "itemInfo": {
                    "title": title,
                    "desc": desc,
                    "images": image_urls[:3],  # 最多传 3 张
                }
            }

            logger.info("调用类目推荐 API: titleLength=%d", len(title))
            result = self._call_api(self.CATEGORY_RECOMMEND_API, self.CATEGORY_RECOMMEND_VERSION, data)

            ret = result.get("ret", [])
            ret_msg = ret[0] if isinstance(ret, list) and ret else str(ret)

            if "SUCCESS" in ret_msg:
                data_body = result.get("data", {})

                # 解析推荐类目
                category_predict = data_body.get("categoryPredictResult", [])
                recommended_cat = {}
                if category_predict and isinstance(category_predict, list) and len(category_predict) > 0:
                    best = category_predict[0]
                    recommended_cat = {
                        "catId": str(best.get("catId", "")),
                        "catName": best.get("catName", ""),
                        "channelCatId": str(best.get("channelCatId", "")),
                        "tbCatId": str(best.get("tbCatId", "")),
                    }

                # 解析推荐标签
                card_list = data_body.get("cardList", [])
                if isinstance(card_list, dict):
                    # 有时 cardList 可能是 dict 而非 list
                    card_list = [card_list]

                logger.info(
                    "类目推荐成功: cat=%s, tags=%d",
                    recommended_cat.get("catName", ""),
                    len(card_list),
                )

                return {
                    "recommended": True,
                    "catId": recommended_cat.get("catId", ""),
                    "catName": recommended_cat.get("catName", ""),
                    "channelCatId": recommended_cat.get("channelCatId", ""),
                    "tbCatId": recommended_cat.get("tbCatId", ""),
                    "cardList": card_list,
                }

            logger.warning("类目推荐 API 返回非成功")
            return {"recommended": False}

        except Exception:
            logger.warning("类目推荐异常，将使用默认类目")
            return {"recommended": False}

    def _build_item_label_ext_list(self, card_list: list) -> list:
        """将类目推荐返回的 cardList 转换为 itemLabelExtList"""
        if not card_list:
            return []

        labels = []
        for card in card_list:
            if not isinstance(card, dict):
                continue
            label = {
                "channelCateName": card.get("channelCateName", ""),
                "channelCateId": str(card.get("channelCateId", "")),
                "tbCatId": str(card.get("tbCatId", "")),
                "labelType": card.get("labelType", "common"),
                "propertyId": str(card.get("propertyId", "-10000")),
                "propertyName": card.get("propertyName", "分类"),
                "text": card.get("text", ""),
                "properties": card.get("properties", ""),
                "from": card.get("from", "newPublishChoice"),
                "labelFrom": card.get("labelFrom", "newPublish"),
                "isUserClick": card.get("isUserClick", "1"),
            }
            labels.append(label)

        return labels

    # ---- Step 2: 构建发布数据 ----

    def _build_publish_data(self, item_data: dict, category_info: dict,
                            xianyu_image_urls: list[str]) -> dict:
        """构建完整的发布数据结构（参考 MTop 协议）"""

        # 基础字段
        # ★ 价格清洗：商品来源可能携带 ¥/￥/RMB/元 等货币符号或单位（例如店铺爬取保留原格式 ¥7），
        #   直接 float() 会抛 ValueError。这里统一提取数字部分。
        price_in_cent = _safe_price_to_cent(item_data.get("price", 0))
        quantity = int(item_data.get("quantity", 1))
        if quantity < 1:
            quantity = 1
        if quantity > 9999:
            quantity = 9999

        # ---- 图片信息 ----
        image_info_list = []
        for idx, url in enumerate(xianyu_image_urls):
            image_info_list.append({
                "url": url,
                "heightSize": 0,
                "widthSize": 0,
                "major": idx == 0,
                "type": 0,
                "status": "done",
                "isQrCode": False,
                "extraInfo": {"isH": "false", "isT": "false", "raw": "false"},
            })

        # ---- 标题与描述 ----
        item_text_dto = {
            "desc": item_data.get("desc", ""),
            "title": item_data.get("title", ""),
            "titleDescSeparate": False,
        }

        # ---- 类目 ----
        cat_id = category_info.get("catId") or self.DEFAULT_CAT_ID
        cat_name = category_info.get("catName") or self.DEFAULT_CAT_NAME
        channel_cat_id = category_info.get("channelCatId") or self.DEFAULT_CHANNEL_CAT_ID
        tb_cat_id = category_info.get("tbCatId") or self.DEFAULT_TB_CAT_ID

        item_cat_dto = {
            "catId": cat_id,
            "catName": cat_name,
            "channelCatId": channel_cat_id,
            "tbCatId": tb_cat_id,
        }

        # ---- 推荐标签 ----
        card_list = category_info.get("cardList", [])
        item_label_ext_list = self._build_item_label_ext_list(card_list)

        # ---- 价格 ----
        item_price_dto = {
            "priceInCent": str(price_in_cent),
        }
        orig_price = item_data.get("origPrice")
        if orig_price:
            try:
                orig_price_in_cent = _safe_price_to_cent(orig_price)
                item_price_dto["origPriceInCent"] = str(orig_price_in_cent)
            except (ValueError, TypeError):
                pass

        # ---- 服务协议（全部关闭） ----
        user_rights_protocols = [
            {"enable": False, "serviceCode": "FAST_DELIVERY_48_HOUR"},
            {"enable": False, "serviceCode": "FAST_DELIVERY_24_HOUR"},
            {"enable": False, "serviceCode": "VIRTUAL_NONCONFORMITY_FREE_REFUND_SERVICE"},
            {"enable": False, "serviceCode": "SKILL_PLAY_NO_MIND"},
        ]

        # ---- 运费 ----
        shipping_mode = item_data.get("shippingMode", "free")
        support_self_pick = item_data.get("supportSelfPick", False)

        if shipping_mode == "none":
            # 无需邮寄
            item_post_fee_dto = {
                "supportFreight": False,
                "templateId": "0",
            }
        elif shipping_mode == "fixed":
            # 一口价运费
            post_fee = item_data.get("postFee", 0)
            post_price_in_cent = str(_safe_price_to_cent(post_fee)) if post_fee else "0"
            item_post_fee_dto = {
                "canFreeShipping": False,
                "supportFreight": True,
                "onlyTakeSelf": support_self_pick,
                "templateId": "0",
                "postPriceInCent": post_price_in_cent,
            }
        else:
            # 包邮（默认）
            item_post_fee_dto = {
                "canFreeShipping": True,
                "supportFreight": True,
                "onlyTakeSelf": support_self_pick,
            }

        # ---- 地址 ----
        location = item_data.get("location", {})
        division_id = location.get("divisionId", "")
        try:
            division_id = int(division_id)
        except (ValueError, TypeError):
            division_id = 0

        item_addr_dto = {
            "prov": location.get("prov", ""),
            "city": location.get("city", ""),
            "area": location.get("area", ""),
            "divisionId": division_id,
            "gps": location.get("gps", ""),
            "poiId": location.get("poiId", ""),
            "poiName": location.get("poiName", ""),
        }

        # ---- SKU（至少一个空属性 SKU） ----
        item_sku_list = []
        sku_list = item_data.get("skuList", [])
        if sku_list:
            for sku in sku_list:
                property_list = []
                if sku.get("propertyKey") and sku.get("propertyValue"):
                    property_list.append({
                        "propertyText": sku.get("propertyKey", ""),
                        "valueText": sku.get("propertyValue", ""),
                    })
                if sku.get("secondPropertyKey") and sku.get("secondPropertyValue"):
                    property_list.append({
                        "propertyText": sku["secondPropertyKey"],
                        "valueText": sku["secondPropertyValue"],
                    })
                sku_entry = {
                    "priceInCent": str(_safe_price_to_cent(sku.get("price", 0))) if sku.get("price") else str(price_in_cent),
                    "quantity": str(int(sku.get("quantity", 1))),
                    "propertyList": property_list,
                }
                item_sku_list.append(sku_entry)
        else:
            # 无多规格：一个空属性 SKU
            item_sku_list.append({
                "priceInCent": str(price_in_cent),
                "quantity": str(quantity),
                "propertyList": [],
            })

        # ---- 商品属性 ----
        item_properties = item_data.get("itemProperties", [])

        # 组装最终数据
        publish_data = {
            "freebies": False,
            "itemTypeStr": "b",
            "quantity": str(quantity),
            "simpleItem": "true",
            "defaultPrice": False,
            "uniqueCode": str(int(time.time() * 1000)),
            "sourceId": "pcMainPublish",
            "bizcode": "pcMainPublish",
            "publishScene": "pcMainPublish",
            "imageInfoDOList": image_info_list,
            "itemTextDTO": item_text_dto,
            "itemCatDTO": item_cat_dto,
            "itemPriceDTO": item_price_dto,
            "itemPostFeeDTO": item_post_fee_dto,
            "itemAddrDTO": item_addr_dto,
            "userRightsProtocols": user_rights_protocols,
            "itemSkuList": item_sku_list,
        }

        # 有推荐标签时添加
        if item_label_ext_list:
            publish_data["itemLabelExtList"] = item_label_ext_list

        # 有商品属性时添加
        if item_properties:
            publish_data["itemProperties"] = item_properties

        return publish_data

    # ---- Step 3: 发布 ----

    def publish(self, item_data: dict) -> dict:
        """
        三步发布商品：

        1. 类目推荐：根据标题/描述/图片获取推荐类目和标签
        2. 图片上传：将图片上传到闲鱼 CDN
        3. 构建数据并调用发布 API
        """
        # ---- Step 0: 刷新 session，避免因 session 过期导致发布失败 ----
        logger.info("Step 0/3: 刷新 session / _m_h5_tk")
        refreshed_cookie = _refresh_m_h5_tk(self.cookie_str)
        if refreshed_cookie != self.cookie_str:
            self.cookie_str = refreshed_cookie
            self.token = extract_token_from_cookie(refreshed_cookie)
            logger.info("Session 已刷新")

        title = item_data.get("title", "")
        desc = item_data.get("desc", "")
        image_urls = item_data.get("imageUrls", [])

        if not image_urls:
            raise RuntimeError("至少需要一张商品图片")

        # ---- Step 1: 类目推荐 ----
        logger.info("Step 1/3: 类目推荐 titleLength=%d", len(title))
        recommend_result = self.category_recommend(title, desc, image_urls)

        if recommend_result.get("recommended"):
            category_info = recommend_result
            logger.info("类目推荐成功: %s (catId=%s)", category_info["catName"], category_info["catId"])
        else:
            # 回退到手动指定的类目
            logger.info("类目推荐失败，使用手动指定类目")
            user_cat = item_data.get("category", {})
            category_info = {
                "recommended": False,
                "catId": user_cat.get("catId") or self.DEFAULT_CAT_ID,
                "catName": user_cat.get("catName") or self.DEFAULT_CAT_NAME,
                "channelCatId": user_cat.get("channelCatId") or self.DEFAULT_CHANNEL_CAT_ID,
                "tbCatId": user_cat.get("tbCatId") or self.DEFAULT_TB_CAT_ID,
                "cardList": [],
            }

        # ---- Step 2: 图片上传到闲鱼 CDN ----
        logger.info("Step 2/3: 图片上传 - %d 张", len(image_urls))
        xianyu_image_urls = self.upload_images_to_xianyu(image_urls)

        # ---- Step 3: 构建发布数据并调用 API ----
        logger.info("Step 3/3: 构建发布数据并调用发布 API")
        publish_data = self._build_publish_data(item_data, category_info, xianyu_image_urls)

        logger.info("开始发布商品: imageCount=%d", len(image_urls))

        result = self._call_api(self.PUBLISH_API, self.PUBLISH_VERSION, publish_data)

        # ---- 解析响应 ----
        ret = result.get("ret", [])
        ret_msg = ret[0] if isinstance(ret, list) and ret else str(ret)

        if "SUCCESS" in ret_msg:
            data_body = result.get("data", {})
            if isinstance(data_body, dict):
                item_id = (
                    data_body.get("itemId", "")
                    or data_body.get("itemIdStr", "")
                    or data_body.get("idleItemId", "")
                )
                if isinstance(item_id, (int, float)):
                    item_id = str(int(item_id))

                if not item_id:
                    # 尝试从 data.data 中取
                    nested = data_body.get("data", {})
                    if isinstance(nested, dict):
                        item_id = str(nested.get("itemId", ""))

            logger.info("商品发布平台调用已确认")
            return {
                "success": True,
                "itemId": item_id,
                "itemUrl": f"https://www.goofish.com/item/{item_id}" if item_id else "",
                "message": "发布成功",
            }

        logger.warning("商品发布被平台拒绝 retMsg=%s", ret_msg)
        # 闲鱼返回的 ret 格式通常为 "FAIL_XXX::中文描述"，把真实原因带给上层
        # 避免前端只看到"平台暂未接受该商品"而不知具体违规点
        friendly = _explain_publish_rejection(ret_msg, result.get("data"))
        return {
            "success": False,
            "itemId": "",
            "errorCode": "PUBLISH_PROVIDER_REJECTED",
            "message": friendly,
            "retMsg": ret_msg,
        }
