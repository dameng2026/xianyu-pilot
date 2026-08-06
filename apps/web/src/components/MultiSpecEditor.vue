<template>
  <div class="mse-root" ref="rootEl">
    <!-- ============ 统计信息条 ============ -->
    <div class="mse-stats-bar">
      <div class="mse-stat-chip">
        <span class="mse-stat-chip-label">规格</span>
        <b class="mse-stat-chip-val">{{ propertyCount }}<span class="mse-stat-chip-unit">/2</span></b>
      </div>
      <div class="mse-stat-chip">
        <span class="mse-stat-chip-label">SKU 组合</span>
        <b class="mse-stat-chip-val">{{ modelValue.skuList.length }}</b>
      </div>
      <div class="mse-stat-chip" :class="filledStatClass">
        <span class="mse-stat-chip-label">已完善</span>
        <b class="mse-stat-chip-val">{{ modelValue.skuList.length === 0 ? '—' : filledSkuCount }}<span v-if="modelValue.skuList.length > 0" class="mse-stat-chip-unit">/{{ modelValue.skuList.length }}</span></b>
      </div>
    </div>

    <!-- ============ 规格设置区 ============ -->
    <div class="mse-section">
      <div class="mse-section-head">
        <div class="mse-step-badge">
          <span class="mse-step-num">1</span>
          <span class="mse-section-title">规格设置</span>
        </div>
        <span class="mse-section-hint">设置商品规格和规格值，最多支持 2 个规格类型，可拖动调整顺序。</span>
      </div>

      <!-- 规格行列表 -->
      <div class="mse-spec-list">
        <div
          v-for="(prop, pIdx) in modelValue.propertyGroups"
          :key="pIdx"
          class="mse-spec-row"
          :class="{ 'mse-spec-dragging': dragIdx === pIdx, 'mse-spec-dragover': dragOverIdx === pIdx, 'mse-spec-editing': editingRow === pIdx }"
          draggable="true"
          @dragstart="onDragStart(pIdx, $event)"
          @dragover.prevent="onDragOver(pIdx)"
          @dragleave="onDragLeave(pIdx)"
          @drop.prevent="onDrop(pIdx)"
          @dragend="onDragEnd"
        >
          <!-- 拖动图标 -->
          <span class="mse-drag-handle" aria-hidden="true" title="拖动排序">
            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="9" cy="6" r="1"/><circle cx="9" cy="12" r="1"/><circle cx="9" cy="18" r="1"/><circle cx="15" cy="6" r="1"/><circle cx="15" cy="12" r="1"/><circle cx="15" cy="18" r="1"/></svg>
          </span>

          <!-- 编辑模式 -->
          <template v-if="editingRow === pIdx">
            <div class="mse-spec-edit-body">
              <div class="mse-spec-edit-name-row">
                <label class="mse-field-label">规格名称</label>
                <input
                  v-model="prop.propertyName"
                  type="text"
                  class="mse-input mse-spec-name-input"
                  placeholder="规格名称，如：颜色、尺码"
                  :maxlength="20"
                />
                <span class="mse-spec-count-badge">{{ validValueCount(pIdx) }} 个值</span>
              </div>
              <div class="mse-spec-edit-values-row">
                <label class="mse-field-label">规格值</label>
                <div class="mse-spec-edit-tags">
                  <span v-for="(val, vIdx) in prop.propertyValues" :key="vIdx" class="mse-tag-edit">
                    <input
                      v-model="val.propertyValue"
                      type="text"
                      class="mse-input mse-tag-input"
                      placeholder="规格值"
                      :maxlength="30"
                    />
                    <button
                      type="button"
                      class="mse-tag-remove"
                      title="删除此规格值"
                      @click="removeValue(pIdx, vIdx)"
                    ><svg viewBox="0 0 24 24" width="10" height="10" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6L6 18M6 6l12 12"/></svg></button>
                  </span>
                  <button type="button" class="mse-tag-add-btn" @click="addValue(pIdx)">
                    <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5v14M5 12h14"/></svg>
                    添加规格值
                  </button>
                </div>
              </div>
            </div>
          </template>

          <!-- 展示模式 -->
          <template v-else>
            <div class="mse-spec-display-name" @click="startEdit(pIdx)">
              <span class="mse-spec-name-text">{{ prop.propertyName || '点击编辑名称' }}</span>
              <span class="mse-spec-count-badge">{{ validValueCount(pIdx) }} 个值</span>
            </div>
            <div class="mse-spec-tags">
              <span
                v-for="(val, vIdx) in validValueList(pIdx)"
                :key="vIdx"
                class="mse-tag"
              >
                <span v-if="isColorSpec(pIdx) && colorForValue(val)" class="mse-color-dot" :style="{ background: colorForValue(val) }"></span>
                {{ val }}
              </span>
              <span v-if="validValueCount(pIdx) === 0" class="mse-tag mse-tag-empty">暂无规格值，点击编辑添加</span>
            </div>
          </template>

          <!-- 右侧操作按钮组 -->
          <div class="mse-spec-actions">
            <button v-if="editingRow !== pIdx" type="button" class="mse-action-btn mse-action-add" @click="addValue(pIdx)" title="添加规格值">
              <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5v14M5 12h14"/></svg>
              添加值
            </button>
            <button v-if="editingRow === pIdx" type="button" class="mse-action-btn mse-action-done" @click="finishEdit">
              <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>
              完成
            </button>
            <button v-else type="button" class="mse-action-btn mse-action-edit" @click="startEdit(pIdx)" title="编辑">
              <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
              编辑
            </button>
            <button type="button" class="mse-action-btn mse-action-delete" title="删除该规格类型" @click="removeProperty(pIdx)">
              <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6M10 11v6M14 11v6M9 6V4a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v2"/></svg>
            </button>
          </div>
        </div>
      </div>

      <!-- 添加规格类型按钮 -->
      <div class="mse-add-spec-area">
        <button
          v-if="modelValue.propertyGroups.length < 2"
          type="button"
          class="mse-add-spec-btn"
          @click="addProperty"
        >
          <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5v14M5 12h14"/></svg>
          添加规格类型
        </button>
        <span v-if="modelValue.propertyGroups.length < 2" class="mse-add-spec-hint">
          <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/></svg>
          最多可添加 2 个规格类型
        </span>
        <span v-else class="mse-add-spec-hint mse-add-spec-max">
          <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><path d="M22 4L12 14.01l-3-3"/></svg>
          已达到规格类型上限（2 个）
        </span>
      </div>
    </div>

    <!-- ============ SKU 组合管理区 ============ -->
    <div class="mse-section">
      <div class="mse-section-head">
        <div class="mse-step-badge">
          <span class="mse-step-num">2</span>
          <span class="mse-section-title">SKU 组合管理</span>
        </div>
        <span v-if="validPropertyGroups.length > 0 && modelValue.skuList.length > 0" class="mse-section-count">
          共 <b>{{ modelValue.skuList.length }}</b> 个 SKU 组合
        </span>
      </div>

      <!-- 空状态 -->
      <div v-if="validPropertyGroups.length === 0" class="mse-sku-empty">
        <div class="mse-empty-icon">
          <svg viewBox="0 0 48 48" width="40" height="40" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
            <rect x="6" y="10" width="36" height="30" rx="4"/>
            <path d="M6 18h36"/>
            <path d="M18 10v30"/>
            <circle cx="12" cy="14" r="1.5" fill="currentColor"/>
            <circle cx="24" cy="24" r="1.5" fill="currentColor"/>
            <circle cx="30" cy="30" r="1.5" fill="currentColor"/>
            <circle cx="36" cy="24" r="1.5" fill="currentColor"/>
          </svg>
        </div>
        <div class="mse-empty-text">请先在上方添加规格类型和规格值</div>
        <div class="mse-empty-sub">系统将根据规格值自动生成 SKU 组合，即可填写价格和库存</div>
      </div>

      <template v-else>
        <!-- 批量设置 -->
        <div class="mse-batch">
          <span class="mse-batch-label">快速操作：</span>
          <button type="button" class="mse-btn-outline" :class="{ active: batchPanel === 'price' }" @click="batchPanel = batchPanel === 'price' ? '' : 'price'">
            <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="1" x2="12" y2="23"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg>
            批量设置价格
          </button>
          <button type="button" class="mse-btn-outline" :class="{ active: batchPanel === 'stock' }" @click="batchPanel = batchPanel === 'stock' ? '' : 'stock'">
            <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/></svg>
            批量设置库存
          </button>
          <transition name="mse-batch-slide">
            <div v-if="batchPanel === 'price'" class="mse-batch-inline">
              <span class="mse-batch-inline-label">统一价格：</span>
              <input v-model="batchPrice" type="number" step="0.01" min="0" class="mse-input mse-batch-input" placeholder="请输入价格" />
              <span class="mse-batch-unit">元</span>
              <button type="button" class="mse-btn-primary" @click="applyBatchPrice">应用到全部</button>
              <button type="button" class="mse-btn-ghost" @click="batchPanel = ''">取消</button>
            </div>
            <div v-else-if="batchPanel === 'stock'" class="mse-batch-inline">
              <span class="mse-batch-inline-label">统一库存：</span>
              <input v-model="batchStock" type="number" step="1" min="0" class="mse-input mse-batch-input" placeholder="请输入库存" />
              <span class="mse-batch-unit">件</span>
              <button type="button" class="mse-btn-primary" @click="applyBatchStock">应用到全部</button>
              <button type="button" class="mse-btn-ghost" @click="batchPanel = ''">取消</button>
            </div>
          </transition>
        </div>

        <!-- SKU 表格 -->
        <div class="mse-table-wrap">
          <div class="mse-table-scroll">
            <table class="mse-table">
              <thead>
                <tr>
                  <th class="mse-th-img">商品主图</th>
                  <th v-for="(prop, pIdx) in validPropertyGroups" :key="pIdx" class="mse-th-spec">
                    {{ prop.propertyName }}
                  </th>
                  <th class="mse-th-price">价格（元）</th>
                  <th class="mse-th-stock">库存</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(sku, sIdx) in modelValue.skuList" :key="sIdx" :class="{ 'mse-tr-filled': isSkuFilled(sku) }">
                  <!-- 商品主图 -->
                  <td class="mse-td-img">
                    <div
                      class="mse-sku-img"
                      :class="{ 'mse-sku-img-empty': !skuImage(sku) }"
                      @click="$emit('upload-sku-cover', { sIdx })"
                      :title="skuImage(sku) ? '点击更换图片' : '点击上传图片'"
                    >
                      <img v-if="skuImage(sku)" :src="displayUrl(skuImage(sku))" alt="" @error="onImgError" />
                      <template v-else>
                        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5L5 21"/></svg>
                      </template>
                      <div v-if="skuImage(sku)" class="mse-sku-img-mask">
                        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M23 4v6h-6"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
                        <span>更换</span>
                      </div>
                      <div v-else class="mse-sku-img-upload-text">上传</div>
                    </div>
                  </td>
                  <!-- 规格列 -->
                  <td v-for="(prop, pIdx) in validPropertyGroups" :key="pIdx" class="mse-td-spec">
                    <span class="mse-spec-text">
                      <span v-if="isColorSpecByIdx(pIdx) && colorForValue(getSkuValue(sku, pIdx))" class="mse-color-dot" :style="{ background: colorForValue(getSkuValue(sku, pIdx)) }"></span>
                      {{ getSkuValue(sku, pIdx) || '-' }}
                    </span>
                  </td>
                  <!-- 价格 -->
                  <td class="mse-td-price">
                    <div class="mse-input-wrap">
                      <span class="mse-input-prefix">¥</span>
                      <input
                        v-model="sku.price"
                        type="number"
                        step="0.01"
                        min="0"
                        class="mse-input mse-sku-input mse-price-input"
                        :class="{ 'mse-input-error': showPriceError(sku) }"
                        placeholder="0.00"
                      />
                    </div>
                  </td>
                  <!-- 库存 -->
                  <td class="mse-td-stock">
                    <div class="mse-input-wrap">
                      <input
                        v-model="sku.quantity"
                        type="number"
                        step="1"
                        min="0"
                        class="mse-input mse-sku-input mse-stock-input"
                        :class="{ 'mse-input-error': showStockError(sku) }"
                        placeholder="0"
                      />
                      <span class="mse-input-suffix">件</span>
                    </div>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'

const props = defineProps({
  modelValue: {
    type: Object,
    required: true,
  },
})

const emit = defineEmits(['upload-sku-cover'])

const rootEl = ref(null)

// ---- 编辑模式状态 ----
const editingRow = ref(null)
function startEdit(pIdx) {
  editingRow.value = pIdx
}
function finishEdit() {
  editingRow.value = null
  rebuildSkus()
}

// ---- 拖拽排序状态 ----
const dragIdx = ref(-1)
const dragOverIdx = ref(-1)
function onDragStart(idx, e) {
  dragIdx.value = idx
  e.dataTransfer.effectAllowed = 'move'
  // Firefox 要求设置 dataTransfer 数据否则 drop 事件不触发
  e.dataTransfer.setData('text/plain', String(idx))
}
function onDragOver(idx) {
  dragOverIdx.value = idx
}
function onDragLeave(idx) {
  if (dragOverIdx.value === idx) dragOverIdx.value = -1
}
function onDrop(idx) {
  const from = dragIdx.value
  dragOverIdx.value = -1
  if (from === -1 || from === idx) return
  const list = props.modelValue.propertyGroups
  const moved = list.splice(from, 1)[0]
  list.splice(idx, 0, moved)
  dragIdx.value = -1
  rebuildSkus()
}
function onDragEnd() {
  dragIdx.value = -1
  dragOverIdx.value = -1
}

// ---- 颜色辅助 ----
const COLOR_MAP = {
  '红': '#ef4444', '蓝': '#3b82f6', '黑': '#374151', '白': '#e5e7eb',
  '绿': '#22c55e', '黄': '#eab308', '紫': '#8b5cf6', '粉': '#ec4899',
  '灰': '#9ca3af', '橙': '#f97316', '棕': '#92400e', '咖': '#92400e',
  '金': '#d4af37', '银': '#c0c0c0', '青': '#06b6d4', '藏': '#1e3a5f',
  '米': '#f5f0e1', '杏': '#f8e8d0', '驼': '#c19a6b',
}
function isColorSpec(pIdx) {
  const prop = props.modelValue.propertyGroups[pIdx]
  if (!prop) return false
  const name = (prop.propertyName || '').trim()
  return name.includes('颜色') || name === '色' || name.endsWith('色')
}
function isColorSpecByIdx(validIdx) {
  const prop = validPropertyGroups.value[validIdx]
  if (!prop) return false
  const name = (prop.propertyName || '').trim()
  return name.includes('颜色') || name === '色' || name.endsWith('色')
}
function colorForValue(valueText) {
  if (!valueText) return ''
  const v = valueText.trim()
  for (const key of Object.keys(COLOR_MAP)) {
    if (v.includes(key)) return COLOR_MAP[key]
  }
  return ''
}

// ---- 规格类型操作 ----
function addProperty() {
  if (props.modelValue.propertyGroups.length >= 2) return
  props.modelValue.propertyGroups.push({
    propertyName: '',
    supportImage: false,
    propertyValues: [{ propertyValue: '', propertyValueImg: '' }],
  })
  editingRow.value = props.modelValue.propertyGroups.length - 1
}

function removeProperty(pIdx) {
  const prop = props.modelValue.propertyGroups[pIdx]
  const propName = prop?.propertyName || `规格 ${pIdx + 1}`
  const hasSkuData = props.modelValue.skuList.some(s =>
    s.price !== '' || s.quantity !== '' || s.coverImage
  )
  if (hasSkuData) {
    if (!window.confirm(`确定删除规格类型「${propName}」？\n\n关联的 SKU 数据（价格、库存、图片）将被删除，此操作不可撤销。`)) return
  }
  props.modelValue.propertyGroups.splice(pIdx, 1)
  if (editingRow.value === pIdx) editingRow.value = null
  rebuildSkus()
}

function addValue(pIdx) {
  props.modelValue.propertyGroups[pIdx].propertyValues.push({ propertyValue: '', propertyValueImg: '' })
  if (editingRow.value !== pIdx) editingRow.value = pIdx
}

function removeValue(pIdx, vIdx) {
  const prop = props.modelValue.propertyGroups[pIdx]
  const valName = prop?.propertyValues?.[vIdx]?.propertyValue || ''
  const hasSkuData = props.modelValue.skuList.some(s =>
    s.price !== '' || s.quantity !== '' || s.coverImage
  )
  if (valName && hasSkuData) {
    if (!window.confirm(`确定删除规格值「${valName}」？\n\n关联的 SKU 数据（价格、库存、图片）将被删除，此操作不可撤销。`)) return
  }
  props.modelValue.propertyGroups[pIdx].propertyValues.splice(vIdx, 1)
  rebuildSkus()
}

// ---- SKU 笛卡尔积重建 ----
function rebuildSkus() {
  const groups = props.modelValue.propertyGroups
  const validGroups = []
  for (const g of groups) {
    const name = (g.propertyName || '').trim()
    if (!name) continue
    const values = (g.propertyValues || [])
      .map(v => typeof v === 'object' ? (v.propertyValue || '').trim() : '')
      .filter(v => v)
    if (values.length === 0) continue
    validGroups.push({ propertyName: name, values })
  }

  if (validGroups.length === 0) {
    const existing = props.modelValue.skuList[0]
    const newSku = {
      price: existing?.price ?? '',
      quantity: existing?.quantity ?? '',
      propertyList: [],
      skuId: existing?.skuId || '',
      inventoryId: existing?.inventoryId || '',
      coverImage: existing?.coverImage || '',
    }
    props.modelValue.skuList.splice(0, props.modelValue.skuList.length, newSku)
    return
  }

  const pools = validGroups.map(g => g.values.map(v => ({ propertyText: g.propertyName, valueText: v })))
  const combos = []
  const indices = new Array(pools.length).fill(0)
  while (true) {
    const combo = indices.map((idx, i) => pools[i][idx])
    combos.push(combo)
    let k = pools.length - 1
    while (k >= 0) {
      indices[k]++
      if (indices[k] < pools[k].length) break
      indices[k] = 0
      k--
    }
    if (k < 0) break
  }

  const oldSkuMap = new Map()
  for (const sku of props.modelValue.skuList) {
    const key = buildPropertyKey(sku.propertyList || [])
    if (key) oldSkuMap.set(key, sku)
  }

  const newSkuList = combos.map(combo => {
    const key = buildPropertyKey(combo)
    const old = key ? oldSkuMap.get(key) : undefined
    return {
      price: old?.price ?? '',
      quantity: old?.quantity ?? '',
      propertyList: combo,
      skuId: old?.skuId || '',
      inventoryId: old?.inventoryId || '',
      coverImage: old?.coverImage || '',
    }
  })

  props.modelValue.skuList.splice(0, props.modelValue.skuList.length, ...newSkuList)
}

function buildPropertyKey(propertyList) {
  if (!propertyList || propertyList.length === 0) return ''
  const sorted = [...propertyList].sort((a, b) => String(a.propertyText).localeCompare(String(b.propertyText)))
  return sorted.map(p => `${p.propertyText}=${p.valueText}`).join('|')
}

watch(
  () => props.modelValue.propertyGroups,
  () => rebuildSkus(),
  { deep: true, immediate: true },
)

// ---- 批量设置 ----
const batchPanel = ref('')
const batchPrice = ref('')
const batchStock = ref('')

function applyBatchPrice() {
  if (batchPrice.value === '') return
  for (const sku of props.modelValue.skuList) {
    sku.price = batchPrice.value
  }
  batchPrice.value = ''
  batchPanel.value = ''
}
function applyBatchStock() {
  if (batchStock.value === '') return
  for (const sku of props.modelValue.skuList) {
    sku.quantity = batchStock.value
  }
  batchStock.value = ''
  batchPanel.value = ''
}

// ---- SKU 表格辅助 ----
const validPropertyGroups = computed(() => {
  return props.modelValue.propertyGroups.filter(g => {
    const name = (g.propertyName || '').trim()
    if (!name) return false
    return (g.propertyValues || []).some(v => typeof v === 'object' && (v.propertyValue || '').trim())
  })
})

function getSkuValue(sku, validIdx) {
  const prop = validPropertyGroups.value[validIdx]
  if (!prop) return ''
  const found = (sku.propertyList || []).find(p => p.propertyText === prop.propertyName)
  return found?.valueText || ''
}

function skuImage(sku) {
  return sku.coverImage || ''
}

function displayUrl(url) {
  const v = String(url || '').trim()
  if (!v) return ''
  if (/^data:image\//i.test(v)) return v
  if (v.startsWith('//')) return `https:${v}`
  if (v.startsWith('http://') || v.startsWith('https://')) return v
  if (v.startsWith('/uploads/')) return v
  if (v.startsWith('/')) return `https://img.alicdn.com${v}`
  return v
}

function onImgError(e) {
  if (e && e.target) e.target.style.display = 'none'
}

// ---- 校验辅助 ----
function isPriceValid(price) {
  if (price === '' || price === null || price === undefined) return false
  const n = parseFloat(price)
  return !isNaN(n) && n >= 0
}

function isQuantityValid(qty) {
  if (qty === '' || qty === null || qty === undefined) return false
  const n = Number(qty)
  return !isNaN(n) && n >= 0 && Number.isInteger(n)
}

function isSkuFilled(sku) {
  return isPriceValid(sku.price) && isQuantityValid(sku.quantity)
}

function showPriceError(sku) {
  return sku.price !== '' && !isPriceValid(sku.price)
}
function showStockError(sku) {
  return sku.quantity !== '' && !isQuantityValid(sku.quantity)
}

// ---- 计算属性 ----
const propertyCount = computed(() => props.modelValue.propertyGroups.length)

function validValueCount(pIdx) {
  const prop = props.modelValue.propertyGroups[pIdx]
  if (!prop) return 0
  return (prop.propertyValues || []).filter(v => typeof v === 'object' && (v.propertyValue || '').trim()).length
}

function validValueList(pIdx) {
  const prop = props.modelValue.propertyGroups[pIdx]
  if (!prop) return []
  return (prop.propertyValues || [])
    .map(v => typeof v === 'object' ? (v.propertyValue || '').trim() : '')
    .filter(v => v)
}

const allSkusFilled = computed(() => {
  if (props.modelValue.skuList.length === 0) return true
  return props.modelValue.skuList.every(isSkuFilled)
})

const filledSkuCount = computed(() => {
  return props.modelValue.skuList.filter(s => isSkuFilled(s)).length
})

const filledStatClass = computed(() => {
  if (props.modelValue.skuList.length === 0) return ''
  if (allSkusFilled.value) return 'mse-stat-ok'
  return 'mse-stat-warn'
})

defineExpose({
  allSkusFilled,
  filledSkuCount,
  rootEl,
})
</script>

<style scoped>
.mse-root {
  --mse-primary: #1677ff;
  --mse-primary-dark: #0958d9;
  --mse-primary-soft: #eef4ff;
  --mse-primary-softer: #f5f9ff;
  --mse-border: #e7ecf3;
  --mse-border-light: #f0f4fa;
  --mse-border-strong: #cbd5e1;
  --mse-text: #172033;
  --mse-text-sub: #667085;
  --mse-text-mute: #98a2b3;
  --mse-bg: #f5f7fb;
  --mse-bg-soft: #f8fafc;
  --mse-green: #16bf78;
  --mse-green-bg: #ecfdf5;
  --mse-green-border: #a7f3d0;
  --mse-red: #ff5b61;
  --mse-red-bg: #fef3f2;
  --mse-red-border: #fda29b;
  --mse-orange: #ff9f22;
  --mse-orange-bg: #fffbeb;
  --mse-orange-border: #fcd34d;
  --mse-purple: #8b5cf6;
  --mse-radius: 12px;
  --mse-radius-sm: 8px;
  --mse-radius-lg: 14px;
  --mse-shadow-sm: 0 2px 8px rgba(16, 24, 40, 0.04);
  --mse-shadow-md: 0 4px 12px rgba(16, 24, 40, 0.06);
  --mse-shadow-primary: 0 4px 12px rgba(22, 119, 255, 0.2);
  margin-top: 16px;
  animation: mse-fade-in 0.4s cubic-bezier(0.4, 0, 0.2, 1) both;
}

@keyframes mse-fade-in {
  from { opacity: 0; transform: translateY(8px); }
  to { opacity: 1; transform: translateY(0); }
}

@keyframes mse-pulse-soft {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.7; }
}

@keyframes mse-bounce-in {
  0% { transform: scale(0.95); opacity: 0; }
  50% { transform: scale(1.02); }
  100% { transform: scale(1); opacity: 1; }
}

/* ============ 统计信息条 ============ */
.mse-stats-bar {
  display: flex;
  align-items: stretch;
  gap: 12px;
  margin-bottom: 20px;
  flex-wrap: wrap;
}
.mse-stat-chip {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 10px;
  padding: 14px 18px;
  background: #fff;
  border: 1px solid var(--mse-border);
  border-radius: var(--mse-radius);
  transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
  box-shadow: var(--mse-shadow-sm);
  overflow: hidden;
  min-width: 120px;
}
.mse-stat-chip::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  width: 4px;
  height: 100%;
  background: linear-gradient(180deg, var(--mse-primary), #4096ff);
  border-radius: 0 2px 2px 0;
  opacity: 0.6;
  transition: opacity 0.2s;
}
.mse-stat-chip:hover {
  border-color: var(--mse-border-strong);
  box-shadow: var(--mse-shadow-md);
  transform: translateY(-1px);
}
.mse-stat-chip:hover::before {
  opacity: 1;
}
.mse-stat-chip-label {
  font-size: 12px;
  color: var(--mse-text-sub);
  font-weight: 500;
  letter-spacing: 0.2px;
}
.mse-stat-chip-val {
  font-size: 22px;
  font-weight: 600;
  color: var(--mse-text);
  font-variant-numeric: tabular-nums;
  line-height: 1;
  letter-spacing: -0.3px;
}
.mse-stat-chip-unit {
  font-size: 13px;
  font-weight: 500;
  color: var(--mse-text-mute);
}
.mse-stat-chip.mse-stat-ok {
  background: linear-gradient(135deg, #fff 0%, var(--mse-green-bg) 100%);
  border-color: var(--mse-green-border);
}
.mse-stat-chip.mse-stat-ok::before {
  background: linear-gradient(180deg, var(--mse-green), #34d399);
  opacity: 1;
}
.mse-stat-chip.mse-stat-ok .mse-stat-chip-val {
  color: #059669;
}
.mse-stat-chip.mse-stat-warn {
  background: linear-gradient(135deg, #fff 0%, var(--mse-orange-bg) 100%);
  border-color: var(--mse-orange-border);
}
.mse-stat-chip.mse-stat-warn::before {
  background: linear-gradient(180deg, var(--mse-orange), #fbbf24);
  opacity: 1;
}
.mse-stat-chip.mse-stat-warn .mse-stat-chip-val {
  color: #d97706;
}

/* ============ 区域通用 ============ */
.mse-section {
  margin-bottom: 20px;
  background: #fff;
  border: 1px solid var(--mse-border);
  border-radius: var(--mse-radius);
  box-shadow: var(--mse-shadow-sm);
  overflow: hidden;
  transition: box-shadow 0.25s ease;
}
.mse-section:last-child {
  margin-bottom: 0;
}
.mse-section:hover {
  box-shadow: var(--mse-shadow-md);
}
.mse-section-head {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 16px 20px;
  margin-bottom: 0;
  flex-wrap: wrap;
  border-bottom: 1px solid var(--mse-border-light);
  background: linear-gradient(180deg, #fff 0%, var(--mse-bg-soft) 100%);
}
.mse-step-badge {
  display: inline-flex;
  align-items: center;
  gap: 10px;
}
.mse-step-num {
  width: 26px;
  height: 26px;
  border-radius: 8px;
  background: linear-gradient(135deg, var(--mse-primary), #4096ff);
  color: #fff;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 700;
  flex-shrink: 0;
  box-shadow: 0 3px 8px rgba(22, 119, 255, 0.25);
}
.mse-section-title {
  font-weight: 600;
  font-size: 15px;
  color: var(--mse-text);
  letter-spacing: -0.2px;
}
.mse-section-hint {
  font-size: 12px;
  color: var(--mse-text-mute);
  font-weight: 500;
}
.mse-section-count {
  font-size: 12px;
  color: var(--mse-primary);
  padding: 4px 12px;
  background: var(--mse-primary-soft);
  border-radius: 20px;
  font-weight: 600;
  margin-left: auto;
}
.mse-section-count b {
  font-weight: 700;
}

/* ============ 规格设置区 ============ */
.mse-spec-list {
  display: flex;
  flex-direction: column;
  gap: 0;
  padding: 12px 16px;
}
.mse-spec-row {
  display: flex;
  align-items: flex-start;
  gap: 0;
  padding: 16px 16px;
  border: 1px solid transparent;
  background: #fff;
  border-radius: var(--mse-radius-sm);
  flex-wrap: nowrap;
  transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
  position: relative;
  margin-bottom: 8px;
}
.mse-spec-row:last-child {
  margin-bottom: 0;
}
.mse-spec-row:hover {
  border-color: var(--mse-border);
  background: var(--mse-bg-soft);
  box-shadow: var(--mse-shadow-sm);
}
.mse-spec-row.mse-spec-editing {
  background: linear-gradient(135deg, #fff 0%, var(--mse-primary-softer) 100%);
  border-color: var(--mse-primary);
  box-shadow: 0 0 0 3px rgba(22, 119, 255, 0.1), var(--mse-shadow-md);
  z-index: 2;
}
.mse-spec-dragging {
  opacity: 0.4;
  transform: scale(0.98);
  box-shadow: var(--mse-shadow-md);
}
.mse-spec-dragover {
  border-color: var(--mse-primary);
  box-shadow: 0 0 0 3px rgba(22, 119, 255, 0.15);
  background: var(--mse-primary-softer);
}

.mse-drag-handle {
  color: var(--mse-text-mute);
  cursor: grab;
  display: inline-flex;
  align-items: center;
  flex-shrink: 0;
  padding: 8px 6px;
  margin-right: 10px;
  margin-top: 2px;
  border-radius: 6px;
  transition: all 0.2s;
  align-self: flex-start;
}
.mse-drag-handle:hover {
  color: var(--mse-primary);
  background: var(--mse-primary-soft);
}
.mse-drag-handle:active {
  cursor: grabbing;
  transform: scale(0.95);
}

/* 展示模式 */
.mse-spec-display-name {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 120px;
  cursor: pointer;
  padding: 6px 10px;
  border-radius: 8px;
  flex-shrink: 0;
  transition: all 0.2s;
}
.mse-spec-display-name:hover {
  background: var(--mse-primary-soft);
}
.mse-spec-name-text {
  font-weight: 600;
  font-size: 14px;
  color: var(--mse-text);
}

.mse-spec-count-badge {
  font-size: 11px;
  color: var(--mse-primary);
  background: var(--mse-primary-soft);
  padding: 3px 10px;
  border-radius: 12px;
  font-weight: 600;
  white-space: nowrap;
  flex-shrink: 0;
}

.mse-spec-tags {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  flex: 1;
  min-width: 0;
  padding: 4px 0;
}

/* 编辑模式 */
.mse-spec-edit-body {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 14px;
  min-width: 0;
}
.mse-spec-edit-name-row,
.mse-spec-edit-values-row {
  display: flex;
  align-items: center;
  gap: 12px;
}
.mse-field-label {
  font-size: 12px;
  color: var(--mse-text-sub);
  font-weight: 600;
  min-width: 60px;
  flex-shrink: 0;
  text-align: right;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}
.mse-spec-edit-tags {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  flex: 1;
}

/* 展示模式标签 */
.mse-tag {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  background: #fff;
  border: 1px solid var(--mse-border);
  border-radius: 8px;
  font-size: 12px;
  color: var(--mse-text);
  white-space: nowrap;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
  font-weight: 500;
}
.mse-tag:hover {
  border-color: var(--mse-primary);
  color: var(--mse-primary);
  background: var(--mse-primary-softer);
  transform: translateY(-1px);
  box-shadow: 0 2px 6px rgba(22, 119, 255, 0.1);
}
.mse-tag-empty {
  background: var(--mse-bg-soft);
  border-style: dashed;
  border-color: var(--mse-border);
  color: var(--mse-text-mute);
  font-weight: 400;
}
.mse-tag-empty:hover {
  border-color: var(--mse-border-strong);
  color: var(--mse-text-sub);
  background: var(--mse-bg-soft);
  transform: none;
  box-shadow: none;
}

/* 编辑模式标签 */
.mse-tag-edit {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  animation: mse-bounce-in 0.25s cubic-bezier(0.4, 0, 0.2, 1);
}
.mse-tag-input {
  width: 100px;
  padding: 7px 12px;
  font-size: 12px;
  text-align: center;
}
.mse-tag-remove {
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: #fff;
  border: 1px solid var(--mse-border);
  color: var(--mse-text-mute);
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  flex-shrink: 0;
  transition: all 0.2s;
}
.mse-tag-remove:hover {
  background: var(--mse-red-bg);
  border-color: var(--mse-red-border);
  color: var(--mse-red);
  transform: scale(1.1);
}
.mse-tag-add-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 7px 14px;
  background: #fff;
  border: 1px dashed var(--mse-border-strong);
  border-radius: var(--mse-radius-sm);
  font-size: 12px;
  color: var(--mse-text-sub);
  cursor: pointer;
  white-space: nowrap;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
  font-weight: 600;
}
.mse-tag-add-btn:hover {
  border-color: var(--mse-primary);
  color: var(--mse-primary);
  background: var(--mse-primary-soft);
  border-style: solid;
  transform: translateY(-1px);
}

/* 颜色点 */
.mse-color-dot {
  display: inline-block;
  width: 12px;
  height: 12px;
  border-radius: 50%;
  border: 2px solid rgba(255,255,255,0.8);
  box-shadow: 0 1px 3px rgba(0,0,0,0.15);
  flex-shrink: 0;
}

/* 右侧操作按钮 */
.mse-spec-actions {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-left: auto;
  flex-shrink: 0;
  padding-left: 12px;
}
.mse-action-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 7px 12px;
  background: transparent;
  border: none;
  border-radius: 8px;
  font-size: 12px;
  cursor: pointer;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
  white-space: nowrap;
  font-weight: 600;
}
.mse-action-add {
  color: var(--mse-primary);
}
.mse-action-add:hover {
  background: var(--mse-primary-soft);
}
.mse-action-edit {
  color: var(--mse-text-sub);
}
.mse-action-edit:hover {
  background: var(--mse-bg-soft);
  color: var(--mse-text);
}
.mse-action-done {
  color: #fff;
  background: linear-gradient(135deg, var(--mse-primary), #4096ff);
  padding: 7px 18px;
  font-weight: 600;
  box-shadow: 0 2px 6px rgba(22, 119, 255, 0.25);
}
.mse-action-done:hover {
  background: linear-gradient(135deg, var(--mse-primary-dark), var(--mse-primary));
  box-shadow: var(--mse-shadow-primary);
  transform: translateY(-1px);
}
.mse-action-delete {
  color: var(--mse-text-mute);
  padding: 7px 10px;
}
.mse-action-delete:hover {
  background: var(--mse-red-bg);
  color: var(--mse-red);
}

/* 添加规格类型 */
.mse-add-spec-area {
  display: flex;
  align-items: center;
  gap: 14px;
  margin: 4px 16px 16px;
  padding: 16px 18px;
  border: 2px dashed var(--mse-border);
  border-radius: var(--mse-radius-sm);
  background: var(--mse-bg-soft);
  transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
}
.mse-add-spec-area:hover {
  border-color: var(--mse-primary);
  background: var(--mse-primary-softer);
}
.mse-add-spec-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 8px 20px;
  background: #fff;
  border: 1px solid var(--mse-primary);
  border-radius: var(--mse-radius-sm);
  font-size: 13px;
  color: var(--mse-primary);
  cursor: pointer;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
  font-weight: 600;
  box-shadow: 0 1px 3px rgba(22, 119, 255, 0.1);
}
.mse-add-spec-btn:hover {
  background: linear-gradient(135deg, var(--mse-primary), #4096ff);
  color: #fff;
  box-shadow: var(--mse-shadow-primary);
  transform: translateY(-1px);
}
.mse-add-spec-hint {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--mse-text-mute);
  font-weight: 500;
}
.mse-add-spec-max {
  color: var(--mse-green);
}

/* ============ 输入框通用 ============ */
.mse-input {
  padding: 8px 12px;
  border: 1px solid var(--mse-border);
  border-radius: var(--mse-radius-sm);
  font-size: 13px;
  outline: none;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
  font-family: inherit;
  background: #fff;
  color: var(--mse-text);
  font-weight: 500;
}
.mse-input:hover {
  border-color: var(--mse-border-strong);
}
.mse-input:focus {
  border-color: var(--mse-primary);
  box-shadow: 0 0 0 3px rgba(22, 119, 255, 0.12);
}
.mse-input::placeholder {
  color: var(--mse-text-mute);
  font-weight: 400;
}
.mse-input-error {
  border-color: var(--mse-red-border) !important;
  background: #fffafa !important;
}
.mse-input-error:focus {
  border-color: var(--mse-red) !important;
  box-shadow: 0 0 0 3px rgba(255, 91, 97, 0.12) !important;
}
.mse-spec-name-input {
  width: 160px;
  font-weight: 600;
  padding: 8px 12px;
  font-size: 13px;
}

/* ============ SKU 表格区 ============ */
.mse-sku-empty {
  margin: 20px;
  padding: 48px 24px;
  text-align: center;
  background: linear-gradient(180deg, var(--mse-bg-soft) 0%, #fff 100%);
  border: 2px dashed var(--mse-border);
  border-radius: var(--mse-radius);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
}
.mse-empty-icon {
  color: var(--mse-text-mute);
  margin-bottom: 8px;
  opacity: 0.4;
  animation: mse-pulse-soft 3s ease-in-out infinite;
}
.mse-empty-text {
  font-size: 14px;
  color: var(--mse-text-sub);
  font-weight: 600;
}
.mse-empty-sub {
  font-size: 12px;
  color: var(--mse-text-mute);
  font-weight: 500;
}

/* 批量设置 */
.mse-batch {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px 20px;
  margin-bottom: 0;
  flex-wrap: wrap;
  background: var(--mse-bg-soft);
  border-bottom: 1px solid var(--mse-border-light);
}
.mse-batch-label {
  font-size: 12px;
  color: var(--mse-text-sub);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}
.mse-btn-outline {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 7px 14px;
  background: #fff;
  border: 1px solid var(--mse-border);
  border-radius: var(--mse-radius-sm);
  font-size: 12px;
  color: var(--mse-text-sub);
  cursor: pointer;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
  font-weight: 600;
  box-shadow: 0 1px 2px rgba(16, 24, 40, 0.04);
}
.mse-btn-outline:hover {
  border-color: var(--mse-primary);
  color: var(--mse-primary);
  background: var(--mse-primary-softer);
  transform: translateY(-1px);
  box-shadow: 0 2px 6px rgba(22, 119, 255, 0.1);
}
.mse-btn-outline.active {
  border-color: var(--mse-primary);
  color: var(--mse-primary);
  background: var(--mse-primary-soft);
  box-shadow: inset 0 1px 3px rgba(22, 119, 255, 0.1);
}
.mse-batch-inline {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  padding: 10px 16px;
  background: #fff;
  border: 1px solid var(--mse-primary-soft);
  border-radius: var(--mse-radius-sm);
  margin-left: 6px;
  animation: mseBatchFadeIn 0.25s cubic-bezier(0.4, 0, 0.2, 1);
  box-shadow: var(--mse-shadow-sm);
}
@keyframes mseBatchFadeIn {
  from { opacity: 0; transform: translateY(-6px) scale(0.98); }
  to { opacity: 1; transform: translateY(0) scale(1); }
}
.mse-batch-inline-label {
  font-size: 12px;
  color: var(--mse-text-sub);
  font-weight: 600;
  white-space: nowrap;
}
.mse-batch-input {
  width: 120px;
  padding: 7px 12px;
  font-size: 13px;
}
.mse-batch-unit {
  font-size: 12px;
  color: var(--mse-text-mute);
  font-weight: 500;
}
.mse-btn-primary {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 7px 16px;
  background: linear-gradient(135deg, var(--mse-primary), #4096ff);
  border: 1px solid var(--mse-primary);
  border-radius: var(--mse-radius-sm);
  font-size: 12px;
  color: #fff;
  cursor: pointer;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
  font-weight: 600;
  box-shadow: 0 2px 6px rgba(22, 119, 255, 0.2);
}
.mse-btn-primary:hover {
  background: linear-gradient(135deg, var(--mse-primary-dark), var(--mse-primary));
  box-shadow: var(--mse-shadow-primary);
  transform: translateY(-1px);
}
.mse-btn-ghost {
  padding: 7px 10px;
  background: transparent;
  border: none;
  font-size: 12px;
  color: var(--mse-text-mute);
  cursor: pointer;
  transition: all 0.2s;
  font-weight: 500;
  border-radius: 6px;
}
.mse-btn-ghost:hover {
  color: var(--mse-text-sub);
  background: var(--mse-bg);
}

/* 表格容器 */
.mse-table-wrap {
  border: none;
  border-radius: 0;
  overflow: hidden;
  box-shadow: none;
}
.mse-table-scroll {
  overflow-x: auto;
}
.mse-table-scroll::-webkit-scrollbar {
  width: 8px;
  height: 8px;
}
.mse-table-scroll::-webkit-scrollbar-track {
  background: var(--mse-bg-soft);
}
.mse-table-scroll::-webkit-scrollbar-thumb {
  background: #d0d9e8;
  border-radius: 4px;
  border: 2px solid var(--mse-bg-soft);
}
.mse-table-scroll::-webkit-scrollbar-thumb:hover {
  background: #b0bdd0;
}

.mse-table {
  width: 100%;
  border-collapse: separate;
  border-spacing: 0;
  font-size: 13px;
}
.mse-table th,
.mse-table td {
  padding: 14px 16px;
  text-align: left;
  vertical-align: middle;
}
.mse-table thead th {
  background: linear-gradient(180deg, var(--mse-bg-soft) 0%, var(--mse-bg) 100%);
  font-weight: 600;
  color: var(--mse-text-sub);
  font-size: 12px;
  white-space: nowrap;
  border-bottom: 1px solid var(--mse-border);
  position: sticky;
  top: 0;
  z-index: 1;
  text-transform: uppercase;
  letter-spacing: 0.3px;
}
.mse-table tbody tr {
  transition: all 0.15s ease;
}
.mse-table tbody td {
  border-bottom: 1px solid var(--mse-border-light);
  background: #fff;
}
.mse-table tbody tr:last-child td {
  border-bottom: none;
}
.mse-table tbody tr:hover td {
  background: var(--mse-primary-softer);
}
.mse-table tbody tr.mse-tr-filled td {
  background: linear-gradient(90deg, #fafffe 0%, #f0fdf950 100%);
}
.mse-table tbody tr.mse-tr-filled:hover td {
  background: #f0fdf9;
}

.mse-th-img,
.mse-td-img {
  width: 80px;
  text-align: center;
}
.mse-th-spec {
  min-width: 100px;
}
.mse-th-price,
.mse-td-price {
  width: 170px;
}
.mse-th-stock,
.mse-td-stock {
  width: 150px;
}

/* 商品主图 */
.mse-sku-img {
  position: relative;
  width: 52px;
  height: 52px;
  border-radius: var(--mse-radius-sm);
  border: 2px solid var(--mse-border);
  background: var(--mse-bg);
  overflow: hidden;
  cursor: pointer;
  margin: 0 auto;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
}
.mse-sku-img:hover {
  border-color: var(--mse-primary);
  box-shadow: 0 0 0 3px rgba(22, 119, 255, 0.12), var(--mse-shadow-sm);
  transform: scale(1.05);
}
.mse-sku-img img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.mse-sku-img-empty {
  border-style: dashed;
  border-color: var(--mse-border-strong);
  color: var(--mse-text-mute);
  flex-direction: column;
  gap: 3px;
}
.mse-sku-img-empty:hover {
  border-color: var(--mse-primary);
  color: var(--mse-primary);
  background: var(--mse-primary-soft);
}
.mse-sku-img-mask {
  position: absolute;
  inset: 0;
  background: linear-gradient(135deg, rgba(22, 119, 255, 0.9), rgba(64, 150, 255, 0.9));
  color: #fff;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 3px;
  font-size: 10px;
  font-weight: 600;
  opacity: 0;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
  backdrop-filter: blur(2px);
}
.mse-sku-img:hover .mse-sku-img-mask {
  opacity: 1;
}
.mse-sku-img-upload-text {
  font-size: 10px;
  color: inherit;
  margin-top: 2px;
  font-weight: 500;
}

/* 规格列文字 */
.mse-spec-text {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--mse-text);
  font-weight: 500;
}

/* 输入框前后缀 */
.mse-input-wrap {
  display: flex;
  align-items: center;
  background: #fff;
  border: 1px solid var(--mse-border);
  border-radius: var(--mse-radius-sm);
  overflow: hidden;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
  box-shadow: 0 1px 2px rgba(16, 24, 40, 0.03);
}
.mse-input-wrap:hover {
  border-color: var(--mse-border-strong);
}
.mse-input-wrap:focus-within {
  border-color: var(--mse-primary);
  box-shadow: 0 0 0 3px rgba(22, 119, 255, 0.12), 0 1px 3px rgba(22, 119, 255, 0.1);
}
.mse-input-wrap:has(.mse-input-error) {
  border-color: var(--mse-red-border);
  background: #fffafa;
}
.mse-input-wrap:has(.mse-input-error):focus-within {
  border-color: var(--mse-red);
  box-shadow: 0 0 0 3px rgba(255, 91, 97, 0.12);
}
.mse-input-prefix,
.mse-input-suffix {
  font-size: 13px;
  color: var(--mse-text-sub);
  padding: 0 12px;
  flex-shrink: 0;
  font-weight: 600;
  user-select: none;
}
.mse-input-prefix {
  color: var(--mse-text);
}

/* SKU 输入框（在 wrap 内） */
.mse-sku-input {
  width: 100%;
  min-width: 0;
  padding: 9px 8px;
  font-size: 13px;
  border: none !important;
  border-radius: 0 !important;
  box-shadow: none !important;
  background: transparent;
  text-align: left;
  font-weight: 600;
}
.mse-sku-input:focus {
  box-shadow: none !important;
}
.mse-price-input {
  padding-left: 0;
}
.mse-stock-input {
  padding-right: 0;
}

/* ============ 响应式 ============ */
@media (max-width: 768px) {
  .mse-root {
    margin-top: 12px;
  }
  .mse-stats-bar {
    gap: 8px;
  }
  .mse-stat-chip {
    padding: 10px 14px;
    min-width: 100px;
  }
  .mse-stat-chip-val {
    font-size: 18px;
  }
  .mse-section-head {
    padding: 14px 16px;
  }
  .mse-spec-list {
    padding: 8px 12px;
  }
  .mse-spec-row {
    flex-wrap: wrap;
    gap: 10px;
    padding: 12px;
  }
  .mse-spec-actions {
    width: 100%;
    justify-content: flex-end;
    padding-left: 0;
    border-top: 1px solid var(--mse-border-light);
    padding-top: 10px;
    margin-top: 6px;
  }
  .mse-spec-display-name {
    min-width: auto;
  }
  .mse-spec-edit-name-row,
  .mse-spec-edit-values-row {
    flex-wrap: wrap;
    gap: 8px;
  }
  .mse-field-label {
    min-width: auto;
    text-align: left;
  }
  .mse-add-spec-area {
    margin: 4px 12px 12px;
    padding: 12px 14px;
    flex-direction: column;
    align-items: flex-start;
    gap: 8px;
  }
  .mse-batch {
    flex-direction: column;
    align-items: stretch;
    padding: 12px 16px;
  }
  .mse-batch-inline {
    margin-left: 0;
    flex-wrap: wrap;
  }
  .mse-batch-input {
    flex: 1;
    min-width: 80px;
  }
  .mse-table th,
  .mse-table td {
    padding: 10px 12px;
  }
  .mse-sku-img {
    width: 44px;
    height: 44px;
  }
  .mse-th-price,
  .mse-td-price {
    width: 140px;
  }
  .mse-th-stock,
  .mse-td-stock {
    width: 120px;
  }
}
</style>
