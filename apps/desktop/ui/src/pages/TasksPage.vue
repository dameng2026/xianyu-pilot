<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { api, formatTime, parameterText, useWorkspace } from '../workspace'
const { data, run, busy, connected, navigate } = useWorkspace()
const form = reactive({ product_id: '', order_key: `LOCAL-${Date.now()}`, paid: true, yuan: 0, parameters: {}, buyer_text: '', scenario: 'normal', parent_id: null })
const selected = ref(''), detail = ref(null), filter = ref('all'), resultText = ref(''), parameterValues = ref({}), editing = ref(false), reassurance = ref('')
const edit = reactive({ mode: 'direct', service_id: '', delivery_template: '' })
const product = computed(() => data.value.products.find(item => item.id === form.product_id))
const service = computed(() => data.value.services.find(item => item.id === product.value?.service_id))
const account = computed(() => data.value.accounts.find(item => item.id === service.value?.account_id))
const tasks = computed(() => data.value.tasks.filter(task => filter.value === 'all' || filter.value === task.state))
const needsAttention = computed(() => ['manual_required', 'result_review', 'awaiting_parameters', 'awaiting_confirmation', 'awaiting_delivery', 'awaiting_shipping'].reduce((sum, key) => sum + (data.value.counts[key] || 0), 0))
const has = action => detail.value?.allowed_actions.includes(action)
const steps = { qq: 'QQ 处理', delivery: '回发买家', shipping: '确认闲鱼发货' }
const stepStates = { pending: '待执行', executing: '执行中', waiting: '等待结果', done: '已确认', failed: '失败待核对', unknown: '结果不明' }
const scenarios = { normal: '正常回复', split: '分段回复', duplicate: '重复消息', wrong_sender: '其他发送者', timeout: '回复超时', refused: '明确未发送', unknown: '发送结果不明' }
let loadingDetail = false

async function loadDetail(id) {
  if (loadingDetail) return
  loadingDetail = true
  try { const result = await api(`/api/workflow/tasks/${id}`); if (selected.value === id) detail.value = result }
  finally { loadingDetail = false }
}
watch(() => data.value.tasks.map(task => `${task.id}:${task.revision}`).join('|'), () => {
  if (selected.value && data.value.tasks.find(task => task.id === selected.value)?.revision !== detail.value?.revision) {
    loadDetail(selected.value).catch(() => {})
  }
})
watch(() => form.product_id, () => { form.parameters = {}; form.buyer_text = ''; form.scenario = 'normal' })
async function select(task) {
  selected.value = task.id; detail.value = null; editing.value = false; reassurance.value = ''; resultText.value = ''
  await run(async () => { await loadDetail(task.id); parameterValues.value = { ...detail.value.parameters }; return detail.value }, '')
}
async function create() {
  const result = await run(() => api('/api/workflow/tasks', {
    product_id: form.product_id, order_key: form.order_key, paid: form.paid, amount_fen: Math.round(Number(form.yuan) * 100),
    parameters: form.parameters, buyer_text: form.buyer_text, scenario: form.scenario, parent_id: form.parent_id,
  }), value => value.created ? '任务已创建，当前商品和服务规则已保存为快照' : '该订单已有任务，已返回原记录，没有重复创建')
  if (result) {
    selected.value = result.task.id; await loadDetail(result.task.id); parameterValues.value = { ...detail.value.parameters }
    filter.value = 'all'; form.order_key = `LOCAL-${Date.now()}`; form.parent_id = null
  }
}
async function action(name, extra = {}) {
  const result = await run(() => api(`/api/workflow/tasks/${selected.value}/actions`, { action: name, expected_revision: detail.value.revision, ...extra }), '任务操作已记录')
  if (result) { detail.value = result; editing.value = false }
}
function beginEdit() { editing.value = true; Object.assign(edit, { mode: detail.value.snapshot.product.mode, service_id: detail.value.snapshot.service?.id || '', delivery_template: detail.value.snapshot.product.delivery_template }) }
function linked() { form.product_id = detail.value.product_id; form.order_key = `${detail.value.order_key}-NEW-${Date.now()}`; form.parent_id = detail.value.id; form.parameters = { ...detail.value.parameters } }
async function notify() {
  await action('notify')
  reassurance.value = detail.value.snapshot.service?.failure_message || '卖家正在核对订单，请稍候。'
}
async function copyReassurance() { await run(async () => { await navigator.clipboard.writeText(reassurance.value); return true }, '话术已复制；请在闲鱼核对目标后手动发送') }
</script>

<template>
  <section class="stats" aria-label="工作流统计">
    <div class="stat"><span>累计任务</span><strong>{{ data.total }}<small>条</small></strong><p>规则快照独立保存</p></div>
    <div class="stat"><span>已完成</span><strong class="green">{{ data.counts.completed || 0 }}<small>条</small></strong><p>交付与发货分别确认</p></div>
    <div class="stat"><span>需要你处理</span><strong>{{ needsAttention }}<small>条</small></strong><p>参数、审核与异常提醒</p></div>
    <div class="stat"><span>失败待人工</span><strong>{{ data.counts.manual_required || 0 }}<small>条</small></strong><p>结果不明不会自动重发</p></div>
  </section>
  <div v-if="data.counts.manual_required" class="alert paused" role="status"><strong>有 {{ data.counts.manual_required }} 条任务需要人工核对</strong><button class="secondary-button" @click="filter = 'manual_required'">查看异常任务</button></div>
  <div class="management-grid task-management">
    <section class="card"><div class="section-label">NEW TASK <span>手动登记订单</span></div><h2>创建任务</h2><p class="section-description">选择商品，复制它当前的处理规则。这里的付款状态由卖家登记。</p>
      <p v-if="!data.products.length" class="inline-warning">先配置一项商品。直接发货商品无需 QQ。<button class="text-button" @click="navigate('products')">配置商品 →</button></p>
      <form @submit.prevent="create">
        <label for="task-product">商品默认规则</label><select id="task-product" v-model="form.product_id" required><option value="" disabled>请选择商品</option><option v-for="item in data.products.filter(item => item.enabled)" :key="item.id" :value="item.id">{{ item.name }} · {{ item.mode === 'direct' ? '直接发货' : 'QQ 处理' }} · v{{ item.version }}</option></select>
        <div v-if="product" class="rule-summary"><strong>{{ product.mode === 'direct' ? '直接发货，无需 QQ' : `QQ 服务：${service?.name}` }}</strong><p>{{ product.execution_policy === 'auto' ? '付款及参数满足后执行' : product.execution_policy === 'threshold' ? '按金额阈值决定是否确认' : '执行前等待卖家确认' }} · {{ product.delivery_channel === 'mock' ? '模拟交付' : '卖家人工交付' }}</p></div>
        <label for="task-order">订单唯一编号</label><input id="task-order" v-model="form.order_key" required maxlength="100" /><p class="field-help">重复登记同一订单会返回原任务，不重复查询或发货。</p>
        <label for="task-amount">订单金额（元）</label><input id="task-amount" v-model.number="form.yuan" type="number" min="0" step="0.01" required /><label class="check-label"><input v-model="form.paid" type="checkbox" />卖家登记已付款（未向闲鱼核验）</label>
        <template v-if="product?.mode === 'qq'">
          <template v-if="['buyer_chat', 'extract_review'].includes(product.parameter_source)"><label for="task-buyer">买家提供的文字（手动粘贴）</label><textarea id="task-buyer" v-model="form.buyer_text" rows="3" maxlength="4000" placeholder="真实聊天尚未自动接入；演示请填写虚构信息"></textarea></template>
          <template v-else-if="product.parameter_source === 'fixed'"><p class="field-help">使用商品快照里的固定参数：</p><pre class="template-preview">{{ parameterText(product.fixed_parameters) }}</pre></template>
          <template v-else-if="product.parameter_source !== 'none'"><template v-for="field in service?.required_fields || []" :key="field"><label :for="`task-param-${field}`">参数：{{ field }} <span class="field-hint">可稍后补齐</span></label><input :id="`task-param-${field}`" v-model="form.parameters[field]" maxlength="1000" /></template></template>
          <template v-if="account?.driver === 'mock'"><label for="task-scenario">模拟 QQ 回复场景</label><select id="task-scenario" v-model="form.scenario"><option v-for="(label, key) in scenarios" :key="key" :value="key">{{ label }}</option></select></template>
          <p v-if="account?.driver === 'onebot'" class="inline-warning">此服务使用自己的 QQ 消息连接器。满足执行条件后，将向快照里的好友提交请求，可能产生服务费用；请核对服务和参数。</p>
        </template>
        <p v-if="form.parent_id" class="inline-warning">这是关联新任务，使用当前商品规则，不会撤回或覆盖原请求。</p><button class="primary-button" :disabled="busy || !connected" type="submit">按商品规则创建任务 →</button>
      </form>
    </section>
    <section class="card tasks-card"><div class="tasks-heading"><div><div class="section-label">TASKS <span>最近 100 条</span></div><h2>任务列表</h2></div><label class="filter-label"><span class="sr-only">筛选状态</span><select v-model="filter"><option value="all">全部状态</option><option v-for="(label, key) in data.states" :key="key" :value="key">{{ label }}</option></select></label></div>
      <div v-if="!tasks.length" class="empty-state"><span class="empty-icon">☷</span><h3>{{ data.total ? '没有符合条件的任务' : '先从一笔虚构订单开始' }}</h3><p>无需 QQ 的商品也可以独立完成交付流程。</p></div>
      <div v-else class="workflow-task-list"><button v-for="task in tasks" :key="task.id" class="task-row" :class="{ selected: selected === task.id }" @click="select(task)"><div class="task-row-top"><span class="task-id">{{ task.order_key }}</span><span class="status-badge" :class="task.state === 'completed' ? 'completed' : task.state === 'manual_required' ? 'unknown' : 'waiting_reply'">{{ data.states[task.state] }}</span></div><p class="task-input">{{ task.product_name }}</p><div class="task-meta"><span>{{ task.mode === 'direct' ? '直接发货' : task.service_name }} · {{ task.delivery_channel === 'mock' ? '模拟交付' : '人工交付' }}</span><time>{{ formatTime(task.created_at) }}</time></div></button></div>
    </section>
  </div>
  <section v-if="detail" class="card detail-card" aria-label="工作流任务详情">
    <div class="detail-heading"><div><div class="section-label">TASK SNAPSHOT <span>按创建时规则执行</span></div><h2>{{ detail.order_key }}<span class="detail-id">{{ data.states[detail.state] }}</span></h2></div><button class="text-button" @click="selected = ''; detail = null">收起详情</button></div>
    <div class="step-list"><div v-for="(step, index) in detail.steps" :key="step.kind" class="step-item" :class="step.state"><span>{{ index + 1 }}</span><div><strong>{{ steps[step.kind] }}</strong><small>{{ stepStates[step.state] }} · 已尝试 {{ step.attempts }} 次</small></div></div></div>
    <div v-if="detail.failure" class="alert error"><div><strong>{{ detail.failure.message }}</strong><p v-if="detail.failure.unknown">可能已经发送或产生费用，禁止自动重试。核对原请求后，可人工回填或创建关联新任务。</p></div></div>
    <div class="detail-grid">
      <div>
        <h3>本任务的规则快照</h3><dl class="snapshot-list"><dt>商品规则</dt><dd>{{ detail.snapshot.product.name }} · v{{ detail.snapshot.product.version }}</dd><dt>处理方式</dt><dd>{{ detail.snapshot.product.mode === 'direct' ? '直接发货，无 QQ 步骤' : 'QQ 处理后交付' }}</dd><template v-if="detail.snapshot.service"><dt>服务版本</dt><dd>{{ detail.snapshot.service.name }} · v{{ detail.snapshot.service.version }}</dd><dt>自有账号</dt><dd>{{ detail.snapshot.account.name }} · {{ detail.snapshot.account.identity }} · 验证版本 {{ detail.snapshot.account.generation }}</dd><dt>固定目标</dt><dd>{{ detail.snapshot.service.target }}</dd></template><dt>交付通道</dt><dd>{{ detail.snapshot.product.delivery_channel === 'mock' ? '本地模拟，未发给真实买家' : '卖家在闲鱼人工执行' }}</dd></dl>
        <h3>交付模板快照</h3><pre class="template-preview">{{ detail.snapshot.product.delivery_template }}</pre>
        <h3>买家 / 卖家参数</h3><pre class="template-preview">{{ parameterText(detail.parameters) || '无需参数 / 尚未提供' }}</pre>
        <template v-if="has('parameters')"><div v-for="field in detail.snapshot.service?.required_fields || []" :key="field"><label :for="`detail-param-${field}`">{{ field }}</label><input :id="`detail-param-${field}`" v-model="parameterValues[field]" maxlength="1000" /></div><button class="secondary-button" :disabled="busy" @click="action('parameters', { parameters: parameterValues })">保存参数并重新核对</button></template>
        <template v-if="detail.request_preview"><h3>固定目标的 QQ 请求预览</h3><pre class="result-box">{{ detail.request_preview }}</pre></template>
        <template v-if="detail.result"><h3>处理结果（{{ detail.result_origin === 'mock' ? '模拟' : detail.result_origin === 'connector_pending' ? '已收片段，待核对完整性' : detail.result_origin === 'onebot' ? 'QQ 接入器' : '卖家回填' }}）</h3><pre class="result-box">{{ detail.result }}</pre></template>
        <template v-if="detail.outbound_preview"><h3>回发买家的内容预览</h3><pre class="result-box">{{ detail.outbound_preview }}</pre></template>
        <template v-if="has('manual_result')"><label for="manual-result">人工核对 / 回填完整结果</label><button v-if="detail.result" class="text-button" @click="resultText = detail.result">将已收内容填入核对框</button><textarea id="manual-result" v-model="resultText" rows="3" maxlength="4000"></textarea><button class="secondary-button" :disabled="busy || !resultText.trim()" @click="action('manual_result', { text: resultText })">保存人工结果，进入审核</button></template>
      </div>
      <div>
        <h3>当前可执行操作</h3><div class="task-action-panel">
          <button v-if="has('pay')" class="secondary-button" :disabled="busy" @click="action('pay')">登记已付款</button>
          <button v-if="has('confirm')" class="primary-button" :disabled="busy" @click="action('confirm')">确认当前参数与目标，执行本任务</button>
          <button v-if="has('manual_sent')" class="secondary-button" :disabled="busy" @click="action('manual_sent')">我已在 QQ 手动提交（仅记录）</button>
          <button v-if="has('approve')" class="primary-button" :disabled="busy" @click="action('approve')">审核结果通过，允许交付</button>
          <button v-if="has('confirm_delivery')" class="primary-button" :disabled="busy" @click="action('confirm_delivery')">我已手动回发买家</button>
          <button v-if="has('confirm_shipping')" class="primary-button" :disabled="busy" @click="action('confirm_shipping')">我已在闲鱼确认发货</button>
          <button v-if="has('edit')" class="secondary-button" @click="beginEdit">执行前修改方式 / 服务</button>
          <button v-if="has('cancel')" class="secondary-button" :disabled="busy" @click="action('cancel')">取消未执行任务</button>
          <button v-if="has('stop_delivery')" class="secondary-button" :disabled="busy" @click="action('stop_delivery')">终止后续交付</button>
          <button v-if="has('retry')" class="secondary-button" :disabled="busy" @click="action('retry')">重试明确未执行的步骤</button>
          <button v-if="has('release_channel')" class="secondary-button" :disabled="busy" @click="action('release_channel')">已核对，解除目标通道暂停</button>
          <button v-if="has('notify')" class="secondary-button" :disabled="busy" @click="notify">生成安抚话术预览</button>
          <button v-if="['completed', 'manual_required', 'delivery_stopped'].includes(detail.state)" class="secondary-button" @click="linked">准备关联新任务 / 补发</button>
        </div><p class="field-help">取消或终止后续交付，不能保证撤销已发出的 QQ 请求或已交付内容。更换服务不会撤销原请求。</p>
        <div v-if="editing" class="inline-editor"><h3>修改本任务，保留旧规则记录</h3><label for="edit-mode">任务处理方式</label><select id="edit-mode" v-model="edit.mode"><option value="direct">直接发货</option><option value="qq">QQ 处理后交付</option></select><template v-if="edit.mode === 'qq'"><label for="edit-service">自己的 QQ 服务</label><select id="edit-service" v-model="edit.service_id"><option v-for="item in data.services.filter(item => item.enabled)" :key="item.id" :value="item.id">{{ item.name }} · v{{ item.version }}</option></select></template><label for="edit-template">本任务交付内容</label><textarea id="edit-template" v-model="edit.delivery_template" rows="3" maxlength="3000"></textarea><button class="primary-button" :disabled="busy" @click="action('edit', { mode: edit.mode, service_id: edit.mode === 'qq' ? edit.service_id : null, delivery_template: edit.delivery_template })">保存任务修改，重新确认</button></div>
        <div v-if="reassurance" class="inline-warning"><pre class="template-preview">{{ reassurance }}</pre><button class="secondary-button" @click="copyReassurance">复制话术，去闲鱼手动发送</button></div>
        <h3>步骤与操作日志</h3><ol class="timeline"><li v-for="(entry, index) in detail.logs" :key="index"><time>{{ formatTime(entry.created_at) }}</time><p>{{ entry.message }}</p></li></ol>
        <details class="receipt-details"><summary>查看操作编号与回执</summary><article v-for="attempt in detail.attempts" :key="attempt.id" class="receipt-item"><strong>{{ steps[attempt.stage] }} · {{ attempt.status }}</strong><code>{{ attempt.id }}</code><p>{{ attempt.receipt.simulated ? '本地模拟回执' : attempt.receipt.source === 'seller_manual_record' ? '卖家人工记录，不是平台回执' : attempt.receipt.source || '执行边界记录' }}</p></article></details>
      </div>
    </div>
  </section>
</template>
