<template>
  <div class="dashboard-page">
    <div class="dashboard-grid">
      <div class="dashboard-main">

        <OnboardingChecklist @navigate="onOnboardingNavigate" />

        <div v-if="error" class="global-notice error">
          <span>{{ error }}</span>
          <button class="retry-btn" type="button" :disabled="reloading" @click="reloadData">{{ reloading ? '重试中...' : '重试' }}</button>
        </div>

        <CardPanel title="快速开始" desc="把最常用的入口放在这里，方便第一次进入系统时快速上手" class="dashboard-section">
          <div class="quick-start-grid">
            <button v-for="item in quickStarts" :key="item.t" type="button" class="quick-card" @click="goFeature(item)">
              <div :class="['circle-ico', item.c]"><Icon :name="item.i" /></div>
              <div class="quick-text">
                <strong>{{ item.t }}</strong>
                <span>{{ item.d }}</span>
              </div>
              <span class="card-arrow">›</span>
            </button>
          </div>
        </CardPanel>

        <CardPanel title="功能特性" desc="常用业务能力模块一览，延续设计稿中的两行卡片结构" class="dashboard-section">
          <div class="feature-grid">
            <button v-for="item in features" :key="item.t" type="button" class="feature-card" @click="goFeature(item)">
              <div :class="['circle-ico', item.c]"><Icon :name="item.i" /></div>
              <div class="feature-text">
                <strong>{{ item.t }}</strong>
                <span>{{ item.d }}</span>
                <em>{{ `点击进入 ${item.targetLabel}` }}</em>
              </div>
              <span class="card-arrow">›</span>
            </button>
          </div>
        </CardPanel>

        <CardPanel title="最近实时事件" class="dashboard-section">
          <div v-if="realtimeEvents.length === 0" class="events-empty">
            <svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="#b7c5db" stroke-width="1.5">
              <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
              <path d="m9 12 2 2 4-4" />
            </svg>
            <p>暂无实时事件，等待后端推送</p>
          </div>
          <div v-else class="events-box">
            <div v-for="(event, index) in realtimeEvents" :key="event.id || `rt-${index}`" class="event-row">
              <strong>{{ event.type || '实时事件' }}</strong>
              <span>{{ event.text }}</span>
              <em>{{ event.time || '--:--:--' }}</em>
            </div>
          </div>
        </CardPanel>
      </div>

      <aside class="dashboard-side">

        <CardPanel class="side-panel">
          <template #title>使用指南</template>
          <template #action>
            <button class="side-link" type="button" @click="emit('navigate', 'settings-about')">查看全部 ›</button>
          </template>
          <div class="guide-section">
            <h4>新手入门指南</h4>
            <p>{{ guideLeadText }}</p>
            <ol class="guide-list">
              <li v-for="item in guides" :key="item.title">
                <div class="guide-step-head">
                  <strong>{{ item.title }}</strong>
                  <em :class="['guide-step-status', item.state]">{{ item.stateText }}</em>
                </div>
                <span>{{ item.desc }}</span>
              </li>
            </ol>
            <button class="guide-doc-link" type="button" @click="openGuideDocument">前往阅读文档</button>
            <div class="guide-collapse-list">
              <div v-for="item in guideCollapsibles" :key="item.label" class="guide-collapse-block">
                <button
                  type="button"
                  class="guide-collapse-item"
                  @click="toggleCollapse(item.label)"
                >
                  <span>{{ item.label }}</span>
                  <svg :class="['collapse-chevron', { open: isGuideOpen(item.label) }]" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <polyline points="6 9 12 15 18 9" />
                  </svg>
                </button>
                <div v-if="isGuideOpen(item.label)" class="guide-collapse-panel">
                  <p>{{ item.summary }}</p>
                  <ul class="guide-collapse-points">
                    <li v-for="point in item.points" :key="point">{{ point }}</li>
                  </ul>
                  <button v-if="item.actionText" class="guide-inline-link" type="button" @click="goFeature(item)">
                    {{ item.actionText }} ›
                  </button>
                </div>
              </div>
            </div>
          </div>
        </CardPanel>

        <CardPanel class="side-panel">
          <template #title>最近通知</template>
          <template #action>
            <button class="side-link" type="button" @click="emit('navigate', 'messages')">查看全部 ›</button>
          </template>
          <div v-if="notificationsAvailable === false" class="side-empty" role="status">
            <strong>通知服务暂不可用</strong>
            <span>当前无法确认是否存在最近通知，请稍后重试。</span>
          </div>
          <div v-else-if="notifications.length === 0" class="side-empty">
            <strong>暂无通知</strong>
            <span>系统消息与业务提醒会展示在这里</span>
          </div>
          <div v-else class="side-list">
            <article v-for="(item, index) in notifications" :key="item.id || `notice-${index}`" class="notice-item">
              <div class="notice-head">
                <strong>{{ item.title }}</strong>
                <span>{{ item.time || '' }}</span>
              </div>
              <div class="notice-meta">
                <i :class="['notice-tag', `notice-tag-${item.typeClass}`]">{{ item.typeLabel }}</i>
                <b :class="['notice-state', { unread: item.isUnread }]">{{ item.isUnread ? '未读' : '已读' }}</b>
              </div>
              <p>{{ item.text }}</p>
            </article>
          </div>
        </CardPanel>

        <CardPanel title="系统状态" class="side-panel">
          <div class="status-list">
            <div v-for="item in systemStatus" :key="item.id || item.label" class="status-row" :title="item.message || ''">
              <span><i :class="['status-dot', { offline: item.ok === false, unknown: item.ok == null }]"></i>{{ item.label }}</span>
              <strong :class="item.ok === true ? 'status-ok' : (item.ok === false ? 'status-bad' : 'status-unknown')">
                {{ item.ok === true ? '正常' : (item.ok === false ? '异常' : '状态未知') }}
              </strong>
            </div>
            <div class="status-footer">
              <strong :class="statusSummaryClass">{{ statusSummaryText }}</strong>
              <span>{{ lastLoaded }}</span>
            </div>
          </div>
        </CardPanel>
      </aside>
    </div>
</div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import CardPanel from '../components/CardPanel.vue'
import Icon from '../components/Icon.vue'
import OnboardingChecklist from '../components/OnboardingChecklist.vue'
import { shortText, timeText } from '../utils/format.js'
import { getNavigationNotifications, getNavigationOverview, getNavigationSystemStatus } from '../api/navigation.js'

const emit = defineEmits(['navigate'])

function onOnboardingNavigate(routeKey) {
  emit('navigate', routeKey)
}

const error = ref('')
const reloading = ref(false)
async function reloadData() {
  reloading.value = true
  try {
    await loadData()
  } catch (e) {
    error.value = `加载失败：${e.message || '网络异常'}，请检查后重试`
  } finally {
    reloading.value = false
  }
}
const notifications = ref([])
const notificationsAvailable = ref(null)
const realtimeEvents = ref([])
const lastLoaded = ref('-')
const collapsed = reactive({})
const defaultOverview = {
  accountCount: 0,
  goodsCount: 0,
  todayOrderCount: 0,
  messageCount: 0,
  pendingCount: 0
}
const overview = ref({ ...defaultOverview })
const overviewAvailable = ref(false)
const fallbackSystemStatus = [
  { id: 'api', label: 'API服务', ok: null },
  { id: 'ws', label: 'WebSocket服务', ok: null },
  { id: 'db', label: '数据库服务', ok: null },
  { id: 'storage', label: '文件存储', ok: null }
]

const systemStatus = ref(fallbackSystemStatus.map(item => ({ ...item })))

const abnormalStatusCount = computed(() => systemStatus.value.filter(item => item.ok === false).length)
const unknownStatusCount = computed(() => systemStatus.value.filter(item => item.ok == null).length)
const statusSummaryText = computed(() => {
  if (abnormalStatusCount.value > 0) return `${abnormalStatusCount.value} 个服务需关注`
  if (unknownStatusCount.value > 0) return '服务状态待确认'
  if (error.value) return '导航数据加载异常'
  return '已探测服务均正常'
})
const statusSummaryClass = computed(() => {
  if (abnormalStatusCount.value > 0 || error.value) return 'status-error'
  if (unknownStatusCount.value > 0) return 'status-unknown'
  return 'status-success'
})
const guideLeadText = computed(() => {
  if (!overviewAvailable.value) return '导航概览暂不可用，当前无法确认账号、商品与待处理记录状态。'
  const { accountCount, goodsCount, pendingCount } = overview.value
  if (accountCount === 0) return '首次使用建议先添加店铺账号并完成授权，再继续配置商品与自动化功能。'
  if (goodsCount === 0) return `当前已接入 ${accountCount} 个账号，下一步建议完善商品信息与发布流程。`
  if (pendingCount > 0) return `当前有 ${pendingCount} 个待处理任务，建议优先跟进订单履约和自动化发货配置。`
  return `当前已接入 ${accountCount} 个账号、同步 ${goodsCount} 个商品，可以继续优化自动化和消息处理效率。`
})
const guides = computed(() => {
  if (!overviewAvailable.value) {
    return [
      {
        title: '连接店铺账号',
        desc: '当前无法确认账号接入状态，请恢复导航概览服务后重试。',
        to: 'accounts',
        state: 'unknown',
        stateText: '状态未知'
      },
      {
        title: '完善商品与订单配置',
        desc: '当前无法确认商品与待处理记录，请恢复导航概览服务后重试。',
        to: 'products',
        state: 'unknown',
        stateText: '状态未知'
      }
    ]
  }
  const { accountCount, goodsCount, messageCount, pendingCount } = overview.value
  return [
    {
      title: '连接店铺账号',
      desc: accountCount > 0
        ? `已接入 ${accountCount} 个账号，可继续检查授权与在线状态。`
        : '先添加店铺账号并完成授权，后续商品、订单与消息功能才会正常联动。',
      to: 'accounts',
      state: accountCount > 0 ? 'done' : 'todo',
      stateText: accountCount > 0 ? '已完成' : '待开始'
    },
    {
      title: '完善商品与订单配置',
      desc: goodsCount > 0
        ? pendingCount > 0
          ? `当前已同步 ${goodsCount} 个商品，另有 ${pendingCount} 个待处理任务需要跟进。`
          : `当前已同步 ${goodsCount} 个商品，可继续检查发布、库存与订单流程。`
        : '建议优先进入商品管理完善商品信息，再联动订单与自动发货配置。',
      to: goodsCount > 0 ? 'orders' : 'products',
      state: goodsCount > 0 ? (pendingCount > 0 ? 'progress' : 'done') : 'todo',
      stateText: goodsCount > 0 ? (pendingCount > 0 ? '处理中' : '已完成') : '待开始'
    },
    {
      title: '开启自动化与消息联动',
      desc: messageCount > 0
        ? `已有 ${messageCount} 条会话数据，可继续配置自动回复、通知策略和数据统计。`
        : '进入自动发货、定时任务或数据面板，逐步建立自动化处理链路。',
      to: messageCount > 0 ? 'messages' : 'auto-delivery',
      state: messageCount > 0 || pendingCount > 0 ? 'progress' : 'suggest',
      stateText: messageCount > 0 || pendingCount > 0 ? '进行中' : '建议体验'
    }
  ]
})
const guideCollapsibles = computed(() => {
  if (!overviewAvailable.value) {
    return [
      {
        label: '功能使用教程',
        summary: '导航概览暂不可用，仍可进入各模块查看其独立状态。',
        points: [
          '先检查账号管理中的授权与连接状态。',
          '再进入商品、订单和自动化页面查看各自的可用性提示。',
          '不要把当前概览中的未知状态当作零数据。'
        ],
        actionText: '检查账号状态',
        to: 'accounts'
      },
      {
        label: '状态恢复建议',
        summary: '概览恢复前，避免根据未加载的数据作运营判断。',
        points: [
          '使用页面顶部“重试”重新加载导航数据。',
          '查看右侧系统状态，区分异常、正常与未探测依赖。',
          '若持续失败，请检查 API 与数据库运行状态。'
        ],
        actionText: '查看系统设置',
        to: 'settings-about'
      }
    ]
  }
  const { accountCount, goodsCount, todayOrderCount, pendingCount } = overview.value
  return [
    {
      label: '功能使用教程',
      summary: '建议按“账号接入 → 商品管理 → 自动化配置”的顺序完成配置，上手速度会更快。',
      points: [
        accountCount > 0
          ? `账号中心当前已接入 ${accountCount} 个账号，可继续检查在线状态与授权有效期。`
          : '先进入“账号管理”添加店铺账号，完成授权后再继续后续业务操作。',
        goodsCount > 0
          ? `商品中心当前已有 ${goodsCount} 个商品，可继续编辑详情、上下架与同步信息。`
          : '进入“商品管理”发布或同步商品，准备后续订单、卡密和自动化流程。',
        '最后进入“自动化发货”或“定时任务”，为重复业务建立稳定规则。'
      ],
      actionText: accountCount > 0 ? '继续管理账号' : '立即添加账号',
      to: 'accounts'
    },
    {
      label: '最佳实践案例',
      summary: '推荐把导航面板作为每天登录后的第一站，先处理提醒，再进入具体模块。',
      points: [
        pendingCount > 0
          ? `当前有 ${pendingCount} 个待处理任务，建议优先进入订单或自动发货模块。`
          : '先查看最近实时事件和最近通知，确认系统是否有新订单、消息或异常提醒。',
        todayOrderCount > 0
          ? `今日已产生 ${todayOrderCount} 笔订单，建议同步跟进履约状态与发货进度。`
          : '若今日订单较少，可优先完善商品资料、自动回复和定时任务规则。',
        '完成日常检查后，再到数据面板观察成交、消息和服务表现。'
      ],
      actionText: '进入定时任务',
      to: 'scheduled-tasks'
    },
    {
      label: '常见问题解答',
      summary: '如果模块没有数据或入口不可用，通常可以先检查以下几个基础项。',
      points: [
        accountCount > 0
          ? '账号已接入但业务数据为空时，先检查店铺授权是否失效或连接是否中断。'
          : '尚未接入账号时，部分商品、订单与消息模块不会展示实时数据。',
        notifications.value.length > 0
          ? '最近通知中已有系统消息，可优先查看提醒内容定位异常来源。'
          : '如果最近通知为空，说明近期没有新的系统消息或业务提醒。',
        error.value
          ? '当前检测到导航数据加载异常，建议稍后刷新页面或检查后端服务状态。'
          : '已探测服务正常时，可继续检查网络、SSE 连接或各模块筛选条件。'
      ],
      actionText: '查看系统设置',
      to: 'settings-about'
    }
  ]
})

function toArray(data) {
  if (Array.isArray(data)) return data
  if (Array.isArray(data?.records)) return data.records
  if (Array.isArray(data?.list)) return data.list
  return []
}

function formatNoticeTime(value) {
  const text = timeText(value)
  if (!text || text === '-') return ''
  const normalized = String(text).replace('T', ' ')
  if (!normalized.includes(' ')) return normalized
  const [date, clock = ''] = normalized.split(' ')
  return `${date.slice(5)} ${clock.slice(0, 5)}`
}

function formatEventTime(value) {
  const text = timeText(value)
  if (!text || text === '-') return '--:--:--'
  const normalized = String(text).replace('T', ' ')
  return normalized.includes(' ') ? normalized.split(' ').pop().slice(0, 8) : normalized.slice(-8)
}

function mapNoticeType(type) {
  switch (String(type || '').toLowerCase()) {
    case 'system':
      return { label: '系统', className: 'system' }
    case 'warning':
      return { label: '预警', className: 'warning' }
    case 'info':
      return { label: '通知', className: 'info' }
    default:
      return { label: '消息', className: 'info' }
  }
}

function normalizeNotification(item, index) {
  const type = mapNoticeType(item?.type)
  return {
    id: item?.id || `notice-${index}`,
    title: item?.title || type.label,
    text: shortText(item?.content || item?.message || '-', 72),
    time: formatNoticeTime(item?.createdTime || item?.time || item?.createdAt),
    typeLabel: type.label,
    typeClass: type.className,
    isUnread: Number(item?.status ?? 0) === 0
  }
}

function normalizeSystemStatus(item, index) {
  const rawStatus = item?.status
  const ok = rawStatus === null || rawStatus === undefined || rawStatus === ''
    ? null
    : Number(rawStatus) === 1
  return {
    id: item?.id || `status-${index}`,
    label: item?.nodeName || fallbackSystemStatus[index]?.label || `服务节点 ${index + 1}`,
    ok,
    message: item?.message || ''
  }
}

function pushRealtimeEvent(eventItem) {
  if (!eventItem?.text) return
  const current = realtimeEvents.value.filter(item => item?.id !== eventItem.id)
  realtimeEvents.value = [eventItem, ...current].slice(0, 5)
}

function buildRealtimeSeedEvents() {
  const seedTime = new Date().toLocaleTimeString('zh-CN', { hour12: false })
  const seeded = []
  const { accountCount, goodsCount, todayOrderCount, pendingCount } = overview.value
  if (accountCount > 0 || goodsCount > 0 || todayOrderCount > 0) {
    seeded.push({
      id: 'seed-overview',
      type: '数据同步',
      text: `已同步 ${accountCount} 个账号、${goodsCount} 个商品，今日订单 ${todayOrderCount} 笔。`,
      time: seedTime
    })
  }
  if (pendingCount > 0) {
    seeded.push({
      id: 'seed-pending',
      type: '待处理提醒',
      text: `当前有 ${pendingCount} 个待处理任务，建议优先跟进订单与发货流程。`,
      time: seedTime
    })
  }
  if (unknownStatusCount.value === 0) {
    seeded.push({
      id: 'seed-status',
      type: abnormalStatusCount.value > 0 ? '服务预警' : '服务状态',
      text: abnormalStatusCount.value > 0
        ? `${abnormalStatusCount.value} 个服务状态异常，请留意右侧系统状态面板。`
        : '状态接口确认已探测的服务均正常。',
      time: seedTime
    })
  }
  if (notifications.value.length > 0) {
    const latestNotice = notifications.value[0]
    seeded.push({
      id: `seed-notice-${latestNotice.id}`,
      type: '通知同步',
      text: `最近通知：${shortText(latestNotice.title || latestNotice.text, 30)}`,
      time: latestNotice.time || seedTime
    })
  }
  return seeded.slice(0, 5)
}

function ensureRealtimeSeedEvents() {
  const seeded = buildRealtimeSeedEvents()
  if (seeded.length === 0) return
  if (realtimeEvents.value.length === 0) {
    realtimeEvents.value = seeded
    return
  }
  const merged = [...realtimeEvents.value]
  for (const item of seeded) {
    if (merged.length >= 5) break
    if (!merged.some(eventItem => eventItem.id === item.id)) {
      merged.push(item)
    }
  }
  realtimeEvents.value = merged.slice(0, 5)
}

async function loadData() {
  error.value = ''
  const failures = []
  const [overviewRes, notificationsRes, systemStatusRes] = await Promise.allSettled([
    getNavigationOverview(),
    getNavigationNotifications({ limit: 5 }),
    getNavigationSystemStatus()
  ])

  if (overviewRes.status === 'fulfilled' && overviewRes.value?.data) {
    overviewAvailable.value = true
    overview.value = {
      ...defaultOverview,
      ...overviewRes.value.data
    }
  } else {
    overviewAvailable.value = false
    overview.value = { ...defaultOverview }
    failures.push('overview')
  }

  if (notificationsRes.status === 'fulfilled') {
    notificationsAvailable.value = true
    notifications.value = toArray(notificationsRes.value?.data)
      .slice(0, 5)
      .map((item, index) => normalizeNotification(item, index))
  } else {
    notificationsAvailable.value = false
    notifications.value = []
    failures.push('notifications')
  }

  if (systemStatusRes.status === 'fulfilled') {
    const list = toArray(systemStatusRes.value?.data).map((item, index) => normalizeSystemStatus(item, index))
    systemStatus.value = list.length ? list : fallbackSystemStatus.map(item => ({ ...item }))
  } else {
    systemStatus.value = fallbackSystemStatus.map(item => ({ ...item }))
    failures.push('system-status')
  }

  if (failures.length > 0) {
    error.value = '部分导航数据加载失败'
  }
  ensureRealtimeSeedEvents()
  lastLoaded.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
}

function onSse(event) {
  const detail = event.detail || {}
  pushRealtimeEvent({
    id: detail.id ? `sse-${detail.id}` : `sse-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    type: detail.title || detail.name || formatRealtimeType(detail.type || detail.event),
    text: shortText(detail.message || detail.content || detail.description || JSON.stringify(detail), 80),
    time: formatEventTime(detail.createdTime || detail.time || detail.timestamp || Date.now())
  })
}

function toggleCollapse(label) {
  const next = !isGuideOpen(label)
  guideCollapsibles.value.forEach(item => {
    collapsed[item.label] = false
  })
  collapsed[label] = next
}

function isGuideOpen(label) {
  const value = collapsed[label]
  if (value === undefined) return label === guideCollapsibles.value[0]?.label
  return value
}

function formatRealtimeType(type) {
  switch (String(type || '').toLowerCase()) {
    case 'order':
      return '订单事件'
    case 'message':
      return '消息事件'
    case 'warning':
      return '预警事件'
    case 'workflow':
      return '工作流事件'
    case 'delivery':
      return '发货事件'
    case 'system':
      return '系统事件'
    default:
      return '实时事件'
  }
}

function openGuideDocument() {
  emit('navigate', 'settings-about')
}

const quickStarts = [
  { t: '添加账号', d: '添加店铺账号，开始管理您的店铺', i: 'users', c: 'blue-bg', to: 'accounts' },
  { t: 'WebSocket连接', d: '建立实时连接，接收消息和数据', i: 'data', c: 'purple-bg', to: 'accounts' },
  { t: '商品管理', d: '发布管理商品，优化商品信息', i: 'product', c: 'green-bg', to: 'products' },
  { t: '自动化发货', d: '设置发货规则并查看实际执行结果', i: 'truck', c: 'orange-bg', to: 'auto-delivery' }
]

const features = [
  { t: '多账号管理', d: '集中查看账号、授权与连接状态', i: 'users', c: 'purple-bg', to: 'accounts', targetLabel: '管理账号' },
  { t: '商品同步', d: '按需同步商品；发布与改价结果以平台确认为准', i: 'product', c: 'green-bg', to: 'products', targetLabel: '商品管理' },
  { t: '订单管理', d: '按需同步订单并查看后端实际记录状态', i: 'order', c: 'blue-bg', to: 'orders', targetLabel: '订单管理' },
  { t: '自动发货', d: '按已配置规则处理发货，异常与未知结果需人工复核', i: 'truck', c: 'orange-bg', to: 'auto-delivery', targetLabel: '自动化' },
  { t: '系统设置', d: '集中管理通用模型、向量模型、RAG 知识库配置。', i: 'settings', c: 'cyan-bg', to: 'settings-system', targetLabel: '系统配置' },
  { t: '数据统计', d: '查看订单、发货与自动回复的实际汇总', i: 'data', c: 'blue-bg', to: 'data', targetLabel: '数据面板' }
]

function goFeature(item) {
  if (item?.to) emit('navigate', item.to)
}

onMounted(() => {
  window.addEventListener('xya-sse-event', onSse)
  loadData()
})

onBeforeUnmount(() => {
  window.removeEventListener('xya-sse-event', onSse)
})
</script>

<style scoped>
.dashboard-page {
  max-width: 100%;
}

.dashboard-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 368px;
  gap: 20px;
  align-items: start;
}

.dashboard-main {
  min-width: 0;
}

.dashboard-side {
  position: sticky;
  top: 88px;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.dashboard-section {
  margin-top: 18px;
  border-radius: 18px;
  padding: 20px;
  box-shadow: 0 16px 40px rgba(32, 68, 132, 0.05);
}















.quick-start-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 16px;
}

.feature-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 16px;
}

.quick-card,
.feature-card {
  min-height: 96px;
  border: 1px solid #edf2fb;
  border-radius: 18px;
  background: #fff;
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 18px 16px;
  text-align: left;
  transition: transform .18s ease, box-shadow .18s ease, border-color .18s ease;
}

.quick-card:hover,
.feature-card:hover {
  transform: translateY(-1px);
  border-color: #c9dcff;
  box-shadow: 0 14px 30px rgba(37, 106, 214, 0.08);
}

.quick-text,
.feature-text {
  min-width: 0;
  flex: 1;
}

.quick-text strong,
.feature-text strong {
  display: block;
  color: #16233d;
  font-size: 15px;
}

.quick-text span,
.feature-text span {
  display: block;
  margin-top: 4px;
  color: #7a8aa5;
  font-size: 12px;
  line-height: 1.65;
}

.feature-text em {
  display: block;
  margin-top: 6px;
  color: #0d6bff;
  font-style: normal;
  font-size: 12px;
  font-weight: 700;
}

.quick-card .circle-ico,
.feature-card .circle-ico {
  width: 44px;
  height: 44px;
  flex: 0 0 44px;
  font-size: 20px;
}

.card-arrow {
  color: #bdc9db;
  font-size: 20px;
  font-weight: 700;
}

.events-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  min-height: 94px;
  color: #8ba0bf;
}

.events-empty p {
  margin: 0;
}

.events-box {
  border: 1px solid #eef3fb;
  border-radius: 14px;
  overflow: hidden;
}

.event-row {
  display: grid;
  grid-template-columns: 120px minmax(0, 1fr) 120px;
  gap: 10px;
  align-items: center;
  min-height: 56px;
  padding: 0 18px;
  font-size: 13px;
}

.event-row + .event-row {
  border-top: 1px solid #f1f5fa;
}

.event-row strong {
  color: #314666;
}

.event-row span {
  color: #6e7f9b;
}

.event-row em {
  color: #9aa7bb;
  font-style: normal;
  text-align: right;
}

.side-panel {
  border-radius: 18px;
  padding: 20px 18px;
  box-shadow: 0 16px 40px rgba(32, 68, 132, 0.06);
}

.side-link {
  border: 0;
  padding: 0;
  background: transparent;
  color: #2f74f6;
  font-size: 12px;
  font-weight: 700;
}

.side-link:disabled {
  color: #94a3b8;
  cursor: not-allowed;
}












.guide-section h4 {
  margin: 0;
  color: #192742;
  font-size: 15px;
}

.guide-section p {
  margin: 10px 0 0;
  color: #6d7f9d;
  font-size: 13px;
  line-height: 1.75;
}

.guide-list {
  margin: 12px 0 0;
  padding: 0 0 0 18px;
  color: #536682;
}

.guide-list li + li {
  margin-top: 8px;
}

.guide-step-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.guide-list strong {
  display: block;
  color: #1d2d4b;
}

.guide-list span {
  display: block;
  margin-top: 4px;
  color: #6d7f9d;
}

.guide-step-status {
  flex: 0 0 auto;
  height: 22px;
  padding: 0 10px;
  border-radius: 999px;
  font-style: normal;
  font-size: 11px;
  font-weight: 800;
}

.guide-step-status.done {
  background: #e9f8f1;
  color: #179866;
}

.guide-step-status.progress {
  background: #fff4e5;
  color: #d97706;
}

.guide-step-status.todo,
.guide-step-status.suggest {
  background: #edf4ff;
  color: #2767e7;
}

.guide-doc-link {
  margin-top: 12px;
  border: 0;
  padding: 0;
  background: transparent;
  color: #0d6bff;
  font-size: 13px;
  font-weight: 800;
}

.guide-collapse-list {
  margin-top: 12px;
  border-top: 1px solid #eef2f8;
}

.guide-collapse-block + .guide-collapse-block {
  border-top: 1px solid #eef2f8;
}

.guide-collapse-item {
  width: 100%;
  min-height: 44px;
  border: 0;
  background: transparent;
  display: flex;
  align-items: center;
  justify-content: space-between;
  color: #2c3d59;
  font-size: 13px;
  font-weight: 700;
}

.collapse-chevron {
  color: #98a7bc;
  transition: transform .18s ease;
}

.collapse-chevron.open {
  transform: rotate(180deg);
}

.guide-collapse-panel {
  padding: 0 0 14px;
}

.guide-collapse-panel p {
  margin: 0;
  color: #6d7f9d;
  font-size: 12px;
  line-height: 1.8;
}

.guide-collapse-points {
  margin: 10px 0 0;
  padding-left: 18px;
  color: #5b6d88;
  font-size: 12px;
  line-height: 1.75;
}

.guide-collapse-points li + li {
  margin-top: 6px;
}

.guide-inline-link {
  margin-top: 10px;
  border: 0;
  padding: 0;
  background: transparent;
  color: #0d6bff;
  font-size: 12px;
  font-weight: 800;
}

.side-empty {
  padding: 12px 0;
  color: #93a2b7;
  font-size: 13px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.side-empty strong {
  color: #445874;
}

.side-empty span {
  line-height: 1.7;
}

.side-list {
  display: flex;
  flex-direction: column;
}

.notice-item + .notice-item {
  border-top: 1px solid #eef2f8;
}

.notice-item {
  padding: 12px 0;
}

.notice-head {
  display: flex;
  justify-content: space-between;
  gap: 10px;
}

.notice-head strong {
  color: #1c2a44;
  font-size: 13px;
}

.notice-head span {
  color: #99a8bd;
  font-size: 11px;
}

.notice-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
}

.notice-tag {
  display: inline-flex;
  align-items: center;
  height: 22px;
  padding: 0 8px;
  border-radius: 999px;
  font-size: 11px;
  font-style: normal;
  font-weight: 800;
}

.notice-tag-system {
  background: #edf4ff;
  color: #2767e7;
}

.notice-tag-warning {
  background: #fff4e5;
  color: #d97706;
}

.notice-tag-info {
  background: #f3f6fb;
  color: #60718c;
}

.notice-state {
  color: #97a6bb;
  font-size: 11px;
  font-weight: 800;
}

.notice-state.unread {
  color: #f97316;
}

.notice-item p {
  margin: 6px 0 0;
  color: #6e809b;
  font-size: 12px;
  line-height: 1.7;
}

.status-list {
  display: flex;
  flex-direction: column;
}

.status-row {
  min-height: 40px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid #eef2f8;
}

.status-row span {
  color: #41516b;
  display: flex;
  align-items: center;
}

.status-row .status-ok {
  color: #16a26d;
}

.status-row .status-bad {
  color: #ef4444;
}

.status-row .status-unknown,
.status-unknown {
  color: #8a6a13;
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #19b16f;
  display: inline-block;
  margin-right: 8px;
}

.status-dot.offline {
  background: #ef4444;
}

.status-dot.unknown {
  background: #d6a629;
}

.status-footer {
  min-height: 48px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  color: #93a2b7;
}

.status-success {
  color: #17a36b;
}

.status-error {
  color: #ef4444;
}

/* 中宽屏（1501-1680px）：4 列下功能卡片过窄，长描述与
   "点击进入 XXX" 文本大量换行溢出，降为 3 列保证文字留白 */
@media (max-width: 1680px) {
  .quick-start-grid,
  .feature-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

@media (max-width: 1500px) {
  .quick-start-grid,
  .feature-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 1180px) {
  .dashboard-grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .dashboard-side {
    position: static;
  }
}

@media (max-width: 900px) {








  /* 仪表盘网格间距收敛 */
  .dashboard-grid {
    gap: 12px;
  }

  .dashboard-side {
    gap: 12px;
  }

  /* 各 Section 内边距收敛 */
  .dashboard-section {
    margin-top: 12px;
    border-radius: 14px;
    padding: 14px;
  }

  /* 快速开始 / 功能特性：小屏 1 列堆叠 */
  .quick-start-grid,
  .feature-grid {
    grid-template-columns: minmax(0, 1fr);
    gap: 10px;
  }

  .quick-card,
  .feature-card {
    min-height: 0;
    padding: 12px;
    border-radius: 14px;
    gap: 12px;
  }

  .quick-card .circle-ico,
  .feature-card .circle-ico {
    width: 38px;
    height: 38px;
    flex: 0 0 38px;
    font-size: 18px;
  }

  .quick-text strong,
  .feature-text strong {
    font-size: 14px;
  }

  .quick-text span,
  .feature-text span {
    margin-top: 3px;
    font-size: 11px;
    line-height: 1.55;
  }

  .feature-text em {
    margin-top: 4px;
    font-size: 11px;
  }

  .card-arrow {
    font-size: 18px;
  }

  /* 实时事件行：横向网格 → 纵向堆叠 */
  .event-row {
    grid-template-columns: minmax(0, 1fr);
    gap: 4px;
    padding: 12px 14px;
    min-height: 0;
  }
  .dashboard-grid > *,
  .quick-start-grid > *,
  .feature-grid > *,
  .event-row > * {
    min-width: 0;
  }

  .event-row + .event-row {
    border-top: 1px solid #f1f5fa;
  }

  .event-row strong {
    font-size: 13px;
  }

  .event-row span {
    font-size: 12px;
    line-height: 1.6;
  }

  .event-row em {
    text-align: left;
    font-size: 11px;
  }

  /* 右侧面板内边距收敛 */
  .side-panel {
    border-radius: 14px;
    padding: 14px 12px;
  }







  /* 指南区收敛 */
  .guide-section h4 {
    font-size: 14px;
  }

  .guide-section p {
    margin-top: 8px;
    font-size: 12px;
    line-height: 1.7;
  }

  .guide-list {
    margin-top: 10px;
    padding-left: 16px;
  }

  .guide-step-head {
    gap: 8px;
  }

  .guide-list span {
    margin-top: 3px;
    font-size: 11px;
    line-height: 1.65;
  }

  .guide-collapse-item {
    min-height: 40px;
    font-size: 12px;
  }

  .guide-collapse-panel p {
    font-size: 11px;
    line-height: 1.7;
  }

  .guide-collapse-points {
    font-size: 11px;
    line-height: 1.7;
  }

  /* 通知项收敛 */
  .notice-item {
    padding: 10px 0;
  }

  .notice-head strong {
    font-size: 12px;
  }

  .notice-head span {
    font-size: 10px;
  }

  .notice-item p {
    margin-top: 4px;
    font-size: 11px;
    line-height: 1.65;
  }

  /* 系统状态行收敛 */
  .status-row {
    min-height: 36px;
  }

  .status-footer {
    min-height: 40px;
  }

  /* 空状态收敛 */
  .events-empty {
    min-height: 70px;
  }
}
</style>
