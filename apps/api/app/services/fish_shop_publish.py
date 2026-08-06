"""
鱼小铺多规格商品发布与编辑服务。

接口（与现有 mtop.idle.pc.idleitem.publish 不同，使用 backend 段）：
- 发布：POST https://h5api.m.goofish.com/h5/mtop.idle.pc.backend.idleitem.publish/1.0/
- 编辑：POST https://h5api.m.goofish.com/h5/mtop.idle.pc.backend.idleitem.edit/1.0/
- 编辑详情：POST https://h5api.m.goofish.com/h5/mtop.idle.pc.backend.idleitem.editdetail/1.0/

仅用于鱼小铺账号 + 多规格商品，普通账号或未开启多规格的鱼小铺账号继续走原有 XianyuItemOperator.publish。

核心约束：
1. 发布/编辑双层 JSON 序列化：内部对象 → JSON 字符串 → 包到 {inputJson: "..."} → 再序列化 → 最终 data 字符串
2. 编辑详情单层 JSON 序列化：data = '{"itemId":"<item_id>"}'，不包裹 inputJson
3. 签名使用最终 data 字符串
4. 提交时 data = 最终 data 字符串（与签名字符串完全相同）
5. 不复用抓包中的固定 t/sign/uniqueCode/itemId
6. 不打印 Cookie/token/sign
"""
from __future__ import annotations

import hashlib
import itertools
import json
import logging
import time
import uuid
from typing import Any, Iterable, List, Optional

from .xianyu_goods_sync import (
    APP_KEY,
    H5_API_BASE,
    _refresh_m_h5_tk,
    _safe_price_to_cent,
    extract_token_from_cookie,
)

logger = logging.getLogger(__name__)

# 鱼小铺多规格发布/编辑 API（注意带 backend 段，与现有 publish API 不同）
FISH_SHOP_PUBLISH_API = "mtop.idle.pc.backend.idleitem.publish"
FISH_SHOP_PUBLISH_VERSION = "1.0"
FISH_SHOP_EDIT_API = "mtop.idle.pc.backend.idleitem.edit"
FISH_SHOP_EDIT_VERSION = "1.0"

# 闲鱼商品编辑详情 API（用于编辑回显完整数据）
# 注意：API 名为 editdetail，不是 detail。
# 该接口 data 字段为单层 JSON {"itemId":"..."}，不是发布/编辑的双层 inputJson 结构。
FISH_SHOP_EDIT_DETAIL_API = "mtop.idle.pc.backend.idleitem.editdetail"
FISH_SHOP_EDIT_DETAIL_VERSION = "1.0"

# 兼容旧引用（保留旧常量名，但指向正确 API）
FISH_SHOP_DETAIL_API = FISH_SHOP_EDIT_DETAIL_API
FISH_SHOP_DETAIL_VERSION = FISH_SHOP_EDIT_DETAIL_VERSION

MAX_PROPERTY_TYPES = 2  # 一个商品最多 2 个规格类型


# ============================================================
# 商品编辑详情接口（mtop.idle.pc.backend.idleitem.editdetail）
# ============================================================
#
# 本接口用于获取鱼小铺商品的完整编辑回显数据：
# - 标题与正文（itemTextDTO.title / itemTextDTO.desc）
# - 完整图片列表（imageInfoDOList，含 major 主图标识）
# - 价格与库存（itemPriceDTO.priceInCent / quantity）
# - 分类（itemCatDTO + itemLabelExtList）
# - 地址（itemAddrDTO）
# - 运费（itemPostFeeDTO）
# - 服务协议（userRightsProtocols）
# - 多规格（itemProperties / itemSkuList / propertyImageList）— 简单商品无此字段
#
# 与发布/编辑接口不同：
# - data 字段为单层 JSON {"itemId":"..."}，**不**包裹 inputJson
# - itemId 必须按字符串处理，避免整数精度损失
# - 签名使用的 data 字符串与提交的 data 字符串必须完全一致
#
# 安全：
# - 不打印 Cookie/token/sign
# - 异常消息只暴露脱敏信息（API名、ret、traceId、itemId）
# ============================================================


# 详情请求短期缓存：键=(account_id, item_id)，值=(timestamp, result)
# 缓存窗口 30 秒，避免重复点击编辑或多个组件同时加载同一详情时并发请求
_EDIT_DETAIL_CACHE_TTL_SECONDS = 30
_edit_detail_cache: dict = {}
# 进行中的请求：键=(account_id, item_id)，值=threading.Event
# 用于跨线程去重，避免同一时刻发送多个相同请求
_edit_detail_inflight: dict = {}


def _safe_str_to_bool(value: Any) -> bool:
    """将字符串/任意类型安全转为布尔值。

    实测响应中 major/canFreeShipping/supportFreight 等字段为字符串 "true"/"false"，
    直接 bool("false") 会错误返回 True，必须按字符串内容判断。
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in ("true", "1", "yes")


def _safe_int(value: Any, *, default: int = 0, minimum: Optional[int] = None) -> int:
    """安全转换为整数。

    - 字符串数字："100" → 100
    - 浮点数：1.5 → 1（截断）
    - None/空字符串：返回 default
    - 转换失败：返回 default
    - minimum 不为 None 时，结果小于 minimum 则返回 minimum
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, int):
        result = value
    elif isinstance(value, float):
        result = int(value)
    else:
        try:
            s = str(value).strip()
            if not s:
                return default
            # 处理 "1.5" 这类小数字符串
            if "." in s:
                result = int(float(s))
            else:
                result = int(s)
        except (ValueError, TypeError):
            return default
    if minimum is not None and result < minimum:
        return minimum
    return result


def _safe_str(value: Any) -> str:
    """安全转换为字符串。None → ""，其他 → str(value)。"""
    if value is None:
        return ""
    return str(value)


def generate_unique_code() -> str:
    """生成唯一码。不复用抓包值。"""
    return uuid.uuid4().hex


def build_property_key(property_list: List[dict]) -> str:
    """
    构建规格组合的规范化键，用于响应乱序匹配。
    格式：prop1=val1|prop2=val2，按 propertyText 排序，valueText 原样保留。
    """
    if not property_list:
        return ""
    sorted_list = sorted(property_list, key=lambda p: str(p.get("propertyText", "")))
    return "|".join(
        f"{p.get('propertyText', '')}={p.get('valueText', '')}" for p in sorted_list
    )


def cartesian_sku_combinations(property_groups: List[dict]) -> List[List[dict]]:
    """
    根据规格类型生成 SKU 笛卡尔积。
    每个规格组形如：
        {
          "propertyName": "颜色",
          "supportImage": True,
          "propertyValues": [{"propertyValue": "红色"}, {"propertyValue": "蓝色"}]
        }
    返回每个 SKU 的 propertyList（list of {propertyText, valueText}）。

    示例：
      颜色 [红色, 蓝色] × 尺码 [S, M, L] → 6 个 SKU
    """
    if not property_groups:
        return []

    # 过滤掉空规格值和空规格名
    valid_groups: List[dict] = []
    for g in property_groups:
        name = (g.get("propertyName") or "").strip()
        if not name:
            continue
        values = []
        for v in g.get("propertyValues", []) or []:
            text = (v.get("propertyValue") or "").strip() if isinstance(v, dict) else ""
            if text:
                values.append(text)
        if not values:
            continue
        valid_groups.append({"propertyName": name, "values": values})

    if not valid_groups:
        return []

    # 笛卡尔积
    pools: List[List[tuple]] = []
    for g in valid_groups:
        pools.append([(g["propertyName"], v) for v in g["values"]])

    combinations: List[List[dict]] = []
    for combo in itertools.product(*pools):
        combinations.append([
            {"propertyText": name, "valueText": val} for (name, val) in combo
        ])
    return combinations


def validate_multi_spec_payload(item_data: dict) -> Optional[str]:
    """
    校验多规格发布/编辑负载。返回错误消息，None 表示通过。

    检查项：
    - 最多 2 个规格类型
    - 规格名不能为空、不能重复
    - 每个规格类型至少一个有效规格值
    - 同一规格类型下规格值不重复
    - 最多一个 supportImage=true
    - 每个 SKU 必须有合法价格和库存
    - 金额可被安全转换为分
    - 库存非负整数
    """
    property_groups = item_data.get("itemProperties", []) or []
    if len(property_groups) > MAX_PROPERTY_TYPES:
        return f"最多只能添加 {MAX_PROPERTY_TYPES} 个规格类型"

    # 规格名校验
    names = []
    support_image_count = 0
    for g in property_groups:
        name = (g.get("propertyName") or "").strip()
        if not name:
            return "规格名称不能为空"
        names.append(name)
        if g.get("supportImage"):
            support_image_count += 1
        # 规格值校验
        values = []
        for v in g.get("propertyValues", []) or []:
            text = (v.get("propertyValue") or "").strip() if isinstance(v, dict) else ""
            if text:
                values.append(text)
        if not values:
            return f"规格「{name}」至少需要一个有效规格值"
        if len(set(values)) != len(values):
            return f"规格「{name}」下存在重复的规格值"

    if len(set(names)) != len(names):
        return "规格名称不能重复"

    if support_image_count > 1:
        return "最多只能有一个规格类型支持图片"

    # SKU 校验
    sku_list = item_data.get("itemSkuList", []) or []
    if not sku_list:
        return "多规格商品必须包含至少一个 SKU"

    for idx, sku in enumerate(sku_list):
        price_raw = sku.get("price")
        if price_raw is None or price_raw == "":
            return f"第 {idx + 1} 个 SKU 未设置价格"
        try:
            price_cent = _safe_price_to_cent(price_raw)
        except (ValueError, TypeError):
            return f"第 {idx + 1} 个 SKU 价格格式不正确"
        if price_cent < 0:
            return f"第 {idx + 1} 个 SKU 价格不能为负数"

        qty_raw = sku.get("quantity")
        # 拒绝小数（float 但不是整数）
        if isinstance(qty_raw, float) and not qty_raw.is_integer():
            return f"第 {idx + 1} 个 SKU 库存必须是整数"
        # 拒绝小数字符串（如 "1.5"）
        if isinstance(qty_raw, str) and "." in qty_raw:
            try:
                if float(qty_raw) != int(float(qty_raw)):
                    return f"第 {idx + 1} 个 SKU 库存必须是整数"
            except (ValueError, TypeError):
                return f"第 {idx + 1} 个 SKU 库存必须是整数"
        try:
            qty = int(qty_raw)
        except (ValueError, TypeError):
            return f"第 {idx + 1} 个 SKU 库存必须是整数"
        if qty < 0:
            return f"第 {idx + 1} 个 SKU 库存不能为负数"

    return None


def build_property_image_list(property_groups: List[dict]) -> List[dict]:
    """
    根据 supportImage=true 的规格类型构造 propertyImageList。
    只引用 supportImage=true 的规格类型的规格值。
    通过 propertyText + valueText 关联。
    """
    result: List[dict] = []
    for g in property_groups or []:
        if not g.get("supportImage"):
            continue
        prop_text = (g.get("propertyName") or "").strip()
        for v in g.get("propertyValues", []) or []:
            if not isinstance(v, dict):
                continue
            val_text = (v.get("propertyValue") or "").strip()
            img = (v.get("propertyValueImg") or "").strip()
            if not val_text or not img:
                continue
            result.append({
                "propertyText": prop_text,
                "valueText": val_text,
                "propertyValueImg": img,
            })
    return result


def build_internal_item_object(item_data: dict, xianyu_image_urls: List[str],
                                category_info: dict, *, is_edit: bool = False) -> dict:
    """
    构造内部商品对象（用于双层序列化的最内层）。
    复用 XianyuItemPublisher._build_publish_data 的字段命名，但增加：
    - 多规格完整结构（itemProperties 带 supportImage/propertyValueImg）
    - propertyImageList
    - 多 SKU 完整 propertyList

    is_edit=True 时携带 itemId（来自 item_data），发布时不携带。
    """
    title = (item_data.get("title") or "").strip()
    desc = (item_data.get("desc") or item_data.get("description") or "").strip()
    image_urls = item_data.get("imageUrls", []) or []

    # ---- 图片 ----
    image_info_list: List[dict] = []
    for idx, url in enumerate(xianyu_image_urls):
        image_info_list.append({
            "url": url,
            "heightSize": 0,
            "widthSize": 0,
            "major": idx == 0,  # 第一张为主图
            "type": 0,
            "status": "done",
            "isQrCode": False,
            "extraInfo": {"isH": "false", "isT": "false", "raw": "false"},
        })

    # ---- 文本 ----
    item_text_dto = {
        "title": title,
        "desc": desc,
        "titleDescSeparate": False,
    }

    # ---- 类目 ----
    cat_id = category_info.get("catId") or "50025461"
    cat_name = category_info.get("catName") or "软件安装包/序列号/激活码"
    channel_cat_id = category_info.get("channelCatId") or "201449620"
    tb_cat_id = category_info.get("tbCatId") or "50003316"
    item_cat_dto = {
        "catId": str(cat_id),
        "catName": cat_name,
        "channelCatId": str(channel_cat_id),
        "tbCatId": str(tb_cat_id),
    }

    # ---- 标签 ----
    card_list = category_info.get("cardList", []) or []
    item_label_ext_list = []
    for card in card_list:
        if isinstance(card, dict):
            item_label_ext_list.append({
                "cardId": str(card.get("cardId", "")),
                "value": str(card.get("value", "")),
            })

    # ---- 运费 ----
    shipping_mode = item_data.get("shippingMode", "free")
    support_self_pick = item_data.get("supportSelfPick", False)
    if shipping_mode == "none":
        item_post_fee_dto = {
            "supportFreight": False,
            "templateId": "0",
        }
    elif shipping_mode == "fixed":
        post_fee = item_data.get("postFee", 0)
        post_price_in_cent = str(_safe_price_to_cent(post_fee)) if post_fee else "0"
        item_post_fee_dto = {
            "canFreeShipping": False,
            "supportFreight": True,
            "onlyTakeSelf": bool(support_self_pick),
            "templateId": "0",
            "postPriceInCent": post_price_in_cent,
        }
    else:
        item_post_fee_dto = {
            "canFreeShipping": True,
            "supportFreight": True,
            "onlyTakeSelf": bool(support_self_pick),
        }

    # ---- 地址 ----
    location = item_data.get("location", {}) or {}
    try:
        division_id = int(location.get("divisionId", 0))
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

    # ---- 服务协议（全部关闭，与现有发布保持一致） ----
    user_rights_protocols = [
        {"enable": False, "serviceCode": "FAST_DELIVERY_48_HOUR"},
        {"enable": False, "serviceCode": "FAST_DELIVERY_24_HOUR"},
        {"enable": False, "serviceCode": "VIRTUAL_NONCONFORMITY_FREE_REFUND_SERVICE"},
        {"enable": False, "serviceCode": "SKILL_PLAY_NO_MIND"},
    ]

    # ---- 多规格：itemProperties + itemSkuList + propertyImageList ----
    property_groups = item_data.get("itemProperties", []) or []
    # 提交前过滤空占位对象
    cleaned_properties: List[dict] = []
    for g in property_groups:
        name = (g.get("propertyName") or "").strip()
        if not name:
            continue
        cleaned_values = []
        for v in g.get("propertyValues", []) or []:
            if not isinstance(v, dict):
                continue
            val = (v.get("propertyValue") or "").strip()
            if not val:
                continue
            entry = {"propertyValue": val}
            if g.get("supportImage") and v.get("propertyValueImg"):
                entry["propertyValueImg"] = v["propertyValueImg"]
            cleaned_values.append(entry)
        if not cleaned_values:
            continue
        cleaned_properties.append({
            "propertyName": name,
            "supportImage": bool(g.get("supportImage")),
            "propertyValues": cleaned_values,
        })

    property_image_list = build_property_image_list(cleaned_properties)

    # SKU 列表（每个 SKU 已包含 propertyList + priceInCent + quantity）
    raw_sku_list = item_data.get("itemSkuList", []) or []
    item_sku_list: List[dict] = []
    for sku in raw_sku_list:
        property_list = sku.get("propertyList") or []
        # 过滤空 propertyText/valueText
        clean_property_list = [
            {"propertyText": str(p.get("propertyText", "")), "valueText": str(p.get("valueText", ""))}
            for p in property_list
            if p.get("propertyText") and p.get("valueText")
        ]
        price_cent = _safe_price_to_cent(sku.get("price", 0))
        try:
            qty = int(sku.get("quantity", 0))
        except (ValueError, TypeError):
            qty = 0
        item_sku_list.append({
            "priceInCent": str(price_cent),
            "quantity": str(qty),
            "propertyList": clean_property_list,
        })

    # 顶层 quantity 与 itemPriceDTO 取自 SKU 汇总
    total_quantity = sum(int(s.get("quantity", 0)) for s in item_sku_list)
    min_price_cent = min(
        (int(s.get("priceInCent", 0)) for s in item_sku_list),
        default=0,
    )
    item_price_dto = {"priceInCent": str(min_price_cent)}

    # ---- 组装最终内部商品对象 ----
    internal_obj: dict = {
        "freebies": False,
        "itemTypeStr": "b",
        "quantity": str(total_quantity),
        "simpleItem": "true",
        "defaultPrice": False,
        "uniqueCode": generate_unique_code(),
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
        "itemProperties": cleaned_properties,
    }

    if item_label_ext_list:
        internal_obj["itemLabelExtList"] = item_label_ext_list

    if property_image_list:
        internal_obj["propertyImageList"] = property_image_list

    # 编辑时携带 itemId；发布时不携带
    if is_edit:
        item_id = (item_data.get("itemId") or "").strip()
        if not item_id:
            raise ValueError("编辑请求必须携带 itemId")
        internal_obj["itemId"] = str(item_id)

    return internal_obj


def double_layer_serialize(internal_obj: dict) -> str:
    """
    双层 JSON 序列化：
    1. 内部商品对象 → 紧凑 JSON 字符串
    2. 包到 {inputJson: "<字符串>"} → 再次紧凑序列化
    3. 返回最终 data 字符串

    签名与提交都使用此最终字符串。
    """
    inner_json = json.dumps(internal_obj, ensure_ascii=False, separators=(",", ":"))
    outer = {"inputJson": inner_json}
    final_data = json.dumps(outer, ensure_ascii=False, separators=(",", ":"))
    return final_data


def build_sign(token: str, t_ms: str, data_str: str) -> str:
    """MD5 签名：md5(token + & + t + & + APP_KEY + & + data_str)"""
    raw = f"{token}&{t_ms}&{APP_KEY}&{data_str}"
    return hashlib.md5(raw.encode()).hexdigest()


def build_request_url(api_name: str, version: str, t_ms: str, sign: str) -> str:
    """构建闲鱼 MTOP 请求 URL（鱼小铺 seller 参数）。"""
    from urllib.parse import urlencode
    params = {
        "jsv": "2.7.2",
        "appKey": APP_KEY,
        "t": t_ms,
        "sign": sign,
        "v": version,
        "type": "json",
        "dataType": "json",
        "accountSite": "xianyu",
        "timeout": "20000",
        "api": api_name,
        "sessionOption": "AutoLoginOnly",
        "spm_cnt": "a21ybx.item.0.0",
        "spm_pre": "",
    }
    query = urlencode(params)
    return f"{H5_API_BASE}/{api_name}/{version}/?{query}"


def get_fish_shop_headers(cookie_str: str) -> dict:
    """构建鱼小铺请求头（不打印 cookie）。"""
    return {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
        "Cookie": cookie_str,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Origin": "https://seller.goofish.com",
        "Referer": "https://seller.goofish.com/",
        "idle_site_biz_code": "COMMONPRO",
        "idle_user_group_member_id": "",
    }


def call_fish_shop_api(cookie_str: str, api_name: str, version: str,
                        internal_obj: dict, *, is_edit: bool = False) -> dict:
    """
    调用鱼小铺多规格发布/编辑 API。
    严格双层序列化：签名与提交使用同一最终 data 字符串。

    返回解析后的 JSON 响应。

    安全：本函数不打印 cookie/token/sign/data 全文。
    """
    import requests

    # Step 0: 刷新 _m_h5_tk（与现有发布保持一致）
    refreshed_cookie = _refresh_m_h5_tk(cookie_str)
    if refreshed_cookie != cookie_str:
        cookie_str = refreshed_cookie

    token = extract_token_from_cookie(cookie_str)
    if not token:
        raise RuntimeError("Cookie 中缺少 _m_h5_tk，无法签名")

    # Step 1: 双层序列化
    final_data = double_layer_serialize(internal_obj)

    # Step 2: 生成时间戳与签名（用最终 data 字符串）
    t_ms = str(int(time.time() * 1000))
    sign = build_sign(token, t_ms, final_data)

    # Step 3: 构建请求
    url = build_request_url(api_name, version, t_ms, sign)
    headers = get_fish_shop_headers(cookie_str)

    # Step 4: 提交（data 字段使用与签名完全相同的 final_data）
    resp = requests.post(
        url,
        headers=headers,
        data={"data": final_data},
        timeout=30,
    )
    resp.raise_for_status()
    result = resp.json()

    # Step 5: 成功判断（不能只判断 HTTP 200）
    ret = result.get("ret", [])
    ret_msg = ret[0] if isinstance(ret, list) and ret else str(ret)
    if not any("SUCCESS" in str(r) for r in ret):
        # 脱敏日志：只记录 API 名、ret、traceId，不记录 cookie/sign/data
        logger.warning(
            "fish_shop_api_failed api=%s is_edit=%s ret=%s traceId=%s",
            api_name, is_edit, ret_msg, result.get("traceId", ""),
        )
        raise RuntimeError(f"鱼小铺接口调用失败：{ret_msg}")

    data_body = result.get("data", {})
    if isinstance(data_body, dict):
        # 检查 data.data 为 False 的情况
        if data_body.get("data") is False:
            msg = data_body.get("msg", "未知错误")
            logger.warning(
                "fish_shop_api_rejected api=%s is_edit=%s msg=%s traceId=%s",
                api_name, is_edit, msg, result.get("traceId", ""),
            )
            raise RuntimeError(f"鱼小铺平台拒绝请求：{msg}")

    return result


def match_response_skus(response_skus: List[dict],
                         submitted_skus: List[dict]) -> List[dict]:
    """
    匹配服务器返回的 SKU 与本地提交的 SKU。
    服务器返回顺序可能与请求不同，必须通过规格组合匹配，不得通过数组下标。

    匹配键：property_key（基于 propertyText + valueText 排序）。

    返回：每个 SKU 增加 skuId、inventoryId（若服务器返回）。
    """
    # 构建服务器 SKU 索引：property_key → response sku
    response_index: dict = {}
    for r_sku in response_skus or []:
        prop_list = r_sku.get("propertyList") or []
        key = build_property_key(prop_list)
        response_index[key] = r_sku

    matched: List[dict] = []
    for s_sku in submitted_skus:
        prop_list = s_sku.get("propertyList") or []
        key = build_property_key(prop_list)
        matched_sku = dict(s_sku)
        r_sku = response_index.get(key)
        if r_sku:
            if r_sku.get("skuId"):
                matched_sku["skuId"] = str(r_sku["skuId"])
            if r_sku.get("inventoryId"):
                matched_sku["inventoryId"] = str(r_sku["inventoryId"])
            # 用服务器响应校准价格和库存（优先级最高）
            if r_sku.get("priceInCent") is not None:
                try:
                    matched_sku["priceInCent"] = str(int(r_sku["priceInCent"]))
                except (ValueError, TypeError):
                    pass
            if r_sku.get("quantity") is not None:
                try:
                    matched_sku["quantity"] = str(int(r_sku["quantity"]))
                except (ValueError, TypeError):
                    pass
        matched.append(matched_sku)
    return matched


def extract_response_item_id(result: dict) -> str:
    """从响应中提取新创建的 itemId（发布场景）。"""
    data = result.get("data", {})
    if isinstance(data, dict):
        # 常见路径：data.data.itemId / data.itemId
        inner = data.get("data", {})
        if isinstance(inner, dict) and inner.get("itemId"):
            return str(inner["itemId"])
        if data.get("itemId"):
            return str(data["itemId"])
    return ""


def extract_response_skus(result: dict) -> List[dict]:
    """从响应中提取 itemSkuList。"""
    data = result.get("data", {})
    if isinstance(data, dict):
        inner = data.get("data", {})
        if isinstance(inner, dict):
            return inner.get("itemSkuList", []) or []
        return data.get("itemSkuList", []) or []
    return []


def _build_edit_detail_data(item_id: str) -> str:
    """构造 editdetail 请求的 data 字符串。

    单层 JSON：{"itemId":"<item_id>"}
    不包裹 inputJson。
    使用紧凑分隔符，与签名字符串完全一致。
    """
    if not item_id:
        raise ValueError("itemId 不能为空")
    # itemId 强制按字符串处理，避免整数精度损失
    inner = {"itemId": str(item_id)}
    return json.dumps(inner, ensure_ascii=False, separators=(",", ":"))


def _build_edit_detail_request_url(t_ms: str, sign: str) -> str:
    """构建 editdetail 请求 URL。"""
    return build_request_url(FISH_SHOP_EDIT_DETAIL_API, FISH_SHOP_EDIT_DETAIL_VERSION, t_ms, sign)


def call_fish_shop_edit_detail(cookie_str: str, item_id: str) -> dict:
    """
    调用 mtop.idle.pc.backend.idleitem.editdetail 接口获取完整商品编辑详情。

    单层 JSON 序列化：data = '{"itemId":"<item_id>"}'
    签名使用此 data 字符串，提交时使用同一字符串。

    返回解析后的 JSON 响应。

    安全：本函数不打印 cookie/token/sign/data 全文。
    异常时只记录脱敏信息（API名、itemId、HTTP状态、ret、traceId）。
    """
    import requests

    if not item_id:
        raise ValueError("itemId 不能为空")

    # Step 0: 刷新 _m_h5_tk
    refreshed_cookie = _refresh_m_h5_tk(cookie_str)
    if refreshed_cookie != cookie_str:
        cookie_str = refreshed_cookie

    token = extract_token_from_cookie(cookie_str)
    if not token:
        raise RuntimeError("Cookie 中缺少 _m_h5_tk，无法签名")

    # Step 1: 构造 data 字符串（单层 JSON）
    final_data = _build_edit_detail_data(item_id)

    # Step 2: 生成时间戳与签名
    t_ms = str(int(time.time() * 1000))
    sign = build_sign(token, t_ms, final_data)

    # Step 3: 构建请求
    url = _build_edit_detail_request_url(t_ms, sign)
    headers = get_fish_shop_headers(cookie_str)

    # Step 4: 提交
    resp = requests.post(
        url,
        headers=headers,
        data={"data": final_data},
        timeout=30,
    )
    http_status = resp.status_code
    if http_status != 200:
        # 脱敏日志
        logger.warning(
            "fish_shop_edit_detail_http_error api=%s item_id=%s http_status=%s",
            FISH_SHOP_EDIT_DETAIL_API, str(item_id)[:32], http_status,
        )
        resp.raise_for_status()

    result = resp.json()

    # Step 5: 业务成功判断（不能只判断 HTTP 200）
    ret = result.get("ret", [])
    ret_msg = ret[0] if isinstance(ret, list) and ret else str(ret)
    if not any("SUCCESS" in str(r) for r in ret):
        logger.warning(
            "fish_shop_edit_detail_failed api=%s item_id=%s ret=%s traceId=%s",
            FISH_SHOP_EDIT_DETAIL_API, str(item_id)[:32], ret_msg, result.get("traceId", ""),
        )
        raise RuntimeError(f"获取商品编辑详情失败：{ret_msg}")

    # Step 6: 校验 data.itemId 与请求 itemId 一致
    data_body = result.get("data", {})
    if not isinstance(data_body, dict):
        raise RuntimeError("商品编辑详情响应 data 字段格式异常")

    # data 可能直接含 itemId，也可能在 data.data 中
    inner_data = data_body.get("data", {})
    if not isinstance(inner_data, dict):
        inner_data = {}

    response_item_id = _safe_str(data_body.get("itemId") or inner_data.get("itemId"))
    if not response_item_id:
        logger.warning(
            "fish_shop_edit_detail_no_item_id api=%s item_id=%s traceId=%s",
            FISH_SHOP_EDIT_DETAIL_API, str(item_id)[:32], result.get("traceId", ""),
        )
        raise RuntimeError("商品编辑详情响应缺少 itemId")

    if str(response_item_id) != str(item_id):
        logger.warning(
            "fish_shop_edit_detail_item_id_mismatch api=%s request=%s response=%s traceId=%s",
            FISH_SHOP_EDIT_DETAIL_API,
            str(item_id)[:32], str(response_item_id)[:32],
            result.get("traceId", ""),
        )
        raise RuntimeError("商品编辑详情响应 itemId 与请求不一致")

    return result


def _extract_edit_detail_data(result: dict) -> dict:
    """从 editdetail 响应中提取 data 字段（统一为 dict）。

    支持两种响应结构（实测闲鱼 backend.idleitem 系列接口）：
    1. 嵌套结构：{ret, data: {data: {商品字段}}} — 旧版/部分接口
    2. 扁平结构：{ret, data: {商品字段}}         — edit/editdetail 实测结构

    优先返回 data.data（若为非空 dict），否则返回 data 本身。
    避免空 dict 覆盖 data 本身导致字段全部解析为空。
    """
    data = result.get("data", {})
    if not isinstance(data, dict):
        return {}
    inner = data.get("data", {})
    # 只有当 inner 是非空 dict 时才返回 inner，避免空 dict 覆盖 data 本身
    if isinstance(inner, dict) and inner:
        return inner
    return data


def _parse_image_info_list(image_info_do_list: Any) -> List[dict]:
    """解析 imageInfoDOList，正确识别主图。

    字符串布尔值 major="false" 必须被识别为 False，不能直接使用 bool()。
    顺序保持服务器返回顺序。
    """
    if not isinstance(image_info_do_list, list):
        return []
    parsed: List[dict] = []
    for img in image_info_do_list:
        if not isinstance(img, dict):
            continue
        url = _safe_str(img.get("url"))
        if not url:
            continue
        parsed.append({
            "url": url,
            "major": _safe_str_to_bool(img.get("major")),
            "widthSize": _safe_int(img.get("widthSize"), default=0, minimum=0),
            "heightSize": _safe_int(img.get("heightSize"), default=0, minimum=0),
            "type": _safe_int(img.get("type"), default=0, minimum=0),
            "extraInfo": img.get("extraInfo") if isinstance(img.get("extraInfo"), dict) else {},
        })
    return parsed


def _parse_item_text_dto(item_text_dto: Any) -> dict:
    """解析 itemTextDTO。

    标题使用 title，正文优先使用 desc。
    wlDescription 不能覆盖 desc，只能作为补充字段返回。
    """
    if not isinstance(item_text_dto, dict):
        return {"title": "", "desc": "", "wlDescription": "", "titleDescSeparate": False, "descPath": ""}
    return {
        "title": _safe_str(item_text_dto.get("title")),
        "desc": _safe_str(item_text_dto.get("desc")),
        # wlDescription 仅作为补充字段，不覆盖 desc
        "wlDescription": _safe_str(item_text_dto.get("wlDescription")),
        "titleDescSeparate": _safe_str_to_bool(item_text_dto.get("titleDescSeparate")),
        "descPath": _safe_str(item_text_dto.get("descPath")),
    }


def _parse_item_price_dto(item_price_dto: Any) -> dict:
    """解析 itemPriceDTO。priceInCent 单位为分，保持整数。"""
    if not isinstance(item_price_dto, dict):
        return {"priceInCent": 0}
    return {
        "priceInCent": _safe_int(item_price_dto.get("priceInCent"), default=0, minimum=0),
    }


def _parse_quantity(quantity_raw: Any) -> int:
    """安全转换库存为非负整数。0 是合法库存，不能被当成空值。"""
    return _safe_int(quantity_raw, default=0, minimum=0)


def _parse_item_cat_dto(item_cat_dto: Any) -> dict:
    """解析 itemCatDTO。"""
    if not isinstance(item_cat_dto, dict):
        return {"catId": "", "catName": "", "tbCatId": "", "channelCatId": "", "sugShow": ""}
    return {
        "catId": _safe_str(item_cat_dto.get("catId")),
        "catName": _safe_str(item_cat_dto.get("catName")),
        "tbCatId": _safe_str(item_cat_dto.get("tbCatId")),
        "channelCatId": _safe_str(item_cat_dto.get("channelCatId")),
        "sugShow": _safe_str(item_cat_dto.get("sugShow")),
    }


def _parse_item_label_ext_list(item_label_ext_list: Any) -> List[dict]:
    """解析 itemLabelExtList。空文本/空 properties 需安全处理。"""
    if not isinstance(item_label_ext_list, list):
        return []
    parsed: List[dict] = []
    for label in item_label_ext_list:
        if not isinstance(label, dict):
            continue
        parsed.append({
            "cardId": _safe_str(label.get("cardId")),
            "value": _safe_str(label.get("value")),
            "properties": label.get("properties") if isinstance(label.get("properties"), (dict, list)) else {},
        })
    return parsed


def _parse_item_addr_dto(item_addr_dto: Any) -> dict:
    """解析 itemAddrDTO。divisionId 兼容字符串与数字形式。"""
    if not isinstance(item_addr_dto, dict):
        return {
            "prov": "", "city": "", "area": "", "poiName": "",
            "divisionId": "", "gps": "", "poiId": "",
        }
    return {
        "prov": _safe_str(item_addr_dto.get("prov")),
        "city": _safe_str(item_addr_dto.get("city")),
        "area": _safe_str(item_addr_dto.get("area")),
        "poiName": _safe_str(item_addr_dto.get("poiName")),
        # divisionId 保持字符串形式，避免数字/字符串形式差异
        "divisionId": _safe_str(item_addr_dto.get("divisionId")),
        "gps": _safe_str(item_addr_dto.get("gps")),
        "poiId": _safe_str(item_addr_dto.get("poiId")),
    }


def _parse_item_post_fee_dto(item_post_fee_dto: Any) -> dict:
    """解析 itemPostFeeDTO。字符串布尔值安全转换。"""
    if not isinstance(item_post_fee_dto, dict):
        return {
            "canFreeShipping": False, "onlyTakeSelf": False,
            "supportFreight": False, "idleTemplateId": "",
            "templateId": "", "postPriceInCent": 0,
        }
    return {
        "canFreeShipping": _safe_str_to_bool(item_post_fee_dto.get("canFreeShipping")),
        "onlyTakeSelf": _safe_str_to_bool(item_post_fee_dto.get("onlyTakeSelf")),
        "supportFreight": _safe_str_to_bool(item_post_fee_dto.get("supportFreight")),
        "idleTemplateId": _safe_str(item_post_fee_dto.get("idleTemplateId")),
        "templateId": _safe_str(item_post_fee_dto.get("templateId")),
        "postPriceInCent": _safe_int(item_post_fee_dto.get("postPriceInCent"), default=0, minimum=0),
    }


def _parse_user_rights_protocols(user_rights_protocols: Any) -> List[dict]:
    """解析 userRightsProtocols。每项 {serviceCode, enable}。"""
    if not isinstance(user_rights_protocols, list):
        return []
    parsed: List[dict] = []
    for p in user_rights_protocols:
        if not isinstance(p, dict):
            continue
        parsed.append({
            "serviceCode": _safe_str(p.get("serviceCode")),
            "enable": _safe_str_to_bool(p.get("enable")),
        })
    return parsed


def _parse_item_properties(item_properties: Any) -> List[dict]:
    """解析 itemProperties（多规格）。

    简单商品响应中不包含此字段，返回空列表。
    多规格商品响应中包含规格类型与规格值。
    """
    if not isinstance(item_properties, list):
        return []
    parsed: List[dict] = []
    for g in item_properties:
        if not isinstance(g, dict):
            continue
        prop_name = _safe_str(g.get("propertyName"))
        if not prop_name:
            continue
        values: List[dict] = []
        for v in g.get("propertyValues", []) or []:
            if not isinstance(v, dict):
                continue
            val = _safe_str(v.get("propertyValue"))
            if not val:
                continue
            values.append({
                "propertyValue": val,
                "propertyValueImg": _safe_str(v.get("propertyValueImg")),
            })
        if not values:
            continue
        parsed.append({
            "propertyName": prop_name,
            "supportImage": _safe_str_to_bool(g.get("supportImage")),
            "propertyValues": values,
        })
    return parsed


def _parse_property_image_list(property_image_list: Any) -> List[dict]:
    """解析 propertyImageList（多规格）。

    简单商品响应中不包含此字段，返回空列表。

    实测响应结构（edit/editdetail 接口）：
        [{
            "major": "false",
            "property": {
                "propertyText": "容量",
                "valueText": "123",
                ...
            },
            "url": "https://img.alicdn.com/..."
        }]

    兼容两种字段路径：
    - 扁平路径（旧）：img.propertyText / img.valueText / img.propertyValueImg
    - 嵌套路径（实测）：img.property.propertyText / img.property.valueText / img.url
    """
    if not isinstance(property_image_list, list):
        return []
    parsed: List[dict] = []
    for img in property_image_list:
        if not isinstance(img, dict):
            continue
        # 规格文本：优先嵌套 property.propertyText，回退扁平 propertyText
        prop_obj = img.get("property")
        if isinstance(prop_obj, dict):
            prop_text = _safe_str(prop_obj.get("propertyText"))
            val_text = _safe_str(prop_obj.get("valueText"))
        else:
            prop_text = _safe_str(img.get("propertyText"))
            val_text = _safe_str(img.get("valueText"))
        # 图片 URL：优先嵌套 url，回退扁平 propertyValueImg
        img_url = _safe_str(img.get("url") or img.get("propertyValueImg"))
        if not prop_text or not val_text or not img_url:
            continue
        parsed.append({
            "propertyText": prop_text,
            "valueText": val_text,
            "propertyValueImg": img_url,
        })
    return parsed


def _parse_item_sku_list(item_sku_list: Any) -> List[dict]:
    """解析 itemSkuList（多规格）。

    简单商品响应中不包含此字段，返回空列表。
    每个 SKU 包含 priceInCent/quantity/propertyList/skuId/inventoryId。
    响应顺序可能乱序，调用方应通过 property_key 匹配。
    """
    if not isinstance(item_sku_list, list):
        return []
    parsed: List[dict] = []
    for s in item_sku_list:
        if not isinstance(s, dict):
            continue
        prop_list = []
        for p in s.get("propertyList", []) or []:
            if not isinstance(p, dict):
                continue
            prop_text = _safe_str(p.get("propertyText"))
            val_text = _safe_str(p.get("valueText"))
            if not prop_text or not val_text:
                continue
            prop_list.append({"propertyText": prop_text, "valueText": val_text})
        parsed.append({
            "priceInCent": _safe_int(s.get("priceInCent"), default=0, minimum=0),
            "quantity": _parse_quantity(s.get("quantity")),
            "propertyList": prop_list,
            "skuId": _safe_str(s.get("skuId")),
            "inventoryId": _safe_str(s.get("inventoryId")),
        })
    return parsed


def parse_edit_detail_response(result: dict) -> dict:
    """
    将 editdetail 接口响应映射为项目统一商品编辑模型。

    返回结构包含：商品标识/状态、标题/正文/图片、价格/库存、分类、地址、
    运费、服务协议、多规格字段。

    标题使用 itemTextDTO.title，正文优先使用 itemTextDTO.desc，
    不使用 wlDescription 覆盖 desc。

    失败（itemId 缺失或不一致）由 call_fish_shop_edit_detail 抛出异常，
    本函数只处理已通过校验的成功响应。
    """
    data = _extract_edit_detail_data(result)

    # 商品标识和状态
    item_id = _safe_str(data.get("itemId"))
    item_status = _safe_str(data.get("itemStatus"))
    item_type_str = _safe_str(data.get("itemTypeStr"))
    simple_item = _safe_str_to_bool(data.get("simpleItem"))
    default_price = _safe_str_to_bool(data.get("defaultPrice"))
    unique_code = _safe_str(data.get("uniqueCode"))
    freebies = _safe_str_to_bool(data.get("freebies"))
    stuff_status = _safe_str(data.get("stuffStatus"))
    support_bargain_price = _safe_str_to_bool(data.get("supportBargainPrice"))
    can_bargain = _safe_str_to_bool(data.get("canBargain"))

    # 标题和正文
    text_dto = _parse_item_text_dto(data.get("itemTextDTO"))
    title = text_dto["title"]
    description = text_dto["desc"]
    wl_description = text_dto["wlDescription"]

    # 商品图片
    image_list = _parse_image_info_list(data.get("imageInfoDOList"))
    image_urls = [img["url"] for img in image_list]
    major_image_url = ""
    for img in image_list:
        if img["major"]:
            major_image_url = img["url"]
            break
    if not major_image_url and image_urls:
        # 服务器未标记主图时，取第一张作为主图
        major_image_url = image_urls[0]

    # 价格和库存
    price_dto = _parse_item_price_dto(data.get("itemPriceDTO"))
    price_in_cent = price_dto["priceInCent"]
    quantity = _parse_quantity(data.get("quantity"))

    # 分类
    cat_dto = _parse_item_cat_dto(data.get("itemCatDTO"))
    label_ext_list = _parse_item_label_ext_list(data.get("itemLabelExtList"))

    # 地址
    addr_dto = _parse_item_addr_dto(data.get("itemAddrDTO"))

    # 发货和运费
    post_fee_dto = _parse_item_post_fee_dto(data.get("itemPostFeeDTO"))

    # 服务协议
    user_rights_protocols = _parse_user_rights_protocols(data.get("userRightsProtocols"))

    # 多规格（简单商品响应中无此字段，返回空列表）
    item_properties = _parse_item_properties(data.get("itemProperties"))
    property_image_list = _parse_property_image_list(data.get("propertyImageList"))
    item_sku_list = _parse_item_sku_list(data.get("itemSkuList"))

    # 是否多规格商品
    is_multi_spec = bool(item_properties) and bool(item_sku_list)

    return {
        # 商品标识和状态
        "itemId": item_id,
        "itemStatus": item_status,
        "itemTypeStr": item_type_str,
        "simpleItem": simple_item,
        "defaultPrice": default_price,
        "uniqueCode": unique_code,
        "freebies": freebies,
        "stuffStatus": stuff_status,
        "supportBargainPrice": support_bargain_price,
        "canBargain": can_bargain,
        # 标题和正文
        "title": title,
        "description": description,
        "wlDescription": wl_description,
        # 图片
        "imageUrls": image_urls,
        "majorImageUrl": major_image_url,
        "imageList": image_list,
        # 价格和库存
        "priceInCent": price_in_cent,
        "quantity": quantity,
        # 分类
        "catId": cat_dto["catId"],
        "catName": cat_dto["catName"],
        "tbCatId": cat_dto["tbCatId"],
        "channelCatId": cat_dto["channelCatId"],
        "itemLabelExtList": label_ext_list,
        # 地址
        "prov": addr_dto["prov"],
        "city": addr_dto["city"],
        "area": addr_dto["area"],
        "poiName": addr_dto["poiName"],
        "divisionId": addr_dto["divisionId"],
        "gps": addr_dto["gps"],
        "poiId": addr_dto["poiId"],
        # 运费
        "canFreeShipping": post_fee_dto["canFreeShipping"],
        "onlyTakeSelf": post_fee_dto["onlyTakeSelf"],
        "supportFreight": post_fee_dto["supportFreight"],
        "idleTemplateId": post_fee_dto["idleTemplateId"],
        "templateId": post_fee_dto["templateId"],
        "postPriceInCent": post_fee_dto["postPriceInCent"],
        # 服务协议
        "userRightsProtocols": user_rights_protocols,
        # 多规格
        "itemProperties": item_properties,
        "itemSkuList": item_sku_list,
        "propertyImageList": property_image_list,
        "isMultiSpec": is_multi_spec,
    }


def invalidate_edit_detail_cache(account_id: int, item_id: str) -> None:
    """失效指定账号+商品的详情缓存。

    编辑成功后必须调用，避免长期展示陈旧数据。
    """
    cache_key = (int(account_id), str(item_id))
    _edit_detail_cache.pop(cache_key, None)
    _edit_detail_inflight.pop(cache_key, None)


def fetch_fish_shop_edit_detail(
    cookie_str: str,
    account_id: int,
    item_id: str,
    *,
    bypass_cache: bool = False,
) -> dict:
    """
    获取鱼小铺商品完整编辑详情（统一入口）。

    带有短期缓存（30 秒）与请求去重（同一账号+itemId 同时只发一个请求）。
    所有需要"完整商品正文"或"完整编辑数据"的场景都应调用本函数。

    参数：
        cookie_str: 当前账号的 Cookie（由服务端从账号凭证解密取得）
        account_id: 闲鱼账号内部 ID（用于缓存隔离）
        item_id: 闲鱼商品 ID（按字符串处理）
        bypass_cache: True 时强制刷新，绕过缓存

    返回：parse_edit_detail_response() 解析后的统一模型 dict。

    安全：
        - 不打印 Cookie/token/sign
        - 异常消息只暴露脱敏信息
        - 缓存键只使用 account_id + item_id，不包含 Cookie
    """
    if not item_id:
        raise ValueError("itemId 不能为空")
    if not account_id:
        raise ValueError("account_id 不能为空")

    cache_key = (int(account_id), str(item_id))

    # 1) 命中缓存直接返回
    if not bypass_cache:
        cached = _edit_detail_cache.get(cache_key)
        if cached:
            cached_ts, cached_result = cached
            if time.time() - cached_ts < _EDIT_DETAIL_CACHE_TTL_SECONDS:
                logger.info(
                    "fish_shop_edit_detail_cache_hit account_id=%s item_id=%s",
                    account_id, str(item_id)[:32],
                )
                return cached_result

    # 2) 请求去重：同一 account_id+item_id 同时只发一个请求
    inflight = _edit_detail_inflight.get(cache_key)
    if inflight is not None:
        # 已有进行中请求，等待其结果
        logger.info(
            "fish_shop_edit_detail_inflight_wait account_id=%s item_id=%s",
            account_id, str(item_id)[:32],
        )
        inflight.wait(timeout=60)
        # 等待结束后尝试命中缓存（请求方应已写入）
        cached = _edit_detail_cache.get(cache_key)
        if cached:
            cached_ts, cached_result = cached
            if time.time() - cached_ts < _EDIT_DETAIL_CACHE_TTL_SECONDS:
                return cached_result
        # 缓存仍未命中，继续向下发起请求

    # 3) 发起真实请求
    import threading
    event = threading.Event()
    _edit_detail_inflight[cache_key] = event
    try:
        start_ts = time.time()
        raw = call_fish_shop_edit_detail(cookie_str, item_id)
        elapsed_ms = int((time.time() - start_ts) * 1000)
        parsed = parse_edit_detail_response(raw)
        # 4) 写入缓存
        _edit_detail_cache[cache_key] = (time.time(), parsed)
        logger.info(
            "fish_shop_edit_detail_loaded account_id=%s item_id=%s elapsed_ms=%s is_multi_spec=%s",
            account_id, str(item_id)[:32], elapsed_ms, parsed.get("isMultiSpec"),
        )
        return parsed
    finally:
        # 5) 清理进行中标记，唤醒等待方
        _edit_detail_inflight.pop(cache_key, None)
        event.set()


def clear_all_edit_detail_cache() -> None:
    """清空所有详情缓存。用于商品同步后批量失效。"""
    _edit_detail_cache.clear()
    _edit_detail_inflight.clear()
