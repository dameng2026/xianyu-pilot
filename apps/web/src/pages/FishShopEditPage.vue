<template>
  <div class="publish-layout">
    <div>
      <!-- 顶部面包屑与返回 -->
      <div class="edit-page-header">
        <button class="back-btn" @click="handleCancel" title="返回商品列表">
          <span aria-hidden="true">←</span> 返回
        </button>
        <div class="edit-page-title">
          <span class="edit-page-title-icon" aria-hidden="true">✎</span>
          <span>编辑鱼小铺商品</span>
          <span v-if="detail?.itemId" class="edit-page-item-id">itemId: {{ detail.itemId }}</span>
        </div>
      </div>

      <div v-if="loadError" class="global-notice error">
        <div class="notice-content">
          <span class="notice-icon" aria-hidden="true">⚠</span>
          <span>{{ loadError }}</span>
        </div>
        <button class="notice-retry" @click="retryLoad">重试</button>
      </div>
      <div v-if="permissionError" class="global-notice error">
        <div class="notice-content">
          <span class="notice-icon" aria-hidden="true">⛔</span>
          <span>{{ permissionError }}</span>
        </div>
        <button class="notice-retry" @click="handleCancel">返回列表</button>
      </div>
      <div v-if="error" class="global-notice error">
        <div class="notice-content">
          <span class="notice-icon" aria-hidden="true">⚠</span>
          <span>{{ error }}</span>
        </div>
        <button class="notice-dismiss" @click="error = ''">关闭</button>
      </div>
      <div v-if="success" class="global-notice success">
        <div class="notice-content">
          <span class="notice-icon" aria-hidden="true">✓</span>
          <span>{{ success }}</span>
        </div>
      </div>

      <!-- 骨架屏加载 -->
      <div v-if="loading" class="skeleton-stack">
        <div class="skeleton-card">
          <div class="skeleton-title"></div>
          <div class="skeleton-line w-60"></div>
          <div class="skeleton-line w-80"></div>
          <div class="skeleton-line w-40"></div>
        </div>
        <div class="skeleton-card">
          <div class="skeleton-title"></div>
          <div class="skeleton-grid">
            <div class="skeleton-block"></div>
            <div class="skeleton-block"></div>
          </div>
        </div>
        <div class="skeleton-card">
          <div class="skeleton-title"></div>
          <div class="skeleton-line w-50"></div>
          <div class="skeleton-line w-70"></div>
        </div>
        <div class="skeleton-tip">
          <span class="skeleton-tip-icon" aria-hidden="true">⏳</span>
          正在加载商品详情（标题、图片、规格、SKU、运费、地址）...
        </div>
      </div>

      <CardPanel v-if="!loading && detail" title="宝贝基础信息">
        <div class="form-grid">
          <div class="form-row">
            <label>闲鱼账号</label>
            <input :value="selectedAccountLabel" disabled>
          </div>
          <div class="form-row">
            <label>商品 itemId</label>
            <input :value="detail.itemId" disabled>
          </div>
          <div class="form-row">
            <label>宝贝标题</label>
            <input v-model="form.title" maxlength="30" placeholder="请填写宝贝标题">
            <span class="char-count">{{ form.title.length }}/30</span>
          </div>
          <div class="form-row">
            <label>宝贝描述</label>
            <textarea v-model="form.description" rows="4" placeholder="请详细描述宝贝的成色、功能、使用感受等信息..."></textarea>
          </div>
        </div>

        <div style="margin-top:18px">
          <b>宝贝图片（{{ form.imageUrls.length }}/10 张，拖拽可调整顺序）</b>
          <div class="image-strip" style="margin-top:12px">
            <div
              v-for="(img, idx) in form.imageUrls"
              :key="idx"
              class="img-card"
              :class="{ 'img-card-main': idx === 0 }"
              draggable="true"
              @dragstart="onDragStart(idx, $event)"
              @dragover.prevent="onDragOver(idx, $event)"
              @drop="onDrop(idx, $event)"
            >
              <img :src="displayImageUrl(img)" alt="" style="width:100%;height:100%;object-fit:cover;border-radius:10px">
              <span v-if="idx === 0" class="img-main-badge">主图</span>
              <div class="img-remove" @click="removeImage(idx)" title="移除图片">×</div>
            </div>
            <div
              v-if="form.imageUrls.length < 10"
              class="img-card add-card"
              @click="triggerUpload"
            >
              <span style="font-size:28px;color:#999">＋</span>
              <span style="font-size:12px;color:#999;margin-top:4px">上传图片</span>
            </div>
            <input
              ref="fileInput"
              type="file"
              accept="image/jpeg,image/png,image/gif,image/webp"
              multiple
              style="display:none"
              @change="onFileSelect"
            >
          </div>
          <p class="image-hint subtle">仅支持 JPEG/PNG/GIF/WebP，单张 ≤ 5MB。主图为第一张。</p>
        </div>
      </CardPanel>

      <CardPanel v-if="!loading && detail" title="商品分类" style="margin-top:16px">
        <p class="subtle">
          已选分类：{{ detail.category || detail.catName || '未选择' }}
          <span class="subtle" style="margin-left:8px;font-size:12px">（编辑场景分类不可更改，如需切换分类请重新发布商品）</span>
        </p>
      </CardPanel>

      <CardPanel v-if="!loading && detail" title="商品位置" style="margin-top:16px">
        <PublishAddressCascader v-model="selectedAddress" />
      </CardPanel>

      <CardPanel v-if="!loading && detail" ref="multispecCardRef" style="margin-top:16px">
        <template #title>
          <div class="multispec-card-title">
            <span class="multispec-card-title-icon" aria-hidden="true">⚙</span>
            <span>多规格商品（鱼小铺专属）</span>
            <span v-if="isFishShopAccount" class="multispec-account-badge multispec-account-badge-ok">鱼小铺账号</span>
          </div>
        </template>

        <!-- 多规格状态提示横幅 -->
        <div class="multispec-banner multispec-banner-active">
          <span class="multispec-banner-icon" aria-hidden="true">✓</span>
          <span>已开启多规格商品，最多支持 2 个规格类型，价格和库存请在 SKU 组合中填写</span>
        </div>

        <!-- 多规格开关（编辑场景下禁用，始终开启） -->
        <div class="auto-delivery-toggle multispec-toggle-row multispec-toggle-on">
          <div class="auto-delivery-toggle-info">
            <span class="auto-delivery-title">
              <span class="multispec-toggle-status-dot on" aria-hidden="true"></span>
              多规格已开启
            </span>
            <span class="subtle">编辑场景下多规格始终开启，至少需要一个规格类型与一个 SKU</span>
          </div>
          <ToggleSwitch :on="true" :disabled="true" />
        </div>

        <MultiSpecEditor
          v-model="multiSpecData"
          @upload-sku-cover="onUploadSkuCover"
        />
      </CardPanel>

      <CardPanel v-if="!loading && detail" title="发货设置" style="margin-top:16px">
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
      <CardPanel v-if="!loading && detail" title="商品预览">
        <div class="product-cell">
          <div class="product-thumb" style="width:130px;height:98px">
            <img v-if="displayCoverImage" :src="displayCoverImage" alt="" style="width:100%;height:100%;object-fit:cover;border-radius:10px">
            <div v-else style="width:100%;height:100%;background:#f0f0f0;border-radius:10px;display:flex;align-items:center;justify-content:center;color:#ccc;font-size:12px">暂无图片</div>
          </div>
          <div>
            <h3 style="margin:0 0 8px">{{ form.title || '商品标题' }}</h3>
            <b style="color:#ef4444;font-size:22px">¥{{ displayPrice }}</b>
          </div>
        </div>
      </CardPanel>
      <CardPanel v-if="!loading && detail" title="编辑摘要" style="margin-top:16px">
        <div class="option-line"><span>闲鱼账号</span><b>{{ selectedAccountLabel || '未选择' }}</b></div>
        <div class="option-line"><span>商品 itemId</span><b>{{ detail.itemId }}</b></div>
        <div class="option-line"><span>规格类型数</span><b>{{ multiSpecData.propertyGroups.length }}</b></div>
        <div class="option-line"><span>SKU 数</span><b>{{ multiSpecData.skuList.length }}</b></div>
        <div class="option-line"><span>总库存</span><b>{{ totalStock }}件</b></div>
        <div class="option-line"><span>起售价</span><b>¥{{ minPrice }}</b></div>
      </CardPanel>
      <CardPanel v-if="!loading && detail" title="编辑检查" style="margin-top:16px">
        <div v-for="i in checks" :key="i.text" class="option-line">
          <span><i :class="['dot', i.ok ? '' : 'orange']"></i>{{ i.text }}</span>
          <b :style="{color:i.ok?'var(--green)':'#f59e0b'}">{{ i.ok ? '通过' : '待完善' }}</b>
        </div>
      </CardPanel>
    </div>

    <div class="bottom-actions">
      <AppButton @click="handleCancel">取消</AppButton>
      <AppButton type="primary" :loading="submitting" :disabled="!canSubmit" @click="submit">{{ canSubmit ? '保存编辑' : '多规格数据不完整' }}</AppButton>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import CardPanel from '../components/CardPanel.vue'
import AppButton from '../components/AppButton.vue'
import ToggleSwitch from '../components/ToggleSwitch.vue'
import PublishAddressCascader from '../components/PublishAddressCascader.vue'
import MultiSpecEditor from '../components/MultiSpecEditor.vue'
import { getLiteAccounts } from '../api/accounts.js'
import { editFishShopItem, getFishShopDetail } from '../api/fishShop.js'
import { uploadImage } from '../api/misc.js'
import { accountName } from '../utils/format.js'
import { confirmAction } from '../utils/confirmAction.js'
import { imageUploadValidationMessage as uploadImageValidationMessage } from '../utils/imageUploadPolicy.js'
import { isPublishAddressComplete, normalizePublishAddress } from '../utils/publishAddress.js'
import { setNavigationGuard, clearNavigationGuard } from '../utils/navigationGuard.js'
import { guardFeatureAction } from '../composables/featureGuard.js'
import { friendlyError } from '../utils/friendlyError.js'

const emit = defineEmits(['navigate'])

// ---- 路由参数解析：#/fish-shop-edit/{accountId}/{itemId} ----
const routeAccountId = ref('')
const routeItemId = ref('')
const permissionError = ref('')

function parseRouteParams() {
  const raw = (location.hash || '').replace(/^#\//, '')
  // 期望格式：fish-shop-edit/{accountId}/{itemId}
  const m = raw.match(/^fish-shop-edit\/([^/]+)\/(.+)$/)
  if (!m) {
    permissionError.value = '编辑页地址参数缺失，请从商品列表进入'
    return
  }
  routeAccountId.value = m[1]
  routeItemId.value = decodeURIComponent(m[2])
}

// ---- 状态 ----
const loading = ref(true)
const loadError = ref('')
const error = ref('')
const success = ref('')
const submitting = ref(false)
const fileInput = ref(null)
const dragIndex = ref(-1)
const multispecCardRef = ref(null)
const accounts = ref([])
const detail = ref(null)
const selectedAddress = ref(null)

const form = reactive({
  title: '',
  description: '',
  imageUrls: [],
  supportSelfPick: false,
})

const multiSpecData = reactive({
  propertyGroups: [],
  skuList: [],
})

const shippingMode = ref('free')
function setShipping(mode) { shippingMode.value = mode }

// ---- 计算属性 ----
const selectedAccountLabel = computed(() => {
  const acc = accounts.value.find(a => String(a.id) === String(routeAccountId.value))
  return acc ? accountName(acc) : ''
})

const isFishShopAccount = computed(() => {
  const acc = accounts.value.find(a => String(a.id) === String(routeAccountId.value))
  return !!(acc && acc.fishShopUser)
})

const displayCoverImage = computed(() => displayImageUrl(form.imageUrls[0] || ''))

const displayPrice = computed(() => {
  // 编辑场景下：起售价取 SKU 最低价
  const prices = multiSpecData.skuList
    .map(s => parseFloat(s.price))
    .filter(p => !isNaN(p) && p > 0)
  if (prices.length === 0) return '0.00'
  return Math.min(...prices).toFixed(2)
})

const totalStock = computed(() => {
  return multiSpecData.skuList.reduce((sum, s) => {
    const q = parseInt(s.quantity, 10)
    return sum + (isNaN(q) ? 0 : q)
  }, 0)
})

const minPrice = computed(() => displayPrice.value)

const checks = computed(() => [
  { text: '已选择闲鱼账号', ok: !!routeAccountId.value },
  { text: '账号属于鱼小铺', ok: isFishShopAccount.value },
  { text: '商品 itemId 已加载', ok: !!detail.value?.itemId },
  { text: '标题已填写', ok: form.title.trim().length > 0 },
  { text: '商品描述已填写', ok: form.description.trim().length > 0 },
  { text: '已上传商品图片', ok: form.imageUrls.length > 0 },
  { text: '已完成省、市、区选择', ok: isPublishAddressComplete(selectedAddress.value) },
  { text: '至少一个规格类型', ok: multiSpecData.propertyGroups.length > 0 },
  { text: '至少一个 SKU', ok: multiSpecData.skuList.length > 0 },
  { text: '所有 SKU 价格已填写', ok: multiSpecData.skuList.length > 0 && multiSpecData.skuList.every(s => s.price !== '' && Number(s.price) >= 0) },
  { text: '所有 SKU 库存已填写', ok: multiSpecData.skuList.length > 0 && multiSpecData.skuList.every(s => s.quantity !== '' && Number(s.quantity) >= 0) },
])

const canSubmit = computed(() => checks.value.every(i => i.ok))

// ---- 工具 ----
function displayImageUrl(url) {
  const v = String(url || '').trim()
  if (!v) return ''
  if (/^data:image\//i.test(v)) return v
  if (v.startsWith('//')) return `https:${v}`
  if (v.startsWith('http://') || v.startsWith('https://')) return v
  if (v.startsWith('/uploads/')) return v
  if (v.startsWith('uploads/')) return `/${v}`
  if (v.startsWith('/')) return `https://img.alicdn.com${v}`
  return v
}

// ---- 加载 ----
async function loadAccounts() {
  try {
    const res = await getLiteAccounts()
    const data = res?.data
    const list = Array.isArray(data) ? data : (data?.records || [])
    if (!Array.isArray(list)) throw new Error('账号列表响应格式异常')
    accounts.value = list
  } catch (e) {
    loadError.value = `账号列表加载失败：${e?.message || '请稍后重试'}`
  }
}

async function loadDetail() {
  if (!routeAccountId.value || !routeItemId.value) {
    loading.value = false
    return
  }
  // 权限校验：必须为鱼小铺账号（前端快速校验，后端会再次校验）
  const acc = accounts.value.find(a => String(a.id) === String(routeAccountId.value))
  if (!acc) {
    permissionError.value = '未找到该闲鱼账号，无法加载商品详情'
    loading.value = false
    return
  }
  if (!acc.fishShopUser) {
    permissionError.value = '当前闲鱼账号不支持多规格商品，只有鱼小铺账号可以编辑商品'
    loading.value = false
    return
  }

  loading.value = true
  loadError.value = ''
  try {
    const res = await getFishShopDetail({
      xianyuAccountId: Number(routeAccountId.value),
      itemId: routeItemId.value,
    })
    const data = res?.data
    if (!data || typeof data !== 'object') {
      throw new Error('商品详情响应格式异常')
    }
    // 后端在 canEdit=0 时会返回失败（403），这里仅做兜底
    if (data.canEdit === 0) {
      permissionError.value = data.editNote || '当前商品暂不支持编辑'
      loading.value = false
      return
    }
    detail.value = data

    // 回显基础字段
    // 标题使用 itemTextDTO.title，正文优先使用 itemTextDTO.desc
    // 不使用 wlDescription 覆盖 desc（避免标题正文重复）
    form.title = data.title || ''
    form.description = data.description || ''
    form.imageUrls = Array.isArray(data.imageUrls) ? [...data.imageUrls] : []
    form.supportSelfPick = false  // 默认值，下面会用 detail 中的值覆盖

    // 数据来源标识：editdetail 接口成功返回 source=editdetail，字段直接可用
    // 失败降级时 source=local_fallback，需要从 snapshot 中读取
    const isEditDetailSource = data.source === 'editdetail'
    const snapshot = data.snapshot || {}
    const internalItem = snapshot.internalItem || {}

    // 回显地址
    // 优先使用 editdetail 接口直接返回的扁平地址字段，缺失时回退到 snapshot
    const addrProv = data.prov || internalItem.itemAddrDTO?.prov || ''
    const addrCity = data.city || internalItem.itemAddrDTO?.city || ''
    const addrArea = data.area || internalItem.itemAddrDTO?.area || ''
    if (addrProv || addrCity || addrArea) {
      selectedAddress.value = {
        poiName: data.poiName || internalItem.itemAddrDTO?.poiName || '',
        prov: addrProv,
        city: addrCity,
        area: addrArea,
        divisionId: String(data.divisionId || internalItem.itemAddrDTO?.divisionId || ''),
        gps: data.gps || internalItem.itemAddrDTO?.gps || '',
        poiId: String(data.poiId || internalItem.itemAddrDTO?.poiId || ''),
      }
    }

    // 回显运费设置
    // 优先使用 editdetail 接口返回的扁平字段（已安全转换为布尔值）
    // 字符串布尔值由后端 _safe_str_to_bool 统一处理，前端不再重复转换
    const canFreeShipping = isEditDetailSource
      ? data.canFreeShipping === true
      : internalItem.itemPostFeeDTO?.canFreeShipping === true
    const supportFreight = isEditDetailSource
      ? data.supportFreight === true
      : internalItem.itemPostFeeDTO?.supportFreight === false
    const postPriceInCent = isEditDetailSource
      ? Number(data.postPriceInCent || 0)
      : Number(internalItem.itemPostFeeDTO?.postPriceInCent || 0)
    const onlyTakeSelf = isEditDetailSource
      ? data.onlyTakeSelf
      : internalItem.itemPostFeeDTO?.onlyTakeSelf

    if (canFreeShipping) {
      shippingMode.value = 'free'
    } else if (supportFreight === false) {
      shippingMode.value = 'none'
    } else if (postPriceInCent > 0) {
      shippingMode.value = 'fixed'
    } else {
      shippingMode.value = 'free'
    }
    if (onlyTakeSelf !== undefined && onlyTakeSelf !== null) {
      form.supportSelfPick = !!onlyTakeSelf
    }

    // 回显分类（用于显示，不可编辑）
    // 优先使用 editdetail 接口返回的 catName
    detail.value.category = data.catName || data.category || internalItem.itemCatDTO?.catName || ''

    // 回显多规格数据
    // 简单商品：itemProperties 为空列表，多规格开关保持关闭
    // 多规格商品：itemProperties / itemSkuList 由 editdetail 接口直接返回
    const itemProperties = Array.isArray(data.itemProperties) ? data.itemProperties : []
    multiSpecData.propertyGroups = itemProperties.map(g => ({
      propertyName: g.propertyName || '',
      supportImage: !!g.supportImage,
      propertyValues: (g.propertyValues || []).map(v => ({
        propertyValue: v.propertyValue || '',
        propertyValueImg: v.propertyValueImg || '',
      })),
    }))
    // 若回显后无规格类型，自动添加一个空规格，避免空表单
    // 注意：简单商品也会走到这里，用户可根据需要切换为多规格
    if (multiSpecData.propertyGroups.length === 0) {
      multiSpecData.propertyGroups.push({
        propertyName: '',
        supportImage: false,
        propertyValues: [{ propertyValue: '', propertyValueImg: '' }],
      })
    }

    // 回显 SKU（保留 skuId / inventoryId 用于编辑）
    // priceInCent 为分单位，转换为元（避免浮点精度问题，使用字符串处理）
    // quantity 为非负整数，0 是合法库存
    const itemSkuList = Array.isArray(data.itemSkuList) ? data.itemSkuList : []
    multiSpecData.skuList = itemSkuList.map(s => {
      const priceInCent = Number(s.priceInCent || 0)
      // 使用整数分转字符串元的方式，避免浮点精度问题
      const priceYuan = (priceInCent / 100).toFixed(2)
      return {
        price: priceInCent > 0 ? priceYuan : '',
        quantity: s.quantity != null ? String(s.quantity) : '',
        propertyList: (s.propertyList || []).map(p => ({
          propertyText: p.propertyText || '',
          valueText: p.valueText || '',
        })),
        skuId: s.skuId || '',
        inventoryId: s.inventoryId || '',
        coverImage: s.coverImage || '',
      }
    })

    // 加载兜底提示：editdetail 失败时后端会返回 warning 字段
    if (data.warning) {
      loadError.value = data.warning
    }
  } catch (e) {
    loadError.value = `加载商品详情失败：${e?.message || '请稍后重试'}`
  } finally {
    loading.value = false
  }
}

// ---- 图片操作 ----
function triggerUpload() {
  if (!routeAccountId.value) {
    error.value = '账号参数缺失，无法上传图片'
    return
  }
  fileInput.value?.click()
}

async function onFileSelect(e) {
  const files = e.target.files
  if (!files || files.length === 0) return
  const remaining = 10 - form.imageUrls.length
  const toUpload = Array.from(files).slice(0, remaining)
  for (const file of toUpload) {
    const validationMessage = uploadImageValidationMessage(file)
    if (validationMessage) {
      error.value = `图片 "${file.name}" ${validationMessage}`
      continue
    }
    try {
      const res = await uploadImage(Number(routeAccountId.value), file)
      if (res.code === 200 && res.data?.url) {
        form.imageUrls.push(res.data.url)
      } else {
        error.value = friendlyError({ message: res.msg || '图片上传失败' }, '图片上传失败，请稍后重试')
      }
    } catch (err) {
      error.value = friendlyError(err, '图片上传失败，请稍后重试')
    }
  }
  e.target.value = ''
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
function onDrop(idx, e) {
  const from = dragIndex.value
  if (from === -1 || from === idx) return
  const list = form.imageUrls
  const moved = list.splice(from, 1)[0]
  list.splice(idx, 0, moved)
  dragIndex.value = -1
}

// SKU 封面图上传（复用 uploadImage 能力）
async function onUploadSkuCover({ sIdx }) {
  if (!routeAccountId.value) {
    error.value = '账号参数缺失，无法上传 SKU 封面图'
    return
  }
  const input = document.createElement('input')
  input.type = 'file'
  input.accept = 'image/jpeg,image/png,image/gif,image/webp'
  input.onchange = async () => {
    const file = input.files?.[0]
    if (!file) return
    const validationMsg = uploadImageValidationMessage(file)
    if (validationMsg) {
      error.value = validationMsg
      return
    }
    try {
      const res = await uploadImage(Number(routeAccountId.value), file)
      const url = res?.data?.url
      if (!url) throw new Error('上传响应格式异常')
      const sku = multiSpecData.skuList[sIdx]
      if (sku) sku.coverImage = url
    } catch (e) {
      error.value = `SKU 封面图上传失败：${e?.message || '请稍后重试'}`
    }
  }
  input.click()
}

// ---- 校验 ----
function validate() {
  const miss = checks.value.find(i => !i.ok)
  if (miss) {
    error.value = `"${miss.text}" 检查未通过，请完善后再提交`
    // 多规格相关检查未通过时滚动到多规格板块
    if (miss.text && miss.text.includes('规格') || miss.text.includes('SKU') || miss.text.includes('价格') || miss.text.includes('库存')) {
      scrollToMultispec()
    }
    return false
  }
  // 规格名校验
  const names = multiSpecData.propertyGroups.map(g => (g.propertyName || '').trim()).filter(Boolean)
  if (names.length !== new Set(names).size) {
    error.value = '规格名称不能重复'
    scrollToMultispec()
    return false
  }
  if (multiSpecData.propertyGroups.length > 2) {
    error.value = '最多只能添加 2 个规格类型'
    scrollToMultispec()
    return false
  }
  if (multiSpecData.propertyGroups.length === 0) {
    error.value = '多规格：至少需要添加一个规格类型'
    scrollToMultispec()
    return false
  }
  // 每个规格类型至少一个有效值
  for (const g of multiSpecData.propertyGroups) {
    const validValues = (g.propertyValues || [])
      .map(v => (v.propertyValue || '').trim())
      .filter(Boolean)
    if (validValues.length === 0) {
      error.value = `规格「${g.propertyName || '未命名'}」至少需要一个有效规格值`
      scrollToMultispec()
      return false
    }
    if (new Set(validValues).size !== validValues.length) {
      error.value = `规格「${g.propertyName}」下存在重复的规格值`
      scrollToMultispec()
      return false
    }
  }
  // SKU 列表完整性校验
  if (multiSpecData.skuList.length === 0) {
    error.value = '多规格：SKU 列表为空，请检查规格值'
    scrollToMultispec()
    return false
  }
  for (let i = 0; i < multiSpecData.skuList.length; i++) {
    const sku = multiSpecData.skuList[i]
    const price = parseFloat(sku.price)
    if (isNaN(price) || price < 0) {
      error.value = `多规格：第 ${i + 1} 个 SKU 价格未填写或非法，请完善后再提交`
      scrollToMultispec()
      return false
    }
    const qty = parseInt(sku.quantity, 10)
    if (isNaN(qty) || qty < 0) {
      error.value = `多规格：第 ${i + 1} 个 SKU 库存未填写或非法，请完善后再提交`
      scrollToMultispec()
      return false
    }
  }
  return true
}

// 滚动到多规格板块
function scrollToMultispec() {
  try {
    const refEl = multispecCardRef.value?.$el || multispecCardRef.value
    if (refEl && refEl.scrollIntoView) {
      refEl.scrollIntoView({ behavior: 'smooth', block: 'center' })
    } else {
      const el = document.querySelector('.multispec-card-title')
      if (el && el.scrollIntoView) {
        el.scrollIntoView({ behavior: 'smooth', block: 'center' })
      }
    }
  } catch (e) { /* 忽略滚动异常 */ }
}

// ---- 提交编辑 ----
async function submit() {
  if (!await guardFeatureAction()) return
  error.value = ''
  success.value = ''
  if (!detail.value || !routeItemId.value) {
    error.value = '商品数据未加载，无法提交'
    return
  }
  if (!isFishShopAccount.value) {
    error.value = '当前闲鱼账号不是鱼小铺，无法编辑多规格商品'
    return
  }
  if (!validate()) return

  const ok = await confirmAction({
    title: '确认保存编辑到闲鱼？',
    description: `账号：${selectedAccountLabel.value || '-'}\n商品 itemId：${routeItemId.value}\n规格类型数：${multiSpecData.propertyGroups.length}\nSKU 数：${multiSpecData.skuList.length}\n总库存：${totalStock.value}\n起售价：¥${minPrice.value}`,
    dangerous: true,
  })
  if (!ok) return

  submitting.value = true
  setNavigationGuard('正在保存编辑，确定要离开吗？')
  try {
    // 构造 cleaned 数据
    const cleanedPropertyGroups = multiSpecData.propertyGroups
      .map(g => ({
        propertyName: (g.propertyName || '').trim(),
        supportImage: !!g.supportImage,
        propertyValues: (g.propertyValues || [])
          .map(v => ({
            propertyValue: (v.propertyValue || '').trim(),
            propertyValueImg: v.propertyValueImg || '',
          }))
          .filter(v => v.propertyValue),
      }))
      .filter(g => g.propertyName && g.propertyValues.length > 0)

    const cleanedSkuList = multiSpecData.skuList
      .map(s => ({
        price: s.price,
        quantity: s.quantity,
        propertyList: (s.propertyList || []).map(p => ({
          propertyText: p.propertyText,
          valueText: p.valueText,
        })),
        // 保留 skuId / inventoryId 用于编辑（后端会以响应为准重新校准）
        skuId: s.skuId || '',
        inventoryId: s.inventoryId || '',
        coverImage: s.coverImage || '',
      }))
      .filter(s => s.propertyList.length > 0)

    const locationData = normalizePublishAddress(selectedAddress.value)

    const res = await editFishShopItem({
      xianyuAccountId: Number(routeAccountId.value),
      itemId: routeItemId.value,
      title: form.title.slice(0, 30),
      description: form.description,
      imageUrls: form.imageUrls,
      itemProperties: cleanedPropertyGroups,
      itemSkuList: cleanedSkuList,
      shippingMode: shippingMode.value,
      supportSelfPick: form.supportSelfPick,
      postFee: 0,
      location: locationData,
      category: detail.value.category ? { catName: detail.value.category } : null,
    })

    if (res && typeof res === 'object' && [0, 200].includes(Number(res.code))) {
      success.value = '编辑成功！商品列表将在 1 秒后刷新...'
      clearNavigationGuard()
      // 标记商品待同步
      localStorage.setItem('xianyu_pending_sync', 'true')
      setTimeout(() => emit('navigate', 'products'), 1000)
    } else {
      error.value = friendlyError({ message: res?.msg || '编辑失败' }, '编辑失败，请稍后重试')
    }
  } catch (e) {
    error.value = friendlyError(e, '编辑失败，请稍后重试')
  } finally {
    submitting.value = false
  }
}

function handleCancel() {
  emit('navigate', 'products')
}

async function retryLoad() {
  loadError.value = ''
  permissionError.value = ''
  loading.value = true
  await loadAccounts()
  await loadDetail()
}

// ---- 生命周期 ----
onMounted(async () => {
  parseRouteParams()
  await loadAccounts()
  if (routeAccountId.value && routeItemId.value) {
    await loadDetail()
  } else {
    loading.value = false
  }
  // 编辑场景下设置导航守卫，避免误操作离开
  setNavigationGuard('正在编辑商品，确定要离开吗？')
})
</script>

<style scoped>
.publish-layout {
  display: grid;
  grid-template-columns: 1fr 320px;
  gap: 16px;
  padding: 16px;
  align-items: start;
}
.bottom-actions {
  position: fixed;
  bottom: 0;
  left: 0;
  right: 0;
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  padding: 12px 24px;
  background: var(--bg, #fff);
  border-top: 1px solid var(--border, #e5e7eb);
  z-index: 5;
}

/* ---- 顶部面包屑与返回 ---- */
.edit-page-header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 14px;
  background: linear-gradient(135deg, #eff6ff 0%, #dbeafe 100%);
  border: 1px solid #bfdbfe;
  border-radius: 8px;
  margin-bottom: 12px;
}
.back-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 6px 12px;
  background: #fff;
  border: 1px solid #bfdbfe;
  color: #1e40af;
  border-radius: 6px;
  cursor: pointer;
  font-size: 13px;
  font-weight: 500;
  transition: all 0.15s;
  flex-shrink: 0;
}
.back-btn:hover {
  background: #dbeafe;
  border-color: #93c5fd;
  transform: translateX(-2px);
}
.edit-page-title {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #1e3a8a;
  font-weight: 600;
  font-size: 15px;
  min-width: 0;
  flex-wrap: wrap;
}
.edit-page-title-icon {
  font-size: 16px;
}
.edit-page-item-id {
  padding: 2px 8px;
  background: #fff;
  color: #1e40af;
  border-radius: 10px;
  font-size: 12px;
  font-weight: 500;
  border: 1px solid #bfdbfe;
}

/* ---- 通知条 ---- */
.global-notice {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 14px;
  border-radius: 8px;
  margin-bottom: 10px;
  font-size: 13px;
  flex-wrap: wrap;
}
.notice-content {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  flex: 1;
}
.notice-icon {
  font-size: 16px;
  flex-shrink: 0;
}
.notice-retry,
.notice-dismiss {
  padding: 4px 12px;
  background: #fff;
  border: 1px solid currentColor;
  border-radius: 4px;
  cursor: pointer;
  font-size: 12px;
  font-weight: 500;
  transition: all 0.15s;
  flex-shrink: 0;
}
.notice-retry:hover {
  background: currentColor;
  color: #fff;
}
.notice-dismiss {
  color: #6b7280;
}
.notice-dismiss:hover {
  background: #f3f4f6;
}

/* ---- 骨架屏 ---- */
.skeleton-stack {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.skeleton-card {
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  padding: 16px;
}
.skeleton-title {
  height: 18px;
  width: 30%;
  background: linear-gradient(90deg, #f3f4f6 25%, #e5e7eb 50%, #f3f4f6 75%);
  background-size: 200% 100%;
  animation: skeleton-shimmer 1.4s infinite;
  border-radius: 4px;
  margin-bottom: 12px;
}
.skeleton-line {
  height: 14px;
  background: linear-gradient(90deg, #f3f4f6 25%, #e5e7eb 50%, #f3f4f6 75%);
  background-size: 200% 100%;
  animation: skeleton-shimmer 1.4s infinite;
  border-radius: 4px;
  margin-bottom: 8px;
}
.skeleton-line.w-40 { width: 40%; }
.skeleton-line.w-50 { width: 50%; }
.skeleton-line.w-60 { width: 60%; }
.skeleton-line.w-70 { width: 70%; }
.skeleton-line.w-80 { width: 80%; }
.skeleton-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}
.skeleton-block {
  height: 60px;
  background: linear-gradient(90deg, #f3f4f6 25%, #e5e7eb 50%, #f3f4f6 75%);
  background-size: 200% 100%;
  animation: skeleton-shimmer 1.4s infinite;
  border-radius: 6px;
}
.skeleton-tip {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  background: #eff6ff;
  border: 1px solid #bfdbfe;
  color: #1e40af;
  border-radius: 8px;
  font-size: 13px;
}
.skeleton-tip-icon {
  font-size: 16px;
  animation: skeleton-pulse 1.4s infinite;
}
@keyframes skeleton-shimmer {
  0% { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}
@keyframes skeleton-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}

/* ---- 图片卡片：主图标识 ---- */
.img-card {
  position: relative;
  transition: transform 0.15s, box-shadow 0.15s;
}
.img-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
}
.img-card-main {
  border-color: #3b82f6;
  box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.2);
}
.img-main-badge {
  position: absolute;
  top: 4px;
  left: 4px;
  padding: 1px 6px;
  background: linear-gradient(135deg, #3b82f6 0%, #2563eb 100%);
  color: #fff;
  font-size: 10px;
  font-weight: 600;
  border-radius: 4px;
  letter-spacing: 0.5px;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.2);
}
.img-remove {
  position: absolute;
  top: 4px;
  right: 4px;
  width: 20px;
  height: 20px;
  background: rgba(0, 0, 0, 0.55);
  color: #fff;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  cursor: pointer;
  transition: background 0.15s, transform 0.15s;
  line-height: 1;
}
.img-remove:hover {
  background: rgba(220, 38, 38, 0.85);
  transform: scale(1.1);
}

/* ---- 响应式：移动端单列 ---- */
@media (max-width: 1024px) {
  .publish-layout {
    grid-template-columns: 1fr;
  }
}
@media (max-width: 768px) {
  .publish-layout {
    padding: 10px;
    gap: 10px;
  }
  .edit-page-header {
    padding: 8px 10px;
  }
  .edit-page-title {
    font-size: 14px;
  }
  .bottom-actions {
    padding: 10px 12px;
  }
  .skeleton-grid {
    grid-template-columns: 1fr;
  }
}
.form-grid { display: grid; gap: 12px; }
.form-row { display: flex; flex-direction: column; gap: 4px; }
.form-row input, .form-row textarea, .form-row select {
  padding: 8px 10px;
  border: 1px solid var(--border, #d1d5db);
  border-radius: 6px;
  font-size: 14px;
  background: var(--input-bg, #fff);
  color: var(--text, #111827);
}
.char-count { font-size: 12px; color: var(--muted, #6b7280); align-self: flex-end; }
.image-strip { display: flex; flex-wrap: wrap; gap: 8px; }
.img-card {
  width: 80px; height: 80px;
  border: 1px solid var(--border, #e5e7eb);
  border-radius: 10px;
  position: relative;
  overflow: hidden;
  cursor: pointer;
}
.img-card.add-card {
  display: flex; flex-direction: column;
  align-items: center; justify-content: center;
  background: var(--bg-soft, #f9fafb);
  border-style: dashed;
}
.img-remove {
  position: absolute; top: 2px; right: 4px;
  width: 18px; height: 18px;
  background: rgba(0,0,0,0.5);
  color: #fff;
  border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  font-size: 12px;
  cursor: pointer;
}
.image-hint { font-size: 12px; margin-top: 6px; }
.subtle { color: var(--muted, #6b7280); font-size: 13px; }
.shipping-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 12px;
}
.shipping-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  border: 1px solid var(--border, #e5e7eb);
  border-radius: 6px;
}
.auto-delivery-toggle {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
}
.auto-delivery-toggle-info {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.auto-delivery-title { font-weight: 600; }

/* ---- 多规格商品板块（与发布页保持一致） ---- */
.multispec-card-title {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.multispec-card-title-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: 6px;
  background: linear-gradient(135deg, #0d6bff, #3186ff);
  color: #fff;
  font-size: 12px;
  font-weight: 700;
}
.multispec-account-badge {
  display: inline-flex;
  align-items: center;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.2px;
  line-height: 1.4;
}
.multispec-account-badge-ok {
  background: #dcfce7;
  color: #166534;
  border: 1px solid #86efac;
}
.multispec-banner {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  border-radius: 10px;
  font-size: 13px;
  margin-bottom: 8px;
  border: 1px solid transparent;
}
.multispec-banner-icon {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  font-size: 11px;
  font-weight: 700;
}
.multispec-banner-active {
  background: #ecfdf5;
  color: #065f46;
  border-color: #6ee7b7;
}
.multispec-banner-active .multispec-banner-icon {
  background: #10b981;
  color: #fff;
}
.multispec-toggle-row {
  padding: 12px 14px;
  border-radius: 10px;
  border: 1px solid #e5eaf2;
  background: #fafcff;
  transition: all 0.2s;
}
.multispec-toggle-row.multispec-toggle-on {
  border-color: #93b8ff;
  background: #f0f6ff;
  box-shadow: 0 0 0 1px rgba(13, 107, 255, 0.1);
}
.multispec-toggle-status-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #d1d5db;
  margin-right: 6px;
  vertical-align: middle;
  transition: all 0.2s;
}
.multispec-toggle-status-dot.on {
  background: #0d6bff;
  box-shadow: 0 0 0 3px rgba(13, 107, 255, 0.18);
}
.product-cell { display: flex; gap: 12px; align-items: center; }
.product-thumb {
  background: var(--bg-soft, #f3f4f6);
  border-radius: 10px;
  flex-shrink: 0;
}
.option-line {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 6px 0;
  border-bottom: 1px dashed var(--border, #e5e7eb);
  font-size: 13px;
}
.dot {
  display: inline-block;
  width: 8px; height: 8px;
  border-radius: 50%;
  background: var(--green, #10b981);
  margin-right: 6px;
}
.dot.orange { background: #f59e0b; }
.global-notice {
  padding: 10px 14px;
  border-radius: 8px;
  margin-bottom: 12px;
  font-size: 14px;
}
.global-notice.error { background: #fee2e2; color: #b91c1c; }
.global-notice.success { background: #d1fae5; color: #065f46; }
.global-notice.info { background: #dbeafe; color: #1e40af; }

@media (max-width: 768px) {
  .publish-layout { grid-template-columns: 1fr; }
  .shipping-grid { grid-template-columns: 1fr; }
}
</style>
