<script setup>
import { reactive, ref } from 'vue'
import { api, parameterText, parseParameters, useWorkspace } from '../workspace'
const { data, run, busy, connected, navigate } = useWorkspace()
const editing = ref('')
const defaults = () => ({ name: '', external_id: '', mode: 'direct', service_id: '', delivery_template: '感谢购买。以下是操作说明：请按教程完成后续设置。', parameter_source: 'none', fixed_text: '', parameter_pattern: '', execution_policy: 'confirm', threshold_yuan: 10, review_result: true, delivery_channel: 'mock', shipping_policy: 'manual', enabled: true })
const form = reactive(defaults())
function reset() { editing.value = ''; Object.assign(form, defaults()) }
function modeChanged() {
  if (form.mode === 'qq') { form.parameter_source = 'seller'; if (form.delivery_template === defaults().delivery_template) form.delivery_template = '本次处理结果：\n{result}' }
  else { form.parameter_source = 'none'; form.service_id = ''; if (form.delivery_template === '本次处理结果：\n{result}') form.delivery_template = defaults().delivery_template }
}
function edit(product) {
  editing.value = product.id
  Object.assign(form, Object.fromEntries(Object.keys(defaults()).filter(key => !['fixed_text', 'threshold_yuan'].includes(key)).map(key => [key, product[key]])), { service_id: product.service_id || '', fixed_text: parameterText(product.fixed_parameters), threshold_yuan: product.approval_threshold / 100 })
}
async function save() {
  await run(async () => {
    const { fixed_text, threshold_yuan, ...rest } = form
    const body = { ...rest, service_id: rest.mode === 'qq' ? rest.service_id : null, fixed_parameters: parseParameters(fixed_text), approval_threshold: Math.round(Number(threshold_yuan) * 100) }
    const result = await api(editing.value ? `/api/workflow/products/${editing.value}/save` : '/api/workflow/products', body)
    reset(); return result
  }, '商品默认规则已保存；已创建的任务不受影响')
}
</script>

<template>
  <div class="management-grid">
    <section class="card"><div class="section-label">PRODUCT RULE <span>{{ editing ? '更新默认规则' : '设置商品默认行为' }}</span></div><h2>{{ editing ? '编辑商品处理规则' : '商品如何处理订单' }}</h2>
      <form @submit.prevent="save">
        <label for="product-name">商品名称</label><input id="product-name" v-model="form.name" required maxlength="80" placeholder="例如：安装教程 / 订单查询" />
        <label for="product-external">商品编号</label><input id="product-external" v-model="form.external_id" required maxlength="80" placeholder="填写自己的商品编号；演示可用 DEMO-1001" />
        <label for="product-mode">默认处理方式</label><select id="product-mode" v-model="form.mode" @change="modeChanged"><option value="direct">直接自动发货 · 无需 QQ</option><option value="qq">QQ 处理后交付</option></select>
        <template v-if="form.mode === 'qq'">
          <label for="product-service">我的 QQ 服务</label><select id="product-service" v-model="form.service_id" required><option value="" disabled>选择处理服务</option><option v-for="service in data.services.filter(item => item.enabled)" :key="service.id" :value="service.id">{{ service.name }} · v{{ service.version }}</option></select><button v-if="!data.services.length" class="text-button" type="button" @click="navigate('services')">先创建 QQ 服务 →</button>
          <label for="parameter-source">参数来源</label><select id="parameter-source" v-model="form.parameter_source"><option value="none">无需参数</option><option value="buyer_chat">买家聊天提供</option><option value="seller">卖家手动填写</option><option value="fixed">固定参数</option><option value="extract_review">正则提取 + 卖家确认</option></select>
          <template v-if="form.parameter_source === 'fixed'"><label for="fixed-params">固定参数（每行 key=value）</label><textarea id="fixed-params" v-model="form.fixed_text" rows="3" placeholder="value=演示查询内容"></textarea></template>
          <template v-if="['buyer_chat', 'extract_review'].includes(form.parameter_source)"><label for="parameter-pattern">参数提取正则 {{ form.parameter_source === 'buyer_chat' ? '（可选）' : '' }}</label><input id="parameter-pattern" v-model="form.parameter_pattern" :required="form.parameter_source === 'extract_review'" maxlength="200" placeholder="例如 编号[:：](?P&lt;value&gt;[A-Z0-9-]+)" /></template><label class="check-label"><input v-model="form.review_result" type="checkbox" />QQ 结果需卖家审核后回发</label>
        </template>
        <label for="delivery-template">{{ form.mode === 'qq' ? '结果交付模板' : '直接发货内容 / 教程 / 链接' }}</label><textarea id="delivery-template" v-model="form.delivery_template" required rows="4" maxlength="3000"></textarea><p class="field-help">{{ form.mode === 'qq' ? '使用 {result} 引用处理结果，也可引用服务参数。' : '这段内容就是交付给买家的内容，无需配置任何 QQ 服务。' }}可用 {order_id} 和 {request_id}。</p>
        <label for="execution-policy">执行策略</label><select id="execution-policy" v-model="form.execution_policy"><option value="confirm">需卖家确认后执行</option><option value="auto">满足付款与参数条件后执行</option><option value="threshold">按订单金额阈值决定</option></select>
        <template v-if="form.execution_policy === 'threshold'"><label for="approval-threshold">达到多少元需人工确认</label><input id="approval-threshold" v-model.number="form.threshold_yuan" type="number" min="0" step="0.01" /></template>
        <label for="delivery-channel">买家交付通道</label><select id="delivery-channel" v-model="form.delivery_channel" @change="form.delivery_channel === 'manual' && (form.shipping_policy = 'manual')"><option value="mock">本地模拟 · 不向真实买家发送</option><option value="manual">卖家在闲鱼手动交付并记录</option></select>
        <label for="shipping-policy">确认闲鱼发货的方式</label><select id="shipping-policy" v-model="form.shipping_policy"><option value="manual">交付后由卖家人工确认</option><option v-if="form.delivery_channel === 'mock'" value="auto">交付成功后自动模拟确认</option></select><p class="field-help">只有回发买家成功后才进入确认发货步骤。真实闲鱼接口接入前，不会自动操作闲鱼发货。</p><label class="check-label"><input v-model="form.enabled" type="checkbox" />启用此规则供新任务使用</label>
        <div class="form-actions"><button class="primary-button" :disabled="busy || !connected">{{ editing ? '保存新版本' : '添加商品规则' }}</button><button v-if="editing" type="button" class="secondary-button" @click="reset">取消编辑</button></div>
      </form>
    </section>
    <section class="card"><div class="section-label">MY PRODUCTS <span>{{ data.products.length }} 项规则</span></div><h2>商品默认规则</h2><div v-if="!data.products.length" class="empty-state"><span class="empty-icon">▦</span><h3>先配置一项直接发货商品</h3><p>无需 QQ，即可体验任务快照、交付和确认发货。</p></div>
      <article v-for="product in data.products" :key="product.id" class="resource-card"><div class="resource-heading"><strong>{{ product.name }}</strong><span class="version-tag">v{{ product.version }}</span></div><p>{{ product.external_id }} · {{ product.mode === 'direct' ? '直接发货' : 'QQ 处理后交付' }}</p><div class="tag-row"><span>{{ product.execution_policy === 'auto' ? '满足条件后执行' : product.execution_policy === 'threshold' ? '金额阈值确认' : '卖家确认' }}</span><span>{{ product.delivery_channel === 'mock' ? '模拟交付' : '人工交付' }}</span><span>{{ product.enabled ? '已启用' : '新任务停用' }}</span></div><p v-if="product.mode === 'qq'" class="field-help">服务：{{ data.services.find(item => item.id === product.service_id)?.name }}</p><pre class="template-preview">{{ product.delivery_template }}</pre><button class="text-button" @click="edit(product)">编辑默认规则 →</button></article>
    </section>
  </div>
</template>
