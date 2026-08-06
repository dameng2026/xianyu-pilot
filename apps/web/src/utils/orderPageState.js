import { dateTime } from './apiData.js'

const ORDER_STATUS_META = {
  0: { text: '待付款', badge: 'orange' },
  1: { text: '已付款', badge: 'blue' },
  2: { text: '待发货', badge: 'orange' },
  3: { text: '已发货', badge: 'green' },
  4: { text: '已完成', badge: 'green' },
  5: { text: '已关闭', badge: 'red' }
}

const DELIVERY_STATUS_META = {
  pending: { text: '待处理', badge: 'orange' },
  running: { text: '执行中', badge: 'blue' },
  in_progress: { text: '执行中', badge: 'blue' },
  partial: { text: '部分完成', badge: 'orange' },
  message_sent: { text: '消息已发送，平台待确认', badge: 'orange' },
  success: { text: '已完成', badge: 'green' },
  failed: { text: '失败', badge: 'red' },
  unknown: { text: '结果待核对', badge: 'orange' }
}

const DELIVERY_METHOD_TEXT = {
  manual_text: '手动文本发货',
  manual_card: '手动卡密发货',
  auto_text: '自动文本发货',
  auto_card: '自动卡密发货'
}

function orderStatusMeta(value) {
  return ORDER_STATUS_META[Number(value)] || { text: String(value ?? '-'), badge: 'gray' }
}

function deliveryStatusMeta(value) {
  if (!value) return { text: '-', badge: 'gray' }
  return DELIVERY_STATUS_META[String(value).toLowerCase()] || { text: String(value), badge: 'gray' }
}

function buildSpecText(item) {
  if (item?.specSummary) return item.specSummary
  const parts = [item?.specName, item?.specValue].filter(Boolean)
  return parts.length === 2 ? `${parts[0]}: ${parts[1]}` : parts[0] || ''
}

function buildItemLine(item) {
  const title = item?.goodsTitle || '-'
  const count = Math.max(Number(item?.goodsCount) || 1, 1)
  const specText = buildSpecText(item)
  return `${title} x${count}${specText ? ` | ${specText}` : ''}`
}

function quantityText(order) {
  const sent = Number(order?.quantitySent ?? 0) || 0
  const requested = Number(order?.quantityRequested ?? order?.quantityTotal ?? 0) || 0
  return `${sent} / ${requested}`
}

function normalizeItemSummary(order) {
  if (order?.itemSummary) return order.itemSummary
  const items = Array.isArray(order?.items) ? order.items : []
  if (!items.length) return '查看详情'
  return items.slice(0, 2).map(buildItemLine).join(' / ')
}

export function buildOrderRowViewModel(order) {
  const orderMeta = orderStatusMeta(order?.orderStatus)
  const deliveryMeta = deliveryStatusMeta(order?.deliveryStatus)
  const quantityTotal = Number(order?.quantityTotal ?? 0) || 0
  return {
    ...order,
    createTimeText: dateTime(order?.createTime),
    payTimeText: dateTime(order?.payTime),
    shipTimeText: dateTime(order?.shipTime),
    platformSyncTimeText: dateTime(order?.platformSyncTime),
    itemSummary: normalizeItemSummary(order),
    quantityTotalText: quantityTotal > 0 ? String(quantityTotal) : '-',
    orderStatusText: orderMeta.text,
    orderStatusBadge: orderMeta.badge,
    deliveryStatusText: deliveryMeta.text,
    deliveryBadge: deliveryMeta.badge,
    deliveryProgressText: quantityText(order)
  }
}

function boolLabel(value) {
  if (value === true || value === 1 || value === '1' || value === 'true') return '是'
  if (value === false || value === 0 || value === '0' || value === 'false') return '否'
  return '-'
}

function boolBadge(value) {
  if (value === true || value === 1 || value === '1' || value === 'true') return 'green'
  if (value === false || value === 0 || value === '0' || value === 'false') return 'gray'
  return 'gray'
}

export function buildOrderDetailViewModel(order) {
  const row = buildOrderRowViewModel(order)
  const items = Array.isArray(order?.items) ? order.items : []
  return {
    ...row,
    itemLines: items.map(buildItemLine),
    deliveryMethodText: DELIVERY_METHOD_TEXT[order?.deliveryMethod] || (order?.deliveryMethod || '-'),
    deliveryFailReasonText: order?.deliveryFailReason || '-',
    itemId: order?.itemId || (items.length > 0 ? items[0]?.externalGoodsId : null) || '-',
    isBargain: order?.isBargain,
    isBargainText: boolLabel(order?.isBargain),
    isBargainBadge: boolBadge(order?.isBargain),
    isRated: order?.isRated,
    isRatedText: boolLabel(order?.isRated),
    isRatedBadge: boolBadge(order?.isRated),
    isRedFlower: order?.isRedFlower,
    isRedFlowerText: boolLabel(order?.isRedFlower),
    isRedFlowerBadge: boolBadge(order?.isRedFlower),
  }
}

export function buildManualDeliveryPayload(form) {
  const deliveryMode = String(form?.deliveryMode || 'text').trim()
  const payload = {
    deliveryMode: deliveryMode === 'card' ? 'card' : 'text',
    deliveryContent: String(form?.deliveryContent || '').trim(),
    quantityRequested: Math.max(Number(form?.quantityRequested) || 1, 1),
    idempotencyKey: String(form?.idempotencyKey || '').trim(),
    deliveryTiming: String(form?.deliveryTiming || 'after_payment').trim()
  }
  if (form?.deliverySource === 'library' && form?.sourceId) {
    payload.sourceId = Number(form.sourceId)
  }
  return payload
}

export function createManualDeliveryIdempotencyKey(cryptoApi = globalThis.crypto) {
  if (typeof cryptoApi?.randomUUID === 'function') {
    return `manual-delivery-${cryptoApi.randomUUID()}`
  }

  if (typeof cryptoApi?.getRandomValues === 'function') {
    const random = cryptoApi.getRandomValues(new Uint32Array(4))
    return `manual-delivery-${Array.from(random, value => value.toString(16).padStart(8, '0')).join('')}`
  }

  const entropy = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`
  return `manual-delivery-${entropy}`
}

export function buildManualDeliveryOutcome(data = {}) {
  const status = String(data?.status || 'unknown').toLowerCase()
  if (status === 'success') {
    return {
      status,
      tone: 'success',
      message: String(data?.message || '手动发货已完成：买家消息已发送，平台发货已确认。'),
      retryScope: null,
      retryAllowed: false,
      submitLabel: '已完成，无需重试'
    }
  }

  if (status === 'message_sent' || status === 'partial') {
    const detail = String(data?.message || '').trim()
    return {
      status: 'message_sent',
      tone: 'warning',
      message: `${detail ? `${detail}。` : ''}买家消息已发送，但平台发货确认未完成。重试将只确认平台发货，绝不会再次发送消息。`,
      retryScope: 'platform_confirm',
      retryAllowed: data?.retrySafe === true && data?.retryScope === 'platform_confirm',
      submitLabel: '仅重试平台确认'
    }
  }

  if (status === 'failed') {
    const retryAllowed = data?.retrySafe === true
    return {
      status,
      tone: 'error',
      message: String(data?.message || '手动发货失败，请核对原因后再处理。'),
      retryScope: data?.retryScope || null,
      retryAllowed,
      submitLabel: retryAllowed ? '使用原幂等键重试' : '失败，禁止重试'
    }
  }

  if (status === 'in_progress') {
    return {
      status,
      tone: 'warning',
      message: '该手动发货操作正在执行中，请勿重复提交。请稍后刷新订单状态。',
      retryScope: null,
      retryAllowed: false,
      submitLabel: '执行中，请稍后刷新'
    }
  }

  return {
    status: 'unknown',
    tone: 'warning',
    message: '执行结果未知。请先到闲鱼 App 核对买家消息和平台发货状态；为避免重复发货，当前禁止重试。',
    retryScope: null,
    retryAllowed: false,
    submitLabel: '禁止重试'
  }
}

export function buildPersistedManualDeliveryOutcome(attempt) {
  if (!attempt || typeof attempt !== 'object' || !attempt.status) return null
  const status = ['pending', 'running', 'in_progress'].includes(String(attempt.status).toLowerCase())
    ? 'in_progress'
    : attempt.status
  return buildManualDeliveryOutcome({ ...attempt, status })
}

export function buildManualDeliveryErrorOutcome(error = {}) {
  const data = error?.data
  if (data && typeof data === 'object') {
    const status = data.retryScope === 'platform_confirm'
      ? 'message_sent'
      : (data.status || 'unknown')
    return buildManualDeliveryOutcome({
      ...data,
      status,
      message: data.message || error?.message
    })
  }
  return buildManualDeliveryOutcome({ status: 'unknown', retrySafe: false })
}

export function buildOrdersQuery(query) {
  const accountId = String(query?.accountId || '').trim()
  const status = String(query?.status ?? '').trim()
  const current = Number(query?.current || 1) || 1
  const shouldSync = query?.sync !== false
  const payload = {
    accountId: accountId ? Number(accountId) : undefined,
    keyword: query?.keyword || undefined,
    status: status === '' ? undefined : Number(status),
    current,
    size: Number(query?.size || 20) || 20,
    sortField: query?.sortField || undefined,
    sortOrder: query?.sortOrder || undefined
  }
  if (accountId && shouldSync) {
    payload.sync = true
  }
  return Object.fromEntries(Object.entries(payload).filter(([, value]) => value !== undefined))
}
