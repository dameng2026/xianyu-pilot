"""
闲鱼 H5 API 调用与签名服务。

提供通用签名和API调用能力，参考文档的 XianyuApiCallUtils / XianyuSignUtils。
支持的API:
- mtop.taobao.idle.logistic.consign.dummy (确认发货/无需物流)
- mtop.taobao.idle.trade.merchant.sold.get (卖家订单列表)
- mtop.taobao.idle.merchant.refund.list (退款订单列表)
- mtop.taobao.idle.user.hasLogin (检查登录状态)
"""
import hashlib
import json
import logging
import time
from typing import Any, Optional
from urllib.parse import quote, urlencode

import pymysql
import requests

from ..core.config import settings

logger = logging.getLogger(__name__)

# 常量（与参考文档对齐）
APP_KEY = "34839810"
H5_API_BASE = "https://h5api.m.goofish.com/h5"


def _post_without_environment_proxy(url: str, **kwargs) -> requests.Response:
    """Send credential-bearing platform requests without ambient proxy leakage."""

    with requests.Session() as session:
        session.trust_env = False
        return session.post(url, **kwargs)
JSV = "2.7.2"
MAX_TOKEN_RETRY = 1


def _make_sign(token: str, t_ms: int, data_str: str) -> str:
    """生成Mtop MD5签名。
    
    参考文档 3.1 节:
        sign = MD5(token + "&" + timestamp + "&" + appKey + "&" + data)
    """
    raw = f"{token}&{t_ms}&{APP_KEY}&{data_str}"
    return hashlib.md5(raw.encode()).hexdigest()


def _extract_token_from_m_h5_tk(m_h5_tk: str) -> Optional[str]:
    """从 _m_h5_tk 值中提取token（第一个_之前的部分）。"""
    if not m_h5_tk:
        return None
    idx = m_h5_tk.find("_")
    return m_h5_tk[:idx] if idx != -1 else m_h5_tk


def _open_sync_db_connection():
    runtime_url = settings.mysql_runtime_url
    return pymysql.connect(
        host=str(runtime_url.host or ""),
        port=int(runtime_url.port or 3306),
        user=str(runtime_url.username or ""),
        password=str(runtime_url.password or ""),
        database=str(runtime_url.database or ""),
        charset="utf8mb4",
        autocommit=True,
        cursorclass=pymysql.cursors.DictCursor,
    )


def _decrypt_value(encrypted_value: str) -> str:
    """解密 Cookie 或 Token。"""
    from ..core.cookie_crypto import decrypt_cookie_if_needed
    return decrypt_cookie_if_needed(encrypted_value or "")


def _parse_cookie_str(cookie_str: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for part in str(cookie_str or "").split(";"):
        name, sep, value = part.strip().partition("=")
        if not sep or not name:
            continue
        cookies[name.strip()] = value.strip()
    return cookies


def _build_cookie_str(cookies: dict[str, str]) -> str:
    return "; ".join(
        f"{key}={value}"
        for key, value in cookies.items()
        if key and value not in (None, "")
    )


def _merge_cookie_str(original_cookie_str: str, updates: dict[str, str]) -> str:
    merged = _parse_cookie_str(original_cookie_str)
    changed = False
    for key, value in (updates or {}).items():
        key_text = str(key or "").strip()
        value_text = str(value or "").strip()
        if not key_text or not value_text:
            continue
        if merged.get(key_text) != value_text:
            changed = True
        merged[key_text] = value_text
    if not changed:
        return original_cookie_str
    return _build_cookie_str(merged) or original_cookie_str


def _extract_updated_cookie_str(response: requests.Response, original_cookie_str: str) -> str:
    try:
        cookie_updates = response.cookies.get_dict()
    except Exception:
        cookie_updates = {}
    return _merge_cookie_str(original_cookie_str, cookie_updates)


def _get_account_auth(account_id: int) -> Optional[dict]:
    """Read credentials synchronously inside the caller's worker thread."""
    try:
        connection = _open_sync_db_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                        SELECT auth.encrypted_cookie, auth.encrypted_token, a.external_uid
                        FROM xianyu_account_auth auth
                        JOIN xianyu_account a ON a.id = auth.account_id
                        WHERE auth.account_id = %s AND auth.deleted = 0
                        LIMIT 1
                    """,
                    (account_id,),
                )
                row = cursor.fetchone()
                return dict(row) if row else None
        finally:
            connection.close()
    except Exception:
        logger.error("获取账号认证信息失败: accountId=%d", account_id, exc_info=True)
        return None


def _persist_account_auth_cookies(account_id: int, cookie_str: str) -> None:
    """Persist refreshed credentials without creating a nested event loop."""
    if not account_id or not cookie_str:
        return
    try:
        from ..core.cookie_crypto import encrypt_cookie_for_storage

        cookie_map = _parse_cookie_str(cookie_str)
        encrypted_cookie = encrypt_cookie_for_storage(cookie_str)
        encrypted_token = encrypt_cookie_for_storage(cookie_map.get("_m_h5_tk")) if cookie_map.get("_m_h5_tk") else None

        connection = _open_sync_db_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                        UPDATE xianyu_account_auth
                        SET encrypted_cookie = %s,
                            encrypted_token = COALESCE(%s, encrypted_token)
                        WHERE account_id = %s
                          AND deleted = 0
                    """,
                    (encrypted_cookie, encrypted_token, account_id),
                )
        finally:
            connection.close()
    except Exception:
        logger.warning("更新账号 Cookie 失败: accountId=%d", account_id, exc_info=True)

def _extract_user_info_result(result: dict[str, Any]) -> dict[str, str]:
    data_payload = result.get("data", {}) if isinstance(result.get("data"), dict) else {}
    module_payload = data_payload.get("module", {}) if isinstance(data_payload.get("module"), dict) else {}
    avatar = ""
    nick = ""
    for candidate in (
        data_payload.get("userInfo"),
        module_payload.get("userInfo"),
        data_payload,
        module_payload,
    ):
        if not isinstance(candidate, dict):
            continue
        avatar = avatar or str(candidate.get("logo") or candidate.get("avatar") or "").strip()
        nick = nick or str(candidate.get("nick") or candidate.get("nickname") or "").strip()
        if avatar and nick:
            break
    return {"avatar": avatar, "nick": nick}


def call_xianyu_api(
    account_id: int,
    api_name: str,
    version: str = "1.0",
    data_map: Optional[dict] = None,
    timeout: int = 20,
) -> Optional[dict]:
    """调用闲鱼 H5 API（带签名）。
    
    参考文档 3.4 节的 XianyuApiCallUtils.callApiWithRetry。
    
    Args:
        account_id: 闲鱼账号ID
        api_name: API名称，如 "mtop.taobao.idle.logistic.consign.dummy"
        version: API版本，默认 "1.0"
        data_map: 请求参数 dict
        timeout: 超时秒数
        
    Returns:
        {"success": True, "data": {...}} 或 {"success": False, "error": "..."}
    """
    # Step 1: 获取账号认证信息
    auth = _get_account_auth(account_id)
    if not auth:
        return {"success": False, "error": "无法获取账号认证信息"}
    
    cookie_str = _decrypt_value(auth.get("encrypted_cookie") or "")
    m_h5_tk = _decrypt_value(auth.get("encrypted_token") or "")
    
    if not cookie_str:
        return {"success": False, "error": "Cookie为空"}
    
    # Step 2: 从Cookie提取 _m_h5_tk（优先使用Cookie中的，与WS Token逻辑一致）
    import re
    cookie_m_h5_tk = None
    match = re.search(r'_m_h5_tk=([^;]+)', cookie_str)
    if match:
        cookie_m_h5_tk = match.group(1)
    
    effective_m_h5_tk = cookie_m_h5_tk or m_h5_tk
    token = _extract_token_from_m_h5_tk(effective_m_h5_tk)
    
    if not token:
        return {"success": False, "error": "无法提取签名token"}
    
    # Step 3: 构建请求参数
    data_str = json.dumps(data_map or {}, separators=(",", ":"))
    t_ms = int(time.time() * 1000)
    sign = _make_sign(token, t_ms, data_str)
    
    # Step 4: 构建URL
    api_url = f"{H5_API_BASE}/{api_name}/{version}/"
    
    params = {
        "jsv": JSV,
        "appKey": APP_KEY,
        "t": str(t_ms),
        "sign": sign,
        "v": version,
        "type": "originaljson",
        "dataType": "json",
        "timeout": "20000",
        "api": api_name,
        "sessionOption": "AutoLoginOnly",
    }
    
    # 编码 data 参数
    form_data = {
        "data": data_str,
    }
    
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
        "Cookie": cookie_str,
        "Referer": "https://www.goofish.com/",
        "Origin": "https://www.goofish.com",
    }
    
    logger.info(
        "调用闲鱼 API accountId=%d api=%s payloadLength=%d",
        account_id,
        api_name,
        len(data_str),
    )
    
    # Step 5: 发送请求
    try:
        resp = _post_without_environment_proxy(
            api_url,
            params=params,
            data=form_data,
            headers=headers,
            timeout=timeout,
        )
        result = resp.json()
        
        ret = result.get("ret", [])
        ret_str = " ".join(ret) if isinstance(ret, list) else str(ret)
        
        if ret and ret[0].startswith("SUCCESS"):
            api_data = result.get("data", {})
            logger.info("API调用成功: accountId=%d api=%s", account_id, api_name)
            return {"success": True, "data": api_data}
        
        # 检查是否 Session 过期
        if "FAIL_SYS_SESSION_EXPIRED" in ret_str or "SESSION_EXPIRED" in ret_str:
            logger.warning("Session过期: accountId=%d api=%s", account_id, api_name)
            return {"success": False, "error": "SESSION_EXPIRED", "ret": ret}
        
        # 检查滑块验证
        if "FAIL_SYS_USER_VALIDATE" in ret_str:
            logger.warning("触发滑块验证: accountId=%d api=%s", account_id, api_name)
            return {"success": False, "error": "CAPTCHA_NEEDED", "ret": ret}
        
        logger.warning("API调用失败: accountId=%d api=%s", account_id, api_name)
        return {"success": False, "error": "PLATFORM_REJECTED"}
        
    except requests.exceptions.Timeout:
        logger.error("API调用超时: accountId=%d api=%s", account_id, api_name)
        return {"success": False, "error": "请求超时"}
    except Exception:
        logger.error("API调用异常: accountId=%d api=%s", account_id, api_name, exc_info=True)
        return {"success": False, "error": "PLATFORM_REQUEST_FAILED"}


def fetch_conversation_user_info(
    account_id: int,
    session_id: str,
    is_owner: bool = False,
    timeout: int = 20,
) -> Optional[dict]:
    """Fetch avatar and nickname for a conversation session."""
    auth = _get_account_auth(account_id)
    if not auth:
        return {"success": False, "error": "鏃犳硶鑾峰彇璐﹀彿璁よ瘉淇℃伅"}

    current_cookie_str = _decrypt_value(auth.get("encrypted_cookie") or "")
    current_m_h5_tk = _decrypt_value(auth.get("encrypted_token") or "")
    if not current_cookie_str:
        return {"success": False, "error": "Cookie涓虹┖"}

    api_name = "mtop.taobao.idlemessage.pc.user.query"
    data_str = json.dumps(
        {
            "type": 0,
            "sessionType": 1,
            "sessionId": str(session_id),
            "isOwner": bool(is_owner),
        },
        separators=(",", ":"),
    )

    for attempt in range(MAX_TOKEN_RETRY + 1):
        cookie_map = _parse_cookie_str(current_cookie_str)
        effective_m_h5_tk = cookie_map.get("_m_h5_tk") or current_m_h5_tk
        token = _extract_token_from_m_h5_tk(effective_m_h5_tk)
        if not token:
            return {"success": False, "error": "鏃犳硶鎻愬彇绛惧悕token"}

        t_ms = int(time.time() * 1000)
        sign = _make_sign(token, t_ms, data_str)
        log_id = f"{account_id}-{session_id}-{t_ms}"
        params = {
            "jsv": JSV,
            "appKey": APP_KEY,
            "t": str(t_ms),
            "sign": sign,
            "v": "4.0",
            "type": "originaljson",
            "accountSite": "xianyu",
            "dataType": "json",
            "timeout": "20000",
            "api": api_name,
            "sessionOption": "AutoLoginOnly",
            "spm_cnt": "a21ybx.im.0.0",
            "spm_pre": "a21ybx.home.sidebar.2.4c053da6MpVe1m",
            "log_id": log_id,
        }
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/146.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://www.goofish.com",
            "Referer": "https://www.goofish.com/",
            "Cookie": current_cookie_str.replace("\n", "").replace("\r", ""),
            "sec-ch-ua": '"Chromium";v="146", "Not-A.Brand";v="24", "Google Chrome";v="146"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-site",
        }

        try:
            response = _post_without_environment_proxy(
                f"{H5_API_BASE}/{api_name}/4.0/",
                params=params,
                data={"data": data_str},
                headers=headers,
                timeout=timeout,
            )
            result = response.json()
        except requests.exceptions.Timeout:
            return {"success": False, "error": "请求超时"}
        except Exception:
            logger.warning(
                "用户资料接口请求失败 accountId=%d attempt=%d",
                account_id,
                attempt + 1,
                exc_info=True,
            )
            return {"success": False, "error": "用户资料接口暂不可用"}

        merged_cookie_str = _extract_updated_cookie_str(response, current_cookie_str)
        if merged_cookie_str != current_cookie_str:
            current_cookie_str = merged_cookie_str
            current_m_h5_tk = _parse_cookie_str(current_cookie_str).get("_m_h5_tk") or current_m_h5_tk
            _persist_account_auth_cookies(account_id, current_cookie_str)

        ret = result.get("ret", [])
        ret_str = " ".join(ret) if isinstance(ret, list) else str(ret)
        if ret and "SUCCESS" in ret_str:
            return {"success": True, "data": _extract_user_info_result(result)}

        if ("TOKEN_EXOIRED" in ret_str or "TOKEN_EXPIRED" in ret_str) and attempt < MAX_TOKEN_RETRY:
            logger.info(
                "用户资料接口令牌过期，刷新后重试 accountId=%d attempt=%d",
                account_id,
                attempt + 1,
            )
            continue

        return {
            "success": False,
            "error": ret_str[:500] or "用户资料接口调用失败",
            "ret": ret,
        }

    return {"success": False, "error": "用户资料接口调用失败"}


def confirm_shipment(account_id: int, order_id: str) -> Optional[dict]:
    """确认发货（无需物流）。

    调用 API: mtop.taobao.idle.logistic.consign.dummy
    参考文档第 10 节。

    使用 _post_mtop_with_token_retry 自动处理令牌过期（FAIL_SYS_TOKEN_EXOIRED），
    避免因令牌过期导致平台确认发货失败。
    """
    auth = _get_account_auth(account_id)
    if not auth:
        return {"success": False, "error": "无法获取账号认证信息"}

    cookie_str = _decrypt_value(auth.get("encrypted_cookie") or "")
    if not cookie_str:
        return {"success": False, "error": "Cookie为空"}

    data_str = json.dumps(
        {
            "orderId": order_id,
            "tradeText": "",
            "picList": [],
            "newUnconsign": True,
        },
        separators=(",", ":"),
    )

    result = _post_mtop_with_token_retry(
        account_id, cookie_str, "mtop.taobao.idle.logistic.consign.dummy", data_str
    )
    if result.get("success"):
        return {"success": True, "data": result.get("data", {})}

    # 令牌过期重试后仍然失败时，返回带 ret 的错误信息以便上层精确判断重试安全性
    return {
        "success": False,
        "error": result.get("error") or "PLATFORM_REJECTED",
        "ret": result.get("ret"),
    }


def freeshipping_order(account_id: int, order_id: str, item_id: str, buyer_id: str) -> Optional[dict]:
    """免拼发货（小刀订单专用）。

    调用 API: mtop.idle.groupon.activity.seller.freeshipping
    小刀（砍价）订单需通过免拼接口发货，而非普通的确认发货接口。
    使用 _post_mtop_with_token_retry 自动处理令牌过期。
    """
    auth = _get_account_auth(account_id)
    if not auth:
        return {"success": False, "error": "无法获取账号认证信息"}

    cookie_str = _decrypt_value(auth.get("encrypted_cookie") or "")
    if not cookie_str:
        return {"success": False, "error": "Cookie为空"}

    # itemId 和 buyerId 需为数值类型，bizOrderId 为字符串
    try:
        item_id_val: int = int(item_id) if str(item_id).strip() else 0
    except (ValueError, TypeError):
        item_id_val = 0
    try:
        buyer_id_val: int = int(buyer_id) if str(buyer_id).strip() else 0
    except (ValueError, TypeError):
        buyer_id_val = 0

    data_str = json.dumps(
        {
            "bizOrderId": str(order_id),
            "itemId": item_id_val,
            "buyerId": buyer_id_val,
        },
        separators=(",", ":"),
    )

    result = _post_mtop_with_token_retry(
        account_id, cookie_str, "mtop.idle.groupon.activity.seller.freeshipping", data_str
    )
    if result.get("success"):
        return {"success": True, "data": result.get("data", {})}

    # 已发货视为成功
    ret = result.get("ret")
    ret_text = " ".join(str(r) for r in ret) if isinstance(ret, (list, tuple)) else str(ret or "")
    if "ORDER_ALREADY_DELIVERY" in ret_text or "已发货成功" in ret_text:
        return {"success": True, "data": {}}

    return {
        "success": False,
        "error": result.get("error") or "PLATFORM_REJECTED",
        "ret": ret,
    }


def check_login_status(account_id: int) -> Optional[dict]:
    """检查Cookie登录状态。
    
    调用 API: mtop.taobao.idle.user.hasLogin
    """
    params = {}
    return call_xianyu_api(
        account_id=account_id,
        api_name="mtop.taobao.idle.user.hasLogin",
        version="1.0",
        data_map=params,
    )


def sync_sold_orders(
    account_id: int,
    unb: str,
    page_number: int = 1,
    page_size: int = 20,
    last_end_row: str = "0",
) -> Optional[dict]:
    """同步卖家订单列表。
    
    调用 API: mtop.taobao.idle.trade.merchant.sold.get
    参考文档第 11 节。
    """
    params = {
        "needGroupInfo": True,
        "pageNumber": page_number,
        "pageSize": page_size,
        "lastEndRow": last_end_row,
        "userId": unb,
    }
    return call_xianyu_api(
        account_id=account_id,
        api_name="mtop.taobao.idle.trade.merchant.sold.get",
        version="1.0",
        data_map=params,
    )


def _is_token_expired_error(ret_list) -> bool:
    """判断API返回是否为令牌过期/令牌为空错误。

    令牌过期（FAIL_SYS_TOKEN_EXOIRED）与令牌为空（FAIL_SYS_TOKEN_EMPTY）
    在闲鱼接口中均会在响应头返回新的 Set-Cookie（含新的 _m_h5_tk），
    处理方式一致：提取新 Cookie 后刷新并重试，不需要重新登录。
    """
    if not ret_list:
        return False
    ret_str = str(ret_list)
    return (
        "FAIL_SYS_TOKEN_EXOIRED" in ret_str
        or "FAIL_SYS_TOKEN_EMPTY" in ret_str
        or "令牌过期" in ret_str
        or "令牌为空" in ret_str
    )


def _build_mtop_request_headers(cookie_str: str) -> dict:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/138.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
        "Cookie": cookie_str,
        "Referer": "https://seller.goofish.com/",
        "idle_site_biz_code": "COMMONPRO",
    }


def _build_mtop_query_params(api_name: str, sign: str, t_ms: int) -> dict:
    return {
        "jsv": JSV,
        "appKey": APP_KEY,
        "t": str(t_ms),
        "sign": sign,
        "v": "1.0",
        "type": "json",
        "accountSite": "xianyu",
        "dataType": "json",
        "timeout": "20000",
        "api": api_name,
        "valueType": "string",
        "sessionOption": "AutoLoginOnly",
    }


def _do_post_mtop_api(
    account_id: int,
    cookie_str: str,
    api_name: str,
    data_str: str,
    timeout: int = 20,
) -> dict:
    """执行一次 MTOP POST 请求，返回包含 success/ret/updatedCookieStr 的字典。

    令牌过期时会从响应 Set-Cookie 提取新 Cookie 并放入 updatedCookieStr 字段，
    由调用方决定是否持久化并重试。
    """
    import re

    cookie_m_h5_tk = None
    match = re.search(r"_m_h5_tk=([^;]+)", cookie_str)
    if match:
        cookie_m_h5_tk = match.group(1)
    token = _extract_token_from_m_h5_tk(cookie_m_h5_tk)
    if not token:
        return {"success": False, "error": "无法提取签名token"}

    t_ms = int(time.time() * 1000)
    sign = _make_sign(token, t_ms, data_str)
    api_url = f"{H5_API_BASE}/{api_name}/1.0/"
    params = _build_mtop_query_params(api_name, sign, t_ms)
    headers = _build_mtop_request_headers(cookie_str)

    try:
        response = _post_without_environment_proxy(
            api_url,
            params=params,
            data={"data": data_str},
            headers=headers,
            timeout=timeout,
        )
        result = response.json()
    except requests.exceptions.Timeout:
        return {"success": False, "error": "请求超时"}
    except Exception:
        logger.warning("订单接口请求失败 api=%s", api_name, exc_info=True)
        return {"success": False, "error": "PLATFORM_REQUEST_FAILED"}

    updated_cookie_str = _extract_updated_cookie_str(response, cookie_str)
    ret = result.get("ret", [])
    ret_str = " ".join(ret) if isinstance(ret, list) else str(ret)
    if not ret or "SUCCESS" not in ret_str:
        return {
            "success": False,
            "error": ret_str[:500] or "订单接口调用失败",
            "ret": ret,
            "data": result.get("data"),
            "updatedCookieStr": updated_cookie_str,
        }

    return {"success": True, "data": result.get("data") or {}, "updatedCookieStr": updated_cookie_str}


def _post_mtop_with_token_retry(
    account_id: int,
    cookie_str: str,
    api_name: str,
    data_str: str,
    timeout: int = 20,
) -> dict:
    """调用 MTOP 接口，令牌过期时自动刷新 Cookie 并重试一次。

    返回的字典始终包含 success 字段；成功时包含 data，失败时包含 error/ret。
    """
    result = _do_post_mtop_api(account_id, cookie_str, api_name, data_str, timeout)
    if result.get("success"):
        return result

    # 令牌过期 → 刷新 Cookie 后重试一次
    if _is_token_expired_error(result.get("ret")):
        new_cookie_str = result.get("updatedCookieStr")
        if new_cookie_str and new_cookie_str != cookie_str:
            logger.info("MTOP 接口令牌过期，刷新 Cookie 后重试: accountId=%s api=%s", account_id, api_name)
            _persist_account_auth_cookies(account_id, new_cookie_str)
            return _do_post_mtop_api(account_id, new_cookie_str, api_name, data_str, timeout)

    return result


def fetch_sold_orders_page(
    account_id: int,
    page_number: int = 1,
    page_size: int = 30,
    query_code: str = "ALL",
    timeout: int = 20,
) -> Optional[dict]:
    """Fetch one seller-order page using the seller console request shape."""
    auth = _get_account_auth(account_id)
    if not auth:
        return {"success": False, "error": "无法获取账号认证信息"}

    cookie_str = _decrypt_value(auth.get("encrypted_cookie") or "")
    if not cookie_str:
        return {"success": False, "error": "Cookie为空"}

    data_str = json.dumps(
        {
            "pageNumber": page_number,
            "rowsPerPage": page_size,
            "orderIds": "",
            "queryCode": query_code,
            "orderSearchParam": "{}",
        },
        separators=(",", ":"),
    )

    result = _post_mtop_with_token_retry(
        account_id, cookie_str, "mtop.taobao.idle.trade.merchant.sold.get", data_str, timeout
    )
    if not result.get("success"):
        failure_text = str(result.get("error") or "").upper()
        raw_ret = result.get("ret")
        ret_text = (
            " ".join(str(item) for item in raw_ret)
            if isinstance(raw_ret, (list, tuple))
            else str(raw_ret or "")
        ).upper()
        combined = f"{failure_text} {ret_text}"
        if any(marker in combined for marker in ("SESSION_EXPIRED", "TOKEN_EXPIRED", "ILLEGAL_ACCESS_TOKEN")):
            error_code = "COOKIE_EXPIRED"
            public_error = "账号登录已失效，请重新登录后同步订单"
        elif any(marker in combined for marker in ("USER_VALIDATE", "RGV587", "CAPTCHA")):
            error_code = "CAPTCHA_REQUIRED"
            public_error = "账号需要完成安全验证后才能同步订单"
        elif "超时" in failure_text or "TIMEOUT" in combined:
            error_code = "PLATFORM_TIMEOUT"
            public_error = "订单平台接口超时，请稍后重试"
        else:
            error_code = "PLATFORM_UNAVAILABLE"
            public_error = "订单平台接口暂不可用，请稍后重试"
        return {
            "success": False,
            "error": public_error,
            "errorCode": error_code,
        }

    module = result.get("data", {}).get("module", {})
    return {
        "success": True,
        "data": {
            "items": module.get("items", []),
            "nextPage": str(module.get("nextPage", "")).lower() == "true",
            "totalCount": int(module.get("totalCount") or 0),
        },
    }


def fetch_refund_orders_page(
    account_id: int,
    dispute_status: str,
    page_number: int = 1,
    page_size: int = 20,
    timeout: int = 20,
) -> Optional[dict]:
    """拉取闲鱼退款订单列表的单页数据。

    通过 mtop.taobao.idle.merchant.refund.list 接口按 disputeStatus 拉取退款订单，
    用于补充卖家已售订单中缺失的退款订单数据。

    Args:
        account_id: 账号ID
        dispute_status: 退款查询状态（1/2/3=退款中，5=退款成功）
        page_number: 页码，默认1
        page_size: 单页大小，默认20（退款接口与订单列表不同）
        timeout: 请求超时秒数
    """
    auth = _get_account_auth(account_id)
    if not auth:
        return {"success": False, "error": "无法获取账号认证信息"}

    cookie_str = _decrypt_value(auth.get("encrypted_cookie") or "")
    if not cookie_str:
        return {"success": False, "error": "Cookie为空"}

    data_str = json.dumps(
        {
            "pageNumber": page_number,
            "rowsPerPage": page_size,
            "queryType": "refund",
            "refundSearchParam": {
                "disputeStatus": dispute_status,
                "queryCode": "ALL",
            },
        },
        separators=(",", ":"),
    )

    result = _post_mtop_with_token_retry(
        account_id, cookie_str, "mtop.taobao.idle.merchant.refund.list", data_str, timeout
    )
    if not result.get("success"):
        return {
            "success": False,
            "error": result.get("error") or "退款订单接口调用失败",
            "ret": result.get("ret"),
        }

    # 退款列表响应结构：data.data.items（与订单列表的 data.module.items 不同）
    data_node = result.get("data", {}).get("data", {})
    return {
        "success": True,
        "data": {
            "items": data_node.get("items", []),
        },
    }


def _legacy_fetch_conversation_user_info(
    account_id: int,
    session_id: str,
    is_owner: bool = False,
    timeout: int = 20,
) -> Optional[dict]:
    """Fetch avatar and nickname for a conversation session."""
    auth = _get_account_auth(account_id)
    if not auth:
        return {"success": False, "error": "无法获取账号认证信息"}

    cookie_str = _decrypt_value(auth.get("encrypted_cookie") or "")
    m_h5_tk = _decrypt_value(auth.get("encrypted_token") or "")
    if not cookie_str:
        return {"success": False, "error": "Cookie为空"}

    import re

    cookie_m_h5_tk = None
    match = re.search(r"_m_h5_tk=([^;]+)", cookie_str)
    if match:
        cookie_m_h5_tk = match.group(1)

    effective_m_h5_tk = cookie_m_h5_tk or m_h5_tk
    token = _extract_token_from_m_h5_tk(effective_m_h5_tk)
    if not token:
        return {"success": False, "error": "无法提取签名token"}

    api_name = "mtop.taobao.idlemessage.pc.user.query"
    data_str = json.dumps(
        {
            "type": 0,
            "sessionType": 1,
            "sessionId": str(session_id),
            "isOwner": bool(is_owner),
        },
        separators=(",", ":"),
    )
    t_ms = int(time.time() * 1000)
    sign = _make_sign(token, t_ms, data_str)
    params = {
        "jsv": JSV,
        "appKey": APP_KEY,
        "t": str(t_ms),
        "sign": sign,
        "v": "4.0",
        "type": "originaljson",
        "accountSite": "xianyu",
        "dataType": "json",
        "timeout": "20000",
        "api": api_name,
        "sessionOption": "AutoLoginOnly",
    }
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/146.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
        "Cookie": cookie_str,
        "Referer": "https://www.goofish.com/",
    }

    try:
        response = _post_without_environment_proxy(
            f"{H5_API_BASE}/{api_name}/4.0/",
            params=params,
            data={"data": data_str},
            headers=headers,
            timeout=timeout,
        )
        result = response.json()
    except requests.exceptions.Timeout:
        return {"success": False, "error": "请求超时"}
    except Exception:
        logger.warning("用户信息接口请求失败 api=%s", api_name, exc_info=True)
        return {"success": False, "error": "PLATFORM_REQUEST_FAILED"}

    ret = result.get("ret", [])
    ret_str = " ".join(ret) if isinstance(ret, list) else str(ret)
    if not ret or "SUCCESS" not in ret_str:
        return {"success": False, "error": ret_str[:500] or "用户信息接口调用失败", "ret": ret}

    data_payload = result.get("data", {}) if isinstance(result.get("data"), dict) else {}
    module_payload = data_payload.get("module", {}) if isinstance(data_payload.get("module"), dict) else {}
    user_info = data_payload.get("userInfo", {})
    if not isinstance(user_info, dict) or (not user_info.get("logo") and not user_info.get("nick")):
        user_info = module_payload.get("userInfo", {})
    if not isinstance(user_info, dict):
        user_info = {}
    return {
        "success": True,
        "data": {
            "avatar": user_info.get("logo") or data_payload.get("logo") or "",
            "nick": user_info.get("nick") or data_payload.get("nick") or "",
        },
    }

# ==================== 确认发货（商业版同步） ====================


def _normalize_numeric_id(value: Any) -> Optional[int]:
    """将可能带 @goofish 后缀或为字符串的 ID 整饬为整数，失败返回 None。"""
    if value is None:
        return None
    text_value = str(value).strip()
    if "@" in text_value:
        text_value = text_value.split("@", 1)[0]
    try:
        return int(text_value)
    except (ValueError, TypeError):
        return None


def confirm_freeshipping(
    account_id: int,
    order_id: str,
    item_id: Any,
    buyer_id: Any,
) -> Optional[dict]:
    """调用闲鱼 MTOP 免拼发货接口（小刀订单专用，商业版同步）。

    API: mtop.idle.groupon.activity.seller.freeshipping
    请求体: {"bizOrderId":"<order_id>", "itemId":<item_id>, "buyerId":<buyer_id>}

    与原版 freeshipping_order 相比的增强：
    - 使用 _normalize_numeric_id 处理 @goofish 后缀
    - 返回更详细的错误信息（account_id / order_id / ship_method / idempotent 标记）
    - 日志更详细

    已发货（ORDER_ALREADY_DELIVERY / 已发货成功）视为幂等成功。
    """
    if not order_id:
        return {"success": False, "error": "MISSING_ORDER_ID", "message": "订单号为空"}

    numeric_item_id = _normalize_numeric_id(item_id)
    numeric_buyer_id = _normalize_numeric_id(buyer_id)
    if numeric_item_id is None or numeric_buyer_id is None:
        return {
            "success": False,
            "error": "INVALID_ITEM_OR_BUYER_ID",
            "message": "商品ID或买家ID无法解析为整数",
            "account_id": account_id,
            "order_id": order_id,
        }

    auth = _get_account_auth(account_id)
    if not auth:
        return {
            "success": False,
            "error": "ACCOUNT_AUTH_NOT_FOUND",
            "message": f"未找到账号 {account_id} 的认证信息",
        }

    cookie_str = _decrypt_value(auth.get("encrypted_cookie"))
    if not cookie_str:
        return {
            "success": False,
            "error": "COOKIE_EMPTY",
            "message": f"账号 {account_id} 的 Cookie 为空",
        }

    api_name = "mtop.idle.groupon.activity.seller.freeshipping"
    # itemId / buyerId 在闲鱼接口中为整数类型（不带引号），bizOrderId 为字符串
    data_str = json.dumps(
        {
            "bizOrderId": str(order_id),
            "itemId": numeric_item_id,
            "buyerId": numeric_buyer_id,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )

    result = _post_mtop_with_token_retry(account_id, cookie_str, api_name, data_str, timeout=20)

    if result.get("success"):
        logger.info(
            "免拼发货成功: accountId=%d orderId=%s itemId=%s buyerId=%s",
            account_id, order_id, numeric_item_id, numeric_buyer_id,
        )
        return {
            "success": True,
            "account_id": account_id,
            "order_id": order_id,
            "ship_method": "freeshipping",
        }

    # 已发货视为成功（幂等）
    ret = result.get("ret") or []
    ret_str = " ".join(ret) if isinstance(ret, list) else str(ret)
    if "ORDER_ALREADY_DELIVERY" in ret_str or "已发货" in ret_str:
        logger.info(
            "订单已发货（免拼幂等成功）: accountId=%d orderId=%s",
            account_id, order_id,
        )
        return {
            "success": True,
            "account_id": account_id,
            "order_id": order_id,
            "ship_method": "freeshipping",
            "idempotent": True,
        }

    logger.warning(
        "免拼发货失败: accountId=%d orderId=%s error=%s ret=%s",
        account_id, order_id, result.get("error", ""), ret_str[:200],
    )
    return {
        "success": False,
        "error": result.get("error") or "FREESHIPPING_FAILED",
        "message": result.get("error") or "免拼发货失败",
        "ret": ret,
        "account_id": account_id,
        "order_id": order_id,
    }


def confirm_order_shipment(
    account_id: int,
    order_id: str,
    is_bargain: bool = False,
    item_id: Any = None,
    buyer_id: Any = None,
) -> Optional[dict]:
    """统一确认发货调度：小刀订单走免拼发货，普通订单走虚拟发货（商业版同步）。

    Args:
        account_id: 闲鱼账号ID
        order_id: 闲鱼订单ID（external_order_id）
        is_bargain: 是否小刀订单。True 时必须提供 item_id 和 buyer_id
        item_id: 商品ID（小刀订单免拼发货必填）
        buyer_id: 买家ID（小刀订单免拼发货必填）

    Returns:
        {"success": True, ...} 或 {"success": False, "error": "...", ...}
    """
    if is_bargain:
        return confirm_freeshipping(account_id, order_id, item_id, buyer_id)
    return confirm_shipment(account_id, order_id)

