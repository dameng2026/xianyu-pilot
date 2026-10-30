export const stateLabels = {
  NEED_MANUAL_CHECK: '待人工核对', COMPLETED: '已完成', PROCESSING: '处理中', WAITING: '等待处理',
  DELIVERED: '全部已确认', UNKNOWN: '结果未知', PARTIAL: '部分已确认', PENDING: '待发送',
  RETRYABLE: '可安全重试', FAILED: '失败', pending: '待处理', sending: '发送中',
  confirmed: '已确认', failed: '失败', unknown: '结果未知', manual_pending: '等待人工确认',
}

export function label(state) { return stateLabels[state] || state }

export function mayRetryConfirmation(record, session) {
  return Boolean(session?.actionsAvailable && record?.receiptMode === 'platform' &&
    record?.contentState === 'DELIVERED' && record?.canRetryConfirmation &&
    !['confirmed', 'sending'].includes(record?.confirmationState))
}

export async function safetyRequest(path, options = {}) {
  const response = await fetch(path, { cache: 'no-store', ...options })
  const body = await response.json()
  if (!response.ok || body.code !== 200) throw new Error(body.msg || body.detail || '本地服务暂时不可用')
  return body.data
}
