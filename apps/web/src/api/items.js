import request from '../utils/request.js'

export const listItems = data => request.post('/item/list', data)
// 同步触发接口：后端创建后台任务后立即返回，但 DB 检查可能耗时，给 60s 超时
export const refreshItems = data => request.post('/item/refresh', data, { timeout: 60000 })
export const publishItem = data => request.post('/item/publish', data, { timeout: 180000 })
export const itemDetail = data => request.post('/item/detail', data)
export const offShelfItem = data => request.post('/item/offShelf', data, { timeout: 60000 })
export const remoteDeleteItem = data => request.post('/item/remoteDelete', data, { timeout: 60000 })
export const batchDeleteItems = data => request.post('/item/batch/delete', data)
export const updateItemPrice = data => request.post('/item/updatePrice', data, { timeout: 120000 })
export const updateAutoReplyStatus = data => request.post('/item/updateAutoReplyStatus', data)
// 售整自动上架：手动重发（重发商品到闲鱼，成功返回新 itemId）
export const republishItem = data => request.post('/item/republish', data, { timeout: 180000 })
// 售整自动上架：切换开关（{ xianyuAccountId, itemId, enabled }）
export const toggleAutoRelist = data => request.post('/item/auto-relist/toggle', data, { timeout: 30000 })
// 进度轮询：每 2s 调用一次，用较短超时避免阻塞下一次轮询
export const getSyncProgress = syncId => request.get(`/item/syncProgress/${syncId}`, { timeout: 15000 })
export const isSyncing = accountId => request.get(`/item/syncing/${accountId}`)

// 同步任务历史：直接查 DB，同步后可能数据量大，给 60s 超时
export const getSyncTasks = params => request.get('/item/syncTasks', { params, timeout: 60000 })

// ---- 自动分类相关 ----
// 自动分类涉及图片下载+CDN上传+MTOP调用，设置较长超时（120s）
export const autoCategory = (accountId, data) => request.post(`/xianyu/accounts/${accountId}/auto-category`, data, { timeout: 120000 })
export const autoCategoryUpload = (accountId, file, title, description) => {
  const form = new FormData()
  form.append('file', file)
  if (title) form.append('title', title)
  if (description) form.append('description', description)
  return request.post(`/xianyu/accounts/${accountId}/auto-category/upload`, form, { timeout: 120000 })
}
export const getAutoCategoryConfig = () => request.get('/xianyu/accounts/auto-category/config')

// 一键擦亮：同步调用闲鱼擦亮 API，可能耗时较长（最多擦亮 50 个商品），给 120s 超时
export const polishItem = data => request.post('/item/polish', data, { timeout: 120000 })
