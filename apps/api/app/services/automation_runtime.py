"""
AI 自动回复编排运行时（开源版）。

在 WebSocket 消息落库后，由 ws_startup._run_ai_auto_reply_after_message_saved 调用。
负责把"收到的买家消息"和"已配置的通用模型"串起来，完成端到端自动回复：

1. 检查自动回复作用域（全局 / 账号 / 商品级）是否启用
2. 加载 AI 客服配置（systemPrompt、知识库、聊天规则）
3. 按明确时区检查工作时段、模式、关键词、人工接管和持久日额度
4. 拼装上下文（商品信息、历史消息、明确绑定的知识内容）
5. 调用 ai_provider.generate_text 生成回复，并在发送前复核运行策略
6. 先持久化发送边界，再通过 ws_client.send_text_message 发送给买家
7. 按 ACK 结果记录 confirmed/failed/unknown，并幂等落库回复消息

同时提供 insert_notification，供 ws_delivery_handler 在自动发货失败时写通知。
"""
import datetime as dt
import hashlib
import json
import logging
import time
from dataclasses import replace
from typing import Any, Optional

from sqlalchemy import and_, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .ai_provider import _resolve_ai_config, generate_text
from .ai_auto_reply_attempt import (
    AiAutoReplyCommand,
    AiAutoReplyGenerationError,
    AiAutoReplyQuotaExceeded,
    AiAutoReplyRuntime,
    AiAutoReplySendResult,
    SqlAiAutoReplyAttemptStore,
)
from .ai_auto_reply_policy import evaluate_ai_auto_reply_policy
from .business_settings import (
    AI_CS_SETTING_KEY,
    load_business_setting,
    load_raw_business_setting,
)
from .ws_client import ws_manager
from .ws_storage import save_chat_message
from ..models.entities import AiAutoReplyAttempt, AutoReplyRule

logger = logging.getLogger(__name__)

# 自动回复最多携带的历史消息条数
_MAX_HISTORY_MESSAGES = 10
# A WebSocket replay can arrive after the first AI response even though the
# buyer sent it before that response.  Do not issue another model call for a
# late packet from the same turn; the window is intentionally generous enough
# to cover observed Xianyu delivery lag, but bounded so it cannot hide a new
# conversation hours later.
_LATE_INCOMING_REPLY_GRACE_MS = 5 * 60 * 1000


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _build_ai_reply_command(payload: dict[str, Any]) -> AiAutoReplyCommand:
    """Build a stable, account-isolated event key without persisting content.

    Upstream message identifiers are consumed only in memory and reduced to a
    SHA-256 digest before they reach the attempt table.  When the protocol does
    not provide one, the fallback includes the conversation, peer, timestamp,
    and a content digest so a replay remains stable without storing the buyer's
    message in the operational state machine.
    """
    account_id = int(payload.get("accountId") or 0)
    session_id = str(payload.get("sId") or "").strip()
    peer_id = str(payload.get("buyerId") or "").strip()
    goods_id = str(payload.get("goodsId") or "").strip()
    seller_external_uid = str(payload.get("sellerExternalUid") or "").strip()
    content = str(payload.get("content") or "")

    raw_source_ids = payload.get("sourceMessageUids")
    source_ids = (
        [str(item or "").strip() for item in raw_source_ids]
        if isinstance(raw_source_ids, list)
        else []
    )
    source_ids = [item for item in source_ids if item]
    if not source_ids:
        fallback_id = str(
            payload.get("sourceMessageUid") or payload.get("pnmId") or ""
        ).strip()
        if fallback_id:
            source_ids = [fallback_id]
    if not source_ids:
        source_ids = [
            "|".join(
                [
                    session_id,
                    peer_id,
                    str(
                        payload.get("latestMessageTime")
                        or payload.get("messageTime")
                        or ""
                    ),
                    _sha256_text(content),
                ]
            )
        ]

    # The latest persisted buyer message owns a debounced turn.  Replays with
    # the same latest source remain one event even if earlier batch members are
    # delivered in a different grouping after restart.
    source_message_digest = _sha256_text(
        f"ai-auto-reply-source:v1|{account_id}|{source_ids[-1]}"
    )
    event_key = _sha256_text(
        f"ai-auto-reply-event:v1|{account_id}|{source_message_digest}"
    )
    request_document = {
        "accountId": account_id,
        "sourceMessageDigest": source_message_digest,
        "sessionDigest": _sha256_text(session_id),
        "peerDigest": _sha256_text(peer_id),
        "goodsDigest": _sha256_text(goods_id),
        "contentDigest": _sha256_text(content),
    }
    request_digest = _sha256_text(
        json.dumps(request_document, sort_keys=True, separators=(",", ":"))
    )
    return AiAutoReplyCommand(
        event_key=event_key,
        request_digest=request_digest,
        account_id=account_id,
        source_message_digest=source_message_digest,
        session_id=session_id,
        peer_id=peer_id,
        goods_id=goods_id,
        seller_external_uid=seller_external_uid,
    )


def _parse_rule_keywords(raw: Any) -> list[str]:
    """解析规则关键词字符串，支持多种分隔符。"""
    text_value = str(raw or "").strip()
    if not text_value:
        return []
    separators = [",", "\uff0c", "\n", "\r", ";", "\uff1b", "|", "/", "\u3001"]
    for separator in separators[1:]:
        text_value = text_value.replace(separator, separators[0])
    return [item.strip() for item in text_value.split(separators[0]) if item.strip()]


async def _try_keyword_auto_reply(
    db: AsyncSession,
    *,
    account_id: int,
    content: str,
    s_id: str,
    buyer_id: str,
    buyer_name: str,
    goods_id: str,
    seller_external_uid: str,
) -> bool:
    """关键词自动回复规则匹配。

    在 AI 自动回复之前检查是否有匹配的关键词规则。若匹配则直接发送规则配置的
    回复内容并返回 True，调用方应跳过后续 AI 自动回复流程。
    """
    if not content or not account_id:
        return False

    query = select(AutoReplyRule).where(
        AutoReplyRule.deleted == 0,
        AutoReplyRule.status == 1,
        or_(
            AutoReplyRule.account_id == account_id,
            AutoReplyRule.account_id.is_(None),
        ),
    )
    result = await db.execute(query.order_by(
        AutoReplyRule.priority.desc(),
        AutoReplyRule.id.desc(),
    ))
    rules = result.scalars().all()
    if not rules:
        return False

    message_lower = content.lower()
    matched_rule = None
    for rule in rules:
        match_type = (rule.match_type or "keyword").strip().lower()
        if match_type == "all":
            matched_rule = rule
            break
        if match_type == "keyword":
            keywords = _parse_rule_keywords(rule.match_keywords)
            if keywords and any(kw.lower() in message_lower for kw in keywords):
                matched_rule = rule
                break

    if not matched_rule:
        # 同步商业版：写入未命中日志
        try:
            from ..models.entities import AutoReplyLog
            log_entry = AutoReplyLog(
                account_id=account_id,
                rule_id=None,
                trigger_message=content[:1000] if content else None,
                hit_type="none",
                status=0,
                action="manual",
                safety_reasons="未命中自动回复规则",
            )
            db.add(log_entry)
            await db.flush()
        except Exception as log_exc:
            logger.warning("写入未命中日志失败：%s", log_exc, exc_info=True)
        return False

    reply_text = str(matched_rule.reply_content or "").strip()
    if not reply_text:
        return False

    sent = await _send_reply_message(account_id, s_id, buyer_id, reply_text)
    if not sent:
        logger.warning(
            "关键词自动回复发送失败 accountId=%d ruleId=%d",
            account_id,
            matched_rule.id,
        )
        return False

    # 落库回复消息（与 AI 自动回复使用相同的持久化路径）
    pnm_id = f"kw-{account_id}-{int(time.time() * 1000)}-{hashlib.md5(reply_text.encode()).hexdigest()[:8]}"
    await _save_reply_message(
        db,
        account_id,
        s_id,
        buyer_id,
        buyer_name,
        reply_text,
        goods_id,
        seller_external_uid,
        pnm_id=pnm_id,
    )

    logger.info(
        "关键词自动回复已发送 accountId=%d ruleId=%d ruleName=%s",
        account_id,
        matched_rule.id,
        matched_rule.rule_name or "",
    )
    # 同步商业版：写入自动回复日志
    try:
        from ..models.entities import AutoReplyLog
        log_entry = AutoReplyLog(
            account_id=account_id,
            rule_id=matched_rule.id,
            trigger_message=content[:1000] if content else None,
            reply_content=reply_text[:1000] if reply_text else None,
            hit_type="keyword",
            status=1,
            action="auto_send_allowed",
        )
        db.add(log_entry)
        await db.flush()
    except Exception as log_exc:
        logger.warning("写入自动回复日志失败：%s", log_exc, exc_info=True)
    return True


async def process_incoming_message(db: AsyncSession, payload: dict[str, Any]) -> None:
    """处理收到的买家消息，按需触发 AI 自动回复。

    Args:
        db: 数据库会话（由调用方负责 commit）
        payload: 消息载荷，包含 accountId/buyerId/buyerName/content/sId/goodsId 等
    """
    account_id = int(payload.get("accountId") or 0)
    buyer_id = str(payload.get("buyerId") or "").strip()
    buyer_name = str(payload.get("buyerName") or "").strip()
    content = str(payload.get("content") or "").strip()
    s_id = str(payload.get("sId") or "").strip()
    goods_id = str(payload.get("goodsId") or "").strip()
    item_title = str(payload.get("itemTitle") or "").strip()
    seller_external_uid = str(payload.get("sellerExternalUid") or "").strip()

    if not account_id or not s_id or not content:
        logger.debug(
            "AI 自动回复跳过：缺少必要参数 accountId=%s sessionPresent=%s contentLen=%d",
            account_id,
            bool(s_id),
            len(content),
        )
        return

    # Resolve the actual conversation product before checking product scope or
    # calling the model. WebSocket text packets often omit xyGoodsId even when
    # the conversation is already associated with a concrete product.
    goods_context = await _resolve_goods_context(
        db,
        account_id=account_id,
        s_id=s_id,
        goods_id=goods_id,
        item_title=item_title,
    )
    goods_id = str(goods_context.get("goodsId") or goods_id).strip()
    item_title = str(goods_context.get("title") or item_title).strip()

    # Step 1: 检查自动回复作用域（全局 > 账号 > 商品）
    should_reply = await _check_auto_reply_scope(db, account_id, goods_id)
    if not should_reply:
        logger.info(
            "AI 自动回复跳过：作用域未开启 accountId=%d goodsPresent=%s",
            account_id,
            bool(goods_id),
        )
        return

    # Step 1.5: 会话级自动回复状态机检查（人工干预自动暂停/恢复）
    # 此检查在 AI 规则匹配之前生效，会话暂停时直接跳过 AI 回复
    conv_state_allow, conv_state_reason = await _check_conversation_auto_reply_state(
        db, account_id, s_id, content,
    )
    if not conv_state_allow:
        logger.info(
            "AI 自动回复跳过：会话级状态机 accountId=%d reason=%s",
            account_id, conv_state_reason,
        )
        return

    # Step 1.6: 关键词自动回复规则匹配（在 AI 自动回复之前）
    # 若匹配到关键词规则，直接发送规则配置的回复内容，跳过 AI 自动回复
    keyword_matched = await _try_keyword_auto_reply(
        db,
        account_id=account_id,
        content=content,
        s_id=s_id,
        buyer_id=buyer_id,
        buyer_name=buyer_name,
        goods_id=goods_id,
        seller_external_uid=seller_external_uid,
    )
    if keyword_matched:
        return

    # Step 2: 加载 AI 客服配置（含 systemPrompt / 知识库 / 聊天规则 / 人设）
    ai_config = await load_business_setting(db, AI_CS_SETTING_KEY)
    if not ai_config.get("enabled"):
        logger.info("AI 自动回复跳过：AI 客服主开关未开启 accountId=%d", account_id)
        return

    # If a buyer packet was delivered late, its source timestamp can precede
    # the seller's newest reply.  The old implementation treated it as a new
    # turn and generated another external message.  Suppress it before the
    # model call: it belongs to the already-handled turn and must never create
    # another request or another buyer-facing reply.
    latest_message_time = _coerce_message_time(
        payload.get("latestMessageTime") or payload.get("messageTime")
    )
    if await _is_late_incoming_before_latest_outbound(
        db,
        account_id=account_id,
        s_id=s_id,
        incoming_message_time=latest_message_time,
    ):
        logger.warning(
            "AI 自动回复跳过迟到消息，避免重复外发 accountId=%d incomingTime=%s",
            account_id,
            latest_message_time,
        )
        return

    policy = await evaluate_ai_auto_reply_policy(
        db,
        config=ai_config,
        account_id=account_id,
        session_id=s_id,
        message_content=content,
    )
    if not policy.allowed:
        logger.info(
            "AI auto-reply blocked by runtime policy accountId=%d reason=%s",
            account_id,
            policy.reason,
        )
        return

    # Step 3: 检查通用模型是否已配置
    provider_config = await _resolve_ai_config()
    if not provider_config.get("enabled"):
        logger.warning("AI 自动回复跳过：通用模型未配置或缺少 baseUrl/apiKey/model accountId=%d", account_id)
        return

    # Step 4: 拼装 system prompt（systemPrompt + 商品事实 + 知识库 + 聊天规则 + 人设）
    system_prompt = _build_system_prompt(ai_config, goods_context)
    model_content = _trim_product_fact(content, limit=12_000)

    # Step 5: 加载历史消息（carryContext=True 时）
    history_messages: list[dict[str, Any]] = []
    if ai_config.get("carryContext", True):
        history_messages = await _load_history_messages(db, account_id, s_id)

    # Step 6: 构造 messages。知识库只使用当前 AI 客服配置中明确绑定的
    # 内容（已由 _build_system_prompt 加入）；不再读取独立 JSON 文件向量库。
    messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
    for hist in history_messages:
        role = "assistant" if hist.get("direction") == "OUT" else "user"
        messages.append({"role": role, "content": hist.get("content") or ""})

    # The WebSocket callback persists the buyer message before it enters this
    # runtime.  When a debounced batch is supplied, all of its texts are
    # already present in history; appending the joined batch a second time made
    # the model see the same question twice and encouraged duplicate replies.
    batched_contents = payload.get("batchedContents")
    if not isinstance(batched_contents, list):
        batched_contents = []
    expected_tail = [
        _trim_product_fact(item, limit=4_000)
        for item in batched_contents[-20:]
        if str(item or "").strip()
    ]
    if not expected_tail and model_content:
        expected_tail = [model_content]
    history_input_tail = [
        str(item.get("content") or "").strip()
        for item in history_messages
        if str(item.get("direction") or "").upper() == "IN" and str(item.get("content") or "").strip()
    ]
    already_in_history = bool(expected_tail) and history_input_tail[-len(expected_tail):] == expected_tail
    if not (payload.get("contentAlreadyPersisted") and already_in_history):
        messages.append({"role": "user", "content": model_content})

    command = replace(
        _build_ai_reply_command(
            {
                **payload,
                "accountId": account_id,
                "buyerId": buyer_id,
                "sId": s_id,
                "goodsId": goods_id,
                "sellerExternalUid": seller_external_uid,
                "content": content,
            }
        ),
        quota_date=policy.local_date,
        quota_limit=policy.max_daily_replies,
        policy_timezone=policy.timezone_name,
    )
    runtime = AiAutoReplyRuntime(SqlAiAutoReplyAttemptStore(db))

    async def generate_reply() -> str:
        result = await generate_text(
            scene="ai_customer_service",
            system_prompt="",  # 已在 messages 中包含
            user_prompt="",
            temperature=0.6,
            messages=messages,
        )
        if not isinstance(result, dict) or not result.get("ok"):
            raise AiAutoReplyGenerationError("model_generation_failed")
        reply = str(result.get("content") or "").strip()
        if not reply:
            raise AiAutoReplyGenerationError("model_reply_empty")
        # A human can take over while the model request is in flight. Recheck
        # immediately before crossing the external-send boundary; denial is a
        # definite no-send failure and releases the reserved daily slot.
        latest_policy = await evaluate_ai_auto_reply_policy(
            db,
            config=ai_config,
            account_id=account_id,
            session_id=s_id,
            message_content=content,
        )
        if not latest_policy.allowed:
            raise AiAutoReplyGenerationError(latest_policy.reason)
        return reply

    async def send_reply(reply_text: str) -> AiAutoReplySendResult:
        # Close the final race between model completion and the actual socket
        # call. A policy lookup error is still a definite no-send at this point.
        try:
            send_policy = await evaluate_ai_auto_reply_policy(
                db,
                config=ai_config,
                account_id=account_id,
                session_id=s_id,
                message_content=content,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "AI auto-reply pre-send policy unavailable accountId=%d errorType=%s",
                account_id,
                type(exc).__name__,
            )
            return AiAutoReplySendResult.failed("policy_check_unavailable")
        if not send_policy.allowed:
            non_retryable = send_policy.reason in {
                "manual_mode",
                "human_intervention_active",
                "handoff_keyword_matched",
                "blacklist_keyword_matched",
            }
            return AiAutoReplySendResult.failed(
                send_policy.reason,
                retry_safe=not non_retryable,
            )
        return await _send_reply_message_result(
            account_id,
            s_id,
            buyer_id,
            reply_text,
        )

    async def save_reply(reply_text: str, local_key: str) -> int | None:
        return await _save_reply_message(
            db,
            account_id,
            s_id,
            buyer_id,
            buyer_name,
            reply_text,
            goods_id,
            seller_external_uid,
            pnm_id=local_key,
        )

    try:
        outcome = await runtime.execute(command, generate_reply, send_reply, save_reply)
    except AiAutoReplyQuotaExceeded:
        logger.info(
            "AI auto-reply blocked by durable daily quota accountId=%d",
            account_id,
        )
        return
    logger.info(
        "AI auto-reply state updated accountId=%d attemptId=%d state=%s repeated=%s",
        account_id,
        outcome.attempt_id,
        outcome.status,
        outcome.repeated,
    )
    if outcome.status in {"unknown", "message_sent"}:
        try:
            await insert_notification(
                db,
                outcome.attempt_id,
                "AI 自动回复需要核对",
                (
                    f"自动回复尝试 {outcome.attempt_id} 的状态为 {outcome.status}。"
                    "请在闲鱼 App 核对是否已向买家发送；未知结果不要重复发送。"
                ),
                "AI 自动回复核对提醒",
                "warn",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "AI reply attention notification failed attemptId=%d errorType=%s",
                outcome.attempt_id,
                type(exc).__name__,
            )


async def recover_ai_auto_reply_attempts(
    db: AsyncSession,
    *,
    limit: int = 100,
) -> dict[str, int]:
    """Reconcile expired send boundaries during startup without external resend.

    ``generating`` is a definite no-send and is failed/released without calling
    a model. ``message_sending`` is quarantined as unknown. ``message_sent`` is
    allowed to write only its deterministic local chat record from encrypted
    recovery data. No external callback is reachable from this recovery path.
    """
    now = dt.datetime.now()
    rows = (
        await db.execute(
            select(AiAutoReplyAttempt)
            .where(
                or_(
                    AiAutoReplyAttempt.state == "generating",
                    AiAutoReplyAttempt.state == "message_sending",
                    and_(
                        AiAutoReplyAttempt.state == "message_sent",
                        AiAutoReplyAttempt.retry_safe == 1,
                    ),
                ),
                or_(
                    AiAutoReplyAttempt.lease_until.is_(None),
                    AiAutoReplyAttempt.lease_until <= now,
                ),
            )
            .order_by(AiAutoReplyAttempt.id.asc())
            .limit(max(1, min(int(limit), 500)))
        )
    ).scalars().all()
    counts = {"confirmed": 0, "unknown": 0, "message_sent": 0, "failed": 0}
    for row in rows:
        command = AiAutoReplyCommand(
            event_key=str(row.event_key),
            request_digest=str(row.request_digest),
            account_id=int(row.account_id),
            source_message_digest=str(row.source_message_digest),
            session_id=str(row.session_id),
            peer_id=str(row.peer_id),
            goods_id=str(row.goods_id or ""),
            seller_external_uid=str(row.seller_external_uid or ""),
        )
        runtime = AiAutoReplyRuntime(SqlAiAutoReplyAttemptStore(db))

        async def forbidden_generation() -> str:
            raise RuntimeError("startup recovery cannot generate a reply")

        async def forbidden_send(_reply_text: str) -> AiAutoReplySendResult:
            raise RuntimeError("startup recovery cannot send a reply")

        async def save_recovered(reply_text: str, local_key: str) -> int | None:
            return await _save_reply_message(
                db,
                int(row.account_id),
                str(row.session_id),
                str(row.peer_id),
                "",
                reply_text,
                str(row.goods_id or ""),
                str(row.seller_external_uid or ""),
                pnm_id=local_key,
            )

        try:
            outcome = await runtime.execute(
                command,
                forbidden_generation,
                forbidden_send,
                save_recovered,
            )
            if outcome.status in counts:
                counts[outcome.status] += 1
        except Exception as exc:  # noqa: BLE001
            await db.rollback()
            counts["failed"] += 1
            logger.error(
                "AI reply startup recovery failed attemptId=%d errorType=%s",
                int(row.id),
                type(exc).__name__,
            )
    return counts


async def _check_conversation_auto_reply_state(
    db: AsyncSession,
    account_id: int,
    s_id: str,
    content: str,
) -> tuple[bool, str]:
    """会话级自动回复状态机检查（人工干预自动暂停/恢复）。

    业务规则：
      1. 买家发送"开启自动回复"指令 → 自动恢复（仅当未被用户手动关闭）
      2. 用户手动关闭（auto_reply_manual_disabled=1）→ 跳过 AI 回复
      3. 距上次人工回复 > 1 分钟，买家发新消息时自动恢复
      4. 距上次人工回复 < 1 分钟 → 保持暂停，跳过 AI 回复
      5. 此检查在 AI 规则匹配之前生效，会话暂停时直接跳过 AI 回复

    Returns:
        (allow_continue, reason)
        - (True, "allowed")：继续走 AI 回复流程
        - (True, "resumed")：刚自动恢复，继续走 AI 回复流程
        - (False, "manual_disabled"|"pause_window"|"resume_command_consumed")：跳过 AI 回复
    """
    if not account_id or not s_id:
        return (True, "allowed")

    normalized_sid = (
        str(s_id or "")
        .strip()
        .removeprefix("sid:")
        .removesuffix("@goofish")
    )
    if not normalized_sid:
        return (True, "allowed")

    try:
        conv_state_row = (await db.execute(
            text(
                """
                SELECT id, auto_reply_paused, auto_reply_manual_disabled, last_manual_reply_at
                FROM xianyu_conversation
                WHERE account_id = :account_id
                  AND (
                    REPLACE(REPLACE(COALESCE(peer_key, ''), 'sid:', ''), '@goofish', '') = :s_id
                    OR REPLACE(REPLACE(COALESCE(external_buyer_id, ''), 'sid:', ''), '@goofish', '') = :s_id
                  )
                ORDER BY last_message_time DESC, id DESC
                LIMIT 1
                """
            ),
            {"account_id": account_id, "s_id": normalized_sid},
        )).mappings().first()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "会话级状态查询失败，按允许处理 accountId=%d errorType=%s",
            account_id,
            type(exc).__name__,
        )
        return (True, "allowed")

    if not conv_state_row:
        # 未找到会话记录，按允许处理（首次消息或会话未同步）
        return (True, "allowed")

    conversation_db_id = int(conv_state_row["id"])
    conv_paused = int(conv_state_row.get("auto_reply_paused") or 0)
    conv_manual_disabled = int(conv_state_row.get("auto_reply_manual_disabled") or 0)
    conv_last_manual_at = conv_state_row.get("last_manual_reply_at")

    # 规则 1：买家发送"开启自动回复"指令
    RESUME_COMMAND = "开启自动回复"
    if content.strip() == RESUME_COMMAND:
        if conv_paused == 1 and conv_manual_disabled == 0:
            await db.execute(text("""
                UPDATE xianyu_conversation
                SET auto_reply_paused = 0, last_manual_reply_at = NULL, updated_time = NOW()
                WHERE id = :conversation_id
            """), {"conversation_id": conversation_db_id})
            logger.info(
                "[AUTO_REPLY] 买家发送'开启自动回复'指令，自动恢复 AI 回复 accountId=%d convId=%d",
                account_id, conversation_db_id,
            )
        elif conv_paused == 1 and conv_manual_disabled == 1:
            logger.info(
                "[AUTO_REPLY] 买家发送'开启自动回复'指令，但用户已手动关闭，保持暂停 accountId=%d convId=%d",
                account_id, conversation_db_id,
            )
        # 此条指令消息本身不触发 AI 回复（避免回复"开启自动回复"这条指令）
        return (False, "resume_command_consumed")

    if conv_paused != 1:
        return (True, "allowed")

    # 规则 2：用户手动关闭，跳过 AI 回复
    if conv_manual_disabled == 1:
        logger.info(
            "[AUTO_REPLY] 用户已手动关闭自动回复，跳过 AI 回复 accountId=%d convId=%d",
            account_id, conversation_db_id,
        )
        return (False, "manual_disabled")

    # 规则 3/4：人工干预暂停中，检查是否超过 1 分钟自动恢复
    now_ms = int(time.time() * 1000)
    if conv_last_manual_at is None:
        # 异常状态：暂停但无 last_manual_reply_at，直接恢复
        await db.execute(text("""
            UPDATE xianyu_conversation
            SET auto_reply_paused = 0, last_manual_reply_at = NULL, updated_time = NOW()
            WHERE id = :conversation_id
        """), {"conversation_id": conversation_db_id})
        logger.warning(
            "[AUTO_REPLY] 暂停状态异常（无 last_manual_reply_at），自动恢复 accountId=%d convId=%d",
            account_id, conversation_db_id,
        )
        return (True, "resumed")

    try:
        elapsed_ms = now_ms - int(conv_last_manual_at)
    except (TypeError, ValueError):
        elapsed_ms = 60_001  # 异常时按已超时处理

    if elapsed_ms >= 60_000:
        # 自动恢复（1 分钟超时）
        await db.execute(text("""
            UPDATE xianyu_conversation
            SET auto_reply_paused = 0, last_manual_reply_at = NULL, updated_time = NOW()
            WHERE id = :conversation_id
        """), {"conversation_id": conversation_db_id})
        logger.info(
            "[AUTO_REPLY] 距上次人工回复 %dms >= 60s，自动恢复 AI 回复 accountId=%d convId=%d",
            elapsed_ms, account_id, conversation_db_id,
        )
        return (True, "resumed")

    # 1 分钟内，保持暂停
    logger.info(
        "[AUTO_REPLY] 人工干预暂停中（%dms < 60s），跳过 AI 回复 accountId=%d convId=%d",
        elapsed_ms, account_id, conversation_db_id,
    )
    return (False, "pause_window")


async def _check_auto_reply_scope(
    db: AsyncSession,
    account_id: int,
    goods_id: str,
) -> bool:
    """检查自动回复作用域是否启用。

    优先级：商品级 > 账号级 > 全局（NULL 不继承全局，默认关闭）。
    """
    # 全局开关关闭 → 一律不回复
    global_enabled = await _load_global_enabled(db)
    if not global_enabled:
        return False

    # 商品级有值 → 按商品级
    if goods_id:
        goods_enabled = await _load_goods_auto_reply_enabled(db, account_id, goods_id)
        if goods_enabled is not None:
            return goods_enabled

    # 商品级 NULL → 按账号级
    account_scopes = await _load_account_scopes(db)
    accounts = account_scopes.get("accounts", {}) if isinstance(account_scopes, dict) else {}
    return bool(accounts.get(str(account_id), False))


async def _load_global_enabled(db: AsyncSession) -> bool:
    """读取 ai-customer-service.enabled 主开关。

    使用 load_business_setting 确保与前端配置页一致的读取路径，
    自动合并默认值（enabled 默认 False）。
    """
    config = await load_business_setting(db, AI_CS_SETTING_KEY)
    return bool(config.get("enabled", False))


async def _load_account_scopes(db: AsyncSession) -> dict:
    """读取 auto-reply-account-scopes 配置。"""
    config = await load_raw_business_setting(db, "auto-reply-account-scopes")
    return config if isinstance(config, dict) else {"accounts": {}}


async def _load_goods_auto_reply_enabled(
    db: AsyncSession,
    account_id: int,
    goods_id: str,
) -> Optional[bool]:
    """查商品级 auto_reply_enabled。返回 None 表示未设置（需回退到账号级）。"""
    if not goods_id:
        return None
    row = (await db.execute(
        text(
            """
            SELECT auto_reply_enabled
            FROM xianyu_goods
            WHERE account_id = :account_id
              AND deleted = 0
              AND (external_goods_id = :gid OR goods_id = :gid)
            ORDER BY id DESC
            LIMIT 1
            """
        ),
        {"account_id": account_id, "gid": goods_id},
    )).first()
    if not row or row[0] is None:
        return None
    return int(row[0]) == 1


def _coerce_message_time(value: Any) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


async def _is_late_incoming_before_latest_outbound(
    db: AsyncSession,
    *,
    account_id: int,
    s_id: str,
    incoming_message_time: int,
) -> bool:
    """Return True when a replay predates the latest seller reply in this turn.

    This is a persisted safety gate, so it works across batcher recreation and
    process restart.  We only suppress a message whose source time is shortly
    before a seller reply; a genuinely new buyer message remains eligible.
    """
    if not account_id or not s_id or incoming_message_time <= 0:
        return False

    normalized_sid = str(s_id).strip().removeprefix("sid:").removesuffix("@goofish")
    if not normalized_sid:
        return False
    sid_plain = normalized_sid
    sid_goofish = f"{normalized_sid}@goofish"
    latest_outbound = (await db.execute(
        text(
            """
            SELECT message_time
            FROM xianyu_chat_message
            WHERE account_id = :account_id
              AND deleted = 0
              AND direction = 'OUT'
              AND s_id IN (:sid_plain, :sid_goofish)
            ORDER BY message_time DESC, id DESC
            LIMIT 1
            """
        ),
        {
            "account_id": account_id,
            "sid_plain": sid_plain,
            "sid_goofish": sid_goofish,
        },
    )).scalar_one_or_none()
    latest_outbound_time = _coerce_message_time(latest_outbound)
    if latest_outbound_time <= 0 or incoming_message_time > latest_outbound_time:
        return False
    return latest_outbound_time - incoming_message_time <= _LATE_INCOMING_REPLY_GRACE_MS


async def _resolve_goods_context(
    db: AsyncSession,
    *,
    account_id: int,
    s_id: str,
    goods_id: str = "",
    item_title: str = "",
) -> dict[str, str | bool]:
    """Resolve product facts from the live conversation and local goods cache.

    A text IM event does not always include an item id, while the matching
    ``xianyu_conversation`` row already has the item binding populated during
    conversation sync.  Product details are read from the seller's local
    ``xianyu_goods`` record only; missing fields stay explicitly missing so the
    model has no reason to invent a category, stock or promise.
    """
    resolved_goods_id = str(goods_id or "").strip()
    resolved_title = str(item_title or "").strip()
    context: dict[str, str | bool] = {
        "goodsId": resolved_goods_id,
        "title": resolved_title,
        "price": "",
        "description": "",
        "detailInfo": "",
        "category": "",
        "status": "",
        "resolved": False,
    }

    normalized_sid = str(s_id or "").strip().removeprefix("sid:").removesuffix("@goofish")
    try:
        if normalized_sid:
            conversation = (await db.execute(
                text(
                    """
                    SELECT goods_id, goods_title
                    FROM xianyu_conversation
                    WHERE account_id = :account_id
                      AND (
                        REPLACE(REPLACE(COALESCE(peer_key, ''), 'sid:', ''), '@goofish', '') = :s_id
                        OR REPLACE(REPLACE(COALESCE(external_buyer_id, ''), 'sid:', ''), '@goofish', '') = :s_id
                      )
                    ORDER BY last_message_time DESC, id DESC
                    LIMIT 1
                    """
                ),
                {"account_id": account_id, "s_id": normalized_sid},
            )).mappings().first()
            if conversation:
                resolved_goods_id = str(conversation.get("goods_id") or resolved_goods_id).strip()
                resolved_title = str(conversation.get("goods_title") or resolved_title).strip()

        # Conversation sync occasionally has only the session binding while
        # individual IM packets already carry the product id.  Fall back to
        # the message table so a text packet without xyGoodsId still receives
        # the correct local product facts.
        if not resolved_goods_id and normalized_sid:
            sid_plain = normalized_sid
            sid_goofish = f"{normalized_sid}@goofish"
            message_goods = (await db.execute(
                text(
                    """
                    SELECT xy_goods_id
                    FROM xianyu_chat_message
                    WHERE account_id = :account_id
                      AND deleted = 0
                      AND xy_goods_id IS NOT NULL
                      AND xy_goods_id != ''
                      AND s_id IN (:sid_plain, :sid_goofish)
                    ORDER BY message_time DESC, id DESC
                    LIMIT 1
                    """
                ),
                {
                    "account_id": account_id,
                    "sid_plain": sid_plain,
                    "sid_goofish": sid_goofish,
                },
            )).scalar_one_or_none()
            resolved_goods_id = str(message_goods or "").strip()

        if resolved_goods_id:
            goods = (await db.execute(
                text(
                    """
                    SELECT external_goods_id, goods_id, title, price, sold_price,
                           description, detail_info, category, status
                    FROM xianyu_goods
                    WHERE account_id = :account_id
                      AND deleted = 0
                      AND (external_goods_id = :goods_id OR goods_id = :goods_id)
                    ORDER BY id DESC
                    LIMIT 1
                    """
                ),
                {"account_id": account_id, "goods_id": resolved_goods_id},
            )).mappings().first()
            if goods:
                context.update({
                    "goodsId": str(goods.get("external_goods_id") or goods.get("goods_id") or resolved_goods_id).strip(),
                    "title": str(goods.get("title") or resolved_title).strip(),
                    "price": str(goods.get("sold_price") or goods.get("price") or "").strip(),
                    "description": _trim_product_fact(goods.get("description")),
                    "detailInfo": _trim_product_fact(goods.get("detail_info")),
                    "category": str(goods.get("category") or "").strip(),
                    "status": str(goods.get("status") if goods.get("status") is not None else "").strip(),
                    "resolved": True,
                })
            else:
                context.update({"goodsId": resolved_goods_id, "title": resolved_title})
        else:
            context.update({"title": resolved_title})
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "加载 AI 客服商品上下文失败 accountId=%d errorType=%s",
            account_id,
            type(exc).__name__,
        )

    return context


def _trim_product_fact(value: Any, limit: int = 4000) -> str:
    """Keep product fields bounded and clearly treat them as reference data."""
    text_value = str(value or "").replace("\x00", "").strip()
    return text_value[:limit]


def _bounded_config_contents(
    value: Any,
    *,
    item_limit: int,
    content_limit: int,
    total_limit: int,
) -> list[str]:
    """Bound operator-configured prompt sections without mutating saved data."""
    if not isinstance(value, list):
        return []
    remaining = total_limit
    contents: list[str] = []
    for item in value[:item_limit]:
        if remaining <= 0:
            break
        raw = item.get("content") if isinstance(item, dict) else item
        content = _trim_product_fact(raw, min(content_limit, remaining))
        if not content:
            continue
        contents.append(content)
        remaining -= len(content)
    return contents


def _build_system_prompt(ai_config: dict, goods_context: dict[str, Any] | str | None) -> str:
    """Build a grounded prompt from configuration and the current product facts."""
    parts: list[str] = []

    system_prompt = _trim_product_fact(ai_config.get("systemPrompt"), limit=6_000)
    if system_prompt:
        parts.append(system_prompt)

    if isinstance(goods_context, str):
        goods_context = {"title": goods_context}
    goods_context = goods_context if isinstance(goods_context, dict) else {}
    goods_title = _trim_product_fact(goods_context.get("title"), limit=500)
    goods_id = _trim_product_fact(goods_context.get("goodsId"), limit=100)
    price = _trim_product_fact(goods_context.get("price"), limit=100)
    description = _trim_product_fact(goods_context.get("description"))
    detail_info = _trim_product_fact(goods_context.get("detailInfo"))
    category = _trim_product_fact(goods_context.get("category"), limit=200)
    status = _trim_product_fact(goods_context.get("status"), limit=50)
    fact_lines = []
    if goods_id:
        fact_lines.append(f"商品ID：{goods_id}")
    if goods_title:
        fact_lines.append(f"标题：{goods_title}")
    if price:
        fact_lines.append(f"售价：{price}")
    if category:
        fact_lines.append(f"分类：{category}")
    if status:
        fact_lines.append(f"状态：{status}")
    if description:
        fact_lines.append(f"商品描述：{description}")
    if detail_info and detail_info != description:
        fact_lines.append(f"详情文案：{detail_info}")
    if fact_lines:
        parts.append("【当前会话商品事实】\n" + "\n".join(fact_lines))
    else:
        parts.append("【当前会话商品事实】暂无可用商品详情。")

    kb_contents = _bounded_config_contents(
        ai_config.get("knowledgeBases"),
        item_limit=20,
        content_limit=4_000,
        total_limit=12_000,
    )
    if kb_contents:
        parts.append("【知识库】\n" + "\n---\n".join(kb_contents))

    rule_contents = _bounded_config_contents(
        ai_config.get("chatRules"),
        item_limit=50,
        content_limit=1_000,
        total_limit=6_000,
    )
    if rule_contents:
        parts.append("【聊天规则】\n" + "\n".join(f"- {c}" for c in rule_contents))

    persona = _trim_product_fact(ai_config.get("persona"), limit=500)
    if persona:
        parts.append(f"【人设】{persona}")

    parts.append(
        "【强制回复边界】\n"
        "只能依据上述当前会话商品事实、知识库和聊天规则回答；不得编造商品品类、库存、价格、优惠、物流或售后信息。\n"
        "不要把本商品说成泛泛的店铺商品集合，也不要用买家消息中的说法替代商品事实。\n"
        "若当前事实没有商品详情，或问题超出已知范围，明确说明需要核实，并引导买家提供具体问题或等待人工确认。"
    )

    return "\n\n".join(parts)


async def _load_history_messages(
    db: AsyncSession,
    account_id: int,
    s_id: str,
) -> list[dict[str, Any]]:
    """加载最近的历史消息（按时间正序返回，只保留文本消息）。"""
    s_id_goofish = f"{s_id}@goofish" if not s_id.endswith("@goofish") else s_id
    rows = (await db.execute(
        text(
            """
            SELECT direction, msg_content, content_type
            FROM xianyu_chat_message
            WHERE account_id = :account_id
              AND deleted = 0
              AND content_type NOT IN (32)
              AND s_id COLLATE utf8mb4_unicode_ci IN (:s_id, :s_id_goofish)
            ORDER BY message_time DESC
            LIMIT :limit
            """
        ),
        {
            "account_id": account_id,
            "s_id": s_id,
            "s_id_goofish": s_id_goofish,
            "limit": _MAX_HISTORY_MESSAGES,
        },
    )).mappings().all()

    messages: list[dict[str, Any]] = []
    for row in reversed(rows):  # 反转为正序
        content_type = int(row.get("content_type") or 1)
        if content_type != 1:
            continue  # 只传文本消息给 AI
        messages.append({
            "direction": str(row.get("direction") or "IN").upper(),
            "content": _trim_product_fact(row.get("msg_content"), limit=4_000),
        })
    return messages


async def _send_reply_message_result(
    account_id: int,
    s_id: str,
    buyer_id: str,
    reply_text: str,
) -> AiAutoReplySendResult:
    """Send once while preserving confirmed/failed/unknown ACK semantics."""
    client = ws_manager.get_client(account_id)
    if not client or not getattr(client, "is_connected", False):
        logger.warning("WebSocket 未连接，无法发送 AI 回复: accountId=%d", account_id)
        return AiAutoReplySendResult.failed("websocket_unavailable")
    if not getattr(client, "_sid", None):
        logger.warning("WebSocket 未注册（无 sid），无法发送 AI 回复: accountId=%d", account_id)
        return AiAutoReplySendResult.failed("websocket_not_registered")

    cid = s_id if s_id.endswith("@goofish") else f"{s_id}@goofish"
    to_id = buyer_id if buyer_id.endswith("@goofish") else f"{buyer_id}@goofish"

    # persist=False: the durable state machine owns local finalization.
    result = await client.send_text_message(cid=cid, to_id=to_id, text=reply_text, persist=False)
    if not isinstance(result, dict):
        return AiAutoReplySendResult.unknown("message_ack_unknown")
    try:
        code = int(result.get("code", 500))
    except (TypeError, ValueError):
        code = 500
    if code == 200:
        return AiAutoReplySendResult.confirmed()
    if code == 503 and not result.get("mid") and not result.get("deliveryUnknown"):
        return AiAutoReplySendResult.failed("websocket_unavailable")
    if code == 422 and not result.get("mid"):
        return AiAutoReplySendResult.failed("message_rejected", retry_safe=False)
    if result.get("deliveryUnknown") or result.get("mid") or code >= 500:
        return AiAutoReplySendResult.unknown("message_ack_unknown")
    return AiAutoReplySendResult.failed("message_rejected")


async def _send_reply_message(
    account_id: int,
    s_id: str,
    buyer_id: str,
    reply_text: str,
) -> bool:
    """Compatibility wrapper for callers that only need confirmed/not-confirmed."""
    result = await _send_reply_message_result(account_id, s_id, buyer_id, reply_text)
    return result.status == "confirmed"


async def _save_reply_message(
    db: AsyncSession,
    account_id: int,
    s_id: str,
    buyer_id: str,
    buyer_name: str,
    reply_text: str,
    goods_id: str,
    seller_external_uid: str,
    *,
    pnm_id: str,
) -> int | None:
    """将 AI 回复消息落库。

    开源版仅写入 xianyu_chat_message（通过 save_chat_message 复用去重 + 会话更新逻辑），
    direction=OUT 即标识为卖家/AI 发出的回复。
    """
    now_ms = int(time.time() * 1000)
    sender_id = f"{seller_external_uid}@goofish" if seller_external_uid else ""
    reply_msg = {
        # Stable local idempotency key: a crash after the INSERT but before the
        # attempt confirmation can safely execute this finalizer again.
        "pnmId": pnm_id,
        "sId": s_id,
        "contentType": 1,
        "msgContent": reply_text,
        "senderUserId": sender_id,
        "receiverUserId": buyer_id,
        "senderUserName": "我",
        "direction": "OUT",
        "messageTime": now_ms,
        "readStatus": 1,
        "xyGoodsId": goods_id,
    }

    local_message_id = await save_chat_message(
        db,
        account_id,
        reply_msg,
        seller_external_uid=seller_external_uid,
        sync_legacy_message=False,
    )

    # 更新会话的 last_auto_reply_at 时间戳（用于人工干预检测与前端展示）
    # 仅在成功落库后执行；失败时由调用方回滚事务，本次更新也会一并回滚
    try:
        normalized_sid = (
            str(s_id or "")
            .strip()
            .removeprefix("sid:")
            .removesuffix("@goofish")
        )
        if normalized_sid:
            await db.execute(text("""
                UPDATE xianyu_conversation
                SET last_auto_reply_at = :last_auto_reply_at,
                    updated_time = NOW()
                WHERE account_id = :account_id
                  AND (
                    REPLACE(REPLACE(COALESCE(peer_key, ''), 'sid:', ''), '@goofish', '') = :s_id
                    OR REPLACE(REPLACE(COALESCE(external_buyer_id, ''), 'sid:', ''), '@goofish', '') = :s_id
                  )
            """), {
                "account_id": account_id,
                "s_id": normalized_sid,
                "last_auto_reply_at": now_ms,
            })
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "更新 last_auto_reply_at 失败 accountId=%d errorType=%s",
            account_id,
            type(exc).__name__,
        )

    return local_message_id


async def insert_notification(
    db: AsyncSession,
    reference_id: Optional[int],
    title: str,
    content: str,
    notification_type: str = "system",
    priority: str = "normal",
) -> None:
    """插入系统通知。

    供 ws_delivery_handler 在自动发货失败时调用，写入代发货提醒。
    """
    priority_map = {"low": 0, "normal": 1, "warn": 2, "high": 3}
    priority_val = priority_map.get(priority, 1)

    try:
        await db.execute(
            text(
                """
                INSERT INTO notification (
                    notification_type, title, content, reference_type, reference_id,
                    is_read, priority, deleted, created_time, updated_time
                ) VALUES (
                    :notification_type, :title, :content, :reference_type, :reference_id,
                    0, :priority, 0, NOW(), NOW()
                )
                """
            ),
            {
                "notification_type": notification_type,
                "title": title,
                "content": content,
                "reference_type": notification_type,
                "reference_id": reference_id or 0,
                "priority": priority_val,
            }
        )
    except Exception as exc:
        logger.error("插入通知失败 errorType=%s", type(exc).__name__)
