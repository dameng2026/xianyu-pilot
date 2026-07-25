/**
 * 将后端返回的技术性错误信息转换为用户友好的中文提示。
 *
 * 用法：
 *   import { friendlyError } from '../utils/friendlyError.js'
 *   catch (e) { error.value = friendlyError(e) }
 *
 * 已处理的错误类型：
 * 1. 闲鱼 API 错误码（FAIL_SYS_USER_VALIDATE / RGV587_ERROR / FAIL_BIZ_* 等）
 * 2. Java 异常堆栈（包含 "Exception:" 的字符串）
 * 3. 纯英文技术错误（如 "Cannot read property..."）
 * 4. HTTP 状态码相关错误（由 request.js 已处理，这里做兜底）
 */

// 闲鱼/淘宝 API 错误码 → 友好提示
const XIANYU_ERROR_MAP = {
  'FAIL_SYS_USER_VALIDATE': '触发了闲鱼安全验证，请稍后重试或在闲鱼 App 中完成验证',
  'RGV587_ERROR': '闲鱼风控拦截，请稍后重试',
  'FAIL_SYS_SESSION_EXPIRED': '登录会话已过期，请在账号管理页重新扫码登录',
  'FAIL_BIZ_ITEM_EDIT_INVALID_MAP_LOCATION': '发布地址信息不完整，请补全省市区、GPS 和 POI 信息',
  'FAIL_BIZ_ITEM_TITLE_REQUIRED': '商品标题不能为空',
  'FAIL_BIZ_ITEM_DESC_REQUIRED': '商品描述不能为空',
  'FAIL_BIZ_ITEM_PRICE_REQUIRED': '商品价格不能为空',
  'FAIL_BIZ_ITEM_IMAGE_REQUIRED': '请至少上传一张商品图片',
  'FAIL_BIZ_USER_NOT_LOGIN': '闲鱼登录已失效，请重新扫码登录',
  'FAIL_SYS_ILLEGAL_ACCESS': '访问被拒绝，请稍后重试',
  'FAIL_SYS_PARAM_ERROR': '请求参数有误，请检查后重试',
  'no_available_account': '暂无可用账号，请稍后重试或添加新账号',
  // 滑块求解相关错误码
  'CAPTCHA_SOLVER_UNAVAILABLE': '滑块求解服务暂时不可用，请稍后重试或改为手动处理',
  'CAPTCHA_ACCOUNT_LOAD_FAILED': '读取账号信息失败，请稍后重试',
  'CAPTCHA_COOKIE_DECRYPT_FAILED': 'Cookie 解密失败，请重新扫码登录闲鱼账号后再尝试求解',
  'CAPTCHA_SOLVE_FAILED': '滑块验证未通过，请重试或改为手动处理',
  'CAPTCHA_BACKOFF_BLOCKED': '滑块求解冷却中，请稍后再试',
  // 远程滑块求解相关错误码
  'REMOTE_CONFIG_LOAD_FAILED': '远程滑块配置读取失败，请检查远程 API 配置',
  'REMOTE_CONFIG_INCOMPLETE': '远程滑块求解未配置 API 地址或密钥，请先完成配置',
  'REMOTE_SOLVER_UNAVAILABLE': '远程滑块求解服务暂时不可用，请稍后重试或改为本地求解',
  'REMOTE_CAPTCHA_SOLVE_FAILED': '远程滑块验证未通过，请重试或改为本地求解',
  // 约束7：远程滑块 errorCode 细分
  'REMOTE_SLIDER_SLIDER_FAIL': '远程滑块验证未通过，请重试或检查 Cookie 状态',
  'REMOTE_SLIDER_TIMEOUT': '远程滑块求解超时，请稍后重试',
  'REMOTE_SLIDER_PRECHECK_REJECTED': '远程滑块预检被拒，请检查 Cookie 是否有效',
  'REMOTE_SLIDER_SERVICE_UNAVAILABLE': '远程滑块求解服务暂时不可用，请稍后重试',
  'REMOTE_SLIDER_NETWORK_ERROR': '网络连接失败，请检查网络或远程 API 配置后重试',
  'REMOTE_SLIDER_INSUFFICIENT_BALANCE': '远程滑块求解余额不足，请充值后重试',
  'CAPTCHA_COOKIE_MISSING_FIELDS': 'Cookie 缺少关键字段，请重新扫码登录闲鱼账号',
}

// 关键词 → 友好提示（用于模糊匹配）
const KEYWORD_PATTERNS = [
  { pattern: /cookie.*过期|cookie.*invalid|登录已失效|token.*失效/i, message: '登录已失效，请在账号管理页点击"重新登录"获取新 Token' },
  { pattern: /database.*error|数据库连接失败|mysql.*refused|mysql.*timeout/i, message: '数据库连接失败，请检查 .env 中 MYSQL_HOST、MYSQL_PORT、MYSQL_USER、MYSQL_PASSWORD 是否正确' },
  { pattern: /redis.*refused|redis.*timeout|redis.*connect/i, message: 'Redis 连接失败，请检查 REDIS_HOST、REDIS_PASSWORD 配置；可临时关闭 Redis 使用内存缓存' },
  { pattern: /websocket.*close|websocket.*disconnect|ws.*断开|消息连接断开/i, message: '消息连接已断开，5 秒后会自动重连，如长时间未恢复请检查网络' },
  { pattern: /network|econnrefused|etimedout|econnreset/i, message: '网络连接失败，请检查网络或服务状态' },
  { pattern: /timeout|超时/i, message: '请求超时，请稍后重试' },
  { pattern: /not found|404|资源不存在/i, message: '请求的资源不存在' },
  { pattern: /forbidden|403|无权限|权限不足/i, message: '暂无权限执行此操作' },
  { pattern: /rate.?limit|限流|频繁/i, message: '操作过于频繁，请稍后再试' },
  { pattern: /server error|500|502|503|504|服务异常|系统繁忙/i, message: '服务暂时不可用，请稍后重试' },
  { pattern: /sync.*fail|同步失败|同步.*失败/i, message: '同步失败，请检查账号 Cookie 状态，或前往"账号管理"重新登录' },
]

/**
 * 判断字符串是否为纯英文技术错误（如 Java 异常、JS 错误）
 */
function isTechnicalMessage(msg) {
  if (typeof msg !== 'string') return false
  // 包含中文，认为不是技术错误
  if (/[\u4e00-\u9fa5]/.test(msg)) return false
  // 包含 Exception / Error / at xxx.yyy 堆栈特征
  if (/exception|error|stack|trace|undefined|null|cannot|failed to/i.test(msg)) return true
  // 下划线大写错误码
  if (/^[A-Z][A-Z0-9_]+$/.test(msg.trim())) return true
  return false
}

/**
 * 从错误对象中提取原始消息字符串
 */
function extractRawMessage(err) {
  if (!err) return ''
  if (typeof err === 'string') return err
  return err.message || err.msg || (typeof err === 'object' ? JSON.stringify(err) : String(err))
}

/**
 * 将错误转换为用户友好的提示
 * @param {Error|object|string} err 错误对象
 * @param {string} fallback 兜底提示文案（当无法识别错误时使用）
 * @returns {string} 友好的错误提示
 */
export function friendlyError(err, fallback = '操作失败，请稍后重试') {
  const raw = extractRawMessage(err)
  if (!raw) return fallback

  // 1. 精确匹配闲鱼错误码
  const trimmed = raw.trim()
  if (XIANYU_ERROR_MAP[trimmed]) return XIANYU_ERROR_MAP[trimmed]

  // 2. 模糊匹配关键词
  for (const { pattern, message } of KEYWORD_PATTERNS) {
    if (pattern.test(raw)) return message
  }

  // 3. 包含闲鱼错误码（如 "FAIL_SYS_USER_VALIDATE: xxx"）
  for (const code of Object.keys(XIANYU_ERROR_MAP)) {
    if (raw.includes(code)) return XIANYU_ERROR_MAP[code]
  }

  // 4. 技术性错误（纯英文、异常堆栈）→ 用兜底提示
  if (isTechnicalMessage(raw)) {
    return fallback
  }

  // 5. 已是中文友好提示，原样返回
  return raw
}
