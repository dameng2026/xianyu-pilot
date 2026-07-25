<template>
  <div class="admin-system-settings system-settings-page system-main">
    <div v-if="error" class="global-notice error">{{ error }}</div>
    <div v-if="success" class="global-notice success">{{ success }}</div>

    <section class="page-hero system-hero">
      <div class="page-hero-copy">
        <span class="page-pill">System Overview</span>
        <h1>系统配置</h1>
        <p>
          这里是开源版系统的总览页面与站点基础配置入口。通用模型配置、向量模型配置与 RAG 知识库已经拆分为独立导航项，
          可在左侧切换至对应页面单独维护。
        </p>

        <div class="page-actions">
          <AppButton type="primary" :loading="saving" :disabled="!configAvailable" @click="save">保存配置</AppButton>
          <AppButton :loading="loading" @click="loadPage">重新加载</AppButton>
        </div>
      </div>
    </section>

    <div class="grid stat-grid" style="grid-template-columns: repeat(4, 1fr)">
      <StatCard
        title="通用模型"
        :value="configAvailable ? summary.generalModel : '状态未知'"
        :change="summary.generalModelHint"
        icon="message"
        color="green"
      />
      <StatCard
        title="向量模型"
        :value="configAvailable ? summary.embeddingModel : '状态未知'"
        :change="summary.embeddingModelHint"
        icon="document"
        color="blue"
      />
      <StatCard
        title="Redis 状态"
        :value="summary.redisStatus"
        :change="summary.redisMode"
        icon="shield"
        :color="runtimeStatusAvailable && runtimeStatus.redisConnected ? 'green' : 'orange'"
      />
    </div>

    <div class="page-grid">
      <CardPanel title="站点基础配置" desc="站点名称、ICP 备案、Logo 与爬虫服务地址，会用于前端展示与后端服务调用。">
        <div class="field-grid two">
          <label class="field">
            <span>站点名称（siteName）</span>
            <input v-model="form.siteName" class="input" placeholder="闲鱼助手开源版" />
          </label>

          <label class="field">
            <span>ICP 备案号（icp）</span>
            <input v-model="form.icp" class="input" placeholder="例如：京ICP备XXXXXXXX号" />
          </label>

          <label class="field">
            <span>站点 Logo URL（logoUrl）</span>
            <input v-model="form.logoUrl" class="input" placeholder="例如：/static/logo.png" />
          </label>

          <label class="field">
            <span>爬虫服务地址（部署只读）</span>
            <input
              :value="form.crawlerBaseUrl"
              class="input"
              readonly
              aria-readonly="true"
              title="由服务端 CRAWLER_BASE_URL 配置并在重启后生效"
            />
          </label>
        </div>
      </CardPanel>

      <CardPanel
        title="商业版桥接状态"
        desc="开源版通过商业版后端拉取轮播图、文字广告与广告套餐，并提交广告投放申请。桥接令牌、商业版后台地址等敏感信息仅在服务端配置，前端不展示，部署方也无法在浏览器侧修改。商业版前台地址仅用于展示引流。"
      >
        <div v-if="!runtimeStatusAvailable" class="bridge-notice">
          运行状态暂不可用，无法确认商业版桥接状态。点击上方「重新加载」可重试。
        </div>
        <div v-else class="bridge-grid">
          <div class="bridge-row">
            <span class="bridge-label">桥接模式</span>
            <span
              class="bridge-value"
              :class="{ ok: runtimeStatus.commercialBridgeMode === 'commercial', warn: runtimeStatus.commercialBridgeMode !== 'commercial' }"
            >
              {{ runtimeStatus.commercialBridgeMode === 'commercial' ? '已对接商业版' : '本地兜底（未对接）' }}
            </span>
          </div>
          <div class="bridge-row">
            <span class="bridge-label">配置状态</span>
            <span
              class="bridge-value"
              :class="{ ok: runtimeStatus.commercialBridgeConfigured, warn: !runtimeStatus.commercialBridgeConfigured }"
            >
              {{ runtimeStatus.commercialBridgeConfigured ? '已配置' : '未配置' }}
            </span>
          </div>
          <div class="bridge-row">
            <span class="bridge-label">桥接连通性</span>
            <span
              class="bridge-value"
              :class="{ ok: runtimeStatus.commercialBridgeConnected, warn: !runtimeStatus.commercialBridgeConnected }"
            >
              {{ runtimeStatus.commercialBridgeConnected ? '已连通' : '未连通' }}
            </span>
          </div>
          <div class="bridge-row">
            <span class="bridge-label">管理端健康</span>
            <span
              class="bridge-value"
              :class="{ ok: runtimeStatus.commercialAdminHealthOk, warn: !runtimeStatus.commercialAdminHealthOk }"
            >
              {{ runtimeStatus.commercialAdminHealthOk ? '正常' : '不可达' }}
            </span>
          </div>
          <div class="bridge-row">
            <span class="bridge-label">用户端健康</span>
            <span
              class="bridge-value"
              :class="{ ok: runtimeStatus.commercialUserHealthOk, warn: !runtimeStatus.commercialUserHealthOk }"
            >
              {{ runtimeStatus.commercialUserHealthOk ? '正常' : '不可达' }}
            </span>
          </div>
          <div class="bridge-row">
            <span class="bridge-label">站点代码</span>
            <span class="bridge-value">{{ runtimeStatus.commercialBridgeSiteCode || '—' }}</span>
          </div>
          <div class="bridge-row">
            <span class="bridge-label">商业版前台</span>
            <span class="bridge-value mono">{{ runtimeStatus.commercialFrontendUrl || '—' }}</span>
          </div>
          <div v-if="runtimeStatus.commercialBridgeMessage" class="bridge-row">
            <span class="bridge-label">桥接消息</span>
            <span class="bridge-value">{{ runtimeStatus.commercialBridgeMessage }}</span>
          </div>
        </div>

        <div v-if="runtimeStatusAvailable" class="bridge-capabilities">
          <h4 class="capabilities-title">能力开关（fail-closed，默认全部关闭）</h4>
          <div class="capability-grid">
            <div class="capability-item">
              <span class="capability-name">写入幂等</span>
              <span
                class="capability-status"
                :class="{ ok: runtimeStatus.commercialMutationIdempotencyEnabled, warn: !runtimeStatus.commercialMutationIdempotencyEnabled }"
              >
                {{ runtimeStatus.commercialMutationIdempotencyEnabled ? '已开启' : '未开启' }}
              </span>
              <span class="capability-desc">广告申请写入支持幂等键，未开启时禁止提交申请。</span>
            </div>
            <div class="capability-item">
              <span class="capability-name">支付幂等</span>
              <span
                class="capability-status"
                :class="{ ok: runtimeStatus.commercialPaymentIdempotencyEnabled, warn: !runtimeStatus.commercialPaymentIdempotencyEnabled }"
              >
                {{ runtimeStatus.commercialPaymentIdempotencyEnabled ? '已开启' : '未开启' }}
              </span>
              <span class="capability-desc">支付订单支持幂等键，未开启时禁止创建订单。</span>
            </div>
            <div class="capability-item">
              <span class="capability-name">付费展示</span>
              <span
                class="capability-status"
                :class="{ ok: runtimeStatus.commercialPaidAdPlacementEnforced, warn: !runtimeStatus.commercialPaidAdPlacementEnforced }"
              >
                {{ runtimeStatus.commercialPaidAdPlacementEnforced ? '已开启' : '未开启' }}
              </span>
              <span class="capability-desc">仅已支付广告可激活展示，未开启时禁止展示广告。</span>
            </div>
          </div>
          <p v-if="!allBridgeCapabilitiesEnabled" class="bridge-hint">
            三个能力开关必须全部开启后，开源版才能完整使用广告展示与投放功能。开关由商业版后端通过契约测试后启用，开源版部署方无法直接修改。
          </p>
          <p v-else class="bridge-hint ok">
            全部能力已就绪，开源版可正常展示商业版广告并接受用户投放申请。
          </p>
        </div>
      </CardPanel>

      <CardPanel title="配置说明" desc="常见配置项的取值规则与注意事项。">
        <ul class="hint-list">
          <li><strong>站点名称</strong>：显示在浏览器标题栏和登录页，建议保持简短（建议 ≤ 16 个字符）。</li>
          <li><strong>ICP 备案号</strong>：中国大陆服务器必须填写，否则前端底部不显示备案信息。海外服务器可留空。</li>
          <li><strong>站点 Logo URL</strong>：可填写相对路径（如 <code>/static/logo.png</code>）或绝对 URL（如 <code>https://cdn.example.com/logo.png</code>）。</li>
          <li><strong>爬虫服务地址</strong>：该地址会接收账号授权信息，只能由部署方通过 <code>CRAWLER_BASE_URL</code> 配置并在重启后生效，浏览器页面不可修改。</li>
          <li><strong>通用模型 / 向量模型</strong>：分别到"模型配置"和"向量模型"页签配置。</li>
          <li><strong>RAG 知识库</strong>：到"RAG 知识库"页签管理文档与检索测试。</li>
        </ul>
      </CardPanel>

      <CardPanel title="知识库列表" desc="系统总览页保留一个轻量概览，便于确认 RAG 知识库模块是否已经接入。">
        <div class="knowledge-summary">
          <strong>{{ knowledgeBaseSummary.available ? knowledgeBaseSummary.total : '—' }}</strong>
          <span>当前知识库数量</span>
          <p v-if="!knowledgeBaseSummary.available" class="global-notice error">知识库概览暂不可用，当前无法确认数量。</p>
          <p>如需查看文档、切片与检索测试，请前往左侧“RAG 知识库”页签继续操作。</p>
        </div>
      </CardPanel>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive } from 'vue'
import StatCard from '../../components/StatCard.vue'
import AppButton from '../../components/AppButton.vue'
import CardPanel from '../../components/CardPanel.vue'
import { listKnowledgeBases } from '../../api/rag.js'
import {
  cloneOpenSourceConfig,
  useOpenSourceSettings,
} from '../../composables/useOpenSourceSettings.js'

const {
  loading,
  saving,
  error,
  success,
  config,
  runtimeStatus,
  configAvailable,
  runtimeStatusAvailable,
  loadBundle,
  saveConfig,
} = useOpenSourceSettings()

const form = reactive({
  siteName: '',
  icp: '',
  logoUrl: '',
  crawlerBaseUrl: '',
})

const knowledgeBaseSummary = reactive({
  total: 0,
  available: false,
})

const summary = computed(() => {
  return {
    generalModel: config.generalModel.modelName || '未配置',
    generalModelHint: runtimeStatusAvailable.value
      ? (runtimeStatus.generalModelConfigured ? '已具备必要配置' : '缺少必要参数')
      : '运行状态暂不可用',
    embeddingModel: config.embeddingModel.modelName || '未配置',
    embeddingModelHint: runtimeStatusAvailable.value
      ? (runtimeStatus.embeddingModelConfigured ? '已具备必要配置' : '缺少必要参数')
      : '运行状态暂不可用',
    redisStatus: runtimeStatusAvailable.value ? (runtimeStatus.redisConnected ? '已连接' : '不可用') : '状态未知',
    redisMode: runtimeStatusAvailable.value
      ? (runtimeStatus.redisConnected ? '共享 Redis 正常' : '认证与限流暂不可用')
      : '运行探测暂不可用'
  }
})

const allBridgeCapabilitiesEnabled = computed(() => {
  if (!runtimeStatusAvailable.value) return false
  return Boolean(runtimeStatus.commercialMutationIdempotencyEnabled)
    && Boolean(runtimeStatus.commercialPaymentIdempotencyEnabled)
    && Boolean(runtimeStatus.commercialPaidAdPlacementEnforced)
})

onMounted(() => {
  window.addEventListener('xya-header-action', onHeaderAction)
  loadPage()
})

onBeforeUnmount(() => {
  window.removeEventListener('xya-header-action', onHeaderAction)
})

function syncForm() {
  form.siteName = config.siteName || ''
  form.icp = config.icp || ''
  form.logoUrl = config.logoUrl || ''
  form.crawlerBaseUrl = config.crawlerBaseUrl || ''
}

async function loadPage() {
  await Promise.all([
    loadBundle({ includeRuntimeStatus: true }),
    loadKnowledgeBaseSummary(),
  ])
  if (configAvailable.value) syncForm()
}

async function loadKnowledgeBaseSummary() {
  try {
    const res = await listKnowledgeBases({ current: 1, size: 1 })
    knowledgeBaseSummary.total = Number(res?.data?.total) || 0
    knowledgeBaseSummary.available = true
  } catch {
    knowledgeBaseSummary.available = false
  }
}

async function save() {
  if (!configAvailable.value) return
  const payload = cloneOpenSourceConfig(config)
  payload.siteName = form.siteName.trim()
  payload.icp = form.icp.trim()
  payload.logoUrl = form.logoUrl.trim()
  delete payload.crawlerBaseUrl
  const saved = await saveConfig(payload, { successMessage: '站点基础配置已保存' })
  if (!saved) return
  syncForm()
}

function onHeaderAction(event) {
  if (event.detail === 'settings-save') save()
  if (event.detail === 'settings-reload') loadPage()
}
</script>

<style scoped>
.admin-system-settings {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.page-hero {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 16px;
  padding: 22px;
  border-radius: 24px;
  border: 1px solid rgba(231, 237, 247, 0.95);
  background:
    radial-gradient(circle at top left, rgba(37, 99, 235, 0.12), transparent 32%),
    linear-gradient(180deg, rgba(255, 255, 255, 0.98), rgba(248, 251, 255, 0.92));
  box-shadow: 0 18px 42px rgba(31, 53, 94, 0.08);
}

.page-pill {
  display: inline-flex;
  align-items: center;
  min-height: 26px;
  padding: 0 10px;
  border-radius: 999px;
  background: rgba(37, 99, 235, 0.08);
  color: #2c63d4;
  font-size: 11px;
  font-weight: 800;
}

.page-hero-copy h1 {
  margin: 10px 0 0;
  font-size: 28px;
  color: #13213d;
}

.page-hero-copy p {
  margin: 10px 0 0;
  max-width: 760px;
  line-height: 1.8;
  color: #60738e;
}

.page-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 18px;
}

.stat-grid {
  display: grid;
  gap: 12px;
}

.page-grid {
  display: grid;
  gap: 16px;
}

.field-grid {
  display: grid;
  gap: 14px;
}

.field-grid.two {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.field {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.field span {
  font-size: 13px;
  font-weight: 700;
  color: #6a7c98;
}

.field .input {
  height: 40px;
  padding: 0 12px;
  border-radius: 10px;
  border: 1px solid #d6deeb;
  background: #fff;
  font-size: 14px;
  color: #1f2a44;
  outline: none;
  transition: border-color 0.18s ease, box-shadow 0.18s ease;
}

.field .input:focus {
  border-color: #2563eb;
  box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.15);
}

.hint-list {
  margin: 0;
  padding-left: 18px;
  color: #667892;
  line-height: 1.8;
}

.knowledge-summary {
  padding: 18px;
  border-radius: 18px;
  border: 1px dashed #d4dff1;
  background: linear-gradient(135deg, #fbfdff, #f6f9ff);
}

.knowledge-summary strong {
  display: block;
  color: #13213d;
  font-size: 30px;
  line-height: 1;
}

.knowledge-summary span {
  display: block;
  margin-top: 8px;
  color: #2c63d4;
  font-size: 13px;
  font-weight: 700;
}

.knowledge-summary p {
  margin: 10px 0 0;
  color: #6e7e98;
  line-height: 1.7;
}

.bridge-notice {
  padding: 14px 16px;
  border-radius: 12px;
  border: 1px dashed #d4dff1;
  background: linear-gradient(135deg, #fbfdff, #f6f9ff);
  color: #6e7e98;
  font-size: 13px;
  line-height: 1.7;
}

.bridge-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px 18px;
  padding: 14px 16px;
  border-radius: 14px;
  border: 1px dashed #d4dff1;
  background: linear-gradient(135deg, #fbfdff, #f6f9ff);
}

.bridge-row {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.bridge-label {
  font-size: 12px;
  font-weight: 700;
  color: #6a7c98;
}

.bridge-value {
  font-size: 14px;
  color: #1f2a44;
  word-break: break-all;
}

.bridge-value.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 13px;
}

.bridge-value.ok {
  color: #1f8a4c;
  font-weight: 700;
}

.bridge-value.warn {
  color: #b26a00;
  font-weight: 700;
}

.bridge-capabilities {
  margin-top: 14px;
  padding: 14px 16px;
  border-radius: 14px;
  border: 1px solid #e3eaf5;
  background: #fff;
}

.capabilities-title {
  margin: 0 0 10px;
  font-size: 14px;
  font-weight: 800;
  color: #13213d;
}

.capability-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}

.capability-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 12px;
  border-radius: 10px;
  border: 1px solid #e3eaf5;
  background: #fbfdff;
}

.capability-name {
  font-size: 13px;
  font-weight: 700;
  color: #2c63d4;
}

.capability-status {
  font-size: 13px;
  font-weight: 700;
}

.capability-status.ok {
  color: #1f8a4c;
}

.capability-status.warn {
  color: #b26a00;
}

.capability-desc {
  font-size: 12px;
  color: #6e7e98;
  line-height: 1.6;
}

.bridge-hint {
  margin: 12px 0 0;
  padding: 10px 12px;
  border-radius: 10px;
  background: rgba(178, 106, 0, 0.08);
  color: #8a4a00;
  font-size: 12px;
  line-height: 1.7;
}

.bridge-hint.ok {
  background: rgba(31, 138, 76, 0.08);
  color: #1f6b3a;
}

@media (max-width: 1100px) {
  .bridge-grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .capability-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}

.hint-list code {
  background: rgba(37, 99, 235, 0.08);
  color: #2c63d4;
  padding: 1px 6px;
  border-radius: 6px;
  font-size: 12px;
}

.hint-list strong {
  color: #13213d;
}

@media (max-width: 1200px) {
  .stat-grid {
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) !important;
  }
}

@media (max-width: 920px) {
  .stat-grid {
    grid-template-columns: minmax(0, 1fr) !important;
  }

  .field-grid.two {
    grid-template-columns: minmax(0, 1fr);
  }
}

@media (max-width: 900px) {
  .admin-system-settings {
    gap: 12px;
  }

  .page-hero {
    padding: 14px;
    border-radius: 16px;
  }

  .page-hero-copy h1 {
    font-size: 20px;
  }

  .page-hero-copy p {
    font-size: 13px;
    line-height: 1.6;
  }

  .page-actions {
    gap: 8px;
    margin-top: 12px;
  }

  .stat-grid {
    grid-template-columns: minmax(0, 1fr) !important;
    gap: 10px;
  }

  .page-grid {
    gap: 12px;
  }

  .field-grid {
    gap: 12px;
  }

  .field-grid.two {
    grid-template-columns: minmax(0, 1fr);
  }

  .stat-grid > *,
  .field-grid.two > * {
    min-width: 0;
  }

  .field .input {
    height: 40px;
    font-size: 13px;
  }

  .knowledge-summary {
    padding: 12px;
    border-radius: 14px;
  }

  .knowledge-summary strong {
    font-size: 22px;
  }

  .knowledge-summary span {
    font-size: 12px;
  }

  .knowledge-summary p {
    font-size: 13px;
    line-height: 1.6;
  }

  .hint-list {
    padding-left: 16px;
    font-size: 13px;
    line-height: 1.7;
  }

  .hint-list code {
    font-size: 11px;
    word-break: break-all;
  }
}
</style>
