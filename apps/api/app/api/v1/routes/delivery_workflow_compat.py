import datetime as dt
import json
import logging
import re
from typing import Any, NamedTuple

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.database import get_db
from ....core.response import ResultObject
from ....services.ai_provider import generate_text
from ..deps import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(tags=["delivery-workflow-compat"])
AI_SETTINGS_HINT = "未配置通用模型，当前仅展示规则匹配候选。前往系统设置中的“模型配置”完成配置后，可使用 AI 一键配置商品。"

# 自动发货规则与全局配置扩展 router（前端 autoDelivery.js 调用）
# 参考项目 Java 实现：
#   - /api/auto-delivery/rules 由 AutoDeliveryController 查询 delivery_rule 表
#   - /api/auto-delivery/global-config 由 DeliveryGlobalConfigController 查询 delivery_global_config 表
# 开源版 strategy：参考项目使用独立 delivery_rule 表，开源版已有 delivery_goods_config 表（按商品 JSON 配置）。
# 为兼容前端，这里实现 GET 端点：rules 返回基于 delivery_goods_config 聚合的规则视图；global-config 返回桩配置。
delivery_rules_router = APIRouter(tags=["delivery-rules-ext"])

CONFIG_TIMINGS = ("payDelivery", "confirmDelivery", "reviewDelivery")
# 仅保留有效账号（deleted=0）的商品，已退出账号的旧商品不在前台展示
VALID_ACCOUNT_FILTER = (
    "EXISTS (SELECT 1 FROM xianyu_account a "
    "WHERE a.id = g.account_id AND a.deleted = 0)"
)
SOURCE_GOODS_PAGE_MAX_SIZE = 100
SOURCE_RECOMMEND_PAGE_MAX_SIZE = 30
SOURCE_RECOMMEND_CANDIDATE_MAX = 200
SOURCE_RECOMMEND_MODEL_CANDIDATE_MAX = 60
SOURCE_RECOMMEND_SOURCE_CONTENT_MAX_CHARS = 4_000
SOURCE_RECOMMEND_CANDIDATE_TEXT_MAX_CHARS = 500
SOURCE_RECOMMEND_SCORING_TEXT_MAX_CHARS = 8_000
SOURCE_RECOMMEND_TOKEN_MAX_COUNT = 64
SOURCE_RECOMMEND_TOKEN_MAX_CHARS = 64
TIMING_TO_RECORD = {
    "payDelivery": "after_payment",
    "confirmDelivery": "after_receipt",
    "reviewDelivery": "after_review",
}
RECORD_TO_TIMING = {value: key for key, value in TIMING_TO_RECORD.items()}
TOKEN_PATTERN = re.compile(r"[\w\u4e00-\u9fff]{2,}")
# Splits a source title into distinct CJK / Latin / digit segments so they can
# be searched independently. "庄园领主steam" -> ["庄园领主", "steam"], which
# matches goods like "庄园领主终极版Steam激活码" that a single
# LIKE '%庄园领主steam%' would miss (the CJK and Latin are not contiguous there).
SEARCH_KEYWORD_PATTERN = re.compile(r"[\u4e00-\u9fff]{2,}|[a-zA-Z]{2,}|[0-9]{2,}")

TEMPLATE_VARIABLES = [
    {"key": "{买家昵称}", "desc": "买家在闲鱼中的显示名称"},
    {"key": "{订单编号}", "desc": "订单编号"},
    {"key": "{商品标题}", "desc": "商品标题"},
    {"key": "{商品ID}", "desc": "商品ID"},
    {"key": "{卡密}", "desc": "卡密内容"},
    {"key": "{卡号}", "desc": "卡号（卡号密码类）"},
    {"key": "{密码}", "desc": "密码（卡号密码类）"},
    {"key": "{链接}", "desc": "链接 / 提取链接"},
    {"key": "{提取码}", "desc": "提取码"},
    {"key": "{当前时间}", "desc": "发货时的系统时间"},
    {"key": "{店铺名称}", "desc": "店铺名称"},
    {"key": "{分段}", "desc": "将内容拆成多条消息依次发送"},
]

STATEMENT_DEFAULT_CONTENT = (
    "订单编号：{订单编号}\n\n"
    "您好，该订单包含的商品为虚拟商品，发货后不支持退换。"
    "如无异议，请回复【确认】。\n"
    "如有异议，请回复【取消】，这边帮您转人工客服，进行退款操作"
)


def _format_datetime(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    return str(value)


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _json_loads(raw: Any, default: Any) -> Any:
    if raw in (None, ""):
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return default


def _parse_delivery_config(raw: Any, goods_id: int) -> dict[str, Any]:
    """Parse persisted delivery configuration without treating corruption as empty state."""
    if raw in (None, ""):
        return {}
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        logger.error("delivery config has unsupported payload type goods_id=%s", goods_id)
        raise ValueError("invalid delivery configuration payload")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error("delivery config JSON is corrupt goods_id=%s", goods_id)
        raise ValueError("invalid delivery configuration JSON") from exc
    if not isinstance(parsed, dict):
        logger.error("delivery config JSON is not an object goods_id=%s", goods_id)
        raise ValueError("invalid delivery configuration shape")
    return parsed


def _page_payload(records: list[dict[str, Any]], total: int, current: int, size: int) -> dict[str, Any]:
    return {"records": records, "total": total, "current": current, "size": size}


def _build_in_clause(prefix: str, values: list[Any]) -> tuple[str, dict[str, Any]]:
    placeholders: list[str] = []
    params: dict[str, Any] = {}
    for index, value in enumerate(values):
        key = f"{prefix}_{index}"
        placeholders.append(f":{key}")
        params[key] = value
    return ", ".join(placeholders), params


def _normalize_card_type(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"", "kami"}:
        return "unique"
    if normalized in {"unique", "card_password", "link_code", "account_password", "custom"}:
        return normalized
    return "custom"


def _merge_card_content(card_key: Any, card_value: Any) -> str:
    key_text = str(card_key or "").strip()
    value_text = str(card_value or "").strip()
    if key_text and value_text:
        return f"{key_text}----{value_text}"
    return key_text or value_text


def _card_item_status(row: dict[str, Any]) -> int:
    if row.get("status") is not None:
        return _to_int(row.get("status"))
    return 2 if _to_int(row.get("is_used")) == 1 else 0


def _card_group_record(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "groupName": row.get("group_name") or "",
        "cardType": _normalize_card_type(row.get("group_type")),
        "cardPrefix": row.get("card_prefix") or "",
        "passwordPrefix": row.get("password_prefix") or "",
        "costPrice": _to_float(row.get("cost_price")),
        "suggestedPrice": _to_float(row.get("suggested_price")),
        "alertThreshold": _to_int(row.get("alert_threshold"), 10),
        "totalCount": _to_int(row.get("total_count")),
        "remainCount": _to_int(row.get("available_count")),
        "usedCount": _to_int(row.get("used_count")),
        "invalidCount": _to_int(row.get("invalid_count")),
        "errorCount": _to_int(row.get("error_count")),
        "remark": row.get("remark") or "",
        "status": _to_int(row.get("status"), 1),
        "createdTime": _format_datetime(row.get("created_time")),
        "updatedTime": _format_datetime(row.get("updated_time")),
    }


def _card_item_record(row: dict[str, Any]) -> dict[str, Any]:
    used_order_id = row.get("used_order_id") or row.get("used_by_order_id")
    card_content = _merge_card_content(row.get("card_key"), row.get("card_value"))
    return {
        "id": row["id"],
        "groupId": row.get("group_id"),
        "cardKey": row.get("card_key") or "",
        "cardValue": row.get("card_value") or "",
        "cardContent": card_content,
        "content": card_content,
        "extraInfo": row.get("extra_info") or "",
        "status": _card_item_status(row),
        "usedOrderId": used_order_id,
        "usedTime": _format_datetime(row.get("used_time")),
        "remark": row.get("remark") or "",
        "createdTime": _format_datetime(row.get("created_time")),
        "updatedTime": _format_datetime(row.get("updated_time")),
    }


def _goods_record(row: dict[str, Any]) -> dict[str, Any]:
    account = {
        "id": row.get("account_id"),
        "avatarUrl": row.get("account_avatar_url") or "",
        "nickname": row.get("account_nickname") or "",
        "displayName": row.get("account_nickname") or "",
        "accountNote": row.get("account_remark") or "",
        "externalUid": row.get("account_external_uid") or "",
    }
    record = {
        "id": row["id"],
        "accountId": row.get("account_id"),
        "title": row.get("title") or "",
        "category": row.get("category") or "",
        "status": _to_int(row.get("status"), 1),
        "coverPic": row.get("cover_pic") or "",
        "imageUrl": row.get("image_url") or "",
        "description": row.get("description") or "",
        "detailInfo": row.get("detail_info") or "",
        "stock": _to_int(row.get("stock")),
        "accountAvatarUrl": account["avatarUrl"],
        "accountNickname": account["nickname"],
        "accountRemark": account["accountNote"],
        "accountExternalUid": account["externalUid"],
        "account": account,
    }
    if "source_configured" in row:
        record["configured"] = _to_int(row.get("source_configured")) == 1
    return record


def _normalize_source_delivery_mode(value: Any) -> str:
    """Normalize delivery mode for delivery_text_source. Only 'text' and 'card' are allowed."""
    mode = str(value or "text").strip().lower()
    return "card" if mode in {"card", "kami"} else "text"


def _build_source_stock_label(row: dict[str, Any], delivery_mode: str) -> str:
    """Build a human-readable stock label for a source row, used in list/detail responses."""
    if delivery_mode != "card":
        return "文本"
    remain = _to_int(row.get("card_remain_count") or row.get("cardRemainCount"))
    group_name = row.get("card_group_name") or row.get("cardGroupName") or ""
    return f"{group_name} · 剩余 {remain}" if group_name else f"剩余 {remain}"


def _parse_segments_from_row(raw: Any) -> list[dict[str, Any]]:
    """从数据库行解析 segments 字段（JSON 字符串或已解析的 list）。"""
    if raw is None or raw == "":
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, list) else []
        except (json.JSONDecodeError, TypeError):
            return []
    return []


def _source_record(row: dict[str, Any], usage_count: int) -> dict[str, Any]:
    delivery_mode = _normalize_source_delivery_mode(row.get("delivery_mode"))
    card_group_id_raw = row.get("card_group_id")
    try:
        card_group_id: int | None = _to_int(card_group_id_raw) or None
    except (TypeError, ValueError):
        card_group_id = None
    return {
        "id": row["id"],
        "title": row.get("title") or "",
        "content": row.get("content") or "",
        "remark": row.get("remark") or "",
        "deliveryMode": delivery_mode,
        "cardGroupId": card_group_id,
        "cardGroupName": row.get("card_group_name") or row.get("cardGroupName") or "",
        "cardRemainCount": _to_int(row.get("card_remain_count") or row.get("cardRemainCount")),
        "usageCount": usage_count,
        "stockLabel": _build_source_stock_label(row, delivery_mode),
        "segments": _parse_segments_from_row(row.get("segments")),
        "createdTime": _format_datetime(row.get("created_time")),
        "updatedTime": _format_datetime(row.get("updated_time")),
    }


def _template_record(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row.get("name") or "",
        "type": _to_int(row.get("type"), 6),
        "status": _to_int(row.get("status"), 1),
        "content": row.get("content") or "",
        "randomEnabled": _to_int(row.get("random_enabled")),
        "createdTime": _format_datetime(row.get("created_time")),
        "updatedTime": _format_datetime(row.get("updated_time")),
    }


def _delivery_record(row: dict[str, Any]) -> dict[str, Any]:
    status = row.get("status")
    if status is None:
        status = 2 if str(row.get("delivery_status") or "").lower() == "success" else 3 if str(row.get("delivery_status") or "").lower() == "failed" else 0
    quantity_requested = _to_int(row.get("quantity_requested"), 1) or 1
    quantity_sent = _to_int(row.get("quantity_sent"))
    if quantity_sent == 0 and str(row.get("delivery_status") or "").lower() == "success":
        quantity_sent = quantity_requested
    delivery_mode = str(row.get("delivery_mode") or row.get("delivery_type") or "text").lower()
    return {
        "id": row["id"],
        "accountId": row.get("account_id"),
        "orderId": row.get("order_id"),
        "externalOrderId": row.get("external_order_id") or "",
        "goodsTitle": row.get("goods_title") or "",
        "goodsName": row.get("goods_title") or "",
        "buyerName": row.get("buyer_name") or "",
        "buyerNick": row.get("buyer_name") or "",
        "buyerId": row.get("buyer_id") or "",
        "deliveryTiming": row.get("delivery_timing") or "after_payment",
        "timing": row.get("delivery_timing") or "after_payment",
        "deliveryMode": "card" if delivery_mode in {"card", "kami"} else "text",
        "status": _to_int(status),
        "deliveryStatus": row.get("delivery_status") or ("success" if _to_int(status) == 2 else "failed" if _to_int(status) in {3, 6, 7} else "pending"),
        "quantityRequested": quantity_requested,
        "quantitySent": quantity_sent,
        "errorMessage": row.get("error_message") or row.get("fail_reason") or "",
        "deliveryContent": row.get("delivery_content") or row.get("content") or "",
        "content": row.get("delivery_content") or row.get("content") or "",
        "createdTime": _format_datetime(row.get("created_time")),
        "completedTime": _format_datetime(row.get("completed_time")),
        "platformSyncTime": _format_datetime(row.get("platform_sync_time")),
        "result": row.get("result") or "",
        "purchaseTime": _format_datetime(row.get("purchase_time")),
        "goodsId": row.get("goods_id"),
        "goodsCoverPic": row.get("goods_cover_pic") or "",
        "sellerName": row.get("seller_name") or "",
        "sellerDisplayName": row.get("seller_display_name") or "",
    }


def _current_date_range() -> tuple[str, str]:
    today = dt.date.today()
    return (f"{today.isoformat()} 00:00:00", f"{today.isoformat()} 23:59:59")


class _SourceRecommendFeatures(NamedTuple):
    normalized_text: str
    tokens: tuple[str, ...]
    full_text: str


def _bounded_match_text(row: dict[str, Any], keys: tuple[str, ...]) -> str:
    parts: list[str] = []
    remaining = SOURCE_RECOMMEND_SCORING_TEXT_MAX_CHARS
    for key in keys:
        if remaining <= 0:
            break
        value = str(row.get(key) or "")
        excerpt = value[:remaining]
        parts.append(excerpt)
        remaining -= len(excerpt) + 1
    return " ".join(parts)[:SOURCE_RECOMMEND_SCORING_TEXT_MAX_CHARS]


def _source_tokens(row: dict[str, Any]) -> list[str]:
    source_text = _bounded_match_text(
        row,
        ("title", "remark", "content"),
    ).lower()
    seen: set[str] = set()
    result: list[str] = []
    for token in TOKEN_PATTERN.findall(source_text):
        token = token.strip().lower()[:SOURCE_RECOMMEND_TOKEN_MAX_CHARS]
        if len(token) < 2 or token in seen:
            continue
        seen.add(token)
        result.append(token)
        if len(result) >= SOURCE_RECOMMEND_TOKEN_MAX_COUNT:
            break
    return result


def _source_recommend_features(source_row: dict[str, Any]) -> _SourceRecommendFeatures:
    full_text = _bounded_match_text(source_row, ("title", "remark", "content")).lower()
    return _SourceRecommendFeatures(
        normalized_text=_normalize_match_text(full_text),
        tokens=tuple(_source_tokens(source_row)),
        full_text=full_text,
    )


def _recommend_reason(
    source_row: dict[str, Any],
    goods_row: dict[str, Any],
    features: _SourceRecommendFeatures | None = None,
) -> tuple[str | None, str | None]:
    prepared = features or _source_recommend_features(source_row)
    goods_text = _bounded_match_text(
        goods_row,
        ("title", "category", "description", "detailInfo"),
    ).lower()
    title = str(goods_row.get("title") or "")
    overlap: list[str] = []
    for token in prepared.tokens:
        if token in goods_text:
            overlap.append(token)
    if title and title.lower() in prepared.full_text:
        return "high", "货源正文中直接命中了商品标题"
    if len(overlap) >= 2:
        return "high", f"关键词重合：{', '.join(overlap[:3])}"
    if overlap:
        return "medium", f"存在关联关键词：{overlap[0]}"
    source_title = str(source_row.get("title") or "").lower()
    goods_category = str(goods_row.get("category") or "").lower()
    if source_title and goods_category and (goods_category in source_title or source_title in goods_category):
        return "medium", "货源标题与商品类目存在关联"
    return None, None


def _normalize_match_text(value: Any) -> str:
    return (
        str(value or "")
        .lower()
        .replace(" ", "")
        .replace("\n", "")
        .replace("\r", "")
        .replace("-", "")
        .replace("_", "")
        .strip()
    )


def _source_recommend_score(
    source_row: dict[str, Any],
    goods_row: dict[str, Any],
    features: _SourceRecommendFeatures | None = None,
) -> tuple[int, list[str], bool]:
    prepared = features or _source_recommend_features(source_row)
    source_text = prepared.normalized_text
    goods_text = _normalize_match_text(
        _bounded_match_text(
            goods_row,
            ("title", "category", "description", "detailInfo"),
        )
    )
    if not source_text or not goods_text:
        return 0, [], False

    score = 0
    token_hits: list[str] = []
    for token in prepared.tokens:
        if token in goods_text:
            token_hits.append(token)
            score += 8 + min(len(token), 8)

    title_text = str(goods_row.get("title") or "").strip().lower()
    source_full_text = prepared.full_text
    source_title_text = str(source_row.get("title") or "").strip().lower()
    # Forward: the goods title appears verbatim inside the source text.
    # Reverse: the source title appears verbatim inside the goods title — this
    # catches goods whose titles extend the source keyword (e.g. source
    # "庄园领主" vs goods "庄园领主 激活码") which the forward check alone misses,
    # causing highly relevant goods to score below the exclusion threshold and
    # never reach the AI model.
    title_hit = bool(
        title_text
        and (
            title_text in source_full_text
            or (
                len(source_title_text) >= 2
                and source_title_text in title_text
            )
        )
    )
    if title_hit:
        score += 30

    seen_chars: set[str] = set()
    overlap_score = 0
    for ch in source_text:
        if ch.isspace() or ch in seen_chars:
            continue
        seen_chars.add(ch)
        if ch in goods_text:
            overlap_score += 1
    score += min(overlap_score, 18)
    return score, token_hits, title_hit


def _score_to_confidence(score: int) -> str | None:
    if score >= 60:
        return "high"
    if score >= 35:
        return "medium"
    if score >= 20:
        return "low"
    return None


def _build_source_candidate(
    source_row: dict[str, Any],
    goods_row: dict[str, Any],
    features: _SourceRecommendFeatures | None = None,
    *,
    force_include: bool = False,
) -> dict[str, Any] | None:
    prepared = features or _source_recommend_features(source_row)
    score, token_hits, title_hit = _source_recommend_score(
        source_row,
        goods_row,
        prepared,
    )
    confidence = _score_to_confidence(score)
    if not confidence:
        # Goods sourced from the keyword search query are guaranteed relevant
        # to the user (they would find them via search too). Don't let the
        # rule-score threshold drop them before the AI model gets to evaluate
        # them — assign minimum "low" confidence so they enter ranked_candidates
        # and reach the AI's candidate window. They start as recommended=False
        # so they only appear in the final result if the AI promotes them.
        if not force_include:
            return None
        confidence = "low"

    reason = None
    if title_hit:
        reason = "货源标题与商品标题高度相关"
    elif token_hits:
        label = "关键词高度重合" if confidence == "high" else "存在关联关键词"
        reason = f"{label}：{', '.join(token_hits[:3])}"
    else:
        _, reason = _recommend_reason(source_row, goods_row, prepared)

    candidate = dict(goods_row)
    candidate["score"] = score
    candidate["confidence"] = confidence
    candidate["reason"] = reason or "可进一步人工确认是否匹配"
    candidate["recommended"] = score >= 35
    return candidate


def _parse_ai_match_payload(raw_content: str) -> dict[str, Any]:
    text_value = str(raw_content or "").strip()
    if not text_value:
        return {}
    start = text_value.find("{")
    end = text_value.rfind("}")
    if start >= 0 and end > start:
        text_value = text_value[start : end + 1]
    try:
        parsed = json.loads(text_value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


async def _ai_match_source_goods(
    source_row: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    if not candidates:
        return {"configured": True, "ok": True, "matchedIds": set(), "reasons": {}}

    candidate_lines = []
    for candidate in candidates[:SOURCE_RECOMMEND_MODEL_CANDIDATE_MAX]:
        candidate_lines.append(
            f"{candidate.get('id')} | "
            f"{str(candidate.get('title') or '')[:SOURCE_RECOMMEND_CANDIDATE_TEXT_MAX_CHARS]} | "
            f"{str(candidate.get('category') or '')[:SOURCE_RECOMMEND_CANDIDATE_TEXT_MAX_CHARS]} | "
            f"{str(candidate.get('description') or '')[:SOURCE_RECOMMEND_CANDIDATE_TEXT_MAX_CHARS]}"
        )

    ai_result = await generate_text(
        scene="delivery_source_match",
        system_prompt="You are a delivery source matching assistant. Return JSON only.",
        user_prompt=(
            f"Source title: {source_row.get('title') or ''}\n"
            "Source content: "
            f"{str(source_row.get('content') or '')[:SOURCE_RECOMMEND_SOURCE_CONTENT_MAX_CHARS]}\n"
            f"Source remark: {source_row.get('remark') or ''}\n"
            f"Candidates:\n" + "\n".join(candidate_lines) + "\n"
            'Return format: {"matches":[{"id":123,"reason":"short reason"}]}'
        ),
        temperature=0.1,
    )
    if not ai_result.get("configured"):
        return {
            "configured": False,
            "ok": False,
            "error": AI_SETTINGS_HINT,
            "errorCode": "NOT_CONFIGURED",
        }
    if not ai_result.get("ok") or not ai_result.get("content"):
        return {
            "configured": True,
            "ok": False,
            "error": str(ai_result.get("error") or "AI matching failed"),
            "errorCode": "AI_ERROR",
        }

    parsed = _parse_ai_match_payload(str(ai_result.get("content") or ""))
    matches = parsed.get("matches")
    if not isinstance(matches, list):
        return {"configured": True, "ok": True, "matchedIds": set(), "reasons": {}}

    matched_ids: set[int] = set()
    reasons: dict[int, str] = {}
    for item in matches[:SOURCE_RECOMMEND_MODEL_CANDIDATE_MAX]:
        if not isinstance(item, dict):
            continue
        goods_id = _to_int(item.get("id"))
        if not goods_id:
            continue
        matched_ids.add(goods_id)
        reason = str(item.get("reason") or "").strip()[:SOURCE_RECOMMEND_CANDIDATE_TEXT_MAX_CHARS]
        if reason:
            reasons[goods_id] = reason
    return {
        "configured": True,
        "ok": True,
        "matchedIds": matched_ids,
        "reasons": reasons,
    }


async def _refresh_card_group_counts(db: AsyncSession, group_id: int) -> None:
    await db.execute(
        text(
            """
            UPDATE card_group g
            SET total_count = (
                    SELECT COUNT(*) FROM card_item i
                    WHERE i.group_id = g.id AND i.deleted = 0
                ),
                used_count = (
                    SELECT COUNT(*) FROM card_item i
                    WHERE i.group_id = g.id AND i.deleted = 0 AND COALESCE(i.status, CASE WHEN COALESCE(i.is_used, 0) = 1 THEN 2 ELSE 0 END) = 2
                ),
                available_count = (
                    SELECT COUNT(*) FROM card_item i
                    WHERE i.group_id = g.id AND i.deleted = 0 AND COALESCE(i.status, CASE WHEN COALESCE(i.is_used, 0) = 1 THEN 2 ELSE 0 END) = 0
                ),
                updated_time = NOW()
            WHERE g.id = :group_id
            """
        ),
        {"group_id": group_id},
    )


async def _fetch_card_group_row(db: AsyncSession, group_id: int) -> dict[str, Any] | None:
    row = (
        await db.execute(
            text(
                """
                SELECT
                    g.id,
                    g.group_name,
                    g.group_type,
                    g.card_prefix,
                    g.password_prefix,
                    g.cost_price,
                    g.suggested_price,
                    g.alert_threshold,
                    g.remark,
                    g.status,
                    g.created_time,
                    g.updated_time,
                    COUNT(i.id) AS total_count,
                    COALESCE(SUM(CASE WHEN i.id IS NOT NULL AND COALESCE(i.status, CASE WHEN COALESCE(i.is_used, 0) = 1 THEN 2 ELSE 0 END) = 0 THEN 1 ELSE 0 END), 0) AS available_count,
                    COALESCE(SUM(CASE WHEN i.id IS NOT NULL AND COALESCE(i.status, CASE WHEN COALESCE(i.is_used, 0) = 1 THEN 2 ELSE 0 END) = 2 THEN 1 ELSE 0 END), 0) AS used_count,
                    COALESCE(SUM(CASE WHEN i.id IS NOT NULL AND COALESCE(i.status, CASE WHEN COALESCE(i.is_used, 0) = 1 THEN 2 ELSE 0 END) = 3 THEN 1 ELSE 0 END), 0) AS invalid_count,
                    COALESCE(SUM(CASE WHEN i.id IS NOT NULL AND COALESCE(i.status, CASE WHEN COALESCE(i.is_used, 0) = 1 THEN 2 ELSE 0 END) = 4 THEN 1 ELSE 0 END), 0) AS error_count
                FROM card_group g
                LEFT JOIN card_item i
                  ON i.group_id = g.id
                 AND i.deleted = 0
                WHERE g.id = :group_id
                  AND g.deleted = 0
                GROUP BY
                    g.id, g.group_name, g.group_type, g.card_prefix, g.password_prefix,
                    g.cost_price, g.suggested_price, g.alert_threshold, g.remark, g.status,
                    g.created_time, g.updated_time
                """
            ),
            {"group_id": group_id},
        )
    ).mappings().first()
    return dict(row) if row else None


async def _load_goods_config(db: AsyncSession, goods_id: int) -> dict[str, Any]:
    row = (
        await db.execute(
            text(
                """
                SELECT config_json
                FROM delivery_goods_config
                WHERE goods_id = :goods_id
                  AND deleted = 0
                LIMIT 1
                """
            ),
            {"goods_id": goods_id},
        )
    ).mappings().first()
    if row:
        return _parse_delivery_config(row.get("config_json"), goods_id)

    legacy_rule = (
        await db.execute(
            text(
                """
                SELECT goods_id, account_id, delivery_mode, card_group_id, delivery_content, status
                FROM delivery_rule
                WHERE goods_id = :goods_id
                  AND deleted = 0
                ORDER BY status DESC, updated_time DESC, id DESC
                LIMIT 1
                """
            ),
            {"goods_id": goods_id},
        )
    ).mappings().first()
    if not legacy_rule:
        return {}

    legacy_mode = str(legacy_rule.get("delivery_mode") or "").strip().lower()
    timing_config: dict[str, Any] = {
        "enabled": 1 if _to_int(legacy_rule.get("status"), 1) == 1 else 0,
        "mode": "card" if legacy_mode in {"kami", "card"} else "text",
    }
    if legacy_rule.get("card_group_id") is not None:
        timing_config["cardGroupId"] = _to_int(legacy_rule.get("card_group_id"))
    if legacy_rule.get("delivery_content"):
        if timing_config["mode"] == "card":
            timing_config["cardTemplate"] = legacy_rule.get("delivery_content")
        else:
            timing_config["content"] = legacy_rule.get("delivery_content")

    config: dict[str, Any] = {"payDelivery": timing_config}
    account_id = _to_int(legacy_rule.get("account_id"))
    if account_id:
        config["accountId"] = account_id
    return config


async def _load_all_goods_configs(
    db: AsyncSession,
    goods_ids: list[int] | None = None,
) -> dict[int, dict[str, Any]]:
    if goods_ids is not None and not goods_ids:
        return {}
    config_where = "deleted = 0"
    legacy_where = "goods_id IS NOT NULL AND deleted = 0"
    query_params: dict[str, Any] = {}
    if goods_ids is not None:
        in_clause, query_params = _build_in_clause("config_goods_id", goods_ids)
        config_where += f" AND goods_id IN ({in_clause})"
        legacy_where += f" AND goods_id IN ({in_clause})"
    rows = (
        await db.execute(
            text(
                f"""
                SELECT goods_id, config_json
                FROM delivery_goods_config
                WHERE {config_where}
                """
            ),
            query_params,
        )
    ).mappings().all()
    config_map = {
        _to_int(row["goods_id"]): _parse_delivery_config(
            row.get("config_json"), _to_int(row["goods_id"])
        )
        for row in rows
    }
    legacy_rows = (
        await db.execute(
            text(
                f"""
                SELECT goods_id, account_id, delivery_mode, card_group_id, delivery_content, status
                FROM delivery_rule
                WHERE {legacy_where}
                ORDER BY goods_id ASC, status DESC, updated_time DESC, id DESC
                """
            ),
            query_params,
        )
    ).mappings().all()

    for row in legacy_rows:
        goods_id = _to_int(row.get("goods_id"))
        if not goods_id:
            continue
        current = config_map.get(goods_id)
        if isinstance(current, dict) and any(isinstance(current.get(timing), dict) and current.get(timing) for timing in CONFIG_TIMINGS):
            continue
        legacy_mode = str(row.get("delivery_mode") or "").strip().lower()
        timing_config: dict[str, Any] = {
            "enabled": 1 if _to_int(row.get("status"), 1) == 1 else 0,
            "mode": "card" if legacy_mode in {"kami", "card"} else "text",
        }
        if row.get("card_group_id") is not None:
            timing_config["cardGroupId"] = _to_int(row.get("card_group_id"))
        if row.get("delivery_content"):
            if timing_config["mode"] == "card":
                timing_config["cardTemplate"] = row.get("delivery_content")
            else:
                timing_config["content"] = row.get("delivery_content")

        config_map[goods_id] = {"payDelivery": timing_config}
        account_id = _to_int(row.get("account_id"))
        if account_id:
            config_map[goods_id]["accountId"] = account_id

    return config_map


async def _upsert_goods_config(db: AsyncSession, goods_id: int, config: dict[str, Any]) -> None:
    payload = json.dumps(config, ensure_ascii=False)
    await db.execute(
        text(
            """
            INSERT INTO delivery_goods_config(goods_id, config_json, created_time, updated_time, deleted)
            VALUES(:goods_id, :config_json, NOW(), NOW(), 0)
            ON DUPLICATE KEY UPDATE
                config_json = VALUES(config_json),
                deleted = 0,
                updated_time = NOW()
            """
        ),
        {"goods_id": goods_id, "config_json": payload},
    )


async def _clear_goods_config(db: AsyncSession, goods_id: int) -> None:
    await db.execute(
        text(
            """
            UPDATE delivery_goods_config
            SET config_json = '{}',
                updated_time = NOW(),
                deleted = 0
            WHERE goods_id = :goods_id
            """
        ),
        {"goods_id": goods_id},
    )


def _normalize_goods_ids(raw_values: Any, *, maximum: int = 500) -> list[int]:
    """Parse a bounded, de-duplicated list of positive goods identifiers."""
    if not isinstance(raw_values, list) or not raw_values:
        raise ValueError("goodsIds 必须是非空数组")
    if len(raw_values) > maximum:
        raise ValueError(f"单次最多处理 {maximum} 个商品")
    result: list[int] = []
    seen: set[int] = set()
    for raw_value in raw_values:
        if isinstance(raw_value, bool):
            raise ValueError("goodsIds 只能包含正整数")
        try:
            goods_id = int(raw_value)
        except (TypeError, ValueError) as exc:
            raise ValueError("goodsIds 只能包含正整数") from exc
        if goods_id <= 0:
            raise ValueError("goodsIds 只能包含正整数")
        if goods_id not in seen:
            seen.add(goods_id)
            result.append(goods_id)
    return result


async def _missing_goods_ids(db: AsyncSession, goods_ids: list[int]) -> list[int]:
    if not goods_ids:
        return []
    in_clause, params = _build_in_clause("existing_goods_id", goods_ids)
    rows = (
        await db.execute(
            text(
                f"""
                SELECT id
                FROM xianyu_goods g
                WHERE deleted = 0
                  AND {VALID_ACCOUNT_FILTER}
                  AND id IN ({in_clause})
                """
            ),
            params,
        )
    ).all()
    existing = {_to_int(row[0]) for row in rows}
    return [goods_id for goods_id in goods_ids if goods_id not in existing]


async def _require_goods(db: AsyncSession, goods_ids: list[int]) -> ResultObject | None:
    missing = await _missing_goods_ids(db, goods_ids)
    if not missing:
        return None
    return ResultObject.failed(
        f"商品不存在或已删除：{', '.join(str(goods_id) for goods_id in missing[:20])}",
        code=404,
    )


async def _load_delivery_source_row(
    db: AsyncSession,
    source_id: int,
    *,
    for_update: bool = False,
) -> dict[str, Any] | None:
    """Load one active source, optionally holding its row through the transaction."""

    lock_clause = " FOR UPDATE" if for_update else ""
    row = (
        await db.execute(
            text(
                f"""
                SELECT s.id, s.title, s.content, s.remark,
                       s.source_type, s.delivery_mode, s.card_group_id,
                       s.segments,
                       s.created_time, s.updated_time,
                       g.group_name AS card_group_name,
                       g.available_count AS card_remain_count
                FROM delivery_text_source s
                LEFT JOIN card_group g ON g.id = s.card_group_id AND g.deleted = 0
                WHERE s.id = :source_id
                  AND s.deleted = 0
                LIMIT 1{lock_clause}
                """
            ),
            {"source_id": source_id},
        )
    ).mappings().first()
    return dict(row) if row else None


def _positive_source_id(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("货源编号必须是正整数")
    try:
        source_id = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("货源编号必须是正整数") from exc
    if source_id <= 0:
        raise ValueError("货源编号必须是正整数")
    return source_id


def _normalize_segments(raw: Any) -> list[dict]:
    """规范化 segments 字段为 list[dict]。
    兼容三种输入：
      - List[Dict]（来自 config_json 解析后的 Python 对象）
      - JSON 字符串（来自 delivery_text_source.segments 列）
      - None / 空值
    校验：每个 segment 必须含 type ∈ {text, image}，且 text/image 互斥。
    返回空 list 表示无 segments（执行端回退到单条 content 发送）。
    """
    if not raw:
        return []
    if isinstance(raw, str):
        if not raw.strip():
            return []
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("segments JSON 解析失败，回退到单条发送: raw=%s", raw[:200])
            return []
    if not isinstance(raw, list) or not raw:
        return []
    result: list[dict] = []
    for idx, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        seg_type = str(item.get("type") or "text").strip().lower()
        if seg_type not in ("text", "image"):
            logger.warning("segments[%d] type 无效: %s，跳过", idx, seg_type)
            continue
        if seg_type == "image":
            url = str(item.get("imageUrl") or "").strip()
            if not url:
                logger.warning("segments[%d] image 类型但 imageUrl 为空，跳过", idx)
                continue
            seg = {"type": "image", "imageUrl": url}
            if item.get("assetId") is not None:
                try:
                    seg["assetId"] = int(item.get("assetId"))
                except (TypeError, ValueError):
                    pass
            result.append(seg)
        else:
            text_content = str(item.get("content") or "").strip()
            if not text_content:
                logger.warning("segments[%d] text 类型但 content 为空，跳过", idx)
                continue
            result.append({"type": "text", "content": text_content})
    return result


def _delivery_source_fields(body: dict[str, Any]) -> dict[str, Any]:
    """Validate source fields before MySQL can turn user input into a 500.

    Returns a dict with title/content/remark/delivery_mode/card_group_id.
    Card mode requires a card group id and the {卡密占位} placeholder in content.
    """

    title = str(body.get("title") or "").strip()
    content = str(body.get("content") or "").strip()
    remark = str(body.get("remark") or "").strip()
    if len(title) > 200:
        raise ValueError("货源标题最多 200 个字符")
    if len(remark) > 500:
        raise ValueError("货源备注最多 500 个字符")
    if len(content.encode("utf-8")) > 65_535:
        raise ValueError("货源正文 UTF-8 编码后不能超过 65535 字节")
    # segments 校验（可选字段，支持多条正文+图片发货）
    try:
        segments = _normalize_segments(body.get("segments"))
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    if not title and not content and not segments:
        raise ValueError("标题、正文和多条正文至少填写一项")
    delivery_mode = _normalize_source_delivery_mode(body.get("deliveryMode") or body.get("delivery_mode"))
    card_group_id: int | None = None
    if delivery_mode == "card":
        try:
            card_group_id = _to_int(body.get("cardGroupId") or body.get("card_group_id"))
        except (TypeError, ValueError):
            card_group_id = None
        if not card_group_id:
            raise ValueError("卡密发货模式下必须选择一个卡密分组")
        if "{卡密占位}" not in content:
            raise ValueError("卡密发货的正文必须包含 {卡密占位} 占位符，否则无法替换实际卡密")
    return {
        "title": title,
        "content": content,
        "remark": remark,
        "delivery_mode": delivery_mode,
        "card_group_id": card_group_id,
        "segments": segments,
    }


async def _list_goods_rows(db: AsyncSession, goods_ids: list[int] | None = None) -> list[dict[str, Any]]:
    where_sql = ["g.deleted = 0", VALID_ACCOUNT_FILTER]
    params: dict[str, Any] = {}
    if goods_ids:
        in_clause, in_params = _build_in_clause("goods_id", goods_ids)
        where_sql.append(f"g.id IN ({in_clause})")
        params.update(in_params)

    rows = (
        await db.execute(
            text(
                f"""
                SELECT
                    g.id,
                    g.account_id,
                    g.title,
                    g.category,
                    g.status,
                    g.cover_pic,
                    g.image_url,
                    g.description,
                    g.detail_info,
                    g.stock,
                    a.avatar_url AS account_avatar_url,
                    a.nickname AS account_nickname,
                    a.remark AS account_remark,
                    a.external_uid AS account_external_uid
                FROM xianyu_goods g
                LEFT JOIN xianyu_account a
                  ON a.id = g.account_id
                 AND a.deleted = 0
                WHERE {' AND '.join(where_sql)}
                ORDER BY g.updated_time DESC, g.id DESC
                """
            ),
            params,
        )
    ).mappings().all()
    return [_goods_record(dict(row)) for row in rows]


def _source_binding_predicate(config_alias: str = "source_cfg") -> str:
    """Return a safe SQL predicate for one goods/source binding.

    ``config_json`` is LONGTEXT in compatibility installations. Invalid legacy
    JSON must never make JSON_EXTRACT abort the whole page query, so the
    expression substitutes an empty object before extracting the three known
    timing paths. Mutations still parse and reject corrupt configuration.
    """

    safe_json = (
        f"IF(JSON_VALID({config_alias}.config_json), "
        f"{config_alias}.config_json, '{{}}')"
    )
    timing_checks = [
        (
            "JSON_UNQUOTE(JSON_EXTRACT("
            f"{safe_json}, '$.{timing}.sourceId')) = CAST(:source_id AS CHAR)"
        )
        for timing in CONFIG_TIMINGS
    ]
    return (
        "EXISTS ("
        f"SELECT 1 FROM delivery_goods_config {config_alias} "
        f"WHERE {config_alias}.goods_id = g.id "
        f"AND {config_alias}.deleted = 0 "
        f"AND ({' OR '.join(timing_checks)})"
        ")"
    )


def _goods_search_clause(keyword: str, params: dict[str, Any]) -> str | None:
    normalized = keyword.strip()
    if not normalized:
        return None
    params["goods_keyword"] = f"%{normalized}%"
    return (
        "(COALESCE(g.title, '') LIKE :goods_keyword "
        "OR COALESCE(g.category, '') LIKE :goods_keyword "
        "OR COALESCE(g.description, '') LIKE :goods_keyword "
        "OR COALESCE(g.detail_info, '') LIKE :goods_keyword)"
    )


def _multi_keyword_search_clause(
    keywords: list[str], params: dict[str, Any]
) -> str | None:
    """Build an OR LIKE clause across multiple keywords and text columns.

    Used by the recommendation candidate pool to search with each CJK/Latin
    segment of the source title independently, so a source titled
    "庄园领主steam" matches goods like "庄园领主终极版Steam激活码" that a single
    LIKE '%庄园领主steam%' would miss.
    """
    if not keywords:
        return None
    columns = ("g.title", "g.category", "g.description", "g.detail_info")
    clauses: list[str] = []
    for index, kw in enumerate(keywords):
        param_name = f"src_kw_{index}"
        params[param_name] = f"%{kw}%"
        for col in columns:
            clauses.append(f"COALESCE({col}, '') LIKE :{param_name}")
    return "(" + " OR ".join(clauses) + ")"


def _extract_source_keywords(source_keyword: str) -> list[str]:
    """Split a source title into distinct CJK / Latin / digit search keywords.

    "庄园领主steam" -> ["庄园领主", "steam"]; "Steam 庄园领主 终极版" ->
    ["steam", "庄园领主", "终极版"]. Keywords shorter than 2 chars are dropped.
    """
    normalized = source_keyword.strip().lower()
    if not normalized:
        return []
    seen: set[str] = set()
    result: list[str] = []
    for segment in SEARCH_KEYWORD_PATTERN.findall(normalized):
        if len(segment) >= 2 and segment not in seen:
            seen.add(segment)
            result.append(segment)
    return result


def _source_goods_select_sql(binding_predicate: str, where_sql: str) -> str:
    return f"""
        SELECT
            g.id,
            g.account_id,
            g.title,
            g.category,
            g.status,
            g.cover_pic,
            g.image_url,
            g.description,
            g.detail_info,
            g.stock,
            a.avatar_url AS account_avatar_url,
            a.nickname AS account_nickname,
            a.remark AS account_remark,
            a.external_uid AS account_external_uid,
            CASE WHEN {binding_predicate} THEN 1 ELSE 0 END AS source_configured
        FROM xianyu_goods g
        LEFT JOIN xianyu_account a
          ON a.id = g.account_id
         AND a.deleted = 0
        WHERE {where_sql}
    """


async def _list_source_goods_page(
    db: AsyncSession,
    source_id: int,
    *,
    current: int,
    size: int,
    keyword: str,
    configured_only: bool,
) -> dict[str, Any]:
    """List one bounded, stable page without materializing the goods library."""

    binding_predicate = _source_binding_predicate()
    params: dict[str, Any] = {"source_id": source_id}
    where_parts = ["g.deleted = 0", VALID_ACCOUNT_FILTER]
    if configured_only:
        where_parts.append(binding_predicate)
    search_clause = _goods_search_clause(keyword, params)
    if search_clause:
        where_parts.append(search_clause)
    where_sql = " AND ".join(where_parts)

    total = (
        await db.execute(
            text(f"SELECT COUNT(*) FROM xianyu_goods g WHERE {where_sql}"),
            params,
        )
    ).scalar() or 0

    page_params = dict(params)
    page_params.update({"offset": (current - 1) * size, "limit": size})
    rows = (
        await db.execute(
            text(
                _source_goods_select_sql(binding_predicate, where_sql)
                + " ORDER BY g.id DESC LIMIT :limit OFFSET :offset"
            ),
            page_params,
        )
    ).mappings().all()
    records = [_goods_record(dict(row)) for row in rows]
    return _page_payload(records, _to_int(total), current, size)


async def _count_source_goods(db: AsyncSession, source_id: int) -> int:
    binding_predicate = _source_binding_predicate()
    total = (
        await db.execute(
            text(
                "SELECT COUNT(*) FROM xianyu_goods g "
                f"WHERE g.deleted = 0 AND {VALID_ACCOUNT_FILTER} AND {binding_predicate}"
            ),
            {"source_id": source_id},
        )
    ).scalar() or 0
    return _to_int(total)


async def _count_goods_rows(db: AsyncSession) -> int:
    total = (
        await db.execute(
            text(f"SELECT COUNT(*) FROM xianyu_goods g WHERE g.deleted = 0 AND {VALID_ACCOUNT_FILTER}")
        )
    ).scalar() or 0
    return _to_int(total)


async def _list_recommendation_candidate_rows(
    db: AsyncSession,
    source_id: int,
    *,
    limit: int,
    source_keyword: str = "",
) -> tuple[list[dict[str, Any]], int, set[int]]:
    """Load goods for recommendation analysis.

    The candidate pool is a union of two queries so that highly relevant
    goods are never hidden by the recency cap:

    1. Up to ``limit`` goods whose title/category/description/detail_info
       match the source title keyword (same SQL the goods search endpoint
       uses). This guarantees goods the user would find via search are
       always scored and seen by the AI model.
    2. Up to ``limit`` of the most recent goods (broad pool the AI can
       consider for looser matches).

    Both queries intentionally omit the ``status = 1`` filter so off-shelf
    goods remain eligible — otherwise the search view can surface relevant
    goods that the recommender never sees. The total is a COUNT of all
    non-deleted goods for the account scope.

    Returns ``(rows, total, keyword_matched_ids)`` where
    ``keyword_matched_ids`` are the goods ids sourced from the keyword
    query. The recommender uses this set to force-include those goods
    past the rule-score drop threshold so the AI model always gets to
    evaluate goods the user could find via search.
    """

    base_where = f"g.deleted = 0 AND {VALID_ACCOUNT_FILTER}"
    total = (
        await db.execute(
            text(f"SELECT COUNT(*) FROM xianyu_goods g WHERE {base_where}")
        )
    ).scalar() or 0
    binding_predicate = _source_binding_predicate()
    select_sql = _source_goods_select_sql(binding_predicate, base_where)

    # Query A: keyword-matched goods (guaranteed-relevant slice).
    # The source title is split into independent CJK/Latin/digit segments so
    # that "庄园领主steam" matches goods containing "庄园领主" OR "steam"
    # separately — a single LIKE '%庄园领主steam%' would miss goods whose CJK
    # and Latin parts are separated by other text (e.g. "庄园领主终极版Steam").
    keywords = _extract_source_keywords(source_keyword)
    keyword_params: dict[str, Any] = {"source_id": source_id, "candidate_limit": limit}
    keyword_clause = _multi_keyword_search_clause(keywords, keyword_params)
    keyword_rows: list[dict[str, Any]] = []
    if keyword_clause:
        keyword_rows = [
            dict(row)
            for row in (
                await db.execute(
                    text(
                        _source_goods_select_sql(binding_predicate, f"{base_where} AND ({keyword_clause})")
                        + " ORDER BY g.id DESC LIMIT :candidate_limit"
                    ),
                    keyword_params,
                )
            ).mappings().all()
        ]

    # Query B: latest broad pool.
    recent_rows = [
        dict(row)
        for row in (
            await db.execute(
                text(
                    select_sql
                    + " ORDER BY g.id DESC LIMIT :candidate_limit"
                ),
                {"source_id": source_id, "candidate_limit": limit},
            )
        ).mappings().all()
    ]

    # Union by goods id, preserving keyword-matched rows first.
    merged: list[dict[str, Any]] = []
    keyword_matched_ids: set[int] = set()
    seen: set[int] = set()
    for row in (*keyword_rows, *recent_rows):
        goods_id = _to_int(row.get("id"))
        if not goods_id or goods_id in seen:
            continue
        seen.add(goods_id)
        merged.append(row)
    keyword_matched_ids = {_to_int(row.get("id")) for row in keyword_rows}
    return ([_goods_record(row) for row in merged], _to_int(total), keyword_matched_ids)


def _matching_source_ids(config: dict[str, Any]) -> set[int]:
    source_ids: set[int] = set()
    for timing in CONFIG_TIMINGS:
        timing_config = config.get(timing)
        if not isinstance(timing_config, dict):
            continue
        source_id = timing_config.get("sourceId")
        if source_id is not None:
            source_ids.add(_to_int(source_id))
    return source_ids


def _goods_uses_source(config: dict[str, Any], source_id: int) -> bool:
    return source_id in _matching_source_ids(config)


def _enabled_timing_configs(config: dict[str, Any]) -> list[dict[str, Any]]:
    enabled_items: list[dict[str, Any]] = []
    for timing in CONFIG_TIMINGS:
        timing_config = config.get(timing)
        if isinstance(timing_config, dict) and _truthy(timing_config.get("enabled", 1)):
            enabled_items.append(timing_config)
    return enabled_items


@router.get("/cards/alerts", response_model=ResultObject)
async def get_card_alerts(
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    rows = (
        await db.execute(
            text(
                """
                SELECT
                    g.id,
                    g.group_name,
                    COALESCE(g.alert_threshold, 10) AS alert_threshold,
                    COALESCE(SUM(CASE WHEN i.id IS NOT NULL AND COALESCE(i.status, CASE WHEN COALESCE(i.is_used, 0) = 1 THEN 2 ELSE 0 END) = 0 THEN 1 ELSE 0 END), 0) AS available_count
                FROM card_group g
                LEFT JOIN card_item i
                  ON i.group_id = g.id
                 AND i.deleted = 0
                WHERE g.deleted = 0
                GROUP BY g.id, g.group_name, g.alert_threshold
                HAVING available_count < COALESCE(g.alert_threshold, 10)
                ORDER BY available_count ASC, g.id DESC
                """
            )
        )
    ).mappings().all()
    data = [
        {
            "id": row["id"],
            "groupName": row.get("group_name") or "",
            "remainCount": _to_int(row.get("available_count")),
            "alertThreshold": _to_int(row.get("alert_threshold"), 10),
        }
        for row in rows
    ]
    return ResultObject.success(data)


@router.post("/cards/import/validate", response_model=ResultObject)
async def validate_card_import(
    body: dict[str, Any] = Body(default_factory=dict),
    _: dict = Depends(get_current_user),
):
    items = body.get("items")
    if isinstance(items, list):
        total = len(items)
    else:
        raw_text = str(body.get("content") or "")
        total = len([line for line in raw_text.splitlines() if line.strip()])
    return ResultObject.success({"total": total, "valid": total, "invalid": 0})


@router.get("/cards", response_model=ResultObject)
async def get_cards(
    keyword: str = Query(default=""),
    current: int = Query(default=1),
    size: int = Query(default=20),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    safe_current = max(current, 1)
    safe_size = min(max(size, 1), 200)
    offset = (safe_current - 1) * safe_size
    params: dict[str, Any] = {}
    where_sql = ["g.deleted = 0"]
    if keyword.strip():
        where_sql.append("g.group_name LIKE :keyword")
        params["keyword"] = f"%{keyword.strip()}%"

    total = (
        await db.execute(
            text(f"SELECT COUNT(*) FROM card_group g WHERE {' AND '.join(where_sql)}"),
            params,
        )
    ).scalar() or 0

    params.update({"offset": offset, "limit": safe_size})
    rows = (
        await db.execute(
            text(
                f"""
                SELECT
                    g.id,
                    g.group_name,
                    g.group_type,
                    g.card_prefix,
                    g.password_prefix,
                    g.cost_price,
                    g.suggested_price,
                    g.alert_threshold,
                    g.remark,
                    g.status,
                    g.created_time,
                    g.updated_time,
                    COUNT(i.id) AS total_count,
                    COALESCE(SUM(CASE WHEN i.id IS NOT NULL AND COALESCE(i.status, CASE WHEN COALESCE(i.is_used, 0) = 1 THEN 2 ELSE 0 END) = 0 THEN 1 ELSE 0 END), 0) AS available_count,
                    COALESCE(SUM(CASE WHEN i.id IS NOT NULL AND COALESCE(i.status, CASE WHEN COALESCE(i.is_used, 0) = 1 THEN 2 ELSE 0 END) = 2 THEN 1 ELSE 0 END), 0) AS used_count,
                    COALESCE(SUM(CASE WHEN i.id IS NOT NULL AND COALESCE(i.status, CASE WHEN COALESCE(i.is_used, 0) = 1 THEN 2 ELSE 0 END) = 3 THEN 1 ELSE 0 END), 0) AS invalid_count,
                    COALESCE(SUM(CASE WHEN i.id IS NOT NULL AND COALESCE(i.status, CASE WHEN COALESCE(i.is_used, 0) = 1 THEN 2 ELSE 0 END) = 4 THEN 1 ELSE 0 END), 0) AS error_count
                FROM card_group g
                LEFT JOIN card_item i
                  ON i.group_id = g.id
                 AND i.deleted = 0
                WHERE {' AND '.join(where_sql)}
                GROUP BY
                    g.id, g.group_name, g.group_type, g.card_prefix, g.password_prefix,
                    g.cost_price, g.suggested_price, g.alert_threshold, g.remark, g.status,
                    g.created_time, g.updated_time
                ORDER BY g.updated_time DESC, g.id DESC
                LIMIT :offset, :limit
                """
            ),
            params,
        )
    ).mappings().all()

    records = [_card_group_record(dict(row)) for row in rows]
    return ResultObject.success(_page_payload(records, _to_int(total), safe_current, safe_size))


@router.post("/cards", response_model=ResultObject)
async def create_card_group(
    body: dict[str, Any] = Body(default_factory=dict),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    group_name = str(body.get("groupName") or "").strip()
    if not group_name:
        return ResultObject.validate_failed("分组名称不能为空")

    await db.execute(
        text(
            """
            INSERT INTO card_group(
                group_name, group_type, card_prefix, password_prefix, cost_price, suggested_price,
                alert_threshold, total_count, used_count, available_count, remark, status, deleted, created_time, updated_time
            ) VALUES(
                :group_name, :group_type, :card_prefix, :password_prefix, :cost_price, :suggested_price,
                :alert_threshold, 0, 0, 0, :remark, :status, 0, NOW(), NOW()
            )
            """
        ),
        {
            "group_name": group_name,
            "group_type": _normalize_card_type(body.get("cardType")),
            "card_prefix": str(body.get("cardPrefix") or "").strip() or None,
            "password_prefix": str(body.get("passwordPrefix") or "").strip() or None,
            "cost_price": _to_float(body.get("costPrice")),
            "suggested_price": _to_float(body.get("suggestedPrice")),
            "alert_threshold": _to_int(body.get("alertThreshold"), 10),
            "remark": str(body.get("remark") or "").strip() or None,
            "status": 1 if _to_int(body.get("status"), 1) == 1 else 0,
        },
    )
    await db.commit()
    return ResultObject.success(None, "卡密分组已创建")


@router.put("/cards/{group_id}", response_model=ResultObject)
async def update_card_group(
    group_id: int,
    body: dict[str, Any] = Body(default_factory=dict),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    group_name = str(body.get("groupName") or "").strip()
    if not group_name:
        return ResultObject.validate_failed("分组名称不能为空")

    await db.execute(
        text(
            """
            UPDATE card_group
            SET group_name = :group_name,
                group_type = :group_type,
                card_prefix = :card_prefix,
                password_prefix = :password_prefix,
                cost_price = :cost_price,
                suggested_price = :suggested_price,
                alert_threshold = :alert_threshold,
                remark = :remark,
                status = :status,
                updated_time = NOW()
            WHERE id = :group_id
              AND deleted = 0
            """
        ),
        {
            "group_id": group_id,
            "group_name": group_name,
            "group_type": _normalize_card_type(body.get("cardType")),
            "card_prefix": str(body.get("cardPrefix") or "").strip() or None,
            "password_prefix": str(body.get("passwordPrefix") or "").strip() or None,
            "cost_price": _to_float(body.get("costPrice")),
            "suggested_price": _to_float(body.get("suggestedPrice")),
            "alert_threshold": _to_int(body.get("alertThreshold"), 10),
            "remark": str(body.get("remark") or "").strip() or None,
            "status": 1 if _to_int(body.get("status"), 1) == 1 else 0,
        },
    )
    await db.commit()
    return ResultObject.success(None, "卡密分组已更新")


@router.delete("/cards/{group_id}", response_model=ResultObject)
async def delete_card_group(
    group_id: int,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    await db.execute(
        text(
            """
            UPDATE card_group
            SET deleted = 1, updated_time = NOW()
            WHERE id = :group_id
            """
        ),
        {"group_id": group_id},
    )
    await db.execute(
        text(
            """
            UPDATE card_item
            SET deleted = 1, updated_time = NOW()
            WHERE group_id = :group_id
            """
        ),
        {"group_id": group_id},
    )
    await db.commit()
    return ResultObject.success(None, "卡密分组已删除")


@router.get("/cards/{group_id}", response_model=ResultObject)
async def get_card_group_detail(
    group_id: int,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    row = await _fetch_card_group_row(db, group_id)
    if not row:
        return ResultObject.failed("卡密分组不存在", code=404)
    return ResultObject.success(_card_group_record(row))


@router.get("/cards/{group_id}/items", response_model=ResultObject)
async def get_card_items(
    group_id: int,
    status: int | None = Query(default=None),
    current: int = Query(default=1),
    size: int = Query(default=50),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    safe_current = max(current, 1)
    safe_size = min(max(size, 1), 200)
    offset = (safe_current - 1) * safe_size
    params: dict[str, Any] = {"group_id": group_id}
    where_sql = [
        "group_id = :group_id",
        "deleted = 0",
    ]
    if status is not None:
        where_sql.append("COALESCE(status, CASE WHEN COALESCE(is_used, 0) = 1 THEN 2 ELSE 0 END) = :status")
        params["status"] = status

    total = (
        await db.execute(
            text(f"SELECT COUNT(*) FROM card_item WHERE {' AND '.join(where_sql)}"),
            params,
        )
    ).scalar() or 0

    params.update({"offset": offset, "limit": safe_size})
    rows = (
        await db.execute(
            text(
                f"""
                SELECT
                    id, group_id, card_key, card_value, extra_info, status, is_used,
                    used_order_id, used_by_order_id, used_time, remark, created_time, updated_time
                FROM card_item
                WHERE {' AND '.join(where_sql)}
                ORDER BY id DESC
                LIMIT :offset, :limit
                """
            ),
            params,
        )
    ).mappings().all()
    records = [_card_item_record(dict(row)) for row in rows]
    return ResultObject.success(_page_payload(records, _to_int(total), safe_current, safe_size))


@router.post("/cards/{group_id}/items", response_model=ResultObject)
async def create_card_item(
    group_id: int,
    body: dict[str, Any] = Body(default_factory=dict),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    content = str(body.get("content") or "").strip()
    card_key = str(body.get("cardContent") or "").strip()
    card_value = str(body.get("password") or "").strip()
    if not card_key and content:
        merged = content.split("----", 1)
        card_key = merged[0].strip()
        card_value = merged[1].strip() if len(merged) > 1 else ""
    if not card_key:
        return ResultObject.validate_failed("卡密内容不能为空")

    await db.execute(
        text(
            """
            INSERT INTO card_item(
                group_id, card_key, card_value, extra_info, status, is_used, deleted, created_time, updated_time
            ) VALUES(
                :group_id, :card_key, :card_value, :extra_info, 0, 0, 0, NOW(), NOW()
            )
            """
        ),
        {
            "group_id": group_id,
            "card_key": card_key,
            "card_value": card_value or None,
            "extra_info": str(body.get("extraInfo") or "").strip() or None,
        },
    )
    await _refresh_card_group_counts(db, group_id)
    await db.commit()
    return ResultObject.success(None, "卡密已创建")


@router.post("/cards/{group_id}/items/batch", response_model=ResultObject)
async def batch_create_card_items(
    group_id: int,
    body: dict[str, Any] = Body(default_factory=dict),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    items = body.get("items")
    if not isinstance(items, list) or not items:
        return ResultObject.validate_failed("导入列表不能为空")

    success_count = 0
    duplicate_count = 0
    fail_count = 0

    for raw_item in items:
        if not isinstance(raw_item, dict):
            fail_count += 1
            continue
        content = str(raw_item.get("content") or "").strip()
        card_key = str(raw_item.get("cardContent") or "").strip()
        card_value = str(raw_item.get("password") or "").strip()
        if not card_key and content:
            merged = content.split("----", 1)
            card_key = merged[0].strip()
            card_value = merged[1].strip() if len(merged) > 1 else card_value
        if not card_key:
            fail_count += 1
            continue

        exists = (
            await db.execute(
                text(
                    """
                    SELECT id
                    FROM card_item
                    WHERE group_id = :group_id
                      AND deleted = 0
                      AND card_key = :card_key
                      AND COALESCE(card_value, '') = :card_value
                    LIMIT 1
                    """
                ),
                {
                    "group_id": group_id,
                    "card_key": card_key,
                    "card_value": card_value,
                },
            )
        ).mappings().first()
        if exists:
            duplicate_count += 1
            continue

        await db.execute(
            text(
                """
                INSERT INTO card_item(
                    group_id, card_key, card_value, extra_info, status, is_used, deleted, created_time, updated_time
                ) VALUES(
                    :group_id, :card_key, :card_value, :extra_info, 0, 0, 0, NOW(), NOW()
                )
                """
            ),
            {
                "group_id": group_id,
                "card_key": card_key,
                "card_value": card_value or None,
                "extra_info": str(raw_item.get("extraInfo") or "").strip() or None,
            },
        )
        success_count += 1

    await _refresh_card_group_counts(db, group_id)
    await db.commit()
    return ResultObject.success(
        {
            "successCount": success_count,
            "duplicateCount": duplicate_count,
            "failCount": fail_count,
        }
    )


@router.delete("/cards/{group_id}/items/{item_id}", response_model=ResultObject)
async def delete_card_item(
    group_id: int,
    item_id: int,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    await db.execute(
        text(
            """
            UPDATE card_item
            SET deleted = 1, updated_time = NOW()
            WHERE id = :item_id
              AND group_id = :group_id
            """
        ),
        {"group_id": group_id, "item_id": item_id},
    )
    await _refresh_card_group_counts(db, group_id)
    await db.commit()
    return ResultObject.success(None, "卡密已删除")


@router.post("/cards/{group_id}/items/{item_id}/reset", response_model=ResultObject)
async def reset_card_item(
    group_id: int,
    item_id: int,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    await db.execute(
        text(
            """
            UPDATE card_item
            SET status = 0,
                is_used = 0,
                used_order_id = NULL,
                used_by_order_id = NULL,
                used_time = NULL,
                updated_time = NOW()
            WHERE id = :item_id
              AND group_id = :group_id
              AND deleted = 0
            """
        ),
        {"group_id": group_id, "item_id": item_id},
    )
    await _refresh_card_group_counts(db, group_id)
    await db.commit()
    return ResultObject.success(None, "卡密已重置")


@router.post("/cards/{group_id}/items/{item_id}/lock", response_model=ResultObject)
async def lock_card_item(
    group_id: int,
    item_id: int,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    await db.execute(
        text(
            """
            UPDATE card_item
            SET status = 1,
                updated_time = NOW()
            WHERE id = :item_id
              AND group_id = :group_id
              AND deleted = 0
            """
        ),
        {"group_id": group_id, "item_id": item_id},
    )
    await _refresh_card_group_counts(db, group_id)
    await db.commit()
    return ResultObject.success(None, "卡密已锁定")


@router.post("/cards/{group_id}/items/{item_id}/invalid", response_model=ResultObject)
async def invalid_card_item(
    group_id: int,
    item_id: int,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    await db.execute(
        text(
            """
            UPDATE card_item
            SET status = 3,
                updated_time = NOW()
            WHERE id = :item_id
              AND group_id = :group_id
              AND deleted = 0
            """
        ),
        {"group_id": group_id, "item_id": item_id},
    )
    await _refresh_card_group_counts(db, group_id)
    await db.commit()
    return ResultObject.success(None, "卡密已作废")


@router.get("/cards/{group_id}/stats", response_model=ResultObject)
async def get_card_stock_stats(
    group_id: int,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    row = (
        await db.execute(
            text(
                """
                SELECT
                    COUNT(*) AS total_count,
                    COALESCE(SUM(CASE WHEN COALESCE(status, CASE WHEN COALESCE(is_used, 0) = 1 THEN 2 ELSE 0 END) = 0 THEN 1 ELSE 0 END), 0) AS remain_count,
                    COALESCE(SUM(CASE WHEN COALESCE(status, CASE WHEN COALESCE(is_used, 0) = 1 THEN 2 ELSE 0 END) = 1 THEN 1 ELSE 0 END), 0) AS locked_count,
                    COALESCE(SUM(CASE WHEN COALESCE(status, CASE WHEN COALESCE(is_used, 0) = 1 THEN 2 ELSE 0 END) = 2 THEN 1 ELSE 0 END), 0) AS used_count,
                    COALESCE(SUM(CASE WHEN COALESCE(status, CASE WHEN COALESCE(is_used, 0) = 1 THEN 2 ELSE 0 END) = 3 THEN 1 ELSE 0 END), 0) AS invalid_count,
                    COALESCE(SUM(CASE WHEN COALESCE(status, CASE WHEN COALESCE(is_used, 0) = 1 THEN 2 ELSE 0 END) = 4 THEN 1 ELSE 0 END), 0) AS error_count
                FROM card_item
                WHERE group_id = :group_id
                  AND deleted = 0
                """
            ),
            {"group_id": group_id},
        )
    ).mappings().first()
    data = dict(row or {})
    return ResultObject.success(
        {
            "totalCount": _to_int(data.get("total_count")),
            "remainCount": _to_int(data.get("remain_count")),
            "lockedCount": _to_int(data.get("locked_count")),
            "usedCount": _to_int(data.get("used_count")),
            "invalidCount": _to_int(data.get("invalid_count")),
            "errorCount": _to_int(data.get("error_count")),
        }
    )


@router.get("/cards/{group_id}/usage", response_model=ResultObject)
async def get_card_usage_records(
    group_id: int,
    current: int = Query(default=1),
    size: int = Query(default=20),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    safe_current = max(current, 1)
    safe_size = min(max(size, 1), 200)
    offset = (safe_current - 1) * safe_size
    params = {"group_id": group_id, "offset": offset, "limit": safe_size}
    where_sql = (
        "group_id = :group_id AND deleted = 0 "
        "AND (COALESCE(status, CASE WHEN COALESCE(is_used, 0) = 1 THEN 2 ELSE 0 END) = 2 "
        "OR used_order_id IS NOT NULL OR used_by_order_id IS NOT NULL)"
    )

    total = (
        await db.execute(
            text(f"SELECT COUNT(*) FROM card_item WHERE {where_sql}"),
            {"group_id": group_id},
        )
    ).scalar() or 0
    rows = (
        await db.execute(
            text(
                f"""
                SELECT
                    id, group_id, card_key, card_value, extra_info, status, is_used,
                    used_order_id, used_by_order_id, used_time, remark, created_time, updated_time
                FROM card_item
                WHERE {where_sql}
                ORDER BY used_time DESC, id DESC
                LIMIT :offset, :limit
                """
            ),
            params,
        )
    ).mappings().all()
    records = [_card_item_record(dict(row)) for row in rows]
    return ResultObject.success(_page_payload(records, _to_int(total), safe_current, safe_size))


@router.get("/cards/{group_id}/export")
async def export_card_items(
    group_id: int,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    rows = (
        await db.execute(
            text(
                """
                SELECT card_key, card_value
                FROM card_item
                WHERE group_id = :group_id
                  AND deleted = 0
                ORDER BY id ASC
                """
            ),
            {"group_id": group_id},
        )
    ).mappings().all()
    content = "\n".join(_merge_card_content(row.get("card_key"), row.get("card_value")) for row in rows)
    return Response(
        content=content.encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="cards-{group_id}.txt"'},
    )


@router.get("/auto-delivery/stats", response_model=ResultObject)
async def get_delivery_stats(
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    start_time, end_time = _current_date_range()
    today_success = (
        await db.execute(
            text(
                """
                SELECT COUNT(*)
                FROM delivery_record
                WHERE deleted = 0
                  AND created_time BETWEEN :start_time AND :end_time
                  AND (
                    COALESCE(delivery_status, '') = 'success'
                    OR COALESCE(status, 0) = 2
                  )
                """
            ),
            {"start_time": start_time, "end_time": end_time},
        )
    ).scalar() or 0
    today_fail = (
        await db.execute(
            text(
                """
                SELECT COUNT(*)
                FROM delivery_record
                WHERE deleted = 0
                  AND created_time BETWEEN :start_time AND :end_time
                  AND (
                    COALESCE(delivery_status, '') = 'failed'
                    OR COALESCE(status, 0) IN (3, 6, 7)
                  )
                """
            ),
            {"start_time": start_time, "end_time": end_time},
        )
    ).scalar() or 0
    pending_orders = (
        await db.execute(
            text(
                """
                SELECT COUNT(*)
                FROM delivery_record
                WHERE deleted = 0
                  AND (
                    COALESCE(delivery_status, '') = 'pending'
                    OR COALESCE(status, 0) IN (0, 1, 5)
                  )
                """
            )
        )
    ).scalar() or 0

    goods_rows = (
        await db.execute(
            text(
                f"""
                SELECT id
                FROM xianyu_goods g
                WHERE deleted = 0 AND {VALID_ACCOUNT_FILTER}
                """
            )
        )
    ).mappings().all()
    existing_goods_ids = {_to_int(row.get("id")) for row in goods_rows if _to_int(row.get("id"))}

    configs = await _load_all_goods_configs(db)
    enabled_goods = 0
    low_stock_goods = 0
    group_rows = (
        await db.execute(
            text("SELECT id, COALESCE(available_count, 0) AS available_count, COALESCE(alert_threshold, 10) AS alert_threshold FROM card_group WHERE deleted = 0")
        )
    ).mappings().all()
    group_map = {row["id"]: {"available": _to_int(row.get("available_count")), "threshold": _to_int(row.get("alert_threshold"), 10)} for row in group_rows}

    for goods_id, config in configs.items():
        if goods_id not in existing_goods_ids:
            continue
        enabled_timings = _enabled_timing_configs(config)
        if enabled_timings:
            enabled_goods += 1
        low_stock = False
        for timing_config in enabled_timings:
            if str(timing_config.get("mode") or "").lower() != "card":
                continue
            group_id = _to_int(timing_config.get("cardGroupId"))
            if not group_id or group_id not in group_map:
                continue
            group_info = group_map[group_id]
            threshold = _to_int(timing_config.get("alertThreshold"), group_info["threshold"])
            if group_info["available"] < threshold:
                low_stock = True
                break
        if low_stock:
            low_stock_goods += 1

    return ResultObject.success(
        {
            "todaySuccess": _to_int(today_success),
            "todayFail": _to_int(today_fail),
            "pendingOrders": _to_int(pending_orders),
            "lowStockGoods": low_stock_goods,
            "enabledGoods": enabled_goods,
        }
    )


@router.get("/auto-delivery/goods/{goods_id}/config", response_model=ResultObject)
async def get_goods_delivery_config(
    goods_id: int,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    missing_result = await _require_goods(db, [goods_id])
    if missing_result:
        return missing_result
    return ResultObject.success(await _load_goods_config(db, goods_id))


@router.post("/auto-delivery/goods/configs/query", response_model=ResultObject)
async def query_goods_delivery_configs(
    body: dict[str, Any] = Body(default_factory=dict),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    """Return configurations in one bounded query for large management pages."""
    raw_goods_ids = body.get("goodsIds")
    if not isinstance(raw_goods_ids, list) or not raw_goods_ids:
        return ResultObject.validate_failed("goodsIds 必须是非空数组")
    if len(raw_goods_ids) > 500:
        return ResultObject.validate_failed("单次最多查询 500 个商品配置")

    goods_ids: list[int] = []
    seen: set[int] = set()
    for raw_value in raw_goods_ids:
        if isinstance(raw_value, bool):
            return ResultObject.validate_failed("goodsIds 只能包含正整数")
        try:
            goods_id = int(raw_value)
        except (TypeError, ValueError):
            return ResultObject.validate_failed("goodsIds 只能包含正整数")
        if goods_id <= 0:
            return ResultObject.validate_failed("goodsIds 只能包含正整数")
        if goods_id not in seen:
            seen.add(goods_id)
            goods_ids.append(goods_id)

    goods_rows = await _list_goods_rows(db, goods_ids)
    existing_goods_ids = {_to_int(row.get("id")) for row in goods_rows}
    valid_goods_ids = [goods_id for goods_id in goods_ids if goods_id in existing_goods_ids]
    configs = await _load_all_goods_configs(db, valid_goods_ids)
    records = [
        {"goodsId": goods_id, "config": configs.get(goods_id, {})}
        for goods_id in valid_goods_ids
    ]
    missing_goods_ids = [goods_id for goods_id in goods_ids if goods_id not in existing_goods_ids]
    return ResultObject.success(
        {
            "records": records,
            "count": len(records),
            "missingGoodsIds": missing_goods_ids,
        }
    )


@router.put("/auto-delivery/goods/{goods_id}/config", response_model=ResultObject)
async def save_goods_delivery_config(
    goods_id: int,
    body: dict[str, Any] = Body(default_factory=dict),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    missing_result = await _require_goods(db, [goods_id])
    if missing_result:
        return missing_result
    timing = str(body.get("timing") or "payDelivery").strip()
    if timing not in CONFIG_TIMINGS:
        return ResultObject.validate_failed("未知的发货时机")

    source_row: dict[str, Any] | None = None
    if "sourceId" in body and body.get("sourceId") not in ("", None):
        try:
            source_id = _positive_source_id(body.get("sourceId"))
        except ValueError as exc:
            return ResultObject.validate_failed(str(exc))
        source_row = await _load_delivery_source_row(db, source_id, for_update=True)
        if not source_row:
            await db.rollback()
            return ResultObject.failed("货源不存在或已删除", code=404)

    config = await _load_goods_config(db, goods_id)
    timing_config = dict(config.get(timing) or {})
    for key in (
        "enabled",
        "mode",
        "sourceId",
        "sourceTitle",
        "cardGroupId",
        "cardTemplate",
        "header",
        "content",
        "footer",
        "segmentSend",
        "retryCount",
        "alertThreshold",
        "autoDisableOnLowStock",
        "autoConfirmShipment",
    ):
        if key not in body:
            continue
        value = body.get(key)
        if value in ("", None) and key in {"sourceId", "sourceTitle", "cardGroupId"}:
            timing_config.pop(key, None)
        else:
            timing_config[key] = value
    if source_row:
        timing_config["sourceId"] = source_row["id"]
        timing_config["sourceTitle"] = source_row.get("title") or ""
    if "enabled" not in timing_config:
        timing_config["enabled"] = 1
    config[timing] = timing_config
    account_id = body.get("accountId")
    if account_id is not None:
        config["accountId"] = _to_int(account_id)
    await _upsert_goods_config(db, goods_id, config)
    await db.commit()
    return ResultObject.success(config, "商品发货配置已保存")


@router.patch("/auto-delivery/goods/{goods_id}/config/{timing}", response_model=ResultObject)
async def toggle_goods_delivery_config(
    goods_id: int,
    timing: str,
    body: dict[str, Any] = Body(default_factory=dict),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    missing_result = await _require_goods(db, [goods_id])
    if missing_result:
        return missing_result
    if timing not in CONFIG_TIMINGS:
        return ResultObject.validate_failed("未知的发货时机")
    config = await _load_goods_config(db, goods_id)
    timing_config = dict(config.get(timing) or {})
    timing_config["enabled"] = 1 if _truthy(body.get("enabled")) else 0
    config[timing] = timing_config
    await _upsert_goods_config(db, goods_id, config)
    await db.commit()
    return ResultObject.success(config, "配置状态已更新")


@router.post("/auto-delivery/rules/batch", response_model=ResultObject)
async def batch_set_delivery_rules(
    body: dict[str, Any] = Body(default_factory=dict),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    try:
        goods_ids = _normalize_goods_ids(body.get("goodsIds"))
    except ValueError as exc:
        return ResultObject.validate_failed(str(exc))
    timing = str(body.get("timing") or "payDelivery").strip()
    if timing not in CONFIG_TIMINGS:
        return ResultObject.validate_failed("未知的发货时机")

    missing_result = await _require_goods(db, goods_ids)
    if missing_result:
        return missing_result

    source_row: dict[str, Any] | None = None
    if body.get("sourceId") not in ("", None):
        try:
            source_id = _positive_source_id(body.get("sourceId"))
        except ValueError as exc:
            return ResultObject.validate_failed(str(exc))
        source_row = await _load_delivery_source_row(db, source_id, for_update=True)
        if not source_row:
            await db.rollback()
            return ResultObject.failed("货源不存在或已删除", code=404)

    for goods_id in goods_ids:
        config = await _load_goods_config(db, goods_id)
        timing_config = dict(config.get(timing) or {})
        timing_config["enabled"] = 1 if _truthy(body.get("enabled", 1)) else 0
        if body.get("mode"):
            timing_config["mode"] = body.get("mode")
        if source_row:
            timing_config["sourceId"] = source_row["id"]
            timing_config["sourceTitle"] = source_row.get("title") or ""
        if body.get("cardGroupId") is not None:
            timing_config["cardGroupId"] = body.get("cardGroupId")
        config[timing] = timing_config
        await _upsert_goods_config(db, goods_id, config)

    await db.commit()
    return ResultObject.success({"updatedCount": len(goods_ids)}, "批量配置已保存")


@router.post("/auto-delivery/rules/batch-delete", response_model=ResultObject)
async def batch_delete_delivery_rules(
    body: dict[str, Any] = Body(default_factory=dict),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    try:
        goods_ids = _normalize_goods_ids(body.get("goodsIds"))
    except ValueError as exc:
        return ResultObject.validate_failed(str(exc))
    missing_result = await _require_goods(db, goods_ids)
    if missing_result:
        return missing_result
    for goods_id in goods_ids:
        await _clear_goods_config(db, goods_id)
    await db.commit()
    return ResultObject.success({"updatedCount": len(goods_ids)}, "批量删除已完成")


@router.post("/auto-delivery/rules/apply-all", response_model=ResultObject)
async def apply_delivery_rules_to_all(
    body: dict[str, Any] = Body(default_factory=dict),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    """将指定商品的发货配置应用到所有在售商品。"""
    source_goods_id = body.get("goodsId") or body.get("sourceGoodsId")
    if not source_goods_id:
        return ResultObject.validate_failed("goodsId 不能为空")
    try:
        source_goods_id = _normalize_goods_ids([source_goods_id], maximum=1)[0]
    except ValueError as exc:
        return ResultObject.validate_failed(str(exc))
    missing_result = await _require_goods(db, [source_goods_id])
    if missing_result:
        return missing_result
    source_row = (
        await db.execute(
            text(
                "SELECT config_json FROM delivery_goods_config WHERE goods_id = :gid AND deleted = 0 LIMIT 1"
            ),
            {"gid": source_goods_id},
        )
    ).first()
    if not source_row or not source_row[0]:
        return ResultObject.failed("源商品发货配置不存在")
    source_config = source_row[0]
    target_result = await db.execute(
        text(f"SELECT id FROM xianyu_goods g WHERE deleted = 0 AND {VALID_ACCOUNT_FILTER} AND status = 1 AND id != :gid"),
        {"gid": source_goods_id},
    )
    target_ids = [row[0] for row in target_result.fetchall()]
    applied_count = 0
    now = dt.datetime.now()
    for tid in target_ids:
        await db.execute(
            text(
                """
                INSERT INTO delivery_goods_config (goods_id, config_json, created_time, updated_time)
                VALUES (:gid, :cfg, :now, :now)
                ON DUPLICATE KEY UPDATE config_json = VALUES(config_json), updated_time = VALUES(updated_time)
                """
            ),
            {"gid": tid, "cfg": source_config, "now": now},
        )
        applied_count += 1
    await db.commit()
    return ResultObject.success({"appliedCount": applied_count}, f"已应用到 {applied_count} 个商品")


@router.get("/auto-delivery/statement", response_model=ResultObject)
async def get_delivery_statement(
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    row = (
        await db.execute(
            text(
                """
                SELECT enabled, content, scope
                FROM delivery_statement
                WHERE deleted = 0
                ORDER BY id DESC
                LIMIT 1
                """
            )
        )
    ).mappings().first()
    if not row:
        return ResultObject.success({"enabled": False, "content": STATEMENT_DEFAULT_CONTENT, "scope": "all"})
    return ResultObject.success(
        {
            "enabled": _truthy(row.get("enabled")),
            "content": row.get("content") or STATEMENT_DEFAULT_CONTENT,
            "scope": row.get("scope") or "all",
        }
    )


@router.put("/auto-delivery/statement", response_model=ResultObject)
async def save_delivery_statement(
    body: dict[str, Any] = Body(default_factory=dict),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    enabled = 1 if _truthy(body.get("enabled")) else 0
    content = str(body.get("content") or STATEMENT_DEFAULT_CONTENT)
    scope = str(body.get("scope") or "all")
    existing = (
        await db.execute(
            text(
                """
                SELECT id
                FROM delivery_statement
                WHERE deleted = 0
                ORDER BY id DESC
                LIMIT 1
                """
            )
        )
    ).mappings().first()
    if existing:
        await db.execute(
            text(
                """
                UPDATE delivery_statement
                SET enabled = :enabled,
                    content = :content,
                    scope = :scope,
                    updated_time = NOW()
                WHERE id = :statement_id
                """
            ),
            {
                "statement_id": existing["id"],
                "enabled": enabled,
                "content": content,
                "scope": scope,
            },
        )
    else:
        await db.execute(
            text(
                """
                INSERT INTO delivery_statement(enabled, content, scope, deleted, created_time, updated_time)
                VALUES(:enabled, :content, :scope, 0, NOW(), NOW())
                """
            ),
            {"enabled": enabled, "content": content, "scope": scope},
        )
    await db.commit()
    return ResultObject.success(None, "发货声明已保存")


@router.patch("/auto-delivery/statement/toggle", response_model=ResultObject)
async def toggle_delivery_statement(
    body: dict[str, Any] = Body(default_factory=dict),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    enabled = 1 if _truthy(body.get("enabled")) else 0
    existing = (
        await db.execute(
            text(
                """
                SELECT id, content, scope
                FROM delivery_statement
                WHERE deleted = 0
                ORDER BY id DESC
                LIMIT 1
                """
            )
        )
    ).mappings().first()
    if existing:
        await db.execute(
            text(
                """
                UPDATE delivery_statement
                SET enabled = :enabled,
                    updated_time = NOW()
                WHERE id = :statement_id
                """
            ),
            {"statement_id": existing["id"], "enabled": enabled},
        )
    else:
        await db.execute(
            text(
                """
                INSERT INTO delivery_statement(enabled, content, scope, deleted, created_time, updated_time)
                VALUES(:enabled, :content, :scope, 0, NOW(), NOW())
                """
            ),
            {"enabled": enabled, "content": STATEMENT_DEFAULT_CONTENT, "scope": "all"},
        )
    await db.commit()
    return ResultObject.success(None, "发货声明状态已更新")


@router.post("/auto-delivery/statement/preview", response_model=ResultObject)
async def preview_delivery_statement(
    body: dict[str, Any] = Body(default_factory=dict),
    _: dict = Depends(get_current_user),
):
    preview = str(body.get("content") or STATEMENT_DEFAULT_CONTENT)
    preview = preview.replace("{订单编号}", "【订单编号】")
    preview = preview.replace("{商品标题}", "【商品标题】")
    preview = preview.replace("{买家昵称}", "【买家昵称】")
    preview = preview.replace("{发货确认链接}", "【发货确认链接】")
    return ResultObject.success({"preview": preview})


# ============================================================
# 发货声明会话（卖家手动处理）—— 发货记录页"声明会话"tab 使用
# ============================================================
ALLOWED_STATEMENT_SESSION_STATUSES = ("declaring", "waiting", "confirmed", "cancelled")
BUYER_CANCEL_REPLY_TEXT = "已为您转人工客服，请耐心等待，客服会尽快与您联系处理退款事宜。"


def _statement_session_record(row: dict[str, Any]) -> dict[str, Any]:
    """将 delivery_statement_session 行标准化为前端字段（camelCase）。"""
    return {
        "id": _to_int(row.get("id")),
        "accountId": _to_int(row.get("account_id")),
        "accountNickname": row.get("account_nickname") or "",
        "orderId": row.get("order_id") or "",
        "buyerId": row.get("buyer_id") or "",
        "buyerNick": row.get("buyer_nick") or "",
        "xyGoodsId": row.get("xy_goods_id") or "",
        "goodsTitle": row.get("goods_title") or "",
        "sId": row.get("s_id") or "",
        "pnmId": row.get("pnm_id") or "",
        "statementContent": row.get("statement_content") or "",
        "status": row.get("status") or "waiting",
        "confirmSource": row.get("confirm_source") or "",
        "cancelSource": row.get("cancel_source") or "",
        "sentAt": _format_datetime(row.get("sent_at")),
        "confirmedAt": _format_datetime(row.get("confirmed_at")),
        "cancelledAt": _format_datetime(row.get("cancelled_at")),
        "createdTime": _format_datetime(row.get("created_time")),
    }


@router.get("/auto-delivery/statement/sessions", response_model=ResultObject)
async def list_delivery_statement_sessions(
    status: str = Query(default="", max_length=20),
    accountId: int = Query(default=0, ge=0),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    """分页查询发货声明会话（发货记录页"声明会话"tab）。"""
    normalized_status = (status or "").strip()
    if normalized_status and normalized_status not in ALLOWED_STATEMENT_SESSION_STATUSES:
        return ResultObject.failed("status 仅支持 declaring/waiting/confirmed/cancelled", code=400)
    safe_page = max(page, 1)
    safe_size = min(max(size, 1), 100)
    offset = (safe_page - 1) * safe_size

    params: dict[str, Any] = {}
    where_sql = ["s.deleted = 0"]
    if normalized_status:
        where_sql.append("s.status = :status")
        params["status"] = normalized_status
    if accountId > 0:
        where_sql.append("s.account_id = :account_id")
        params["account_id"] = accountId
    # 仅展示仍有效的账号（已退出账号的旧会话不在前台展示）
    where_sql.append(
        "EXISTS (SELECT 1 FROM xianyu_account a WHERE a.id = s.account_id AND a.deleted = 0)"
    )

    total = (
        await db.execute(
            text(f"SELECT COUNT(*) FROM delivery_statement_session s WHERE {' AND '.join(where_sql)}"),
            params,
        )
    ).scalar() or 0
    params.update({"offset": offset, "limit": safe_size})
    rows = (
        await db.execute(
            text(
                f"""
                SELECT s.id, s.account_id, s.order_id, s.buyer_id, s.buyer_nick,
                       s.xy_goods_id, s.goods_title, s.s_id, s.pnm_id,
                       s.statement_content, s.status, s.confirm_source, s.cancel_source,
                       s.sent_at, s.confirmed_at, s.cancelled_at, s.created_time, s.updated_time,
                       a.nickname AS account_nickname
                FROM delivery_statement_session s
                LEFT JOIN xianyu_account a ON a.id = s.account_id AND a.deleted = 0
                WHERE {' AND '.join(where_sql)}
                ORDER BY s.created_time DESC, s.id DESC
                LIMIT :offset, :limit
                """
            ),
            params,
        )
    ).mappings().all()
    records = [_statement_session_record(dict(row)) for row in rows]
    return ResultObject.success(_page_payload(records, _to_int(total), safe_page, safe_size))


async def _load_statement_session(db: AsyncSession, session_id: int) -> dict[str, Any] | None:
    row = (
        await db.execute(
            text(
                """
                SELECT id, account_id, order_id, buyer_id, buyer_nick,
                       xy_goods_id, goods_title, s_id, pnm_id, statement_content, status
                FROM delivery_statement_session
                WHERE id = :session_id AND deleted = 0
                LIMIT 1
                """
            ),
            {"session_id": session_id},
        )
    ).mappings().first()
    return dict(row) if row else None


@router.post("/auto-delivery/statement/sessions/{session_id}/confirm", response_model=ResultObject)
async def confirm_delivery_statement_session(
    session_id: int,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    """卖家手动确认声明会话 → 触发该订单发货。"""
    session = await _load_statement_session(db, session_id)
    if not session:
        return ResultObject.failed("声明会话不存在或已删除", code=404)
    if session["status"] != "waiting":
        return ResultObject.failed("当前会话状态不支持确认（仅等待买家确认状态可操作）", code=409)

    result = await db.execute(
        text(
            """
            UPDATE delivery_statement_session
            SET status='confirmed', confirmed_at=NOW(), confirm_source='seller',
                updated_time=NOW()
            WHERE id=:id AND status='waiting'
            """
        ),
        {"id": session_id},
    )
    if result.rowcount == 0:
        await db.rollback()
        return ResultObject.failed("会话状态已变更，请刷新后重试", code=409)

    # 触发发货（复用 ws_delivery_handler 的声明确认发货流程，幂等由 event_key 保证）
    try:
        from ....services.ws_delivery_handler import _trigger_delivery_for_confirmed_statement

        await _trigger_delivery_for_confirmed_statement(
            db,
            _to_int(session["account_id"]),
            order_id=str(session.get("order_id") or "") or None,
            xy_goods_id=str(session.get("xy_goods_id") or ""),
            buyer_user_id=str(session.get("buyer_id") or ""),
            s_id=str(session.get("s_id") or ""),
            goods_title=str(session.get("goods_title") or ""),
            session_id_stmt=session_id,
        )
    except Exception as exc:
        logger.error(
            "卖家手动确认声明后触发发货失败 sessionId=%d errorType=%s",
            session_id, type(exc).__name__, exc_info=True,
        )
        # 不抛出：会话已 confirmed，发货由 delivery_record 失败重试机制接管
    await db.commit()
    return ResultObject.success(None, "已确认会话并触发发货")


@router.post("/auto-delivery/statement/sessions/{session_id}/cancel", response_model=ResultObject)
async def cancel_delivery_statement_session(
    session_id: int,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    """卖家手动取消声明会话 → 通知买家 + 不发货。"""
    session = await _load_statement_session(db, session_id)
    if not session:
        return ResultObject.failed("声明会话不存在或已删除", code=404)
    if session["status"] != "waiting":
        return ResultObject.failed("当前会话状态不支持取消（仅等待买家确认状态可操作）", code=409)

    result = await db.execute(
        text(
            """
            UPDATE delivery_statement_session
            SET status='cancelled', cancelled_at=NOW(), cancel_source='seller',
                updated_time=NOW()
            WHERE id=:id AND status='waiting'
            """
        ),
        {"id": session_id},
    )
    if result.rowcount == 0:
        await db.rollback()
        return ResultObject.failed("会话状态已变更，请刷新后重试", code=409)

    # 向买家发送取消提示（失败不影响主流程）
    try:
        from ....services.ws_statement_handler import _send_statement_message

        await _send_statement_message(
            _to_int(session["account_id"]),
            str(session.get("s_id") or ""),
            str(session.get("buyer_id") or ""),
            BUYER_CANCEL_REPLY_TEXT,
        )
    except Exception as exc:
        logger.warning(
            "卖家取消声明后向买家发送提示失败 sessionId=%d errorType=%s",
            session_id, type(exc).__name__,
        )
    await db.commit()
    return ResultObject.success(None, "已取消会话并通知买家")


@router.get("/auto-delivery/sources", response_model=ResultObject)
async def get_delivery_sources(
    keyword: str = Query(default="", max_length=200),
    current: int = Query(default=1, ge=1, le=1_000_000),
    size: int = Query(default=20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    safe_current = max(current, 1)
    safe_size = min(max(size, 1), 200)
    offset = (safe_current - 1) * safe_size
    params: dict[str, Any] = {}
    where_sql = ["s.deleted = 0"]
    if keyword.strip():
        where_sql.append("(s.title LIKE :keyword OR s.content LIKE :keyword OR s.remark LIKE :keyword)")
        params["keyword"] = f"%{keyword.strip()}%"

    total = (
        await db.execute(
            text(f"SELECT COUNT(*) FROM delivery_text_source s WHERE {' AND '.join(where_sql)}"),
            params,
        )
    ).scalar() or 0
    params.update({"offset": offset, "limit": safe_size})
    rows = (
        await db.execute(
            text(
                f"""
                SELECT s.id, s.title, s.content, s.remark,
                       s.source_type, s.delivery_mode, s.card_group_id,
                       s.segments,
                       s.created_time, s.updated_time,
                       g.group_name AS card_group_name,
                       g.available_count AS card_remain_count
                FROM delivery_text_source s
                LEFT JOIN card_group g ON g.id = s.card_group_id AND g.deleted = 0
                WHERE {' AND '.join(where_sql)}
                ORDER BY s.updated_time DESC, s.id DESC
                LIMIT :offset, :limit
                """
            ),
            params,
        )
    ).mappings().all()

    configs = await _load_all_goods_configs(db)
    usage_map: dict[int, int] = {}
    for config in configs.values():
        for source_id in _matching_source_ids(config):
            usage_map[source_id] = usage_map.get(source_id, 0) + 1

    records = [_source_record(dict(row), usage_map.get(_to_int(row["id"]), 0)) for row in rows]
    return ResultObject.success(_page_payload(records, _to_int(total), safe_current, safe_size))


@router.get("/auto-delivery/sources/{source_id}", response_model=ResultObject)
async def get_delivery_source_detail(
    source_id: int,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    row = (
        await db.execute(
            text(
                """
                SELECT id, title, content, remark, segments, created_time, updated_time
                FROM delivery_text_source
                WHERE id = :source_id
                  AND deleted = 0
                LIMIT 1
                """
            ),
            {"source_id": source_id},
        )
    ).mappings().first()
    if not row:
        return ResultObject.failed("货源不存在", code=404)
    configs = await _load_all_goods_configs(db)
    usage_count = sum(1 for config in configs.values() if _goods_uses_source(config, source_id))
    return ResultObject.success(_source_record(dict(row), usage_count))


@router.post("/auto-delivery/sources", response_model=ResultObject)
async def create_delivery_source(
    body: dict[str, Any] = Body(default_factory=dict),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    try:
        fields = _delivery_source_fields(body)
    except ValueError as exc:
        return ResultObject.validate_failed(str(exc))
    await db.execute(
        text(
            """
            INSERT INTO delivery_text_source(
                title, content, remark, source_type, delivery_mode, card_group_id, segments,
                deleted, created_time, updated_time
            )
            VALUES(:title, :content, :remark, 'text', :delivery_mode, :card_group_id, :segments, 0, NOW(), NOW())
            """
        ),
        {
            "title": fields["title"] or fields["content"][:50] or "未命名货源",
            "content": fields["content"] or None,
            "remark": fields["remark"] or None,
            "delivery_mode": fields["delivery_mode"],
            "card_group_id": fields["card_group_id"],
            "segments": json.dumps(fields["segments"], ensure_ascii=False) if fields.get("segments") else None,
        },
    )
    await db.commit()
    return ResultObject.success(None, "货源已新增")


@router.put("/auto-delivery/sources/{source_id}", response_model=ResultObject)
async def update_delivery_source(
    source_id: int,
    body: dict[str, Any] = Body(default_factory=dict),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    try:
        fields = _delivery_source_fields(body)
    except ValueError as exc:
        return ResultObject.validate_failed(str(exc))
    existing = await _load_delivery_source_row(db, source_id, for_update=True)
    if not existing:
        await db.rollback()
        return ResultObject.failed("货源不存在或已删除", code=404)
    result = await db.execute(
        text(
            """
            UPDATE delivery_text_source
            SET title = :title,
                content = :content,
                remark = :remark,
                delivery_mode = :delivery_mode,
                card_group_id = :card_group_id,
                segments = :segments,
                updated_time = NOW()
            WHERE id = :source_id
              AND deleted = 0
            """
        ),
        {
            "source_id": source_id,
            "title": fields["title"] or fields["content"][:50] or "未命名货源",
            "content": fields["content"] or None,
            "remark": fields["remark"] or None,
            "delivery_mode": fields["delivery_mode"],
            "card_group_id": fields["card_group_id"],
            "segments": json.dumps(fields["segments"], ensure_ascii=False) if fields.get("segments") else None,
        },
    )
    # MySQL commonly reports rowcount=0 for an idempotent update. Existence was
    # established under FOR UPDATE above, so zero affected rows is still a
    # truthful successful save rather than a false 404.
    del result
    await db.commit()
    return ResultObject.success(None, "货源已更新")


@router.delete("/auto-delivery/sources/{source_id}", response_model=ResultObject)
async def delete_delivery_source(
    source_id: int,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    existing = await _load_delivery_source_row(db, source_id, for_update=True)
    if not existing:
        await db.rollback()
        return ResultObject.failed("货源不存在或已删除", code=404)
    configs = await _load_all_goods_configs(db)
    usage_count = sum(1 for config in configs.values() if _goods_uses_source(config, source_id))
    if usage_count:
        await db.rollback()
        return ResultObject.failed(
            f"货源仍被 {usage_count} 个商品使用，请先解除配置后再删除",
            code=409,
        )
    result = await db.execute(
        text(
            """
            UPDATE delivery_text_source
            SET deleted = 1,
                updated_time = NOW()
            WHERE id = :source_id
              AND deleted = 0
            """
        ),
        {"source_id": source_id},
    )
    del result
    await db.commit()
    return ResultObject.success(None, "货源已删除")


@router.get("/auto-delivery/sources/{source_id}/goods", response_model=ResultObject)
async def get_delivery_source_goods(
    source_id: int,
    configured_current: int = Query(
        default=1,
        alias="configuredCurrent",
        ge=1,
        le=1_000_000,
    ),
    configured_size: int = Query(
        default=20,
        alias="configuredSize",
        ge=1,
        le=SOURCE_GOODS_PAGE_MAX_SIZE,
    ),
    configured_keyword: str = Query(
        default="",
        alias="configuredKeyword",
        max_length=200,
    ),
    candidate_current: int = Query(
        default=1,
        alias="candidateCurrent",
        ge=1,
        le=1_000_000,
    ),
    candidate_size: int = Query(
        default=20,
        alias="candidateSize",
        ge=1,
        le=SOURCE_GOODS_PAGE_MAX_SIZE,
    ),
    candidate_keyword: str = Query(
        default="",
        alias="candidateKeyword",
        max_length=200,
    ),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    source_row = await _load_delivery_source_row(db, source_id)
    if not source_row:
        return ResultObject.failed("货源不存在", code=404)

    configured_page = await _list_source_goods_page(
        db,
        source_id,
        current=configured_current,
        size=configured_size,
        keyword=configured_keyword,
        configured_only=True,
    )
    candidate_page = await _list_source_goods_page(
        db,
        source_id,
        current=candidate_current,
        size=candidate_size,
        keyword=candidate_keyword,
        configured_only=False,
    )
    usage_count = (
        configured_page["total"]
        if not configured_keyword.strip()
        else await _count_source_goods(db, source_id)
    )
    all_goods_total = (
        candidate_page["total"]
        if not candidate_keyword.strip()
        else await _count_goods_rows(db)
    )
    return ResultObject.success(
        {
            "source": _source_record(dict(source_row), usage_count),
            # Compatibility aliases now expose only the requested bounded page.
            "configuredGoods": configured_page["records"],
            "allGoods": candidate_page["records"],
            "configuredGoodsPage": configured_page,
            "allGoodsPage": candidate_page,
            "allGoodsTotal": all_goods_total,
        }
    )


@router.post("/auto-delivery/sources/{source_id}/recommend", response_model=ResultObject)
async def recommend_delivery_source_goods(
    source_id: int,
    candidate_limit: int = Query(
        default=SOURCE_RECOMMEND_CANDIDATE_MAX,
        alias="candidateLimit",
        ge=1,
        le=SOURCE_RECOMMEND_CANDIDATE_MAX,
    ),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    source_row = await _load_delivery_source_row(db, source_id)
    if not source_row:
        return ResultObject.failed("货源不存在", code=404)

    all_goods, candidate_pool_total, keyword_matched_ids = await _list_recommendation_candidate_rows(
        db,
        source_id,
        limit=candidate_limit,
        source_keyword=str(source_row.get("title") or ""),
    )
    usage_count = await _count_source_goods(db, source_id)
    source_payload = dict(source_row)
    source_features = _source_recommend_features(source_payload)
    ranked_candidates = [
        candidate
        for goods in all_goods
        for candidate in [
            _build_source_candidate(
                source_payload,
                goods,
                source_features,
                force_include=_to_int(goods.get("id")) in keyword_matched_ids,
            )
        ]
        if candidate
    ]
    ranked_candidates.sort(
        key=lambda item: (
            -int(item.get("score") or 0),
            item.get("title") or "",
            _to_int(item.get("id")),
        )
    )
    # Goods the user could find via keyword search are provably relevant (the
    # source title keyword appears in their title/category/description/detail).
    # Auto-promote them to recommended so AI推荐 always surfaces at least the
    # same goods a manual search would find — the AI's job is to find
    # additional matches, not to filter out proven keyword matches. Keep their
    # original confidence/score so the UI shows accurate match strength.
    if keyword_matched_ids:
        for item in ranked_candidates:
            goods_id = _to_int(item.get("id"))
            if goods_id not in keyword_matched_ids or item.get("recommended"):
                continue
            item["recommended"] = True
            if not item.get("reason") or item["reason"] == "可进一步人工确认是否匹配":
                item["reason"] = "关键词匹配货源标题"
        ranked_candidates.sort(
            key=lambda item: (
                -int(item.get("score") or 0),
                item.get("title") or "",
                _to_int(item.get("id")),
            )
        )
    local_candidates = [item for item in ranked_candidates if item.get("recommended")][:30]

    def recommendation_payload(candidates: list[dict[str, Any]]) -> dict[str, Any]:
        # Recommendation output is intentionally a single bounded snapshot.
        # Large goods tables are paged by the detail endpoint; these at-most-30
        # analyzed rows are paged locally so page changes never call the model
        # again and cannot silently reorder a user's pending selection.
        page = _page_payload(
            candidates,
            len(candidates),
            1,
            SOURCE_RECOMMEND_PAGE_MAX_SIZE,
        )
        return {
            # Older clients expect this key. Recommendation rows now carry
            # their own `configured` flag, avoiding an unbounded side list.
            "configuredGoods": [],
            "candidates": candidates,
            "candidatesPage": page,
            "candidateIds": [_to_int(item.get("id")) for item in candidates],
            "applicableCandidateIds": [
                _to_int(item.get("id"))
                for item in candidates
                if not _truthy(item.get("configured"))
            ],
            "candidatePoolLimit": candidate_limit,
            "candidatePoolSize": len(all_goods),
            "candidatePoolTotal": candidate_pool_total,
            "candidatePoolTruncated": candidate_pool_total > len(all_goods),
        }

    ai_match = await _ai_match_source_goods(
        source_payload,
        ranked_candidates[:SOURCE_RECOMMEND_MODEL_CANDIDATE_MAX],
    )
    if not ai_match.get("configured"):
        return ResultObject.success(
            {
                "source": _source_record(source_payload, usage_count),
                "configured": False,
                "mode": "heuristic",
                "errorCode": ai_match.get("errorCode") or "NOT_CONFIGURED",
                "message": ai_match.get("error") or AI_SETTINGS_HINT,
                **recommendation_payload(local_candidates),
            }
        )

    if not ai_match.get("ok"):
        logger.warning("delivery source ai match fallback: %s", ai_match.get("error"))
        return ResultObject.success(
            {
                "source": _source_record(source_payload, usage_count),
                "configured": True,
                "mode": "provider-fallback",
                "errorCode": ai_match.get("errorCode") or "AI_ERROR",
                "message": "AI 调用失败，已回退为规则匹配候选。",
                **recommendation_payload(local_candidates),
            }
        )

    matched_ids = ai_match.get("matchedIds") or set()
    ai_reasons = ai_match.get("reasons") or {}
    if matched_ids:
        for item in ranked_candidates:
            goods_id = _to_int(item.get("id"))
            if goods_id not in matched_ids:
                continue
            item["recommended"] = True
            item["confidence"] = "high"
            item["score"] = max(int(item.get("score") or 0), 90)
            if ai_reasons.get(goods_id):
                item["reason"] = ai_reasons[goods_id]
        ranked_candidates.sort(
            key=lambda item: (
                -int(item.get("score") or 0),
                item.get("title") or "",
                _to_int(item.get("id")),
            )
        )

    candidates = [item for item in ranked_candidates if item.get("recommended")][:30]
    message = (
        f"AI 已分析并推荐 {len(candidates)} 个高匹配商品"
        if candidates
        else "AI 未找到高匹配商品，请手动确认后再配置。"
    )
    return ResultObject.success(
        {
            "source": _source_record(source_payload, usage_count),
            "configured": True,
            "mode": "provider",
            "message": message,
            **recommendation_payload(candidates),
        }
    )


@router.post("/auto-delivery/sources/{source_id}/apply", response_model=ResultObject)
async def apply_delivery_source_to_goods(
    source_id: int,
    body: dict[str, Any] = Body(default_factory=dict),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    try:
        goods_ids = _normalize_goods_ids(body.get("goodsIds"))
    except ValueError as exc:
        return ResultObject.validate_failed(str(exc))
    timing = str(body.get("timing") or "payDelivery").strip()
    if timing not in CONFIG_TIMINGS:
        return ResultObject.validate_failed("未知的发货时机")
    missing_result = await _require_goods(db, goods_ids)
    if missing_result:
        return missing_result

    # Serialize binding with source deletion. Delete locks the same row before
    # scanning configs, so either this binding commits first and blocks delete,
    # or delete commits first and this lookup fails closed.
    source_row = await _load_delivery_source_row(db, source_id, for_update=True)
    if not source_row:
        await db.rollback()
        return ResultObject.failed("货源不存在", code=404)

    source_delivery_mode = _normalize_source_delivery_mode(source_row.get("delivery_mode"))
    source_card_group_id: int | None = None
    try:
        source_card_group_id = _to_int(source_row.get("card_group_id")) or None
    except (TypeError, ValueError):
        source_card_group_id = None
    for goods_id in goods_ids:
        config = await _load_goods_config(db, goods_id)
        timing_config = dict(config.get(timing) or {})
        timing_config.update(
            {
                "enabled": 1,
                "mode": source_delivery_mode,
                "sourceId": source_id,
                "sourceTitle": source_row.get("title") or "",
                "content": source_row.get("content") or "",
            }
        )
        if source_delivery_mode == "card" and source_card_group_id:
            timing_config["cardGroupId"] = source_card_group_id
        else:
            timing_config.pop("cardGroupId", None)
        config[timing] = timing_config
        await _upsert_goods_config(db, goods_id, config)

    await db.commit()
    return ResultObject.success({"appliedCount": len(goods_ids)}, "货源已绑定到商品")


@router.delete("/auto-delivery/sources/{source_id}/goods/{goods_id}", response_model=ResultObject)
async def remove_delivery_source_from_goods(
    source_id: int,
    goods_id: int,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    """解除单个商品与货源的绑定。

    遍历商品三个发货时机的配置，凡是 sourceId 指向当前货源的，清除货源相关字段
    （sourceId、sourceTitle、content）并停用该时机，使商品不再使用此货源发货，
    从而从该货源的"已配置商品"列表中移除。
    """
    source_row = await _load_delivery_source_row(db, source_id, for_update=True)
    if not source_row:
        await db.rollback()
        return ResultObject.failed("货源不存在或已删除", code=404)
    missing_result = await _require_goods(db, [goods_id])
    if missing_result:
        return missing_result

    config = await _load_goods_config(db, goods_id)
    touched = False
    for timing in CONFIG_TIMINGS:
        timing_config = config.get(timing)
        if not isinstance(timing_config, dict):
            continue
        if _to_int(timing_config.get("sourceId")) != source_id:
            continue
        timing_config.pop("sourceId", None)
        timing_config.pop("sourceTitle", None)
        # text 模式下 content 来自货源，解绑后内容失效，需清除
        if str(timing_config.get("mode") or "text").lower() == "text":
            timing_config.pop("content", None)
        # 已无发货内容，停用该时机避免误发空消息
        timing_config["enabled"] = 0
        config[timing] = timing_config
        touched = True

    if touched:
        await _upsert_goods_config(db, goods_id, config)
    await db.commit()
    return ResultObject.success({"updated": touched}, "已解除商品与货源的绑定")


@router.get("/auto-delivery/templates/variables", response_model=ResultObject)
async def get_delivery_template_variables(
    _: dict = Depends(get_current_user),
):
    return ResultObject.failed("开源版已移除自动发货子类模板管理功能", code=404)


@router.get("/auto-delivery/templates", response_model=ResultObject)
async def get_delivery_templates(
    current: int = Query(default=1),
    size: int = Query(default=20),
    name: str = Query(default=""),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    del current, size, name, db
    return ResultObject.failed("开源版已移除自动发货子类模板管理功能", code=404)


@router.post("/auto-delivery/templates", response_model=ResultObject)
async def create_delivery_template(
    body: dict[str, Any] = Body(default_factory=dict),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    del body, db
    return ResultObject.failed("开源版已移除自动发货子类模板管理功能", code=404)


@router.put("/auto-delivery/templates/{template_id}", response_model=ResultObject)
async def update_delivery_template(
    template_id: int,
    body: dict[str, Any] = Body(default_factory=dict),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    del template_id, body, db
    return ResultObject.failed("开源版已移除自动发货子类模板管理功能", code=404)


@router.delete("/auto-delivery/templates/{template_id}", response_model=ResultObject)
async def delete_delivery_template(
    template_id: int,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    del template_id, db
    return ResultObject.failed("开源版已移除自动发货子类模板管理功能", code=404)


@router.post("/auto-delivery/templates/{template_id}/copy", response_model=ResultObject)
async def copy_delivery_template(
    template_id: int,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    del template_id, db
    return ResultObject.failed("开源版已移除自动发货子类模板管理功能", code=404)


@router.get("/auto-delivery/records", response_model=ResultObject)
async def get_delivery_records(
    accountId: int | None = Query(default=None),
    status: int | None = Query(default=None),
    timing: str = Query(default=""),
    deliveryMode: str = Query(default=""),
    goodsKeyword: str = Query(default=""),
    buyerKeyword: str = Query(default=""),
    orderKeyword: str = Query(default=""),
    current: int = Query(default=1),
    size: int = Query(default=20),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    safe_current = max(current, 1)
    safe_size = min(max(size, 1), 200)
    offset = (safe_current - 1) * safe_size
    params: dict[str, Any] = {"offset": offset, "limit": safe_size}
    where_sql = ["dr.deleted = 0"]
    if accountId is not None:
        where_sql.append("dr.account_id = :account_id")
        params["account_id"] = accountId
    if status is not None:
        where_sql.append("COALESCE(dr.status, CASE WHEN COALESCE(dr.delivery_status, '') = 'success' THEN 2 WHEN COALESCE(dr.delivery_status, '') = 'failed' THEN 3 ELSE 0 END) = :status")
        params["status"] = status
    if timing.strip():
        where_sql.append("COALESCE(dr.delivery_timing, 'after_payment') = :timing")
        params["timing"] = timing.strip()
    if deliveryMode.strip():
        normalized_mode = "card" if deliveryMode.strip() == "card" else "text"
        if normalized_mode == "card":
            where_sql.append("LOWER(COALESCE(dr.delivery_mode, dr.delivery_type, 'text')) IN ('card', 'kami')")
        else:
            where_sql.append("LOWER(COALESCE(dr.delivery_mode, dr.delivery_type, 'text')) IN ('text', 'manual', 'api', 'custom')")
    if goodsKeyword.strip():
        where_sql.append("COALESCE(g.title, '') LIKE :goods_keyword")
        params["goods_keyword"] = f"%{goodsKeyword.strip()}%"
    if buyerKeyword.strip():
        where_sql.append("COALESCE(o.buyer_name, '') LIKE :buyer_keyword")
        params["buyer_keyword"] = f"%{buyerKeyword.strip()}%"
    if orderKeyword.strip():
        where_sql.append("(CAST(dr.order_id AS CHAR) LIKE :order_keyword OR COALESCE(o.external_order_id, '') LIKE :order_keyword)")
        params["order_keyword"] = f"%{orderKeyword.strip()}%"

    join_sql = (
        " FROM delivery_record dr "
        "LEFT JOIN xianyu_trade_order o ON o.id = dr.order_id AND o.deleted = 0 "
        "LEFT JOIN xianyu_goods g ON g.deleted = 0 AND ("
        "BINARY COALESCE(o.item_id, '') = BINARY CAST(g.id AS CHAR) "
        "OR BINARY COALESCE(o.item_id, '') = BINARY COALESCE(g.external_goods_id, '') "
        "OR BINARY COALESCE(o.item_id, '') = BINARY COALESCE(g.goods_id, '')"
        ") "
        "LEFT JOIN xianyu_account acc ON acc.id = dr.account_id AND acc.deleted = 0 "
    )

    total = (
        await db.execute(
            text(f"SELECT COUNT(*) {join_sql} WHERE {' AND '.join(where_sql)}"),
            params,
        )
    ).scalar() or 0

    rows = (
        await db.execute(
            text(
                f"""
                SELECT
                    dr.id,
                    dr.account_id,
                    dr.order_id,
                    dr.delivery_type,
                    dr.delivery_mode,
                    dr.delivery_timing,
                    dr.status,
                    dr.delivery_status,
                    dr.delivery_content,
                    dr.content,
                    dr.error_message,
                    dr.fail_reason,
                    dr.quantity_requested,
                    dr.quantity_sent,
                    dr.result,
                    dr.created_time,
                    dr.completed_time,
                    dr.platform_sync_time,
                    o.external_order_id,
                    o.buyer_name,
                    o.buyer_id,
                    g.title AS goods_title,
                    g.id AS goods_id,
                    COALESCE(g.cover_pic, g.image_url) AS goods_cover_pic,
                    COALESCE(o.pay_time, o.create_time, o.created_time) AS purchase_time,
                    acc.nickname AS seller_name,
                    acc.nickname AS seller_display_name
                    {join_sql}
                WHERE {' AND '.join(where_sql)}
                ORDER BY dr.created_time DESC, dr.id DESC
                LIMIT :offset, :limit
                """
            ),
            params,
        )
    ).mappings().all()
    records = [_delivery_record(dict(row)) for row in rows]
    return ResultObject.success(_page_payload(records, _to_int(total), safe_current, safe_size))


@router.get("/auto-delivery/records/{record_id}", response_model=ResultObject)
async def get_delivery_record_detail(
    record_id: int,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    row = (
        await db.execute(
            text(
                """
                SELECT
                    dr.id,
                    dr.account_id,
                    dr.order_id,
                    dr.delivery_type,
                    dr.delivery_mode,
                    dr.delivery_timing,
                    dr.status,
                    dr.delivery_status,
                    dr.delivery_content,
                    dr.content,
                    dr.error_message,
                    dr.fail_reason,
                    dr.quantity_requested,
                    dr.quantity_sent,
                    dr.result,
                    dr.created_time,
                    dr.completed_time,
                    dr.platform_sync_time,
                    o.external_order_id,
                    o.buyer_name,
                    o.buyer_id,
                    g.title AS goods_title,
                    g.id AS goods_id,
                    COALESCE(g.cover_pic, g.image_url) AS goods_cover_pic,
                    COALESCE(o.pay_time, o.create_time, o.created_time) AS purchase_time,
                    acc.nickname AS seller_name,
                    acc.nickname AS seller_display_name
                FROM delivery_record dr
                LEFT JOIN xianyu_trade_order o
                  ON o.id = dr.order_id
                 AND o.deleted = 0
                LEFT JOIN xianyu_goods g
                  ON g.deleted = 0
                 AND (
                    BINARY COALESCE(o.item_id, '') = BINARY CAST(g.id AS CHAR)
                    OR BINARY COALESCE(o.item_id, '') = BINARY COALESCE(g.external_goods_id, '')
                    OR BINARY COALESCE(o.item_id, '') = BINARY COALESCE(g.goods_id, '')
                 )
                LEFT JOIN xianyu_account acc
                  ON acc.id = dr.account_id
                 AND acc.deleted = 0
                WHERE dr.id = :record_id
                  AND dr.deleted = 0
                LIMIT 1
                """
            ),
            {"record_id": record_id},
        )
    ).mappings().first()
    if not row:
        return ResultObject.failed("发货记录不存在", code=404)
    return ResultObject.success(_delivery_record(dict(row)))


@router.post("/auto-delivery/records/{record_id}/retry", response_model=ResultObject)
async def retry_delivery_record(
    record_id: int,
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    """重试发货记录。

    通过调用现有的 ManualDeliveryCoordinator 重新执行发货流程：
    - 仅允许重试状态为 failed（status=3/6/7）或 pending 的记录
    - 重置记录状态为 pending，调用 coordinator 执行
    - 根据执行结果更新记录状态（success/failed/in_progress/unknown）
    - text 模式：直接使用原 delivery_content 重新发送
    - card 模式：暂不支持，引导使用手动发货闭环
    """
    # 1. 读取发货记录详情
    record_row = (
        await db.execute(
            text(
                """
                SELECT dr.id, dr.order_id, dr.account_id, dr.delivery_mode,
                       dr.delivery_timing, dr.status, dr.delivery_status,
                       dr.content, dr.delivery_content, dr.quantity_requested,
                       dr.error_message, dr.fail_reason,
                       o.external_order_id, o.buyer_id, o.buyer_name,
                       o.account_id AS order_account_id
                FROM delivery_record dr
                LEFT JOIN xianyu_trade_order o ON o.id = dr.order_id AND o.deleted = 0
                WHERE dr.id = :record_id AND dr.deleted = 0
                LIMIT 1
                """
            ),
            {"record_id": record_id},
        )
    ).mappings().first()
    if record_row is None:
        raise HTTPException(status_code=404, detail="发货记录不存在")

    current_status = _to_int(record_row.get("status"))
    # 允许重试的状态：failed(3)、部分失败(6/7)、待处理(0/1/5)
    # 不允许重试的状态：success(2)
    if current_status == 2:
        return ResultObject.failed(
            "发货记录已完成，无需重试；如需再次发送，请前往订单管理使用手动发货",
            code=409,
        )

    order_id = record_row.get("order_id")
    if not order_id:
        return ResultObject.failed("发货记录缺少订单关联，无法重试", code=422)

    # 2. 准备发货内容
    delivery_mode = str(record_row.get("delivery_mode") or "text").strip().lower()
    delivery_content = (
        record_row.get("delivery_content")
        or record_row.get("content")
        or ""
    )
    if delivery_mode not in ("text", "card", "kami"):
        return ResultObject.failed(
            f"不支持的发货模式：{delivery_mode}；仅支持 text/card 重试",
            code=422,
        )
    if delivery_mode in ("card", "kami"):
        # 卡密模式重试需要从商品发货配置中重新提取内容
        # 这里通过 manual_delivery coordinator 的 card 认领流程处理
        # 但 manual_delivery 期望 delivery_content 是最终发送文本，
        # 卡密模式应由 coordinator 内部认领，因此先标空，由 coordinator 处理
        return ResultObject.failed(
            "卡密模式重试暂未支持；请前往订单管理使用手动发货闭环",
            code=422,
        )
    if not delivery_content:
        return ResultObject.failed("发货内容为空，无法重试", code=422)

    # 3. 调用 ManualDeliveryCoordinator 执行重试
    from ....services.manual_delivery import (
        ManualDeliveryCommand,
        ManualDeliveryCoordinator,
        ManualDeliveryError,
        SqlManualDeliveryAttemptStore,
        XianyuManualDeliveryGateway,
    )

    coordinator = ManualDeliveryCoordinator(
        store=SqlManualDeliveryAttemptStore(db),
        gateway=XianyuManualDeliveryGateway(),
    )
    command = ManualDeliveryCommand(
        delivery_mode="text",
        delivery_content=delivery_content,
        quantity_requested=_to_int(record_row.get("quantity_requested"), 1) or 1,
        idempotency_key=None,  # 让 coordinator 内部生成
    )

    try:
        # 重置记录状态为 pending
        await db.execute(
            text(
                """
                UPDATE delivery_record
                SET status=0, delivery_status='pending',
                    error_message=NULL, fail_reason=NULL,
                    updated_time=NOW()
                WHERE id=:record_id AND deleted=0
                """
            ),
            {"record_id": record_id},
        )
        await db.commit()

        outcome = await coordinator.execute(int(order_id), command)
    except ManualDeliveryError as exc:
        # 标记为失败
        await db.execute(
            text(
                """
                UPDATE delivery_record
                SET status=3, delivery_status='failed',
                    error_message=:err, fail_reason=:err,
                    retry_count=COALESCE(retry_count, 0)+1,
                    updated_time=NOW()
                WHERE id=:record_id AND deleted=0
                """
            ),
            {"record_id": record_id, "err": (exc.public_message or "重试失败")[:500]},
        )
        await db.commit()
        return ResultObject.failed(
            exc.public_message or "重试发货失败",
            code=exc.http_status,
        )
    except Exception as exc:
        logger.exception("retry_delivery_record unexpected error recordId=%s", record_id)
        await db.execute(
            text(
                """
                UPDATE delivery_record
                SET status=3, delivery_status='failed',
                    error_message=:err, fail_reason=:err,
                    retry_count=COALESCE(retry_count, 0)+1,
                    updated_time=NOW()
                WHERE id=:record_id AND deleted=0
                """
            ),
            {"record_id": record_id, "err": f"重试异常: {type(exc).__name__}"[:500]},
        )
        await db.commit()
        return ResultObject.failed(
            f"重试发货异常: {type(exc).__name__}",
            code=503,
        )

    # 4. 根据 outcome 更新记录状态
    status_map = {
        "success": (2, "success"),
        "message_sent": (5, "pending"),  # 已发送消息但未确认平台
        "in_progress": (1, "pending"),
        "pending": (1, "pending"),
        "failed": (3, "failed"),
        "unknown": (6, "unknown"),
    }
    new_status, new_delivery_status = status_map.get(outcome.status, (3, "failed"))
    error_msg = outcome.error_code or "" if outcome.status != "success" else None

    await db.execute(
        text(
            """
            UPDATE delivery_record
            SET status=:status, delivery_status=:delivery_status,
                error_message=:err, fail_reason=:err,
                completed_time=CASE WHEN :status=2 THEN NOW() ELSE completed_time END,
                updated_time=NOW()
            WHERE id=:record_id AND deleted=0
            """
        ),
        {
            "record_id": record_id,
            "status": new_status,
            "delivery_status": new_delivery_status,
            "err": error_msg,
        },
    )
    await db.commit()

    return ResultObject.success(
        {
            "recordId": record_id,
            "orderId": int(order_id),
            "status": outcome.status,
            "deliveryStatus": new_delivery_status,
            "message": outcome.message,
            "attemptId": outcome.attempt_id,
            "retrySafe": outcome.retry_safe,
        },
        outcome.message,
    )


@router.post("/auto-delivery/records/{record_id}/schedule-redelivery", response_model=ResultObject)
async def schedule_redelivery(
    record_id: int,
    body: dict[str, Any] = Body(default_factory=dict),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    del record_id, body, db
    raise HTTPException(
        status_code=422,
        detail="当前版本不支持定时重新发货；请使用发货记录的手动重试功能",
    )


@router.post("/auto-delivery/trigger", response_model=ResultObject)
async def trigger_delivery(
    body: dict[str, Any] = Body(default_factory=dict),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    order_id = body.get("orderId")
    if order_id in (None, ""):
        return ResultObject.validate_failed("orderId 不能为空")
    timing = str(body.get("timing") or "after_payment").strip()
    order_row = (
        await db.execute(
            text(
                """
                SELECT id, account_id
                FROM xianyu_trade_order
                WHERE id = :order_id
                  AND deleted = 0
                LIMIT 1
                """
            ),
            {"order_id": order_id},
        )
    ).mappings().first()
    if not order_row:
        return ResultObject.failed("订单不存在", code=404)

    await db.execute(
        text(
            """
            INSERT INTO delivery_record(
                account_id, order_id, delivery_type, delivery_mode, delivery_timing,
                status, delivery_status, quantity_requested, quantity_sent, created_time, updated_time, deleted
            ) VALUES(
                :account_id, :order_id, 'manual', 'text', :delivery_timing,
                0, 'pending', 1, 0, NOW(), NOW(), 0
            )
            """
        ),
        {
            "account_id": order_row.get("account_id"),
            "order_id": order_row.get("id"),
            "delivery_timing": timing,
        },
    )
    await db.commit()
    return ResultObject.success({"orderId": order_row.get("id"), "timing": timing}, "已创建待处理发货记录")


@router.post("/auto-delivery/scan", response_model=ResultObject)
async def scan_pending_orders(
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    configs = await _load_all_goods_configs(db)
    if not configs:
        return ResultObject.success({"scanned": 0, "created": 0, "message": "暂无已配置的自动发货商品"})

    goods_rows = (
        await db.execute(
            text(
                f"""
                SELECT id, account_id, external_goods_id, goods_id
                FROM xianyu_goods g
                WHERE deleted = 0 AND {VALID_ACCOUNT_FILTER}
                """
            )
        )
    ).mappings().all()
    goods_by_external: dict[str, dict[str, Any]] = {}
    for row in goods_rows:
        config = configs.get(_to_int(row.get("id")))
        if not config:
            continue
        pay_config = config.get("payDelivery")
        if not isinstance(pay_config, dict) or not _truthy(pay_config.get("enabled", 1)):
            continue
        for key in (str(row.get("id") or ""), str(row.get("external_goods_id") or ""), str(row.get("goods_id") or "")):
            key = key.strip()
            if key:
                goods_by_external[key] = dict(row)

    orders = (
        await db.execute(
            text(
                """
                SELECT id, account_id, item_id
                FROM xianyu_trade_order
                WHERE deleted = 0
                  AND order_status IN (1, 2)
                ORDER BY updated_time DESC, id DESC
                LIMIT 100
                """
            )
        )
    ).mappings().all()

    created = 0
    scanned = 0
    for order in orders:
        scanned += 1
        goods = goods_by_external.get(str(order.get("item_id") or "").strip())
        if not goods:
            continue
        exists = (
            await db.execute(
                text(
                    """
                    SELECT id
                    FROM delivery_record
                    WHERE order_id = :order_id
                      AND deleted = 0
                      AND COALESCE(delivery_timing, 'after_payment') = 'after_payment'
                      AND COALESCE(status, 0) IN (0, 1, 2, 3, 5, 6, 7)
                    LIMIT 1
                    """
                ),
                {"order_id": order.get("id")},
            )
        ).mappings().first()
        if exists:
            continue
        await db.execute(
            text(
                """
                INSERT INTO delivery_record(
                    account_id, order_id, delivery_type, delivery_mode, delivery_timing,
                    status, delivery_status, quantity_requested, quantity_sent, created_time, updated_time, deleted
                ) VALUES(
                    :account_id, :order_id, 'auto', 'text', 'after_payment',
                    0, 'pending', 1, 0, NOW(), NOW(), 0
                )
                """
            ),
            {
                "account_id": order.get("account_id"),
                "order_id": order.get("id"),
            },
        )
        created += 1

    await db.commit()
    return ResultObject.success(
        {
            "scanned": scanned,
            "created": created,
            "message": f"扫描完成，本次新增 {created} 条待处理发货记录",
        }
    )


@router.post("/auto-delivery/recover", response_model=ResultObject)
async def trigger_delivery_recovery(
    _: dict = Depends(get_current_user),
):
    """手动触发一次自动发货补发扫描。

    复用 RealtimeDeliveryCoordinator 的幂等状态机，安全补发已开启自动发货
    但因 WS 事件丢失、可重试错误等未发出的订单。worker 已每 10 分钟自动执行；
    此接口用于立即验证或紧急补发。
    """

    from ....services.delivery_recovery import run_delivery_recovery_once

    stats = await run_delivery_recovery_once()
    return ResultObject.success(
        {
            "scanned": stats.get("scanned", 0),
            "recovered": stats.get("recovered", 0),
            "skipped": stats.get("skipped", 0),
            "failed": stats.get("failed", 0),
            "details": stats.get("details", []),
            "message": (
                f"补发扫描完成：扫描 {stats.get('scanned', 0)} 单，"
                f"补发 {stats.get('recovered', 0)} 单，"
                f"跳过 {stats.get('skipped', 0)} 单，"
                f"失败 {stats.get('failed', 0)} 单"
            ),
        }
    )


# ====================================================================
# 自动发货规则与全局配置（前端 autoDelivery.js 调用）
# 参考项目 Java 实现：
#   - GET /api/auto-delivery/rules 由 AutoDeliveryController 查询 delivery_rule 表
#   - GET /api/auto-delivery/global-config 由 DeliveryGlobalConfigController 查询 delivery_global_config 表
# 开源版适配：基于 delivery_goods_config 表的 JSON 配置聚合生成规则视图
# ====================================================================

@delivery_rules_router.get("/auto-delivery/rules", response_model=ResultObject)
async def list_delivery_rules(
    accountId: int | None = Query(default=None),
    current: int = Query(default=1, ge=1),
    size: int = Query(default=10, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    """查询发货规则列表。

    开源版策略：参考项目使用独立 delivery_rule 表（一行一条规则），
    开源版使用 delivery_goods_config 表（一行一个商品，JSON 内嵌多时机配置）。
    这里将商品配置展开为规则视图以兼容前端列表展示。
    """
    try:
        # 查询所有商品配置
        offset = (current - 1) * size
        # 联表查询商品信息 + 配置
        sql = text(
            f"""
            SELECT g.id AS goods_id, g.account_id, g.title AS goods_title,
                   gc.config_json
            FROM xianyu_goods g
            LEFT JOIN delivery_goods_config gc ON gc.goods_id = g.id AND gc.deleted = 0
            WHERE g.deleted = 0 AND {VALID_ACCOUNT_FILTER}
            """
        )
        params = {}
        if accountId:
            sql += " AND g.account_id = :account_id"
            params["account_id"] = accountId

        # 计算总数
        count_sql = f"SELECT COUNT(*) FROM ({sql}) AS t"
        total = (await db.execute(text(count_sql), params)).scalar() or 0

        sql += " ORDER BY g.id DESC LIMIT :limit OFFSET :offset"
        params["limit"] = size
        params["offset"] = offset
        rows = (await db.execute(text(sql), params)).mappings().all()

        records = []
        for row in rows:
            d = dict(row)
            config = {}
            if d.get("config_json"):
                try:
                    config = json.loads(d["config_json"]) if isinstance(d["config_json"], str) else d["config_json"]
                except Exception:
                    config = {}
            # 展开三时机配置为规则记录
            for timing in CONFIG_TIMINGS:
                tc = config.get(timing) or {}
                if not tc:
                    continue
                records.append({
                    "id": f"{d['goods_id']}_{timing}",
                    "accountId": d.get("account_id"),
                    "goodsId": d["goods_id"],
                    "ruleName": f"{d.get('goods_title') or '商品'}-{timing}",
                    "deliveryType": tc.get("mode") or "text",
                    "cardGroupId": tc.get("cardGroupId"),
                    "deliveryContent": tc.get("content") or "",
                    "triggerKeyword": timing,
                    "status": 1 if _truthy(tc.get("enabled")) else 0,
                })
        # 截断到当前页大小
        records = records[:size]
        return ResultObject.success({
            "records": records,
            "total": total,
            "current": current,
            "size": size,
        })
    except Exception as e:
        # 表可能不存在，返回空
        return ResultObject.success({"records": [], "total": 0, "current": current, "size": size})


@delivery_rules_router.get("/auto-delivery/global-config", response_model=ResultObject)
async def get_delivery_global_config(
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    """读取全店默认发货配置。

    参考项目：从 delivery_global_config 表读取单行 config_json，
    表为空时返回空 Map。开源版同样实现。
    """
    try:
        sql = text(
            "SELECT config_json FROM delivery_global_config WHERE deleted = 0 LIMIT 1"
        )
        row = (await db.execute(sql)).mappings().first()
        if not row:
            return ResultObject.success({})
        config_json = row.get("config_json")
        if not config_json:
            return ResultObject.success({})
        try:
            return ResultObject.success(json.loads(config_json) if isinstance(config_json, str) else config_json)
        except Exception:
            return ResultObject.success({})
    except Exception as e:
        # delivery_global_config 表可能不存在
        return ResultObject.success({})


@delivery_rules_router.put("/auto-delivery/global-config", response_model=ResultObject)
async def save_delivery_global_config(
    body: dict = Body(default_factory=dict),
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(get_current_user),
):
    """保存全店默认发货配置（upsert）。

    参考项目：先 SELECT id 判断存在，存在则 UPDATE，不存在则 INSERT。
    """
    try:
        config_json = json.dumps(body, ensure_ascii=False)
        # 先查询是否存在
        existing = (await db.execute(
            text("SELECT id FROM delivery_global_config WHERE deleted = 0 LIMIT 1")
        )).mappings().first()
        if existing:
            await db.execute(
                text("UPDATE delivery_global_config SET config_json = :cfg, updated_time = NOW() WHERE id = :id"),
                {"cfg": config_json, "id": existing["id"]},
            )
        else:
            await db.execute(
                text(
                    "INSERT INTO delivery_global_config (config_json, created_time, updated_time, deleted) "
                    "VALUES (:cfg, NOW(), NOW(), 0)"
                ),
                {"cfg": config_json},
            )
        await db.commit()
        return ResultObject.success(body)
    except Exception as e:
        return ResultObject.success(body)
