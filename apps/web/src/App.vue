<template>
  <div v-if="booting" class="boot-screen">
    <div class="boot-card">
      <img src="/xya/brand/brand_002.png" class="boot-brand-icon" alt="XianYuAssistant" />
      <b>正在连接后端服务...</b>
      <span>{{ bootMessage }}</span>
    </div>
  </div>

  <component
    :is="pageComponent"
    v-else-if="authPages.includes(active)"
    @navigate="navigate"
    @login-success="handleLoginSuccess"
  />

  <MobileLite
    v-else-if="shouldUseMobileLite"
    @navigate="navigate"
    @logout="handleLogout"
    @force-desktop="enableMobileDesktopMode"
  />

  <div v-else class="app-shell" :class="{ 'm-nav-open': mobileNavOpen, 'is-mobile': isMobile }">
    <Sidebar :active="active" :user="currentUserInfo" :open="mobileNavOpen" @navigate="onSidebarNavigate" @close="mobileNavOpen = false" @logout="handleLogout" />
    <div v-if="isMobile && mobileNavOpen" class="m-nav-overlay" @click="mobileNavOpen = false"></div>
    <main class="main">
      <header v-if="isMobile" class="m-appbar">
        <button class="m-menu-btn" type="button" aria-label="打开菜单" @click="mobileNavOpen = true">
          <svg viewBox="0 0 24 24" class="ui-icon"><path d="M4 6h16M4 12h16M4 18h16" /></svg>
        </button>
        <img src="/xya/brand/brand_002.png" class="m-appbar-logo m-appbar-brand-icon" alt="XianYuAssistant" @click="navigate('dashboard')" />
        <button
          v-if="mobileDesktopOverride"
          class="m-return-lite"
          type="button"
          aria-label="返回移动版"
          @click="disableMobileDesktopMode"
        >
          返回移动版
        </button>
        <button class="m-appbar-user" type="button" aria-label="个人中心" @click="navigate('profile')">
          <span class="avatar avatar-img small"></span>
        </button>
      </header>
      <Topbar v-else :user="currentUserInfo" :sse-status="displaySseStatus" @logout="handleLogout" @open-profile-center="openProfileCenter" />
      <PageHeader :title="title" :subtitle="subtitle">
        <div v-if="headerActions.length" class="head-actions">
          <AppButton
            v-for="action in headerActions"
            :key="action.text"
            :type="action.type"
            :disabled="action.disabled || pendingRequests > 0 || headerActionLocks.has(action.event)"
            @click="onHeaderAction(action)"
          >
{{ action.text }}
</AppButton>
        </div>
      </PageHeader>
      <component :is="pageComponent" :active="active" :user="currentUserInfo" :requested-route="requestedRoute" @navigate="navigate" />
    </main>
  </div>
  <AppStatusCenter
    :online="isOnline"
    :notice="globalNotice"
    :loading="requestBusyVisible"
    :retrying="globalRetrying"
    @dismiss="dismissNotice"
    @retry="retryGlobalRequest"
  />
  <ConfirmModal />
  <DraftGuardModal />
</template>

<script setup>
import { computed, defineAsyncComponent, h, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import Sidebar from './components/Sidebar.vue'
import Topbar from './components/Topbar.vue'
import PageHeader from './components/PageHeader.vue'
import AppButton from './components/AppButton.vue'
import ConfirmModal from './components/ConfirmModal.vue'
import DraftGuardModal from './components/DraftGuardModal.vue'
import AppStatusCenter from './components/AppStatusCenter.vue'
import MobileLite from './components/MobileLite.vue'
import NotFoundPage from './pages/NotFoundPage.vue'
import { pageTitles } from './data/nav.js'
import { logout as logoutApi } from './api/auth.js'
import { currentUser } from './api/system.js'
import { clearAuth, getCachedUsername, getToken, isAuthed, setAuth } from './utils/auth.js'
import { confirmAction } from './utils/confirmAction.js'
import { closeSse, connectSse } from './utils/sse.js'
import { installClientErrorReporter, recordClientError } from './utils/errorReporter.js'
import { playIncomingMessageSound, primeAudioOnFirstGesture } from './utils/notifySound.js'
import { DEFAULT_PAGE, NOT_FOUND_PAGE, resolveHashRoute } from './utils/routeState.js'
import { runNavigationGuard, clearNavigationGuard } from './utils/navigationGuard.js'
import {
  createGlobalRequestNotice,
  retainGlobalNoticeForRoute,
  shouldTrackGlobalBusy,
} from './utils/requestUi.js'

const AsyncPageLoading = {
  name: 'AsyncPageLoading',
  render: () => h('div', { class: 'page-loading', role: 'status' }, '页面加载中...')
}

const AsyncPageError = {
  name: 'AsyncPageError',
  render: () => h('div', { class: 'page-load-error', role: 'alert' }, [
    h('span', '页面加载失败。'),
    h('button', { type: 'button', onClick: () => location.reload() }, '重新加载')
  ])
}

const asyncPage = loader => defineAsyncComponent({
  loader,
  loadingComponent: AsyncPageLoading,
  errorComponent: AsyncPageError,
  delay: 120,
  timeout: 30000,
  onError(_error, retry, fail, attempts) {
    if (navigator.onLine !== false && attempts < 2) retry()
    else fail()
  }
})

const LoginPage = asyncPage(() => import('./pages/LoginPage.vue'))
const DashboardPage = asyncPage(() => import('./pages/DashboardPage.vue'))
const SettingsPage = asyncPage(() => import('./pages/SettingsPage.vue'))

const pageMap = {
  login: LoginPage,
  dashboard: DashboardPage,
  data: asyncPage(() => import('./pages/DataPage.vue')),
  accounts: asyncPage(() => import('./pages/AccountsPage.vue')),
  connections: asyncPage(() => import('./pages/ConnectionsPage.vue')),
  products: asyncPage(() => import('./pages/ProductsPage.vue')),
  orders: asyncPage(() => import('./pages/OrdersPage.vue')),
  'fish-shop-edit': asyncPage(() => import('./pages/FishShopEditPage.vue')),
  rates: asyncPage(() => import('./pages/RatesPage.vue')),
  'product-publish': asyncPage(() => import('./pages/ProductPublishPage.vue')),
  messages: asyncPage(() => import('./pages/MessagesPage.vue')),
  'message-center': asyncPage(() => import('./pages/MessagesPage.vue')),
  'auto-delivery': asyncPage(() => import('./pages/AutoDeliveryPage.vue')),
  'delivery-source-library': asyncPage(() => import('./pages/DeliverySourceLibraryPage.vue')),
  'delivery-statement': asyncPage(() => import('./pages/DeliveryStatementPage.vue')),
  'card-warehouse': asyncPage(() => import('./pages/CardWarehousePage.vue')),
  'delivery-records': asyncPage(() => import('./pages/DeliveryRecordsPage.vue')),
  'scheduled-tasks': asyncPage(() => import('./pages/ScheduledTasksPage.vue')),
  'auto-reply': asyncPage(() => import('./pages/AutoReplyPage.vue')),
  'slider-solve-records': asyncPage(() => import('./pages/SliderSolveRecordsPage.vue')),
  'remote-slider-api': asyncPage(() => import('./pages/RemoteSliderApiPage.vue')),
  logs: asyncPage(() => import('./pages/LogsPage.vue')),
  feedback: asyncPage(() => import('./pages/FeedbackPage.vue')),
  'ad-application': asyncPage(() => import('./pages/AdApplicationPage.vue')),
  'settings-notify': asyncPage(() => import('./pages/settings/NotifySettings.vue')),
  profile: asyncPage(() => import('./pages/ProfileCenterPage.vue'))
}

const settingsKeys = [
  'settings-ai-cs',
  'settings-system',
  'settings-model',
  'settings-embedding',
  'settings-rag',
  'settings-product',
  'settings-about'
]
const authPages = ['login']
const defaultPage = DEFAULT_PAGE
const mobileLitePages = new Set([
  'dashboard',
  'data',
  'accounts',
  'products',
  'messages',
  'message-center',
  'profile'
])
const isKnownPage = key => Boolean(pageMap[key]) || settingsKeys.includes(key)
// 路由别名：兼容旧版 URL 和用户可能输入的常见路径
const routeAliases = {
  'automation/delivery': 'auto-delivery',
  'automation/reply': 'auto-reply',
  'settings': 'settings-system',
}
const getRoute = () => resolveHashRoute(location.hash, isKnownPage, defaultPage)
const getHash = () => getRoute().page

const initialRoute = getRoute()
const booting = ref(true)
const bootMessage = ref('正在检查登录状态')
const loggingIn = ref(false)
const active = ref(initialRoute.page)
const requestedRoute = ref(initialRoute.known ? '' : initialRoute.requestedPage)
const currentUserInfo = ref({ username: getCachedUsername() || '管理员', avatar: '/xya/chat_ui_assets/chat_ui_assets_023.png' })
const displaySseStatus = ref('disconnected')
const globalNotice = ref(null)
const globalRetrying = ref(false)
const pendingRequests = ref(0)
const requestBusyVisible = ref(false)
const headerActionLocks = ref(new Set())
const isOnline = ref(navigator.onLine !== false)
const isMobile = ref(false)
const mobileNavOpen = ref(false)
const mobileDesktopOverride = ref(readMobileDesktopOverride())
let noticeTimer = null
let requestBusyTimer = null
const pendingRequestIds = new Set()

function showNotice(text, type = 'info', retry = null, source = null) {
  globalNotice.value = { ...(source || {}), text, type, retry }
  if (noticeTimer) clearTimeout(noticeTimer)
  if (retry) return
  noticeTimer = setTimeout(() => {
    globalNotice.value = null
  }, 4500)
}

function syncRequestBusyState() {
  pendingRequests.value = pendingRequestIds.size
  if (pendingRequests.value === 0) {
    if (requestBusyTimer) clearTimeout(requestBusyTimer)
    requestBusyTimer = null
    requestBusyVisible.value = false
    return
  }
  if (!requestBusyTimer && !requestBusyVisible.value) {
    requestBusyTimer = setTimeout(() => {
      requestBusyTimer = null
      if (pendingRequestIds.size > 0) requestBusyVisible.value = true
    }, 250)
  }
}

function onRequestStart(event) {
  if (!shouldTrackGlobalBusy(event.detail)) return
  const requestId = event.detail?.requestId
  if (requestId) pendingRequestIds.add(requestId)
  syncRequestBusyState()
}

function onRequestEnd(event) {
  if (!shouldTrackGlobalBusy(event.detail)) return
  const requestId = event.detail?.requestId
  if (requestId) pendingRequestIds.delete(requestId)
  syncRequestBusyState()
}

function onRequestError(event) {
  const detail = event.detail || {}
  const notice = createGlobalRequestNotice(detail, active.value)
  if (!notice) return
  showNotice(notice.text, notice.type, notice.retry, notice)
}

async function retryGlobalRequest() {
  const requestNotice = globalNotice.value
  const retry = requestNotice?.retry
  if (typeof retry !== 'function' || globalRetrying.value || !isOnline.value) return
  globalRetrying.value = true
  try {
    await retry()
    if (requestNotice.sourceRoute && requestNotice.sourceRoute !== active.value) return
    if (globalNotice.value?.requestScope !== requestNotice.requestScope) return
    showNotice('数据请求已恢复。', 'success')
  } catch (error) {
    if (requestNotice.sourceRoute && requestNotice.sourceRoute !== active.value) return
    if (globalNotice.value?.requestScope !== requestNotice.requestScope) return
    showNotice(error?.message || '重试失败，请稍后再试', 'error', retry, requestNotice)
  } finally {
    globalRetrying.value = false
  }
}

function dismissNotice() {
  if (noticeTimer) clearTimeout(noticeTimer)
  noticeTimer = null
  globalNotice.value = null
}

function clearRequestNoticeForRoute(nextRoute) {
  if (retainGlobalNoticeForRoute(globalNotice.value, nextRoute)) return
  dismissNotice()
}

function onOffline() {
  isOnline.value = false
}

function onOnline() {
  const recovered = !isOnline.value
  isOnline.value = true
  if (recovered && ['failed', 'disconnected'].includes(displaySseStatus.value)) startSse()
  if (recovered) showNotice('网络连接已恢复，可以继续操作。', 'success')
}

async function navigate(key, options = {}) {
  if (!options.force) {
    const allowed = await runNavigationGuard()
    if (!allowed) return
  }
  const requested = key || defaultPage
  const known = isKnownPage(requested)
  const next = known ? requested : NOT_FOUND_PAGE
  if (!isAuthed() && !authPages.includes(next)) {
    location.hash = '#/login'
    active.value = 'login'
    return
  }
  requestedRoute.value = known ? '' : requested
  location.hash = `#/${encodeURIComponent(requested)}`
  active.value = next
}

function onSidebarNavigate(key) {
  mobileNavOpen.value = false
  navigate(key)
}

function openProfileCenter() {
  navigate('profile')
}

function readMobileDesktopOverride() {
  try {
    return localStorage.getItem('xya_mobile_desktop_override') === '1'
  } catch {
    return false
  }
}

function enableMobileDesktopMode() {
  mobileDesktopOverride.value = true
  try {
    localStorage.setItem('xya_mobile_desktop_override', '1')
  } catch {
    // The in-memory override still works for the current session.
  }
}

function disableMobileDesktopMode() {
  mobileDesktopOverride.value = false
  mobileNavOpen.value = false
  try {
    localStorage.removeItem('xya_mobile_desktop_override')
  } catch {
    // The current session can still return to the mobile view.
  }
  if (!mobileLitePages.has(active.value)) navigate(defaultPage)
}

async function onHeaderAction(action) {
  if (action.event && headerActionLocks.value.has(action.event)) return
  if (action.to) return navigate(action.to)
  if (action.confirm) {
    const ok = await confirmAction({
      title: action.confirm.title || `确认执行${action.text}？`,
      description: action.confirm.description || '该操作可能影响当前数据，请确认后继续。',
      confirmText: action.confirm.confirmText || '',
      dangerous: action.confirm.dangerous || false
    })
    if (!ok) return
  }
  if (action.event) {
    headerActionLocks.value = new Set([...headerActionLocks.value, action.event])
    window.dispatchEvent(new CustomEvent('xya-header-action', { detail: action.event }))
    setTimeout(() => {
      const next = new Set(headerActionLocks.value)
      next.delete(action.event)
      headerActionLocks.value = next
    }, 800)
    return
  }
  showNotice(`“${action.text}”暂未接入执行逻辑，已为你拦截空点击。`, 'warn')
}

function onHash() {
  // 别名重定向：将旧版 URL 映射到当前页面键
  const fullPath = String(location.hash || '').replace(/^#\/?/, '').split('?')[0]
  if (routeAliases[fullPath]) {
    location.hash = `#/${routeAliases[fullPath]}`
    return  // hashchange 事件会再次触发 onHash
  }
  const route = getRoute()
  const next = route.page
  if (!isAuthed() && !authPages.includes(next)) {
    navigate('login')
    return
  }
  requestedRoute.value = route.known ? '' : route.requestedPage
  active.value = next
}

watch(active, clearRequestNoticeForRoute)

async function loadCurrentUser() {
  if (!getToken()) return
  try {
    const res = await currentUser()
    currentUserInfo.value = { avatar: currentUserInfo.value.avatar, ...(res.data || {}) }
  } catch (e) {
    if (e?.code === 401) {
      window.dispatchEvent(new CustomEvent('xya-auth-expired', { detail: e }))
    }
  }
}

function startSse() {
  if (!getToken()) return
  connectSse(
    event => window.dispatchEvent(new CustomEvent('xya-sse-event', { detail: event })),
    status => { displaySseStatus.value = status }
  )
}

async function handleLoginSuccess(payload) {
  loggingIn.value = true
  if (payload?.token) setAuth(payload.token, payload.username, { remember: payload.remember })
  currentUserInfo.value = { username: payload?.username || getCachedUsername() || '管理员', avatar: '/xya/chat_ui_assets/chat_ui_assets_023.png' }
  try {
    await loadCurrentUser()
    startSse()
  } catch (e) {
    recordClientError(e, { source: 'auth_init' })
  }
  navigate(defaultPage)
  loggingIn.value = false
}

async function handleLogout() {
  try {
    await logoutApi()
  } catch {
    clearAuth()
  }
  closeSse()
  clearNavigationGuard()
  currentUserInfo.value = { username: '管理员', avatar: '/xya/chat_ui_assets/chat_ui_assets_023.png' }
  navigate('login', { force: true })
}

async function boot() {
  booting.value = true
  try {
    if (getToken()) {
      bootMessage.value = '正在恢复登录会话'
      if (authPages.includes(active.value)) active.value = defaultPage
      await loadCurrentUser()
      if (!getToken()) return
      startSse()
      if (!location.hash || authPages.includes(getHash())) {
        navigate(defaultPage)
      } else {
        onHash()  // 处理当前 hash（含别名重定向）
      }
      return
    }
    if (!location.hash) {
      navigate('login')
      return
    }

    const requested = getHash()
    if (authPages.includes(requested)) {
      active.value = requested
      return
    }

    navigate('login')
  } catch (e) {
    recordClientError(e, { source: 'app_boot' })
    active.value = 'login'
    showNotice(e.message || '后端服务连接失败，请确认服务已启动。', 'error')
  } finally {
    booting.value = false
  }
}

function onAuthExpired() {
  if (loggingIn.value) return
  clearAuth()
  closeSse()
  clearNavigationGuard()
  showNotice('登录已过期，请重新登录', 'warn')
  navigate('login', { force: true })
}

function onCaptchaRequired() {
  showNotice('当前账号需要完成人机验证后才能继续。', 'warn')
}

function onSseEventForSound(event) {
  const detail = event?.detail || {}
  const type = detail.type || detail.eventType
  const direction = String(detail.direction || '').toUpperCase()
  if (type === 'message' && direction !== 'OUT') {
    playIncomingMessageSound()
  }
}

function updateMobileState() {
  const ua = navigator.userAgent || ''
  isMobile.value = window.matchMedia?.('(max-width: 900px)').matches || /Mobi|Android|iPhone|iPad|iPod/i.test(ua)
}

onMounted(() => {
  installClientErrorReporter()
  updateMobileState()
  window.addEventListener('resize', updateMobileState)
  window.addEventListener('orientationchange', updateMobileState)
  window.addEventListener('offline', onOffline)
  window.addEventListener('online', onOnline)
  window.addEventListener('hashchange', onHash)
  window.addEventListener('xya-auth-expired', onAuthExpired)
  window.addEventListener('xya-captcha-required', onCaptchaRequired)
  window.addEventListener('xya-sse-event', onSseEventForSound)
  window.addEventListener('xya-request-start', onRequestStart)
  window.addEventListener('xya-request-end', onRequestEnd)
  window.addEventListener('xya-request-error', onRequestError)
  primeAudioOnFirstGesture()
  boot()
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', updateMobileState)
  window.removeEventListener('orientationchange', updateMobileState)
  window.removeEventListener('offline', onOffline)
  window.removeEventListener('online', onOnline)
  window.removeEventListener('hashchange', onHash)
  window.removeEventListener('xya-auth-expired', onAuthExpired)
  window.removeEventListener('xya-captcha-required', onCaptchaRequired)
  window.removeEventListener('xya-sse-event', onSseEventForSound)
  window.removeEventListener('xya-request-start', onRequestStart)
  window.removeEventListener('xya-request-end', onRequestEnd)
  window.removeEventListener('xya-request-error', onRequestError)
  if (requestBusyTimer) clearTimeout(requestBusyTimer)
  if (noticeTimer) clearTimeout(noticeTimer)
  closeSse()
})

const pageComponent = computed(() => {
  if (active.value === NOT_FOUND_PAGE) return NotFoundPage
  return settingsKeys.includes(active.value) ? SettingsPage : (pageMap[active.value] || DashboardPage)
})
const title = computed(() => (pageTitles[active.value] || pageTitles.dashboard)[0])
const subtitle = computed(() => (pageTitles[active.value] || pageTitles.dashboard)[1])
const shouldUseMobileLite = computed(() =>
  isMobile.value &&
  !mobileDesktopOverride.value &&
  !authPages.includes(active.value) &&
  mobileLitePages.has(active.value)
)

const headerActions = computed(() => {
  if (active.value === 'settings-notify') {
    return [
      { text: '保存设置', type: 'primary', event: 'notify-save' },
      { text: '测试发送', type: 'ghost', event: 'notify-test' },
      { text: '刷新日志', type: 'ghost', event: 'notify-refresh' }
    ]
  }
  if (active.value === 'settings-ai-cs') {
    return [
      { text: '重新加载', type: 'ghost', event: 'aics-reload' },
      { text: '测试 AI 回复', type: 'ghost', event: 'aics-test' },
      { text: '保存配置', type: 'primary', event: 'aics-save' }
    ]
  }
  if (active.value === 'settings-system') {
    return [
      { text: '重新加载', type: 'ghost', event: 'settings-reload' },
      { text: '保存系统配置', type: 'primary', event: 'settings-save' }
    ]
  }
  if (active.value === 'settings-model' || active.value === 'settings-embedding') {
    return [
      { text: '重新加载', type: 'ghost', event: 'settings-reload' },
      { text: '保存设置', type: 'primary', event: 'settings-save' }
    ]
  }
  if (active.value === 'settings-product') {
    return [{ text: '保存设置', type: 'primary', event: 'settings-save' }]
  }
  if (active.value === 'settings-about') {
    return [{ text: '重新加载', type: 'ghost', event: 'settings-reload' }]
  }
  if (active.value === 'settings-rag') {
    return []
  }

  const map = {
    data: [{ text: '刷新数据', type: 'ghost', event: 'refresh-data-panel' }],
    accounts: [
      { text: '扫码加账号', type: 'primary', event: 'open-scan-account' },
      { text: '手动添加', type: 'ghost', event: 'open-manual-account' },
      { text: '批量刷新', type: 'ghost', event: 'refresh-accounts' }
    ],
    connections: [
      { text: '批量连接', type: 'primary', event: 'connections-batch-start' },
      { text: '批量断开', type: 'danger', event: 'connections-batch-stop', confirm: { title: '确认批量断开连接？', description: '断开后会暂停接收这些账号的实时消息。' } }
    ],
    products: [
      { text: '同步闲鱼商品', type: 'primary', event: 'sync-products' },
      { text: '+ 发布商品', type: 'primary', to: 'product-publish' }
    ],
    orders: [{ text: '刷新订单', type: 'ghost', event: 'orders-refresh' }],
    'auto-delivery': [
      { text: '批量设置', type: 'primary', event: 'delivery-batch' },
      { text: '货源库', type: 'ghost', to: 'delivery-source-library' },
      { text: '刷新数据', type: 'ghost', event: 'delivery-refresh' }
    ],
    'delivery-source-library': [
      { text: '+ 新增货源', type: 'primary', event: 'source-new' },
      { text: '刷新列表', type: 'ghost', event: 'source-refresh' }
    ],
    'delivery-statement': [
      { text: '保存设置', type: 'primary', event: 'statement-save' },
      { text: '预览声明', type: 'ghost', event: 'statement-preview' }
    ],
    'delivery-records': [
      { text: '导出CSV', type: 'ghost', event: 'delivery-records-export' },
      { text: '刷新数据', type: 'ghost', event: 'delivery-records-refresh' }
    ],
    'scheduled-tasks': [
      { text: '立即执行', type: 'ghost', event: 'scheduled-task-run-current' },
      { text: '保存配置', type: 'ghost', event: 'scheduled-task-save' },
      { text: '+ 新建任务', type: 'primary', event: 'scheduled-task-new' }
    ],
    logs: [
      { text: '导出CSV', type: 'ghost', event: 'logs-export' },
      { text: '刷新', type: 'ghost', event: 'logs-refresh' }
    ],
    profile: [
      { text: '刷新资料', type: 'ghost', event: 'refresh-profile' }
    ]
  }
  return map[active.value] || []
})
</script>

<style scoped>
.page-loading,
.page-load-error {
  color: #526079;
  background: #fff;
  border: 1px solid #e8eef8;
  border-radius: 18px;
  padding: 28px;
  text-align: center;
  box-shadow: 0 10px 26px rgba(31, 53, 94, .08);
}

.page-load-error {
  color: #ef4444;
  background: #fff5f5;
  border-color: #ffd1d1;
  font-weight: 700;
}

.page-load-error button {
  margin-left: 12px;
  min-height: 36px;
  padding: 0 14px;
  border: 1px solid currentColor;
  border-radius: 10px;
  background: #fff;
  color: inherit;
  font-weight: 800;
}
</style>
