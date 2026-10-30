<script setup>
import { computed, reactive, ref } from 'vue'
import { api, useWorkspace } from '../workspace'
const { data, run, busy, connected, navigate } = useWorkspace()
const editing = ref('')
const defaults = () => ({ name: '', account_id: '', target: '', request_template: '查询 {value}，请求编号 {request_id}', fields: 'value', timeout_seconds: 60, retry_limit: 0, allow_auto: false, result_mode: 'manual', result_pattern: '', failure_message: '查询暂未完成，卖家正在核对，请稍候。', enabled: true })
const form = reactive(defaults())
const account = computed(() => data.value.accounts.find(item => item.id === form.account_id))
function reset() { editing.value = ''; Object.assign(form, defaults()) }
function edit(service) { editing.value = service.id; Object.assign(form, Object.fromEntries(Object.keys(defaults()).filter(key => key !== 'fields').map(key => [key, service[key]])), { fields: service.required_fields.join(', ') }) }
async function save() {
  const { fields, ...rest } = form
  const body = { ...rest, timeout_seconds: Number(rest.timeout_seconds), retry_limit: Number(rest.retry_limit), required_fields: fields.split(/[,，]/).map(item => item.trim()).filter(Boolean) }
  if (await run(() => api(editing.value ? `/api/workflow/services/${editing.value}/save` : '/api/workflow/services', body), '服务版本已保存；已有任务保持原快照')) reset()
}
</script>

<template>
  <div class="management-grid">
    <section class="card"><div class="section-label">SERVICE <span>{{ editing ? '编辑服务版本' : '创建处理服务' }}</span></div><h2>{{ editing ? '编辑 QQ 服务' : '配置一个处理服务' }}</h2>
      <p v-if="!data.accounts.some(item => item.status !== 'unbound')" class="inline-warning">先添加自己的账号，或添加明确标注的模拟账号。<button class="text-button" @click="navigate('accounts')">前往账号管理 →</button></p>
      <form @submit.prevent="save">
        <label for="service-name">服务名称</label><input id="service-name" v-model="form.name" required maxlength="60" placeholder="例如：订单查询服务" />
        <label for="service-account">我的 QQ 账号</label><select id="service-account" v-model="form.account_id" required><option value="" disabled>选择自己的账号</option><option v-for="item in data.accounts.filter(item => item.status !== 'unbound')" :key="item.id" :value="item.id">{{ item.name }}{{ item.is_simulated ? '（模拟）' : '' }}{{ !item.verified ? ' · 未验证' : '' }}</option></select>
        <label for="service-target">目标好友 / 机器人</label><input id="service-target" v-model="form.target" list="friend-options" required maxlength="120" :placeholder="account?.driver === 'onebot' ? `${account.identity}:direct:目标QQ` : '例如 demo-bot'" /><datalist id="friend-options"><option v-for="friend in account?.evidence.friends || []" :key="friend.conversation_id" :value="friend.conversation_id">{{ friend.name }}</option></datalist>
        <label for="service-fields">必填参数名称 <span class="field-hint">英文，逗号分隔；无参数可留空</span></label><input id="service-fields" v-model="form.fields" placeholder="例如 value 或 order_no, region" />
        <label for="service-request">QQ 请求模板</label><textarea id="service-request" v-model="form.request_template" required rows="3" maxlength="2000"></textarea><p class="field-help">可用已声明参数及 {request_id}、{order_id}。自动匹配结果需保留请求编号或引用消息关联，回复必须能可靠关联该编号；无编号协议使用人工回填。</p>
        <div class="field-grid"><div><label for="service-timeout">结果等待上限（秒）</label><input id="service-timeout" v-model.number="form.timeout_seconds" type="number" min="5" max="300" required /></div><div><label for="service-retries">明确未执行时的重试上限</label><input id="service-retries" v-model.number="form.retry_limit" type="number" min="0" max="3" required /></div></div>
        <label class="check-label"><input v-model="form.allow_auto" type="checkbox" />允许商品规则自动提交此服务</label><p class="field-help">未验证账号始终需要人工处理。发送结果不明或结果超时不自动重试，避免重复扣费。</p>
        <label for="service-result-mode">结果处理方式</label><select id="service-result-mode" v-model="form.result_mode"><option value="manual">收到结果后人工核对</option><option value="regex">按完整结果正则提取</option><option value="manual_fill">卖家手动回填结果</option></select>
        <template v-if="form.result_mode === 'regex'"><label for="service-pattern">完整结果提取正则</label><input id="service-pattern" v-model="form.result_pattern" maxlength="200" required placeholder="例如 结果[:：](?P&lt;result&gt;.+)" /><p class="field-help">用于你已核实的完整回复格式；不能仅凭短暂无消息就判断分段结果结束。</p></template>
        <label for="service-failure">失败安抚话术</label><textarea id="service-failure" v-model="form.failure_message" maxlength="500" rows="2"></textarea><label class="check-label"><input v-model="form.enabled" type="checkbox" />启用服务供新任务选择</label>
        <div class="form-actions"><button class="primary-button" :disabled="busy || !connected">{{ editing ? '保存新版本' : '添加服务' }}</button><button v-if="editing" class="secondary-button" type="button" @click="reset">取消编辑</button></div>
      </form>
    </section>
    <section class="card"><div class="section-label">MY SERVICES <span>{{ data.services.length }} 个服务</span></div><h2>我的 QQ 处理服务</h2><div v-if="!data.services.length" class="empty-state"><span class="empty-icon">↗</span><h3>把账号连接配置成可选服务</h3><p>创建任务时选服务名称，任务会保存目标与模板版本。</p></div>
      <article v-for="service in data.services" :key="service.id" class="resource-card"><div class="resource-heading"><strong>{{ service.name }}</strong><span class="version-tag">v{{ service.version }}</span></div><p>{{ data.accounts.find(item => item.id === service.account_id)?.name }} → {{ service.target }}</p><div class="tag-row"><span>{{ service.allow_auto ? '允许自动提交' : '需卖家确认' }}</span><span>{{ service.timeout_seconds }} 秒超时</span><span>{{ service.enabled ? '已启用' : '新任务停用' }}</span></div><pre class="template-preview">{{ service.request_template }}</pre><button class="text-button" @click="edit(service)">编辑服务 / 启停 →</button></article>
    </section>
  </div>
</template>
