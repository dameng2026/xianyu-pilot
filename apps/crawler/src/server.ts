import { timingSafeEqual, randomUUID } from 'node:crypto'
import fs from 'node:fs'
import { isIP } from 'node:net'
import { pathToFileURL } from 'node:url'
import type { Server } from 'node:http'

import cors, { type CorsOptions } from 'cors'
import express, {
  type ErrorRequestHandler,
  type NextFunction,
  type Request,
  type Response,
} from 'express'
import { chromium } from 'playwright'

import { readSecretEnvironmentValue } from './fileSecret.js'
import { solveGoofishSlider, type SlideSolveOptions } from './sliderSolver.js'

const SERVICE_NAME = 'xianyu-crawler'
const NODE_ENV = String(process.env.NODE_ENV || 'development').trim().toLowerCase()
const IS_PRODUCTION = ['production', 'prod', 'staging', 'stage'].includes(NODE_ENV)
const PORT = process.env.CRAWLER_PORT
  ? readIntegerEnv('CRAWLER_PORT', 15178, 1, 65535)
  : readIntegerEnv('PORT', 15178, 1, 65535)
const HOST = String(process.env.HOST || '127.0.0.1').trim() || '127.0.0.1'
const JSON_LIMIT_BYTES = readByteSizeEnv('CRAWLER_JSON_LIMIT', 256 * 1024, 1024, 1024 * 1024)
const RATE_LIMIT_WINDOW_MS = readIntegerEnv('CRAWLER_RATE_LIMIT_WINDOW_MS', 60_000, 1_000, 3_600_000)
const RATE_LIMIT_MAX = readIntegerEnv('CRAWLER_RATE_LIMIT_MAX', 60, 1, 10_000)
const MAX_BROWSER_OPERATIONS = readIntegerEnv('CRAWLER_MAX_CONCURRENCY', 2, 1, 16)
const MAX_COOKIE_BYTES = readIntegerEnv('CRAWLER_MAX_COOKIE_BYTES', 64 * 1024, 1_024, 1024 * 1024)
const OPERATION_TIMEOUT_MS = readIntegerEnv('CRAWLER_OPERATION_TIMEOUT_MS', 180_000, 30_000, 300_000)
const FORCE_HEADLESS = readBooleanEnv('CRAWLER_FORCE_HEADLESS', IS_PRODUCTION)
const SAVE_FAILURE_SCREENSHOTS = readBooleanEnv('CRAWLER_SAVE_FAILURE_SCREENSHOTS', false)
const INTERNAL_TOKEN = readSecretEnvironmentValue('INTERNAL_API_TOKEN')
const ALLOWED_TARGET_HOSTS = parseCsv(
  process.env.CRAWLER_ALLOWED_TARGET_HOSTS || 'goofish.com,taobao.com',
).map((host) => host.replace(/^\.+/, '').toLowerCase())
const ALLOWED_ORIGINS = new Set(parseCsv(process.env.CRAWLER_ALLOWED_ORIGINS || ''))
const KNOWN_INSECURE_TOKENS = new Set([
  'changeme',
  'dev-only-internal-api-token-change-me-32-chars',
  'please-change-this-to-a-random-32-char-string',
  'replace-with-a-random-secret',
])

interface RateWindow {
  count: number
  resetAt: number
}

class HttpError extends Error {
  constructor(
    readonly statusCode: number,
    readonly code: string,
    message: string,
  ) {
    super(message)
  }
}

const app = express()
const rateWindows = new Map<string, RateWindow>()
const activeRequestControllers = new Set<AbortController>()
let activeBrowserOperations = 0
let isShuttingDown = false
let httpServer: Server | undefined
let warnedMissingDevelopmentToken = false

app.disable('x-powered-by')

app.use((req: Request, res: Response, next: NextFunction) => {
  const requestId = randomUUID()
  res.locals.requestId = requestId
  res.setHeader('X-Request-ID', requestId)
  res.setHeader('X-Content-Type-Options', 'nosniff')
  res.setHeader('Cache-Control', 'no-store')
  const startedAt = Date.now()
  res.once('finish', () => {
    if (req.path === '/health' || req.path === '/ready') return
    const matchedPath = typeof req.route?.path === 'string' ? `${req.baseUrl}${req.route.path}` : 'unmatched'
    console.log('[Server] request completed', {
      requestId,
      method: req.method,
      route: matchedPath,
      statusCode: res.statusCode,
      durationMs: Date.now() - startedAt,
    })
  })
  next()
})

const corsOptions: CorsOptions = {
  origin(origin, callback) {
    if (!origin || ALLOWED_ORIGINS.has(origin)) {
      callback(null, true)
      return
    }
    callback(new HttpError(403, 'ORIGIN_NOT_ALLOWED', 'origin is not allowed'))
  },
  methods: ['GET', 'POST'],
  allowedHeaders: ['Content-Type', 'X-Internal-Token', 'X-Request-ID'],
  credentials: false,
  maxAge: 600,
}

app.use(cors(corsOptions))
app.use(express.json({ limit: JSON_LIMIT_BYTES, strict: true, type: 'application/json' }))

app.get('/health', (_req: Request, res: Response) => {
  res.json({
    status: 'ok',
    service: SERVICE_NAME,
    uptimeSeconds: Math.floor(process.uptime()),
  })
})

app.get('/ready', (_req: Request, res: Response) => {
  const errors = getConfigurationErrors()
  if (isShuttingDown) errors.push('service is shutting down')
  if (!isBrowserExecutableAvailable()) errors.push('playwright chromium executable is unavailable')

  const ready = errors.length === 0
  res.status(ready ? 200 : 503).json({
    status: ready ? 'ready' : 'not_ready',
    service: SERVICE_NAME,
    activeBrowserOperations,
    browserCapacity: MAX_BROWSER_OPERATIONS,
    ...(ready ? {} : { errors }),
  })
})

app.use('/api', rateLimit, requireInternalToken)

const sliderHandler = async (req: Request, res: Response, next: NextFunction) => {
  let release: (() => void) | undefined
  const operation = createRequestAbortController(req, res)
  try {
    const body = requireObjectBody(req.body)
    const options = buildSlideOptions(body, operation.controller.signal)
    release = acquireBrowserSlot()

    console.log('[Server] slider solve requested', {
      requestId: res.locals.requestId,
      target: safeUrlForLog(options.targetUrl),
      headless: options.headless,
      hasCookie: Boolean(options.cookieStr),
    })
    const result = await solveGoofishSlider(options)
    res.json(serializeSlideResult(result))
  } catch (error) {
    next(error)
  } finally {
    release?.()
    operation.dispose()
  }
}

app.post('/api/solver/slider', sliderHandler)
// Compatibility with the Python captcha client shipped in this repository.
app.post('/api/goofish/slide-solve', sliderHandler)

app.use((_req: Request, res: Response) => {
  res.status(404).json({ ok: false, code: 'NOT_FOUND', error: 'route not found' })
})

const errorHandler: ErrorRequestHandler = (error, _req, res, _next) => {
  if (res.headersSent) return

  let statusCode = 500
  let code = 'INTERNAL_ERROR'
  let message = IS_PRODUCTION ? 'internal service error' : error?.message || 'internal service error'

  if (error instanceof HttpError) {
    statusCode = error.statusCode
    code = error.code
    message = error.message
  } else if (error?.type === 'entity.too.large') {
    statusCode = 413
    code = 'REQUEST_TOO_LARGE'
    message = `JSON request body exceeds ${JSON_LIMIT_BYTES} bytes`
  } else if (error instanceof SyntaxError && 'body' in error) {
    statusCode = 400
    code = 'INVALID_JSON'
    message = 'request body must be valid JSON'
  }

  if (statusCode >= 500) {
    console.error('[Server] request failed', {
      requestId: res.locals.requestId,
      code,
      errorType: error instanceof Error ? error.name : 'UnknownError',
    })
  }
  res.status(statusCode).json({ ok: false, code, error: message })
}

app.use(errorHandler)

function readIntegerEnv(name: string, fallback: number, minimum: number, maximum: number): number {
  const raw = String(process.env[name] || '').trim()
  if (!raw) return fallback
  const value = Number(raw)
  if (!Number.isInteger(value) || value < minimum || value > maximum) {
    throw new Error(`${name} must be an integer between ${minimum} and ${maximum}`)
  }
  return value
}

function readByteSizeEnv(name: string, fallback: number, minimum: number, maximum: number): number {
  const raw = String(process.env[name] || '').trim().toLowerCase()
  if (!raw) return fallback
  const match = /^(\d+)(b|kb|mb)?$/.exec(raw)
  if (!match) throw new Error(`${name} must be a byte size such as 262144, 256kb, or 1mb`)
  const unit = match[2] || 'b'
  const multiplier = unit === 'mb' ? 1024 * 1024 : unit === 'kb' ? 1024 : 1
  const value = Number(match[1]) * multiplier
  if (!Number.isSafeInteger(value) || value < minimum || value > maximum) {
    throw new Error(`${name} must resolve to between ${minimum} and ${maximum} bytes`)
  }
  return value
}

function readBooleanEnv(name: string, fallback: boolean): boolean {
  const raw = String(process.env[name] || '').trim().toLowerCase()
  if (!raw) return fallback
  if (['1', 'true', 'yes', 'on'].includes(raw)) return true
  if (['0', 'false', 'no', 'off'].includes(raw)) return false
  throw new Error(`${name} must be true or false`)
}

function parseCsv(raw: string): string[] {
  return raw.split(',').map((item) => item.trim()).filter(Boolean)
}

function getConfigurationErrors(): string[] {
  const errors: string[] = []
  if (!ALLOWED_TARGET_HOSTS.length) errors.push('CRAWLER_ALLOWED_TARGET_HOSTS must not be empty')
  for (const hostname of ALLOWED_TARGET_HOSTS) {
    if (!isSafeConfiguredHostname(hostname)) {
      errors.push(`CRAWLER_ALLOWED_TARGET_HOSTS contains an unsafe hostname: ${hostname}`)
    }
  }
  for (const origin of ALLOWED_ORIGINS) {
    try {
      const parsed = new URL(origin)
      if (!['http:', 'https:'].includes(parsed.protocol) || parsed.origin !== origin || parsed.username || parsed.password) {
        errors.push(`CRAWLER_ALLOWED_ORIGINS contains an invalid origin: ${origin}`)
      }
    } catch {
      errors.push(`CRAWLER_ALLOWED_ORIGINS contains an invalid origin: ${origin}`)
    }
  }
  if (IS_PRODUCTION) {
    if (INTERNAL_TOKEN.length < 32) errors.push('INTERNAL_API_TOKEN must contain at least 32 characters')
    if (KNOWN_INSECURE_TOKENS.has(INTERNAL_TOKEN.toLowerCase())) {
      errors.push('INTERNAL_API_TOKEN uses a documented insecure placeholder')
    }
  }
  return errors
}

function isSafeConfiguredHostname(hostname: string): boolean {
  if (hostname.length > 253 || hostname === 'localhost' || isIP(hostname)) return false
  if (!hostname.includes('.') || hostname.includes('*')) return false
  return hostname.split('.').every((label) => (
    label.length > 0
    && label.length <= 63
    && /^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$/i.test(label)
  ))
}

function isBrowserExecutableAvailable(): boolean {
  try {
    return fs.existsSync(chromium.executablePath())
  } catch {
    return false
  }
}

function secureTokenEquals(actual: string, expected: string): boolean {
  const actualBuffer = Buffer.from(actual)
  const expectedBuffer = Buffer.from(expected)
  if (actualBuffer.length !== expectedBuffer.length) return false
  return timingSafeEqual(actualBuffer, expectedBuffer)
}

function requireInternalToken(req: Request, res: Response, next: NextFunction): void {
  if (!INTERNAL_TOKEN) {
    if (IS_PRODUCTION) {
      res.status(503).json({
        ok: false,
        code: 'SERVICE_MISCONFIGURED',
        error: 'internal authentication is not configured',
      })
      return
    }
    if (!warnedMissingDevelopmentToken) {
      warnedMissingDevelopmentToken = true
      console.warn('[Server] INTERNAL_API_TOKEN is not set; development API authentication is disabled')
    }
    next()
    return
  }

  const actual = String(req.header('X-Internal-Token') || '').trim()
  if (actual && secureTokenEquals(actual, INTERNAL_TOKEN)) {
    next()
    return
  }
  res.status(401).json({ ok: false, code: 'UNAUTHORIZED', error: 'invalid internal token' })
}

function rateLimit(req: Request, res: Response, next: NextFunction): void {
  const now = Date.now()
  const key = req.socket.remoteAddress || 'unknown'
  let window = rateWindows.get(key)
  if (!window || window.resetAt <= now) {
    window = { count: 0, resetAt: now + RATE_LIMIT_WINDOW_MS }
    rateWindows.set(key, window)
  }
  window.count += 1
  const remaining = Math.max(0, RATE_LIMIT_MAX - window.count)
  res.setHeader('RateLimit-Limit', RATE_LIMIT_MAX)
  res.setHeader('RateLimit-Remaining', remaining)
  res.setHeader('RateLimit-Reset', Math.ceil(window.resetAt / 1000))
  if (window.count > RATE_LIMIT_MAX) {
    res.setHeader('Retry-After', Math.max(1, Math.ceil((window.resetAt - now) / 1000)))
    res.status(429).json({ ok: false, code: 'RATE_LIMITED', error: 'too many requests' })
    return
  }
  next()
}

function requireObjectBody(body: unknown): Record<string, unknown> {
  if (!body || typeof body !== 'object' || Array.isArray(body)) {
    throw new HttpError(400, 'INVALID_BODY', 'request body must be a JSON object')
  }
  return body as Record<string, unknown>
}

function optionalString(
  body: Record<string, unknown>,
  key: string,
  maximumLength?: number,
): string | undefined {
  const value = body[key]
  if (value === undefined || value === null || value === '') return undefined
  if (typeof value !== 'string') throw new HttpError(400, 'INVALID_INPUT', `${key} must be a string`)
  if (maximumLength !== undefined && value.length > maximumLength) {
    throw new HttpError(400, 'INVALID_INPUT', `${key} is too long`)
  }
  return value
}

function optionalBoolean(body: Record<string, unknown>, key: string): boolean | undefined {
  const value = body[key]
  if (value === undefined || value === null) return undefined
  if (typeof value !== 'boolean') throw new HttpError(400, 'INVALID_INPUT', `${key} must be a boolean`)
  return value
}

function optionalInteger(
  body: Record<string, unknown>,
  key: string,
  minimum: number,
  maximum: number,
): number | undefined {
  const value = body[key]
  if (value === undefined || value === null) return undefined
  if (!Number.isInteger(value) || Number(value) < minimum || Number(value) > maximum) {
    throw new HttpError(400, 'INVALID_INPUT', `${key} must be an integer between ${minimum} and ${maximum}`)
  }
  return Number(value)
}

function validateCookie(cookie: string | undefined): string | undefined {
  if (!cookie) return undefined
  if (Buffer.byteLength(cookie, 'utf8') > MAX_COOKIE_BYTES) {
    throw new HttpError(413, 'COOKIE_TOO_LARGE', `cookie exceeds ${MAX_COOKIE_BYTES} bytes`)
  }
  if (/[\r\n\0]/.test(cookie)) {
    throw new HttpError(400, 'INVALID_COOKIE', 'cookie contains invalid control characters')
  }
  return cookie
}

function validateTargetUrl(targetUrl: string | undefined): string | undefined {
  if (!targetUrl) return undefined
  if (targetUrl.length > 2_048) throw new HttpError(400, 'INVALID_TARGET_URL', 'targetUrl is too long')
  let parsed: URL
  try {
    parsed = new URL(targetUrl)
  } catch {
    throw new HttpError(400, 'INVALID_TARGET_URL', 'targetUrl must be a valid absolute URL')
  }
  if (parsed.protocol !== 'https:' || parsed.username || parsed.password || (parsed.port && parsed.port !== '443')) {
    throw new HttpError(400, 'INVALID_TARGET_URL', 'targetUrl must use HTTPS without credentials or a custom port')
  }
  const hostname = parsed.hostname.toLowerCase().replace(/\.$/, '')
  const allowed = ALLOWED_TARGET_HOSTS.some((allowedHost) => (
    hostname === allowedHost || hostname.endsWith(`.${allowedHost}`)
  ))
  if (!allowed) throw new HttpError(400, 'TARGET_HOST_NOT_ALLOWED', 'targetUrl host is not allowed')
  return parsed.toString()
}

function buildSlideOptions(body: Record<string, unknown>, signal: AbortSignal): SlideSolveOptions {
  const requestedHeadless = optionalBoolean(body, 'headless')
  return {
    cookieStr: validateCookie(optionalString(body, 'cookieStr') || optionalString(body, 'cookie')),
    targetUrl: validateTargetUrl(optionalString(body, 'targetUrl')),
    // 显式传入的 headless 优先（前台手动求解场景前端会传 false 让 Chrome 弹窗），
    // 未传时按 FORCE_HEADLESS 兜底（Docker/生产环境默认无头，避免无 DISPLAY 时启动失败）。
    // sliderSolver.resolveHeadlessMode 内部会再次按此规则解析。
    headless: requestedHeadless !== undefined ? requestedHeadless : (FORCE_HEADLESS ? true : undefined),
    maxRetries: optionalInteger(body, 'maxRetries', 1, 5),
    timeoutMs: optionalInteger(body, 'timeoutMs', 5_000, 60_000),
    signal,
    saveFailureScreenshot: SAVE_FAILURE_SCREENSHOTS,
  }
}

function acquireBrowserSlot(): () => void {
  if (isShuttingDown) throw new HttpError(503, 'SHUTTING_DOWN', 'service is shutting down')
  if (activeBrowserOperations >= MAX_BROWSER_OPERATIONS) {
    throw new HttpError(429, 'BROWSER_CAPACITY_EXHAUSTED', 'browser capacity is exhausted; retry later')
  }
  activeBrowserOperations += 1
  let released = false
  return () => {
    if (released) return
    released = true
    activeBrowserOperations = Math.max(0, activeBrowserOperations - 1)
  }
}

function serializeSlideResult(
  result: Awaited<ReturnType<typeof solveGoofishSlider>>,
): Record<string, unknown> {
  if (!IS_PRODUCTION || result.ok) return { ...result }
  return {
    ...result,
    error: 'slider solve operation failed',
    screenshotPath: undefined,
  }
}

function createRequestAbortController(
  req: Request,
  res: Response,
): { controller: AbortController; dispose: () => void } {
  const controller = new AbortController()
  activeRequestControllers.add(controller)
  const onAborted = () => controller.abort(new Error('client aborted request'))
  const onClose = () => {
    if (!res.writableEnded) controller.abort(new Error('client disconnected'))
  }
  req.once('aborted', onAborted)
  res.once('close', onClose)
  const timeout = setTimeout(() => {
    controller.abort(new Error('crawler operation timed out'))
  }, OPERATION_TIMEOUT_MS).unref()
  let disposed = false
  return {
    controller,
    dispose: () => {
      if (disposed) return
      disposed = true
      clearTimeout(timeout)
      req.off('aborted', onAborted)
      res.off('close', onClose)
      activeRequestControllers.delete(controller)
    },
  }
}

function safeUrlForLog(targetUrl: string | undefined): string {
  if (!targetUrl) return 'default'
  try {
    const parsed = new URL(targetUrl)
    return parsed.origin
  } catch {
    return 'invalid'
  }
}

const rateCleanupTimer = setInterval(() => {
  const now = Date.now()
  for (const [key, window] of rateWindows) {
    if (window.resetAt <= now) rateWindows.delete(key)
  }
}, RATE_LIMIT_WINDOW_MS).unref()

export function startServer(): Server {
  const configurationErrors = getConfigurationErrors()
  if (configurationErrors.length) {
    throw new Error(`Crawler configuration is invalid: ${configurationErrors.join('; ')}`)
  }
  if (httpServer) return httpServer
  httpServer = app.listen(PORT, HOST, () => {
    console.log(`[Server] ${SERVICE_NAME} listening on ${HOST}:${PORT}`)
  })
  httpServer.requestTimeout = OPERATION_TIMEOUT_MS + 10_000
  httpServer.headersTimeout = 15_000
  httpServer.keepAliveTimeout = 5_000
  httpServer.maxRequestsPerSocket = 100
  httpServer.maxHeadersCount = 100
  return httpServer
}

async function shutdown(signal: string): Promise<void> {
  if (isShuttingDown) return
  isShuttingDown = true
  console.log('[Server] graceful shutdown started', { signal })
  clearInterval(rateCleanupTimer)
  for (const controller of activeRequestControllers) {
    controller.abort(new Error('service is shutting down'))
  }
  activeRequestControllers.clear()
  const forceExit = setTimeout(() => {
    console.error('[Server] graceful shutdown timed out')
    process.exit(1)
  }, 10_000).unref()
  if (httpServer) {
    httpServer.closeIdleConnections?.()
    await new Promise<void>((resolve) => httpServer?.close(() => resolve()))
  }
  clearTimeout(forceExit)
  process.exit(0)
}

const entrypoint = process.argv[1] ? pathToFileURL(process.argv[1]).href : ''
if (entrypoint === import.meta.url) {
  try {
    startServer()
    process.once('SIGTERM', () => { void shutdown('SIGTERM') })
    process.once('SIGINT', () => { void shutdown('SIGINT') })
  } catch (error) {
    console.error('[Server] startup failed', { errorType: error instanceof Error ? error.name : 'UnknownError' })
    process.exit(1)
  }
}

export { app }

/** Test-only state hooks. They never create an HTTP route and are disabled
 * outside NODE_ENV=test. */
export const crawlerTestHarness = {
  activeBrowserOperations: () => activeBrowserOperations,
  validateTargetUrl,
  clear(): void {
    if (NODE_ENV !== 'test') throw new Error('crawlerTestHarness is only available in NODE_ENV=test')
    rateWindows.clear()
  },
}
