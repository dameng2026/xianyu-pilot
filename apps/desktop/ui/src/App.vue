<script setup>
import { computed, onMounted, onUnmounted, provide, ref } from 'vue'
import { api } from './workspace'
import AccountsPage from './pages/AccountsPage.vue'
import ServicesPage from './pages/ServicesPage.vue'
import ProductsPage from './pages/ProductsPage.vue'
import TasksPage from './pages/TasksPage.vue'

const pages = { tasks: '任务工作台', accounts: 'QQ 账号管理', services: 'QQ 服务管理', products: '商品处理配置' }
const page = ref(Object.hasOwn(pages, location.hash.slice(1)) ? location.hash.slice(1) : 'tasks')
const data = ref({ accounts: [], services: [], products: [], tasks: [], counts: {}, states: {}, total: 0 })
const error = ref(''), notice = ref(''), connected = ref(false), busy = ref(false), stopped = ref(false)
let timer, refreshing = false
const active = computed(() => Object.entries(data.value.counts).reduce((sum, [key, value]) => sum + (['completed', 'cancelled', 'delivery_stopped'].includes(key) ? 0 : value), 0))
async function refresh() {
  if (refreshing || stopped.value) return
  refreshing = true
  try {
    const result = await api('/api/workflow/state')
    if (!stopped.value) { data.value = result; connected.value = true }
    if (!result.worker_healthy) error.value = '工作流处理器已停止，请停止服务并重新启动，操作记录已保留。'
  } catch (err) { if (!stopped.value) { connected.value = false; error.value = err.message } }
  finally { refreshing = false }
}
async function run(callback, message = '已保存') {
  if (busy.value) return null
  busy.value = true; error.value = ''; notice.value = ''
  try {
    const result = await callback()
    notice.value = typeof message === 'function' ? message(result) : message
    await refresh(); return result
  } catch (err) { error.value = err.message; return null }
  finally { busy.value = false }
}
function navigate(key) { page.value = key; location.hash = key; error.value = ''; notice.value = '' }
function hashChanged() { if (Object.hasOwn(pages, location.hash.slice(1))) page.value = location.hash.slice(1) }
async function stop() {
  await run(async () => { const result = await api('/api/host/stop', {}); stopped.value = true; connected.value = false; clearInterval(timer); return result }, '服务已停止，记录已保留。双击启动脚本可重新运行。')
}
provide('workspace', { data, run, refresh, busy, connected, navigate })
onMounted(() => { refresh(); timer = setInterval(() => { if (!document.hidden) refresh() }, 1500); window.addEventListener('hashchange', hashChanged) })
onUnmounted(() => { clearInterval(timer); window.removeEventListener('hashchange', hashChanged) })
</script>

<template>
  <div class="layout">
    <aside class="sidebar">
      <a class="brand" href="#tasks"><span class="brand-mark">鱼</span><span>闲鱼助手<small>LOCAL WORKSPACE</small></span></a>
      <div class="workspace-label">我的工作区</div>
      <nav class="workflow-nav" aria-label="工作区导航"><button v-for="(label, key) in pages" :key="key" :class="{ current: page === key }" @click="navigate(key)">{{ label }}<span v-if="key === 'tasks'">{{ active }}</span></button></nav>
      <div class="sidebar-note"><span class="note-icon">↗</span><strong>账号属于你</strong><p>用自己的 QQ、配置自己的服务。商品保存默认规则，任务按快照执行。</p></div>
      <div class="sidebar-bottom"><span class="status-dot" :class="{ offline: !connected }"></span>{{ stopped ? '服务已停止' : connected ? '本地服务已连接' : '正在连接' }}<small>SQLite · 数据保存在本机</small></div>
    </aside>
    <main>
      <header class="topbar"><span>我的工作区 <span class="breadcrumb">/</span>{{ pages[page] }}</span><div class="topbar-actions"><span class="mode-pill">本机工作流</span><button class="text-button" :disabled="!connected || busy" @click="stop">停止服务</button></div></header>
      <div class="content">
        <nav class="mobile-nav" aria-label="移动端导航"><button v-for="(label, key) in pages" :key="key" :class="{ current: page === key }" @click="navigate(key)">{{ label }}</button></nav>
        <div class="heading"><div><div class="eyebrow">YOUR OWN ACCOUNTS · YOUR OWN RULES</div><h1>{{ pages[page] }}<span>。</span></h1><p>直接发货与 QQ 处理分别配置，每笔任务保留独立记录。</p></div><span class="sqlite-tag">用户自有账号</span></div>
        <div class="demo-banner"><span class="banner-icon">i</span><p>闲鱼真实接口尚未接入。交付步骤可选<strong>本地模拟</strong>或<strong>卖家人工确认</strong>；本项目提供独立 MCP 工具接口；QQ 消息连接器可替换，检测通过并明确配置后才会提交请求。模拟账号不代表真实 QQ 在线。</p></div>
        <div v-if="error" class="alert error" role="alert">{{ error }}<button class="text-button" @click="error = ''">关闭</button></div>
        <div v-if="notice" class="alert success" role="status">{{ notice }}</div>
        <TasksPage v-if="page === 'tasks'" /><AccountsPage v-else-if="page === 'accounts'" /><ServicesPage v-else-if="page === 'services'" /><ProductsPage v-else />
        <footer>本机工作区 · 无需 Docker / MySQL / Redis<span>不提供 QQ 号，不跨账号自动切换</span></footer>
      </div>
    </main>
  </div>
</template>
