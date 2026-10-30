<template>
  <div class="data-page">
    <div class="data-hero">
      <div class="hero-glow glow-blue"></div>
      <div class="hero-glow glow-purple"></div>
      <div class="hero-glow glow-accent"></div>
      <div class="hero-header">
        <div class="hero-title-group">
          <div class="hero-badge">
            <span class="hero-dot"></span>
            <span>实时数据</span>
          </div>
          <h2>数据面板</h2>
          <p>{{ scopeLabel }} · {{ dateLabel }} · 更新于 {{ updatedAt }}</p>
        </div>
        <div class="hero-controls">
          <div class="control-item">
            <label>账号</label>
            <select
              v-model="accountId"
              class="hero-select"
              :disabled="accountsLoading"
            >
              <option value="all">全部账号</option>
              <option v-for="acc in accounts" :key="acc.id" :value="acc.id">
                {{ formatAccountLabel(acc) }}
              </option>
            </select>
          </div>
          <div class="control-item">
            <label>日期</label>
            <input v-model="date" class="hero-input" type="date">
          </div>
          <button class="refresh-btn" :disabled="loading" @click="load">
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" :class="{ 'spin': loading }">
              <path d="M21 12a9 9 0 11-6.219-8.56"/><polyline points="21 3 21 9 15 9"/>
            </svg>
            {{ loading ? '加载中' : '刷新' }}
          </button>
        </div>
      </div>
    </div>

    <div v-if="summaryError || trendError" class="error-banner">
      <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
      {{ summaryError || trendError }}
    </div>

    <div class="metrics-strip">
      <div v-for="(m, i) in metrics" :key="m.key" class="metric-card" :style="{ '--delay': i * 60 + 'ms', '--accent': m.color }">
        <div class="metric-icon-wrap">
          <div class="metric-icon-bg"></div>
          <Icon :name="m.icon" class="metric-icon" />
        </div>
        <div class="metric-body">
          <span class="metric-label">{{ m.title }}</span>
          <strong class="metric-value" :class="{ 'metric-loading': m.value === null }">{{ m.display }}</strong>
          <span class="metric-sub">{{ m.sub }}</span>
        </div>
        <div class="metric-spark" v-if="m.spark && m.spark.length > 1">
          <svg viewBox="0 0 100 36" preserveAspectRatio="none" class="spark-svg">
            <polyline :points="m.sparkPoints" fill="none" :stroke="m.color" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
            <polygon :points="m.sparkFillPoints" :fill="m.color" opacity="0.12"/>
          </svg>
        </div>
      </div>
    </div>

    <div class="chart-row chart-row-main">
      <CardPanel class="chart-panel trend-panel">
        <template #title>
          <div class="panel-title-row">
            <span class="panel-title-text">业务趋势</span>
            <span class="panel-title-desc">近 {{ trendDays }} 天多维度走势</span>
          </div>
        </template>
        <template #action>
          <div class="range-pills">
            <button
              v-for="opt in trendRangeOptions"
              :key="opt.value"
              type="button"
              :class="['range-pill', { active: trendDays === opt.value }]"
              @click="switchTrendRange(opt.value)"
            >
              {{ opt.label }}
            </button>
          </div>
        </template>
        <div v-if="trendAvailable" ref="trendChartEl" class="echart-box trend-box"></div>
        <EmptyState v-else icon="⚠" title="趋势不可用" :description="trendError || '正在加载趋势数据'" />
      </CardPanel>
    </div>

    <div class="chart-row chart-row-three">
      <CardPanel class="chart-panel">
        <template #title>
          <div class="panel-title-row">
            <span class="panel-title-text">发货分布</span>
          </div>
        </template>
        <div v-if="summaryAvailable && totalDelivery > 0" ref="deliveryPieEl" class="echart-box pie-box"></div>
        <EmptyState v-else icon="⚠" title="暂无数据" :description="summaryError || '暂无发货数据'" />
      </CardPanel>
      <CardPanel class="chart-panel">
        <template #title>
          <div class="panel-title-row">
            <span class="panel-title-text">核心指标对比</span>
          </div>
        </template>
        <div v-if="summaryAvailable" ref="replyBarEl" class="echart-box bar-box"></div>
        <EmptyState v-else icon="⚠" title="暂无数据" :description="summaryError || '正在加载汇总数据'" />
      </CardPanel>
      <CardPanel class="chart-panel overview-panel">
        <template #title>
          <div class="panel-title-row">
            <span class="panel-title-text">运营概览</span>
          </div>
        </template>
        <div v-if="summaryAvailable" class="overview-grid">
          <div v-for="item in overviewItems" :key="item.label" class="overview-item">
            <div class="overview-label">{{ item.label }}</div>
            <div class="overview-value" :style="{ color: item.color }">{{ item.value }}</div>
            <div class="overview-bar"><div class="overview-bar-fill" :style="{ width: item.pct + '%', background: item.color }"></div></div>
          </div>
        </div>
        <EmptyState v-else icon="⚠" title="暂无数据" :description="summaryError || '正在加载汇总数据'" />
      </CardPanel>
    </div>

    <CardPanel class="table-panel">
      <template #title>
        <div class="panel-title-row">
          <span class="panel-title-text">趋势明细</span>
          <span class="panel-title-desc">按日展示各项业务指标</span>
        </div>
      </template>
      <template #action>
        <button v-if="trendAvailable" type="button" class="export-btn" @click="exportTrendCsv">
          <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
          导出 CSV
        </button>
      </template>
      <div v-if="trendAvailable" class="table-wrap">
        <table class="data-table">
          <thead>
            <tr>
              <th>日期</th>
              <th>订单数</th>
              <th>消息数</th>
              <th>发货合计</th>
              <th>发货成功</th>
              <th>发货失败</th>
              <th>AI 回复</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, idx) in trendRows" :key="row.date" :class="{ 'row-alt': idx % 2 === 1 }">
              <td class="td-date">{{ row.date }}</td>
              <td class="td-num"><span class="num-pill num-blue">{{ row.orderCount }}</span></td>
              <td class="td-num"><span class="num-pill num-cyan">{{ row.messageCount }}</span></td>
              <td class="td-num">{{ row.deliveryTotal }}</td>
              <td class="td-num"><span v-if="row.deliverySuccess > 0" class="num-dot dot-green"></span>{{ row.deliverySuccess }}</td>
              <td class="td-num"><span v-if="row.deliveryFail > 0" class="num-dot dot-red"></span>{{ row.deliveryFail }}</td>
              <td class="td-num"><span v-if="row.aiReply > 0" class="num-pill num-purple">{{ row.aiReply }}</span><span v-else class="num-muted">0</span></td>
            </tr>
          </tbody>
        </table>
      </div>
      <EmptyState v-else icon="⚠" title="趋势明细不可用" :description="trendError || '正在加载趋势数据'" />
    </CardPanel>

    <div class="bottom-grid">
      <CardPanel class="chart-panel events-panel">
        <template #title>
          <div class="panel-title-row">
            <span class="panel-title-text">最新实时事件</span>
            <span class="live-dot" :class="sseStatus"><span class="live-pulse"></span>{{ sseStatus === 'connected' ? '实时连接' : '连接离线' }}</span>
          </div>
        </template>
        <div v-if="logs.length > 0" class="event-list">
          <div v-for="(n, idx) in logs" :key="n.t + n.time + idx" class="event-item">
            <div class="event-dot"></div>
            <div class="event-content">
              <b>{{ n.t }}</b>
              <p>{{ n.d }}</p>
            </div>
            <span class="event-time">{{ n.time }}</span>
          </div>
        </div>
        <EmptyState
          v-else
          icon="📡"
          :title="sseStatus === 'connected' ? '暂无实时事件' : '实时事件流暂时不可用'"
          :description="sseStatus === 'connected' ? '订单、发货、AI 回复等实时事件会在这里显示。' : '当前未确认实时连接可用，请以各业务列表中的服务端数据为准。'"
        />
      </CardPanel>

      <CardPanel class="chart-panel quick-panel">
        <template #title>
          <div class="panel-title-row">
            <span class="panel-title-text">快捷操作</span>
          </div>
        </template>
        <div class="quick-grid">
          <div v-for="q in quick" :key="q.key" class="quick-card" @click="$emit('navigate', q.key)">
            <div class="quick-ico">{{ q.icon }}</div>
            <span>{{ q.label }}</span>
            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" class="quick-arrow"><polyline points="9 18 15 12 9 6"/></svg>
          </div>
        </div>
      </CardPanel>
    </div>
  </div>
</template>
<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import CardPanel from '../components/CardPanel.vue'
import EmptyState from '../components/EmptyState.vue'
import Icon from '../components/Icon.vue'
import { getDashboardSummary, getDashboardSalesTrend } from '../api/dashboard.js'
import { getAccounts } from '../api/accounts.js'
import { shortText } from '../utils/format.js'
import * as echarts from 'echarts/core'
import { LineChart, PieChart, BarChart } from 'echarts/charts'
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

echarts.use([
  LineChart, PieChart, BarChart,
  GridComponent, TooltipComponent, LegendComponent, DataZoomComponent,
  CanvasRenderer,
])

defineEmits(['navigate'])

const C = {
  primary: '#0d6bff',
  primarySoft: '#3186ff',
  green: '#16bf78',
  red: '#ff5b61',
  orange: '#ff9f22',
  purple: '#8b5cf6',
  cyan: '#11b5d8',
  pink: '#ec4899',
  slate: '#72809a',
}

const emptyStats = () => ({
  accountCount: null, goodsCount: null, sellingGoodsCount: null, totalSoldCount: null,
  todayOrderCount: null, todaySalesAmount: null, messageCount: null,
  autoReplyCount: null, aiReplyCount: null, wsOnlineRate: null,
  deliverySuccessCount: null, deliveryFailCount: null, pendingDeliveryCount: null,
  orderCount: null,
})

const stats = ref(emptyStats())
const trend = ref({ dates: [], orderCount: [], messageCount: [], deliveryCount: [], deliverySuccess: [], deliveryFail: [], aiReplyCount: [] })
const updatedAt = ref('-')
const summaryError = ref('')
const trendError = ref('')
const summaryAvailable = ref(false)
const trendAvailable = ref(false)
const loading = ref(false)
const logs = ref([])
const sseStatus = ref('disconnected')
const date = ref('')
const accountId = ref('all')
const accounts = ref([])
const accountsLoading = ref(false)
const trendDays = ref(7)
const trendRangeOptions = [
  { label: '7天', value: 7 },
  { label: '14天', value: 14 },
  { label: '30天', value: 30 },
]

const trendChartEl = ref(null)
const deliveryPieEl = ref(null)
const replyBarEl = ref(null)
let trendChart = null, deliveryPieChart = null, replyBarChart = null

const quick = [
  { label: '添加闲鱼账号', key: 'accounts', icon: '👤' },
  { label: '发布新商品', key: 'product-publish', icon: '📦' },
  { label: '同步商品', key: 'products', icon: '🔄' },
  { label: '配置自动发货', key: 'auto-delivery', icon: '🚚' },
  { label: '定时任务', key: 'scheduled-tasks', icon: '⏰' },
  { label: 'AI 客服设置', key: 'settings-ai-cs', icon: '🤖' },
]

const dateLabel = computed(() => {
  if (date.value) return date.value
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
})

const scopeLabel = computed(() => {
  if (accountId.value === 'all' || !accountId.value) return '全部账号'
  const acc = accounts.value.find(a => String(a.id) === String(accountId.value))
  return acc ? (acc.nickname || acc.remark || acc.loginUsername || `账号 #${acc.id}`) : '全部账号'
})

const totalDelivery = computed(() => Number(stats.value.deliverySuccessCount) + Number(stats.value.deliveryFailCount) + Number(stats.value.pendingDeliveryCount))

const successRate = computed(() => {
  const s = Number(stats.value.deliverySuccessCount), f = Number(stats.value.deliveryFailCount)
  const sum = s + f
  return sum > 0 ? Math.round(s * 100 / sum) : 0
})

function metricVal(v) { return v === null || v === undefined ? null : v }
function metricDisplay(v, suffix = '') { return v === null || v === undefined ? '—' : v + suffix }
function formatAmount(v) {
  if (v === null || v === undefined) return '0.00'
  const n = Number(v)
  return Number.isFinite(n) ? n.toFixed(2) : '0.00'
}
function formatAccountLabel(acc) {
  const parts = []
  if (acc.nickname) parts.push(acc.nickname)
  else if (acc.remark) parts.push(acc.remark)
  else if (acc.loginUsername) parts.push(acc.loginUsername)
  if (acc.id) parts.push(`#${acc.id}`)
  return parts.length ? parts.join(' ') : `账号 ${acc.id}`
}

function buildSparkline(series) {
  if (!series || series.length < 2) return null
  const w = 100, h = 36, pad = 2
  const max = Math.max(...series, 1)
  const step = (w - pad * 2) / (series.length - 1)
  const pts = series.map((v, i) => {
    const x = pad + i * step
    const y = h - pad - (v / max) * (h - pad * 2)
    return `${x.toFixed(1)},${y.toFixed(1)}`
  })
  const fillPts = `${pad},${h - pad} ${pts.join(' ')} ${w - pad},${h - pad}`
  return { points: pts.join(' '), fillPoints: fillPts }
}

const metrics = computed(() => {
  const s = stats.value
  const t = trend.value
  const orderSpark = t.orderCount?.length > 1 ? buildSparkline(t.orderCount) : null
  const successSpark = t.deliverySuccess?.length > 1 ? buildSparkline(t.deliverySuccess) : null
  const replySpark = t.aiReplyCount?.length > 1 ? buildSparkline(t.aiReplyCount) : null
  return [
    { key: 'orders', title: '今日订单', value: metricVal(s.todayOrderCount ?? s.orderCount), display: metricDisplay(s.todayOrderCount ?? s.orderCount), sub: '今日新增订单', icon: 'product', color: C.primary, spark: t.orderCount, sparkPoints: orderSpark?.points, sparkFillPoints: orderSpark?.fillPoints },
    { key: 'success', title: '发货成功', value: metricVal(s.deliverySuccessCount), display: metricDisplay(s.deliverySuccessCount), sub: `成功率 ${successRate.value}%`, icon: 'record', color: C.green, spark: t.deliverySuccess, sparkPoints: successSpark?.points, sparkFillPoints: successSpark?.fillPoints },
    { key: 'fail', title: '发货失败', value: metricVal(s.deliveryFailCount), display: metricDisplay(s.deliveryFailCount), sub: '需处理', icon: 'task', color: C.red, spark: t.deliveryFail, sparkPoints: buildSparkline(t.deliveryFail)?.points, sparkFillPoints: buildSparkline(t.deliveryFail)?.fillPoints },
    { key: 'pending', title: '待发货', value: metricVal(s.pendingDeliveryCount), display: metricDisplay(s.pendingDeliveryCount), sub: '排队中', icon: 'task', color: C.orange, spark: null },
    { key: 'reply', title: 'AI 回复', value: metricVal(s.autoReplyCount ?? s.aiReplyCount), display: metricDisplay(s.autoReplyCount ?? s.aiReplyCount), sub: '今日命中次数', icon: 'chat', color: C.purple, spark: t.aiReplyCount, sparkPoints: replySpark?.points, sparkFillPoints: replySpark?.fillPoints },
    { key: 'msg', title: '今日消息', value: metricVal(s.messageCount), display: metricDisplay(s.messageCount), sub: '今日消息总数', icon: 'message', color: C.cyan, spark: null },
    { key: 'sales', title: '今日销售额', value: s.todaySalesAmount, display: `¥${formatAmount(s.todaySalesAmount)}`, sub: '订单金额合计', icon: 'data', color: C.pink, spark: null },
    { key: 'goods', title: '商品总数', value: s.goodsCount, display: metricDisplay(s.goodsCount), sub: `在售 ${metricDisplay(s.sellingGoodsCount)} · 已售 ${metricDisplay(s.totalSoldCount)}`, icon: 'product', color: C.slate, spark: null },
  ]
})

const overviewItems = computed(() => {
  const s = stats.value
  const items = [
    { label: '发货成功率', value: `${successRate.value}%`, pct: successRate.value, color: C.green },
    { label: '商品在售率', value: metricDisplay(s.sellingGoodsCount), pct: s.goodsCount > 0 ? Math.round(Number(s.sellingGoodsCount || 0) / Number(s.goodsCount || 1) * 100) : 0, color: C.primary },
    { label: '账号数', value: metricDisplay(s.accountCount), pct: s.accountCount > 0 ? 100 : 0, color: C.cyan },
    { label: 'AI 回复占比', value: metricDisplay(s.autoReplyCount ?? s.aiReplyCount), pct: (s.messageCount ?? 0) > 0 ? Math.min(100, Math.round(Number(s.autoReplyCount || 0) / Number(s.messageCount || 1) * 100)) : 0, color: C.purple },
  ]
  return items
})

const trendRows = computed(() => {
  const t = trend.value
  if (!t.dates || !t.dates.length) return []
  return t.dates.map((d, i) => ({
    date: d,
    orderCount: t.orderCount?.[i] ?? 0,
    messageCount: t.messageCount?.[i] ?? 0,
    deliveryTotal: (t.deliverySuccess?.[i] ?? 0) + (t.deliveryFail?.[i] ?? 0),
    deliverySuccess: t.deliverySuccess?.[i] ?? 0,
    deliveryFail: t.deliveryFail?.[i] ?? 0,
    aiReply: t.aiReplyCount?.[i] ?? 0,
  }))
})

async function loadAccounts() {
  accountsLoading.value = true
  try {
    const result = await getAccounts({ current: 1, size: 100 })
    const data = result?.data
    let records = []
    if (Array.isArray(data)) records = data
    else if (data && typeof data === 'object') {
      for (const key of ['records', 'accounts', 'list', 'rows']) {
        if (Array.isArray(data[key])) { records = data[key]; break }
      }
    }
    accounts.value = records
  } catch {
    accounts.value = []
  } finally {
    accountsLoading.value = false
  }
}

function buildParams(params) {
  const p = { ...params }
  if (p.accountId === '' || p.accountId === 'all' || p.accountId == null) delete p.accountId
  return p
}

async function load() {
  loading.value = true
  summaryError.value = ''; trendError.value = ''
  summaryAvailable.value = false; trendAvailable.value = false
  stats.value = emptyStats()
  trend.value = { dates: [], orderCount: [], messageCount: [], deliveryCount: [], deliverySuccess: [], deliveryFail: [], aiReplyCount: [] }

  const sp = date.value ? { date: date.value, accountId: accountId.value } : { accountId: accountId.value }
  const tp = { days: trendDays.value, accountId: accountId.value }

  const [sr, tr] = await Promise.allSettled([
    getDashboardSummary(buildParams(sp)),
    getDashboardSalesTrend(buildParams(tp)),
  ])

  if (sr.status === 'fulfilled') {
    try {
      const sd = sr.value?.data
      if (!sd || typeof sd !== 'object' || Array.isArray(sd)) throw new Error('汇总数据响应格式异常')
      const ns = {
        ...emptyStats(),
        accountCount: sd.accountCount ?? null,
        goodsCount: sd.goodsCount ?? null,
        sellingGoodsCount: sd.sellingGoodsCount ?? null,
        totalSoldCount: sd.totalSoldCount ?? null,
        todayOrderCount: sd.todayOrderCount ?? sd.orderCount ?? null,
        todaySalesAmount: sd.todaySalesAmount ?? null,
        messageCount: sd.messageCount ?? null,
        autoReplyCount: sd.autoReplyCount ?? sd.aiReplyCount ?? null,
        aiReplyCount: sd.autoReplyCount ?? sd.aiReplyCount ?? null,
        deliverySuccessCount: sd.deliverySuccessCount ?? null,
        deliveryFailCount: sd.deliveryFailCount ?? null,
        pendingDeliveryCount: sd.pendingDeliveryCount ?? null,
      }
      stats.value = ns
      summaryAvailable.value = true
    } catch (e) { summaryError.value = e.message || '汇总数据不可用' }
  } else { summaryError.value = sr.reason?.message || '汇总数据加载失败' }

  if (tr.status === 'fulfilled') {
    try {
      const td = tr.value?.data
      if (!td || typeof td !== 'object' || Array.isArray(td)) throw new Error('趋势数据响应格式异常')
      if (!Array.isArray(td.dates) || !Array.isArray(td.deliverySuccess) || !Array.isArray(td.deliveryFail)) throw new Error('趋势数据响应格式异常')
      const len = td.dates.length
      trend.value = {
        dates: td.dates,
        orderCount: (td.orderCount ?? new Array(len).fill(0)).map(Number),
        messageCount: (td.messageCount ?? new Array(len).fill(0)).map(Number),
        deliveryCount: (td.deliveryCount ?? new Array(len).fill(0)).map(Number),
        deliverySuccess: td.deliverySuccess.map(Number),
        deliveryFail: td.deliveryFail.map(Number),
        aiReplyCount: (td.aiReplyCount ?? td.aiReplies ?? new Array(len).fill(0)).map(Number),
      }
      trendAvailable.value = true
    } catch (e) { trendError.value = e.message || '趋势数据不可用' }
  } else { trendError.value = tr.reason?.message || '趋势数据加载失败' }

  if (summaryAvailable.value || trendAvailable.value)
    updatedAt.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
  else updatedAt.value = '-'
  loading.value = false

  await nextTick()
  renderAllCharts()
}

function switchTrendRange(days) {
  if (trendDays.value === days) return
  trendDays.value = days
  load()
}

function renderAllCharts() {
  if (trendAvailable.value) renderTrendChart()
  if (summaryAvailable.value && totalDelivery.value > 0) renderDeliveryPie()
  if (summaryAvailable.value) renderReplyBar()
}

const tooltipStyle = {
  backgroundColor: 'rgba(255,255,255,0.98)',
  borderColor: 'rgba(13,107,255,0.08)',
  borderWidth: 1,
  borderRadius: 12,
  padding: [12, 16],
  textStyle: { color: '#15213d', fontSize: 13, fontWeight: 500 },
  extraCssText: 'box-shadow:0 12px 40px rgba(31,53,94,0.12);backdrop-filter:blur(16px);',
}

function renderTrendChart() {
  if (!trendChartEl.value) return
  if (!trendChart) trendChart = echarts.init(trendChartEl.value)
  const t = trend.value
  trendChart.setOption({
    color: [C.primary, C.green, C.red, C.purple, C.cyan],
    tooltip: {
      trigger: 'axis',
      axisPointer: {
        type: 'cross',
        crossStyle: { color: '#e7edf7', width: 1 },
        lineStyle: { color: '#c5d0e4', type: 'dashed' }
      },
      ...tooltipStyle
    },
    legend: {
      data: ['订单数', '发货成功', '发货失败', 'AI回复', '消息数'],
      top: 8, right: 20,
      textStyle: { color: '#72809a', fontSize: 12, fontWeight: 500 },
      itemWidth: 18, itemHeight: 4, itemGap: 20,
      icon: 'roundRect',
      itemBorderRadius: 2,
    },
    grid: { left: 52, right: 28, top: 52, bottom: 52, containLabel: true },
    xAxis: {
      type: 'category', boundaryGap: false, data: t.dates,
      axisLine: { lineStyle: { color: '#e7edf7' } },
      axisTick: { show: false },
      axisLabel: { color: '#8c98ae', fontSize: 11, margin: 14, fontWeight: 500 },
    },
    yAxis: {
      type: 'value',
      splitLine: { lineStyle: { color: '#f0f4fa', type: 'dashed' } },
      axisLabel: { color: '#8c98ae', fontSize: 11, fontWeight: 500 },
      axisLine: { show: false }, axisTick: { show: false },
    },
    dataZoom: [
      { type: 'inside', start: 0, end: 100, zoomOnMouseWheel: false },
      {
        type: 'slider',
        height: 22,
        bottom: 10,
        start: 0,
        end: 100,
        borderColor: 'transparent',
        backgroundColor: 'transparent',
        fillerColor: 'rgba(13,107,255,0.08)',
        handleSize: 18,
        moveHandleSize: 14,
        textStyle: { color: '#8c98ae', fontSize: 10 },
        dataBackground: {
          lineStyle: { color: '#e7edf7', width: 1 },
          areaStyle: { color: '#f4f7fc' }
        },
        selectedDataBackground: {
          lineStyle: { color: C.primary, width: 1.5 },
          areaStyle: { color: 'rgba(13,107,255,0.1)' }
        }
      },
    ],
    series: [
      {
        name: '订单数',
        type: 'line',
        smooth: 0.35,
        showSymbol: false,
        emphasis: { focus: 'series', lineStyle: { width: 4 }, scale: true },
        data: t.orderCount,
        lineStyle: { width: 3, color: C.primary, shadowColor: 'rgba(13,107,255,0.2)', shadowBlur: 8, shadowOffsetY: 4 },
        itemStyle: { color: C.primary, borderWidth: 2, borderColor: '#fff' },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: 'rgba(13,107,255,0.22)' },
            { offset: 0.6, color: 'rgba(13,107,255,0.06)' },
            { offset: 1, color: 'rgba(13,107,255,0.01)' }
          ])
        }
      },
      {
        name: '发货成功',
        type: 'line',
        smooth: 0.35,
        showSymbol: false,
        emphasis: { focus: 'series' },
        data: t.deliverySuccess,
        lineStyle: { width: 2.5, color: C.green },
        itemStyle: { color: C.green },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: 'rgba(22,191,120,0.15)' },
            { offset: 1, color: 'rgba(22,191,120,0.01)' }
          ])
        }
      },
      {
        name: '发货失败',
        type: 'line',
        smooth: 0.35,
        showSymbol: false,
        emphasis: { focus: 'series' },
        data: t.deliveryFail,
        lineStyle: { width: 2, color: C.red },
        itemStyle: { color: C.red }
      },
      {
        name: 'AI回复',
        type: 'line',
        smooth: 0.35,
        showSymbol: false,
        emphasis: { focus: 'series' },
        data: t.aiReplyCount,
        lineStyle: { width: 2.5, color: C.purple },
        itemStyle: { color: C.purple },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: 'rgba(139,92,246,0.12)' },
            { offset: 1, color: 'rgba(139,92,246,0.01)' }
          ])
        }
      },
      {
        name: '消息数',
        type: 'line',
        smooth: 0.35,
        showSymbol: false,
        emphasis: { focus: 'series' },
        data: t.messageCount,
        lineStyle: { width: 2, color: C.cyan, type: [6, 4] },
        itemStyle: { color: C.cyan }
      },
    ],
  }, true)
}

function renderDeliveryPie() {
  if (!deliveryPieEl.value) return
  if (!deliveryPieChart) deliveryPieChart = echarts.init(deliveryPieEl.value)
  const sc = Number(stats.value.deliverySuccessCount) || 0
  const fc = Number(stats.value.deliveryFailCount) || 0
  const pc = Number(stats.value.pendingDeliveryCount) || 0
  deliveryPieChart.setOption({
    color: [C.green, C.red, C.orange],
    tooltip: {
      trigger: 'item',
      formatter: (params) => {
        return `<div style="font-weight:600;margin-bottom:4px;">${params.name}</div>
                <div style="display:flex;align-items:center;gap:6px;">
                  <span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${params.color};"></span>
                  <span>${params.value} 单</span>
                  <span style="color:#72809a;font-weight:600;">(${params.percent}%)</span>
                </div>`
      },
      ...tooltipStyle
    },
    series: [{
      name: '发货分布', type: 'pie',
      radius: ['48%', '76%'], center: ['50%', '50%'],
      avoidLabelOverlap: true,
      itemStyle: {
        borderRadius: 8,
        borderColor: '#fff',
        borderWidth: 4,
        shadowBlur: 12,
        shadowColor: 'rgba(31,53,94,0.08)'
      },
      label: {
        show: true,
        position: 'center',
        formatter: () => `{total|${sc + fc + pc}}\n{label|总发货}`,
        rich: {
          total: { fontSize: 30, fontWeight: 800, color: '#15213d', lineHeight: 40, textShadowColor: 'rgba(13,107,255,0.08)', textShadowBlur: 8 },
          label: { fontSize: 12, color: '#72809a', lineHeight: 20, fontWeight: 500 }
        }
      },
      emphasis: {
        scaleSize: 10,
        label: { show: true },
        itemStyle: { shadowBlur: 20, shadowColor: 'rgba(31,53,94,0.15)' }
      },
      labelLine: { show: false, length: 12, length2: 8 },
      data: [
        {
          value: sc, name: '成功',
          label: { show: false },
          emphasis: {
            label: {
              show: true, position: 'outer',
              formatter: '{b}\n{c}单 ({d}%)',
              fontSize: 12,
              color: C.green,
              fontWeight: 600,
              lineHeight: 18
            }
          }
        },
        {
          value: fc, name: '失败',
          label: { show: false },
          emphasis: {
            label: {
              show: true, position: 'outer',
              formatter: '{b}\n{c}单 ({d}%)',
              fontSize: 12,
              color: C.red,
              fontWeight: 600,
              lineHeight: 18
            }
          }
        },
        {
          value: pc, name: '待发货',
          label: { show: false },
          emphasis: {
            label: {
              show: true, position: 'outer',
              formatter: '{b}\n{c}单 ({d}%)',
              fontSize: 12,
              color: C.orange,
              fontWeight: 600,
              lineHeight: 18
            }
          }
        },
      ],
    }],
  }, true)
}

function renderReplyBar() {
  if (!replyBarEl.value) return
  if (!replyBarChart) replyBarChart = echarts.init(replyBarEl.value)
  const reply = Number(stats.value.autoReplyCount ?? stats.value.aiReplyCount) || 0
  const order = Number(stats.value.todayOrderCount ?? stats.value.orderCount) || 0
  const delivery = Number(stats.value.deliverySuccessCount) || 0
  const maxVal = Math.max(reply, order, delivery, 1)
  const barData = [
    { value: reply, name: 'AI回复', color: C.purple, icon: '🤖' },
    { value: order, name: '今日订单', color: C.primary, icon: '📦' },
    { value: delivery, name: '发货成功', color: C.green, icon: '✅' },
  ]
  replyBarChart.setOption({
    tooltip: {
      trigger: 'axis',
      axisPointer: {
        type: 'shadow',
        shadowStyle: { color: 'rgba(13,107,255,0.04)', borderRadius: 8 }
      },
      formatter: (params) => {
        const d = barData[params[0].dataIndex]
        return `<div style="font-weight:600;margin-bottom:4px;">${d.icon} ${params[0].name}</div>
                <div style="font-size:16px;font-weight:800;color:${d.color};">${params[0].value}</div>`
      },
      ...tooltipStyle
    },
    grid: { left: 12, right: 12, top: 32, bottom: 28, containLabel: true },
    xAxis: {
      type: 'category', data: barData.map(d => d.name),
      axisLine: { show: false }, axisTick: { show: false },
      axisLabel: { color: '#72809a', fontSize: 12, margin: 12, fontWeight: 500 },
    },
    yAxis: {
      type: 'value', show: false, max: Math.max(maxVal * 1.35, 5),
    },
    series: [{
      type: 'bar', barWidth: 52, data: barData.map((d) => ({
        value: d.value,
        itemStyle: {
          borderRadius: [12, 12, 4, 4],
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: d.color },
            { offset: 0.5, color: d.color + 'cc' },
            { offset: 1, color: d.color + '66' },
          ]),
          shadowColor: d.color + '40',
          shadowBlur: 12,
          shadowOffsetY: 4,
        },
      })),
      label: {
        show: true,
        position: 'top',
        color: '#15213d',
        fontWeight: 800,
        fontSize: 18,
        distance: 8,
        formatter: '{c}'
      },
      emphasis: {
        itemStyle: {
          shadowBlur: 16,
          shadowOffsetY: 6,
        }
      },
      animationDelay: (idx) => idx * 150,
    }],
    animationEasing: 'elasticOut',
    animationDuration: 1000,
  }, true)
}

function exportTrendCsv() {
  if (!trendRows.value.length) return
  const header = ['日期', '订单数', '消息数', '发货合计', '发货成功', '发货失败', 'AI回复'].join(',')
  const lines = trendRows.value.map(row => [row.date, row.orderCount, row.messageCount, row.deliveryTotal, row.deliverySuccess, row.deliveryFail, row.aiReply].join(','))
  const csv = '\ufeff' + [header, ...lines].join('\n')
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `trend-${accountId.value === 'all' ? 'all' : accountId.value}-${trendDays.value}d-${Date.now()}.csv`
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  URL.revokeObjectURL(url)
}

function handleResize() {
  trendChart?.resize()
  deliveryPieChart?.resize()
  replyBarChart?.resize()
}

function onSse(event) {
  sseStatus.value = 'connected'
  const d = event.detail || {}
  logs.value.unshift({ t: d.type || d.event || '实时事件', d: shortText(d.message || d.content || JSON.stringify(d), 70), time: new Date().toLocaleTimeString('zh-CN', { hour12: false }) })
  logs.value = logs.value.slice(0, 5)
}
function onSseStatus(event) { sseStatus.value = String(event?.detail || 'disconnected') }
function onHeader(e) { if (e.detail === 'refresh-data-panel') load() }

watch(accountId, () => { load() })

onMounted(() => {
  window.addEventListener('xya-sse-event', onSse)
  window.addEventListener('xya-sse-status', onSseStatus)
  window.addEventListener('xya-header-action', onHeader)
  window.addEventListener('resize', handleResize)
  loadAccounts()
  load()
})

onBeforeUnmount(() => {
  window.removeEventListener('xya-sse-event', onSse)
  window.removeEventListener('xya-sse-status', onSseStatus)
  window.removeEventListener('xya-header-action', onHeader)
  window.removeEventListener('resize', handleResize)
  trendChart?.dispose(); deliveryPieChart?.dispose(); replyBarChart?.dispose()
  trendChart = null; deliveryPieChart = null; replyBarChart = null
})
</script>

<style scoped>
.data-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding-bottom: 32px;
}

/* ===== Hero Section ===== */
.data-hero {
  position: relative;
  background: linear-gradient(135deg, #0f1b3d 0%, #162d6b 40%, #0d2152 100%);
  border-radius: 16px;
  padding: 24px 28px 22px;
  overflow: hidden;
  color: #fff;
  box-shadow: 0 8px 32px rgba(13, 107, 255, 0.15);
}

.data-hero::before {
  content: '';
  position: absolute;
  top: 0; left: 0; right: 0;
  height: 1px;
  background: linear-gradient(90deg, transparent, rgba(255,255,255,0.2), transparent);
}

.hero-glow {
  position: absolute;
  border-radius: 50%;
  pointer-events: none;
  will-change: transform, opacity;
}
.glow-blue {
  width: 320px; height: 320px;
  background: radial-gradient(circle, rgba(13,107,255,0.5) 0%, transparent 70%);
  top: -120px; right: -60px;
  animation: glow-float 8s ease-in-out infinite;
}
.glow-purple {
  width: 240px; height: 240px;
  background: radial-gradient(circle, rgba(49,134,255,0.35) 0%, transparent 70%);
  bottom: -100px; left: 20%;
  animation: glow-float 10s ease-in-out infinite reverse;
  animation-delay: -3s;
}
.glow-accent {
  width: 180px; height: 180px;
  background: radial-gradient(circle, rgba(22,183,255,0.25) 0%, transparent 70%);
  top: 40%; left: -40px;
  animation: glow-float 12s ease-in-out infinite;
  animation-delay: -6s;
}

@keyframes glow-float {
  0%, 100% { opacity: 0.6; transform: translate(0, 0) scale(1); }
  33% { opacity: 0.8; transform: translate(8px, -8px) scale(1.05); }
  66% { opacity: 0.5; transform: translate(-6px, 6px) scale(0.98); }
}

.hero-header {
  position: relative;
  z-index: 1;
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 18px;
}

.hero-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: rgba(255,255,255,0.1);
  backdrop-filter: blur(12px);
  border: 1px solid rgba(255,255,255,0.12);
  border-radius: 999px;
  padding: 5px 14px;
  font-size: 12px;
  color: rgba(255,255,255,0.85);
  margin-bottom: 14px;
  font-weight: 500;
}
.hero-dot {
  width: 7px; height: 7px;
  border-radius: 50%;
  background: #16bf78;
  box-shadow: 0 0 10px rgba(22,191,120,0.6);
  animation: pulse-dot 2s ease-in-out infinite;
}
@keyframes pulse-dot {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.5; transform: scale(0.85); }
}

.hero-title-group h2 {
  font-size: 26px;
  font-weight: 800;
  margin: 0;
  letter-spacing: -0.8px;
  background: linear-gradient(135deg, #fff 0%, rgba(255,255,255,0.8) 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}
.hero-title-group p {
  margin: 6px 0 0;
  font-size: 13px;
  color: rgba(255,255,255,0.55);
  font-weight: 400;
}

.hero-controls {
  display: flex;
  align-items: flex-end;
  gap: 10px;
  flex-wrap: wrap;
}

.control-item {
  display: flex;
  flex-direction: column;
  gap: 5px;
}
.control-item label {
  font-size: 11px;
  color: rgba(255,255,255,0.45);
  text-transform: uppercase;
  letter-spacing: 0.8px;
  font-weight: 500;
}

.hero-select,
.hero-input {
  background: rgba(255,255,255,0.07);
  border: 1px solid rgba(255,255,255,0.12);
  border-radius: 10px;
  padding: 9px 14px;
  color: #fff;
  font-size: 13px;
  min-width: 150px;
  outline: none;
  transition: all 0.25s ease;
  cursor: pointer;
  font-weight: 500;
}
.hero-select option {
  background: #162d6b;
  color: #fff;
}
.hero-select:hover, .hero-input:hover {
  border-color: rgba(255,255,255,0.25);
  background: rgba(255,255,255,0.12);
}
.hero-select:focus, .hero-input:focus {
  border-color: rgba(49,134,255,0.6);
  background: rgba(255,255,255,0.1);
  box-shadow: 0 0 0 3px rgba(13,107,255,0.2);
}
.hero-input { cursor: default; }
.hero-input::-webkit-calendar-picker-indicator {
  filter: invert(1) opacity(0.6);
  cursor: pointer;
}

.refresh-btn {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  background: linear-gradient(135deg, #0d6bff 0%, #3186ff 100%);
  color: #fff;
  border: none;
  border-radius: 10px;
  padding: 10px 20px;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.25s ease;
  white-space: nowrap;
  box-shadow: 0 4px 14px rgba(13,107,255,0.35);
}
.refresh-btn:hover:not(:disabled) {
  transform: translateY(-1px);
  box-shadow: 0 6px 20px rgba(13,107,255,0.45);
}
.refresh-btn:active:not(:disabled) {
  transform: translateY(0);
}
.refresh-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.spin { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }

/* ===== Error Banner ===== */
.error-banner {
  display: flex;
  align-items: center;
  gap: 10px;
  background: linear-gradient(135deg, #fef2f2 0%, #fff5f5 100%);
  border: 1px solid #fecaca;
  color: #dc2626;
  border-radius: 12px;
  padding: 12px 18px;
  font-size: 13px;
  font-weight: 500;
}

/* ===== Metrics Strip ===== */
.metrics-strip {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
}

.metric-card {
  position: relative;
  background: var(--card, #fff);
  border: 1px solid var(--line, #e7edf7);
  border-radius: 14px;
  padding: 16px 18px;
  display: flex;
  align-items: center;
  gap: 14px;
  transition: all 0.3s cubic-bezier(0.4,0,0.2,1);
  overflow: hidden;
  animation: card-in 0.6s cubic-bezier(0.4,0,0.2,1) both;
  animation-delay: var(--delay, 0ms);
  box-shadow: 0 1px 2px rgba(31, 53, 94, .04), 0 4px 12px rgba(31, 53, 94, .04);
  will-change: transform;
}
@keyframes card-in {
  from { opacity: 0; transform: translateY(16px) scale(0.98); }
  to { opacity: 1; transform: translateY(0) scale(1); }
}
.metric-card:hover {
  border-color: rgba(13,107,255,0.2);
  box-shadow: 0 8px 24px rgba(31, 53, 94, .08);
  transform: translateY(-3px);
}
.metric-card::after {
  content: '';
  position: absolute;
  top: 0; left: 0;
  width: 4px;
  height: 0;
  background: var(--accent);
  border-radius: 0 4px 4px 0;
  transition: height 0.3s cubic-bezier(0.4,0,0.2,1);
}
.metric-card:hover::after { height: 100%; }

.metric-icon-wrap {
  position: relative;
  width: 44px; height: 44px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
}
.metric-icon-bg {
  position: absolute;
  inset: 0;
  border-radius: 12px;
  background: var(--accent);
  opacity: 0.1;
  transition: all 0.3s;
}
.metric-card:hover .metric-icon-bg {
  opacity: 0.15;
  transform: scale(1.08);
}
.metric-icon {
  width: 22px; height: 22px;
  color: var(--accent);
  position: relative;
  z-index: 1;
}

.metric-body {
  flex: 1;
  min-width: 0;
}
.metric-label {
  display: block;
  font-size: 12px;
  color: var(--muted, #72809a);
  margin-bottom: 4px;
  font-weight: 500;
}
.metric-value {
  display: block;
  font-size: 22px;
  font-weight: 800;
  color: var(--text, #15213d);
  line-height: 1.15;
  letter-spacing: -0.6px;
  font-variant-numeric: tabular-nums;
}
.metric-loading { color: #cbd5e1; }
.metric-sub {
  display: block;
  font-size: 11px;
  color: var(--muted, #72809a);
  margin-top: 4px;
  font-weight: 500;
}

.metric-spark {
  position: absolute;
  right: 8px;
  bottom: 6px;
  width: 70px;
  height: 32px;
  opacity: 0.5;
}
.spark-svg {
  width: 100%;
  height: 100%;
}

/* ===== Chart Rows ===== */
.chart-row { display: flex; gap: 14px; }
.chart-row-main { flex-direction: column; }
.chart-row-three { display: grid; grid-template-columns: 1fr 1fr 1.2fr; gap: 14px; }

.chart-panel,
.table-panel,
.events-panel,
.quick-panel {
  border-radius: 14px !important;
  border: 1px solid var(--line, #e7edf7) !important;
  box-shadow: 0 1px 2px rgba(31, 53, 94, .04), 0 6px 16px rgba(31, 53, 94, .05) !important;
  overflow: hidden;
  transition: all 0.3s ease !important;
}
.chart-panel:hover,
.table-panel:hover {
  box-shadow: 0 1px 2px rgba(31, 53, 94, .04), 0 10px 28px rgba(31, 53, 94, .08) !important;
}

.panel-title-row {
  display: flex;
  align-items: baseline;
  gap: 10px;
}
.panel-title-text {
  font-size: 15px;
  font-weight: 700;
  color: var(--text, #15213d);
}
.panel-title-desc {
  font-size: 12px;
  color: var(--muted, #72809a);
  font-weight: 500;
}

.range-pills {
  display: inline-flex;
  gap: 2px;
  background: #f4f7fc;
  border-radius: 9px;
  padding: 3px;
}
.range-pill {
  border: none;
  background: transparent;
  color: var(--muted, #72809a);
  border-radius: 7px;
  padding: 6px 14px;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s;
}
.range-pill:hover { color: var(--text, #15213d); }
.range-pill.active {
  background: #fff;
  color: var(--primary, #0d6bff);
  box-shadow: 0 1px 3px rgba(13,107,255,0.1);
}

.echart-box { width: 100%; }
.trend-box { height: 320px; margin: 0 -4px; }
.pie-box, .bar-box { height: 240px; margin: 0 -4px; }

/* ===== Overview Panel ===== */
.overview-grid {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 4px 0;
}
.overview-item {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.overview-label {
  font-size: 12px;
  color: var(--muted, #72809a);
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-weight: 500;
}
.overview-value {
  font-size: 20px;
  font-weight: 800;
  font-variant-numeric: tabular-nums;
  color: var(--text, #15213d);
}
.overview-bar {
  height: 6px;
  background: #f0f4fa;
  border-radius: 999px;
  overflow: hidden;
}
.overview-bar-fill {
  height: 100%;
  border-radius: 999px;
  transition: width 0.8s cubic-bezier(0.4,0,0.2,1);
}

/* ===== Data Table ===== */
.table-wrap {
  overflow-x: auto;
}
.data-table {
  width: 100%;
  border-collapse: collapse;
}
.data-table thead th {
  text-align: left;
  padding: 14px 18px;
  font-size: 11px;
  font-weight: 700;
  color: var(--muted, #72809a);
  text-transform: uppercase;
  letter-spacing: 0.6px;
  background: #f8fafe;
  border-bottom: 1px solid var(--line, #e7edf7);
  white-space: nowrap;
}
.data-table tbody td {
  padding: 13px 18px;
  font-size: 13px;
  color: var(--text, #15213d);
  border-bottom: 1px solid #f4f7fc;
  font-variant-numeric: tabular-nums;
  font-weight: 500;
}
.data-table tbody tr { transition: background 0.15s; }
.data-table tbody tr:hover { background: #f8fafe; }
.data-table tbody tr:last-child td { border-bottom: none; }
.data-table tbody tr.row-alt { background: #fbfcfe; }
.data-table tbody tr.row-alt:hover { background: #f4f7fc; }

.td-date {
  font-weight: 700;
  color: var(--text, #15213d);
  font-family: 'SF Mono', 'Cascadia Code', 'Consolas', monospace;
  font-size: 12px;
}
.td-num { text-align: left; }

.num-pill {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 34px;
  padding: 3px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 700;
  text-align: center;
}
.num-blue { background: #eef4ff; color: #0d6bff; }
.num-cyan { background: #e8f9fc; color: #0e95b2; }
.num-purple { background: #f3f0ff; color: #7c3aed; }
.num-muted { color: #cbd5e1; font-weight: 500; }
.num-dot {
  display: inline-block;
  width: 7px; height: 7px;
  border-radius: 50%;
  margin-right: 7px;
  vertical-align: middle;
}
.dot-green { background: #16bf78; box-shadow: 0 0 6px rgba(22,191,120,0.4); }
.dot-red { background: #ff5b61; box-shadow: 0 0 6px rgba(255,91,97,0.4); }

.export-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 1px solid var(--line, #e7edf7);
  background: #fff;
  color: var(--muted, #72809a);
  border-radius: 9px;
  padding: 7px 14px;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s;
}
.export-btn:hover {
  border-color: var(--primary, #0d6bff);
  color: var(--primary, #0d6bff);
  background: #f0f5ff;
}

/* ===== Bottom Grid ===== */
.bottom-grid {
  display: grid;
  grid-template-columns: 1.4fr 1fr;
  gap: 14px;
}

/* Events */
.live-dot {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  font-weight: 600;
  padding: 3px 10px;
  border-radius: 999px;
  margin-left: auto;
}
.live-dot.connected { background: #edfbf5; color: #0ea365; }
.live-dot.disconnected { background: #fff0f1; color: #e53e44; }
.live-pulse {
  width: 6px; height: 6px;
  border-radius: 50%;
  background: currentColor;
}
.live-dot.connected .live-pulse { animation: pulse-dot 2s ease-in-out infinite; }

.event-list {
  display: flex;
  flex-direction: column;
  padding: 4px 0;
}
.event-item {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 12px 0;
  border-bottom: 1px solid #f4f7fc;
}
.event-item:last-child { border-bottom: none; }
.event-dot {
  width: 8px; height: 8px;
  border-radius: 50%;
  background: var(--primary, #0d6bff);
  margin-top: 5px;
  flex-shrink: 0;
  box-shadow: 0 0 0 3px rgba(13,107,255,0.12);
  position: relative;
}
.event-dot::after {
  content: '';
  position: absolute;
  inset: -4px;
  border-radius: 50%;
  border: 2px solid var(--primary, #0d6bff);
  opacity: 0;
  animation: event-pulse 2s ease-out infinite;
}
@keyframes event-pulse {
  0% { transform: scale(0.8); opacity: 0.6; }
  100% { transform: scale(2); opacity: 0; }
}
.event-content { flex: 1; min-width: 0; }
.event-content b {
  font-size: 13px;
  color: var(--text, #15213d);
  display: block;
  font-weight: 700;
}
.event-content p {
  margin: 3px 0 0;
  font-size: 12px;
  color: var(--muted, #72809a);
  line-height: 1.55;
}
.event-time {
  font-size: 11px;
  color: #b8c4d9;
  font-family: 'SF Mono', 'Cascadia Code', monospace;
  white-space: nowrap;
  margin-top: 2px;
  font-weight: 500;
}

/* Quick Actions */
.quick-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
  padding: 4px 0;
}
.quick-card {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 14px;
  border-radius: 11px;
  background: #f8fafe;
  cursor: pointer;
  transition: all 0.22s ease;
  font-size: 13px;
  font-weight: 600;
  color: var(--text, #15213d);
}
.quick-card:hover {
  background: #eef4ff;
  color: var(--primary, #0d6bff);
  transform: translateX(3px);
}
.quick-ico {
  width: 34px; height: 34px;
  border-radius: 10px;
  background: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  flex-shrink: 0;
  box-shadow: 0 2px 6px rgba(31,53,94,0.06);
  transition: all 0.22s ease;
}
.quick-card:hover .quick-ico {
  box-shadow: 0 3px 10px rgba(13,107,255,0.15);
  transform: scale(1.05);
}
.quick-card span { flex: 1; }
.quick-arrow {
  color: #c5d0e4;
  transition: all 0.22s ease;
}
.quick-card:hover .quick-arrow {
  color: var(--primary, #0d6bff);
  transform: translateX(3px);
}

/* ===== Reduced Motion Support ===== */
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
  }
  .hero-dot { animation: none; }
  .live-dot.connected .live-pulse { animation: none; }
  .spin { animation: none; }
  .glow-blue, .glow-purple, .glow-accent { animation: none; }
  .event-dot::after { display: none; }
  .metric-card { animation: none; }
}

/* ===== Responsive ===== */
@media (max-width: 1200px) {
  .metrics-strip { grid-template-columns: repeat(4, 1fr); gap: 10px; }
  .chart-row-three { grid-template-columns: 1fr 1fr; }
  .chart-row-three .overview-panel { grid-column: 1 / -1; }
  .bottom-grid { grid-template-columns: 1fr 1fr; }
}
@media (max-width: 900px) {
  .data-hero { padding: 20px; border-radius: 14px; }
  .hero-title-group h2 { font-size: 22px; }
  .metrics-strip { grid-template-columns: repeat(2, 1fr); gap: 10px; }
  .metric-card { padding: 14px; border-radius: 12px; }
  .metric-value { font-size: 20px; }
  .chart-row-three { grid-template-columns: 1fr; }
  .bottom-grid { grid-template-columns: 1fr; }
  .trend-box { height: 260px; }
  .pie-box, .bar-box { height: 220px; }
}
@media (max-width: 600px) {
  .data-page { gap: 12px; }
  .metrics-strip { grid-template-columns: 1fr 1fr; gap: 8px; }
  .hero-controls { width: 100%; }
  .control-item { flex: 1; min-width: 0; }
  .hero-select, .hero-input { min-width: 0; width: 100%; }
  .quick-grid { grid-template-columns: 1fr; }
  .metric-spark { display: none; }
  .refresh-btn { width: 100%; justify-content: center; }
}
</style>
