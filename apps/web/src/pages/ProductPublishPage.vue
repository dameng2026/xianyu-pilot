<template>
  <div class="publish-layout">
    <div>
      <div v-if="error" class="global-notice error">{{ error }}</div>
      <div v-if="warning" class="global-notice warning">{{ warning }}</div>
      <div v-if="success" class="global-notice success">{{ success }}</div>
      <div v-if="publishIntent.payload" class="global-notice warning publish-intent-notice" role="status">
        <b>当前是恢复中的已持久化发布意图</b>
        <span>本次操作固定使用下列数据和原幂等键；页面表单的后续改动不会进入该恢复请求。</span>
        <pre>{{ persistedIntentSummary }}</pre>
      </div>

      <div class="draft-toolbar">
        <span class="draft-tip">📝 草稿会自动保存本页已输入内容，仅保留上一次未发布的草稿</span>
        <button type="button" class="clear-draft-btn" @click="clearAllData">清空数据</button>
      </div>

      <CardPanel title="宝贝基础信息">
        <div class="form-grid">
          <div class="form-row">
            <label>闲鱼账号</label>
            <select v-model="form.accountId" :disabled="accountsAvailable !== true">
              <option value="">请选择账号</option>
              <option v-for="a in accounts" :key="a.id" :value="a.id">{{ accountName(a) }}</option>
            </select>
            <span v-if="accountsAvailable === false" class="field-error">账号列表暂不可用，当前禁止发布。</span>
          </div>
          <div class="form-row">
            <label>宝贝标题</label>
            <input v-model="form.title" maxlength="30" placeholder="请填写宝贝标题，建议包含品牌、规格、成色等关键信息">
            <span class="char-count">{{ form.title.length }}/30</span>
          </div>
          <div class="form-row">
            <label>宝贝描述</label>
            <textarea v-model="form.description" rows="4" placeholder="请详细描述宝贝的成色、功能、使用感受等信息..."></textarea>
            <div class="chips">
              <button type="button" class="chip" :disabled="aiDescLoading" @click="aiDesc">{{ aiDescLoading ? 'AI 生成中...' : 'AI 生成描述' }}</button>
              <button type="button" class="chip" @click="insertPhrase">插入常用语</button>
            </div>
            <p v-if="!aiCategoryStatus.configured" class="ai-unconfigured-tip">
              {{ aiCategoryStatus.message || '未配置通用模型，AI 生成功能当前不可用。' }}
            </p>
          </div>
        </div>

        <!-- 图片上传区域 -->
        <div style="margin-top:18px">
          <b>宝贝图片（{{ form.imageUrls.length }}/10 张，拖拽可调整顺序，支持 Ctrl+V 粘贴）</b>
          <div class="image-strip" style="margin-top:12px">
            <div
              v-for="(img, idx) in form.imageUrls"
              :key="idx"
              class="img-card"
              draggable="true"
              @dragstart="onDragStart(idx, $event)"
              @dragover.prevent="onDragOver(idx, $event)"
              @drop="onDrop(idx, $event)"
            >
              <img :src="resolveTrustedMediaUrl(img)" alt="" style="width:100%;height:100%;object-fit:cover;border-radius:10px">
              <button type="button" class="img-remove" :disabled="uploadingImages" :aria-label="`移除第 ${idx + 1} 张图片`" @click="removeImage(idx)">×</button>
            </div>
            <button
              v-if="form.imageUrls.length < 10"
              type="button"
              class="img-card add-card"
              :disabled="uploadingImages"
              @click="triggerUpload"
            >
              <span style="font-size:28px;color:#999">{{ uploadingImages ? '…' : '＋' }}</span>
              <span style="font-size:12px;color:#999;margin-top:4px">{{ uploadingImages ? '上传中，请稍候' : '上传图片' }}</span>
            </button>
            <input
              ref="fileInput"
              type="file"
              accept="image/jpeg,image/png,image/gif,image/webp"
              multiple
              :disabled="uploadingImages"
              style="display:none"
              @change="onFileSelect"
            >
          </div>
          <!-- URL 导入封面图 -->
          <div class="image-url-bar">
            <input
              v-model="imageUrlInput"
              type="url"
              class="image-url-input"
              placeholder="粘贴图片 URL（http:// 或 https://）也可导入封面图"
              :disabled="imageUrlLoading"
              @keydown.enter.prevent="addImageFromUrl"
            >
            <button
              type="button"
              class="image-url-btn"
              :disabled="imageUrlLoading || !imageUrlInput.trim()"
              @click="addImageFromUrl"
            >
              {{ imageUrlLoading ? '导入中...' : 'URL 导入' }}
            </button>
          </div>
          <p class="image-hint subtle">
            支持 Ctrl+V 直接粘贴剪贴板里的图片，也可在上方输入图片 URL 后点击「URL 导入」。仅支持 JPEG/PNG/GIF/WebP，单张 ≤ 5MB。
          </p>
        </div>
      </CardPanel>

      <CardPanel title="商品分类" style="margin-top:16px">
        <div class="category-selector">
          <div class="auto-category-hint">
            <span class="hint-icon">💡</span>
            <span>上传封面图之后自动获取分类</span>
            <span v-if="autoCategoryLoading" class="auto-category-spinner">检测中...</span>
          </div>
          <div v-if="autoCategoryMessage" :class="['auto-category-msg', autoCategoryMsgType]">
            {{ autoCategoryMessage }}
          </div>
          <div v-if="autoCategoryCandidates.length" class="auto-category-candidates">
            <span class="candidates-label">推荐分类：</span>
            <button
              v-for="(cat, idx) in autoCategoryCandidates"
              :key="cat.catId || idx"
              type="button"
              :class="['candidate-btn', { active: autoSelectedCatId === (cat.catId || cat.catName) }]"
              @click="applyAutoCategory(cat)"
            >
              {{ cat.catName || cat.catName }}
              <small v-if="cat.score">({{ (cat.score * 100).toFixed(1) }}%)</small>
            </button>
          </div>
          <div class="category-tools">
            <input v-model="categoryKeyword" class="category-search" placeholder="搜索分类，如 手机 / 图书 / 家居" />
            <button v-if="aiCategoryStatus.configured" type="button" class="category-ai-btn" :disabled="aiCategoryLoading || !form.title.trim()" @click="autoSelectCategory">{{ aiCategoryLoading ? 'AI选择中...' : 'AI自动选择' }}</button>
            <button v-if="categoryKeyword" type="button" class="category-clear" @click="categoryKeyword = ''">清空</button>
          </div>
          <p v-if="!aiCategoryStatus.configured" class="ai-unconfigured-tip">
            {{ aiCategoryStatus.message || '未配置通用模型，AI 自动选分类当前不可用。' }}
          </p>
          <button
            v-if="!aiCategoryStatus.configured"
            type="button"
            class="category-ai-btn category-ai-btn-blocked"
            :disabled="aiCategoryLoading || !form.title.trim()"
            @click="autoSelectCategory"
          >
            AI 自动选择
          </button>
          <div v-if="categoryKeyword" class="category-search-results">
            <div
              v-for="item in categorySearchResults"
              :key="item.pathIds.join('-')"
              class="category-result"
              @click="selectCategoryByPath(item)"
            >
              <strong>{{ item.name }}</strong>
              <span>{{ item.path }}</span>
            </div>
            <div v-if="!categorySearchResults.length" class="category-result muted">未找到匹配分类</div>
          </div>
          <div v-if="favoriteCategories.length" class="recent-categories favorite-categories">
            <span>收藏分类：</span>
            <button v-for="item in favoriteCategories" :key="item.path" type="button" @click="selectCategoryByPath(item)">{{ item.name }}</button>
          </div>
          <div v-if="recentCategories.length" class="recent-categories">
            <span>最近使用：</span>
            <button v-for="item in recentCategories" :key="item.path" type="button" @click="selectCategoryByPath(item)">{{ item.name }}</button>
            <button type="button" class="category-link" @click="clearRecentCategories">清除</button>
          </div>
          <div v-if="selectedCategoryPath" class="category-actions">
            <button type="button" @click="toggleFavoriteCategory">{{ isFavoriteCategory ? '取消收藏当前分类' : '收藏当前分类' }}</button>
          </div>
          <div class="cascader-levels">
            <div class="cascader-col">
              <div v-if="categoriesLoading" class="cascader-item muted">分类加载中...</div>
              <div v-else-if="!categories.length" class="cascader-item muted">暂无分类</div>
              <div
                v-for="cat in filteredCategories"
                :key="cat.id"
                :class="['cascader-item', { active: level1Id === cat.id }]"
                @click="selectLevel1(cat)"
              >
{{ cat.label || cat.title }}
</div>
            </div>
            <div v-if="level2List.length" class="cascader-col">
              <div
                v-for="cat in level2List"
                :key="cat.id"
                :class="['cascader-item', { active: level2Id === cat.id }]"
                @click="selectLevel2(cat)"
              >
{{ cat.label || cat.title }}
</div>
            </div>
            <div v-if="level3List.length" class="cascader-col">
              <div
                v-for="cat in level3List"
                :key="cat.id"
                :class="['cascader-item', { active: level3Id === cat.id }]"
                @click="selectLevel3(cat)"
              >
{{ cat.label || cat.title }}
</div>
            </div>
          </div>
          <p class="subtle" style="margin-top:8px">
            已选分类：{{ selectedCategoryPath || '请选择分类' }}
            <span v-if="aiCategoryMessage" class="ai-category-tip">{{ aiCategoryMessage }}</span>
          </p>
        </div>
      </CardPanel>

      <CardPanel title="商品位置" style="margin-top:16px">
        <PublishAddressCascader v-model="selectedAddress" clearable />
      </CardPanel>

      <CardPanel title="商品价格与规格" style="margin-top:16px">
        <div class="form-grid">
          <div class="form-row">
            <label>售价（元）</label>
            <input v-model="form.price" type="number" step="0.01" min="0" placeholder="0.00">
          </div>
          <div class="form-row">
            <label>库存</label>
            <input v-model="form.stock" type="number" placeholder="1">
          </div>
        </div>
      </CardPanel>

      <CardPanel title="自动发货" style="margin-top:16px">
        <div class="auto-delivery-section">
          <div class="auto-delivery-toggle">
            <div class="auto-delivery-toggle-info">
              <span class="auto-delivery-title">开启自动发货</span>
              <span class="subtle">本功能为项目内置自动发货，非闲鱼发布功能。开启后，买家付款将自动发送所选货源的发货内容</span>
            </div>
            <ToggleSwitch :on="autoDelivery.enabled" @click="toggleAutoDelivery" />
          </div>
          <div v-if="autoDelivery.enabled" class="auto-delivery-source-row">
            <label>关联货源库</label>
            <select v-model="autoDelivery.sourceId" class="input" :disabled="!sourcesAvailable || sourcesLoading">
              <option value="">请选择货源</option>
              <option v-for="source in deliverySources" :key="source.id" :value="source.id">
                {{ source.title }}<span v-if="source.usageCount != null">（已绑 {{ source.usageCount }} 件）</span>
              </option>
            </select>
            <!-- 加载中 -->
            <div v-if="sourcesLoading" class="subtle">货源库加载中...</div>
            <!-- 加载失败：提供重试 -->
            <div v-else-if="sourcesError" class="auto-delivery-msg error">
              <span>{{ sourcesError }}</span>
              <button type="button" class="ad-action-btn" @click="reloadDeliverySources">重试</button>
              <button type="button" class="ad-action-btn" @click="goToSourceLibrary">前往货源库</button>
            </div>
            <!-- 已选货源：显示已绑数量 -->
            <div v-else-if="autoDelivery.sourceId" class="subtle">
              已选货源：{{ selectedSourceTitle }}<span v-if="selectedSourceUsageCount != null">（已绑 {{ selectedSourceUsageCount }} 件）</span>
            </div>
            <!-- 暂无货源：引导前往货源库创建 -->
            <div v-else-if="sourcesAvailable && !deliverySources.length" class="auto-delivery-empty">
              <span class="subtle warn">暂无货源，请先到「货源库」创建</span>
              <button type="button" class="ad-action-btn primary" @click="goToSourceLibrary">前往货源库创建</button>
            </div>
            <!-- 已有货源但未选 -->
            <div v-else class="subtle warn">请选择要绑定的货源</div>
          </div>
        </div>
      </CardPanel>

      <CardPanel title="发货设置" style="margin-top:16px">
        <div class="shipping-grid">
          <div class="shipping-item">
            <span>包邮</span>
            <ToggleSwitch :on="shippingMode === 'free'" @click="setShipping('free')" />
          </div>
          <div class="shipping-item">
            <span>一口价 / 运费</span>
            <ToggleSwitch :on="shippingMode === 'fixed'" @click="setShipping('fixed')" />
          </div>
          <div class="shipping-item">
            <span>无需邮寄</span>
            <ToggleSwitch :on="shippingMode === 'none'" @click="setShipping('none')" />
          </div>
          <div class="shipping-item">
            <span>支持自提</span>
            <ToggleSwitch :on="form.supportSelfPick" @click="form.supportSelfPick = !form.supportSelfPick" />
          </div>
        </div>
      </CardPanel>
      <div style="height:90px"></div>
    </div>

    <div>
      <CardPanel title="商品预览">
        <div class="product-cell">
          <div class="product-thumb" style="width:130px;height:98px">
            <img v-if="form.imageUrls.length > 0" :src="resolveTrustedMediaUrl(form.imageUrls[0])" alt="" style="width:100%;height:100%;object-fit:cover;border-radius:10px">
            <div v-else style="width:100%;height:100%;background:#f0f0f0;border-radius:10px;display:flex;align-items:center;justify-content:center;color:#ccc;font-size:12px">暂无图片</div>
          </div>
          <div>
            <h3 style="margin:0 0 8px">{{ form.title || '商品标题' }}</h3>
            <b style="color:#ef4444;font-size:22px">¥{{ displayPrice }}</b>
          </div>
        </div>
      </CardPanel>
      <CardPanel title="发布摘要" style="margin-top:16px">
        <div class="option-line"><span>闲鱼账号</span><b>{{ selectedAccount || '未选择' }}</b></div>
        <div class="option-line"><span>商品分类</span><b>{{ selectedCategoryPath || '未选择' }}</b></div>
        <div class="option-line"><span>商品位置</span><b>{{ selectedAddress ? `${selectedAddress.prov} ${selectedAddress.city} ${selectedAddress.area}`.trim() : '未选择' }}</b></div>
        <div class="option-line"><span>总库存</span><b>{{ totalStock }}件</b></div>
        <div class="option-line"><span>运费模式</span><b>{{ shippingLabel }}</b></div>
      </CardPanel>
      <CardPanel title="发布检查" style="margin-top:16px">
        <div v-for="i in checks" :key="i.text" class="option-line">
          <span><i :class="['dot', i.ok ? '' : 'orange']"></i>{{ i.text }}</span>
          <b :style="{color:i.ok?'var(--green)':'#f59e0b'}">{{ i.ok ? '通过' : '待完善' }}</b>
        </div>
      </CardPanel>
    </div>

    <div class="bottom-actions">
      <AppButton @click="handleCancel">取消</AppButton>
      <AppButton type="primary" :loading="submitting" :disabled="publishSubmitDisabled" @click="submit">{{ publishSubmitLabel }}</AppButton>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import CardPanel from '../components/CardPanel.vue'
import AppButton from '../components/AppButton.vue'
import ToggleSwitch from '../components/ToggleSwitch.vue'
import PublishAddressCascader from '../components/PublishAddressCascader.vue'
import { getAccounts, checkAccountAuth } from '../api/accounts.js'
import { publishItem, autoCategory } from '../api/items.js'
import { uploadImage, uploadImageFromUrl } from '../api/misc.js'
import { runtimeConfig } from '../api/system.js'
import { getGoods } from '../api/goods.js'
import { getDeliverySources, applyDeliverySourceToGoods } from '../api/autoDelivery.js'
import { accountName } from '../utils/format.js'
import { friendlyError } from '../utils/friendlyError.js'
import { recordsOf } from '../utils/apiData.js'
import { confirmAction } from '../utils/confirmAction.js'
import { getAiProviderStatus, suggestCategoryByAi } from '../api/aiProvider.js'
import { fetchCategories } from '../api/categories.js'
import { aiRewriteGoods } from '../api/workflow.js'
import {
  buildPublishIntentSummary,
  canSubmitPublishIntent,
  captureProductAsyncContext,
  isProductAsyncRequestCurrent,
  markPendingProductSync,
} from '../utils/productPublishState.js'
import { imageUploadValidationMessage } from '../utils/imageUploadPolicy.js'
import { accountAuthUsable, pickPreferredAccount } from '../utils/accountAuth.js'
import { resolveTrustedMediaUrl } from '../utils/safeMediaUrl.js'
import { loadPublishDraft, savePublishDraft, clearPublishDraft } from '../utils/publishDraft.js'
import { setNavigationGuard, clearNavigationGuard } from '../utils/navigationGuard.js'
import { promptDraftChoice } from '../composables/draftGuardState.js'

const emit = defineEmits(['navigate'])
const accounts = ref([])
const error = ref('')
const warning = ref('')
const success = ref('')
const submitting = ref(false)
const publishOutcome = ref(null)
const publishIntent = reactive({ idempotencyKey: '', payload: null })
const PUBLISH_INTENT_KEY = 'xianyu_publish_intent_v1'
const unsafePublishFailure = computed(() => {
  const outcome = publishOutcome.value
  return outcome?.status === 'failed' && outcome?.retrySafe !== true
})
const publishSubmitDisabled = computed(() => !canSubmitPublishIntent({
  accountsAvailable: accountsAvailable.value,
  submitting: submitting.value,
  hasIntent: Boolean(publishIntent.payload),
  outcome: publishOutcome.value,
}))
const publishSubmitLabel = computed(() => {
  if (publishOutcome.value?.status === 'remote_confirmed') return '仅修复本地状态'
  if (publishOutcome.value?.status === 'unknown') return '结果未知，禁止重试'
  if (publishOutcome.value?.status === 'in_progress') return '发布执行中'
  if (unsafePublishFailure.value) return '失败不可安全重试'
  if (publishOutcome.value?.status === 'failed' && publishOutcome.value?.retrySafe) return '使用原意图重试'
  return '立即发布'
})
const fileInput = ref(null)
const uploadingImages = ref(false)
let uploadRequestVersion = 0
const accountsAvailable = ref(null)
const runtime = reactive({ defaultCity: '', defaultAddress: '' })
const dragIndex = ref(-1)

// ---- 分类级联 ----
const categories = ref([])
const categoriesLoading = ref(false)
const level1Id = ref(null)
const level2Id = ref(null)
const level3Id = ref(null)
const level2List = ref([])
const level3List = ref([])
const categoryKeyword = ref('')
const recentCategories = ref([])
const favoriteCategories = ref([])
const selectedCategoryPath = ref('')
const selectedCategoryName = ref('')
const aiCategoryStatus = ref({ configured: false, message: '' })
const aiCategoryLoading = ref(false)
const aiCategoryMessage = ref('')

// ---- 自动分类（闲鱼接口） ----
const autoCategoryLoading = ref(false)
const autoCategoryMessage = ref('')
const autoCategoryMsgType = ref('info')
const autoCategoryCandidates = ref([])
const autoSelectedCatId = ref('')
const autoCategorySource = ref(null) // 'xianyu_auto' | 'local_category' | null
const userManuallySelectedCategory = ref(false) // 用户是否手动改过分类
let categoryRevision = 0
let aiCategoryRequestVersion = 0
let autoCategoryRequestVersion = 0

function productAsyncContext() {
  return captureProductAsyncContext({
    accountId: form.accountId,
    title: form.title,
    description: form.description,
    coverImageUrl: form.imageUrls[0],
    categoryRevision,
  })
}

function markManualCategorySelection() {
  categoryRevision += 1
  userManuallySelectedCategory.value = true
  aiCategoryRequestVersion += 1
  autoCategoryRequestVersion += 1
  aiCategoryLoading.value = false
  autoCategoryLoading.value = false
}

function selectLevel1(cat, { manual = true } = {}) {
  if (manual) markManualCategorySelection()
  level1Id.value = cat.id
  level2Id.value = null
  level3Id.value = null
  level2List.value = cat.children || []
  level3List.value = []
  selectedCategoryName.value = cat.label || cat.title
  selectedCategoryPath.value = cat.label || cat.title
  if (!level2List.value.length) rememberCategory({ name: selectedCategoryName.value, path: selectedCategoryPath.value, pathIds: [cat.id] })
}

function selectLevel2(cat, { manual = true } = {}) {
  if (manual) markManualCategorySelection()
  level2Id.value = cat.id
  level3Id.value = null
  level3List.value = cat.children || []
  const l1 = categories.value.find(c => c.id === level1Id.value)
  const path = (l1?.label || l1?.title) + ' ＞ ' + (cat.label || cat.title)
  selectedCategoryName.value = cat.label || cat.title
  selectedCategoryPath.value = path
  if (!level3List.value.length) rememberCategory({ name: selectedCategoryName.value, path, pathIds: [l1?.id, cat.id].filter(Boolean) })
}

function selectLevel3(cat, { manual = true } = {}) {
  if (manual) markManualCategorySelection()
  level3Id.value = cat.id
  const l1 = categories.value.find(c => c.id === level1Id.value)
  const l2 = level2List.value.find(c => c.id === level2Id.value)
  const path = (l1?.label || l1?.title) + ' ＞ ' + (l2?.label || l2?.title) + ' ＞ ' + (cat.label || cat.title)
  selectedCategoryName.value = cat.label || cat.title
  selectedCategoryPath.value = path
  rememberCategory({ name: selectedCategoryName.value, path, pathIds: [l1?.id, l2?.id, cat.id].filter(Boolean) })
}


const filteredCategories = computed(() => {
  if (!categoryKeyword.value.trim()) return categories.value
  const kw = categoryKeyword.value.trim().toLowerCase()
  return categories.value.filter(cat => String(cat.label || cat.title || '').toLowerCase().includes(kw) || (cat.children || []).some(child => String(child.label || child.title || '').toLowerCase().includes(kw)))
})

const isFavoriteCategory = computed(() => favoriteCategories.value.some(item => item.path === selectedCategoryPath.value))

const categorySearchResults = computed(() => {
  const kw = categoryKeyword.value.trim().toLowerCase()
  if (!kw) return []
  const res = []
  const walk = (nodes, parents = [], parentIds = []) => {
    for (const node of nodes || []) {
      const name = node.label || node.title || ''
      const pathParts = [...parents, name]
      const pathIds = [...parentIds, node.id]
      if (String(name).toLowerCase().includes(kw)) {
        res.push({ name, path: pathParts.join(' ＞ '), pathIds })
      }
      if (res.length < 20) walk(node.children || [], pathParts, pathIds)
      if (res.length >= 20) return
    }
  }
  walk(categories.value)
  return res.slice(0, 20)
})

function flatCategoryOptions(limit = 5000) {
  const res = []
  const walk = (nodes, parents = [], parentIds = []) => {
    for (const node of nodes || []) {
      if (res.length >= limit) return
      const name = node.label || node.title || ''
      const pathParts = [...parents, name]
      const pathIds = [...parentIds, node.id]
      const children = node.children || []
      if (!children.length) {
        res.push({ id: node.id, name, path: pathParts.join(' ＞ '), pathIds })
      } else {
        walk(children, pathParts, pathIds)
      }
    }
  }
  walk(categories.value)
  return res
}

function findCategoryOption(category) {
  if (!category) return null
  const options = flatCategoryOptions(5000)
  const id = String(category.id || category.categoryId || '')
  const path = String(category.path || category.categoryPath || '')
  const name = String(category.name || category.categoryName || '')
  return options.find(item =>
    (id && String(item.id) === id) ||
    (path && item.path === path) ||
    (name && (item.name === name || item.path.endsWith(name)))
  ) || null
}

async function loadAiCategoryStatus() {
  try {
    const res = await getAiProviderStatus()
    aiCategoryStatus.value = res?.data || { configured: false, message: '' }
  } catch {
    if (import.meta.env.DEV) console.warn('[loadAiCategoryStatus] failed')
    aiCategoryStatus.value = {
      configured: false,
      message: '未能读取 AI 模型配置状态，请稍后重试。'
    }
  }
}

function aiStatusMessage(defaultMessage = '未配置通用模型，请先前往系统设置中的“模型配置”完成配置。') {
  return aiCategoryStatus.value?.message || defaultMessage
}

async function autoSelectCategory() {
  if (aiCategoryLoading.value) return
  if (!aiCategoryStatus.value.configured) {
    error.value = aiStatusMessage('未配置通用模型，暂时无法使用 AI 自动选分类。')
    aiCategoryMessage.value = error.value
    return
  }
  if (!form.title.trim()) { error.value = '请先填写宝贝标题，AI才能判断分类'; return }
  const options = flatCategoryOptions(5000)
  if (!options.length) { error.value = '分类数据尚未加载完成'; return }
  const requestVersion = ++aiCategoryRequestVersion
  const requestContext = productAsyncContext()
  aiCategoryLoading.value = true
  aiCategoryMessage.value = ''
  error.value = ''
  try {
    const res = await suggestCategoryByAi({
      title: requestContext.title,
      description: requestContext.description,
      categories: options,
    })
    if (!isProductAsyncRequestCurrent({
      requestVersion,
      currentVersion: aiCategoryRequestVersion,
      snapshot: requestContext,
      current: productAsyncContext(),
      fields: ['title', 'description', 'categoryRevision'],
    })) return
    const data = res?.data || {}
    if (data.enabled === false || data.configured === false) {
      aiCategoryStatus.value = {
        configured: false,
        message: data.message || data.reason || aiStatusMessage()
      }
      error.value = aiCategoryStatus.value.message
      return
    }
    if (!data.matched) {
      error.value = data.error || data.message || 'AI未能匹配到合适分类，请手动选择'
      return
    }
    const matched = findCategoryOption(data.category) || data.category
    selectCategoryByPath(matched, { manual: false })
    aiCategoryMessage.value = data.reason ? `AI已选择：${data.reason}` : 'AI已自动选择分类'
  } catch (e) {
    if (requestVersion === aiCategoryRequestVersion) error.value = e.message || 'AI自动选择分类失败'
  } finally {
    if (requestVersion === aiCategoryRequestVersion) aiCategoryLoading.value = false
  }
}

// ---- 自动分类（上传封面图后触发） ----
async function triggerAutoCategory() {
  if (!form.accountId) {
    autoCategoryMessage.value = '请先选择闲鱼账号'
    autoCategoryMsgType.value = 'warn'
    return
  }
  if (!form.imageUrls.length) {
    return
  }
  // 如果用户已手动选择分类，不要覆盖
  if (userManuallySelectedCategory.value) {
    autoCategoryMessage.value = '已手动选择分类，重新上传封面图可再次自动识别'
    autoCategoryMsgType.value = 'info'
    return
  }
  const coverImageUrl = form.imageUrls[0]
  const requestAccountId = form.accountId
  const requestVersion = ++autoCategoryRequestVersion
  const requestContext = productAsyncContext()
  autoCategoryLoading.value = true
  autoCategoryMessage.value = '正在识别商品分类...'
  autoCategoryMsgType.value = 'info'
  autoCategoryCandidates.value = []
  autoSelectedCatId.value = ''
  try {
    const res = await autoCategory(requestAccountId, {
      coverImageUrl,
      title: requestContext.title || undefined,
      description: requestContext.description || undefined,
    })
    if (!isProductAsyncRequestCurrent({
      requestVersion,
      currentVersion: autoCategoryRequestVersion,
      snapshot: requestContext,
      current: productAsyncContext(),
      fields: ['accountId', 'title', 'description', 'coverImageUrl', 'categoryRevision'],
    })) return
    const data = res?.data || {}
    if (!data.success) {
      // 自动分类失败，如果是 cookie 问题提示重新登录
      if (data.fallbackReason === 'COOKIE_EXPIRED' || data.fallbackReason === 'COOKIE_MISSING_M_H5_TK') {
        autoCategoryMessage.value = '账号 Cookie 已失效，请重新登录后再试'
        autoCategoryMsgType.value = 'error'
      } else if (data.fallbackReason && data.fallbackReason.includes('LOW_CONFIDENCE')) {
        autoCategoryMessage.value = '封面图自动识别置信度不足，已切换到本地分类'
        autoCategoryMsgType.value = 'warn'
        // 展示能检测到的候选
        if (data.candidates && data.candidates.length) {
          autoCategoryCandidates.value = data.candidates
        }
      } else {
        const reason = data.fallbackReason ? `（${data.fallbackReason}）` : ''
        autoCategoryMessage.value = `封面图自动识别失败，请手动选择分类${reason}`
        autoCategoryMsgType.value = 'warn'
        // 展示能检测到的候选
        if (data.candidates && data.candidates.length) {
          autoCategoryCandidates.value = data.candidates
        }
      }
      autoCategorySource.value = data.source || null
      return
    }
    // 自动分类成功
    autoCategorySource.value = 'xianyu_auto'
    const selected = data.selectedCategory
    const candidates = data.candidates || []
    autoCategoryCandidates.value = candidates
    if (selected) {
      // 尝试在本地分类树中找到匹配项
      const option = findCategoryOption(selected)
      if (option) {
        selectCategoryByPath(option, { manual: false })
        autoSelectedCatId.value = selected.catId || selected.catName || ''
        autoCategoryMessage.value = '已根据封面图自动识别分类'
        autoCategoryMsgType.value = 'success'
      } else if (candidates.length) {
        // 未匹配到本地分类树，展示候选分类让用户手动点选
        autoCategoryMessage.value = '已识别候选分类，请点击选择'
        autoCategoryMsgType.value = 'info'
        autoSelectedCatId.value = selected.catId || ''
      } else {
        autoCategoryMessage.value = '已根据封面图自动识别分类'
        autoCategoryMsgType.value = 'success'
      }
    } else {
      autoCategoryMessage.value = '已识别候选分类，请点击选择'
      autoCategoryMsgType.value = 'info'
    }
  } catch (e) {
    if (requestVersion === autoCategoryRequestVersion) {
      autoCategoryMessage.value = '自动分类请求失败：' + (e.message || '网络异常')
      autoCategoryMsgType.value = 'error'
      autoCategoryCandidates.value = []
      autoCategorySource.value = null
    }
  } finally {
    if (requestVersion === autoCategoryRequestVersion) {
      autoCategoryLoading.value = false
      // 后台刷新分类树，让新增的分类在前端级联选择器中可见
      refreshCategoriesInBackground()
    }
  }
}

async function refreshCategoriesInBackground() {
  try {
    const res = await fetchCategories()
    const newTree = res?.data?.cation || []
    if (newTree.length) {
      categories.value = newTree
    }
  } catch {
    if (import.meta.env.DEV) console.warn('[refreshCategoriesInBackground] failed')
  }
}

function applyAutoCategory(cat) {
  if (!cat) return
  markManualCategorySelection()
  // 用候选分类的名称匹配本地分类树
  const matchName = cat.catName || cat.name || ''
  if (matchName) {
    const options = flatCategoryOptions(5000)
    const matched = options.find(item =>
      item.name === matchName || item.path.endsWith(matchName)
    )
    if (matched) {
      selectCategoryByPath(matched, { manual: false })
      autoSelectedCatId.value = cat.catId || cat.catName || ''
      autoCategoryMessage.value = `已选择分类：${matchName}`
      autoCategoryMsgType.value = 'success'
      return
    }
  }
  // 未匹配到本地分类树时，直接设置分类名称
  selectedCategoryName.value = matchName
  selectedCategoryPath.value = matchName
  autoSelectedCatId.value = cat.catId || cat.catName || ''
  autoCategoryMessage.value = `已选择分类：${matchName}`
  autoCategoryMsgType.value = 'success'
}

function selectCategoryByPath(item, { manual = true } = {}) {
  const ids = item.pathIds || []
  const l1 = categories.value.find(c => String(c.id) === String(ids[0]))
  if (!l1) return
  if (manual) markManualCategorySelection()
  selectLevel1(l1, { manual: false })
  if (ids[1]) {
    const l2 = (l1.children || []).find(c => String(c.id) === String(ids[1]))
    if (l2) selectLevel2(l2, { manual: false })
    if (ids[2]) {
      const l3 = (l2?.children || []).find(c => String(c.id) === String(ids[2]))
      if (l3) selectLevel3(l3, { manual: false })
    }
  }
  categoryKeyword.value = ''
  rememberCategory(item)
}

function rememberCategory(item) {
  if (!item?.path) return
  const normalized = { name: item.name, path: item.path, pathIds: item.pathIds || [] }
  recentCategories.value = [normalized, ...recentCategories.value.filter(i => i.path !== normalized.path)].slice(0, 6)
  try { localStorage.setItem('xianyu_recent_categories', JSON.stringify(recentCategories.value)) } catch { /* Recent categories are optional. */ }
}

function loadRecentCategories() {
  try {
    const list = JSON.parse(localStorage.getItem('xianyu_recent_categories') || '[]')
    recentCategories.value = Array.isArray(list) ? list.slice(0, 6) : []
  } catch {
    recentCategories.value = []
  }
  try {
    const list = JSON.parse(localStorage.getItem('xianyu_favorite_categories') || '[]')
    favoriteCategories.value = Array.isArray(list) ? list.slice(0, 12) : []
  } catch {
    favoriteCategories.value = []
  }
}

function clearRecentCategories() {
  recentCategories.value = []
  try { localStorage.removeItem('xianyu_recent_categories') } catch { /* Storage may be unavailable. */ }
}

function toggleFavoriteCategory() {
  if (!selectedCategoryPath.value) return
  const item = { name: selectedCategoryName.value, path: selectedCategoryPath.value, pathIds: [level1Id.value, level2Id.value, level3Id.value].filter(Boolean) }
  if (isFavoriteCategory.value) {
    favoriteCategories.value = favoriteCategories.value.filter(i => i.path !== item.path)
  } else {
    favoriteCategories.value = [item, ...favoriteCategories.value.filter(i => i.path !== item.path)].slice(0, 12)
  }
  try { localStorage.setItem('xianyu_favorite_categories', JSON.stringify(favoriteCategories.value)) } catch { /* Favorites remain available for this session. */ }
}

// ---- 表单（清除草稿，初始为空） ----
const form = reactive({
  accountId: '',
  title: '',
  description: '',
  imageUrls: [],
  price: '',
  stock: '',
  supportSelfPick: false,
})

// === 运费设置 ===
const shippingMode = ref('free')
function setShipping(mode) {
  shippingMode.value = mode
}
const shippingLabel = computed(() => {
  const map = { free: '包邮', fixed: '一口价 / 运费', none: '无需邮寄' }
  return map[shippingMode.value] || '包邮'
})

// ---- 自动发货（项目内置功能，发布成功后绑定货源到本地商品） ----
const autoDelivery = reactive({ enabled: false, sourceId: '' })
const deliverySources = ref([])
const sourcesAvailable = ref(false)
const sourcesLoaded = ref(false)
const sourcesLoading = ref(false)
const sourcesError = ref('')

const selectedSourceTitle = computed(() => {
  const src = deliverySources.value.find(s => String(s.id) === String(autoDelivery.sourceId))
  return src ? src.title : ''
})

const selectedSourceUsageCount = computed(() => {
  const src = deliverySources.value.find(s => String(s.id) === String(autoDelivery.sourceId))
  return src ? (src.usageCount ?? null) : null
})

// force=true 时强制重新拉取（用于重试、绑定后刷新 usageCount）
async function loadDeliverySources(force = false) {
  if (sourcesLoading.value) return
  if (!force && sourcesLoaded.value) return
  sourcesLoading.value = true
  sourcesError.value = ''
  try {
    const res = await getDeliverySources({ current: 1, size: 200 })
    const data = res?.data
    const list = Array.isArray(data) ? data : (data?.records || [])
    if (!Array.isArray(list)) throw new Error('货源库响应格式异常')
    deliverySources.value = list
    sourcesAvailable.value = true
    sourcesLoaded.value = true
    // 刷新后校验已选货源是否仍存在（可能被用户在货源库页删除）
    if (autoDelivery.sourceId && !list.some(s => String(s.id) === String(autoDelivery.sourceId))) {
      autoDelivery.sourceId = ''
    }
  } catch (e) {
    deliverySources.value = []
    sourcesAvailable.value = false
    sourcesError.value = e?.message || '货源库加载失败，当前不能选择货源'
  } finally {
    sourcesLoading.value = false
  }
}

function reloadDeliverySources() {
  loadDeliverySources(true)
}

function goToSourceLibrary() {
  emit('navigate', 'delivery-source-library')
}

function toggleAutoDelivery() {
  autoDelivery.enabled = !autoDelivery.enabled
  if (autoDelivery.enabled) {
    if (!sourcesLoaded.value) loadDeliverySources()
    if (!autoDelivery.sourceId && deliverySources.value.length === 1) {
      autoDelivery.sourceId = deliverySources.value[0].id
    }
  }
}

// 发布成功后绑定自动发货货源：查询本地商品按 externalGoodsId 找到内部 id，再调用货源 apply 接口
// 开源版后端 publish 接口已自动保存到本地商品库，此处仅查询并绑定货源
// 绑定失败不影响发布结果（商品已发布到闲鱼），仅提示用户到「自动发货」页面手动配置
// 绑定成功后强制刷新货源库，同步 usageCount
async function bindAutoDeliverySource(itemId) {
  try {
    const goodsRes = await getGoods({ accountId: Number(form.accountId), size: 50 })
    const list = goodsRes?.data?.records || goodsRes?.data || []
    const created = (Array.isArray(list) ? list : []).find(g => String(g.externalGoodsId) === String(itemId))
    if (!created || !created.id) {
      success.value = '发布成功！自动发货绑定待商品同步后生效，请稍后到「自动发货」页面确认。'
      return
    }
    await applyDeliverySourceToGoods(autoDelivery.sourceId, {
      goodsIds: [created.id],
      timing: 'payDelivery',
    })
    success.value = `发布成功！已绑定自动发货货源：${selectedSourceTitle.value || '已选货源'}（买家付款后自动发货）`
    // 绑定成功后刷新货源库数据，同步 usageCount
    loadDeliverySources(true)
  } catch (bindErr) {
    success.value = `发布成功！自动发货绑定失败：${bindErr?.message || '请稍后到「自动发货」页面手动配置'}`
  }
}

watch(() => form.accountId, (nextAccountId, previousAccountId) => {
  if (String(nextAccountId ?? '') === String(previousAccountId ?? '')) return
  const discardedUpload = uploadingImages.value
  uploadRequestVersion += 1
  autoCategoryRequestVersion += 1
  aiCategoryRequestVersion += 1
  uploadingImages.value = false
  autoCategoryLoading.value = false
  aiCategoryLoading.value = false
  if (discardedUpload && previousAccountId) {
    warning.value = '账号已切换，旧账号尚未完成的图片上传结果已忽略。'
  }
})

async function handleCancel() {
  const ok = await confirmAction({
    title: '确认离开？',
    description: '未保存的更改将丢失，确定要离开吗？',
    confirmText: '离开',
    dangerous: true
  })
  if (ok) emit('navigate', 'products')
}

// ---- 商品位置（省/市/区 三级联动，与商业版发布逻辑保持一致）----
// selectedAddress 由 PublishAddressCascader 维护，结构：
// { prov, city, area, divisionId, gps, poiId, poiName, detail, source }
const selectedAddress = ref(null)

// ---- 请求封装 ----

const selectedAccount = computed(() => accountName(accounts.value.find(a => String(a.id) === String(form.accountId)) || {}))
const displayPrice = computed(() => form.price || '0.00')
const totalStock = computed(() => Number(form.stock) || 0)
const confirmationPayload = computed(() => publishIntent.payload || {
  xianyuAccountId: Number(form.accountId),
  title: form.title,
  category: selectedCategoryName.value,
  price: form.price,
  stock: Number(form.stock) || 0,
  autoDelivery: autoDelivery.enabled && autoDelivery.sourceId
    ? `已绑定「${selectedSourceTitle.value || '货源'}」`
    : (autoDelivery.enabled ? '未选货源' : '未开启'),
})
function resolveIntentAccountName(accountId) {
  const account = accounts.value.find(item => String(item.id) === String(accountId))
  return account ? accountName(account) : ''
}
const persistedIntentSummary = computed(() => (
  publishIntent.payload
    ? buildPublishIntentSummary(publishIntent.payload, resolveIntentAccountName)
    : ''
))
const checks = computed(() => [
  { text: '已选择闲鱼账号', ok: !!form.accountId },
  { text: '标题已填写', ok: form.title.trim().length > 0 },
  { text: '商品描述已填写', ok: form.description.trim().length > 0 },
  { text: '已上传商品图片', ok: form.imageUrls.length > 0 },
  { text: '分类已选择', ok: !!selectedCategoryName.value },
  { text: '商品位置已确认', ok: !!selectedAddress.value },
  { text: '价格已填写', ok: Number(form.price) > 0 },
  { text: '库存数大于 0', ok: totalStock.value > 0 },
  { text: '自动发货货源已选择', ok: !autoDelivery.enabled || !!autoDelivery.sourceId },
])

// 触发文件选择
function triggerUpload() {
  if (uploadingImages.value) {
    warning.value = '图片正在上传，请等待当前批次完成后再选择。'
    return
  }
  if (accountsAvailable.value !== true) {
    error.value = '账号列表暂不可用，无法安全上传商品图片。'
    return
  }
  if (!form.accountId) {
    error.value = '请先选择闲鱼账号，再上传商品图片。'
    return
  }
  fileInput.value?.click()
}

async function onFileSelect(e) {
  const input = e.target
  const files = input.files
  if (!files || files.length === 0) return
  if (uploadingImages.value) {
    input.value = ''
    return
  }
  const requestVersion = ++uploadRequestVersion
  const requestContext = productAsyncContext()
  const uploadAccountId = form.accountId
  const remaining = 10 - form.imageUrls.length
  const toUpload = Array.from(files).slice(0, remaining)
  const hadNoImages = form.imageUrls.length === 0
  const uploadedUrls = []
  let uploadError = ''
  uploadingImages.value = true
  warning.value = ''
  try {
    for (const file of toUpload) {
      const validationMessage = imageUploadValidationMessage(file)
      if (validationMessage) {
        uploadError = '图片 "' + file.name + '" ' + validationMessage
        continue
      }
      try {
        const res = await uploadImage(uploadAccountId || 0, file)
        if (!isProductAsyncRequestCurrent({
          requestVersion,
          currentVersion: uploadRequestVersion,
          snapshot: requestContext,
          current: productAsyncContext(),
          fields: ['accountId'],
        })) return
        if (res.code === 200 && res.data?.url) {
          uploadedUrls.push(res.data.url)
        } else {
          uploadError = friendlyError({ message: res.msg || '图片上传失败', requestId: res?.requestId }, '图片上传失败，请稍后重试')
        }
      } catch (err) {
        if (requestVersion !== uploadRequestVersion) return
        uploadError = friendlyError(err, '图片上传失败，请稍后重试')
      }
    }
    if (!isProductAsyncRequestCurrent({
      requestVersion,
      currentVersion: uploadRequestVersion,
      snapshot: requestContext,
      current: productAsyncContext(),
      fields: ['accountId'],
    })) return
    form.imageUrls.push(...uploadedUrls.slice(0, 10 - form.imageUrls.length))
    if (uploadError) error.value = uploadError
    uploadingImages.value = false
    // 上传完成后，如果是第一次上传图片（刚有了封面图），触发自动分类
    if (hadNoImages && uploadedUrls.length > 0) await triggerAutoCategory()
  } finally {
    input.value = ''
    if (requestVersion === uploadRequestVersion) uploadingImages.value = false
  }
}

// 粘贴图片：监听页面 paste 事件，从剪贴板提取图片文件后调用通用上传逻辑
async function onPaste(e) {
  const items = e.clipboardData?.items
  if (!items || !items.length) return
  const imageItems = Array.from(items).filter(it => it.type && it.type.startsWith('image/'))
  if (!imageItems.length) return
  // 若剪贴板里同时含文本，且当前焦点在输入框/文本域，则放行让浏览器粘贴文本
  const hasText = Array.from(items).some(it => it.type === 'text/plain' || it.type === 'text/html')
  const target = e.target
  const tag = target?.tagName?.toLowerCase?.()
  const inEditable = tag === 'input' || tag === 'textarea'
  if (hasText && inEditable) return
  if (accountsAvailable.value !== true || !form.accountId) {
    error.value = '请先选择闲鱼账号再粘贴图片'
    return
  }
  if (form.imageUrls.length >= 10) {
    error.value = '宝贝图片已满 10 张，无法继续粘贴'
    return
  }
  // 拦截粘贴，避免图片被当作文本插入到其他输入框
  e.preventDefault()
  const hadNoImages = form.imageUrls.length === 0
  uploadingImages.value = true
  try {
    for (const item of imageItems) {
      if (form.imageUrls.length >= 10) {
        error.value = '宝贝图片已满 10 张，已停止粘贴后续图片'
        break
      }
      const rawFile = item.getAsFile()
      if (!rawFile) continue
      // 剪贴板图片常无文件名，统一补上扩展名以便校验工具识别
      const ext = (rawFile.type.split('/')[1] || 'png').toLowerCase()
      const fileName = rawFile.name || `paste_${Date.now()}.${ext}`
      const file = new File([rawFile], fileName, { type: rawFile.type })
      const validationMessage = imageUploadValidationMessage(file)
      if (validationMessage) {
        error.value = `粘贴图片 ${file.name} ${validationMessage}`
        continue
      }
      try {
        const res = await uploadImage(form.accountId || 0, file)
        if (res.code === 200 && res.data?.url) {
          form.imageUrls.push(res.data.url)
        } else {
          error.value = friendlyError({ message: res.msg || '粘贴图片上传失败', requestId: res?.requestId }, '图片上传失败，请稍后重试')
        }
      } catch (err) {
        error.value = friendlyError(err, '图片上传失败，请稍后重试')
      }
    }
    if (hadNoImages && form.imageUrls.length > 0) {
      await triggerAutoCategory()
    }
  } finally {
    uploadingImages.value = false
  }
}

// URL 导入封面图：用户输入图片 URL，后端下载后保存到 uploads/images/
const imageUrlInput = ref('')
const imageUrlLoading = ref(false)

async function addImageFromUrl() {
  const url = imageUrlInput.value.trim()
  if (!url) {
    error.value = '请输入图片 URL'
    return
  }
  if (!/^https?:\/\//i.test(url)) {
    error.value = '图片 URL 必须以 http:// 或 https:// 开头'
    return
  }
  if (accountsAvailable.value !== true || !form.accountId) {
    error.value = '请先选择闲鱼账号再导入图片'
    return
  }
  if (form.imageUrls.length >= 10) {
    error.value = '宝贝图片已满 10 张，无法继续导入'
    return
  }
  imageUrlLoading.value = true
  error.value = ''
  const hadNoImages = form.imageUrls.length === 0
  try {
    const res = await uploadImageFromUrl({ url })
    if (res.code === 200 && res.data?.url) {
      form.imageUrls.push(res.data.url)
      imageUrlInput.value = ''
    } else {
      error.value = friendlyError({ message: res.msg || 'URL 图片导入失败', requestId: res?.requestId }, 'URL 图片导入失败')
    }
  } catch (err) {
    error.value = friendlyError(err, 'URL 图片导入失败')
  } finally {
    imageUrlLoading.value = false
    if (hadNoImages && form.imageUrls.length > 0) {
      await triggerAutoCategory()
    }
  }
}

function removeImage(idx) {
  form.imageUrls.splice(idx, 1)
}

function onDragStart(idx, e) {
  dragIndex.value = idx
  e.dataTransfer.effectAllowed = 'move'
}
function onDragOver(idx, e) {
  e.dataTransfer.dropEffect = 'move'
}
function onDrop(idx) {
  const from = dragIndex.value
  if (from === idx) return
  const item = form.imageUrls.splice(from, 1)[0]
  form.imageUrls.splice(idx, 0, item)
  dragIndex.value = -1
}

async function loadCategories() {
  if (categories.value.length || categoriesLoading.value) return
  categoriesLoading.value = true
  try {
    // 优先从静态 JSON 文件加载（零网络延迟）
    const module = await import('../assets/data/categories.json')
    categories.value = module.default?.cation || module.cation || []
    // 静默从后端拉取最新分类树（含自动分类新增的分类）
    // 自动分类服务在后台已将新分类写入后端 categories.json
    refreshCategoriesInBackground()
  } catch (e) {
    error.value = e?.message || '商品分类加载失败'
    categories.value = []
  } finally {
    categoriesLoading.value = false
  }
}

async function load() {
  restorePublishIntent()
  loadRecentCategories()
  await Promise.allSettled([loadCategories(), loadAiCategoryStatus()])
  const [accountResult, configResult] = await Promise.allSettled([
    loadAllAccounts(),
    runtimeConfig()
  ])
  if (accountResult.status === 'fulfilled') {
    accounts.value = accountResult.value
    accountsAvailable.value = true
    if (!form.accountId || !accountAuthUsable(accounts.value.find(a => String(a.id) === String(form.accountId)))) {
      const preferred = pickPreferredAccount(accounts.value, form.accountId)
      if (preferred) form.accountId = preferred.id
    }
    if (!form.accountId && accounts.value[0]) form.accountId = accounts.value[0].id
  } else {
    accounts.value = []
    accountsAvailable.value = false
    error.value = accountResult.reason?.message || '账号列表暂不可用，当前禁止发布。'
  }
  if (configResult.status === 'fulfilled') {
    const cfg = configResult.value?.data || {}
    runtime.defaultCity = cfg.map?.defaultCity || cfg.defaultCity || ''
    runtime.defaultAddress = cfg.map?.defaultAddress || cfg.defaultAddress || ''
  } else {
    warning.value = warning.value || '默认位置配置暂不可用，请手动选择并确认商品位置。'
  }
}

async function loadAllAccounts() {
  const result = []
  const pageSize = 200
  let current = 1
  let total
  let hasMore = true
  while (hasMore) {
    const response = await getAccounts({ current, size: pageSize })
    const pageRecords = recordsOf(response?.data)
    total = Number(response?.data?.total ?? pageRecords.length) || 0
    result.push(...pageRecords)
    hasMore = pageRecords.length === pageSize && result.length < total
    if (hasMore) current += 1
  }
  return result
}

const aiDescLoading = ref(false)
let aiDescriptionRequestVersion = 0
async function aiDesc() {
  if (aiDescLoading.value) return
  if (!form.title && !form.description) {
    error.value = '请先填写商品标题或基础描述'
    return
  }
  if (!aiCategoryStatus.value.configured) {
    error.value = aiStatusMessage('未配置通用模型，暂时无法使用 AI 生成描述。')
    return
  }
  const requestVersion = ++aiDescriptionRequestVersion
  const requestContext = productAsyncContext()
  aiDescLoading.value = true
  error.value = ''
  try {
    const res = await aiRewriteGoods({
      title: requestContext.title,
      description: requestContext.description
    })
    if (!isProductAsyncRequestCurrent({
      requestVersion,
      currentVersion: aiDescriptionRequestVersion,
      snapshot: requestContext,
      current: productAsyncContext(),
      fields: ['title', 'description'],
    })) {
      warning.value = 'AI 描述已生成，但您已继续编辑，因此未覆盖当前内容。'
      return
    }
    const data = res?.data || {}
    if (data.configured === false || data.ok === false) {
      error.value = data.message || aiStatusMessage('未配置通用模型，暂时无法使用 AI 生成描述。')
      return
    }
    form.description = data.content || data.description || (typeof data === 'string' ? data : '') || ''
  } catch (e) {
    if (requestVersion === aiDescriptionRequestVersion) error.value = friendlyError(e, 'AI 描述生成失败')
  } finally {
    if (requestVersion === aiDescriptionRequestVersion) aiDescLoading.value = false
  }
}
function insertPhrase() {
  form.description += (form.description ? '\n' : '') + '下单前请先确认库存，售出不退不换。'
}

function validate() {
  if (accountsAvailable.value !== true) {
    error.value = '账号列表状态未知，重新加载成功前禁止发布。'
    return false
  }
  const miss = checks.value.find(i => !i.ok)
  if (miss) { error.value = `"${miss.text}" 检查未通过，请完善后再提交`; return false }
  return true
}

// 发布前主动调用 /check-auth 实时探活选中账号 cookie 状态。
// 仅依赖 accounts 列表中的缓存 cookieStatus 会导致 cookie 实际已失效但 DB 未更新时
// validate() 通过、submit() 才被闲鱼接口报错"账号已失效"拦截，用户体验差。
// 这里在发布前主动 check-auth，若失效则直接阻断并提示"该账号 Cookie 已经失效"，
// 同时刷新本地账号状态，让用户立即看到需要重新登录的提示。
async function ensureSelectedAccountCookieValid() {
  if (!form.accountId) return false
  let data
  try {
    const res = await checkAccountAuth(form.accountId)
    data = res?.data
  } catch {
    // 接口异常时阻断发布并提示用户，避免带着未校验的账号直接调用发布接口
    error.value = '无法确认账号登录状态，请检查网络后重试'
    return false
  }
  if (!data || typeof data !== 'object' || typeof data.usable !== 'boolean') {
    error.value = '账号登录状态响应异常，请稍后重试'
    return false
  }
  // 同步刷新本地账号缓存，让账号选择框旁边也能反映真实状态
  const account = accounts.value.find(a => String(a.id) === String(form.accountId))
  if (account) {
    account.cookieStatus = data.cookieStatus
    account.authUsable = data.usable
    account.loginStatusCode = data.loginStatusCode
    account.loginStatusMessage = data.loginStatusMessage
    account.loginCheckTime = data.checkedAt
  }
  if (!data.usable) {
    const accountLabel = account ? accountName(account) : '当前账号'
    error.value = `${accountLabel} Cookie 已经失效（${data.loginStatusMessage || '请重新登录闲鱼账号'}），请到「账号管理」重新登录后再发布`
    return false
  }
  return true
}

function createPublishIntentKey() {
  if (typeof globalThis.crypto?.randomUUID === 'function') {
    return `publish-${globalThis.crypto.randomUUID()}`
  }
  return `publish-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`
}

function savePublishIntent() {
  try {
    sessionStorage.setItem(PUBLISH_INTENT_KEY, JSON.stringify({
      idempotencyKey: publishIntent.idempotencyKey,
      payload: publishIntent.payload,
      outcome: publishOutcome.value,
    }))
  } catch { /* Session recovery is best-effort. */ }
}

function restorePublishIntent() {
  try {
    const saved = JSON.parse(sessionStorage.getItem(PUBLISH_INTENT_KEY) || 'null')
    if (saved?.idempotencyKey && saved?.payload) {
      const hasUnsupportedSku = Array.isArray(saved.payload.skus) && saved.payload.skus.length > 0
      const hasUnsupportedShipping = saved.payload.shippingMode && saved.payload.shippingMode !== 'free'
      if (hasUnsupportedSku || hasUnsupportedShipping || saved.payload.freeShipping === false) {
        clearPublishIntent()
        warning.value = '检测到旧版多规格或非包邮发布意图。当前无法安全恢复，请按单规格、包邮重新确认后提交。'
        return
      }
      publishIntent.idempotencyKey = String(saved.idempotencyKey)
      publishIntent.payload = saved.payload
      publishOutcome.value = saved.outcome || { status: 'failed', retrySafe: true, retryScope: 'resume' }
      if (publishOutcome.value.status === 'unknown') {
        warning.value = '检测到结果未知的发布意图。请先同步商品或到闲鱼 App 核对，当前禁止重试。'
      } else if (publishOutcome.value.status === 'remote_confirmed') {
        warning.value = '闲鱼平台已确认发布；继续操作只会修复本地商品状态。'
      } else {
        warning.value = '检测到未完成的发布意图。继续操作只会恢复该意图；服务端会阻止重复发布。'
      }
    }
  } catch { /* Ignore invalid session state. */ }
}

function clearPublishIntent() {
  publishIntent.idempotencyKey = ''
  publishIntent.payload = null
  publishOutcome.value = null
  try { sessionStorage.removeItem(PUBLISH_INTENT_KEY) } catch { /* Optional storage. */ }
}

async function submit() {
  error.value = ''
  warning.value = ''
  success.value = ''
  if (!publishIntent.payload && !validate()) return
  // 发布前主动校验 cookie 实时状态，避免 cookie 已失效时被动等闲鱼接口报错
  if (!publishIntent.payload && !(await ensureSelectedAccountCookieValid())) return
  const onlyRepairLocal = publishOutcome.value?.status === 'remote_confirmed'
  const confirmationSummary = buildPublishIntentSummary(confirmationPayload.value, resolveIntentAccountName)
  const ok = await confirmAction({
    title: onlyRepairLocal ? '确认仅修复本地商品状态？' : '确认立即发布到闲鱼？',
    description: onlyRepairLocal
      ? `平台已经确认发布。本次仅补全本地商品库，不会再次调用闲鱼发布接口。\n\n${confirmationSummary}`
      : `${confirmationSummary}\n发布成功后会同步保存到本地商品库。`,
    dangerous: true
  })
  if (!ok) return
  submitting.value = true
  try {
    const finalPrice = form.price
    const finalStock = Number(form.stock) || 1
    const shippingMap = { free: true, fixed: false, none: false }
    const freeShipping = shippingMap[shippingMode.value] ?? true

    // 构建位置数据：使用三级联动选择的结构化地址，与商业版发布逻辑保持一致
    const addr = selectedAddress.value
    const locationData = addr ? {
      prov: addr.prov || '',
      city: addr.city || '',
      area: addr.area || '',
      divisionId: addr.divisionId || '',
      gps: addr.gps || '',
      poiId: addr.poiId || '',
      poiName: addr.poiName || '',
      detail: addr.detail || '',
    } : {
      prov: '',
      city: '',
      area: '',
      divisionId: '',
      gps: '',
      poiId: '',
      poiName: runtime.defaultAddress || '',
      detail: '',
    }

    // 先发布到闲鱼，成功后再保存到本地数据库，避免发布失败时本地却显示商品
    if (!publishIntent.payload) {
      publishIntent.idempotencyKey = createPublishIntentKey()
      publishIntent.payload = {
        xianyuAccountId: Number(form.accountId),
        title: form.title.slice(0, 30),
        description: form.description,
        imageUrls: [...form.imageUrls],
        price: finalPrice,
        stock: finalStock,
        category: selectedCategoryName.value,
        shippingMode: shippingMode.value,
        postFee: shippingMode.value === 'fixed' ? 0 : 0,
        freeShipping: freeShipping,
        supportSelfPick: form.supportSelfPick,
        location: locationData,
      }
      savePublishIntent()
    }
    const publishRes = await publishItem({
      ...publishIntent.payload,
      idempotencyKey: publishIntent.idempotencyKey,
    })

    if (publishRes.code === 200) {
      success.value = '发布成功，平台与本地商品库均已确认。'
      clearPublishIntent()
      // 发布成功后清除草稿，避免下次进入时恢复已发布内容
      clearPublishDraft()
      // 自动发货货源绑定（发布成功且本地商品已保存后执行）
      // 开源版后端 publish 接口已自动保存到本地商品库，此处查询本地商品按 externalGoodsId 找到内部 id 再绑定
      if (autoDelivery.enabled && autoDelivery.sourceId) {
        const publishedItemId = String(publishRes.data?.itemId || '').trim()
        if (publishedItemId) await bindAutoDeliverySource(publishedItemId)
      }
      // 浏览器中的待同步标记只是可选的后续 UX，失败不得改写已确认的平台发布结果。
      const pendingSync = markPendingProductSync()
      if (!pendingSync.stored) {
        warning.value = '发布已确认成功，但浏览器未能记录自动同步标记。请进入商品管理手动同步；该提示不影响发布结果。'
      }
      setTimeout(() => emit('navigate', 'products'), 1000)
    } else {
      error.value = publishRes.msg || '发布到闲鱼失败'
    }
  } catch (e) {
    const state = String(e?.data?.status || 'unknown')
    publishOutcome.value = {
      status: state,
      retrySafe: e?.data?.retrySafe === true,
      retryScope: e?.data?.retryScope || null,
    }
    savePublishIntent()
    if (state === 'remote_confirmed') {
      warning.value = '闲鱼平台已确认发布，但本地商品库尚未完成。可点击“仅修复本地状态”，系统绝不会重复发布。'
    } else if (state === 'unknown') {
      error.value = '发布结果未知。请先同步商品或到闲鱼 App 核对；为避免重复发布，当前禁止重试。'
    } else if (state === 'in_progress') {
      warning.value = '同一发布意图正在执行，请勿重复提交。'
    } else if (state === 'failed' && publishOutcome.value.retrySafe !== true) {
      error.value = `${e.message || '发布失败'}。服务端未授权安全重试，当前意图已锁定。`
    } else {
      error.value = e.message || '发布失败'
    }
  } finally {
    submitting.value = false
  }
}

// ---- 草稿（自动保存 / 恢复 / 清空） ----
const isRestoring = ref(false)
let saveTimer = null

const hasDraftData = computed(() => {
  // accountId 不计入判定，因为会自动选择
  return !!(form.title.trim()
    || form.description.trim()
    || form.imageUrls.length
    || form.price
    || form.stock
    || selectedCategoryName.value
    || selectedAddress.value
    || autoDelivery.enabled)
})

function serializeCurrentDraft() {
  return {
    form: {
      accountId: form.accountId,
      title: form.title,
      description: form.description,
      imageUrls: [...form.imageUrls],
      price: form.price,
      stock: form.stock,
      supportSelfPick: form.supportSelfPick,
    },
    shippingMode: shippingMode.value,
    category: {
      name: selectedCategoryName.value,
      path: selectedCategoryPath.value,
      pathIds: [level1Id.value, level2Id.value, level3Id.value].filter(Boolean),
    },
    poi: null,
    location: selectedAddress.value ? JSON.parse(JSON.stringify(selectedAddress.value)) : null,
    autoDelivery: { enabled: autoDelivery.enabled, sourceId: autoDelivery.sourceId },
  }
}

function restoreDraft(draft) {
  isRestoring.value = true
  try {
    const f = draft.form || {}
    form.accountId = f.accountId || ''
    form.title = f.title || ''
    form.description = f.description || ''
    form.imageUrls = Array.isArray(f.imageUrls) ? [...f.imageUrls] : []
    form.price = f.price || ''
    form.stock = f.stock || ''
    form.supportSelfPick = !!f.supportSelfPick
    shippingMode.value = draft.shippingMode || 'free'
    // 分类恢复：优先走级联选择，无 pathIds 时回退到直接设置 name/path
    const cat = draft.category || {}
    if (cat.pathIds && cat.pathIds.length) {
      nextTick(() => {
        // 试图在分类树中找到对应路径
        try {
          if (cat.pathIds[0]) selectLevel1(categories.value.find(c => c.id === cat.pathIds[0]), { manual: false })
          if (cat.pathIds[1]) selectLevel2(level2List.value.find(c => c.id === cat.pathIds[1]), { manual: false })
          if (cat.pathIds[2]) selectLevel3(level3List.value.find(c => c.id === cat.pathIds[2]), { manual: false })
        } catch {
          selectedCategoryName.value = cat.name || ''
          selectedCategoryPath.value = cat.path || ''
        }
      })
    } else {
      selectedCategoryName.value = cat.name || ''
      selectedCategoryPath.value = cat.path || ''
    }
    // 商品位置恢复（三级联动地址）
    if (draft.location) {
      selectedAddress.value = draft.location
    } else if (draft.poi) {
      // 兼容旧草稿：将旧版 POI 字段映射为结构化地址
      const p = draft.poi
      selectedAddress.value = {
        prov: p.pname || '',
        city: p.cityname || '',
        area: p.adname || '',
        divisionId: p.adcode || '',
        gps: p.location || '',
        poiId: p.id || '',
        poiName: p.name || '',
        detail: p.address || '',
        source: 'legacy',
      }
    }
    // 自动发货恢复
    const ad = draft.autoDelivery || {}
    autoDelivery.enabled = !!ad.enabled
    autoDelivery.sourceId = ad.sourceId || ''
    if (autoDelivery.enabled && !sourcesLoaded.value) loadDeliverySources()
  } finally {
    nextTick(() => { isRestoring.value = false })
  }
}

function scheduleAutoSave() {
  if (isRestoring.value) return
  if (saveTimer) clearTimeout(saveTimer)
  saveTimer = setTimeout(() => {
    if (hasDraftData.value) {
      savePublishDraft(serializeCurrentDraft())
    } else {
      clearPublishDraft()
    }
  }, 800)
}

// 监听表单与分类、位置变化，自动保存草稿（防抖 800ms）
watch(
  () => [
    form.accountId, form.title, form.description, form.price, form.stock,
    form.supportSelfPick, form.imageUrls.length,
  ],
  scheduleAutoSave,
)
watch(shippingMode, scheduleAutoSave)
watch(selectedCategoryName, scheduleAutoSave)
watch(selectedAddress, scheduleAutoSave, { deep: true })
// 自动发货状态变化时自动保存草稿
watch(() => [autoDelivery.enabled, autoDelivery.sourceId], scheduleAutoSave)

function flushDraftBeforeUnload() {
  if (saveTimer) {
    clearTimeout(saveTimer)
    saveTimer = null
  }
  // beforeunload 需要立即落盘，不经过防抖
  if (hasDraftData.value) {
    savePublishDraft(serializeCurrentDraft())
  }
}

// 导航守卫：有未发布数据时弹窗询问是否保存草稿
async function navigationGuardFn() {
  if (!hasDraftData.value) return true
  const choice = await promptDraftChoice({
    title: '是否保存草稿？',
    description: '离开后本页已输入的内容将保存为草稿，下次进入可自动恢复。',
  })
  if (choice === 'discard') {
    clearPublishDraft()
    return true
  }
  // save 或未选择（自动保存）
  savePublishDraft(serializeCurrentDraft())
  return true
}

async function clearAllData() {
  const ok = await confirmAction({
    title: '清空本页所有数据？',
    description: '将一键清除已填写的标题、描述、图片、分类、位置、价格等全部内容，且会删除已保存的草稿。此操作不可撤销。',
    confirmText: '清空',
    dangerous: true,
  })
  if (!ok) return
  isRestoring.value = true
  try {
    form.title = ''
    form.description = ''
    form.imageUrls = []
    form.price = ''
    form.stock = ''
    form.supportSelfPick = false
    shippingMode.value = 'free'
    selectedCategoryName.value = ''
    selectedCategoryPath.value = ''
    level1Id.value = null
    level2Id.value = null
    level3Id.value = null
    level2List.value = []
    level3List.value = []
    selectedAddress.value = null
    categoryKeyword.value = ''
    autoCategoryCandidates.value = []
    autoCategoryMessage.value = ''
    autoSelectedCatId.value = ''
    aiCategoryMessage.value = ''
    autoDelivery.enabled = false
    autoDelivery.sourceId = ''
    error.value = ''
    success.value = ''
    clearPublishDraft()
  } finally {
    isRestoring.value = false
  }
}

onMounted(async () => {
  setNavigationGuard(navigationGuardFn)
  window.addEventListener('beforeunload', flushDraftBeforeUnload)
  // 监听全局 paste 事件，支持用户直接 Ctrl+V 粘贴图片作为宝贝图片
  window.addEventListener('paste', onPaste)
  await load()
  // 尝试恢复草稿
  try {
    const draft = loadPublishDraft()
    if (draft && draft.form) {
      // 验证 accountId 是否仍存在于账号列表，避免引用已删除账号
      if (draft.form.accountId) {
        const exists = accounts.value.some(a => String(a.id) === String(draft.form.accountId))
        if (!exists) draft.form.accountId = ''
      }
      restoreDraft(draft)
      success.value = '已恢复上次未发布的草稿'
    }
  } catch {
    // 草稿恢复失败不影响正常使用
  }
})

onBeforeUnmount(() => {
  clearNavigationGuard()
  window.removeEventListener('beforeunload', flushDraftBeforeUnload)
  window.removeEventListener('paste', onPaste)
  if (saveTimer) {
    clearTimeout(saveTimer)
    saveTimer = null
  }
})
</script>

<style scoped>
.unavailable-option {
  opacity: 0.72;
  cursor: not-allowed;
}

.unavailable-option em {
  margin-left: 8px;
  color: #a05b00;
  font-size: 12px;
  font-style: normal;
}

.field-error {
  color: #b42318;
  font-size: 12px;
}

.img-card {
  position: relative;
  width: 100px;
  height: 100px;
  border: 2px solid #e8e8e8;
  border-radius: 12px;
  overflow: hidden;
  flex-shrink: 0;
  cursor: grab;
  transition: border-color 0.2s;
}
.img-card:hover {
  border-color: var(--primary, #1677ff);
}
.img-card.add-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  background: #fbfdff;
  border-style: dashed;
  cursor: pointer;
  color: inherit;
  font: inherit;
}
.img-card.add-card:hover:not(:disabled) {
  border-color: var(--primary, #1677ff);
  background: #f0f5ff;
}
.img-card.add-card:disabled { cursor: wait; opacity: .72; }
.img-card.add-card:focus-visible,
.img-remove:focus-visible { outline: 3px solid rgba(22,119,255,.35); outline-offset: 2px; }
.img-remove {
  position: absolute;
  top: 4px;
  right: 4px;
  width: 20px;
  height: 20px;
  background: rgba(0,0,0,0.5);
  border: 0;
  padding: 0;
  color: #fff;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  cursor: pointer;
  z-index: 2;
}
.img-remove:hover:not(:disabled) {
  background: rgba(255,0,0,0.7);
}
.img-remove:disabled { cursor: wait; opacity: .7; }
.image-strip {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}
/* URL 导入封面图 */
.image-url-bar {
  display: flex;
  gap: 8px;
  margin-top: 12px;
  align-items: stretch;
}
.image-url-input {
  flex: 1;
  min-width: 0;
  padding: 10px 12px;
  border: 1px solid #e5e7eb;
  border-radius: 10px;
  outline: none;
  font-size: 13px;
  transition: border-color 0.15s;
  background: #fbfdff;
}
.image-url-input:focus {
  border-color: var(--primary, #1677ff);
  background: #fff;
}
.image-url-input:disabled {
  background: #f3f4f6;
  cursor: not-allowed;
}
.image-url-btn {
  flex-shrink: 0;
  padding: 0 18px;
  border: 1px solid var(--primary, #1677ff);
  background: var(--primary, #1677ff);
  color: #fff;
  border-radius: 10px;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: opacity 0.15s, transform 0.15s;
}
.image-url-btn:hover:not(:disabled) {
  opacity: 0.92;
}
.image-url-btn:disabled {
  background: #cbd5e1;
  border-color: #cbd5e1;
  cursor: not-allowed;
}
.image-hint {
  margin: 8px 2px 0;
  font-size: 12px;
  color: #94a3b8;
  line-height: 1.6;
}
/* ---- 分类级联选择器 ---- */
.category-selector {
  min-height: 60px;
}
.category-tools { display: flex; gap: 8px; margin-bottom: 10px; }
.category-search { flex: 1; padding: 10px 12px; border: 1px solid #e5e7eb; border-radius: 10px; outline: none; }
.category-search:focus { border-color: var(--primary, #1677ff); }
.category-clear { border: 1px solid #dbe3ef; background: #fff; border-radius: 10px; padding: 0 12px; cursor: pointer; }
.category-ai-btn { border: 1px solid #bcd7ff; background: #f4f8ff; color: #0d6bff; border-radius: 10px; padding: 0 12px; cursor: pointer; font-weight: 700; }
.category-ai-btn-blocked { margin-bottom: 10px; }
.category-ai-btn:disabled { cursor: not-allowed; opacity: .55; }
.ai-unconfigured-tip { margin: 8px 0 0; color: #b45309; font-size: 12px; line-height: 1.6; }
.ai-category-tip { margin-left: 10px; color: #16bf78; font-weight: 700; }
.category-search-results { display: grid; gap: 6px; max-height: 220px; overflow: auto; margin-bottom: 10px; padding: 8px; border: 1px solid #e8edf5; border-radius: 12px; background: #fbfdff; }
.category-result { display: grid; gap: 2px; padding: 8px 10px; border-radius: 9px; cursor: pointer; }
.category-result:hover { background: #eef5ff; }
.category-result strong { color: #111827; font-size: 13px; }
.category-result span { color: #64748b; font-size: 12px; }
.recent-categories { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 10px; color: #64748b; font-size: 12px; }
.recent-categories button { border: 1px solid #dbeafe; background: #eff6ff; color: #1d4ed8; border-radius: 999px; padding: 5px 10px; cursor: pointer; }
.favorite-categories span { color: #92400e; }
.favorite-categories button { border-color: #fed7aa; background: #fff7ed; color: #c2410c; }
.category-actions { margin-bottom: 10px; }
.category-actions button, .category-link { border: 1px solid #dbe3ef; background: #fff; border-radius: 999px; padding: 5px 10px; cursor: pointer; font-size: 12px; color: #2563eb; }
.cascader-levels {
  display: flex;
  gap: 8px;
  border: 1px solid #e8e8e8;
  border-radius: 10px;
  overflow: hidden;
}
.cascader-col {
  flex: 1;
  min-width: 120px;
  max-height: 220px;
  overflow-y: auto;
  border-right: 1px solid #e8e8e8;
  background: #fafafa;
}
.cascader-col:last-child {
  border-right: none;
}
.cascader-item {
  padding: 8px 12px;
  font-size: 13px;
  cursor: pointer;
  transition: background 0.15s;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.cascader-item:hover {
  background: #e6f0ff;
}
.cascader-item.active {
  background: var(--primary, #1677ff);
  color: #fff;
  font-weight: 500;
}
/* ---- 发货设置 ---- */
.shipping-grid {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.shipping-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 0;
}
.shipping-item:not(:last-child) {
  border-bottom: 1px solid #f0f0f0;
}
/* 商品位置改用 PublishAddressCascader 组件，原 .location-search / .poi-* 样式已移除 */
.char-count {
  font-size: 12px;
  color: #999;
  margin-left: 8px;
  white-space: nowrap;
}

/* ---- 自动分类 ---- */
.auto-category-hint {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 10px;
  padding: 8px 12px;
  background: #f0f7ff;
  border: 1px solid #d4e4ff;
  border-radius: 10px;
  color: #1a56db;
  font-size: 13px;
}
.auto-category-hint .hint-icon {
  font-size: 16px;
}
.auto-category-spinner {
  margin-left: auto;
  color: #0d6bff;
  font-weight: 700;
  animation: pulse 1.5s ease-in-out infinite;
}
@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}
.auto-category-msg {
  margin-bottom: 10px;
  padding: 8px 12px;
  border-radius: 10px;
  font-size: 13px;
  font-weight: 600;
}
.auto-category-msg.success {
  background: #ecfdf5;
  border: 1px solid #a7f3d0;
  color: #059669;
}
.auto-category-msg.warn {
  background: #fffbeb;
  border: 1px solid #fde68a;
  color: #b45309;
}
.auto-category-msg.error {
  background: #fef2f2;
  border: 1px solid #fecaca;
  color: #dc2626;
}
.auto-category-msg.info {
  background: #eff6ff;
  border: 1px solid #bfdbfe;
  color: #1d4ed8;
}
.auto-category-candidates {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 10px;
  padding: 8px 12px;
  border: 1px solid #e4eaf2;
  border-radius: 10px;
  background: #fafcff;
}
.candidates-label {
  font-size: 12px;
  color: #64748b;
  font-weight: 600;
  white-space: nowrap;
}
.candidate-btn {
  border: 1px solid #dbeafe;
  background: #eff6ff;
  color: #1d4ed8;
  border-radius: 999px;
  padding: 5px 12px;
  cursor: pointer;
  font-size: 12px;
  transition: all 0.15s;
}
.candidate-btn:hover {
  background: #dbeafe;
  border-color: #93c5fd;
}
.candidate-btn.active {
  background: #1d4ed8;
  border-color: #1d4ed8;
  color: #fff;
}
.candidate-btn small {
  font-weight: 400;
  opacity: 0.8;
}

/* ---- 自动发货板块 ---- */
.auto-delivery-section {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.auto-delivery-toggle {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  padding: 10px 0;
}
.auto-delivery-toggle-info {
  display: flex;
  flex-direction: column;
  gap: 4px;
  flex: 1;
  min-width: 0;
}
.auto-delivery-title {
  font-size: 14px;
  font-weight: 600;
  color: #111827;
}
.auto-delivery-source-row {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 10px 12px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
}
.auto-delivery-source-row label {
  font-size: 13px;
  font-weight: 600;
  color: #334155;
}
.auto-delivery-source-row .input {
  width: 100%;
  padding: 10px 12px;
  border: 1px solid #e5e7eb;
  border-radius: 10px;
  outline: none;
  font-size: 13px;
  background: #fff;
  transition: border-color 0.15s;
}
.auto-delivery-source-row .input:focus {
  border-color: var(--primary, #1677ff);
}
.auto-delivery-source-row .input:disabled {
  background: #f3f4f6;
  cursor: not-allowed;
}
.auto-delivery-msg.error {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  font-size: 12px;
  color: #dc2626;
  font-weight: 600;
}
.auto-delivery-empty {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
}
.ad-action-btn {
  border: 1px solid #dbe3ef;
  background: #fff;
  color: #2563eb;
  border-radius: 8px;
  padding: 4px 12px;
  cursor: pointer;
  font-size: 12px;
  font-weight: 600;
  white-space: nowrap;
  transition: all 0.15s;
}
.ad-action-btn:hover {
  background: #eff6ff;
  border-color: #93c5fd;
}
.ad-action-btn.primary {
  border-color: var(--primary, #1677ff);
  background: var(--primary, #1677ff);
  color: #fff;
}
.ad-action-btn.primary:hover {
  opacity: 0.92;
}
.subtle.warn {
  color: #b45309;
  font-weight: 600;
}

/* ---- 草稿工具栏 ---- */
.draft-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 14px;
  padding: 10px 14px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
}
.draft-tip {
  font-size: 13px;
  color: #475569;
}
.clear-draft-btn {
  border: 1px solid #fecaca;
  background: #fef2f2;
  color: #dc2626;
  border-radius: 8px;
  padding: 6px 14px;
  cursor: pointer;
  font-size: 13px;
  font-weight: 600;
  white-space: nowrap;
  transition: all 0.15s;
}
.clear-draft-btn:hover {
  background: #fee2e2;
  border-color: #fca5a5;
}

/* === 移动端适配 (max-width: 900px) === */
@media (max-width: 900px) {
  /* 图片上传卡片：移动端缩小尺寸，更多列数 */
  .img-card {
    width: 78px;
    height: 78px;
    border-radius: 10px;
  }
  .image-strip {
    gap: 8px;
  }
  /* URL 导入封面图：移动端纵向堆叠 */
  .image-url-bar {
    flex-direction: column;
    gap: 6px;
  }
  .image-url-btn {
    padding: 10px 14px;
  }
  /* 分类级联选择器：移动端纵向堆叠三列，避免横向挤压 */
  .cascader-levels {
    flex-direction: column;
    gap: 6px;
    border-radius: 10px;
  }
  .cascader-col {
    min-width: 0;
    width: 100%;
    max-height: 180px;
    border-right: none;
    border-bottom: 1px solid #e8e8e8;
  }
  .cascader-col:last-child {
    border-bottom: none;
  }
  .cascader-item {
    padding: 8px 10px;
    font-size: 12px;
  }
  /* 分类工具栏允许换行 */
  .category-tools {
    flex-wrap: wrap;
    gap: 6px;
  }
  .category-search {
    flex: 1 1 100%;
    padding: 8px 10px;
  }
  .category-ai-btn,
  .category-clear {
    padding: 6px 10px;
    font-size: 12px;
  }
  .category-search-results {
    max-height: 180px;
    padding: 6px;
  }
  .recent-categories {
    gap: 6px;
  }
  .recent-categories button {
    padding: 4px 8px;
    font-size: 11px;
  }
  /* 商品位置三级联动组件由 PublishAddressCascader 自带样式处理 */
  /* 自动分类提示与候选 */
  .auto-category-hint {
    flex-wrap: wrap;
    padding: 6px 10px;
    font-size: 12px;
  }
  .auto-category-candidates {
    flex-wrap: wrap;
    gap: 6px;
    padding: 6px 10px;
  }
  .candidate-btn {
    padding: 4px 10px;
    font-size: 11px;
  }
  /* 发货设置 */
  .shipping-grid {
    gap: 8px;
  }
  .shipping-item {
    padding: 6px 0;
  }
  :deep(.base-table) {
    display: block;
    overflow-x: auto;
    white-space: nowrap;
    -webkit-overflow-scrolling: touch;
  }
  /* 字符计数 */
  .char-count {
    font-size: 11px;
  }
  /* 预览卡片图片缩小 */
  .product-thumb {
    width: 90px !important;
    height: 68px !important;
  }
  /* 草稿工具栏 */
  .draft-toolbar {
    flex-wrap: wrap;
    gap: 8px;
    padding: 8px 10px;
  }
  .draft-tip {
    font-size: 12px;
  }
  .clear-draft-btn {
    padding: 5px 10px;
    font-size: 12px;
  }
}
</style>
