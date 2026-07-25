import { reactive, readonly } from 'vue'
import { handleCaptcha } from '../api/captcha.js'

/**
 * 全局滑块求解状态管理
 *
 * 监听 SSE captcha_solve 事件，维护每个账号的求解状态。
 * 提供 isAccountSolving / getAccountSolveStatus / solveManually 方法。
 *
 * 状态字段: { status, result, reason, accountName, timestamp, recordId }
 *   status: 'retrying' | 'success' | 'fail'
 *   result: 'slider_success' | 'slider_fail' | ''
 *
 * 单租户版：与商业版逻辑一致，仅去 tenant_id 相关字段。
 */

const solveStates = reactive({})  // accountId(string) → state object

// 自动求解去重：同账号 30 秒内只自动求解一次
const autoSolveTimestamps = {}
const AUTO_SOLVE_COOLDOWN_MS = 30000

function setSolveState(accountId, payload) {
  if (!accountId) return
  const key = String(accountId)
  solveStates[key] = {
    status: payload.status || 'retrying',
    result: payload.result || '',
    reason: payload.reason || '',
    accountName: payload.accountName || solveStates[key]?.accountName || '',
    timestamp: Date.now(),
    recordId: payload.recordId || null,
  }
}

function isAccountSolving(accountId) {
  const state = solveStates[String(accountId)]
  return state?.status === 'retrying'
}

function getAccountSolveStatus(accountId) {
  return solveStates[String(accountId)] || null
}

function clearSolveStatus(accountId) {
  delete solveStates[String(accountId)]
}

/**
 * 手动触发滑块求解
 * @param {number} accountId 账号ID
 * @param {string} triggerScene 触发场景，默认 'manual'
 * @param {object} extra 额外参数 { openReason, solveReason, headless }
 *   - openReason: 开启原因（为什么打开滑块求解流程）
 *   - solveReason: 求解原因（为什么进行滑块求解）
 *   - headless: 是否无头模式。false=弹出浏览器窗口便于人工观察（前台手动求解推荐），
 *               true=无头模式，不传/undefined=沿用后端默认（无头）
 *               注意：crawler 容器 CRAWLER_FORCE_HEADLESS=true 会强制覆盖为无头
 * @returns {Promise<{success: boolean, recovered: boolean, message: string}>}
 */
async function solveManually(accountId, triggerScene = 'manual', extra = {}) {
  if (!accountId) return { success: false, recovered: false, message: '账号ID不能为空' }
  const key = String(accountId)
  const openReason = extra.openReason || ''
  const solveReason = extra.solveReason || ''

  // 标记为求解中
  solveStates[key] = {
    status: 'retrying',
    result: '',
    reason: solveReason || '手动触发滑块求解',
    accountName: solveStates[key]?.accountName || '',
    timestamp: Date.now(),
    recordId: null,
  }

  try {
    const payload = {
      accountId: Number(accountId),
      autoSolve: true,
      triggerScene,
      openReason,
      solveReason,
    }
    // 仅当显式指定 headless 时透传，避免影响自动触发场景的默认行为
    if (extra.headless !== undefined && extra.headless !== null) {
      payload.headless = Boolean(extra.headless)
    }
    const res = await handleCaptcha(payload)
    const data = res?.data || res || {}
    const recovered = Boolean(data.recovered)
    const autoSolveResult = data.autoSolveResult || {}
    const cookieVerified = autoSolveResult.cookieVerified !== false

    if (recovered) {
      solveStates[key] = {
        status: 'success',
        result: 'slider_success',
        reason: '滑块求解成功，Cookie 已恢复',
        accountName: solveStates[key]?.accountName || '',
        timestamp: Date.now(),
        recordId: null,
      }
      return { success: true, recovered: true, message: '滑块求解成功，Cookie 已恢复' }
    }
    if (autoSolveResult.solved && !cookieVerified) {
      solveStates[key] = {
        status: 'fail',
        result: 'slider_success',
        reason: '滑块已通过但 Cookie Session 已过期，需重新扫码登录',
        accountName: solveStates[key]?.accountName || '',
        timestamp: Date.now(),
        recordId: null,
      }
      return { success: false, recovered: false, message: '滑块已通过但 Cookie Session 已过期，需重新扫码登录' }
    }
    solveStates[key] = {
      status: 'fail',
      result: 'slider_fail',
      reason: autoSolveResult.error || '滑块求解失败',
      errorCode: autoSolveResult.errorCode || '',
      accountName: solveStates[key]?.accountName || '',
      timestamp: Date.now(),
      recordId: null,
    }
    return {
      success: false,
      recovered: false,
      message: autoSolveResult.error || '滑块求解失败',
      errorCode: autoSolveResult.errorCode || '',
    }
  } catch (e) {
    solveStates[key] = {
      status: 'fail',
      result: 'slider_fail',
      reason: e?.message || '滑块求解请求失败',
      accountName: solveStates[key]?.accountName || '',
      timestamp: Date.now(),
      recordId: null,
    }
    return { success: false, recovered: false, message: e?.message || '滑块求解请求失败' }
  }
}

/**
 * 自动触发滑块求解（带冷却去重）
 * 由 request.js 的 code=1001 拦截器调用
 */
async function autoSolveIfNeeded(accountId) {
  if (!accountId) return
  const key = String(accountId)
  const now = Date.now()
  const lastTs = autoSolveTimestamps[key] || 0
  if (now - lastTs < AUTO_SOLVE_COOLDOWN_MS) return  // 冷却期内跳过
  autoSolveTimestamps[key] = now
  // 后端 handle API 会写记录 + SSE 广播，前端通过 SSE 更新状态
  solveManually(accountId, 'ws_connect', {
    openReason: 'HTTP 接口返回需要滑块验证自动触发',
    solveReason: 'API 响应 code=1001，需要滑块验证',
  }).catch(() => {})
}

// ============================================================
// SSE 事件监听：监听 captcha_solve 事件更新状态
// ============================================================
function onSseCaptchaSolve(event) {
  const detail = event?.detail
  const data = detail?.payload || detail || {}
  const eventType = detail?.type || data.type || ''
  if (eventType !== 'captcha_solve') return
  const accountId = data.accountId
  if (!accountId) return
  setSolveState(accountId, {
    status: data.status,
    result: data.result,
    reason: data.reason,
    accountName: data.accountName,
    recordId: data.recordId,
  })
}

function initCaptchaSolverListener() {
  window.addEventListener('xya-sse-event', onSseCaptchaSolve)
}

function destroyCaptchaSolverListener() {
  window.removeEventListener('xya-sse-event', onSseCaptchaSolve)
}

export function useCaptchaSolver() {
  return {
    solveStates: readonly(solveStates),
    isAccountSolving,
    getAccountSolveStatus,
    clearSolveStatus,
    solveManually,
    autoSolveIfNeeded,
    initCaptchaSolverListener,
    destroyCaptchaSolverListener,
  }
}
