import request from '../utils/request'

export function getOrders(params, config = {}) {
  return request({
    ...config,
    url: '/orders',
    method: 'get',
    params
  })
}

export function getOrderDetail(id, params) {
  return request({
    url: `/orders/${id}`,
    method: 'get',
    params
  })
}

/**
 * 查询今日订单金额（仅统计 order_status IN (1,2,3,4) 且未删除订单的 total_amount 之和）。
 * accountId 为空时统计当前用户全部账号。
 * @param {number} [accountId]
 */
export function getTodayOrderAmount(accountId) {
  return request({
    url: '/orders/today-amount',
    method: 'get',
    params: { accountId: accountId || undefined }
  })
}

export function updateOrder(id, data) {
  return request({
    url: `/orders/${id}`,
    method: 'put',
    data
  })
}

export function manualDeliverOrder(id, data) {
  const payload = {
    deliveryMode: data?.deliveryMode,
    deliveryContent: data?.deliveryContent,
    quantityRequested: data?.quantityRequested,
    idempotencyKey: data?.idempotencyKey,
    deliveryTiming: data?.deliveryTiming || 'after_payment'
  }
  if (data?.sourceId) {
    payload.sourceId = Number(data.sourceId)
  }
  return request({
    url: `/orders/${id}/manual-delivery`,
    method: 'post',
    data: payload,
    timeout: 60000
  })
}

export function syncOrder(id) {
  return request({
    url: `/orders/${id}/sync`,
    method: 'post'
  })
}

export function syncOrders(data) {
  return request({
    url: '/orders/sync',
    method: 'post',
    data
  })
}


/**
 * 查询指定买家的近期订单（用于在线消息页客户订单板块）
 * @param {number} accountId 闲鱼账号 ID
 * @param {string} buyerId 买家用户 ID（不带 @goofish 后缀）
 * @param {number} size 返回条数，默认 10
 */
export function getCustomerOrders(accountId, buyerId, size = 10) {
  return request({
    url: '/orders',
    method: 'get',
    params: {
      accountId: accountId || undefined,
      buyerId: buyerId || undefined,
      current: 1,
      size
    }
  })
}
