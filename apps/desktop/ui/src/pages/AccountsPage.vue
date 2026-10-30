<script setup>
import { reactive, ref } from 'vue'
import { api, useWorkspace } from '../workspace'
import McpPanel from '../components/McpPanel.vue'
const { data, run, busy, connected } = useWorkspace()
const editing = ref(''), unbinding = ref('')
const defaults = () => ({ name: '', identity: '', driver: 'manual', endpoint: 'http://127.0.0.1:3000', credential: '', event_secret: '' })
const form = reactive(defaults())
const origin = location.origin
function newEventSecret() { form.event_secret = Array.from(crypto.getRandomValues(new Uint8Array(32)), value => value.toString(16).padStart(2, '0')).join('') }
const status = { unverified: '未验证', online: '在线', offline: '掉线', restricted: '受限', unbound: '已解绑' }
function reset() { editing.value = ''; Object.assign(form, defaults()) }
function edit(account) { editing.value = account.id; Object.assign(form, { name: account.name, identity: account.identity, driver: account.driver, endpoint: account.endpoint, credential: '', event_secret: '' }) }
async function save() {
  const body = { ...form, credential: form.credential || null, event_secret: form.event_secret || null }
  if (await run(() => api(editing.value ? `/api/workflow/accounts/${editing.value}/save` : '/api/workflow/accounts', body), '账号已登记，自动提交需先通过连接检测')) reset()
}
async function verify(account) {
  await run(() => api(`/api/workflow/accounts/${account.id}/verify`, {}), result => result.verified ? (result.is_simulated ? '模拟连接检测通过；未连接真实 QQ' : '已核对自己的 QQ 登录身份、好友与签名事件通道') : result.evidence.note)
}
async function unbind(account) {
  if (await run(() => api(`/api/workflow/accounts/${account.id}/unbind`, {}), '本应用已解绑并清除接入凭据；不会切换到其他 QQ 账号')) unbinding.value = ''
}
</script>

<template>
  <div class="management-grid">
    <section class="card"><div class="section-label">ACCOUNT <span>{{ editing ? '更新接入' : '添加自己的账号' }}</span></div><h2>{{ editing ? '更新 QQ 接入信息' : '登记 QQ 账号' }}</h2>
      <form @submit.prevent="save">
        <label for="account-name">账号名称</label><input id="account-name" v-model="form.name" required maxlength="60" placeholder="例如：我的业务 QQ" />
        <label for="account-driver">接入方式</label><select id="account-driver" v-model="form.driver" :disabled="Boolean(editing)"><option value="onebot">可选 QQ 接入器 · OneBot 兼容 HTTP</option><option value="manual">手动登记 · 不自动收发</option><option value="mock">本地模拟 · 不是真实 QQ</option></select>
        <label for="account-identity">{{ form.driver === 'mock' ? '模拟账号标识' : '自己的 QQ 号码' }}</label><input id="account-identity" v-model="form.identity" :disabled="Boolean(editing)" required maxlength="30" :placeholder="form.driver === 'mock' ? 'demo-owner' : '填写当前登录的 QQ 号码'" />
        <template v-if="form.driver === 'onebot'">
          <label for="account-endpoint">自己的 QQ 接入地址</label><input id="account-endpoint" v-model="form.endpoint" required />
          <label for="account-token">QQ 接入 API Token <span class="field-hint">{{ editing ? '留空保留原凭据' : '仅在本机加密保存' }}</span></label><input id="account-token" v-model="form.credential" type="password" autocomplete="new-password" maxlength="4096" placeholder="已有 QQ 接入器提供的本机 API 令牌" />
          <label for="account-event-secret">事件签名密钥<span class="field-hint">至少 24 字符，仅本机加密保存</span></label><input id="account-event-secret" v-model="form.event_secret" type="password" autocomplete="new-password" maxlength="256" placeholder="与自己的 QQ 接入器配置一致" /><button type="button" class="text-button" @click="newEventSecret">生成随机签名密钥</button>
          <p class="field-help">这是可替换的底层消息接口，无需 Tulpa。已有兼容 QQ 接入器时，配置相同签名密钥并开启 HTTP 事件回调；添加账号后会显示回调地址。没有接入器时，可先使用手动或模拟模式。本应用未实现 QQ 登录协议，QQ 密码无需提供给本应用。</p>
        </template>
        <p v-else class="field-help">{{ form.driver === 'mock' ? '仅用于体验流程，检测通过只表示模拟器可用。' : '人工登记不能证明账号归属，始终只允许卖家手动处理。' }}</p>
        <div class="form-actions"><button class="primary-button" :disabled="busy || !connected">{{ editing ? '保存并重新验证' : '添加账号' }}</button><button v-if="editing" class="secondary-button" type="button" @click="reset">取消编辑</button></div>
      </form>
    </section>
    <section class="card"><div class="section-label">MY ACCOUNTS <span>{{ data.accounts.length }} 个账号</span></div><h2>我的 QQ 账号</h2><div v-if="!data.accounts.length" class="empty-state"><span class="empty-icon">＋</span><h3>先添加你自己的 QQ 接入</h3><p>直接发货商品无需添加 QQ 账号。</p></div>
      <article v-for="account in data.accounts" :key="account.id" class="resource-card"><div class="resource-heading"><strong>{{ account.name }}</strong><span class="status-badge" :class="account.verified ? 'completed' : 'unknown'">{{ account.is_simulated ? '模拟 · ' : '' }}{{ status[account.status] }}</span></div><p>{{ account.identity }} · {{ { onebot: 'QQ 接入器', mock: '本地模拟', manual: '手动模式' }[account.driver] }}</p><p class="field-help">验证版本 {{ account.generation }} · {{ account.has_credential ? '凭据已加密保存' : '无已保存凭据' }}</p><p v-if="account.driver === 'onebot'" class="field-help">事件回调：<code>{{ `${origin}/api/qq/events/${account.id}` }}</code></p><p v-if="account.evidence.note" class="field-help">{{ account.evidence.note }}</p>
        <details v-if="account.evidence.friends?.length"><summary>已授权好友（{{ account.evidence.friends.length }}）</summary><div v-for="friend in account.evidence.friends" :key="friend.conversation_id" class="friend-item">{{ friend.name }}<code>{{ friend.conversation_id }}</code></div></details>
        <div v-if="account.status !== 'unbound'" class="button-row"><button class="secondary-button" :disabled="busy" @click="verify(account)">验证连接 / 健康检查</button><button class="text-button" @click="edit(account)">更新接入</button><button class="danger-link" @click="unbinding = account.id">解绑</button></div>
        <div v-if="unbinding === account.id" class="inline-warning"><p>解绑会停止该账号后续自动提交并清除本应用的凭据。已发送请求不会撤回。</p><button class="secondary-button" :disabled="busy" @click="unbind(account)">确认解绑</button><button class="text-button" @click="unbinding = ''">暂不解绑</button></div>
      </article>
    </section>
  </div>
  <McpPanel />
</template>
