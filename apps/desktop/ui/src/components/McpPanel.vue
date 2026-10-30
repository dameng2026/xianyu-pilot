<script setup>
import { onMounted, reactive, ref } from 'vue'
import { api, useWorkspace } from '../workspace'
const { data, run, busy } = useWorkspace()
const form = reactive({ name: '我的 Agent', product_ids: [], allow_create: false, allow_actions: false, expires_days: 7 })
const grants = ref([]), oneTime = ref(null)
async function load() { grants.value = (await api('/api/workflow/mcp/grants')).grants }
async function create() {
  const result = await run(() => api('/api/workflow/mcp/grants', form), 'MCP 连接已创建，令牌只显示本次；请复制保存')
  if (result) { oneTime.value = result; await load() }
}
async function revoke(grant) {
  if (await run(() => api(`/api/workflow/mcp/grants/${grant.id}/revoke`, {}), 'MCP 凭据已撤销，已创建任务保留原状态')) await load()
}
async function copy() {
  await run(async () => { await navigator.clipboard.writeText(JSON.stringify({ mcpServers: { 'xianyu-pilot': { url: oneTime.value.url, headers: { Authorization: `Bearer ${oneTime.value.token}` } } } }, null, 2)); return true }, '通用 MCP 连接配置已复制，请在自己的客户端中保存')
}
onMounted(() => run(load, ''))
</script>

<template>
  <section class="card detail-card">
    <div class="section-label">PROJECT MCP <span>本项目自行实现的工具协议</span></div><h2>MCP 客户端连接</h2><p class="field-help">让自己的 Agent 访问明确授权的商品任务。无需安装 Tulpa。默认仅开放读取；不提供任意 QQ 发送、账号切换、凭据读取或权限修改工具。</p>
    <div class="detail-grid"><form @submit.prevent="create"><label for="mcp-name">连接名称</label><input id="mcp-name" v-model="form.name" required maxlength="60" />
      <label>允许访问的商品（不默认开放全部）</label><p v-if="!data.products.length" class="field-help">先创建商品规则，再创建 MCP 授权。</p><label v-for="product in data.products" :key="product.id" class="check-label"><input v-model="form.product_ids" type="checkbox" :value="product.id" />{{ product.name }}</label>
      <label class="check-label"><input v-model="form.allow_create" type="checkbox" />允许创建任务（可能按商品规则执行 QQ 请求或交付）</label><label class="check-label"><input v-model="form.allow_actions" type="checkbox" />允许更新参数、确认执行、取消和终止后续交付</label>
      <p class="field-help">读取授权包含所选商品任务的参数与结果。连接令牌需要交给你信任的客户端；MCP 不会替你核验买家付款，也不能记录人工发货成功。</p><label for="mcp-expiry">有效期（天）</label><input id="mcp-expiry" v-model.number="form.expires_days" type="number" min="1" max="30" required /><button class="primary-button" :disabled="busy || !form.product_ids.length">创建受限 MCP 连接</button>
    </form><div><p class="field-help">Streamable HTTP 地址：<code>{{ data.mcp_url }}</code></p>
      <div v-if="oneTime" class="inline-warning"><strong>令牌仅显示本次</strong><p>{{ oneTime.note }}</p><input :value="oneTime.token" type="password" readonly aria-label="新建 MCP 连接令牌" /><button class="secondary-button" @click="copy">复制通用 MCP 客户端配置</button><button class="text-button" @click="oneTime = null">我已保存，隐藏令牌</button></div>
      <article v-for="grant in grants" :key="grant.id" class="resource-card"><div class="resource-heading"><strong>{{ grant.name }}</strong><span class="version-tag">{{ grant.revoked ? '已撤销' : Date.now() / 1000 >= grant.expires ? '已过期' : '有效' }}</span></div><p>{{ grant.scope.product_ids.length }} 个授权商品 · {{ grant.scope.allow_create || grant.scope.allow_actions ? '含已授权写操作' : '仅读取' }}</p><button v-if="!grant.revoked" class="danger-link" :disabled="busy" @click="revoke(grant)">撤销此连接</button></article>
    </div></div>
  </section>
</template>
