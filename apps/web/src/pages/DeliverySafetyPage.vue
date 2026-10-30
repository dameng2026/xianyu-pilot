<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { label, mayRetryConfirmation, safetyRequest } from '../utils/deliverySafety.js'

const records = ref([]), selected = ref(null), session = ref(null), error = ref(''), busy = ref(false)
const onlyReview = ref(false), current = ref(1), total = ref(0), updated = ref('')
const monitors = ref([])
const needsReview = computed(() => records.value.filter(r => r.requiresManualCheck).length)
const canRetry = computed(() => mayRetryConfirmation(selected.value, session.value))
let timer, refreshing = false, disposed = false
async function choose(id) {
  try { selected.value = await safetyRequest(`/api/auto-delivery/records/${id}`) }
  catch (e) { error.value = e.message }
}
async function refresh() {
  if (refreshing || busy.value) return
  refreshing = true
  try {
    const page = await safetyRequest(`/api/auto-delivery/records?current=${current.value}&size=20&needsReview=${onlyReview.value}`)
    if (disposed) return
    records.value = page.records
    total.value = page.total
    monitors.value = await safetyRequest('/api/desktop/monitor-status')
    if (selected.value) await choose(selected.value.id)
    updated.value = new Date().toLocaleTimeString('zh-CN')
    error.value = ''
  } catch (e) { error.value = `${e.message}；保留上次显示的记录，请勿据此判断已完成。` }
  finally { refreshing = false }
}
async function changePage(delta) { current.value += delta; await refresh() }
async function filterChanged() { current.value = 1; await refresh() }
async function act(action) {
  if (busy.value || !selected.value || !session.value?.actionsAvailable) return
  busy.value = true
  error.value = ''
  let actionError = ''
  try {
    selected.value = await safetyRequest(`/api/desktop/safety/records/${selected.value.id}/${action}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Desktop-Action': session.value.token },
      body: JSON.stringify({ requestId: crypto.randomUUID() }),
    })
  } catch (e) { actionError = `${e.message}。结果不明时先查询官方状态，原卡密绑定持续保留。` }
  finally { busy.value = false; await refresh(); if (actionError) error.value = actionError }
}
onMounted(async () => {
  try { session.value = await safetyRequest('/api/desktop/safety/session', { headers: { 'X-Desktop-UI': 'delivery-safety' } }) }
  catch (e) { error.value = e.message }
  await refresh()
  timer = setInterval(refresh, 5000)
})
onUnmounted(() => { disposed = true; clearInterval(timer) })
</script>

<template>
  <main>
    <header><div><span class="eyebrow">闲鱼桌面端 / 本地资产保护</span><h1>发货安全工作台</h1><p>内容触达与平台履约分别记录，未知结果保留原订单绑定。</p></div><button :disabled="busy" @click="refresh">刷新记录</button></header>
    <aside v-if="!session?.actionsAvailable" class="notice">当前为本地只读模式：真实平台尚未连接。记录查询可用，官方核验与平台确认暂不可操作。</aside>
    <template v-for="monitor in monitors" :key="monitor.accountId"><aside v-if="['paused', 'blocked', 'reconciling', 'stock_blocked'].includes(monitor.state)" class="danger" role="alert">账号 #{{ monitor.accountId }}：{{ { paused: '连接或账号异常，已安全暂停', blocked: '发货核验受阻', reconciling: '正在执行恢复对账', stock_blocked: '库存不足，整单暂停' }[monitor.state] }}。待核对 {{ monitor.needsReviewCount }} 笔，缺货 {{ monitor.stockBlockedCount }} 笔。</aside></template>
    <aside v-if="needsReview" class="danger" role="alert">本页有 {{ needsReview }} 笔订单待人工核对。禁止释放未知卡密，禁止整单重发。</aside>
    <aside v-if="error" class="danger" role="alert">{{ error }}</aside>
    <section class="workspace">
      <section class="panel list">
        <div class="section-head"><h2>发货记录 <small>{{ total }} 笔</small></h2><label><input v-model="onlyReview" type="checkbox" @change="filterChanged"> 只看待核对</label></div>
        <div v-if="!records.length" class="empty">暂无{{ onlyReview ? '待核对' : '发货' }}记录</div>
        <button v-for="record in records" :key="record.id" class="record" :class="{ review: record.requiresManualCheck, active: selected?.id === record.id }" @click="choose(record.id)">
          <div class="record-title"><strong>{{ record.orderNumber }}</strong><span class="badge" :class="{ red: record.requiresManualCheck }">{{ label(record.safetyStatus) }}</span></div>
          <div class="phases"><span>A 内容：{{ label(record.contentState) }}（{{ record.confirmedSegments }}/{{ record.totalSegments }}）</span><span>B 平台：{{ label(record.confirmationState) }}</span></div>
          <small>{{ record.receiptMode === 'platform' ? '官方订单' : '离线模拟记录' }} · #{{ record.id }}</small>
        </button>
        <footer><button :disabled="current === 1 || busy" @click="changePage(-1)">上一页</button><span>第 {{ current }} 页</span><button :disabled="current * 20 >= total || busy" @click="changePage(1)">下一页</button></footer>
      </section>
      <section v-if="selected" class="panel detail">
        <div class="section-head"><h2>订单交付明细</h2><span class="badge">快照已锁定 · 只读</span></div>
        <p class="order-number">{{ selected.orderNumber }}</p>
        <aside v-if="selected.requiresManualCheck" class="danger" role="alert"><strong>NEED_MANUAL_CHECK · 待人工核对</strong><p>{{ selected.warning }}</p></aside>
        <div class="phase-grid"><div><small>阶段 A · 内容触达</small><strong>{{ label(selected.contentState) }}</strong></div><div><small>阶段 B · 平台履约</small><strong>{{ label(selected.confirmationState) }}</strong></div></div>
        <p class="muted">最近核验的官方状态：{{ selected.officialStatus || '未接入官方核验（模拟）' }}</p>
        <p v-if="selected.officialCheckedAt" class="muted">核验时间：{{ new Date(selected.officialCheckedAt * 1000).toLocaleString('zh-CN') }}。操作前会重新查询官方状态。</p>
        <ol class="segments"><li v-for="segment in selected.segments" :key="segment.id"><span>{{ { text: '说明文字', card: '独立卡密', image: '教程图片', link: '资源链接' }[segment.kind] }}</span><span class="badge" :class="{ red: segment.state === 'unknown' }">{{ label(segment.state) }}</span><small v-if="segment.state === 'unknown'">禁止自动重发</small></li></ol>
        <p class="muted">此页面不展示卡密正文。已确认段落会跳过；结果未知的段落必须先核对聊天回执。</p>
        <div class="actions"><button :disabled="busy || !session?.actionsAvailable || selected.receiptMode !== 'platform'" @click="act('reconcile')">查询官方状态</button><button class="primary" :disabled="busy || !canRetry" @click="act('retry-confirmation')">{{ busy ? '处理中…' : '仅补发平台确认（阶段 B）' }}</button></div>
        <p class="muted">阶段 B 操作要求全部内容已确认且官方仍为待发货；操作仅调用平台履约接口。</p>
        <details><summary>冻结素材与审计记录</summary><p v-if="!selected.assetReferences.length">无图片素材</p><code v-for="asset in selected.assetReferences" :key="asset">{{ asset }}</code><ul class="audit"><li v-for="(entry, index) in selected.audit" :key="index">{{ entry.createdTime }} · {{ entry.action }}</li></ul></details>
      </section>
      <section v-else class="panel empty">选择一笔订单，查看分段状态与安全操作。</section>
    </section>
    <p class="updated">每 5 秒刷新本地记录 · 最近更新 {{ updated || '—' }} · 数据保存在程序目录 data/</p>
  </main>
</template>

<style>
*{box-sizing:border-box}body{margin:0;background:#f3f5f7;color:#203044;font-family:"Microsoft YaHei",system-ui,sans-serif;font-size:14px}button,input{font:inherit}button{cursor:pointer;border:1px solid #ced6df;border-radius:8px;background:white;color:#203044;padding:9px 13px}button:disabled{opacity:.45;cursor:not-allowed}button:focus-visible,summary:focus-visible{outline:3px solid #417eca;outline-offset:3px}main{max-width:1440px;margin:auto;padding:32px}header,.section-head,.record-title,footer{display:flex;align-items:center;justify-content:space-between;gap:16px}h1{font-size:29px;margin:8px 0 10px}h2{font-size:17px;margin:0}p{line-height:1.7}small,.muted,.updated{color:#607087}.eyebrow{font-size:12px;letter-spacing:1px;color:#3b6389}.notice,.danger{padding:14px 17px;border-radius:9px;margin:16px 0;line-height:1.7}.notice{background:#e9f0fa;border:1px solid #cadcf1}.danger{background:#fff0ef;color:#9e2626;border:1px solid #e9b8b6}.danger p{margin:5px 0 0}.workspace{display:grid;grid-template-columns:minmax(340px, .9fr) minmax(420px, 1.1fr);gap:22px;margin-top:22px;align-items:start}.panel{background:white;border:1px solid #dce2e8;border-radius:12px;padding:22px;min-width:0}.section-head{margin-bottom:18px}.section-head label{white-space:nowrap;font-size:12px}.record{display:block;text-align:left;width:100%;padding:16px;margin-bottom:10px}.record.review{border-left:4px solid #ba3333}.record.active{background:#f0f5fc;border-color:#5381b5}.record-title strong{overflow-wrap:anywhere}.badge{display:inline-block;border-radius:5px;background:#edf1f6;color:#4c627c;padding:4px 7px;font-size:12px;white-space:nowrap}.badge.red{background:#ffe1df;color:#9e2626}.phases{display:flex;flex-wrap:wrap;gap:8px 18px;margin:12px 0}.empty{padding:60px 20px;text-align:center;color:#718096}footer{margin-top:20px;font-size:12px}.order-number{font-size:17px;font-weight:bold;overflow-wrap:anywhere}.phase-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:18px 0}.phase-grid>div{background:#f5f7fa;border-radius:8px;padding:17px}.phase-grid strong{display:block;margin-top:9px;font-size:18px}.segments{padding-left:22px}.segments li{padding:10px 0;border-bottom:1px solid #edf0f3}.segments .badge{margin:0 12px}.segments small{color:#9e2626}.actions{display:flex;gap:10px;flex-wrap:wrap;margin-top:22px}.primary{background:#245a91;color:white;border-color:#245a91}details{border-top:1px solid #e2e6ea;padding-top:18px;margin-top:22px}summary{cursor:pointer}code{display:block;font-size:11px;overflow-wrap:anywhere;margin:12px 0}.audit{padding-left:16px;font-size:11px;line-height:2;overflow-wrap:anywhere}.updated{font-size:12px;text-align:right;margin-top:22px}@media(max-width:900px){main{padding:18px}.workspace{grid-template-columns:1fr}header{align-items:flex-start}h1{font-size:24px}}
</style>
