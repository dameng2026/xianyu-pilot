import request from '../utils/request'
import { pageParams } from '../utils/apiData.js'

// ─── 发货规则 ───
export function getAutoDeliveryRules(params = {}) {
  return request({ url: '/auto-delivery/rules', method: 'get', params: pageParams(params) })
}

export function createAutoDeliveryRule(data) {
  return request({ url: '/auto-delivery/rules', method: 'post', data })
}

export function updateAutoDeliveryRule(id, data) {
  if (typeof id === 'object') return request({ url: `/auto-delivery/rules/${id.id}`, method: 'put', data: id })
  return request({ url: `/auto-delivery/rules/${id}`, method: 'put', data })
}

export function deleteAutoDeliveryRule(id) {
  return request({ url: `/auto-delivery/rules/${id}`, method: 'delete' })
}

// ─── 批量操作 ───
export function batchSetDeliveryRules(data) {
  return request({ url: '/auto-delivery/rules/batch', method: 'post', data })
}

export function batchDeleteDeliveryRules(data) {
  return request({ url: '/auto-delivery/rules/batch-delete', method: 'post', data })
}

export function applyToAllGoods(data) {
  return request({ url: '/auto-delivery/rules/apply-all', method: 'post', data })
}

// ─── 商品发货配置（按商品维度） ───
export function getGoodsDeliveryConfig(goodsId) {
  return request({ url: `/auto-delivery/goods/${goodsId}/config`, method: 'get' })
}

export function getGoodsDeliveryConfigs(goodsIds) {
  return request({
    url: '/auto-delivery/goods/configs/query',
    method: 'post',
    data: { goodsIds }
  })
}

export function saveGoodsDeliveryConfig(goodsId, data) {
  return request({ url: `/auto-delivery/goods/${goodsId}/config`, method: 'put', data })
}

export function toggleGoodsDeliveryConfig(goodsId, timing, enabled) {
  return request({ url: `/auto-delivery/goods/${goodsId}/config/${timing}`, method: 'patch', data: { enabled } })
}

// ─── 发货声明 ───
export function getDeliveryStatement() {
  return request({ url: '/auto-delivery/statement', method: 'get' })
}

export function saveDeliveryStatement(data) {
  return request({ url: '/auto-delivery/statement', method: 'put', data })
}

export function toggleDeliveryStatement(enabled) {
  return request({ url: '/auto-delivery/statement/toggle', method: 'patch', data: { enabled } })
}

export function previewDeliveryStatement(data) {
  return request({ url: '/auto-delivery/statement/preview', method: 'post', data })
}

// ─── 发货声明会话（卖家手动处理） ───
export function listDeliveryStatementSessions(params = {}) {
  const current = params.current ?? params.page ?? 1
  const size = params.size ?? 20
  const query = { page: current, size }
  if (params.status) query.status = params.status
  if (params.accountId) query.accountId = params.accountId
  return request({ url: '/auto-delivery/statement/sessions', method: 'get', params: query })
}

export function confirmDeliveryStatementSession(id) {
  return request({ url: `/auto-delivery/statement/sessions/${id}/confirm`, method: 'post' })
}

export function cancelDeliveryStatementSession(id) {
  return request({ url: `/auto-delivery/statement/sessions/${id}/cancel`, method: 'post' })
}

// ─── 发货记录 ───
export function getDeliveryRecords(params = {}, config = {}) {
  return request({ ...config, url: '/auto-delivery/records', method: 'get', params: pageParams(params) })
}

export function getDeliveryRecordDetail(id) {
  return request({ url: `/auto-delivery/records/${id}`, method: 'get' })
}

export function retryDeliveryRecord(id) {
  return request({ url: `/auto-delivery/records/${id}/retry`, method: 'post' })
}

export function scheduleRedelivery(id, data) {
  return request({ url: `/auto-delivery/records/${id}/schedule-redelivery`, method: 'post', data })
}

// ─── 统计 / Dashboard ───
export function getDeliveryStats() {
  return request({ url: '/auto-delivery/stats', method: 'get' })
}

// ─── 手动触发 ───
export function triggerDelivery(orderId, timing) {
  return request({ url: `/auto-delivery/trigger`, method: 'post', data: { orderId, timing } })
}

export function scanPendingOrders() {
  return request({ url: '/auto-delivery/scan', method: 'post' })
}

// 立即触发一次自动发货补发扫描（worker 已每 10 分钟自动执行）
export function recoverPendingDeliveries() {
  return request({ url: '/auto-delivery/recover', method: 'post' })
}

// 文本货源库
export function getDeliverySources(params = {}) {
  return request({ url: '/auto-delivery/sources', method: 'get', params: pageParams(params) })
}

export function getDeliverySourceDetail(id) {
  return request({ url: `/auto-delivery/sources/${id}`, method: 'get' })
}

export function createDeliverySource(data) {
  return request({ url: '/auto-delivery/sources', method: 'post', data })
}

export function updateDeliverySource(id, data) {
  return request({ url: `/auto-delivery/sources/${id}`, method: 'put', data })
}

export function deleteDeliverySource(id) {
  return request({ url: `/auto-delivery/sources/${id}`, method: 'delete' })
}

export function getDeliverySourceGoods(id, params = {}) {
  return request({ url: `/auto-delivery/sources/${id}/goods`, method: 'get', params })
}

export function recommendDeliverySourceGoods(id, params = {}) {
  return request({ url: `/auto-delivery/sources/${id}/recommend`, method: 'post', params })
}

export function applyDeliverySourceToGoods(id, data) {
  return request({ url: `/auto-delivery/sources/${id}/apply`, method: 'post', data })
}

export function removeDeliverySourceFromGoods(sourceId, goodsId) {
  return request({ url: `/auto-delivery/sources/${sourceId}/goods/${goodsId}`, method: 'delete' })
}

// ─── 商品多规格 SKU ───
export function getGoodsSkus(goodsId) {
  return request({ url: '/goods-sku/list', method: 'post', data: { goodsId } })
}

export function getGoodsSkuRules(goodsId) {
  return request({ url: '/goods-sku/rules', method: 'get', params: { goodsId } })
}

export function saveGoodsSkuRules(goodsId, rules) {
  return request({ url: '/goods-sku/rules', method: 'put', data: { goodsId, rules } })
}
