import request from '../utils/request.js'

// 滑块求解涉及 Playwright 浏览器操作 + 多场景重试（加载转圈/点击重试/下载失败刷新），需 180 秒超时
const SOLVE_TIMEOUT = 180000

// 滑块求解是长任务（启动浏览器+拖动+二次验证），uiMode=background 避免触发全局"正在同步数据..."banner
// 局部 loading 状态由调用方自行管理（如 AccountsPage 的 captchaSolving）
const SOLVE_UI_MODE = { timeout: SOLVE_TIMEOUT, uiMode: 'background' }

export const detectCaptcha = data => request.post('/captcha/detect', data)
export const getCaptchaInstructions = data => request.post('/captcha/instructions', data)
export const autoSolveCaptcha = data => request.post('/captcha/auto-solve', data, SOLVE_UI_MODE)
export const handleCaptcha = data => request.post('/captcha/handle', data, SOLVE_UI_MODE)
export const getCaptchaRecords = (params = {}) => request.get('/captcha/records', { params })
