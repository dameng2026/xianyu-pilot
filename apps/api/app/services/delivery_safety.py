"""Presentation of the two independent delivery phases; no secrets in summaries."""


def safety_summary(record,segments):
    states=[s["state"] for s in segments]
    confirmed=sum(state=="confirmed" for state in states)
    if states and confirmed==len(states):
        content="DELIVERED"
    elif "unknown" in states:
        content="UNKNOWN"
    elif "sending" in states:
        content="PROCESSING"
    elif "failed" in states:
        content="RETRYABLE" if any(s["state"]=="failed" and s["retry_safe"] for s in segments) else "FAILED"
    elif confirmed:
        content="PARTIAL"
    else:
        content="PENDING"
    manual=record["workflow_state"]=="needs_review" or content=="UNKNOWN" or record["confirmation_state"]=="unknown"
    status="NEED_MANUAL_CHECK" if manual else ("COMPLETED" if record["workflow_state"]=="completed" else
        "PROCESSING" if record["workflow_state"] in ("delivering","confirmation_pending") else "WAITING")
    warning=""
    if manual:
        warning="发送结果尚未确认，卡密持续绑定原订单，禁止重新入库或重发。"
        if record["confirmation_state"]=="confirmed" and content!="DELIVERED":
            warning="平台已确认发货，但内容尚未全部确认；卡密仍保留绑定，需要人工核对聊天记录。"
    return {"safetyStatus":status,"requiresManualCheck":manual,"warning":warning,
        "contentState":content,"confirmedSegments":confirmed,"totalSegments":len(states),
        "fulfillmentState":record["confirmation_state"],
        "canRetryConfirmation":record["receipt_mode"]=="platform" and content=="DELIVERED"
            and record["confirmation_state"] not in ("confirmed","sending")}
