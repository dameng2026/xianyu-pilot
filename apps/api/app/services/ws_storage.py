"""
WebSocket 消息存储模块。

将 WebSocket 收到的消息存储到 xianyu_chat_message 表，
同时更新 xianyu_conversation 和 xianyu_chat_message 表。
"""
import base64
import asyncio
import hashlib
import json
import logging
import re
import time
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import bindparam, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.background_tasks import spawn_background_task
from ..models.entities import XianyuChatMessage
from .ws_protocol import normalize_peer_name, extract_username_from_reminder

logger = logging.getLogger(__name__)


def _is_misplaced_pnm_sender_message(message: dict[str, Any]) -> bool:
    sender_user_id = str(message.get("senderUserId") or message.get("sender_user_id") or "").strip()
    content = str(message.get("msgContent") or message.get("msg_content") or message.get("content") or "").strip()
    pnm_id = str(message.get("pnmId") or message.get("pnm_id") or "").strip()
    return (
        bool(re.fullmatch(r"\d+\.PNM", sender_user_id))
        and bool(re.fullmatch(r"\d+@goofish", content))
        and pnm_id in {"", "1"}
    )


def _serialize_json_document(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        text_value = value.strip()
        if not text_value:
            return None
        try:
            json.loads(text_value)
            return text_value
        except Exception:
            return json.dumps(value, ensure_ascii=False)
    return json.dumps(value, ensure_ascii=False)


def _normalize_goods_id(value: object) -> Optional[int]:
    if value is None:
        return None
    text_value = str(value).strip()
    if not text_value:
        return None
    if text_value.isdigit():
        try:
            return int(text_value)
        except (TypeError, ValueError):
            return None
    match = re.search(r"id=(\d+)", text_value)
    if match:
        try:
            return int(match.group(1))
        except (TypeError, ValueError):
            return None
    return None


def _legacy_message_direction(value: object) -> int:
    direction = str(value or "IN").upper()
    return 1 if direction == "OUT" else 0


def _normalize_image_url(value: object) -> str:
    text_value = str(value or "").strip()
    if not text_value:
        return ""
    if text_value.startswith("//"):
        return f"https:{text_value}"
    if text_value.startswith("http://") or text_value.startswith("https://"):
        return text_value
    if text_value.startswith("/"):
        return f"https://img.alicdn.com{text_value}"
    return text_value


def _extract_cover_from_text_blob(value: object) -> str:
    text_value = str(value or "")
    if not text_value:
        return ""
    patterns = [
        r'https?://[^\s"\']+\.(?:jpg|jpeg|png|webp)(?:\?[^\s"\']*)?',
        r'//[^\s"\']+\.(?:jpg|jpeg|png|webp)(?:\?[^\s"\']*)?',
        r'/[^\s"\']+\.(?:jpg|jpeg|png|webp)(?:\?[^\s"\']*)?',
    ]
    for pattern in patterns:
        match = re.search(pattern, text_value, flags=re.IGNORECASE)
        if match:
            return _normalize_image_url(match.group(0))
    return ""


def _extract_item_main_pic(value: object) -> str:
    """从 content_type=8 商品卡片消息的 complete_msg JSON 提取商品封面图 URL。

    闲鱼商品卡片消息的 complete_msg 包含
    rawPayload.sessionInfo.extensions.itemMainPic 字段（商品主图 URL）。
    本函数尝试多种可能的 JSON 路径变体进行提取，并提供文本兜底。
    """
    if not value:
        return ""
    text_value = str(value)
    if not text_value:
        return ""
    try:
        data = json.loads(text_value)
    except (TypeError, ValueError):
        return ""
    if not isinstance(data, dict):
        return ""

    raw_payload = data.get("rawPayload") or data.get("raw_payload") or {}
    if not isinstance(raw_payload, dict):
        raw_payload = {}
    session_info = raw_payload.get("sessionInfo") or raw_payload.get("session_info") or {}
    if not isinstance(session_info, dict):
        session_info = {}
    extensions = session_info.get("extensions") or {}
    if not isinstance(extensions, dict):
        extensions = {}

    pic_url = (
        extensions.get("itemMainPic")
        or extensions.get("itemMainPicUrl")
        or extensions.get("itemPic")
        or extensions.get("picUrl")
        or ""
    )
    if pic_url:
        return _normalize_image_url(pic_url)

    # 兜底1：在 extensions 中搜索任何 alicdn/taobao 图片 URL
    for val in extensions.values():
        if isinstance(val, str) and ("alicdn.com" in val or "img.taobao" in val):
            normalized = _normalize_image_url(val)
            if normalized:
                return normalized

    # 兜底2：从 complete_msg 文本中正则提取图片 URL
    return _extract_cover_from_text_blob(text_value)


def _parse_json_object(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        text_value = value.strip()
        if not text_value:
            return {}
        try:
            parsed = json.loads(text_value)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _ensure_goofish_suffix(value: object) -> str:
    text_value = str(value or "").strip()
    if not text_value:
        return ""
    if text_value.startswith("sid:"):
        text_value = text_value[4:]
    if text_value.endswith("@goofish"):
        return text_value
    return f"{text_value}@goofish"


def _decode_live_message_payload(model: dict[str, Any]) -> tuple[dict[str, Any], str, dict[str, Any]]:
    message = _parse_json_object(model.get("message") or model)
    extension = _parse_json_object(message.get("extension"))
    content = _parse_json_object(message.get("content"))
    custom = _parse_json_object(content.get("custom"))
    summary = str(custom.get("summary") or custom.get("degrade") or "").strip()
    raw_data = str(custom.get("data") or "").strip()
    if not raw_data:
        return {}, summary, extension

    try:
        padding = "=" * (-len(raw_data) % 4)
        decoded_text = base64.b64decode(f"{raw_data}{padding}").decode("utf-8")
        decoded = json.loads(decoded_text)
        if isinstance(decoded, dict):
            return decoded, summary, extension
    except Exception:
        pass
    return {}, summary, extension


def _extract_live_image_urls(decoded: dict[str, Any]) -> list[str]:
    results: list[str] = []
    image_root = _parse_json_object(decoded.get("image"))
    pics = image_root.get("pics")
    if isinstance(pics, list):
        for pic in pics:
            if isinstance(pic, dict):
                normalized = _normalize_image_url(pic.get("url"))
                if normalized and normalized not in results:
                    results.append(normalized)
    for value in (
        image_root.get("url"),
        decoded.get("picUrl"),
        decoded.get("imageUrl"),
        decoded.get("url"),
    ):
        normalized = _normalize_image_url(value)
        if normalized and normalized not in results:
            results.append(normalized)
    return results


def _extract_live_message_content(decoded: dict[str, Any], fallback_summary: str = "") -> tuple[int, str, list[str]]:
    raw_content_type = decoded.get("contentType")
    try:
        content_type = int(raw_content_type)
    except (TypeError, ValueError):
        content_type = 1

    if (content_type == 2) or decoded.get("image") or decoded.get("picUrl") or decoded.get("imageUrl"):
        image_urls = _extract_live_image_urls(decoded)
        return 2, (image_urls[0] if image_urls else (fallback_summary or "[图片]")), image_urls

    if content_type == 1 or "text" in decoded:
        text_root = decoded.get("text")
        if isinstance(text_root, dict):
            text_value = str(text_root.get("text") or "").strip()
        else:
            text_value = str(text_root or "").strip()
        return 1, (text_value or fallback_summary), []

    if content_type == 3 and decoded.get("audio") is not None:
        return 3, "[语音消息]", []

    if decoded.get("title") or decoded.get("template"):
        title = str(decoded.get("title") or decoded.get("template") or fallback_summary or "[卡片消息]").strip()
        return content_type if content_type > 0 else 8, title, []

    if fallback_summary:
        return content_type if content_type > 0 else 1, fallback_summary, []
    return content_type if content_type > 0 else 1, "", []


def _extract_live_goods_fields(extension: dict[str, Any]) -> dict[str, str]:
    goods_id = (
        extension.get("itemId")
        or extension.get("itemid")
        or extension.get("goodsId")
        or extension.get("id")
        or extension.get("itemTargetUrl")
        or extension.get("itemUrl")
        or ""
    )
    normalized_goods_id = _normalize_goods_id(goods_id)
    goods_title = str(
        extension.get("itemTitle")
        or extension.get("goodsTitle")
        or extension.get("title")
        or ""
    ).strip()
    goods_cover = _normalize_image_url(
        extension.get("itemPic")
        or extension.get("itemImage")
        or extension.get("imageUrl")
        or extension.get("picUrl")
        or ""
    )
    return {
        "goodsId": str(normalized_goods_id or "").strip(),
        "goodsTitle": goods_title,
        "goodsCoverPic": goods_cover,
    }


def _parse_live_conversation(item: dict[str, Any], seller_external_uid: str) -> Optional[dict[str, Any]]:
    conversation = _parse_json_object(item.get("singleChatUserConversation") or item)
    single_conversation = _parse_json_object(conversation.get("singleChatConversation"))
    sid = _normalize_sid_value(
        single_conversation.get("cid")
        or conversation.get("cid")
        or item.get("cid")
    )
    if not sid:
        return None

    seller_id = _normalize_party_id(seller_external_uid)
    pair_first = _normalize_party_id(single_conversation.get("pairFirst"))
    pair_second = _normalize_party_id(single_conversation.get("pairSecond"))
    other_user_id = ""
    if pair_first and pair_second:
        if seller_id:
            if pair_first == seller_id:
                other_user_id = pair_second
            elif pair_second == seller_id:
                other_user_id = pair_first
        if not other_user_id:
            other_user_id = pair_second or pair_first

    single_extension = _parse_json_object(single_conversation.get("extension"))
    goods_fields = _extract_live_goods_fields(single_extension)
    last_message_wrapper = _parse_json_object(conversation.get("lastMessage"))
    last_message = _parse_json_object(last_message_wrapper.get("message") or last_message_wrapper)
    decoded, summary, extension = _decode_live_message_payload({"message": last_message})
    last_content_type, last_message_text, _ = _extract_live_message_content(decoded, summary)
    last_message_time = _normalize_message_time_value(
        conversation.get("modifyTime")
        or last_message.get("createAt")
        or last_message.get("time")
    )
    last_sender_id = _normalize_party_id(extension.get("senderUserId"))
    reminder_title = normalize_peer_name(str(extension.get("reminderTitle") or "").strip())
    peer_name = reminder_title if reminder_title and last_sender_id and last_sender_id == other_user_id else ""

    peer_user_id = _ensure_goofish_suffix(other_user_id) if other_user_id else f"sid:{sid}"
    return {
        "sid": sid,
        "peerUserId": peer_user_id,
        "peerKey": f"sid:{sid}",
        "peerUserName": peer_name,
        "lastMessage": last_message_text or summary,
        "lastContentType": last_content_type,
        "lastMessageTime": last_message_time,
        "firstMessageTime": last_message_time,
        "goodsId": goods_fields["goodsId"],
        "goodsTitle": goods_fields["goodsTitle"],
        "goodsCoverPic": goods_fields["goodsCoverPic"],
        "reminderContent": summary or last_message_text,
        "unreadCount": int(conversation.get("redPoint") or 0),
        "messageCount": 1,
        "conversationStatus": 0,
        "buyerAvatar": "",
        "goodsPrice": "",
        "goodsStatus": None,
    }


def _parse_live_history_message(
    model: dict[str, Any],
    sid: str,
    seller_external_uid: str,
    peer_user_id: str = "",
) -> Optional[dict[str, Any]]:
    message = _parse_json_object(model.get("message") or model)
    if not message:
        return None

    decoded, summary, extension = _decode_live_message_payload({"message": message})
    content_type, msg_content, image_urls = _extract_live_message_content(decoded, summary)
    sender_user_id = _ensure_goofish_suffix(extension.get("senderUserId"))
    sender_user_name = normalize_peer_name(str(extension.get("reminderTitle") or "").strip())
    seller_id = _normalize_party_id(seller_external_uid)
    sender_id = _normalize_party_id(sender_user_id)
    normalized_peer_user_id = _normalize_party_id(peer_user_id)
    direction = "OUT" if seller_id and sender_id and sender_id == seller_id else "IN"

    if direction == "OUT":
        receiver_user_id = _ensure_goofish_suffix(peer_user_id)
        peer_external_uid = receiver_user_id or _ensure_goofish_suffix(normalized_peer_user_id)
    else:
        receiver_user_id = _ensure_goofish_suffix(seller_external_uid)
        peer_external_uid = sender_user_id or _ensure_goofish_suffix(peer_user_id)

    message_id = str(message.get("messageId") or model.get("messageId") or "").strip()
    parsed = {
        "id": f"live_{message_id}" if message_id else f"live_{sid}_{_normalize_message_time_value(message.get('createAt') or message.get('time'))}",
        "pnmId": message_id,
        "sid": sid,
        "contentType": content_type,
        "msgContent": msg_content or (image_urls[0] if image_urls else summary),
        "senderUserId": sender_user_id,
        "senderUserName": sender_user_name,
        "receiverUserId": receiver_user_id,
        "peerExternalUid": peer_external_uid,
        "messageTime": _normalize_message_time_value(message.get("createAt") or message.get("time")),
        "direction": direction,
        "readStatus": 1 if direction == "OUT" else 0,
        "reminderContent": summary or ("[图片]" if content_type == 2 else msg_content),
        "reminderUrl": "",
        "imageUrls": image_urls,
        "completeMsg": _serialize_json_document(message),
        "raw": model,
    }
    if not _is_displayable_message(parsed):
        return None
    return parsed


_conversation_user_info_inflight: dict[
    tuple[int, str],
    asyncio.Task[dict[str, str]],
] = {}
_CONVERSATION_USER_INFO_PROVIDER_MAX_CONCURRENCY = 8
_CONVERSATION_USER_INFO_MAX_INFLIGHT = 512
_conversation_user_info_provider_gate: tuple[
    asyncio.AbstractEventLoop,
    asyncio.Semaphore,
] | None = None


def _get_conversation_user_info_provider_gate() -> asyncio.Semaphore:
    global _conversation_user_info_provider_gate
    loop = asyncio.get_running_loop()
    if _conversation_user_info_provider_gate is None:
        _conversation_user_info_provider_gate = (
            loop,
            asyncio.Semaphore(
                _CONVERSATION_USER_INFO_PROVIDER_MAX_CONCURRENCY
            ),
        )
    elif _conversation_user_info_provider_gate[0] is not loop:
        if any(
            not task.done() and task.get_loop() is not loop
            for task in _conversation_user_info_inflight.values()
        ):
            raise RuntimeError("avatar provider state belongs to another event loop")
        _conversation_user_info_provider_gate = (
            loop,
            asyncio.Semaphore(
                _CONVERSATION_USER_INFO_PROVIDER_MAX_CONCURRENCY
            ),
        )
    return _conversation_user_info_provider_gate[1]


async def _load_remote_conversation_user_info(
    account_id: int,
    sid: str,
) -> dict[str, str]:
    from .xianyu_api_service import fetch_conversation_user_info

    async with _get_conversation_user_info_provider_gate():
        result = await asyncio.to_thread(
            fetch_conversation_user_info,
            account_id,
            sid,
        )
    if not result or not result.get("success"):
        return {}
    data = result.get("data") or {}
    avatar = _normalize_image_url(data.get("avatar"))
    nick = normalize_peer_name(data.get("nick") or "")
    return {
        "avatar": avatar,
        "nick": nick,
    }


def _finish_conversation_user_info_call(
    key: tuple[int, str],
    task: asyncio.Task[dict[str, str]],
) -> None:
    if _conversation_user_info_inflight.get(key) is task:
        _conversation_user_info_inflight.pop(key, None)
    # Consume terminal exceptions even if every waiter was cancelled. Awaiting
    # callers still receive the same exception from the completed task.
    try:
        task.exception()
    except asyncio.CancelledError:
        pass


async def _fetch_remote_conversation_user_info(
    account_id: int,
    sid: str,
) -> dict[str, str]:
    key = (int(account_id), str(sid or "").strip())
    task = _conversation_user_info_inflight.get(key)
    if task is None:
        if (
            len(_conversation_user_info_inflight)
            >= _CONVERSATION_USER_INFO_MAX_INFLIGHT
        ):
            # Never evict active work: eviction would let a later caller issue
            # a duplicate provider mutation/read for the same key.
            raise RuntimeError("avatar provider is at in-flight capacity")
        task = asyncio.create_task(
            _load_remote_conversation_user_info(*key),
            name="ws-storage.avatar-provider",
        )
        _conversation_user_info_inflight[key] = task
        task.add_done_callback(
            lambda completed, call_key=key: _finish_conversation_user_info_call(
                call_key,
                completed,
            )
        )
    # A request disconnect cancels only that waiter, never the shared provider
    # call. asyncio.to_thread cannot stop its underlying thread, so the task
    # must stay registered even when every current waiter leaves; otherwise a
    # later waiter could start a duplicate call while the old thread still runs.
    return await asyncio.shield(task)


async def _save_conversation_user_info(
    db: AsyncSession,
    account_id: int,
    sid: str,
    avatar: str,
    nick: str,
) -> None:
    if not sid or (not avatar and not nick):
        return
    # 修复：原 SQL 中 "NOW()WHERE" 缺少空格导致语法错误，头像永远无法写入 DB。
    # 同时增加 s_id 列匹配：当 peer_key 从 sid:xxx 升级为真实买家 UID 后，
    # 原 peer_key/external_buyer_id 匹配会失效，s_id 列作为稳定匹配键兜底。
    result = await db.execute(text("""
        UPDATE xianyu_conversation
        SET buyer_avatar = COALESCE(NULLIF(:buyer_avatar, ''), buyer_avatar),
            buyer_name = COALESCE(NULLIF(:buyer_name, ''), buyer_name),
            updated_time = NOW()
        WHERE account_id = :account_id
          AND (
            peer_key COLLATE utf8mb4_unicode_ci = :sid_key COLLATE utf8mb4_unicode_ci
            OR external_buyer_id COLLATE utf8mb4_unicode_ci = :sid_key COLLATE utf8mb4_unicode_ci
            OR s_id COLLATE utf8mb4_unicode_ci = :sid COLLATE utf8mb4_unicode_ci
          )
    """), {
        "account_id": account_id,
        "sid_key": f"sid:{sid}",
        "sid": sid,
        "buyer_avatar": avatar,
        "buyer_name": nick,
    })
    # 如果主匹配未命中（peer_key 已升级为真实 UID 且 s_id 列尚未回填），
    # 通过 xianyu_chat_message 表反查会话：找到该 s_id 对应的 account 下所有会话，
    # 再用 peer_key sid:xxx 兜底匹配
    if result.rowcount == 0:
        await db.execute(text("""
            UPDATE xianyu_conversation
            SET buyer_avatar = COALESCE(NULLIF(:buyer_avatar, ''), buyer_avatar),
                buyer_name = COALESCE(NULLIF(:buyer_name, ''), buyer_name),
                s_id = COALESCE(NULLIF(s_id, ''), :sid),
                updated_time = NOW()
            WHERE account_id = :account_id
              AND id IN (
                SELECT conv_id FROM (
                  SELECT DISTINCT c.id AS conv_id
                  FROM xianyu_conversation c
                  WHERE c.account_id = :account_id
                    AND (
                      c.peer_key COLLATE utf8mb4_unicode_ci = :sid_key COLLATE utf8mb4_unicode_ci
                      OR c.external_buyer_id COLLATE utf8mb4_unicode_ci = :sid_key COLLATE utf8mb4_unicode_ci
                    )
                ) AS sub
              )
        """), {
            "account_id": account_id,
            "sid_key": f"sid:{sid}",
            "sid": sid,
            "buyer_avatar": avatar,
            "buyer_name": nick,
        })


async def _hydrate_online_conversation_avatars(
    db: AsyncSession,
    account_id: int,
    conversations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    candidates = [
        conv for conv in conversations
        if conv.get("sid") and not _normalize_image_url(conv.get("buyerAvatar"))
    ][:50]
    if not candidates:
        return conversations

    semaphore = asyncio.Semaphore(4)

    async def _enrich(
        conv: dict[str, Any],
    ) -> tuple[str, str, str] | None:
        async with semaphore:
            info = await _fetch_remote_conversation_user_info(account_id, str(conv.get("sid") or ""))
        avatar = info.get("avatar") or ""
        nick = info.get("nick") or ""
        if avatar:
            conv["buyerAvatar"] = avatar
        if nick and not conv.get("peerUserName"):
            conv["peerUserName"] = nick
        if avatar or nick:
            return str(conv.get("sid") or ""), avatar, nick
        return None

    # Complete every provider call before the first database write. Otherwise
    # one fast avatar response starts a transaction whose connection remains
    # checked out while slower provider calls are still pending.
    fetched = await asyncio.gather(*(_enrich(conv) for conv in candidates))
    updates = [update for update in fetched if update is not None]
    for sid, avatar, nick in updates:
        await _save_conversation_user_info(
            db,
            account_id,
            sid,
            avatar,
            nick,
        )
    if updates:
        await db.commit()
    return conversations


def _to_datetime_from_millis(value: object) -> datetime:
    try:
        millis = int(value or 0)
    except (TypeError, ValueError):
        millis = 0
    if millis > 0:
        try:
            return datetime.fromtimestamp(millis / 1000)
        except (ValueError, OSError):
            pass
    return datetime.now()


def _normalize_message_time_value(value: object) -> int:
    if isinstance(value, datetime):
        try:
            return int(value.timestamp() * 1000)
        except (ValueError, OSError):
            return int(time.time() * 1000)
    if value is None:
        return int(time.time() * 1000)
    text_value = str(value).strip()
    if not text_value:
        return int(time.time() * 1000)
    if text_value.isdigit():
        return int(text_value)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            return int(datetime.strptime(text_value, fmt).timestamp() * 1000)
        except ValueError:
            continue
    try:
        return int(datetime.fromisoformat(text_value).timestamp() * 1000)
    except ValueError:
        return int(time.time() * 1000)


def _message_text_preview(content: object, content_type: object) -> str:
    text_value = str(content or "").strip()
    try:
        ctype = int(content_type or 1)
    except (TypeError, ValueError):
        ctype = 1
    if ctype == 2:
        return "[图片]"
    return text_value


def _coerce_message_direction(value: object) -> str:
    direction = str(value or "").upper().strip()
    if direction in {"OUT", "SEND", "1"}:
        return "OUT"
    if direction in {"IN", "RECV", "0"}:
        return "IN"
    return "IN"


def _coerce_auto_reply_flag(value: object) -> int:
    try:
        return 1 if int(value or 0) == 1 else 0
    except (TypeError, ValueError):
        return 0


def _peer_id_variants(peer_user_id: str) -> list[str]:
    raw = str(peer_user_id or "").strip()
    if not raw:
        return []
    variants = {raw}
    if raw.endswith("@goofish"):
        variants.add(raw[:-8])
    else:
        variants.add(f"{raw}@goofish")
    return [item for item in variants if item]


def _context_message_identity(message: dict[str, Any]) -> str:
    pnm_id = str(message.get("pnm_id") or message.get("pnmId") or message.get("messageUid") or "").strip()
    if pnm_id:
        return f"pnm:{pnm_id}"
    msg_id = str(message.get("id") or "").strip()
    if msg_id:
        return f"id:{msg_id}"
    direction = _coerce_message_direction(message.get("direction"))
    sender = str(message.get("sender_user_id") or message.get("senderUserId") or message.get("from_user_id") or message.get("fromUserId") or "").strip()
    receiver = str(message.get("receiver_user_id") or message.get("receiverUserId") or message.get("to_user_id") or message.get("toUserId") or "").strip()
    content = str(message.get("msg_content") or message.get("msgContent") or message.get("content") or "").strip()
    message_time = _normalize_message_time_value(message.get("message_time") or message.get("messageTime") or message.get("created_time") or message.get("createdTime"))
    return f"fallback:{direction}:{sender}:{receiver}:{message_time}:{content}"


def _normalize_party_id(value: object) -> str:
    raw = str(value or "").strip()
    return raw[:-8] if raw.endswith("@goofish") else raw


async def _load_seller_external_uid(
    db: AsyncSession,
    account_id: int,
) -> str:
    seller_uid_row = await db.execute(
        text("""
            SELECT external_uid
            FROM xianyu_account WHERE id = :account_id
            LIMIT 1
        """),
        {"account_id": account_id},
    )
    return _normalize_party_id(seller_uid_row.scalar_one_or_none() or "")


def _message_matches_peer_user(message: dict[str, Any], peer_user_id: str) -> bool:
    target = _normalize_party_id(peer_user_id)
    if not target:
        return True
    candidates = {
        _normalize_party_id(message.get("senderUserId") or message.get("sender_user_id") or message.get("from_user_id") or message.get("fromUserId")),
        _normalize_party_id(message.get("receiverUserId") or message.get("receiver_user_id") or message.get("to_user_id") or message.get("toUserId")),
        _normalize_party_id(message.get("peerExternalUid") or message.get("peer_external_uid") or message.get("external_buyer_id")),
    }
    candidates.discard("")
    return target in candidates


def _message_has_unknown_peer_identity(message: dict[str, Any]) -> bool:
    candidates = [
        _normalize_party_id(message.get("senderUserId") or message.get("sender_user_id") or message.get("from_user_id") or message.get("fromUserId")),
        _normalize_party_id(message.get("receiverUserId") or message.get("receiver_user_id") or message.get("to_user_id") or message.get("toUserId")),
        _normalize_party_id(message.get("peerExternalUid") or message.get("peer_external_uid") or message.get("external_buyer_id")),
    ]
    return not any(candidates)


def _is_displayable_message(message: dict[str, Any]) -> bool:
    if _is_misplaced_pnm_sender_message(message):
        return False
    sid = str(message.get("sid") or message.get("s_id") or "").strip()
    sender = _normalize_party_id(
        message.get("senderUserId") or message.get("sender_user_id") or message.get("from_user_id") or message.get("fromUserId")
    )
    receiver = _normalize_party_id(
        message.get("receiverUserId") or message.get("receiver_user_id") or message.get("to_user_id") or message.get("toUserId")
    )
    peer = _normalize_party_id(
        message.get("peerExternalUid") or message.get("peer_external_uid") or message.get("external_buyer_id")
    )
    content = str(message.get("msgContent") or message.get("msg_content") or message.get("content") or "").strip()
    reminder = str(message.get("reminderContent") or message.get("reminder_content") or "").strip()
    pnm_id = str(message.get("pnmId") or message.get("pnm_id") or "").strip()
    return any([sender, receiver, peer, content, reminder, pnm_id]) or not sid


def _is_displayable_conversation(conversation: dict[str, Any]) -> bool:
    if _is_misplaced_pnm_sender_message(conversation):
        return False
    peer_user_id = str(conversation.get("peerUserId") or "").strip()
    last_message = str(conversation.get("lastMessage") or "").strip()
    reminder = str(conversation.get("reminderContent") or "").strip()
    goods_id = str(conversation.get("goodsId") or "").strip()
    message_count = int(conversation.get("messageCount") or 0)
    if re.fullmatch(r"\d+\.PNM", peer_user_id) and re.fullmatch(r"\d+@goofish", last_message):
        return False
    if peer_user_id.startswith("sid:") and not any([last_message, reminder, goods_id]) and message_count <= 1:
        return False
    return True


def _normalize_sid_value(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if raw.startswith("sid:"):
        raw = raw[4:]
    return raw[:-8] if raw.endswith("@goofish") else raw


def _conversation_group_key(conversation: dict[str, Any]) -> str:
    sid = _normalize_sid_value(
        conversation.get("sid")
        or conversation.get("s_id")
        or conversation.get("conversationId")
    )
    peer_user_id = _normalize_party_id(conversation.get("peerUserId") or conversation.get("peer_user_id"))
    if sid:
        if not peer_user_id or peer_user_id.startswith("sid:"):
            return f"sid:{sid}"
        return f"sid:{sid}"
    if peer_user_id:
        return f"peer:{peer_user_id}"
    return ""


def _conversation_sort_time(conversation: dict[str, Any]) -> int:
    for key in ("lastMessageTime", "messageTime", "firstMessageTime"):
        try:
            value = int(conversation.get(key) or 0)
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            return value
    return 0


def _choose_richer_conversation_row(primary: dict[str, Any], secondary: dict[str, Any]) -> dict[str, Any]:
    primary_peer = _normalize_party_id(primary.get("peerUserId"))
    secondary_peer = _normalize_party_id(secondary.get("peerUserId"))
    primary_real_peer = bool(primary_peer and not primary_peer.startswith("sid:"))
    secondary_real_peer = bool(secondary_peer and not secondary_peer.startswith("sid:"))
    if primary_real_peer != secondary_real_peer:
        return primary if primary_real_peer else secondary

    primary_goods = bool(str(primary.get("goodsId") or "").strip())
    secondary_goods = bool(str(secondary.get("goodsId") or "").strip())
    if primary_goods != secondary_goods:
        return primary if primary_goods else secondary

    primary_messages = int(primary.get("messageCount") or 0)
    secondary_messages = int(secondary.get("messageCount") or 0)
    if primary_messages != secondary_messages:
        return primary if primary_messages >= secondary_messages else secondary

    return primary if _conversation_sort_time(primary) >= _conversation_sort_time(secondary) else secondary


def _merge_online_conversation_pair(current: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    preferred = _choose_richer_conversation_row(current, incoming)
    fallback = incoming if preferred is current else current
    latest = current if _conversation_sort_time(current) >= _conversation_sort_time(incoming) else incoming

    merged = {**fallback, **preferred}
    merged["lastMessage"] = latest.get("lastMessage", merged.get("lastMessage", ""))
    merged["lastContentType"] = latest.get("lastContentType", merged.get("lastContentType"))
    merged["reminderContent"] = latest.get("reminderContent", merged.get("reminderContent"))
    merged["lastMessageTime"] = _conversation_sort_time(latest)

    first_candidates = [value for value in [current.get("firstMessageTime"), incoming.get("firstMessageTime")] if value not in (None, "")]
    if first_candidates:
        merged["firstMessageTime"] = min(int(value) for value in first_candidates)

    merged["messageCount"] = int(current.get("messageCount") or 0) + int(incoming.get("messageCount") or 0)
    merged["unreadCount"] = max(int(current.get("unreadCount") or 0), int(incoming.get("unreadCount") or 0))
    merged["hasAiReply"] = bool(current.get("hasAiReply")) or bool(incoming.get("hasAiReply"))
    merged["lastIsAutoReply"] = bool(current.get("lastIsAutoReply")) or bool(incoming.get("lastIsAutoReply"))
    return merged


def _merge_online_conversation_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        key = _conversation_group_key(row)
        if not key:
            key = f"fallback:{len(grouped)}"
        grouped.setdefault(key, []).append(dict(row))

    merged_rows: list[dict[str, Any]] = []
    for group_rows in grouped.values():
        real_peer_rows = [
            row for row in group_rows
            if (peer := _normalize_party_id(row.get("peerUserId"))) and not peer.startswith("sid:")
        ]
        unique_real_peers = {
            _normalize_party_id(row.get("peerUserId"))
            for row in real_peer_rows
            if _normalize_party_id(row.get("peerUserId"))
        }
        if len(unique_real_peers) == 1:
            merged = group_rows[0]
            for row in group_rows[1:]:
                merged = _merge_online_conversation_pair(merged, row)
            merged_rows.append(merged)
        else:
            merged_rows.extend(group_rows)

    merged_rows.sort(key=_conversation_sort_time, reverse=True)
    return merged_rows


def _has_equivalent_base_message(candidate: dict[str, Any], base_messages: list[dict[str, Any]]) -> bool:
    if int(candidate.get("isAutoReply") or candidate.get("is_auto_reply") or 0) != 1:
        return False

    candidate_direction = _coerce_message_direction(candidate.get("direction"))
    candidate_sid = str(candidate.get("sid") or candidate.get("s_id") or "").replace("@goofish", "").strip()
    candidate_content = str(candidate.get("msgContent") or candidate.get("msg_content") or candidate.get("content") or "").strip()
    candidate_sender = _normalize_party_id(
        candidate.get("senderUserId") or candidate.get("sender_user_id") or candidate.get("from_user_id") or candidate.get("fromUserId")
    )
    candidate_receiver = _normalize_party_id(
        candidate.get("receiverUserId") or candidate.get("receiver_user_id") or candidate.get("to_user_id") or candidate.get("toUserId")
    )
    candidate_time = _normalize_message_time_value(
        candidate.get("messageTime") or candidate.get("message_time") or candidate.get("createdTime") or candidate.get("created_time")
    )

    for existing in base_messages:
        if _coerce_message_direction(existing.get("direction")) != candidate_direction:
            continue
        existing_sid = str(existing.get("sid") or existing.get("s_id") or "").replace("@goofish", "").strip()
        if candidate_sid and existing_sid and existing_sid != candidate_sid:
            continue
        existing_content = str(existing.get("msgContent") or existing.get("msg_content") or existing.get("content") or "").strip()
        if existing_content != candidate_content:
            continue
        existing_sender = _normalize_party_id(
            existing.get("senderUserId") or existing.get("sender_user_id") or existing.get("from_user_id") or existing.get("fromUserId")
        )
        if candidate_sender and existing_sender != candidate_sender:
            continue
        existing_receiver = _normalize_party_id(
            existing.get("receiverUserId") or existing.get("receiver_user_id") or existing.get("to_user_id") or existing.get("toUserId")
        )
        if candidate_receiver and existing_receiver != candidate_receiver:
            continue
        existing_time = _normalize_message_time_value(
            existing.get("messageTime") or existing.get("message_time") or existing.get("createdTime") or existing.get("created_time")
        )
        if candidate_time and existing_time and abs(candidate_time - existing_time) > 15000:
            continue
        return True
    return False


async def _load_ai_reply_context_messages(
    db: AsyncSession,
    account_id: int,
    conversation_ids: list[int],
    peer_user_id: str,
    s_id: str,
    base_messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    existing_keys = {_context_message_identity(item) for item in base_messages}
    peer_variants = _peer_id_variants(peer_user_id)
    if not conversation_ids and not peer_variants:
        return []
    query = """
            SELECT
                xm.id,
                xm.s_id AS conversation_id,
                xm.msg_content AS content,
                xm.content_type AS message_type,
                xm.direction,
                1 AS is_auto_reply,
                xm.message_time AS created_time,
                xm.sender_user_id AS from_user_id,
                xm.receiver_user_id AS to_user_id,
                c.peer_key,
                c.external_buyer_id
            FROM xianyu_chat_message xm
            JOIN xianyu_conversation c
                ON c.s_id = xm.s_id
               AND c.account_id = xm.account_id
            WHERE xm.account_id = :account_id
              AND xm.deleted = 0
              AND xm.direction = 'OUT'
    """
    params: dict[str, Any] = {
        "account_id": account_id,
    }
    filters: list[str] = []
    bind_params = []
    if conversation_ids:
        filters.append("c.id IN :conversation_ids")
        params["conversation_ids"] = conversation_ids
        bind_params.append(bindparam("conversation_ids", expanding=True))
    if peer_variants:
        filters.append("xm.receiver_user_id IN :peer_variants")
        params["peer_variants"] = peer_variants
        bind_params.append(bindparam("peer_variants", expanding=True))
    if not filters:
        return []
    query += f" AND ({' OR '.join(filters)})"
    message_times = [
        _normalize_message_time_value(item.get("messageTime") or item.get("message_time"))
        for item in base_messages
        if item.get("messageTime") or item.get("message_time")
    ]
    if message_times:
        start_ms = min(message_times) - 10 * 60 * 1000
        end_ms = max(message_times) + 10 * 60 * 1000
        params["start_time"] = start_ms
        params["end_time"] = end_ms
        query += " AND xm.message_time BETWEEN :start_time AND :end_time"
    rows = await db.execute(text(query).bindparams(*bind_params), params)
    messages: list[dict[str, Any]] = []
    for row in rows.mappings().all():
        message_time = _normalize_message_time_value(row.get("created_time"))
        peer_key = str(row.get("peer_key") or row.get("external_buyer_id") or "")
        sid = str(s_id or "").strip() or (peer_key[4:] if peer_key.startswith("sid:") else peer_key)
        candidate = {
            "id": f"legacy_auto_{row.get('id')}",
            "pnmId": "",
            "sid": sid,
            "contentType": 2 if str(row.get("message_type") or "") == "image" else 1,
            "msgContent": str(row.get("content") or ""),
            "senderUserId": str(row.get("from_user_id") or ""),
            "receiverUserId": str(row.get("to_user_id") or ""),
            "peerExternalUid": str(row.get("external_buyer_id") or ""),
            "messageTime": message_time,
            "direction": _coerce_message_direction(row.get("direction")),
            "readStatus": 1,
            "isAutoReply": 1,
            "conversationId": row.get("conversation_id"),
        }
        if _context_message_identity(candidate) in existing_keys:
            continue
        messages.append(candidate)
    return messages


async def _resolve_conversation_ids_for_context(
    db: AsyncSession,
    account_id: int,
    s_id: str,
    peer_user_id: str,
) -> list[int]:
    conditions: list[str] = []
    params: dict[str, Any] = {
        "account_id": account_id,
    }
    if s_id:
        s_id_goofish = f"{s_id}@goofish"
        params["sid_key"] = f"sid:{s_id}"
        params["sid_key_goofish"] = f"sid:{s_id_goofish}"
        params["external_sid"] = s_id
        params["external_sid_goofish"] = s_id_goofish
        conditions.append("""
            (
                c.peer_key COLLATE utf8mb4_unicode_ci IN (:sid_key, :sid_key_goofish)
                OR c.external_buyer_id COLLATE utf8mb4_unicode_ci IN (:sid_key, :sid_key_goofish, :external_sid, :external_sid_goofish)
            )
        """)
    direct_ids: list[int] = []
    if peer_user_id:
        peer_user_id_goofish = f"{peer_user_id}@goofish" if not peer_user_id.endswith("@goofish") else peer_user_id
        params["peer_user_id"] = peer_user_id
        params["peer_user_id_goofish"] = peer_user_id_goofish
        conditions.append("""
            (
                c.external_buyer_id COLLATE utf8mb4_unicode_ci IN (:peer_user_id, :peer_user_id_goofish)
                OR c.peer_external_uid COLLATE utf8mb4_unicode_ci IN (:peer_user_id, :peer_user_id_goofish)
                OR c.peer_key COLLATE utf8mb4_unicode_ci IN (:peer_user_id, :peer_user_id_goofish)
            )
        """)
    ids = set(direct_ids)
    if conditions:
        rows = await db.execute(
            text(f"""
                SELECT DISTINCT c.id
                FROM xianyu_conversation c WHERE c.account_id = :account_id
                  AND (
                    {" OR ".join(conditions)}
                  )
            """),
            params
        )
        ids.update(int(row[0]) for row in rows.all() if row and row[0] is not None)
    return sorted(ids)


async def _merge_context_messages_with_ai_replies(
    db: AsyncSession,
    account_id: int,
    base_messages: list[dict[str, Any]],
    s_id: str,
    peer_user_id: str,
    *,
    filter_base_messages_by_peer: bool = True,
) -> list[dict[str, Any]]:
    conversation_ids = await _resolve_conversation_ids_for_context(db, account_id, s_id, peer_user_id)
    ai_messages = await _load_ai_reply_context_messages(
        db, account_id, conversation_ids, peer_user_id, s_id, base_messages
    )
    if peer_user_id and filter_base_messages_by_peer:
        base_messages = [
            item for item in base_messages
            if _message_matches_peer_user(item, peer_user_id) or _message_has_unknown_peer_identity(item)
        ]
    if peer_user_id:
        ai_messages = [item for item in ai_messages if _message_matches_peer_user(item, peer_user_id)]
    ai_messages = [item for item in ai_messages if not _has_equivalent_base_message(item, base_messages)]
    merged = list(base_messages) + ai_messages
    deduped: dict[str, dict[str, Any]] = {}
    for item in merged:
        deduped[_context_message_identity(item)] = item
    return sorted(
        deduped.values(),
        key=lambda item: (
            _normalize_message_time_value(item.get("messageTime") or item.get("message_time")),
            str(item.get("id") or ""),
        ),
    )


def _merge_context_source_messages(
    base_messages: list[dict[str, Any]],
    live_messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    deduped: dict[str, dict[str, Any]] = {}
    for item in list(base_messages) + list(live_messages):
        if not _is_displayable_message(item):
            continue
        deduped[_context_message_identity(item)] = item
    return sorted(
        deduped.values(),
        key=lambda item: (
            _normalize_message_time_value(item.get("messageTime") or item.get("message_time")),
            str(item.get("id") or ""),
        ),
    )


async def _resolve_live_context_sid(
    db: AsyncSession,
    account_id: int,
    peer_user_id: str,
) -> str:
    target_peer = _normalize_party_id(peer_user_id)
    if not target_peer:
        return ""
    conversations = await _fetch_live_online_conversations(
        db, account_id,
        limit=100,
    )
    for conversation in conversations:
        if _normalize_party_id(conversation.get("peerUserId")) == target_peer:
            return _normalize_sid_value(
                conversation.get("sid")
                or conversation.get("s_id")
                or conversation.get("conversationId")
            )
    return ""


async def _fetch_live_online_conversations(
    db: AsyncSession,
    account_id: int,
    limit: int = 50,
    user_id: int | None = None,
) -> list[dict[str, Any]]:
    del user_id
    try:
        from .ws_client import ws_manager

        client = ws_manager.get_client(account_id)
        if not client or not getattr(client, "is_connected", False):
            return []

        seller_external_uid = await _load_seller_external_uid(db, account_id)
        seller_external_uid = seller_external_uid or str(getattr(client, "unb", "") or "")
        # This helper is read-only. End the implicit SELECT transaction before
        # waiting on IM so the caller's request connection returns to the pool.
        await db.rollback()
        page_limit = max(min(int(limit or 50), 50), 1)
        cursor: int | None = None
        page = 0
        conversations: list[dict[str, Any]] = []

        while len(conversations) < max(int(limit or 50), 1) and page < 3:
            body = await client.list_conversations(start_timestamp=cursor, limit=page_limit)
            items = body.get("userConvs", []) if isinstance(body, dict) else []
            if not items:
                break
            for item in items:
                parsed = _parse_live_conversation(item, seller_external_uid)
                if parsed:
                    conversations.append(parsed)
            has_more = body.get("hasMore", False) if isinstance(body, dict) else False
            has_more = has_more if isinstance(has_more, bool) else str(has_more) == "1"
            cursor = body.get("nextCursor") if isinstance(body, dict) else None
            if not has_more or cursor in (None, ""):
                break
            page += 1
        return conversations[:max(int(limit or 50), 1)]
    except Exception as exc:
        logger.warning(
            "fetch live conversations failed accountId=%d errorType=%s",
            account_id,
            type(exc).__name__,
        )
        return []


async def _fetch_live_context_messages(
    db: AsyncSession,
    account_id: int,
    s_id: str,
    limit: int = 50,
    peer_user_id: str = "",
) -> list[dict[str, Any]]:
    try:
        from .ws_client import ws_manager

        client = ws_manager.get_client(account_id)
        if not client or not getattr(client, "is_connected", False):
            return []

        resolved_sid = _normalize_sid_value(s_id)
        if not resolved_sid and peer_user_id:
            resolved_sid = await _resolve_live_context_sid(db, account_id, peer_user_id)
        if not resolved_sid:
            return []

        seller_external_uid = await _load_seller_external_uid(db, account_id)
        seller_external_uid = seller_external_uid or str(getattr(client, "unb", "") or "")
        # Local context rows have already been materialized. Release their
        # read transaction before the potentially slow history provider call.
        await db.rollback()
        page_limit = max(min(int(limit or 50), 50), 20)
        cursor: int | None = None
        page = 0
        messages: list[dict[str, Any]] = []

        while len(messages) < max(int(limit or 50), 1) and page < 4:
            body = await client.list_messages(resolved_sid, start_timestamp=cursor, limit=page_limit)
            models = body.get("userMessageModels", []) if isinstance(body, dict) else []
            if not models:
                break
            for model in models:
                parsed = _parse_live_history_message(
                    model,
                    sid=resolved_sid,
                    seller_external_uid=seller_external_uid,
                    peer_user_id=peer_user_id,
                )
                if parsed:
                    messages.append(parsed)
            has_more = body.get("hasMore", False) if isinstance(body, dict) else False
            has_more = has_more if isinstance(has_more, bool) else str(has_more) == "1"
            cursor = body.get("nextCursor") if isinstance(body, dict) else None
            if not has_more or cursor in (None, ""):
                break
            page += 1

        if peer_user_id:
            messages = [
                item for item in messages
                if _message_matches_peer_user(item, peer_user_id) or _message_has_unknown_peer_identity(item)
            ]
        return _merge_context_source_messages([], messages)[:max(int(limit or 50), 1)]
    except Exception:
        logger.warning("fetch live context messages failed accountId=%d", account_id, exc_info=True)
        return []


async def _finalize_context_messages(
    db: AsyncSession,
    account_id: int,
    base_messages: list[dict[str, Any]],
    s_id: str,
    peer_user_id: str,
    limit: int,
    offset: int,
    *,
    filter_base_messages_by_peer: bool = True,
) -> tuple[list[dict[str, Any]], int]:
    fetch_limit = max(int(limit or 0) + int(offset or 0), int(limit or 0), 50)
    live_messages = await _fetch_live_context_messages(
        db, account_id,
        s_id=s_id,
        limit=fetch_limit,
        peer_user_id=peer_user_id,
    )
    merged_source = _merge_context_source_messages(base_messages, live_messages)
    if not merged_source:
        return [], 0

    merged = await _merge_context_messages_with_ai_replies(
        db, account_id,
        merged_source,
        s_id,
        peer_user_id,
        filter_base_messages_by_peer=filter_base_messages_by_peer,
    )
    total = len(merged)
    if total == 0:
        return [], 0
    # 从最新的消息开始分页（往前翻更旧的消息）
    # offset=0 返回最新 limit 条；offset=limit 返回更旧的 limit 条
    start = max(0, total - offset - limit)
    end = max(0, total - offset)
    return merged[start:end], total


async def _backfill_online_conversation_records(
    db: AsyncSession,
    account_id: int,
    conversations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not conversations:
        return conversations

    sid_lookup_keys: list[str] = []
    peer_lookup_keys: list[str] = []
    raw_sid_values: list[str] = []
    for conv in conversations:
        sid = _normalize_sid_value(
            conv.get("sid")
            or conv.get("sId")
            or conv.get("conversationId")
        )
        if sid:
            sid_lookup_keys.extend([
                f"sid:{sid}",
                f"sid:{sid}@goofish",
                sid,
                f"{sid}@goofish",
            ])
            raw_sid_values.append(sid)
        peer_user_id = _normalize_party_id(
            conv.get("peerUserId")
            or conv.get("peerExternalUid")
            or conv.get("externalBuyerId")
        )
        if peer_user_id and not peer_user_id.startswith("sid:"):
            peer_lookup_keys.extend(_peer_id_variants(peer_user_id))

    conversation_rows: list[dict[str, Any]] = []
    if sid_lookup_keys or peer_lookup_keys:
        where_parts: list[str] = []
        bind_params = []
        params: dict[str, Any] = {"account_id": account_id}
        if sid_lookup_keys:
            params["sid_keys"] = list({key for key in sid_lookup_keys if key})
            sid_where = (
                "(c.peer_key COLLATE utf8mb4_unicode_ci IN :sid_keys "
                "OR c.external_buyer_id COLLATE utf8mb4_unicode_ci IN :sid_keys)"
            )
            # 也通过 s_id 列匹配（peer_key 已升级为真实 UID 时兜底）
            if raw_sid_values:
                params["raw_sids"] = list({s for s in raw_sid_values if s})
                sid_where += (
                    " OR c.s_id COLLATE utf8mb4_unicode_ci IN :raw_sids"
                )
                bind_params.append(bindparam("raw_sids", expanding=True))
            where_parts.append(sid_where)
            bind_params.append(bindparam("sid_keys", expanding=True))
        if peer_lookup_keys:
            params["peer_keys"] = list({key for key in peer_lookup_keys if key})
            where_parts.append(
                "(c.external_buyer_id COLLATE utf8mb4_unicode_ci IN :peer_keys "
                "OR c.peer_external_uid COLLATE utf8mb4_unicode_ci IN :peer_keys "
                "OR c.peer_key COLLATE utf8mb4_unicode_ci IN :peer_keys)"
            )
            bind_params.append(bindparam("peer_keys", expanding=True))
        if where_parts:
            rows = await db.execute(
                text(f"""
                    SELECT
                        c.id,
                        c.peer_key,
                        c.external_buyer_id,
                        c.peer_external_uid,
                        c.buyer_name,
                        c.buyer_avatar,
                        c.goods_id,
                        c.goods_title,
                        c.goods_cover_pic,
                        c.s_id,
                        c.status,
                        c.updated_time,
                        c.last_message_time
                    FROM xianyu_conversation c
                    WHERE c.account_id = :account_id
                      AND ({' OR '.join(where_parts)})
                    ORDER BY COALESCE(c.updated_time, c.last_message_time) DESC, c.id DESC
                """).bindparams(*bind_params),
                params,
            )
            conversation_rows = [dict(row) for row in rows.mappings().all()]

    conversation_by_sid: dict[str, dict[str, Any]] = {}
    conversation_by_sid_goods: dict[tuple[str, str], dict[str, Any]] = {}
    conversation_by_peer: dict[str, dict[str, Any]] = {}
    conversation_by_peer_goods: dict[tuple[str, str], dict[str, Any]] = {}
    for row in conversation_rows:
        row_goods_id = str(row.get("goods_id") or "").strip()
        row_sid_candidates = [
            candidate
            for candidate in (
                _normalize_sid_value(row.get("peer_key")),
                _normalize_sid_value(row.get("external_buyer_id")),
                _normalize_sid_value(row.get("peer_external_uid")),
            )
            if candidate
        ]
        row_peer_candidates = [
            candidate
            for candidate in (
                _normalize_party_id(row.get("external_buyer_id")),
                _normalize_party_id(row.get("peer_external_uid")),
                _normalize_party_id(row.get("peer_key")),
            )
            if candidate and not candidate.startswith("sid:")
        ]
        for sid in row_sid_candidates:
            conversation_by_sid.setdefault(sid, row)
            if row_goods_id:
                conversation_by_sid_goods.setdefault((sid, row_goods_id), row)
        for peer in row_peer_candidates:
            conversation_by_peer.setdefault(peer, row)
            if row_goods_id:
                conversation_by_peer_goods.setdefault((peer, row_goods_id), row)

    for conv in conversations:
        sid = _normalize_sid_value(
            conv.get("sid")
            or conv.get("sId")
            or conv.get("conversationId")
        )
        goods_id = str(conv.get("goodsId") or "").strip()
        peer_user_id = _normalize_party_id(
            conv.get("peerUserId")
            or conv.get("peerExternalUid")
            or conv.get("externalBuyerId")
        )
        matched_row = None
        if sid and goods_id:
            matched_row = conversation_by_sid_goods.get((sid, goods_id))
        if matched_row is None and peer_user_id and goods_id:
            matched_row = conversation_by_peer_goods.get((peer_user_id, goods_id))
        if matched_row is None and sid:
            matched_row = conversation_by_sid.get(sid)
        if matched_row is None and peer_user_id:
            matched_row = conversation_by_peer.get(peer_user_id)
        if not matched_row:
            continue

        existing_conversation_id = conv.get("conversationId") or conv.get("conversationDbId") or conv.get("id")
        try:
            existing_conversation_id = int(existing_conversation_id)
        except (TypeError, ValueError):
            existing_conversation_id = 0
        matched_conversation_id = int(matched_row.get("id") or 0)
        if existing_conversation_id <= 0 and matched_conversation_id > 0:
            conv["conversationId"] = matched_conversation_id
            conv["conversationDbId"] = matched_conversation_id
            conv["id"] = matched_conversation_id

        row_peer_user_id = _normalize_party_id(
            matched_row.get("external_buyer_id") or matched_row.get("peer_external_uid")
        )
        if row_peer_user_id and (not peer_user_id or peer_user_id.startswith("sid:")):
            conv["peerUserId"] = _ensure_goofish_suffix(row_peer_user_id)
        if matched_row.get("peer_key") and not conv.get("peerKey"):
            conv["peerKey"] = matched_row.get("peer_key")
        if matched_row.get("buyer_name") and not conv.get("peerUserName"):
            conv["peerUserName"] = matched_row.get("buyer_name")
        if matched_row.get("buyer_avatar") and not conv.get("buyerAvatar"):
            conv["buyerAvatar"] = matched_row.get("buyer_avatar")
        if matched_row.get("goods_id") and not conv.get("goodsId"):
            conv["goodsId"] = str(matched_row.get("goods_id"))
        if matched_row.get("goods_title") and not conv.get("goodsTitle"):
            conv["goodsTitle"] = matched_row.get("goods_title")
        if matched_row.get("goods_cover_pic") and not conv.get("goodsCoverPic"):
            conv["goodsCoverPic"] = matched_row.get("goods_cover_pic")
        if matched_row.get("s_id") and not conv.get("sid"):
            conv["sid"] = matched_row.get("s_id")
        if matched_row.get("status") is not None:
            conv["conversationStatus"] = matched_row.get("status")
    return conversations


async def _ensure_online_conversation_records(
    db: AsyncSession,
    account_id: int,
    conversations: list[dict[str, Any]],
    seller_external_uid: str = "",
) -> list[dict[str, Any]]:
    if not conversations:
        return conversations

    conversations = await _backfill_online_conversation_records(
        db, account_id, conversations
    )

    missing_rows: list[tuple[dict[str, Any], str]] = []
    for conv in conversations:
        existing_id = conv.get("conversationId") or conv.get("conversationDbId") or conv.get("id")
        try:
            if int(existing_id or 0) > 0:
                continue
        except (TypeError, ValueError):
            pass
        sid = _normalize_sid_value(
            conv.get("sid")
            or conv.get("sId")
            or conv.get("conversationId")
        )
        if sid:
            missing_rows.append((conv, sid))

    if not missing_rows:
        return conversations

    normalized_seller_uid = _normalize_party_id(seller_external_uid)
    if not normalized_seller_uid:
        normalized_seller_uid = await _load_seller_external_uid(db, account_id)

    changed = False
    for conv, sid in missing_rows:
        sid_key = f"sid:{sid}"
        sid_key_goofish = f"{sid_key}@goofish"
        peer_user_id = _normalize_party_id(
            conv.get("peerUserId")
            or conv.get("peerExternalUid")
            or conv.get("externalBuyerId")
        )
        goods_id = str(conv.get("goodsId") or "").strip()
        goods_title = str(conv.get("goodsTitle") or "").strip()
        goods_cover_pic = _normalize_image_url(conv.get("goodsCoverPic"))
        buyer_name = normalize_peer_name(str(conv.get("peerUserName") or "").strip())
        buyer_avatar = _normalize_image_url(conv.get("buyerAvatar"))
        last_message_content = str(
            conv.get("lastMessage")
            or conv.get("reminderContent")
            or ""
        ).strip()
        last_message_time_ms = _normalize_message_time_value(
            conv.get("lastMessageTime")
            or conv.get("firstMessageTime")
        )
        last_message_time = (
            datetime.fromtimestamp(last_message_time_ms / 1000)
            if last_message_time_ms > 0
            else datetime.now()
        )
        try:
            unread_count = max(0, int(conv.get("unreadCount") or 0))
        except (TypeError, ValueError):
            unread_count = 0
        try:
            conversation_status = int(conv.get("conversationStatus") or 0)
        except (TypeError, ValueError):
            conversation_status = 0

        existing = await db.execute(
            text("""
                SELECT
                    c.id,
                    c.external_buyer_id,
                    c.peer_external_uid
                FROM xianyu_conversation c
                WHERE c.account_id = :account_id
                  AND (
                    c.peer_key COLLATE utf8mb4_unicode_ci = :sid_key COLLATE utf8mb4_unicode_ci
                    OR c.peer_key COLLATE utf8mb4_unicode_ci = :sid_key_goofish COLLATE utf8mb4_unicode_ci
                    OR c.external_buyer_id COLLATE utf8mb4_unicode_ci = :sid_key COLLATE utf8mb4_unicode_ci
                    OR c.external_buyer_id COLLATE utf8mb4_unicode_ci = :sid_key_goofish COLLATE utf8mb4_unicode_ci
                  )
                ORDER BY c.id DESC
                LIMIT 1
            """),
            {
                "account_id": account_id,
                "sid_key": sid_key,
                "sid_key_goofish": sid_key_goofish,
            },
        )
        existing_row = existing.mappings().first()

        if existing_row:
            current_external_buyer_id = str(existing_row.get("external_buyer_id") or "").strip()
            current_peer_external_uid = str(existing_row.get("peer_external_uid") or "").strip()
            external_buyer_id = peer_user_id if peer_user_id else current_external_buyer_id
            peer_external_uid = peer_user_id if peer_user_id else current_peer_external_uid
            await db.execute(
                text("""
                    UPDATE xianyu_conversation
                    SET seller_external_uid = COALESCE(NULLIF(:seller_external_uid, ''), seller_external_uid),
                        external_buyer_id = CASE
                            WHEN :external_buyer_id != ''
                             AND (
                                external_buyer_id IS NULL
                                OR external_buyer_id = ''
                                OR external_buyer_id LIKE 'sid:%'
                             )
                            THEN :external_buyer_id
                            ELSE external_buyer_id
                        END,
                        peer_external_uid = CASE
                            WHEN :peer_external_uid != ''
                             AND (peer_external_uid IS NULL OR peer_external_uid = '')
                            THEN :peer_external_uid
                            ELSE peer_external_uid
                        END,
                        buyer_name = COALESCE(NULLIF(:buyer_name, ''), buyer_name),
                        buyer_avatar = COALESCE(NULLIF(:buyer_avatar, ''), buyer_avatar),
                        goods_id = COALESCE(NULLIF(:goods_id, ''), goods_id),
                        goods_title = COALESCE(NULLIF(:goods_title, ''), goods_title),
                        goods_cover_pic = COALESCE(NULLIF(:goods_cover_pic, ''), goods_cover_pic),
                        s_id = COALESCE(NULLIF(s_id, ''), :sid),
                        last_message_time = CASE
                            WHEN last_message_time IS NULL OR :last_message_time > last_message_time
                            THEN :last_message_time
                            ELSE last_message_time
                        END,
                        last_message_content = COALESCE(NULLIF(:last_message_content, ''), last_message_content),
                        unread_count = GREATEST(COALESCE(unread_count, 0), :unread_count),
                        updated_time = NOW()
                    WHERE id = :id
                """),
                {
                    "id": int(existing_row.get("id")),
                    "seller_external_uid": normalized_seller_uid,
                    "external_buyer_id": external_buyer_id,
                    "peer_external_uid": peer_external_uid,
                    "buyer_name": buyer_name,
                    "buyer_avatar": buyer_avatar,
                    "goods_id": goods_id,
                    "goods_title": goods_title,
                    "goods_cover_pic": goods_cover_pic,
                    "sid": sid,
                    "last_message_time": last_message_time,
                    "last_message_content": last_message_content,
                    "unread_count": unread_count,
                },
            )
            changed = True
            continue

        await db.execute(
            text("""
                INSERT INTO xianyu_conversation (
                    account_id,
                    seller_external_uid,
                    peer_key,
                    external_buyer_id,
                    peer_external_uid,
                    buyer_name,
                    buyer_avatar,
                    goods_title,
                    goods_id,
                    goods_cover_pic,
                    s_id,
                    status,
                    last_message_time,
                    last_message_content,
                    unread_count,
                    created_time,
                    updated_time
                ) VALUES (
                    :account_id,
                    :seller_external_uid,
                    :peer_key,
                    :external_buyer_id,
                    :peer_external_uid,
                    :buyer_name,
                    :buyer_avatar,
                    :goods_title,
                    :goods_id,
                    :goods_cover_pic,
                    :sid,
                    :status,
                    :last_message_time,
                    :last_message_content,
                    :unread_count,
                    NOW(),
                    NOW()
                )
            """),
            {
                "account_id": account_id,
                "seller_external_uid": normalized_seller_uid or None,
                "peer_key": sid_key,
                "external_buyer_id": peer_user_id or sid_key,
                "peer_external_uid": peer_user_id or None,
                "buyer_name": buyer_name or None,
                "buyer_avatar": buyer_avatar or None,
                "goods_title": goods_title or None,
                "goods_id": goods_id or None,
                "goods_cover_pic": goods_cover_pic or None,
                "sid": sid,
                "status": conversation_status,
                "last_message_time": last_message_time,
                "last_message_content": last_message_content or None,
                "unread_count": unread_count,
            },
        )
        changed = True

    if changed:
        await db.commit()
    # 无论是否有变更，都回填数据库 ID，确保前端能通过 id 调用标记已读等接口
    conversations = await _backfill_online_conversation_records(
        db, account_id, conversations
    )
    return conversations


async def _apply_ai_reply_preview(
    db: AsyncSession,
    account_id: int,
    conversations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not conversations:
        return conversations

    conversations = await _backfill_online_conversation_records(
        db, account_id, conversations
    )

    peer_variants = sorted({
        variant
        for conv in conversations
        for variant in _peer_id_variants(str(conv.get("peerUserId") or conv.get("peerExternalUid") or conv.get("externalBuyerId") or ""))
    })
    if not peer_variants:
        return conversations
    rows = await db.execute(
        text("""
            SELECT
                xm.id,
                xm.s_id AS conversation_id,
                xm.receiver_user_id AS to_user_id,
                xm.msg_content AS content,
                xm.content_type AS message_type,
                xm.direction,
                1 AS is_auto_reply,
                xm.message_time AS created_time
            FROM xianyu_chat_message xm
            WHERE xm.account_id = :account_id
              AND xm.deleted = 0
              AND xm.direction = 'OUT'
              AND xm.receiver_user_id IN :peer_variants
            ORDER BY xm.message_time DESC, xm.id DESC
        """).bindparams(bindparam("peer_variants", expanding=True)),
        {
            "account_id": account_id,
            "peer_variants": peer_variants,
        }
    )
    ai_rows = [dict(row) for row in rows.mappings().all()]
    for conv in conversations:
        variants = set(_peer_id_variants(str(conv.get("peerUserId") or conv.get("peerExternalUid") or conv.get("externalBuyerId") or "")))
        if not variants:
            conv["hasAiReply"] = False
            conv["lastIsAutoReply"] = False
            continue
        current_time = _normalize_message_time_value(conv.get("lastMessageTime"))
        first_time = _normalize_message_time_value(conv.get("firstMessageTime") or conv.get("lastMessageTime"))
        earliest_allowed = first_time - 10 * 60 * 1000
        latest_allowed = current_time + 10 * 60 * 1000
        ai_row = next((
            row for row in ai_rows
            if str(row.get("to_user_id") or "") in variants
            and earliest_allowed <= _normalize_message_time_value(row.get("created_time")) <= latest_allowed
        ), None)
        if not ai_row:
            conv["hasAiReply"] = False
            conv["lastIsAutoReply"] = False
            continue
        ai_time = _normalize_message_time_value(ai_row.get("created_time"))
        conv["hasAiReply"] = True
        conv["lastAiReplyTime"] = ai_time
        if ai_time >= current_time:
            conv["lastMessage"] = str(ai_row.get("content") or "")
            conv["lastContentType"] = 2 if str(ai_row.get("message_type") or "") == "image" else 1
            conv["lastIsAutoReply"] = True
        else:
            conv["lastIsAutoReply"] = False
    return conversations


async def _resolve_peer_id(
    db: AsyncSession,
    account_id: int,
    msg: dict,
) -> str:
    """解析会话的 peer_id（对应 xianyu_conversation.external_buyer_id）。

    优先级：
    1. 根据方向取 senderUserId（收）或 receiverUserId（发）
    2. 如果 s_id 下已有当前账号的会话记录，优先复用已有的 external_buyer_id，
       确保同一账号内同一会话的 external_buyer_id 保持一致
    3. 从当前账号历史消息中查找 peer_id（收：sender_user_id；发：receiver_user_id）
    4. 从当前账号已有会话中查找 external_buyer_id
    5. 兜底使用 sid:xxx 作为 peer_id
    """
    direction = str(msg.get("direction") or "IN").upper()
    sender_id = str(msg.get("senderUserId") or "")
    receiver_id = str(msg.get("receiverUserId") or "")
    s_id = str(msg.get("sId") or "")

    # 首选：根据方向确定 peer_id
    peer_id = receiver_id if direction == "OUT" and receiver_id else sender_id

    # 如果解析出的 peer_id 等于卖家自己，不能拿它当买家会话键。
    # 真实日志里大量商品卡片消息 senderUserId/receiverUserId 都是卖家 external_uid，
    # 旧逻辑会把所有 sId 聚合到同一个买家，导致前端只看到 1 个会话。
    seller_uid = str(msg.get("sellerExternalUid") or "")
    # 去掉 @goofish 后缀后比较（WS 协议中有时会带后缀）
    peer_id_clean = peer_id.replace("@goofish", "").strip() if peer_id else ""
    seller_uid_clean = seller_uid.replace("@goofish", "").strip() if seller_uid else ""
    if peer_id and seller_uid and peer_id_clean == seller_uid_clean and s_id:
        logger.info("解析会话对端时检测到卖家自身，改用会话标识兜底 accountId=%d", account_id)
        return f"sid:{s_id}"

    # 如果得到了有效的真实 peer_id，则直接使用；不要跨 sId 复用旧 external_buyer_id，
    # 否则在闲鱼协议缺失 senderUserId 的情况下会把多个会话错误合并。
    if peer_id:
        return peer_id

    # peer_id 为空时的兜底逻辑
    if s_id:
        # 兜底1：从当前账号历史消息中按方向查找 peer_id
        history_field = "receiver_user_id" if direction == "OUT" else "sender_user_id"
        recent = await db.execute(
            text(f"""
                SELECT {history_field}
                FROM xianyu_chat_message WHERE account_id = :account_id
                  AND s_id COLLATE utf8mb4_unicode_ci = :s_id COLLATE utf8mb4_unicode_ci AND deleted = 0
                  AND {history_field} IS NOT NULL AND {history_field} != ''
                ORDER BY id DESC
                LIMIT 1
            """),
            {"account_id": account_id, "s_id": s_id}
        )
        peer_id = recent.scalar_one_or_none() or ""
        if peer_id and seller_uid and str(peer_id).replace("@goofish", "").strip() == seller_uid_clean:
            logger.info("历史会话对端为卖家自身，继续使用会话标识 accountId=%d", account_id)
            peer_id = ""

        # 兜底2：通过当前账号的 xianyu_chat_message/s_id 查找已有会话的 external_buyer_id
        conv = await db.execute(
            text("""
                SELECT
                    c.external_buyer_id,
                    c.peer_external_uid,
                    c.peer_key
                FROM xianyu_conversation c WHERE c.account_id = :account_id
                  AND (
                    c.peer_key COLLATE utf8mb4_unicode_ci = :sid_key COLLATE utf8mb4_unicode_ci
                    OR c.external_buyer_id COLLATE utf8mb4_unicode_ci = :sid_key COLLATE utf8mb4_unicode_ci
                    OR c.peer_key COLLATE utf8mb4_unicode_ci = :sid_key_goofish COLLATE utf8mb4_unicode_ci
                    OR c.external_buyer_id COLLATE utf8mb4_unicode_ci = :sid_key_goofish COLLATE utf8mb4_unicode_ci
                  )
                ORDER BY c.id DESC
                LIMIT 1
            """),
            {
                "account_id": account_id,
                "sid_key": f"sid:{s_id}",
                "sid_key_goofish": f"sid:{s_id}@goofish",
            }
        )
        existing_row = conv.mappings().first() or {}
        existing_peer = (
            existing_row.get("peer_external_uid")
            or existing_row.get("external_buyer_id")
            or existing_row.get("peer_key")
            or ""
        )
        if existing_peer:
            if seller_uid and str(existing_peer).replace("@goofish", "").strip() == seller_uid_clean:
                logger.info("已有会话对端为卖家自身，改用会话标识 accountId=%d", account_id)
                return f"sid:{s_id}"
            if str(existing_peer).startswith("sid:"):
                return str(existing_peer)
            if peer_id and peer_id != existing_peer:
                logger.info("会话兜底使用已有对端标识 accountId=%d direction=%s", account_id, direction)
            return str(existing_peer)

        if peer_id:
            return str(peer_id)

    # 兜底：当所有方式都无法解析出 peer_id 时，使用 sId 作为 peer_id，
    # 确保会话记录能被创建，后续消息可以通过 sId 关联到该会话。
    if s_id:
        logger.info("会话对端解析使用会话标识兜底 accountId=%d", account_id)
        return f"sid:{s_id}"

    return ""


def _generate_message_uid(msg: dict, seller_external_uid: str = "") -> str:
    """生成稳定的消息唯一ID（message_uid）。

    用于去重。优先级：
    1. pnm_id 非空 → 直接使用 pnm_id
    2. pnm_id 为空 → 与 validate_parsed_message 使用相同的兜底哈希

    注意：不包含 message_time。原因：
    - OUT 消息先入库时 messageTime=0（无服务端时间戳），推送回环到来时带真实时间戳。
    - 如果 uid 包含 message_time，两条消息的 uid 不同，去重无法命中，
      导致同一消息被存两次（一条本地时间，一条服务端时间），引发排序错乱。
    - 排除 message_time 后，去重能正确命中，由 save_chat_message 的
      去重更新逻辑用服务端时间戳覆盖 message_time。

    兜底哈希必须与 ws_protocol.validate_parsed_message 的兜底逻辑完全一致，
    否则 save_chat_message 存储的 message_uid 与 WS 回环去重时计算的 content_hash
    不匹配，会导致回环消息被当作新消息二次入库。
    """
    pnm_id = str(msg.get("pnmId") or "")
    if pnm_id:
        return pnm_id
    s_id = str(msg.get("sId") or "")
    sender = str(msg.get("senderUserId") or "")
    receiver = str(msg.get("receiverUserId") or "")
    content = str(msg.get("msgContent") or "")
    content_type = int(msg.get("contentType") or 1)
    reminder = str(msg.get("reminderContent") or "")
    fallback_raw = "|".join([
        s_id,
        sender,
        receiver,
        str(content_type),
        content,
        reminder,
    ])
    return hashlib.sha256(fallback_raw.encode()).hexdigest()[:24]


def stable_chat_message_uid(msg: dict, seller_external_uid: str = "") -> str:
    """Return the same stable identity used by chat-message persistence.

    Side-effect runtimes reduce this value to a SHA-256 digest before durable
    storage.  Exposing the calculation avoids coupling those runtimes to raw
    buyer content or to a database re-query after the message INSERT.
    """
    return _generate_message_uid(msg, seller_external_uid)


def _extract_time_from_raw_payload(raw_payload: Any) -> int:
    """从 rawPayload 中提取消息时间戳（毫秒）。

    修复背景：当 WebSocket 协议解析器无法从字段 "5" 提取 messageTime 时，
    原实现直接用当前时间兜底，导致同一批入库的消息时间戳几乎相同（差几毫秒），
    破坏消息时间线排序。

    参考目标项目（xianyu-auto-reply/backend-web/app/api/routes/chat_new.py）：
    - _parse_message 第 817 行：msg_time = message.get("createAt", 0) or message.get("time", 0)
    - _parse_conversation 第 666 行：last_msg_time = conv.get("modifyTime", 0)

    本函数递归搜索 rawPayload 中的常见时间字段：
    - createAt / time / modifyTime / createTime / messageTime
    - 同时兼容嵌套在 message / extension / content 等子结构中的时间字段

    Returns:
        毫秒时间戳；提取失败返回 0
    """
    if not raw_payload:
        return 0

    # 优先字段名（参考目标项目的解析顺序）
    priority_keys = ("createAt", "time", "modifyTime", "createTime", "messageTime")

    def _try_parse_int(value: Any) -> int:
        if value is None:
            return 0
        if isinstance(value, (int, float)):
            return int(value)
        text = str(value).strip()
        if not text:
            return 0
        try:
            return int(text)
        except (ValueError, TypeError):
            return 0

    def _search(obj: Any, depth: int = 0) -> int:
        if depth > 5 or not obj:
            return 0
        if isinstance(obj, dict):
            # 优先在当前层级查找已知时间字段
            for key in priority_keys:
                if key in obj:
                    candidate = _try_parse_int(obj[key])
                    # 合理的时间戳范围：2000-01-01 ~ 2100-01-01（毫秒）
                    if 946684800000 < candidate < 4102444800000:
                        return candidate
            # 递归查找子节点（限制深度避免性能问题）
            for value in obj.values():
                result = _search(value, depth + 1)
                if result:
                    return result
        elif isinstance(obj, list):
            for item in obj:
                result = _search(item, depth + 1)
                if result:
                    return result
        return 0

    try:
        return _search(raw_payload)
    except Exception:
        logger.debug("从 rawPayload 提取时间失败")
        return 0


async def save_chat_message(
    db: AsyncSession,
    account_id: int,
    msg: dict,
    seller_external_uid: str = "",
    sync_legacy_message: bool = False,
) -> Optional[int]:
    """保存聊天消息到 xianyu_chat_message 表（去重）。

    Args:
        db: 数据库会话
        account_id: 闲鱼账号ID
        msg: 解析后的消息字典（会被同步更新 pnmId，以便调用方用于 SSE 广播
            等场景与 DB 记录保持同一身份）
        seller_external_uid: 卖家外部UID（externalUid/unb），用于稳定身份
        sync_legacy_message: 已废弃，保留参数仅为向后兼容。旧实现会向同一张表
            二次 INSERT 导致前端出现重复消息，已移除该逻辑。

    Returns:
        消息 ID 或 None（已存在时）
    """
    # === Step 1: 消息校验 ===
    from .ws_protocol import validate_parsed_message
    if seller_external_uid:
        msg["sellerExternalUid"] = seller_external_uid
    validated = validate_parsed_message(msg)
    # validate_parsed_message 在 pnmId 为空时会生成兜底哈希。它返回的是副本，
    # 这里把生成的 pnmId 回写到调用方传入的 msg，确保 SSE 广播、DB 存储、
    # WS 回环去重使用同一身份，避免前端出现重复消息。
    generated_pnm_id = validated.get("pnmId") or ""
    if generated_pnm_id and not str(msg.get("pnmId") or "").strip():
        msg["pnmId"] = generated_pnm_id
    msg = validated
    
    pnm_id = msg.get("pnmId", "") or ""
    message_uid = _generate_message_uid(msg, seller_external_uid)
    parse_status = msg.get("parseStatus", "ok") or "ok"
    direction = str(msg.get("direction") or "IN").upper()
    
    # === Step 2: 去重检查（优先用 message_uid，兼容 pnm_id）===
    # 去重命中时：如果新消息带服务端时间戳（messageTime > 0），用服务端时间戳覆盖旧 message_time。
    # 这解决了 OUT 消息先入库时（messageTime=0 兜底为本地时间）后推送回环到来时（带服务端时间戳）
    # 的更新问题。服务端时间戳是权威的，应优先使用。
    if message_uid:
        # 计算基于内容的稳定哈希（不含 pnmId 和 messageTime）
        # 用于跨不同 pnmId 的同一条消息去重（如本地持久化与 WS 回环使用不同 pnmId）
        content_hash = _generate_message_uid({**msg, "pnmId": ""}, seller_external_uid)
        # 第 4 条件匹配遗留记录（pnm_id/message_uid 均为 NULL 的旧 _insert_xianyu_chat_message 产物）。
        # 旧实现仅检查 pnm_id = ''，无法匹配 NULL；且未按内容匹配，过于宽泛。
        # 修复：同时匹配空字符串和 NULL，并增加 s_id+sender+receiver+content 精确匹配。
        existing = await db.execute(
            text("""
                SELECT id, message_time, pnm_id, message_uid FROM xianyu_chat_message
                WHERE account_id = :account_id AND (
                    message_uid = :muid
                    OR (pnm_id = :muid AND pnm_id != '' AND pnm_id IS NOT NULL)
                    OR (:content_hash != '' AND message_uid = :content_hash)
                    OR (:content_hash != '' AND (pnm_id = '' OR pnm_id IS NULL) AND message_uid IS NULL
                        AND s_id = :s_id AND sender_user_id = :sender_user_id
                        AND receiver_user_id = :receiver_user_id AND msg_content = :msg_content)
                )
                LIMIT 1
            """),
            {
                "muid": message_uid,
                "content_hash": content_hash,
                "account_id": account_id,
                "s_id": msg.get("sId", ""),
                "sender_user_id": msg.get("senderUserId", ""),
                "receiver_user_id": msg.get("receiverUserId", ""),
                "msg_content": msg.get("msgContent", ""),
            }
        )
        existing_row = existing.first()
        if existing_row:
            existing_id = existing_row[0]
            existing_msg_time = existing_row[1] or 0
            existing_pnm_id = str(existing_row[2] or "")
            existing_message_uid = str(existing_row[3] or "")
            new_msg_time = msg.get("messageTime", 0) or 0
            if isinstance(new_msg_time, (int, float)) and new_msg_time > 0 and int(new_msg_time) != int(existing_msg_time or 0):
                try:
                    await db.execute(
                        text("UPDATE xianyu_chat_message SET message_time = :mt, updated_time = NOW() WHERE id = :id"),
                        {"mt": int(new_msg_time), "id": existing_id}
                    )
                    await db.flush()
                    logger.debug("消息已存在，已更新消息时间")
                except Exception:
                    logger.debug("更新消息时间失败（可忽略）")
            # === pnmId 同步 ===
            # 场景：misc.py 先入库时 pnmId 为空，save_chat_message 生成兜底哈希作为 pnm_id；
            # 后续 WS 回环消息带有闲鱼服务端真实 pnmId，去重命中此记录。
            # 如果不同步，DB 记录的 pnm_id（兜底哈希）与 WS 回环 SSE 广播的 pnmId（真实 pnmId）
            # 不一致，loadContext 加载 DB 记录后会与 SSE 合并的消息产生不同 messageIdentity，
            # 导致前端显示重复消息。
            # 修复：用新消息的真实 pnmId 覆盖 DB 记录的兜底哈希 pnm_id 和 message_uid，
            # 并回写到 msg，确保 DB、SSE 广播、WS 回环 SSE 广播使用同一 pnmId。
            if pnm_id and pnm_id != existing_pnm_id:
                try:
                    await db.execute(
                        text("UPDATE xianyu_chat_message SET pnm_id = :pnm_id, message_uid = :message_uid, updated_time = NOW() WHERE id = :id"),
                        {"pnm_id": pnm_id, "message_uid": pnm_id, "id": existing_id}
                    )
                    await db.flush()
                    logger.info(
                        "消息已存在，已同步 pnmId 旧=%s 新=%s",
                        existing_pnm_id or "(空)", pnm_id,
                    )
                except Exception:
                    logger.debug("同步 pnmId 失败（可忽略）")
            # 回写 DB 记录的权威 pnmId 到 msg，确保 SSE 广播使用同一身份
            authoritative_pnm_id = pnm_id or existing_pnm_id or existing_message_uid
            if authoritative_pnm_id:
                msg["pnmId"] = authoritative_pnm_id
            return None
    elif pnm_id:
        existing = await db.execute(
            text("SELECT id, message_time FROM xianyu_chat_message WHERE pnm_id = :pnm_id AND account_id = :account_id LIMIT 1"),
            {"pnm_id": pnm_id, "account_id": account_id}
        )
        existing_row = existing.first()
        if existing_row:
            existing_id = existing_row[0]
            existing_msg_time = existing_row[1] or 0
            new_msg_time = msg.get("messageTime", 0) or 0
            if isinstance(new_msg_time, (int, float)) and new_msg_time > 0 and int(new_msg_time) != int(existing_msg_time or 0):
                try:
                    await db.execute(
                        text("UPDATE xianyu_chat_message SET message_time = :mt, updated_time = NOW() WHERE id = :id"),
                        {"mt": int(new_msg_time), "id": existing_id}
                    )
                    await db.flush()
                    logger.debug("消息已存在，已更新消息时间")
                except Exception:
                    logger.debug("更新消息时间失败（可忽略）")
            return None

    # 解析 peer_external_uid
    peer_external_uid = str(msg.get("receiverUserId") or "") if direction == "OUT" else str(msg.get("senderUserId") or "")

    # 插入消息
    message_time = msg.get("messageTime", 0)
    if isinstance(message_time, str):
        try:
            message_time = int(message_time)
        except (ValueError, TypeError):
            message_time = 0
    if not message_time:
        # 修复：messageTime=0 时不能盲目用当前时间，否则同一批消息时间戳几乎相同。
        # 参考目标项目（xianyu-auto-reply）：IM 消息真实时间在 createAt/time/modifyTime 字段。
        # 先尝试从 rawPayload 中提取这些字段，提取失败才用当前时间作为最后兜底。
        message_time = _extract_time_from_raw_payload(msg.get("rawPayload") or msg.get("raw_payload"))
        if message_time:
            logger.debug("save_chat_message 从 rawPayload 兜底提取时间")
        else:
            # 最后兜底：用当前时间，但记录警告便于排查
            message_time = int(time.time() * 1000)
            logger.warning(
                "save_chat_message 缺少消息时间，使用当前时间兜底 contentLen=%d",
                len(msg.get("msgContent", "") or ""),
            )

    s_id = msg.get("sId", "")
    logger.info(
        "save_chat_message accountId=%d parseStatus=%s contentLen=%d",
        account_id,
        parse_status,
        len(msg.get("msgContent", "") or ""),
    )

    # 构建 complete_msg（含完整解析上下文）
    complete_msg = json.dumps(msg, ensure_ascii=False)
    raw_payload = _serialize_json_document(msg.get("rawPayload", msg.get("raw_payload")))

    # 插入消息（捕获 IntegrityError 做幂等处理，避免并发去重窗口导致整条消息丢失）
    try:
        result = await db.execute(
            text("""
            INSERT INTO xianyu_chat_message (account_id, seller_external_uid, pnm_id, message_uid,
                s_id, content_type, msg_content,
                sender_user_id, receiver_user_id, sender_user_name,
                peer_external_uid, xy_goods_id, message_time,
                direction, parse_status, reminder_content, reminder_url,
                complete_msg, raw_payload, read_status, deleted, created_time, updated_time
            ) VALUES (
                :account_id, :seller_external_uid, :pnm_id, :message_uid,
                :s_id, :content_type, :msg_content,
                :sender_user_id, :receiver_user_id, :sender_user_name,
                :peer_external_uid, :xy_goods_id, :message_time,
                :direction, :parse_status, :reminder_content, :reminder_url,
                :complete_msg, :raw_payload, :read_status, 0, NOW(), NOW()
            )
        """),
        {
            "account_id": account_id,
            "seller_external_uid": seller_external_uid or None,
            "pnm_id": pnm_id or None,
            "message_uid": message_uid or None,
            "s_id": msg.get("sId", ""),
            "content_type": msg.get("contentType", 1),
            "msg_content": msg.get("msgContent", ""),
            "sender_user_id": msg.get("senderUserId", ""),
            "receiver_user_id": msg.get("receiverUserId", ""),
            "sender_user_name": normalize_peer_name(msg.get("senderUserName", "")),
            "peer_external_uid": peer_external_uid or None,
            "xy_goods_id": msg.get("xyGoodsId", ""),
            "message_time": message_time,
            "direction": direction,
            "parse_status": parse_status,
            "reminder_content": msg.get("reminderContent", ""),
            "reminder_url": msg.get("reminderUrl", ""),
            "complete_msg": complete_msg,
            "raw_payload": raw_payload,
            "read_status": 1 if direction == "OUT" else int(msg.get("readStatus", 0) or 0),
            }
        )
        await db.flush()
        new_id = result.lastrowid
    except IntegrityError:
        # 并发去重窗口：两个任务都通过了上面的 SELECT 去重检查，
        # 第二个 INSERT 会触发唯一键冲突。旧实现直接抛错导致整条消息丢失。
        # 此处回滚事务并返回 None，消息已由先到达的插入保存，不丢失数据。
        await db.rollback()
        logger.info("消息插入冲突（已存在），跳过 accountId=%d", account_id)
        return None

    # 更新会话（xianyu_conversation）— 只有解析成功或可降级的消息才进入会话
    s_id = msg.get("sId", "")
    if s_id and parse_status in ("ok", "partial"):
        try:
            await _upsert_conversation(db, account_id, msg, seller_external_uid)
        except Exception:
            logger.error(
                "更新会话失败（消息已保存，会话创建失败不影响消息存储）accountId=%d",
                account_id,
                exc_info=True,
            )

    return new_id


async def _upsert_conversation(
    db: AsyncSession,
    account_id: int,
    msg: dict,
    seller_external_uid: str = "",
):
    """更新或创建会话记录。"""
    s_id = msg.get("sId", "")
    direction = str(msg.get("direction") or "IN").upper()
    sender_id = msg.get("senderUserId", "")
    receiver_id = msg.get("receiverUserId", "")
    peer_id = await _resolve_peer_id(db, account_id, msg)

    # 生成 peer_key（稳定对端标识）
    peer_external_uid = peer_id if peer_id and not peer_id.startswith("sid:") else ""
    is_sid_fallback = peer_id.startswith("sid:") if peer_id else True
    peer_key = f"sid:{s_id}" if s_id else (peer_external_uid if peer_external_uid else peer_id or "")

    logger.debug(
        "upsert conversation accountId=%d hasPeer=%s sidFallback=%s",
        account_id,
        bool(peer_id),
        is_sid_fallback,
    )
    if not peer_id:
        logger.warning("跳过会话更新：对端标识为空 accountId=%d", account_id)
        return
    content = msg.get("msgContent", "")
    message_time = msg.get("messageTime", 0)
    content_type = msg.get("contentType", 1)
    xy_goods_id = msg.get("xyGoodsId", "")
    normalized_goods_id = _normalize_goods_id(xy_goods_id)
    
    # 提取买家名称（经过系统文本过滤）
    # IN消息：sender_user_name 是买家名称
    # OUT消息：无法直接获取买家名称，保留已有值
    buyer_name = ""
    if direction == "IN":
        buyer_name = normalize_peer_name(str(msg.get("senderUserName", "") or ""))
    # 如果 senderUserName 是"我"或卖家名称，则忽略（可能是系统自己发的消息被推送回来）
    if buyer_name and seller_external_uid:
        # 通过 xianyu_account 表检查是否是卖家自己的名称
        pass  # 简化处理：买家名称不应该是"我"
    if buyer_name in ("我", "", seller_external_uid):
        buyer_name = ""

    # 将 message_time 转换为 datetime
    from datetime import datetime
    if message_time and message_time > 0:
        try:
            msg_dt = datetime.fromtimestamp(message_time / 1000)
        except (ValueError, OSError):
            msg_dt = datetime.now()
    else:
        msg_dt = datetime.now()

    # 查找已有会话（优先按 peer_key，兜底按 peer_id/account_id 加 s_id）
    existing = await db.execute(
        text("""
            SELECT id FROM xianyu_conversation WHERE account_id = :account_id
              AND peer_key COLLATE utf8mb4_unicode_ci = :pkey COLLATE utf8mb4_unicode_ci
            ORDER BY id DESC LIMIT 1
        """),
        {"account_id": account_id, "pkey": peer_key}
    )
    row = existing.mappings().first()
    conv = row["id"] if row else None

    # 兜底：通过 s_id 查找（兼容迁移期间的旧数据）
    if not conv and s_id:
        fallback = await db.execute(
            text("""
                SELECT
                    c.id,
                    c.peer_key,
                    c.external_buyer_id,
                    c.peer_external_uid
                FROM xianyu_conversation c WHERE c.account_id = :account_id
                  AND (
                    c.peer_key COLLATE utf8mb4_unicode_ci = :sid_key COLLATE utf8mb4_unicode_ci
                    OR c.external_buyer_id COLLATE utf8mb4_unicode_ci = :sid_key COLLATE utf8mb4_unicode_ci
                    OR c.peer_key COLLATE utf8mb4_unicode_ci = :sid_key_goofish COLLATE utf8mb4_unicode_ci
                    OR c.external_buyer_id COLLATE utf8mb4_unicode_ci = :sid_key_goofish COLLATE utf8mb4_unicode_ci
                  )
                ORDER BY c.id DESC LIMIT 1
            """),
            {
                "account_id": account_id,
                "sid_key": f"sid:{s_id}",
                "sid_key_goofish": f"sid:{s_id}@goofish",
            }
        )
        fallback_row = fallback.mappings().first()
        if fallback_row:
            conv = fallback_row["id"]
            old_peer_key = fallback_row.get("peer_key", "")
            # 关键修复：如果已有会话的 peer_key 是 sid:xxx 但与当前 s_id 不同，
            # 说明这是另一个不同会话的消息被误匹配，不应该合并。
            # 应创建新的独立会话。
            if old_peer_key and old_peer_key.startswith("sid:") and s_id:
                old_sid = old_peer_key[4:]  # 去掉 "sid:" 前缀
                if old_sid != s_id:
                    logger.warning("会话兜底匹配到不同会话，已跳过合并 accountId=%d", account_id)
                    conv = None
                    old_peer_key = None
            # 如果 peer_key 已变更（如从 sid:xxx 变为真实 uid），更新
            if old_peer_key and old_peer_key.startswith("sid:") and s_id and old_peer_key[4:] == s_id:
                if peer_external_uid:
                    await db.execute(
                        text("""
                            UPDATE xianyu_conversation
                            SET external_buyer_id = CASE
                                    WHEN external_buyer_id IS NULL
                                      OR external_buyer_id = ''
                                      OR external_buyer_id LIKE 'sid:%'
                                    THEN :new_external_buyer_id
                                    ELSE external_buyer_id
                                END,
                                peer_external_uid = COALESCE(NULLIF(peer_external_uid, ''), :new_peuid),
                                updated_time = NOW()
                            WHERE id = :id
                        """),
                        {
                            "id": conv,
                            "new_external_buyer_id": peer_id,
                            "new_peuid": peer_external_uid,
                        }
                    )
            elif old_peer_key and old_peer_key != peer_key:
                await db.execute(
                    text("""
                        UPDATE xianyu_conversation
                        SET peer_key = :new_pkey,
                            peer_external_uid = :new_peuid,
                            updated_time = NOW()
                        WHERE id = :id
                    """),
                    {"new_pkey": peer_key, "new_peuid": peer_external_uid or None, "id": conv}
                )
                logger.info(
                    "_upsert_conversation 更新 peer_key: %s -> %s, convId=%s",
                    old_peer_key, peer_key, conv
                )

    content_preview = content[:200] if content else f"[消息类型: {content_type}]"
    unread_increment = 0 if direction == "OUT" else 1

    if conv:
        update_fields = """
                last_message_time = :msg_time,
                    last_message_content = :content,
                    unread_count = COALESCE(unread_count, 0) + :unread_increment,
                    updated_time = NOW()
            """
        update_params = {
            "id": conv,
            "msg_time": msg_dt,
            "content": content_preview,
            "unread_increment": unread_increment,
        }
        # 持久化 s_id（头像查询的稳定匹配键）
        if s_id:
            update_fields = f"""
                    s_id = COALESCE(NULLIF(s_id, ''), :s_id),
                    {update_fields.strip()}
            """
            update_params["s_id"] = s_id
        # 如果有买家名称且会话尚未保存买家名称，更新
        if buyer_name:
            update_fields = f"""
                    buyer_name = COALESCE(NULLIF(buyer_name, ''), :buyer_name),
                    {update_fields.strip()}
            """
            update_params["buyer_name"] = buyer_name
        # 如果有商品ID且会话尚未保存商品ID，更新（关键修复：之前只在新创建会话时才存 goods_id）
        if normalized_goods_id:
            update_fields = f"""
                    goods_id = COALESCE(NULLIF(goods_id, ''), :goods_id),
                    {update_fields.strip()}
            """
            update_params["goods_id"] = str(normalized_goods_id)
        update_fields = "SET " + update_fields.strip()
        await db.execute(
            text(f"""
                UPDATE xianyu_conversation
                {update_fields}
                WHERE id = :id
            """),
            update_params
        )
    else:
        # 创建新会话
        try:
            logger.info(
                "准备插入新会话 accountId=%d isSidFallback=%s",
                account_id,
                is_sid_fallback,
            )
            await db.execute(
                text("""
                    INSERT INTO xianyu_conversation (account_id, peer_key, external_buyer_id, peer_external_uid,
                        buyer_name, goods_id, s_id, last_message_content, last_message_time, unread_count,
                        status, created_time, updated_time
                    ) VALUES (
                        :account_id, :peer_key, :external_buyer_id, :peer_external_uid,
                        :buyer_name, :goods_id, :s_id, :last_message_content, :last_message_time, :unread_count,
                        0, NOW(), NOW()
                    )
                """),
                {
                    "account_id": account_id,
                    "peer_key": peer_key,
                    "external_buyer_id": peer_id,
                    "peer_external_uid": peer_external_uid or None,
                    "buyer_name": buyer_name or None,
                    "goods_id": str(normalized_goods_id or xy_goods_id or "") or None,
                    "s_id": s_id or None,
                    "last_message_content": content_preview,
                    "last_message_time": msg_dt,
                    "unread_count": unread_increment,
                }
            )
        except Exception as insert_err:
            logger.error(
                "插入会话失败: accountId=%d tenantId=%d peerKey=%s externalBuyerId=%s error=%s",
                account_id, peer_key, peer_id, insert_err, exc_info=True
            )
            raise


async def _insert_xianyu_chat_message(
    db: AsyncSession,
    account_id: int,
    msg: dict,
):
    """兼容旧数据流：同步写入 xianyu_chat_message 表。"""
    direction = str(msg.get("direction") or "IN").upper()
    # The legacy compatibility table follows the ORM contract: varchar
    # sent/received and created_time only. Do not borrow columns or numeric
    # direction values from xianyu_chat_message/older downstream schemas.
    direction_value = "sent" if direction == "OUT" else "received"
    session_id = str(msg.get("sId") or msg.get("sessionId") or "").strip()
    sender_user_id = str(msg.get("senderUserId") or "")
    receiver_user_id = str(msg.get("receiverUserId") or "")
    message_time = msg.get("messageTime") or int(time.time() * 1000)

    if isinstance(message_time, str):
        try:
            message_time = int(message_time)
        except (TypeError, ValueError):
            message_time = int(time.time() * 1000)

    await db.execute(
        text("""
            INSERT INTO xianyu_chat_message (account_id, s_id,
                sender_user_id, receiver_user_id, msg_content, content_type, direction,
                message_time, deleted
            ) VALUES (
                :account_id, :session_id, :from_user_id, :to_user_id,
                :content, :content_type_int, :direction_code,
                :created_time, 0
            )
        """),
        {
            "account_id": account_id,
            "session_id": session_id or None,
            "from_user_id": sender_user_id or None,
            "to_user_id": receiver_user_id or None,
            "content": msg.get("msgContent") or "",
            "content_type_int": int(msg.get("contentType") or 1),
            "direction_code": "OUT" if direction_value == "sent" else "IN",
            "created_time": message_time,
        }
    )


async def update_message_read_status(
    db: AsyncSession,
    account_id: int,
    pnm_id: str,
    read_status: int,
):
    """更新消息已读状态。"""
    await db.execute(
        text("""
            UPDATE xianyu_chat_message
            SET read_status = :read_status,
                updated_time = NOW()
            WHERE account_id = :account_id
              AND pnm_id = :pnm_id
              AND deleted = 0
        """),
        {
            "account_id": account_id,
            "pnm_id": pnm_id,
            "read_status": read_status,
        }
    )


async def save_raw_websocket_message(
    db: AsyncSession,
    account_id: int,
    message_type: str,
    raw_data: str,
):
    """保存原始 WebSocket 消息（调试用）。"""
    await db.execute(
        text("""
            INSERT INTO xianyu_operation_log (account_id, operation_type, operation_desc,
                request_data, response_data, status, created_time, updated_time,
                deleted
            ) VALUES (
                :account_id, 'WS_RAW_MESSAGE', :operation_desc,
                :request_data, :response_data, 1, NOW(), NOW(),
                0
            )
        """),
        {
            "account_id": account_id,
            "operation_desc": f"原始 WS 消息: {message_type}",
            "request_data": raw_data[:65535],
            "response_data": None,
        }
    )


async def get_recent_messages(
    db: AsyncSession,
    account_id: int,
    limit: int = 50,
) -> list[XianyuChatMessage]:
    """获取最近消息。"""
    result = await db.execute(
        text("""
            SELECT * FROM xianyu_chat_message WHERE account_id = :account_id
              AND deleted = 0
            ORDER BY message_time DESC
            LIMIT :limit
        """),
        {
            "account_id": account_id,
            "limit": limit,
        }
    )
    return result.scalars().all()


async def get_message_count(
    db: AsyncSession,
    account_id: int,
) -> int:
    """获取消息总数。"""
    result = await db.execute(
        text("""
            SELECT COUNT(*) FROM xianyu_chat_message WHERE account_id = :account_id
              AND deleted = 0
        """),
        {
            "account_id": account_id,
        }
    )
    return result.scalar() or 0


async def get_unread_count(
    db: AsyncSession,
    account_id: int,
) -> int:
    """获取未读消息数。"""
    result = await db.execute(
        text("""
            SELECT COUNT(*) FROM xianyu_chat_message WHERE account_id = :account_id
              AND direction = 'IN'
              AND read_status = 0
              AND deleted = 0
        """),
        {
            "account_id": account_id,
        }
    )
    return result.scalar() or 0


async def get_online_conversations(
    db: AsyncSession,
    account_id: int,
    limit: int = 50,
    user_id: int | None = None,
    schedule_live_refresh: bool = True,
    before_time: int | None = None,
) -> list[dict]:
    """获取在线会话列表。

    按 (peer_user_id, goods_id) 聚合会话。同一买家+同一商品的多个 sId 合并为 1 个会话，
    同一买家不同商品仍为不同会话。当 peer_user_id 为 sid:xxx 兜底时（无法解析真实买家UID），
    则按 sId 聚合避免误合并。

    before_time: 可选的游标分页时间戳（毫秒），仅返回 last_message_time < before_time 的会话。
    """
    having_clause = ""
    params: dict[str, Any] = {"account_id": account_id, "limit": limit, "user_id": user_id}
    if before_time is not None:
        having_clause = "HAVING MAX(base.message_time) < :before_time"
        params["before_time"] = before_time

    rows = await db.execute(
        text(f"""
            SELECT
                MIN(conv.id) AS conversationId,
                SUBSTRING_INDEX(GROUP_CONCAT(base.s_id ORDER BY base.message_time DESC SEPARATOR ','), ',', 1) AS sid,
                MAX(base.peer_user_id) AS peerUserId,
                COALESCE(
                    MAX(conv.peer_key),
                    MAX(base.conv_peer_key),
                    CONCAT('sid:', SUBSTRING_INDEX(GROUP_CONCAT(base.s_id ORDER BY base.message_time DESC SEPARATOR ','), ',', 1))
                ) AS peerKey,
                COALESCE(
                    SUBSTRING_INDEX(GROUP_CONCAT(NULLIF(base.inbound_sender_name, '') ORDER BY base.message_time DESC SEPARATOR ','), ',', 1),
                    SUBSTRING_INDEX(GROUP_CONCAT(NULLIF(base.history_sender_name, '') ORDER BY base.message_time DESC SEPARATOR ','), ',', 1),
                    ''
                ) AS peerUserName,
                SUBSTRING_INDEX(GROUP_CONCAT(base.msg_content ORDER BY base.message_time DESC SEPARATOR ','), ',', 1) AS lastMessage,
                CAST(SUBSTRING_INDEX(GROUP_CONCAT(base.content_type ORDER BY base.message_time DESC SEPARATOR ','), ',', 1) AS UNSIGNED) AS lastContentType,
                MAX(base.message_time) AS lastMessageTime,
                MIN(base.message_time) AS firstMessageTime,
                COALESCE(
                    SUBSTRING_INDEX(GROUP_CONCAT(NULLIF(base.xy_goods_id, '') ORDER BY base.message_time DESC SEPARATOR ','), ',', 1),
                    ''
                ) AS goodsId,
                COALESCE(MAX(NULLIF(conv.goods_title, '')), '') AS goodsTitle,
                COALESCE(MAX(NULLIF(conv.goods_cover_pic, '')), '') AS goodsCoverPic,
                SUBSTRING_INDEX(GROUP_CONCAT(NULLIF(base.reminder_content, '') ORDER BY base.message_time DESC SEPARATOR '\x01'), '\x01', 1) AS reminderContent,
                COALESCE(MAX(conv.unread_count), SUM(CASE WHEN base.direction = 'IN' AND COALESCE(base.read_status, 0) = 0 THEN 1 ELSE 0 END)) AS unreadCount,
                COUNT(*) AS messageCount,
                COALESCE(MAX(conv.status), 0) AS conversationStatus,
                COALESCE(MAX(NULLIF(conv.buyer_avatar, '')), '') AS buyerAvatar,
                '' AS goodsPrice,
                NULL AS goodsStatus
            FROM (
                SELECT base.account_id,
                    CASE
                        WHEN base.s_id LIKE '%@goofish' THEN SUBSTRING_INDEX(base.s_id, '@', 1)
                        ELSE base.s_id
                    END AS s_id,
                    base.msg_content,
                    base.content_type,
                    base.message_time,
                    base.xy_goods_id,
                    base.reminder_content,
                    base.direction,
                    base.read_status,
                    base.sender_user_id,
                    base.receiver_user_id,
                    COALESCE(
                        NULLIF(NULLIF(base.peer_external_uid, ''), a.external_uid),
                        NULLIF(
                            CASE
                                WHEN base.direction = 'OUT' THEN
                                    CASE
                                        WHEN base.receiver_user_id IS NOT NULL
                                          AND base.receiver_user_id != ''
                                          AND (a.external_uid IS NULL OR base.receiver_user_id != a.external_uid)
                                        THEN base.receiver_user_id
                                        ELSE NULL
                                    END
                                ELSE
                                    CASE
                                        WHEN base.sender_user_id IS NOT NULL
                                          AND base.sender_user_id != ''
                                          AND (a.external_uid IS NULL OR base.sender_user_id != a.external_uid)
                                        THEN base.sender_user_id
                                        ELSE NULL
                                    END
                            END,
                            ''
                        ),
                        NULLIF(conv_by_sid.external_buyer_id, ''),
                        CONCAT('sid:', base.s_id)
                    ) AS peer_user_id,
                    COALESCE(
                        NULLIF(conv_by_sid.peer_key, ''),
                        NULLIF(conv_by_sid.external_buyer_id, ''),
                        CONCAT('sid:', base.s_id)
                    ) AS conv_peer_key,
                    NULLIF(CASE WHEN base.direction = 'IN' THEN base.sender_user_name ELSE NULL END, '') AS inbound_sender_name,
                    -- 性能优化：移除相关子查询 history_sender_name（对每行都执行一次全表扫描）。
                    -- 由 _enrich_online_conversations_batch 的 fallback_map 兜底处理（等价实现）。
                    CAST(NULL AS CHAR) AS history_sender_name
                FROM xianyu_chat_message base
                JOIN xianyu_account a
                    ON a.id = base.account_id
                LEFT JOIN xianyu_conversation conv_by_sid
                    ON conv_by_sid.account_id = base.account_id
                    AND (
                        conv_by_sid.peer_key COLLATE utf8mb4_unicode_ci = CONCAT('sid:', base.s_id) COLLATE utf8mb4_unicode_ci
                        OR conv_by_sid.external_buyer_id COLLATE utf8mb4_unicode_ci = CONCAT('sid:', base.s_id) COLLATE utf8mb4_unicode_ci
                    )
                WHERE base.account_id = :account_id
                  AND base.deleted = 0
                  AND base.content_type NOT IN (32)
                  AND base.s_id IS NOT NULL
                  AND base.s_id != ''
            ) base
            LEFT JOIN xianyu_conversation conv
                ON conv.account_id = base.account_id
                AND (
                    conv.peer_key COLLATE utf8mb4_unicode_ci = CONCAT('sid:', base.s_id) COLLATE utf8mb4_unicode_ci
                    OR conv.external_buyer_id COLLATE utf8mb4_unicode_ci = CONCAT('sid:', base.s_id) COLLATE utf8mb4_unicode_ci
                )
            GROUP BY
                CASE
                    WHEN base.peer_user_id LIKE 'sid:%' OR base.peer_user_id = '' THEN CONCAT('sid:', base.s_id)
                    ELSE base.peer_user_id
                END,
                COALESCE(NULLIF(base.xy_goods_id, ''), '')
            {having_clause}
            ORDER BY MAX(base.message_time) DESC
            LIMIT :limit
        """),
        params,
    )
    seller_external_uid = await _load_seller_external_uid(db, account_id)
    result = [dict(row) for row in rows.mappings().all()]
    if seller_external_uid:
        result = [
            row for row in result
            if _normalize_party_id(row.get("peerUserId")) != seller_external_uid
        ]
    result = [row for row in result if _is_displayable_conversation(row)]

    # === 性能优化：live conversations 通过外部 HTTP 调用，不应阻塞首次响应 ===
    # 后台异步触发拉取，下次刷新时即可拿到最新数据；本次响应先返回 DB 数据
    if schedule_live_refresh:
        spawn_background_task(
            _fetch_live_online_conversations_safe(
                account_id,
                limit=limit,
                user_id=user_id,
            ),
            name="ws-storage.live-conversations",
        )

    result = _merge_online_conversation_rows(result)
    result = await _apply_ai_reply_preview(db, account_id, result)
    logger.info(
        "get_online_conversations: tenantId=%d accountId=%d 返回 %d 条会话（按 peer_user_id+goods_id 聚合）", account_id, len(result)
    )

    # === 性能优化：将 N+1 后处理改为批量查询 ===
    # 之前对每个会话（最多200条）依次执行 5 次独立 SQL，最坏 1000 次串行查询。
    # 现在改为 5 次批量 IN 查询，再用内存匹配回填到对应会话。
    result = await _enrich_online_conversations_batch(
        db, account_id, result
    )

    # === 性能优化：远程头像拉取改为后台异步，不阻塞响应 ===
    # 之前会等待最多 12 个外部 HTTP 调用，现在只读 DB 缓存，远程拉取后台异步进行
    result = _hydrate_online_conversation_avatars_from_cache(result)
    spawn_background_task(
        _hydrate_online_conversation_avatars_async(account_id, result),
        name="ws-storage.avatar-hydration",
    )

    logger.debug("在线会话查询完成 accountId=%d resultCount=%d", account_id, len(result))
    return result


# 简单的内存缓存：避免短时间重复调用 IM 导致限流（flow control）
# key: (account_id, cursor, page_size), value: (timestamp, result)
_online_conversations_cache: dict[tuple, tuple[float, dict[str, Any]]] = {}
# IM 数据缓存 TTL：10 秒内复用 IM 拉取结果，避免轮询触发限流
_ONLINE_CONVERSATIONS_CACHE_TTL = 10.0  # 秒
_ONLINE_CONVERSATIONS_CACHE_MAX_ENTRIES = 512
# 后台 IM 刷新所有权：None 表示任务仍在运行，float 表示完成时间并用于去抖。
_im_refresh_inflight: dict[tuple, float | None] = {}
_IM_REFRESH_DEBOUNCE = 5.0  # 秒
_IM_REFRESH_INFLIGHT_MAX_ENTRIES = 512
_avatar_hydration_active: set[int] = set()
_AVATAR_HYDRATION_MAX_ACTIVE_ACCOUNTS = 512


def _prune_online_conversations_cache(now: float) -> None:
    expired = [
        key
        for key, (cached_at, _result) in _online_conversations_cache.items()
        if now - cached_at >= _ONLINE_CONVERSATIONS_CACHE_TTL
    ]
    for key in expired:
        _online_conversations_cache.pop(key, None)

    overflow = (
        len(_online_conversations_cache)
        - _ONLINE_CONVERSATIONS_CACHE_MAX_ENTRIES
    )
    if overflow > 0:
        oldest = sorted(
            _online_conversations_cache.items(),
            key=lambda item: (item[1][0], repr(item[0])),
        )[:overflow]
        for key, _value in oldest:
            _online_conversations_cache.pop(key, None)


def _store_online_conversations_cache(
    cache_key: tuple,
    result: dict[str, Any],
    *,
    now: float | None = None,
) -> None:
    now = time.time() if now is None else now
    _prune_online_conversations_cache(now)
    _online_conversations_cache[cache_key] = (now, result)
    _prune_online_conversations_cache(now)


def _get_online_conversations_cache(
    cache_key: tuple,
    *,
    now: float | None = None,
) -> dict[str, Any] | None:
    now = time.time() if now is None else now
    _prune_online_conversations_cache(now)
    cached = _online_conversations_cache.get(cache_key)
    return cached[1] if cached else None


def _prune_im_refresh_inflight(now: float) -> None:
    expired = [
        key
        for key, completed_at in _im_refresh_inflight.items()
        if completed_at is not None
        and now - completed_at >= _IM_REFRESH_DEBOUNCE
    ]
    for key in expired:
        _im_refresh_inflight.pop(key, None)

    overflow = len(_im_refresh_inflight) - _IM_REFRESH_INFLIGHT_MAX_ENTRIES
    if overflow > 0:
        oldest = sorted(
            (
                item
                for item in _im_refresh_inflight.items()
                if item[1] is not None
            ),
            key=lambda item: (item[1], repr(item[0])),
        )[:overflow]
        for key, _completed_at in oldest:
            _im_refresh_inflight.pop(key, None)


def _claim_im_refresh(
    cache_key: tuple,
    *,
    now: float | None = None,
) -> bool:
    now = time.time() if now is None else now
    _prune_im_refresh_inflight(now)
    if cache_key in _im_refresh_inflight:
        return False
    active_count = sum(
        completed_at is None
        for completed_at in _im_refresh_inflight.values()
    )
    if active_count >= _IM_REFRESH_INFLIGHT_MAX_ENTRIES:
        return False
    _im_refresh_inflight[cache_key] = None
    _prune_im_refresh_inflight(now)
    return cache_key in _im_refresh_inflight


def _release_im_refresh(
    cache_key: tuple,
    *,
    now: float | None = None,
) -> None:
    if (
        cache_key not in _im_refresh_inflight
        or _im_refresh_inflight[cache_key] is not None
    ):
        return
    completed_at = time.time() if now is None else now
    _im_refresh_inflight[cache_key] = completed_at
    _prune_im_refresh_inflight(completed_at)


def _claim_avatar_hydration(account_id: int) -> bool:
    normalized = int(account_id)
    if normalized in _avatar_hydration_active:
        return False
    if len(_avatar_hydration_active) >= _AVATAR_HYDRATION_MAX_ACTIVE_ACCOUNTS:
        return False
    _avatar_hydration_active.add(normalized)
    return True


def _release_avatar_hydration(account_id: int) -> None:
    _avatar_hydration_active.discard(int(account_id))


async def _refresh_im_conversations_background(
    account_id: int,
    cursor: int | None,
    page_size: int,
    user_id: int | None,
) -> None:
    """后台异步调用 IM WebSocket 拉取会话并更新缓存。

    非阻塞：调用方立即返回 DB 数据，IM 数据到达后更新缓存供下次请求使用。
    带去抖动：同一 key 在 _IM_REFRESH_DEBOUNCE 秒内不重复触发。
    """
    import time as _time
    from .ws_client import ws_manager

    cache_key = (account_id, cursor, page_size)
    if not _claim_im_refresh(cache_key, now=_time.time()):
        return  # 已有刷新在进行或刚完成，跳过

    try:
        client = ws_manager.get_client(account_id)
        if not client or not getattr(client, "is_connected", False):
            return

        # Keep database ownership in short phases. The IM provider may hang or
        # take seconds; no checked-out SQL connection may cross that boundary.
        from ..core.database import async_session
        async with async_session() as lookup_db:
            seller_external_uid = await _load_seller_external_uid(
                lookup_db,
                account_id,
            )
        seller_external_uid = seller_external_uid or str(
            getattr(client, "unb", "") or ""
        )

        try:
            body = await client.list_conversations(
                start_timestamp=cursor,
                limit=page_size,
            )
        except Exception as exc:
            logger.debug(
                "后台 IM 刷新失败 accountId=%d errorType=%s",
                account_id,
                type(exc).__name__,
            )
            return

        # 检测 IM 限流
        body_str = str(body) if isinstance(body, dict) else str(body or "")
        body_code = body.get("code") if isinstance(body, dict) else None
        is_flow_controled = (
            str(body_code) == "400600001"
            or "flow control" in body_str.lower()
            or (
                isinstance(body, dict)
                and "userConvs" not in body
                and "reason" in body
            )
        )
        if is_flow_controled:
            logger.debug(
                "后台 IM 刷新被限流 tenantId=%d accountId=%d",
                account_id,
            )
            return

        items = body.get("userConvs", []) if isinstance(body, dict) else []
        conversations: list[dict[str, Any]] = []
        for item in items:
            parsed = _parse_live_conversation(item, seller_external_uid)
            if parsed:
                conversations.append(parsed)

        has_more = body.get("hasMore", False) if isinstance(body, dict) else False
        has_more = has_more if isinstance(has_more, bool) else str(has_more) == "1"
        next_cursor = body.get("nextCursor") if isinstance(body, dict) else None

        if seller_external_uid:
            conversations = [
                row
                for row in conversations
                if _normalize_party_id(row.get("peerUserId"))
                != seller_external_uid
            ]
        conversations = [
            row for row in conversations if _is_displayable_conversation(row)
        ]
        conversations = _merge_online_conversation_rows(conversations)

        # Re-open a session only after provider I/O is complete. All helpers in
        # this phase are database/local transformations and may commit updates.
        async with async_session() as persist_db:
            conversations = await _enrich_online_conversations_batch(
                persist_db,
                account_id,
                conversations,
            )
            conversations = await _ensure_online_conversation_records(
                persist_db,
                account_id,
                conversations,
                seller_external_uid,
            )
            conversations = await _apply_ai_reply_preview(
                persist_db,
                account_id,
                conversations,
            )
        conversations = _hydrate_online_conversation_avatars_from_cache(
            conversations
        )

        # 仅当 IM 返回非空会话时才更新缓存，避免空结果覆盖 DB 聚合数据
        # （对齐商业版：IM 空结果不缓存，下次请求仍走 DB 聚合）
        if conversations:
            result = {
                "conversations": conversations,
                "hasMore": has_more,
                "nextCursor": next_cursor,
            }
            _store_online_conversations_cache(
                cache_key,
                result,
                now=_time.time(),
            )
        logger.info(
            "后台 IM 刷新完成 tenantId=%d accountId=%d cursor=%s 返回 %d 条, hasMore=%s (cached=%s)",
            account_id,
            cursor,
            len(conversations),
            has_more,
            bool(conversations),
        )
    except Exception as exc:
        logger.debug(
            "后台 IM 刷新异常 accountId=%d errorType=%s",
            account_id,
            type(exc).__name__,
        )
    finally:
        _release_im_refresh(cache_key, now=_time.time())


async def _db_fallback_conversations_paged(
    db: AsyncSession,
    account_id: int,
    cursor: int | None,
    page_size: int,
    user_id: int | None = None,
) -> dict[str, Any]:
    """WebSocket 不可用时，回退到 DB 基于 cursor 的游标分页。

    cursor 为最后一条会话的 last_message_time（毫秒时间戳）。
    查询 message_time < cursor 的会话，按时间倒序返回。
    """
    try:
        before_time = int(cursor) if cursor is not None else None
    except (TypeError, ValueError):
        before_time = None

    # 多查 1 条用于判断 hasMore
    fetch_limit = page_size + 1
    try:
        conversations = await get_online_conversations(
            db=db,
            account_id=account_id,
            limit=fetch_limit,
            user_id=user_id,
            schedule_live_refresh=False,
            before_time=before_time,
        )
    except Exception:
        logger.error(
            "DB 回退分页查询失败 accountId=%d cursor=%s",
            account_id, cursor,
            exc_info=True,
        )
        return {"conversations": [], "hasMore": False, "nextCursor": None}

    has_more = len(conversations) > page_size
    if has_more:
        conversations = conversations[:page_size]

    next_cursor = None
    if has_more and conversations:
        next_cursor = _conversation_sort_time(conversations[-1])

    logger.info(
        "get_online_conversations_paged: DB 回退分页 accountId=%d cursor=%s 返回 %d 条, hasMore=%s, nextCursor=%s",
        account_id, cursor, len(conversations), has_more, next_cursor,
    )

    return {
        "conversations": conversations,
        "hasMore": has_more,
        "nextCursor": next_cursor,
    }


async def get_online_conversations_paged(
    db: AsyncSession,
    account_id: int,
    cursor: int | None = None,
    page_size: int = 20,
    user_id: int | None = None,
) -> dict[str, Any]:
    """获取在线会话列表（1 秒内响应，支持 cursor 分页）。

    性能优化策略（解决 20 秒超时问题）：
    - 立即返回 DB 聚合数据（< 100ms）或缓存数据
    - 后台异步触发 IM WebSocket 刷新（不阻塞响应）
    - IM 数据到达后写入缓存，下次请求即可看到最新数据
    - 带 10 秒缓存 + 5 秒去抖动，避免轮询触发 IM 限流

    cursor 分页：
    - cursor=None：第一页，返回 DB 最新会话 + 后台 IM 刷新
    - cursor=<timestamp>：后续页，使用缓存或 IM 拉取（带 2 秒超时）
    """
    import time as _time
    import asyncio as _asyncio
    from .ws_client import ws_manager

    cache_key = (account_id, cursor, page_size)
    now = _time.time()
    cached_result = _get_online_conversations_cache(cache_key, now=now)
    if cached_result is not None:
        logger.info(
            "get_online_conversations_paged: 命中缓存 tenantId=%d accountId=%d cursor=%s pageSize=%d", account_id, cursor, page_size,
        )
        return cached_result

    # 第一页（cursor=None）：优先尝试 IM WebSocket（5 秒超时），失败则回退 DB
    if cursor is None:
        client = ws_manager.get_client(account_id)
        im_attempted = False

        if client and getattr(client, "is_connected", False):
            im_attempted = True
            seller_external_uid = await _load_seller_external_uid(db, account_id)
            seller_external_uid = seller_external_uid or str(getattr(client, "unb", "") or "")
            # 释放读事务，避免 IM 等待期间占用连接
            await db.rollback()

            try:
                body = await _asyncio.wait_for(
                    client.list_conversations(start_timestamp=None, limit=page_size),
                    timeout=5.0,
                )
            except _asyncio.TimeoutError:
                logger.warning("首页 IM 超时(5s) accountId=%d，回退 DB", account_id)
                body = None
            except Exception as exc:
                logger.warning("首页 IM 调用失败 errorType=%s，回退 DB", type(exc).__name__)
                body = None

            if body is not None:
                # 检测限流
                body_code = body.get("code") if isinstance(body, dict) else None
                body_str = str(body) if isinstance(body, dict) else str(body or "")
                is_flow = (
                    str(body_code) == "400600001"
                    or "flow control" in body_str.lower()
                    or (isinstance(body, dict) and "userConvs" not in body and "reason" in body)
                )
                if is_flow:
                    logger.warning("首页 IM 限流 code=%s，回退 DB", body_code)
                else:
                    items = body.get("userConvs", []) if isinstance(body, dict) else []
                    im_conversations: list[dict[str, Any]] = []
                    for item in items:
                        parsed = _parse_live_conversation(item, seller_external_uid)
                        if parsed:
                            im_conversations.append(parsed)

                    has_more = body.get("hasMore", False) if isinstance(body, dict) else False
                    has_more = has_more if isinstance(has_more, bool) else str(has_more) == "1"
                    next_cursor = body.get("nextCursor") if isinstance(body, dict) else None

                    if seller_external_uid:
                        im_conversations = [
                            row for row in im_conversations
                            if _normalize_party_id(row.get("peerUserId")) != seller_external_uid
                        ]
                    im_conversations = [row for row in im_conversations if _is_displayable_conversation(row)]
                    im_conversations = _merge_online_conversation_rows(im_conversations)

                    if im_conversations:
                        im_conversations = await _enrich_online_conversations_batch(
                            db, account_id, im_conversations,
                        )
                        im_conversations = await _ensure_online_conversation_records(
                            db, account_id, im_conversations, seller_external_uid,
                        )
                        im_conversations = await _apply_ai_reply_preview(
                            db, account_id, im_conversations,
                        )
                        im_conversations = _hydrate_online_conversation_avatars_from_cache(
                            im_conversations,
                        )
                        # 异步补全头像（不阻塞响应）
                        spawn_background_task(
                            _hydrate_online_conversation_avatars(
                                db, account_id, im_conversations,
                            ),
                            name="ws-storage.avatar-hydrate",
                        )

                        result = {
                            "conversations": im_conversations,
                            "hasMore": has_more,
                            "nextCursor": next_cursor,
                        }
                        _store_online_conversations_cache(cache_key, result, now=_time.time())
                        logger.info(
                            "get_online_conversations_paged: IM 首页返回 accountId=%d %d 条, hasMore=%s",
                            account_id, len(im_conversations), has_more,
                        )
                        return result
                    else:
                        logger.info("首页 IM 返回空，回退 DB accountId=%d", account_id)

        # IM 不可用或返回空：回退 DB 聚合数据
        fetch_limit = page_size + 1
        conversations = await get_online_conversations(
            db=db, account_id=account_id,
            limit=fetch_limit, user_id=user_id,
            schedule_live_refresh=False,
        )
        has_more = len(conversations) > page_size
        if has_more:
            conversations = conversations[:page_size]
        next_cursor = None
        if has_more and conversations:
            next_cursor = _conversation_sort_time(conversations[-1])
        result = {
            "conversations": conversations,
            "hasMore": has_more,
            "nextCursor": next_cursor,
        }

        if conversations:
            _store_online_conversations_cache(cache_key, result, now=now)

        # 后台异步刷新 IM（仅当 IM 未刚尝试过时才触发）
        if not im_attempted and client and getattr(client, "is_connected", False):
            spawn_background_task(
                _refresh_im_conversations_background(
                    account_id,
                    cursor,
                    page_size,
                    user_id,
                ),
                name="ws-storage.im-refresh",
            )

        logger.info(
            "get_online_conversations_paged: DB 即时返回 tenantId=%d accountId=%d cursor=%s 返回 %d 条", account_id, cursor, len(conversations),
        )
        return result

    # 后续页（cursor != None）：优先 DB 本地分页，DB 返回空时再尝试 IM
    # 对齐商业版逻辑：先查 DB（before_message_time=cursor），DB 有结果就直接返回
    try:
        before_time = int(cursor) if cursor is not None else None
    except (TypeError, ValueError):
        before_time = None

    fetch_limit = page_size + 1
    db_conversations = await get_online_conversations(
        db=db,
        account_id=account_id,
        limit=fetch_limit,
        user_id=user_id,
        schedule_live_refresh=False,
        before_time=before_time,
    )
    if db_conversations:
        has_more = len(db_conversations) > page_size
        if has_more:
            db_conversations = db_conversations[:page_size]
        next_cursor = None
        if has_more and db_conversations:
            next_cursor = _conversation_sort_time(db_conversations[-1])
        result = {
            "conversations": db_conversations,
            "hasMore": has_more,
            "nextCursor": next_cursor,
        }
        _store_online_conversations_cache(cache_key, result, now=_time.time())
        logger.info(
            "get_online_conversations_paged: DB cursor 分页 accountId=%d cursor=%s 返回 %d 条, hasMore=%s",
            account_id, cursor, len(db_conversations), has_more,
        )
        return result

    # DB 没有更多本地页时，再尝试实时 IM 分页
    client = ws_manager.get_client(account_id)
    if not client or not getattr(client, "is_connected", False):
        # WebSocket 未连接，返回空页
        logger.info("DB 无更多数据且 IM 未连接 accountId=%d cursor=%s", account_id, cursor)
        return {"conversations": [], "hasMore": False, "nextCursor": None}

    seller_external_uid = await _load_seller_external_uid(db, account_id)
    seller_external_uid = seller_external_uid or str(getattr(client, "unb", "") or "")
    # Cursor pages are read-only; release the seller lookup transaction before
    # the bounded but still remote IM wait.
    await db.rollback()

    try:
        # 2 秒超时，避免 20 秒阻塞
        body = await _asyncio.wait_for(
            client.list_conversations(start_timestamp=cursor, limit=page_size),
            timeout=5.0,
        )
    except _asyncio.TimeoutError:
        logger.warning("IM 分页超时(5s) tenantId=%d accountId=%d cursor=%s", account_id, cursor)
        return {"conversations": [], "hasMore": False, "nextCursor": None}
    except Exception as exc:
        logger.warning("IM WebSocket 调用失败 errorType=%s", type(exc).__name__)
        return {"conversations": [], "hasMore": False, "nextCursor": None}

    # 检测 IM 限流
    body_str = str(body) if isinstance(body, dict) else str(body or "")
    body_code = body.get("code") if isinstance(body, dict) else None
    is_flow_controled = (
        str(body_code) == "400600001"
        or "flow control" in body_str.lower()
        or (isinstance(body, dict) and "userConvs" not in body and "reason" in body)
    )
    if is_flow_controled:
        logger.warning("IM 返回限流 code=%s", body_code)
        return {"conversations": [], "hasMore": False, "nextCursor": None}

    items = body.get("userConvs", []) if isinstance(body, dict) else []
    conversations: list[dict[str, Any]] = []
    for item in items:
        parsed = _parse_live_conversation(item, seller_external_uid)
        if parsed:
            conversations.append(parsed)

    # IM 返回空会话时，返回空页
    if not conversations:
        logger.info("IM 分页返回空 accountId=%d cursor=%s", account_id, cursor)
        return {"conversations": [], "hasMore": False, "nextCursor": None}

    has_more = body.get("hasMore", False) if isinstance(body, dict) else False
    has_more = has_more if isinstance(has_more, bool) else str(has_more) == "1"
    next_cursor = body.get("nextCursor") if isinstance(body, dict) else None

    if seller_external_uid:
        conversations = [
            row for row in conversations
            if _normalize_party_id(row.get("peerUserId")) != seller_external_uid
        ]
    conversations = [row for row in conversations if _is_displayable_conversation(row)]
    conversations = await _enrich_online_conversations_batch(db, account_id, conversations)
    conversations = await _ensure_online_conversation_records(
        db, account_id, conversations, seller_external_uid
    )
    conversations = await _apply_ai_reply_preview(db, account_id, conversations)
    conversations = _hydrate_online_conversation_avatars_from_cache(conversations)

    logger.info(
        "get_online_conversations_paged: IM 分页 tenantId=%d accountId=%d cursor=%s 返回 %d 条, hasMore=%s, nextCursor=%s", account_id, cursor, len(conversations), has_more, next_cursor,
    )
    result = {
        "conversations": conversations,
        "hasMore": has_more,
        "nextCursor": next_cursor,
    }
    _store_online_conversations_cache(
        cache_key,
        result,
        now=_time.time(),
    )
    return result


async def _fetch_live_online_conversations_safe(
    account_id: int,
    limit: int = 50,
    user_id: int | None = None,
) -> None:
    """后台异步拉取 live conversations 并写库，不阻塞主响应。

    作为账号切换时的预热：本次响应返回 DB 数据，下次刷新即可看到 live 数据。
    使用独立 session 避免与请求 session 共享生命周期。
    """
    try:
        from ..core.database import async_session
        async with async_session() as bg_db:
            await _fetch_live_online_conversations(
                bg_db, account_id,
                limit=limit,
                user_id=user_id,
            )
    except Exception as exc:
        logger.debug(
            "后台拉取 live conversations 失败 errorType=%s",
            type(exc).__name__,
        )


async def _hydrate_online_conversation_avatars_async(
    account_id: int,
    conversations: list[dict[str, Any]],
) -> None:
    """后台异步拉取远程头像并写库，不阻塞主响应。

    使用独立 session 避免与请求 session 共享生命周期。
    """
    if not _claim_avatar_hydration(account_id):
        return
    try:
        from ..core.database import async_session
        async with async_session() as bg_db:
            await _hydrate_online_conversation_avatars(
                bg_db, account_id, conversations
            )
    except Exception as exc:
        logger.debug("后台拉取远程头像失败 errorType=%s", type(exc).__name__)
    finally:
        _release_avatar_hydration(account_id)


def _hydrate_online_conversation_avatars_from_cache(
    conversations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """仅做内存层面的 image URL 规范化，不发起任何外部 HTTP 调用。

    远程头像由 _hydrate_online_conversation_avatars_async 后台异步拉取，
    下次刷新会从 DB 拿到最新头像。本次响应先返回已有 DB 缓存。
    """
    for conv in conversations:
        avatar = conv.get("buyerAvatar") or ""
        if avatar:
            conv["buyerAvatar"] = _normalize_image_url(avatar)
    return conversations


async def _enrich_online_conversations_batch(
    db: AsyncSession,
    account_id: int,
    conversations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """批量补全会话的用户名、商品信息。

    将原本 N+1 查询（每会话 5 次独立 SQL）改为 5 次批量 IN 查询，再在内存里匹配回填。
    """
    if not conversations:
        return conversations

    # 收集需要兜底的 sid 列表 和 goods_id 列表
    sids_need_fallback: list[str] = []  # 需要 fallback_row 的 sid（goods_id 或 peer_user_name 为空）
    goods_ids: list[str] = []            # 所有非空 goods_id
    peer_user_ids_real: list[str] = []   # 真实（非 sid:）peer_user_id 且无 peer_user_name

    # 用于内存匹配的索引
    by_sid: dict[str, dict[str, Any]] = {}
    by_goods_id: dict[str, list[dict[str, Any]]] = {}
    by_peer_id_real: dict[str, list[dict[str, Any]]] = {}

    for conv in conversations:
        sid = conv.get("sid", "")
        peer_user_id = conv.get("peerUserId", "") or ""
        peer_user_name = conv.get("peerUserName", "") or ""
        goods_id = conv.get("goodsId", "") or ""

        if sid:
            by_sid.setdefault(sid, conv)
            if (not goods_id or not peer_user_name):
                sids_need_fallback.append(sid)

        if goods_id:
            goods_ids.append(goods_id)
            by_goods_id.setdefault(goods_id, []).append(conv)

        if not peer_user_name and peer_user_id and not peer_user_id.startswith("sid:"):
            peer_user_ids_real.append(peer_user_id)
            by_peer_id_real.setdefault(peer_user_id, []).append(conv)

    # === 批量查询1：fallback_row（每个 sid 取最新 5 条 IN 消息）===
    fallback_map: dict[str, list[dict[str, Any]]] = {}
    if sids_need_fallback:
        unique_sids = list({s for s in sids_need_fallback if s})
        if unique_sids:
            try:
                fb_rows = await db.execute(
                    text("""
                        SELECT
                            s_id,
                            xy_goods_id,
                            reminder_url,
                            reminder_content,
                            sender_user_name,
                            message_time,
                            id
                        FROM xianyu_chat_message WHERE account_id = :account_id
                          AND s_id COLLATE utf8mb4_unicode_ci IN :sids
                          AND deleted = 0
                          AND direction = 'IN'
                          AND (
                            (xy_goods_id IS NOT NULL AND xy_goods_id != '')
                            OR (reminder_url IS NOT NULL AND reminder_url != '')
                            OR (sender_user_name IS NOT NULL AND sender_user_name != '' AND sender_user_name != '我')
                            OR (reminder_content IS NOT NULL AND reminder_content != '')
                          )
                        ORDER BY message_time DESC, id DESC
                    """).bindparams(bindparam("sids", expanding=True)),
                    {
                        "account_id": account_id,
                        "sids": unique_sids,
                    }
                )
                for fb in fb_rows.mappings().all():
                    fb_sid = str(fb.get("s_id") or "")
                    if not fb_sid:
                        continue
                    fallback_map.setdefault(fb_sid, []).append(dict(fb))
                    # 每个 sid 最多保留 5 条
                    if len(fallback_map[fb_sid]) >= 5:
                        # 由于按时间倒序，前 5 条已是最新的
                        pass
            except Exception as exc:
                logger.debug("批量 fallback_row 查询失败 errorType=%s", type(exc).__name__)

    # 应用 fallback 结果
    for sid, fb_list in fallback_map.items():
        conv = by_sid.get(sid)
        if not conv:
            continue
        goods_id = conv.get("goodsId", "") or ""
        peer_user_name = conv.get("peerUserName", "") or ""
        for fb in fb_list[:5]:
            fb_goods_id = str(fb.get("xy_goods_id") or "")
            fb_reminder_url = str(fb.get("reminder_url") or "")
            fb_reminder_content = str(fb.get("reminder_content") or "")
            fb_sender_name = str(fb.get("sender_user_name") or "")

            if not goods_id:
                if fb_goods_id and fb_goods_id.isdigit():
                    goods_id = fb_goods_id
                    conv["goodsId"] = goods_id
                elif fb_reminder_url:
                    m = re.search(r'[?&]itemId=(\d+)', fb_reminder_url)
                    if not m:
                        m = re.search(r'[?&]id=(\d+)', fb_reminder_url)
                    if m:
                        goods_id = m.group(1)
                        conv["goodsId"] = goods_id

            if not peer_user_name:
                if fb_sender_name and fb_sender_name not in ("我", "买家", ""):
                    cleaned = normalize_peer_name(fb_sender_name)
                    if cleaned:
                        peer_user_name = cleaned
                        conv["peerUserName"] = cleaned
                if not peer_user_name and fb_reminder_content:
                    extracted = extract_username_from_reminder(fb_reminder_content)
                    if extracted:
                        cleaned = normalize_peer_name(extracted)
                        if cleaned:
                            peer_user_name = cleaned
                            conv["peerUserName"] = cleaned

            if goods_id and peer_user_name:
                break

    # 重新收集需要补 goods_id 的会话（fallback 后可能新增）
    goods_ids = []
    by_goods_id = {}
    for conv in conversations:
        goods_id = conv.get("goodsId", "") or ""
        if goods_id:
            goods_ids.append(goods_id)
            by_goods_id.setdefault(goods_id, []).append(conv)

    # === 批量查询2：xianyu_goods 商品信息 ===
    goods_data_map: dict[str, dict[str, Any]] = {}
    if goods_ids:
        unique_goods_ids = list({g for g in goods_ids if g})
        if unique_goods_ids:
            try:
                goods_rows = await db.execute(
                    text("""
                        SELECT external_goods_id, goods_id, title, cover_pic,
                               image_url, image_urls, detail_info, sold_price, status
                        FROM xianyu_goods WHERE account_id = :account_id
                          AND deleted = 0
                          AND (external_goods_id IN :goods_ids OR goods_id IN :goods_ids)
                    """).bindparams(
                        bindparam("goods_ids", expanding=True)
                    ),
                    {
                        "account_id": account_id,
                        "goods_ids": unique_goods_ids,
                    }
                )
                for gr in goods_rows.mappings().all():
                    ext_id = str(gr.get("external_goods_id") or "")
                    gid = str(gr.get("goods_id") or "")
                    # 同一 goods_id 可能有多条记录，取第一条
                    for key in (ext_id, gid):
                        if key and key not in goods_data_map:
                            goods_data_map[key] = dict(gr)
            except Exception as exc:
                logger.debug("批量商品查询失败 errorType=%s", type(exc).__name__)

    # 应用商品信息
    sids_need_listing: list[str] = []  # 缺商品标题或封面图的会话，需查商品卡片消息(content_type=8)
    for conv in conversations:
        goods_id = conv.get("goodsId", "") or ""
        sid = conv.get("sid", "")
        missing_title = not conv.get("goodsTitle") or str(conv.get("goodsTitle", "")).strip() == ""
        missing_cover = not conv.get("goodsCoverPic")
        if not goods_id:
            # 无商品ID，但若有 sId 仍可从 content_type=8 消息提取标题和封面图
            if sid and (missing_title or missing_cover):
                sids_need_listing.append(sid)
            continue
        goods_data = goods_data_map.get(goods_id)
        if not goods_data:
            # 没匹配到商品记录，标记需要查商品卡片消息
            if sid and (missing_title or missing_cover):
                sids_need_listing.append(sid)
            continue
        if not conv.get("goodsTitle") or conv["goodsTitle"] == goods_id:
            conv["goodsTitle"] = goods_data.get("title") or ""
        if not conv.get("goodsCoverPic"):
            cover_pic = (
                _normalize_image_url(goods_data.get("cover_pic"))
                or _normalize_image_url(goods_data.get("image_url"))
                or _extract_cover_from_text_blob(goods_data.get("image_urls"))
                or _extract_cover_from_text_blob(goods_data.get("detail_info"))
            )
            if cover_pic:
                conv["goodsCoverPic"] = cover_pic
        if not conv.get("goodsPrice"):
            conv["goodsPrice"] = goods_data.get("sold_price") or ""
        if conv.get("goodsStatus") is None:
            conv["goodsStatus"] = goods_data.get("status")
        # 标题或封面图仍缺，标记需要查商品卡片消息
        if sid and (not conv.get("goodsTitle") or str(conv.get("goodsTitle", "")).strip() == "" or not conv.get("goodsCoverPic")):
            sids_need_listing.append(sid)

    # === 批量查询3：订单商品标题（仅对仍缺标题且 goods_id 存在的会话）===
    goods_ids_for_order_title = []
    for conv in conversations:
        goods_id = conv.get("goodsId", "") or ""
        if goods_id and (not conv.get("goodsTitle") or str(conv.get("goodsTitle", "")).strip() == ""):
            goods_ids_for_order_title.append(goods_id)
    if goods_ids_for_order_title:
        unique_goods_ids = list({g for g in goods_ids_for_order_title if g})
        if unique_goods_ids:
            try:
                order_item_rows = await db.execute(
                    text("""
                        SELECT oi.goods_title, o.external_order_id, oi.goods_id
                        FROM xianyu_trade_order_item oi
                        JOIN xianyu_trade_order o ON o.id = oi.order_id
                        WHERE o.account_id = :account_id
                          AND o.deleted = 0
                          AND oi.deleted = 0
                          AND (o.external_order_id IN :goods_ids OR oi.goods_id IN :goods_ids)
                          AND oi.goods_title IS NOT NULL
                          AND oi.goods_title != ''
                        ORDER BY o.id DESC
                    """).bindparams(
                        bindparam("goods_ids", expanding=True)
                    ),
                    {
                        "account_id": account_id,
                        "goods_ids": unique_goods_ids,
                    }
                )
                order_title_map: dict[str, str] = {}
                for oi in order_item_rows.mappings().all():
                    ext_id = str(oi.get("external_order_id") or "")
                    gid = str(oi.get("goods_id") or "")
                    title = str(oi.get("goods_title") or "")
                    if not title:
                        continue
                    for key in (ext_id, gid):
                        if key and key not in order_title_map:
                            order_title_map[key] = title
                for conv in conversations:
                    goods_id = conv.get("goodsId", "") or ""
                    if not goods_id:
                        continue
                    title = order_title_map.get(goods_id)
                    if title and (not conv.get("goodsTitle") or str(conv.get("goodsTitle", "")).strip() == ""):
                        conv["goodsTitle"] = title
            except Exception as exc:
                logger.debug("批量订单商品标题查询失败 errorType=%s", type(exc).__name__)

    # === 批量查询4：商品卡片消息（content_type=8，提取标题与封面图）===
    if sids_need_listing:
        unique_sids = list({s for s in sids_need_listing if s})
        if unique_sids:
            try:
                listing_rows = await db.execute(
                    text("""
                        SELECT s_id, msg_content, complete_msg
                        FROM xianyu_chat_message WHERE account_id = :account_id
                          AND s_id COLLATE utf8mb4_unicode_ci IN :sids
                          AND direction = 'OUT'
                          AND content_type = 8
                          AND (msg_content IS NOT NULL OR complete_msg IS NOT NULL)
                        ORDER BY id DESC
                    """).bindparams(bindparam("sids", expanding=True)),
                    {
                        "account_id": account_id,
                        "sids": unique_sids,
                    }
                )
                listing_title_map: dict[str, str] = {}
                listing_cover_map: dict[str, str] = {}
                for lr in listing_rows.mappings().all():
                    lr_sid = str(lr.get("s_id") or "")
                    if not lr_sid:
                        continue
                    # 标题（msg_content）
                    if lr_sid not in listing_title_map:
                        title_val = str(lr.get("msg_content") or "").strip()
                        if title_val:
                            listing_title_map[lr_sid] = title_val[:200]
                    # 封面图（从 complete_msg JSON 提取 itemMainPic）
                    if lr_sid not in listing_cover_map:
                        cover_url = _extract_item_main_pic(lr.get("complete_msg"))
                        if cover_url:
                            listing_cover_map[lr_sid] = cover_url
                for conv in conversations:
                    sid = conv.get("sid", "")
                    if not sid:
                        continue
                    if not conv.get("goodsTitle") or str(conv.get("goodsTitle", "")).strip() == "":
                        title = listing_title_map.get(sid)
                        if title:
                            conv["goodsTitle"] = title
                    if not conv.get("goodsCoverPic"):
                        cover = listing_cover_map.get(sid)
                        if cover:
                            conv["goodsCoverPic"] = cover
            except Exception as exc:
                logger.debug("批量商品卡片消息查询失败 errorType=%s", type(exc).__name__)

    # === 批量查询5：订单表买家名称（仅对真实 peer_user_id 且无 peer_user_name 的会话）===
    # 重新收集 fallback 后仍缺 peer_user_name 的会话
    peer_user_ids_real = []
    by_peer_id_real = {}
    for conv in conversations:
        peer_user_id = conv.get("peerUserId", "") or ""
        peer_user_name = conv.get("peerUserName", "") or ""
        if not peer_user_name and peer_user_id and not peer_user_id.startswith("sid:"):
            peer_user_ids_real.append(peer_user_id)
            by_peer_id_real.setdefault(peer_user_id, []).append(conv)

    if peer_user_ids_real:
        # 收集所有可能的 buyer_id 变体（带/不带 @goofish）
        all_variants: list[str] = []
        for pid in peer_user_ids_real:
            all_variants.extend(_peer_id_variants(pid))
        unique_variants = list({v for v in all_variants if v})
        if unique_variants:
            try:
                order_buyer_rows = await db.execute(
                    text("""
                        SELECT buyer_id, COALESCE(NULLIF(buyer_nickname, ''), NULLIF(buyer_name, '')) AS buyer_display_name
                        FROM xianyu_trade_order WHERE account_id = :account_id
                          AND deleted = 0
                          AND buyer_id IN :buyer_variants
                          AND COALESCE(NULLIF(buyer_nickname, ''), NULLIF(buyer_name, '')) IS NOT NULL
                        ORDER BY id DESC
                    """).bindparams(bindparam("buyer_variants", expanding=True)),
                    {
                        "account_id": account_id,
                        "buyer_variants": unique_variants,
                    }
                )
                buyer_name_map: dict[str, str] = {}
                for ob in order_buyer_rows.mappings().all():
                    bid = str(ob.get("buyer_id") or "")
                    name = str(ob.get("buyer_display_name") or "")
                    if not bid or not name:
                        continue
                    # 只记录第一次（按 id DESC 已是最新的）
                    if bid not in buyer_name_map:
                        buyer_name_map[bid] = name
                for pid, convs in by_peer_id_real.items():
                    name = None
                    for variant in _peer_id_variants(pid):
                        if variant in buyer_name_map:
                            name = buyer_name_map[variant]
                            break
                    if not name:
                        continue
                    for conv in convs:
                        if not conv.get("peerUserName"):
                            conv["peerUserName"] = name
            except Exception as exc:
                logger.debug("批量订单买家名称查询失败 errorType=%s", type(exc).__name__)

    # === 最终兜底：避免一律显示"买家"，优先使用更稳定但不误导的占位名 ===
    for conv in conversations:
        if conv.get("peerUserName"):
            continue
        peer_user_id = conv.get("peerUserId", "") or ""
        sid = conv.get("sid", "") or ""
        if peer_user_id and not peer_user_id.startswith("sid:"):
            conv["peerUserName"] = peer_user_id[-6:]
        elif sid and sid != "hello":
            conv["peerUserName"] = f"用户{str(sid)[-4:]}"

    # === 批量持久化商品封面图/标题到 DB（避免每次查询都重新 enrichment）===
    # 仅对有 conversationId（DB行ID）且本次新获取到 goods_cover_pic/goods_title 的会话执行
    for conv in conversations:
        conv_id = conv.get("conversationId")
        if not conv_id:
            continue
        try:
            conv_id_int = int(conv_id)
        except (TypeError, ValueError):
            continue
        if conv_id_int <= 0:
            continue
        cover_pic = _normalize_image_url(conv.get("goodsCoverPic"))
        title = str(conv.get("goodsTitle") or "").strip()
        if not cover_pic and not title:
            continue
        try:
            await db.execute(
                text("""
                    UPDATE xianyu_conversation
                    SET goods_cover_pic = COALESCE(NULLIF(:cover_pic, ''), goods_cover_pic),
                        goods_title = COALESCE(NULLIF(:title, ''), goods_title),
                        s_id = COALESCE(NULLIF(s_id, ''), :sid),
                        updated_time = NOW()
                    WHERE id = :conv_id
                """),
                {
                    "conv_id": conv_id_int,
                    "cover_pic": cover_pic,
                    "title": title,
                    "sid": str(conv.get("sid") or "").strip(),
                },
            )
        except Exception:
            # 持久化失败不影响响应，下次查询会重新 enrichment
            pass

    return conversations


async def get_context_messages(
    db: AsyncSession,
    account_id: int,
    s_id: str,
    limit: int = 50,
    offset: int = 0,
    user_id: int | None = None,
    peer_user_id: str | None = None,
) -> tuple[list[dict], int]:
    """获取会话上下文消息。

    查询逻辑（三种分支互斥）：
    1. s_id 非空 → 只按 base.s_id 查询（忽略 peer_user_id）
    2. s_id 为空但 peer_user_id 非空 → 按 sender_user_id / receiver_user_id / peer_external_uid 查询
    3. 两者都空 → 返回空数组

    所有字符串比较均显式 COLLATE utf8mb4_unicode_ci 以避免 1267 排序规则冲突。
    """
    # 防御性参数归一化
    s_id = str(s_id or "").strip()
    peer_user_id = str(peer_user_id or "").strip()
    if s_id.startswith("sid:"):
        s_id = s_id[4:]
    # 如果 s_id 包含 @goofish 后缀，也去掉
    if s_id.endswith("@goofish"):
        s_id = s_id[:-8]
    seller_external_uid = await _load_seller_external_uid(db, account_id)
    if peer_user_id and seller_external_uid and _normalize_party_id(peer_user_id) == seller_external_uid:
        return [], 0

    # 共享的 JOIN 子句
    base_join = """
        JOIN xianyu_account a
            ON a.id = base.account_id
    """

    # === 分支1: s_id 非空 —— 只按 s_id 查询 ===
    if s_id:
        # 同时匹配裸 s_id、带 @goofish 后缀、带 sid: 前缀的 s_id
        s_id_goofish = f"{s_id}@goofish"
        s_id_sid_prefixed = f"sid:{s_id}"
        s_id_goofish_sid_prefixed = f"sid:{s_id}@goofish"
        peer_user_id_goofish = ""
        s_id_where = """
            AND base.s_id COLLATE utf8mb4_unicode_ci IN (:s_id, :s_id_goofish, :s_id_sid_prefixed, :s_id_goofish_sid_prefixed)
        """
        if peer_user_id:
            peer_user_id_goofish = f"{peer_user_id}@goofish" if not peer_user_id.endswith("@goofish") else peer_user_id
        count_result = await db.execute(
            text(f"""
                SELECT COUNT(*)
                FROM xianyu_chat_message base
                {base_join}
                WHERE base.account_id = :account_id
                  AND base.deleted = 0
                  AND base.content_type NOT IN (32)
                  {s_id_where}
            """),
            {
                "account_id": account_id,
                "s_id": s_id,
                "s_id_goofish": s_id_goofish,
                "s_id_sid_prefixed": s_id_sid_prefixed,
                "s_id_goofish_sid_prefixed": s_id_goofish_sid_prefixed,
                "peer_user_id": peer_user_id,
                "peer_user_id_goofish": peer_user_id_goofish,
                "user_id": user_id,
            }
        )
        total = count_result.scalar() or 0

        sql_limit = int(limit or 50) + int(offset or 0)
        rows = await db.execute(
            text(f"""
                SELECT
                    base.id, base.pnm_id, base.s_id AS sid, base.content_type AS contentType,
                    base.msg_content AS msgContent, base.complete_msg AS completeMsg, base.sender_user_id AS senderUserId,
                    base.sender_user_name AS senderUserName, base.xy_goods_id AS xyGoodsId,
                    base.message_time AS messageTime, base.direction, base.reminder_content AS reminderContent,
                    base.reminder_url AS reminderUrl, base.read_status AS readStatus,
                    base.receiver_user_id AS receiverUserId, base.peer_external_uid AS peerExternalUid
                FROM xianyu_chat_message base
                {base_join}
                WHERE base.account_id = :account_id
                  AND base.deleted = 0
                  AND base.content_type NOT IN (32)
                  {s_id_where}
                ORDER BY base.message_time DESC
                LIMIT :sql_limit
            """),
            {
                "account_id": account_id,
                "s_id": s_id,
                "s_id_goofish": s_id_goofish,
                "s_id_sid_prefixed": s_id_sid_prefixed,
                "s_id_goofish_sid_prefixed": s_id_goofish_sid_prefixed,
                "peer_user_id": peer_user_id,
                "peer_user_id_goofish": peer_user_id_goofish,
                "sql_limit": sql_limit,
                "user_id": user_id,
            }
        )
        messages = [dict(row) for row in rows.mappings().all()]
        return await _finalize_context_messages(
            db, account_id,
            messages,
            s_id,
            peer_user_id,
            limit,
            offset,
            filter_base_messages_by_peer=False,
        )

    # === 分支2: s_id 为空、peer_user_id 非空 —— 按真实 UID 查询 ===
    if peer_user_id:
        # 尝试多种格式匹配：裸 UID 和带 @goofish 后缀的 UID
        peer_user_id_goofish = f"{peer_user_id}@goofish" if not peer_user_id.endswith("@goofish") else peer_user_id
        uid_where = """
            AND (
                base.sender_user_id COLLATE utf8mb4_unicode_ci IN (:peer_user_id, :peer_user_id_goofish)
                OR base.receiver_user_id COLLATE utf8mb4_unicode_ci IN (:peer_user_id, :peer_user_id_goofish)
                OR base.peer_external_uid COLLATE utf8mb4_unicode_ci IN (:peer_user_id, :peer_user_id_goofish)
            )
        """
        count_result = await db.execute(
            text(f"""
                SELECT COUNT(*)
                FROM xianyu_chat_message base
                {base_join}
                WHERE base.account_id = :account_id
                  AND base.deleted = 0
                  AND base.content_type NOT IN (32)
                  {uid_where}
            """),
            {
                "account_id": account_id,
                "peer_user_id": peer_user_id, "peer_user_id_goofish": peer_user_id_goofish,
                "user_id": user_id
            }
        )
        total = count_result.scalar() or 0

        # 如果通过 peer_user_id 直接匹配不到消息，尝试先找到关联的 s_id
        if total == 0:
            # 通过 peer_user_id 查找关联的 s_id（可能在 sender_user_id/receiver_user_id/peer_external_uid 中）
            sid_result = await db.execute(
                text(f"""
                    SELECT base.s_id
                    FROM xianyu_chat_message base
                    {base_join}
                    WHERE base.account_id = :account_id
                      AND base.deleted = 0
                      AND base.content_type NOT IN (32)
                      AND (
                          base.sender_user_id COLLATE utf8mb4_unicode_ci IN (:peer_user_id, :peer_user_id_goofish)
                          OR base.receiver_user_id COLLATE utf8mb4_unicode_ci IN (:peer_user_id, :peer_user_id_goofish)
                          OR base.peer_external_uid COLLATE utf8mb4_unicode_ci IN (:peer_user_id, :peer_user_id_goofish)
                      )
                    ORDER BY base.message_time DESC
                    LIMIT 1
                """),
                {
                    "account_id": account_id,
                    "peer_user_id": peer_user_id, "peer_user_id_goofish": peer_user_id_goofish,
                    "user_id": user_id
                }
            )
            found_sid = sid_result.scalar_one_or_none()
            if found_sid:
                # 使用找到的 s_id 重新查询（进入分支1逻辑）
                count_result = await db.execute(
                    text(f"""
                        SELECT COUNT(*)
                        FROM xianyu_chat_message base
                        {base_join}
                        WHERE base.account_id = :account_id
                          AND base.s_id COLLATE utf8mb4_unicode_ci = :s_id COLLATE utf8mb4_unicode_ci
                          AND base.deleted = 0
                          AND base.content_type NOT IN (32)
                    """),
                    {"account_id": account_id, "s_id": found_sid, "user_id": user_id}
                )
                total = count_result.scalar() or 0
                sql_limit = int(limit or 50) + int(offset or 0)
                rows = await db.execute(
                    text(f"""
                        SELECT
                            base.id, base.pnm_id, base.s_id AS sid, base.content_type AS contentType,
                            base.msg_content AS msgContent, base.complete_msg AS completeMsg, base.sender_user_id AS senderUserId,
                            base.sender_user_name AS senderUserName, base.xy_goods_id AS xyGoodsId,
                            base.message_time AS messageTime, base.direction, base.reminder_content AS reminderContent,
                            base.reminder_url AS reminderUrl, base.read_status AS readStatus,
                            base.receiver_user_id AS receiverUserId, base.peer_external_uid AS peerExternalUid
                        FROM xianyu_chat_message base
                        {base_join}
                        WHERE base.account_id = :account_id
                          AND base.s_id COLLATE utf8mb4_unicode_ci = :s_id COLLATE utf8mb4_unicode_ci
                          AND base.deleted = 0
                          AND base.content_type NOT IN (32)
                        ORDER BY base.message_time DESC
                        LIMIT :sql_limit
                    """),
                    {
                        "account_id": account_id,
                        "s_id": found_sid, "sql_limit": sql_limit,
                        "user_id": user_id,
                    }
                )
                messages = [dict(row) for row in rows.mappings().all()]
                return await _finalize_context_messages(
                    db, account_id,
                    messages,
                    found_sid,
                    peer_user_id,
                    limit,
                    offset,
                )
            # 也尝试从 xianyu_conversation 表查找
            conv_result = await db.execute(
                text("""
                    SELECT cm.s_id
                    FROM xianyu_conversation c
                    JOIN xianyu_chat_message cm
                        ON cm.account_id = c.account_id
                       AND cm.s_id COLLATE utf8mb4_unicode_ci = c.peer_key COLLATE utf8mb4_unicode_ci
                    WHERE c.account_id = :account_id
                      AND (
                          c.external_buyer_id COLLATE utf8mb4_unicode_ci IN (:peer_user_id, :peer_user_id_goofish)
                          OR c.peer_external_uid COLLATE utf8mb4_unicode_ci IN (:peer_user_id, :peer_user_id_goofish)
                          OR c.peer_key COLLATE utf8mb4_unicode_ci IN (:peer_user_id, :peer_user_id_goofish)
                      )
                    ORDER BY cm.message_time DESC
                    LIMIT 1
                """),
                {
                    "account_id": account_id,
                    "peer_user_id": peer_user_id, "peer_user_id_goofish": peer_user_id_goofish,
                }
            )
            conv_sid = conv_result.scalar_one_or_none()
            if conv_sid:
                count_result = await db.execute(
                    text(f"""
                        SELECT COUNT(*)
                        FROM xianyu_chat_message base
                        {base_join}
                        WHERE base.account_id = :account_id
                          AND base.s_id COLLATE utf8mb4_unicode_ci = :s_id COLLATE utf8mb4_unicode_ci
                          AND base.deleted = 0
                          AND base.content_type NOT IN (32)
                    """),
                    {"account_id": account_id, "s_id": conv_sid, "user_id": user_id}
                )
                total = count_result.scalar() or 0
                sql_limit = int(limit or 50) + int(offset or 0)
                rows = await db.execute(
                    text(f"""
                        SELECT
                            base.id, base.pnm_id, base.s_id AS sid, base.content_type AS contentType,
                            base.msg_content AS msgContent, base.complete_msg AS completeMsg, base.sender_user_id AS senderUserId,
                            base.sender_user_name AS senderUserName, base.xy_goods_id AS xyGoodsId,
                            base.message_time AS messageTime, base.direction, base.reminder_content AS reminderContent,
                            base.reminder_url AS reminderUrl, base.read_status AS readStatus,
                            base.receiver_user_id AS receiverUserId, base.peer_external_uid AS peerExternalUid
                        FROM xianyu_chat_message base
                        {base_join}
                        WHERE base.account_id = :account_id
                          AND base.s_id COLLATE utf8mb4_unicode_ci = :s_id COLLATE utf8mb4_unicode_ci
                          AND base.deleted = 0
                          AND base.content_type NOT IN (32)
                        ORDER BY base.message_time DESC
                        LIMIT :sql_limit
                    """),
                    {
                        "account_id": account_id,
                        "s_id": conv_sid, "sql_limit": sql_limit,
                        "user_id": user_id,
                    }
                )
                messages = [dict(row) for row in rows.mappings().all()]
                return await _finalize_context_messages(
                    db, account_id,
                    messages,
                    conv_sid,
                    peer_user_id,
                    limit,
                    offset,
                )
            return await _finalize_context_messages(
                db, account_id,
                [],
                "",
                peer_user_id,
                limit,
                offset,
            )

        sql_limit = int(limit or 50) + int(offset or 0)
        rows = await db.execute(
            text(f"""
                SELECT
                    base.id, base.pnm_id, base.s_id AS sid, base.content_type AS contentType,
                    base.msg_content AS msgContent, base.complete_msg AS completeMsg, base.sender_user_id AS senderUserId,
                    base.sender_user_name AS senderUserName, base.xy_goods_id AS xyGoodsId,
                    base.message_time AS messageTime, base.direction, base.reminder_content AS reminderContent,
                    base.reminder_url AS reminderUrl, base.read_status AS readStatus,
                    base.receiver_user_id AS receiverUserId, base.peer_external_uid AS peerExternalUid
                FROM xianyu_chat_message base
                {base_join}
                WHERE base.account_id = :account_id
                  AND base.deleted = 0
                  AND base.content_type NOT IN (32)
                  {uid_where}
                ORDER BY base.message_time DESC
                LIMIT :sql_limit
            """),
            {
                "account_id": account_id,
                "peer_user_id": peer_user_id,
                "peer_user_id_goofish": peer_user_id_goofish,
                "sql_limit": sql_limit,
                "user_id": user_id,
            }
        )
        messages = [dict(row) for row in rows.mappings().all()]
        return await _finalize_context_messages(
            db, account_id,
            messages,
            s_id,
            peer_user_id,
            limit,
            offset,
        )

    # === 分支3: 两者都空 ===
    return [], 0
