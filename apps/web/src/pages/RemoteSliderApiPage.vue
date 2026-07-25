<template>
  <div class="remote-slider-page">
    <!-- 预检验错误提示 -->
    <div v-if="precheckErrors.length" class="precheck-errors">
      <div v-for="error in precheckErrors" :key="error.id" class="precheck-error-item">
        <span class="precheck-error-icon">⚠</span>
        <span>{{ error.message }}</span>
      </div>
    </div>

    <!-- 1. 远程滑块求解开关 -->
    <CardPanel>
      <div class="toggle-row">
        <div class="toggle-info">
          <div class="toggle-label">
            <span class="toggle-name">远程滑块求解服务</span>
            <Badge :type="config.enabled ? 'green' : 'gray'">{{ config.enabled ? '已开启' : '未开启' }}</Badge>
          </div>
          <p class="toggle-desc">
            开启后，项目中所有的滑块求解将自动切换为远程滑块求解服务；不开启则使用本地开源版滑块求解服务。
          </p>
        </div>
        <ToggleSwitch :on="config.enabled" :disabled="!authed || prechecking" @click="onToggle" />
        <div v-if="prechecking" class="precheck-hint">正在预检验远程服务连通性...</div>
      </div>
      <div class="notice-block">
        <div class="notice-item">
          <div class="notice-icon notice-icon-blue">i</div>
          <div>
            <strong>服务说明：</strong>
            <span>本页面可以开启远程滑块求解服务；不开启则使用本地开源版滑块求解服务。本地开源版滑块求解服务能力较弱，如需使用，可以对接我们的 API 滑块求解服务。目前通过率在 80% 以上。</span>
          </div>
        </div>
      </div>
    </CardPanel>

    <!-- 2. 配置信息 -->
    <CardPanel title="配置信息" desc="远程滑块求解 API 对接配置">
      <div class="form-row">
        <label>API 链接（URL）</label>
        <div class="input-group">
          <input v-model="form.apiUrl" class="form-input" placeholder="https://your-slider-api.example.com/api/v1/slider/solve" />
        </div>
      </div>
      <div class="form-row">
        <label>对接密钥（API Key）</label>
        <div class="input-group">
          <input v-model="form.apiKey" type="password" class="form-input" :placeholder="config.apiKeyConfigured ? '已配置（如需修改请输入新密钥）' : '请输入对接密钥'" />
          <button class="mini-btn" @click="copyText(form.apiKey, '密钥')" v-if="form.apiKey">复制</button>
        </div>
        <p class="form-hint" v-if="config.apiKeyConfigured && !form.apiKey">密钥已配置，留空保存则保留原有密钥</p>
      </div>
      <div class="form-row">
        <label>自动触发场景</label>
        <p class="form-hint" style="margin-bottom: 10px;">选择在哪些场景下自动发起远程滑块求解，WS 失效为必选项不可关闭</p>
        <div class="trigger-scenes">
          <label class="trigger-item" :class="{ 'trigger-disabled': true }">
            <input type="checkbox" :checked="true" :disabled="true" />
            <span class="trigger-text">
              <span class="trigger-name">WS 失效</span>
              <span class="trigger-tag">必选</span>
            </span>
            <span class="trigger-desc">WebSocket Token API 返回滑块验证或 Cookie 过期时触发</span>
          </label>
          <label class="trigger-item">
            <input type="checkbox" v-model="form.triggerScenes" value="cookie_keepalive" />
            <span class="trigger-text">
              <span class="trigger-name">Cookie 保活策略</span>
            </span>
            <span class="trigger-desc">Cookie 30 分钟保活检测到滑块验证时触发</span>
          </label>
          <label class="trigger-item">
            <input type="checkbox" v-model="form.triggerScenes" value="heartbeat_stop" />
            <span class="trigger-text">
              <span class="trigger-name">心跳停跳</span>
            </span>
            <span class="trigger-desc">WebSocket 45 秒未收到服务端消息（静默断开）时触发</span>
          </label>
        </div>
      </div>
      <div class="form-actions">
        <AppButton type="primary" :loading="saving" @click="saveConfig">保存配置</AppButton>
      </div>
    </CardPanel>

    <!-- 3. 新手教学 -->
    <CardPanel title="新手教学" desc="如何配置远程滑块求解服务">
      <ol class="tutorial-list">
        <li>
          <strong>注册账号：</strong>
          前往商业版前台官网注册账号：<a :href="commercialFrontendUrl" target="_blank" rel="noopener noreferrer" class="link">{{ commercialFrontendUrl }}</a>（注册成功后联系商业版服务方获取 API 对接凭证）。
        </li>
        <li>
          <strong>充值 Token：</strong>
          注册成功后登录账号，在个人中心充值 Token，Token 用于扣费滑块求解调用。
        </li>
        <li>
          <strong>获取配置链接与对接密钥：</strong>
          进入前台「API 对接」页面，获取 API 链接（URL）与对接密钥（API Key）。
        </li>
        <li>
          <strong>填入配置：</strong>
          将获取到的 API 链接和对接密钥填入上方「配置信息」板块，点击保存。
        </li>
        <li>
          <strong>开启服务：</strong>
          在页面顶部开启「远程滑块求解服务」开关，所有滑块求解将自动使用远程服务。
        </li>
      </ol>
    </CardPanel>

    <!-- 4. API 计费说明 -->
    <CardPanel title="API 计费说明" desc="计费范围、扣费保证、服务支持">
      <div class="billing-section">
        <div class="billing-item">
          <div class="billing-icon billing-icon-blue">¥</div>
          <div>
            <strong>计费范围：</strong>
            <span>仅对成功求解的滑块任务扣除 Token，扣费价格随官网定价，具体请前往官网查看。失败、预检测未通过、超时、服务不可用等情况一律不扣费。</span>
          </div>
        </div>
        <div class="billing-item">
          <div class="billing-icon billing-icon-green">✓</div>
          <div>
            <strong>扣费保证：</strong>
            <span>仅对成功求解的滑块任务扣除 Token，失败不扣费。如遇疑似误扣费，可联系客服处理。</span>
          </div>
        </div>
        <div class="billing-item">
          <div class="billing-icon billing-icon-orange">?</div>
          <div>
            <strong>服务支持：</strong>
            <span>如果对功能有建议、反馈，或发现疑似误扣费，可联系客服技术人员。客服微信：<b class="wechat">JiShu0724</b></span>
          </div>
        </div>
      </div>
    </CardPanel>

    <!-- 5. Token 消费说明 -->
    <CardPanel title="Token 消费说明" desc="成功才扣费，失败不扣费">
      <div class="mini-stats">
        <div class="mini-card">
          <div class="mini-value">{{ stats.today?.chargedTokens ?? 0 }}</div>
          <div class="mini-label">今日消耗</div>
        </div>
        <div class="mini-card">
          <div class="mini-value">{{ stats.kpi?.chargedTokens ?? 0 }}</div>
          <div class="mini-label">累计消耗</div>
        </div>
        <div class="mini-card">
          <div class="mini-value">{{ successRate }}%</div>
          <div class="mini-label">近7天成功率</div>
        </div>
      </div>
      <div class="data-source-note">
        <div class="data-source-icon">ⓘ</div>
        <div class="data-source-text">
          <strong>数据来源：</strong>
          <span>本页统计数据基于商业版远程滑块求解服务实时返回的真实扣费结果，仅统计成功求解且实际扣费的记录。失败、超时、预检验拒绝、服务不可用等均不计入 Token 消耗。</span>
        </div>
      </div>
      <div class="legend">
        <div class="legend-item"><span class="dot dot-green"></span>成功扣费</div>
        <div class="legend-item"><span class="dot dot-red"></span>失败不扣费</div>
        <div class="legend-item"><span class="dot dot-orange"></span>预检测不扣费</div>
        <div class="legend-item"><span class="dot dot-purple"></span>超时不扣费</div>
      </div>
    </CardPanel>

    <!-- 6. API 计费记录和 Token 消费记录 -->
    <CardPanel title="记录" desc="API 滑块求解记录与 Token 消费记录">
      <div class="tabs">
        <button :class="['tab', { active: activeTab === 'solve' }]" @click="activeTab = 'solve'">API 滑块求解记录</button>
        <button :class="['tab', { active: activeTab === 'token' }]" @click="activeTab = 'token'">Token 消费记录</button>
      </div>

      <div class="toolbar" v-if="activeTab === 'solve'">
        <select v-model="filters.status" @change="search">
          <option value="">全部状态</option>
          <option value="success">成功</option>
          <option value="fail">失败</option>
          <option value="timeout">超时</option>
          <option value="precheck_rejected">预检验拒绝</option>
          <option value="service_unavailable">服务不可用</option>
        </select>
        <input v-model="filters.keyword" placeholder="搜索记录编号/错误信息" @keyup.enter="search" />
        <AppButton type="primary" @click="search">查询</AppButton>
      </div>

      <div v-if="activeTab === 'solve'">
        <BaseTable :columns="solveCols" :rows="rows">
          <template #status="{ row }">
            <Badge :type="statusBadge(row.status)">{{ statusText(row.status) }}</Badge>
          </template>
          <template #durationMs="{ row }">
            {{ row.durationMs ? row.durationMs + 'ms' : '—' }}
          </template>
          <template #tokenCharged="{ row }">
            {{ row.tokenCharged }}
          </template>
          <template #createdAt="{ row }">{{ formatDateTime(row.createdAt) }}</template>
          <template #empty>
            <div class="table-empty">暂无求解记录</div>
          </template>
        </BaseTable>
        <Pagination :current="current" :total="total" :page-size="size" @page-change="goPage" />
      </div>

      <div v-else>
        <BaseTable :columns="tokenCols" :rows="tokenRows">
          <template #status="{ row }">
            <Badge :type="statusBadge(row.status)">{{ statusText(row.status) }}</Badge>
          </template>
          <template #createdAt="{ row }">{{ formatDateTime(row.createdAt) }}</template>
          <template #empty>
            <div class="table-empty">暂无消费记录</div>
          </template>
        </BaseTable>
        <Pagination :current="current" :total="tokenRows.length" :page-size="size" />
      </div>
    </CardPanel>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { getToken } from '../utils/auth.js'
import CardPanel from '../components/CardPanel.vue'
import ToggleSwitch from '../components/ToggleSwitch.vue'
import BaseTable from '../components/BaseTable.vue'
import Badge from '../components/Badge.vue'
import AppButton from '../components/AppButton.vue'
import Pagination from '../components/Pagination.vue'
import {
  getRemoteSliderConfig,
  saveRemoteSliderConfig,
  precheckRemoteSlider,
  getRemoteSolveRecords,
  getRemoteSolveStats,
} from '../api/remoteSlider.js'

// 商业版前台引流地址（允许在开源版前台展示，用于引导用户注册商业版账号）
const commercialFrontendUrl = 'https://www.xianyupilot.com/'

const config = reactive({
  enabled: false,
  apiUrl: '',
  apiKey: '',
  apiKeyConfigured: false,
  apiUrlConfigured: false,
  triggerScenes: ['ws_failure'],
})
const form = reactive({ apiUrl: '', apiKey: '', triggerScenes: ['ws_failure'] })
const saving = ref(false)
const prechecking = ref(false)
const precheckErrors = ref([])
const authed = computed(() => !!getToken())

// 记录相关
const activeTab = ref('solve')
const rows = ref([])
const total = ref(0)
const current = ref(1)
const size = ref(20)
const filters = reactive({ status: '', keyword: '' })
const stats = reactive({ kpi: {}, today: {}, trend: [] })

const solveCols = [
  { key: 'requestId', title: '记录编号' },
  { key: 'accountName', title: '账号名称' },
  { key: 'triggerScene', title: '触发场景' },
  { key: 'status', title: '状态' },
  { key: 'failureReason', title: '失败原因' },
  { key: 'durationMs', title: '耗时' },
  { key: 'tokenCharged', title: 'Token消耗' },
  { key: 'createdAt', title: '创建时间' },
]

const tokenCols = [
  { key: 'requestId', title: '记录编号' },
  { key: 'accountName', title: '账号名称' },
  { key: 'status', title: '状态' },
  { key: 'tokenCharged', title: 'Token消耗' },
  { key: 'createdAt', title: '创建时间' },
]

const tokenRows = computed(() => rows.value.filter(r => Number(r.tokenCharged) > 0))

const successRate = computed(() => {
  const kpi = stats.kpi || {}
  const totalNum = Number(kpi.total ?? 0)
  const success = Number(kpi.successCount ?? 0)
  if (totalNum === 0) return 0
  return Math.round((success / totalNum) * 100)
})

function statusText(status) {
  const map = {
    success: '成功',
    fail: '失败',
    retrying: '求解中',
    timeout: '超时',
    precheck_rejected: '预检验拒绝',
    service_unavailable: '服务不可用',
  }
  return map[status] || status || '-'
}
function statusBadge(status) {
  const map = {
    success: 'green',
    fail: 'red',
    retrying: 'orange',
    timeout: 'gray',
    precheck_rejected: 'orange',
    service_unavailable: 'gray',
  }
  return map[status] || 'gray'
}

function triggerSceneText(scene) {
  const map = {
    manual: '手动触发',
    manual_retry: '手动重试',
    ws_connect: 'WS 连接',
    cookie_keepalive: 'Cookie 保活',
    heartbeat_stop: '心跳停跳',
    token_refresh: 'Token 刷新',
  }
  return map[scene] || scene || '-'
}

function formatDateTime(value) {
  if (!value) return '-'
  return String(value).replace('T', ' ').replace(/\.\d+$/, '').slice(0, 19)
}

async function copyText(text, label) {
  if (!text) {
    window.dispatchEvent(new CustomEvent('xya-toast', { detail: { message: '暂无内容可复制', isError: true } }))
    return
  }
  try {
    await navigator.clipboard.writeText(text)
    window.dispatchEvent(new CustomEvent('xya-toast', { detail: { message: `${label}已复制` } }))
  } catch {
    window.dispatchEvent(new CustomEvent('xya-toast', { detail: { message: '复制失败，请手动复制', isError: true } }))
  }
}

async function loadConfig() {
  try {
    const res = await getRemoteSliderConfig()
    const data = res?.data || res || {}
    config.enabled = Boolean(data.enabled)
    config.apiUrl = data.apiUrl || ''
    config.apiKey = ''
    config.apiKeyConfigured = Boolean(data.apiKeyConfigured)
    config.apiUrlConfigured = Boolean(data.apiUrlConfigured)
    // triggerScenes：后端保证 ws_failure 始终存在
    const scenes = Array.isArray(data.triggerScenes) ? data.triggerScenes : ['ws_failure']
    config.triggerScenes = scenes
    form.apiUrl = config.apiUrl
    form.apiKey = ''
    form.triggerScenes = [...scenes]
  } catch (e) {
    if (e?.response?.status === 401) {
      window.dispatchEvent(new CustomEvent('xya-toast', { detail: { message: '请先登录后查看配置', isError: true } }))
    }
  }
}

async function onToggle() {
  if (!authed.value) {
    window.dispatchEvent(new CustomEvent('xya-toast', { detail: { message: '请先登录后再操作', isError: true } }))
    return
  }
  const oldVal = config.enabled
  const newVal = !config.enabled
  // 关闭时直接保存，无需预检验
  if (!newVal) {
    precheckErrors.value = []
    config.enabled = newVal
    try {
      await saveRemoteSliderConfig({
        enabled: newVal,
        apiUrl: form.apiUrl || config.apiUrl,
        apiKey: form.apiKey,
        triggerScenes: form.triggerScenes,
      })
      window.dispatchEvent(new CustomEvent('xya-toast', { detail: { message: '已切换为本地求解' } }))
    } catch (e) {
      config.enabled = oldVal
      const msg = e?.response?.data?.msg || e?.response?.statusText || '切换失败，请重试'
      window.dispatchEvent(new CustomEvent('xya-toast', { detail: { message: msg, isError: true } }))
    }
    return
  }
  // 开启时先预检验
  precheckErrors.value = []
  const apiUrl = form.apiUrl || config.apiUrl
  const apiKey = form.apiKey
  const errors = []
  if (!apiUrl) {
    errors.push({ id: 'api_url', message: '请先填写 API 链接' })
  }
  if (!apiKey && !config.apiKeyConfigured) {
    errors.push({ id: 'api_key', message: '请先填写对接密钥' })
  }
  if (errors.length) {
    precheckErrors.value = errors
    errors.forEach(e => {
      window.dispatchEvent(new CustomEvent('xya-toast', { detail: { message: e.message, isError: true } }))
    })
    return
  }
  prechecking.value = true
  try {
    const res = await precheckRemoteSlider({ apiUrl, apiKey })
    const data = res?.data || res || {}
    if (data.ok) {
      precheckErrors.value = []
      config.enabled = newVal
      await saveRemoteSliderConfig({
        enabled: newVal,
        apiUrl,
        apiKey,
        triggerScenes: form.triggerScenes,
      })
      window.dispatchEvent(new CustomEvent('xya-toast', { detail: { message: '预检验通过，远程滑块求解已开启' } }))
    } else {
      precheckErrors.value = [{ id: 'precheck', message: data.message || '预检验失败，无法开启远程滑块求解' }]
      window.dispatchEvent(new CustomEvent('xya-toast', { detail: { message: data.message || '预检验失败，无法开启远程滑块求解', isError: true } }))
    }
  } catch (e) {
    const msg = e?.response?.data?.msg || e?.response?.statusText || '预检验失败，无法开启远程滑块求解'
    precheckErrors.value = [{ id: 'precheck', message: msg }]
    window.dispatchEvent(new CustomEvent('xya-toast', { detail: { message: msg, isError: true } }))
  } finally {
    prechecking.value = false
  }
}

async function saveConfig() {
  saving.value = true
  try {
    const res = await saveRemoteSliderConfig({
      enabled: config.enabled,
      apiUrl: form.apiUrl,
      apiKey: form.apiKey,
      triggerScenes: form.triggerScenes,
    })
    const data = res?.data || res || {}
    config.apiUrl = data.apiUrl || form.apiUrl
    config.apiKeyConfigured = Boolean(data.apiKeyConfigured)
    // 同步后端规范化后的 triggerScenes（确保 ws_failure 始终存在）
    const scenes = Array.isArray(data.triggerScenes) ? data.triggerScenes : ['ws_failure']
    config.triggerScenes = scenes
    form.triggerScenes = [...scenes]
    form.apiKey = ''
    window.dispatchEvent(new CustomEvent('xya-toast', { detail: { message: '配置已保存' } }))
  } catch (e) {
    const msg = e?.response?.data?.msg || (e?.response?.status === 401 ? '登录已过期，请重新登录' : '保存失败，请重试')
    window.dispatchEvent(new CustomEvent('xya-toast', { detail: { message: msg, isError: true } }))
  } finally {
    saving.value = false
  }
}

async function loadRecords() {
  try {
    const params = {
      page: current.value,
      pageSize: size.value,
      status: filters.status,
      keyword: filters.keyword,
    }
    const res = await getRemoteSolveRecords(params)
    const data = res?.data || res || {}
    rows.value = data.list || []
    total.value = Number(data.total) || 0
  } catch (e) {
    rows.value = []
    total.value = 0
  }
}

async function loadStats() {
  try {
    const res = await getRemoteSolveStats({ days: 7 })
    const data = res?.data || res || {}
    stats.kpi = data.kpi || {}
    stats.today = data.today || {}
    stats.trend = data.trend || []
  } catch (e) {
    // ignore
  }
}

function goPage(p) {
  current.value = p
  loadRecords()
}
function search() {
  current.value = 1
  loadRecords()
}

onMounted(() => {
  loadConfig()
  loadRecords()
  loadStats()
})
</script>

<style scoped>
.remote-slider-page { display: flex; flex-direction: column; gap: 16px; }
.toggle-row { display: flex; justify-content: space-between; align-items: center; gap: 16px; padding: 8px 0; }
.toggle-info { flex: 1; }
.toggle-label { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.toggle-name { font-size: 15px; font-weight: 600; }
.toggle-desc { font-size: 13px; color: var(--muted); line-height: 1.6; margin: 0; }
.precheck-hint { font-size: 12px; color: var(--primary); margin-top: 8px; }
.notice-block { margin-top: 16px; padding-top: 16px; border-top: 1px solid var(--line); }
.notice-item { display: flex; gap: 10px; margin-bottom: 12px; font-size: 13px; line-height: 1.6; color: var(--text); }
.notice-item:last-child { margin-bottom: 0; }
.notice-icon { width: 22px; height: 22px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 12px; font-weight: 700; flex-shrink: 0; }
.notice-icon-blue { background: #e3f2fd; color: #1976d2; }
.form-row { margin-bottom: 16px; }
.form-row label { display: block; font-size: 13px; color: var(--muted); margin-bottom: 6px; }
.input-group { display: flex; gap: 8px; }
.form-input { flex: 1; height: 36px; padding: 0 12px; border: 1px solid var(--line); border-radius: 6px; font-size: 13px; background: #fff; color: var(--text); }
.form-input:focus { outline: none; border-color: var(--primary); }
.mini-btn { border: 1px solid var(--primary); background: #fff; color: var(--primary); border-radius: 6px; padding: 0 12px; cursor: pointer; font-size: 12px; }
.form-hint { font-size: 12px; color: var(--muted); margin-top: 4px; }
.form-actions { display: flex; gap: 8px; margin-top: 16px; }
.tutorial-list { padding-left: 20px; margin: 0; }
.tutorial-list li { margin-bottom: 12px; font-size: 13px; line-height: 1.7; color: var(--text); }
.link { color: var(--primary); text-decoration: none; }
.link:hover { text-decoration: underline; }
.billing-section { display: flex; flex-direction: column; gap: 14px; }
.billing-item { display: flex; gap: 10px; font-size: 13px; line-height: 1.6; color: var(--text); }
.billing-icon { width: 22px; height: 22px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 12px; font-weight: 700; flex-shrink: 0; }
.billing-icon-blue { background: #e3f2fd; color: #1976d2; }
.billing-icon-green { background: #e8f5e9; color: #2e7d32; }
.billing-icon-orange { background: #fff3e0; color: #ef6c00; }
.wechat { color: var(--primary); }
.mini-stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-bottom: 16px; }
.mini-card { text-align: center; padding: 12px; background: #f5f8ff; border-radius: 8px; }
.mini-value { font-size: 22px; font-weight: 600; color: var(--primary); }
.mini-label { font-size: 12px; color: var(--muted); margin-top: 4px; }
.legend { display: flex; flex-wrap: wrap; gap: 12px; font-size: 12px; color: var(--muted); }
.legend-item { display: flex; align-items: center; gap: 4px; }
.data-source-note { display: flex; gap: 10px; margin: 12px 0; padding: 10px 12px; background: #f0f7ff; border-radius: 6px; border-left: 3px solid var(--primary); font-size: 12px; line-height: 1.6; color: var(--text); }
.data-source-icon { font-size: 14px; flex-shrink: 0; }
.data-source-text { flex: 1; }
.dot { width: 10px; height: 10px; border-radius: 50%; }
.dot-green { background: #22c55e; }
.dot-red { background: #ef4444; }
.dot-orange { background: #f59e0b; }
.dot-purple { background: #8b5cf6; }
.tabs { display: flex; gap: 8px; margin-bottom: 16px; }
.tab { padding: 8px 16px; border: 1px solid var(--line); background: #fff; border-radius: 6px; cursor: pointer; font-size: 13px; color: var(--text); }
.tab.active { background: var(--primary); color: #fff; border-color: var(--primary); }
.toolbar { display: flex; gap: 8px; margin-bottom: 16px; flex-wrap: wrap; }
.toolbar select, .toolbar input { height: 34px; padding: 0 10px; border: 1px solid var(--line); border-radius: 6px; font-size: 13px; }
.toolbar input { flex: 1; min-width: 180px; }
.table-empty { text-align: center; padding: 32px; color: var(--muted); font-size: 13px; }
.precheck-errors { display: flex; flex-direction: column; gap: 8px; padding: 12px 16px; background: #fef2f2; border: 1px solid #fecaca; border-radius: 8px; }
.precheck-error-item { display: flex; align-items: center; gap: 8px; font-size: 13px; color: #dc2626; line-height: 1.5; }
.precheck-error-icon { font-size: 14px; flex-shrink: 0; }
.trigger-scenes { display: flex; flex-direction: column; gap: 10px; }
.trigger-item { display: flex; align-items: flex-start; gap: 10px; padding: 12px 14px; border: 1px solid var(--line); border-radius: 8px; cursor: pointer; transition: border-color 0.15s, background 0.15s; }
.trigger-item:hover { border-color: var(--primary); background: #f5f8ff; }
.trigger-item.trigger-disabled { cursor: not-allowed; background: #f9fafb; }
.trigger-item.trigger-disabled:hover { border-color: var(--line); background: #f9fafb; }
.trigger-item input[type="checkbox"] { width: 16px; height: 16px; margin-top: 2px; cursor: pointer; flex-shrink: 0; }
.trigger-item.trigger-disabled input[type="checkbox"] { cursor: not-allowed; }
.trigger-text { display: flex; align-items: center; gap: 6px; flex-shrink: 0; }
.trigger-name { font-size: 13px; font-weight: 600; color: var(--text); }
.trigger-tag { font-size: 10px; padding: 1px 6px; border-radius: 4px; background: #e3f2fd; color: #1976d2; font-weight: 600; }
.trigger-desc { font-size: 12px; color: var(--muted); line-height: 1.5; flex: 1; }
</style>
