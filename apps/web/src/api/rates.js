import request from '../utils/request.js'

// ---- 评价管理（仅鱼小铺账号可用）----
export const getRates = params => request.get('/rates', { params })
// 同步评价数据：拉取闲鱼评价列表，可能耗时较长
export const syncRates = data => request.post('/rates/sync', data, { timeout: 180000 })
export const getRateSyncStatus = params => request.get('/rates/sync-status', { params })
export const getRateOverview = params => request.get('/rates/overview', { params })
export const createRate = data => request.post('/rates/create', data, { timeout: 60000 })
export const getRateFishShopAccounts = () => request.get('/rates/fish-shop-accounts')
// ---- 自动评价 ----
export const getAutoRateLogs = params => request.get('/rates/auto-rate/logs', { params })
export const getAutoRateSchedulerStatus = () => request.get('/rates/auto-rate/scheduler-status')
export const triggerAutoRateRun = data => request.post('/rates/auto-rate/run', data, { timeout: 180000 })
