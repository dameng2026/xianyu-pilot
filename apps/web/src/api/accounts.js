import request from '../utils/request'
import { pageParams } from '../utils/apiData.js'

export function getAccounts(params = {}, config = {}) {
  return request({ ...config, url: '/xianyu/accounts', method: 'get', params: pageParams(params) })
}

// 轻量账号列表：开源版账号列表接口本身即返回轻量字段，
// 此处复用 /xianyu/accounts 以保持与商业版前端调用约定一致，避免新增后端路由。
export function getLiteAccounts(params = {}, config = {}) {
  return request({ ...config, url: '/xianyu/accounts', method: 'get', params: pageParams(params) })
}

export function createAccount(data) {
  return request({ url: '/xianyu/accounts', method: 'post', data })
}

export function createAccountByCookie(data) {
  return request({ url: '/xianyu/accounts/manual-cookie', method: 'post', data })
}

export function getAccountDetail(id, params) {
  return request({ url: `/xianyu/accounts/${id}`, method: 'get', params })
}

export function updateAccount(id, data) {
  return request({ url: `/xianyu/accounts/${id}`, method: 'put', data })
}

export function updateAccountCookie(accountId, cookie, extracted) {
  return request({
    url: `/xianyu/accounts/${accountId}/cookie`,
    method: 'post',
    data: {
      cookie,
      extractedUnb: extracted?.unb || null,
      extractedMH5Tk: extracted?.mH5Tk || null,
    },
  })
}

export function deleteAccount(id) {
  return request({ url: `/xianyu/accounts/${id}`, method: 'delete' })
}

export function getAccountSummary(params) {
  return request({ url: '/xianyu/accounts/summary', method: 'get', params })
}

export function refreshAccountProfile(id, config = {}) {
  return request({ url: `/xianyu/accounts/${id}/refresh-profile`, method: 'post', ...config })
}

export function checkAccountAuth(id) {
  return request({ url: `/xianyu/accounts/${id}/check-auth`, method: 'post' })
}

export function getAccountAutoRateConfig(id) {
  return request({ url: `/xianyu/accounts/${id}/auto-rate`, method: 'get' })
}

export function saveAccountAutoRateConfig(id, data) {
  return request({ url: `/xianyu/accounts/${id}/auto-rate`, method: 'put', data })
}

export function getAccountStrategyConfig(id) {
  return request({ url: `/xianyu/accounts/${id}/strategy-config`, method: 'get' })
}

export function saveAccountStrategyConfig(id, data) {
  return request({ url: `/xianyu/accounts/${id}/strategy-config`, method: 'put', data })
}
