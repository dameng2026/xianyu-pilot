from .contracts import Binding, Incoming, Query, SCENARIOS


def validate_query(query: Query) -> None:
    if not query.operation_id or len(query.operation_id) > 80:
        raise ValueError("请求编号无效")
    if not query.text.strip() or len(query.text) > 4000:
        raise ValueError("请求正文需要 1–4000 个字符")
    if query.scenario not in SCENARIOS:
        raise ValueError("未知的演示场景")


def rejection_reason(binding: Binding, query: Query, event: Incoming) -> str | None:
    if (event.account_id, event.conversation_id, event.sender_id) != (
        binding.account_id, binding.conversation_id, binding.bot_id
    ):
        return "来源账号、会话或发送者不符，已过滤"
    if event.operation_id != query.operation_id:
        return "请求编号不符，已过滤迟到或无关回复"
    if not 1 <= event.part <= event.total <= 16 or not event.body or len(event.body) > 8000:
        return "消息分段或正文无效，已过滤"
    return None
