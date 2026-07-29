import axios from 'axios'
import { clearAuth, getToken } from './auth.js'
import {
  createRequestUiContext,
  normalizeRequestUiMode,
  requestUiDetail,
  REQUEST_UI_MODES,
} from './requestUi.js'

function emit(name, detail) {
  window.dispatchEvent(new CustomEvent(name, { detail }))
}

function createRequestId() {
  const rand = Math.random().toString(36).slice(2, 10)
  return `web-${Date.now().toString(36)}-${rand}`
}

function getHeader(headers, name) {
  if (!headers) return undefined
  if (typeof headers.get === 'function') return headers.get(name)
  const key = Object.keys(headers).find(item => item.toLowerCase() === name.toLowerCase())
  return key ? headers[key] : undefined
}

function requestIdOf(config, response) {
  return getHeader(config?.headers, 'X-Request-Id')
    || response?.headers?.['x-request-id']
    || response?.data?.requestId
    || response?.data?.data?.requestId
}

function messageWithRequestId(message, requestId) {
  return requestId ? `${message}（错误编号：${requestId}）` : message
}

function createStructuredError(message, requestId, extra = {}) {
  return {
    ...extra,
    message: messageWithRequestId(message, requestId),
    requestId,
  }
}

const request = axios.create({
  baseURL: '/api',
  timeout: 30000,
  headers: { 'Content-Type': 'application/json' }
})

function semanticRetryAction(config) {
  return typeof config?.uiRetry === 'function' ? config.uiRetry : null
}

function publishRequestError(error, config) {
  if (config?.uiMode === REQUEST_UI_MODES.SILENT || config?.suppressGlobalError === true) return
  const status = Number(error?.status || 0)
  const expectedFeatureUnavailable = error?.data?.status === 'unavailable'
    && error?.data?.reason === 'commercial_bridge_not_configured'
    && error?.data?.commercialBridgeConfigured === false
  if (expectedFeatureUnavailable) return
  const globallyRelevant = error?.timeout
    || error?.code === 'NETWORK_ERROR'
    || error?.serverError
    || status >= 500
  if (!globallyRelevant) return
  const retry = semanticRetryAction(config)
  const message = retry
    ? (error.message || '请求失败，请稍后重试')
    : `${error.message || '请求失败，请稍后重试'}；可在当前页面手动刷新后重试。`
  emit('xya-request-error', requestUiDetail(config, {
    message,
    requestId: error.requestId,
    url: config?.url || '',
    canRetry: Boolean(retry),
    retry,
  }))
}

function rejectNormalized(error, config) {
  publishRequestError(error, config)
  return Promise.reject(error)
}

request.interceptors.request.use(config => {
  config.uiMode = normalizeRequestUiMode(config)
  const token = getToken()
  if (token) config.headers.Authorization = `Bearer ${token}`
  if (!getHeader(config.headers, 'X-Request-Id')) config.headers['X-Request-Id'] = createRequestId()

  if (config.data instanceof FormData) {
    delete config.headers['Content-Type']
  }

  config.xyaUiContext = createRequestUiContext(config, requestIdOf(config))
  if (config.uiMode !== REQUEST_UI_MODES.SILENT) {
    emit('xya-request-start', requestUiDetail(config, {
      requestId: requestIdOf(config),
      method: String(config.method || 'get').toUpperCase(),
      url: config.url || '',
    }))
  }
  return config
})

request.interceptors.response.use(
  response => {
    const res = response.data
    const config = response.config
    const url = config?.url || ''
    const requestId = requestIdOf(config, response)
    if (config?.uiMode !== REQUEST_UI_MODES.SILENT) {
      emit('xya-request-end', requestUiDetail(config, { requestId, url }))
    }

    if (!res || typeof res !== 'object' || !Object.prototype.hasOwnProperty.call(res, 'code')) {
      return res
    }

    if (res.code === 401) {
      clearAuth()
      emit('xya-auth-expired', { url, message: res.msg || '登录已过期，请重新登录' })
      return Promise.reject(createStructuredError(res.msg || '登录已过期，请重新登录', requestId, {
        code: 401,
        data: res.data,
        raw: res,
      }))
    }

    if (res.code === 1001) {
      emit('xya-captcha-required', res.data)
      return Promise.reject(createStructuredError(res.msg || '需要滑块验证', requestId, {
        type: 'captcha',
        data: res.data,
        raw: res,
      }))
    }

    if (res.code !== 200 && res.code !== 0) {
      const structured = createStructuredError(res.msg || '请求失败', requestId, {
        code: res.code,
        data: res.data,
        raw: res,
        status: Number(res.code) >= 500 ? Number(res.code) : undefined,
        serverError: Number(res.code) >= 500,
      })
      return rejectNormalized(structured, config)
    }

    const nested = res.data
    if (nested && typeof nested === 'object' && Object.prototype.hasOwnProperty.call(nested, 'code')) {
      const nestedCode = Number(nested.code)
      if (nestedCode !== 200 && nestedCode !== 0) {
        const structured = createStructuredError(nested.msg || nested.message || res.msg || '请求失败', requestId, {
          code: nestedCode,
          data: nested.data,
          raw: nested,
          status: nestedCode >= 500 ? nestedCode : undefined,
          serverError: nestedCode >= 500,
        })
        return rejectNormalized(structured, config)
      }
    }

    return res
  },
  async error => {
    const config = error?.config
    const status = error?.response?.status
    const requestId = requestIdOf(config, error?.response)
    const responseBody = error?.response?.data
    if (config?.uiMode !== REQUEST_UI_MODES.SILENT) {
      emit('xya-request-end', requestUiDetail(config, { requestId, url: config?.url || '' }))
    }

    if (error?.code === 'ECONNABORTED' || /timeout/i.test(error?.message || '')) {
      return rejectNormalized(createStructuredError('请求超时，请稍后重试', requestId, {
        code: 'TIMEOUT',
        status,
        timeout: true,
      }), config)
    }

    if (error?.code === 'ERR_NETWORK' || (!status && /network/i.test(error?.message || ''))) {
      return rejectNormalized(createStructuredError('网络连接失败，请检查网络或服务状态', requestId, {
        code: 'NETWORK_ERROR',
        status,
      }), config)
    }

    let body = responseBody
    if (responseBody instanceof Blob) {
      try {
        body = JSON.parse(await responseBody.text())
      } catch {
        body = null
      }
    }
    const rawMessage = body?.msg || body?.message || body?.detail || error?.message || '网络请求失败'

    if (status === 401) {
      const authError = createStructuredError(rawMessage, requestId, {
        code: 401,
        data: body?.data,
        raw: body,
        status,
      })
      clearAuth()
      emit('xya-auth-expired', { message: authError.message })
      return Promise.reject(authError)
    }

    if (status === 403) {
      return Promise.reject(createStructuredError(rawMessage || '无权限访问该资源', requestId, {
        code: 403,
        data: body?.data,
        raw: body,
        status,
        forbidden: true,
      }))
    }

    if (status >= 500) {
      return rejectNormalized(createStructuredError(rawMessage || '服务异常，请稍后重试', requestId, {
        code: status,
        data: body?.data,
        raw: body,
        status,
        serverError: true,
      }), config)
    }

    return Promise.reject(createStructuredError(rawMessage, requestId, {
      code: body?.code || status,
      data: body?.data,
      raw: body,
      status,
    }))
  }
)

export default request
