<template>
  <div class="grid slider-layout" style="grid-template-columns:minmax(0,1fr) 460px;gap:18px">
    <div>
      <div v-if="loadError" class="global-notice error">滑块记录加载失败：{{ loadError }}</div>
      <div class="grid stat-grid">
        <StatCard title="总记录数" :value="total" change="服务端总数" icon="record" />
        <StatCard title="成功数" :value="successCount" change="本页统计" icon="shield" color="green" />
        <StatCard title="失败数" :value="failedCount" change="本页统计" icon="warning" color="red" />
        <StatCard title="处理中数" :value="retryingCount" change="本页统计" icon="refresh" color="orange" />
      </div>
      <!-- 滑块求解规则说明：统计卡片下方常驻展示 -->
      <div class="rules-card">
        <div class="rules-head">
          <span class="rules-title">滑块求解规则说明</span>
          <span class="rules-sub">了解求解机制，便于判断预期</span>
        </div>
        <div class="rules-callout">
          <span class="rules-badge badge-blue">i</span>
          <div>
            <strong>预检测与能力范围</strong>
            <span>每次求解前预检 Cookie 有效性，失效则不予求解。本功能主要解决 WS 掉线引起的滑块问题；Cookie 失效表示登录态已被闲鱼拒绝，需重新扫码登录或更新 Cookie。</span>
          </div>
        </div>
        <div class="rules-grid">
          <div class="rule-item">
            <span class="rules-dot dot-blue"></span>
            <span><strong>求解方式</strong>：默认使用本地开源版滑块求解（能力较弱）；可在「API 对接」页面开启远程滑块求解服务以提升通过率（80%+）。</span>
          </div>
          <div class="rule-item">
            <span class="rules-dot dot-orange"></span>
            <span><strong>手动优先</strong>：手动触发求解优先于自动触发求解。</span>
          </div>
          <div class="rule-item">
            <span class="rules-dot dot-green"></span>
            <span><strong>Cookie 合并</strong>：求解成功后会自动合并浏览器新写入的风控 Cookie 字段（cna/isg/x5sec 等）到数据库。</span>
          </div>
          <div class="rule-item">
            <span class="rules-dot dot-red"></span>
            <span><strong>失败冷却</strong>：求解失败会触发指数退避冷却，期间拒绝自动求解，避免 punish 加码。</span>
          </div>
          <div class="rule-item">
            <span class="rules-dot dot-purple"></span>
            <span><strong>触发场景</strong>：支持手动触发、手动重试、WS 连接、Cookie 保活、Token 刷新五种场景。</span>
          </div>
          <div class="rule-item">
            <span class="rules-dot dot-gray"></span>
            <span><strong>求解耗时</strong>：单次约需 30～120 秒，受网络与风控策略影响。</span>
          </div>
        </div>
      </div>
      <CardPanel title="滑块求解记录" desc="点击表格行查看完整详情">
        <div class="toolbar">
          <select v-model="filters.status" class="input" @change="search">
            <option value="">全部状态</option>
            <option value="success">成功</option>
            <option value="fail">失败</option>
            <option value="retrying">处理中</option>
          </select>
          <select v-model="filters.triggerScene" class="input" @change="search">
            <option value="">全部触发场景</option>
            <option value="manual">手动触发</option>
            <option value="manual_retry">手动重试</option>
            <option value="ws_connect">WS 连接</option>
            <option value="cookie_keepalive">Cookie 保活</option>
            <option value="token_refresh">Token 刷新</option>
          </select>
          <AppButton type="primary" :disabled="loading" @click="search">{{ loading ? '查询中...' : '查询' }}</AppButton>
        </div>
        <BaseTable :columns="cols" :rows="rows" @row-click="showDetail">
          <template #createdAt="{row}">{{ formatDateTime(row.createdAt) }}</template>
          <template #accountId="{row}"><span :title="row.accountId">{{ row.accountId || '-' }}</span></template>
          <template #accountName="{row}"><span :title="row.accountName">{{ row.accountName || '-' }}</span></template>
          <template #openReason="{row}"><span :title="row.openReason" class="cell-truncate">{{ row.openReason || '-' }}</span></template>
          <template #solveReason="{row}"><span :title="row.solveReason" class="cell-truncate">{{ row.solveReason || '-' }}</span></template>
          <template #status="{row}"><Badge :type="statusBadge(row.status)">{{ statusText(row.status) }}</Badge></template>
          <template #failed="{row}">
            <Badge v-if="row.status === 'fail'" type="red">失败</Badge>
            <Badge v-else-if="row.status === 'success'" type="green">成功</Badge>
            <Badge v-else type="orange">处理中</Badge>
          </template>
          <template #failReason="{row}">
            <span v-if="row.status === 'fail' && row.errorMessage" :title="row.errorMessage" class="cell-truncate fail-text">{{ row.errorMessage }}</span>
            <span v-else-if="row.status === 'fail'" class="cell-truncate fail-text">{{ row.result === 'slider_success' ? '滑块已通过但 Cookie Session 已过期' : '滑块验证未通过' }}</span>
            <span v-else>-</span>
          </template>
          <template #empty><EmptyState icon="🧩" title="暂无滑块求解记录" description="滑块验证记录将在此显示。" /></template>
        </BaseTable>
        <Pagination :total="total" :current="current" :page-size="size" @page-change="goPage" />
      </CardPanel>
    </div>
    <div class="right-drawer">
      <template v-if="detail">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px">
          <h3>记录详情</h3>
          <button class="modal-close" @click="detail=null"><Icon name="close" /></button>
        </div>
        <p>记录 ID：<b>{{ detail.id || detail.recordId || '-' }}</b></p>
        <div class="grid" style="grid-template-columns:repeat(2,1fr);gap:10px">
          <div class="metric-tile"><span>账号ID</span><b :title="detail.accountId">{{ detail.accountId || '-' }}</b></div>
          <div class="metric-tile"><span>账号名称</span><b :title="detail.accountName">{{ detail.accountName || '-' }}</b></div>
          <div class="metric-tile"><span>处理状态</span><Badge :type="statusBadge(detail.status)">{{ statusText(detail.status) }}</Badge></div>
          <div class="metric-tile"><span>是否失败</span>
            <Badge v-if="detail.status === 'fail'" type="red">失败</Badge>
            <Badge v-else-if="detail.status === 'success'" type="green">成功</Badge>
            <Badge v-else type="orange">处理中</Badge>
          </div>
          <div class="metric-tile"><span>处理结果</span><Badge :type="resultBadge(detail.result)">{{ resultText(detail.result) }}</Badge></div>
          <div class="metric-tile"><span>验证引擎</span><b :title="detail.engine">{{ detail.engine || '-' }}</b></div>
          <div class="metric-tile"><span>触发场景</span><b :title="detail.triggerScene">{{ triggerSceneText(detail.triggerScene) }}</b></div>
          <div class="metric-tile"><span>重试次数</span><b>{{ detail.retryCount ?? 0 }}</b></div>
        </div>
        <div class="option-line"><span>记录时间</span><b>{{ formatDateTime(detail.createdAt) }}</b></div>
        <div class="option-line"><span>更新时间</span><b>{{ formatDateTime(detail.updatedAt) }}</b></div>
        <div class="option-line"><span>事件描述</span><b>{{ detail.eventDesc || '-' }}</b></div>
        <div class="option-line"><span>耗时</span><b>{{ formatDuration(detail.errorMessage) }}</b></div>
        <div class="option-line option-line-block">
          <span>开启原因</span>
          <div class="option-content">{{ detail.openReason || '-' }}</div>
        </div>
        <div class="option-line option-line-block">
          <span>求解原因</span>
          <div class="option-content">{{ detail.solveReason || '-' }}</div>
        </div>
        <div v-if="extractScreenshot(detail.errorMessage)" class="option-line option-line-block">
          <span>调试截图</span>
          <div class="option-content mono">{{ extractScreenshot(detail.errorMessage) }}</div>
        </div>
        <div v-if="detail.status === 'fail'" class="error-message">
          <div class="error-message-head">失败原因</div>
          <pre class="error-message-body">{{ stripMeta(detail.errorMessage) || (detail.result === 'slider_success' ? '滑块已通过但 Cookie Session 已过期，需重新扫码登录' : '滑块验证未通过') }}</pre>
        </div>
        <div v-else-if="detail.status === 'success' && stripMeta(detail.errorMessage)" class="option-line option-line-block">
          <span>备注</span>
          <div class="option-content">{{ stripMeta(detail.errorMessage) }}</div>
        </div>
      </template>
      <EmptyState v-else icon="🧩" title="选择记录查看详情" description="点击左侧列表中的任意一行，这里会展示该滑块求解记录的完整信息。" />
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import StatCard from '../components/StatCard.vue'
import CardPanel from '../components/CardPanel.vue'
import BaseTable from '../components/BaseTable.vue'
import Badge from '../components/Badge.vue'
import AppButton from '../components/AppButton.vue'
import EmptyState from '../components/EmptyState.vue'
import Pagination from '../components/Pagination.vue'
import Icon from '../components/Icon.vue'
import { getCaptchaRecords } from '../api/captcha.js'

const loading = ref(false)
const loadError = ref('')
const rows = ref([])
const total = ref(0)
const current = ref(1)
const size = ref(20)
const detail = ref(null)
const filters = reactive({ status: '', triggerScene: '' })

const cols = [
  { key: 'createdAt', title: '记录时间' },
  { key: 'accountId', title: '账号ID' },
  { key: 'accountName', title: '账号名称' },
  { key: 'openReason', title: '开启原因' },
  { key: 'solveReason', title: '求解原因' },
  { key: 'status', title: '求解状态' },
  { key: 'failed', title: '是否失败' },
  { key: 'failReason', title: '失败原因' }
]

const successCount = computed(() => rows.value.filter(r => r.status === 'success').length)
const failedCount = computed(() => rows.value.filter(r => r.status === 'fail').length)
const retryingCount = computed(() => rows.value.filter(r => r.status === 'retrying').length)

function resultText(result) {
  if (result === 'slider_success') return '滑块成功'
  if (result === 'slider_fail') return '滑块失败'
  return '未求解'
}
function resultBadge(result) {
  if (result === 'slider_success') return 'green'
  if (result === 'slider_fail') return 'red'
  return 'gray'
}
function statusText(status) {
  if (status === 'success') return '成功'
  if (status === 'fail') return '失败'
  if (status === 'retrying') return '处理中'
  return status || '-'
}
function statusBadge(status) {
  if (status === 'success') return 'green'
  if (status === 'fail') return 'red'
  if (status === 'retrying') return 'orange'
  return 'gray'
}
function triggerSceneText(scene) {
  const map = {
    manual: '手动触发',
    manual_retry: '手动重试',
    ws_connect: 'WS 连接',
    cookie_keepalive: 'Cookie 保活',
    token_refresh: 'Token 刷新',
  }
  return map[scene] || scene || '-'
}

function formatDateTime(value) {
  if (!value) return '-'
  return String(value).replace('T', ' ').replace(/\.\d+$/, '').slice(0, 19)
}

/** 从 error_message 元数据前缀解析 durationMs */
function formatDuration(errorMessage) {
  const m = String(errorMessage || '').match(/durationMs=(\d+)/i)
  if (!m) return '-'
  const ms = Number(m[1])
  if (!Number.isFinite(ms) || ms < 0) return '-'
  if (ms < 1000) return `${ms} ms`
  return `${(ms / 1000).toFixed(1)} s`
}

function extractScreenshot(errorMessage) {
  const m = String(errorMessage || '').match(/screenshot=([^\s\]]+)/i)
  return m ? m[1] : ''
}

function stripMeta(errorMessage) {
  if (!errorMessage) return ''
  return String(errorMessage).replace(/^\[[^\]]*\]\s*/, '').trim()
}

async function load() {
  loading.value = true
  loadError.value = ''
  rows.value = []
  total.value = 0
  detail.value = null
  try {
    const params = {
      page: current.value,
      pageSize: size.value,
      status: filters.status,
    }
    if (filters.triggerScene) params.triggerScene = filters.triggerScene
    const res = await getCaptchaRecords(params)
    // 开源版无 Java 网关拆包，直接返回 { code, data: { list, total, ... } }
    const payload = res?.data?.list || res?.data?.total != null ? res.data : (res || {})
    const list = Array.isArray(payload.list) ? payload.list : []
    rows.value = list
    total.value = Number(payload.total) || 0
  } catch (e) {
    loadError.value = e?.message || '滑块记录加载失败'
  } finally {
    loading.value = false
  }
}

function goPage(p) {
  current.value = p
  load()
}

function search() {
  current.value = 1
  load()
}

function showDetail(row) { detail.value = row }

// ============================================================
// SSE 事件监听：收到 captcha_solve 事件时自动刷新记录列表
// ============================================================
// 用户反馈：手动点击滑块求解后，本页未显示新增记录。
// 原因：页面仅在 onMounted 时加载一次，不感知后端写入的新记录。
// 修复：监听全局 SSE 事件 captcha_solve（与 useCaptchaSolver.js 一致的事件源），
//       收到事件后刷新列表。为避免 retrying→success/fail 两次事件导致重复请求，加 800ms 防抖。
let refreshTimer = null
function scheduleRefresh() {
  if (refreshTimer) clearTimeout(refreshTimer)
  refreshTimer = setTimeout(() => {
    refreshTimer = null
    // 仅当用户未离开本页且非查询中时刷新，避免与手动查询冲突
    if (!loading.value) load()
  }, 800)
}

function onSseCaptchaSolve(event) {
  const evtDetail = event?.detail
  const data = evtDetail?.payload || evtDetail || {}
  const eventType = evtDetail?.type || data.type || ''
  if (eventType !== 'captcha_solve') return
  scheduleRefresh()
}

onMounted(() => {
  load()
  window.addEventListener('xya-sse-event', onSseCaptchaSolve)
})

onUnmounted(() => {
  window.removeEventListener('xya-sse-event', onSseCaptchaSolve)
  if (refreshTimer) {
    clearTimeout(refreshTimer)
    refreshTimer = null
  }
})
</script>

<style scoped>
.slider-layout :deep(.stat-grid) {
  grid-template-columns: repeat(4, 1fr);
}
@media (max-width: 1500px) {
  .slider-layout :deep(.stat-grid) {
    grid-template-columns: repeat(2, 1fr);
  }
}
.slider-layout :deep(.base-table tbody tr) {
  cursor: pointer;
  transition: background .15s;
}
.slider-layout :deep(.base-table tbody tr:hover) {
  background: #f3f8ff;
}
.cell-truncate {
  display: inline-block;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: middle;
}
.fail-text {
  color: #ef4444;
}
.option-line-block {
  flex-direction: column;
  align-items: flex-start;
  gap: 6px;
}
.option-line-block .option-content {
  width: 100%;
  padding: 8px 12px;
  background: #f6f8fa;
  border-radius: 8px;
  font-size: 13px;
  line-height: 1.6;
  color: #475569;
  word-break: break-word;
  white-space: pre-wrap;
}
.error-message {
  margin-top: 14px;
  border: 1px solid #ffd1d1;
  border-radius: 10px;
  background: linear-gradient(135deg, #fff8f8, #fff5f5);
  overflow: hidden;
}
.error-message-head {
  padding: 8px 14px;
  font-size: 13px;
  font-weight: 700;
  color: #ef4444;
  border-bottom: 1px solid #ffd1d1;
}
.error-message-body {
  margin: 0;
  padding: 12px 14px;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 13px;
  line-height: 1.6;
  color: #526079;
  font-family: inherit;
}
.rules-card {
  padding: 16px 18px;
  background: linear-gradient(180deg, #f8fafc 0%, #f1f5f9 100%);
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  margin: 4px 0 14px;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
}
.rules-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 14px;
  padding-bottom: 10px;
  border-bottom: 1px dashed #e2e8f0;
}
.rules-head::before {
  content: '';
  width: 3px;
  height: 14px;
  background: linear-gradient(180deg, #3b82f6, #6366f1);
  border-radius: 2px;
}
.rules-title {
  font-size: 14px;
  font-weight: 700;
  color: #1e293b;
  letter-spacing: 0.3px;
}
.rules-sub {
  font-size: 12px;
  color: #94a3b8;
  margin-left: auto;
}
.rules-callout {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 12px 14px;
  background: linear-gradient(135deg, #eff6ff 0%, #e0f2fe 100%);
  border: 1px solid #bfdbfe;
  border-left: 3px solid #3b82f6;
  border-radius: 8px;
  margin-bottom: 14px;
  transition: box-shadow 0.2s;
}
.rules-callout:hover {
  box-shadow: 0 2px 8px rgba(59, 130, 246, 0.12);
}
.rules-callout strong {
  display: block;
  font-size: 13px;
  color: #1e40af;
  margin-bottom: 4px;
  font-weight: 600;
}
.rules-callout span {
  font-size: 12.5px;
  color: #475569;
  line-height: 1.6;
}
.rules-badge {
  flex-shrink: 0;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 700;
  font-style: italic;
  color: #fff;
  margin-top: 1px;
  background: linear-gradient(135deg, #3b82f6, #6366f1);
  box-shadow: 0 1px 3px rgba(59, 130, 246, 0.3);
}
.badge-blue { background: linear-gradient(135deg, #3b82f6, #6366f1); }
.rules-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 10px 22px;
}
.rule-item {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  font-size: 12.5px;
  line-height: 1.85;
  color: #475569;
  padding: 4px 6px;
  border-radius: 6px;
  transition: background 0.15s;
}
.rule-item:hover {
  background: rgba(255, 255, 255, 0.6);
}
.rule-item strong {
  color: #1e293b;
  font-weight: 600;
}
.rules-dot {
  flex-shrink: 0;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-top: 7px;
  position: relative;
}
.rules-dot::after {
  content: '';
  position: absolute;
  inset: -3px;
  border-radius: 50%;
  background: inherit;
  opacity: 0.18;
  z-index: -1;
}
.dot-red { background: #ef4444; }
.dot-purple { background: #8b5cf6; }
.dot-orange { background: #f59e0b; }
.dot-blue { background: #3b82f6; }
.dot-green { background: #10b981; }
.dot-gray { background: #94a3b8; }
@media (max-width: 768px) {
  .rules-grid { grid-template-columns: 1fr; }
  .rules-card { padding: 14px; }
}
</style>
