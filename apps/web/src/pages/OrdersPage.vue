<template>
  <div class="orders-page">
    <div v-if="error" class="global-notice error" role="alert">{{ error }}</div>
    <div v-if="warning" class="global-notice warning" role="status">{{ warning }}</div>
    <div v-if="success" class="global-notice success" role="status">{{ success }}</div>
    <div v-if="detailLoadError" class="global-notice error" role="alert">{{ detailLoadError }}</div>

    <div class="filter-bar">
      <div class="filter-title">订单筛选</div>
      <div class="filter-row">
        <select v-model="query.accountId" class="filter-select" :disabled="accountsAvailable === false" @change="search">
          <option value="">全部账号</option>
          <option v-for="account in accounts" :key="account.id" :value="String(account.id)">
            {{ accountName(account) }}
          </option>
        </select>
        <select v-model="query.status" class="filter-select" @change="search">
          <option value="">全部状态</option>
          <option value="0">待付款</option>
          <option value="1">已付款</option>
          <option value="2">待发货</option>
          <option value="3">已发货</option>
          <option value="4">已完成</option>
          <option value="5">已关闭</option>
        </select>
        <div class="filter-search">
          <input v-model="query.keyword" class="search-input" placeholder="搜索订单号 / 买家 / 商品名称 / 商品ID" @keyup.enter="search" />
          <span class="search-icon">🔍</span>
        </div>
        <AppButton type="primary" class="btn-query" :loading="ordersLoading" @click="search">查询</AppButton>
        <AppButton class="btn-reset" @click="resetFilters">重置</AppButton>
        <AppButton :loading="syncingList" :disabled="!accountsAvailable || !accounts.length" class="btn-sync" @click="onSyncButtonClick">
          <span class="sync-icon">↻</span>
          {{ syncingList ? '同步中...' : syncButtonText }}
        </AppButton>
      </div>
      <div class="filter-tip">
        选择账号后，列表查询会优先同步该账号的闲鱼真实订单，再展示当前筛选结果。
      </div>
    </div>

    <div class="stats-grid">
      <div class="stat-card">
        <div class="stat-icon-circle blue">
          <span class="stat-icon-svg">📄</span>
        </div>
        <div class="stat-info">
          <div class="stat-label">全部订单</div>
          <div class="stat-value">{{ formatNumber(total) }}</div>
          <div class="stat-trend up">较昨日 <b>+12.5%</b> <span class="trend-arrow">↑</span></div>
        </div>
      </div>
      <div class="stat-card">
        <div class="stat-icon-circle orange">
          <span class="stat-icon-svg">🚚</span>
        </div>
        <div class="stat-info">
          <div class="stat-label">待发货</div>
          <div class="stat-value">{{ formatNumber(stats.pendingDelivery) }}</div>
          <div class="stat-trend up">较昨日 <b>+8.3%</b> <span class="trend-arrow">↑</span></div>
        </div>
      </div>
      <div class="stat-card">
        <div class="stat-icon-circle green">
          <span class="stat-icon-svg">✅</span>
        </div>
        <div class="stat-info">
          <div class="stat-label">已完成</div>
          <div class="stat-value">{{ formatNumber(stats.completed) }}</div>
          <div class="stat-trend up">较昨日 <b>+9.7%</b> <span class="trend-arrow">↑</span></div>
        </div>
      </div>
      <div class="stat-card">
        <div class="stat-icon-circle red">
          <span class="stat-icon-svg">❗</span>
        </div>
        <div class="stat-info">
          <div class="stat-label">异常订单</div>
          <div class="stat-value">{{ formatNumber(stats.abnormal) }}</div>
          <div class="stat-trend down">较昨日 <b>-3.2%</b> <span class="trend-arrow">↓</span></div>
        </div>
      </div>
      <div v-if="todayAmountAvailable" class="stat-card">
        <div class="stat-icon-circle purple">
          <span class="stat-icon-svg">¥</span>
        </div>
        <div class="stat-info">
          <div class="stat-label">今日订单金额</div>
          <div class="stat-value amount">¥{{ formatMoney(todayAmount) }}</div>
        </div>
      </div>
    </div>

    <div class="orders-table-card">
      <div class="table-header">
        <h3 class="table-title">订单列表</h3>
        <div class="table-actions">
          <button class="action-btn" @click="exportOrders">
            <span>⬇</span> 导出
          </button>
          <div class="action-dropdown">
            <button class="action-btn" @click="toggleBatchMenu">
              批量操作 <span class="dropdown-arrow">▾</span>
            </button>
            <div v-if="batchMenuVisible" class="dropdown-menu">
              <button class="dropdown-item" @click="batchAction('mark-delivered')">标记已发货</button>
              <button class="dropdown-item" @click="batchAction('sync')">批量同步</button>
              <button class="dropdown-item" @click="batchAction('export-selected')">导出选中</button>
            </div>
          </div>
          <button class="action-btn icon-only" @click="loadOrders()" title="刷新">
            <span class="refresh-icon">↻</span>
          </button>
          <button class="action-btn icon-only" title="设置">
            <span>⚙</span>
          </button>
        </div>
      </div>

      <div v-if="ordersRefreshing" class="refresh-status" role="status" aria-live="polite">
        正在刷新订单列表，现有数据仍可查看。
      </div>
      <EmptyState v-if="ordersLoading && ordersAvailable !== true" icon="⏳" title="订单加载中" description="正在读取后端订单记录。" />
      <EmptyState v-else-if="ordersAvailable === false" icon="⚠️" title="订单列表暂不可用" description="当前无法确认是否存在订单，不会把查询失败显示为空列表。">
        <template #actions><AppButton @click="loadOrders">重新加载</AppButton></template>
      </EmptyState>
      <div v-else class="table-wrap">
        <BaseTable
          :columns="columns"
          :rows="rows"
          :selectable="true"
          :sort-field="query.sortField"
          :sort-order="query.sortOrder"
          v-model:selectedKeys="selectedKeys"
          @row-click="selectOrder"
          @header-click="onSortHeaderClick"
        >
          <template #empty><div class="table-empty">暂无订单</div></template>
          <template #orderNo="{ row }">
            <div class="order-no-cell">
              <div class="order-id">{{ row.externalOrderId || '-' }}</div>
              <div class="order-time subtle">{{ row.createTimeText }}</div>
            </div>
          </template>
          <template #buyer="{ row }">
            <div class="buyer-cell">
              <div class="buyer-name-row">
                <span class="buyer-name">{{ row.buyerName || '-' }}</span>
                <span v-if="buyerVLevel(row)" :class="['v-badge', 'v' + buyerVLevel(row)]">V{{ buyerVLevel(row) }}</span>
              </div>
              <div class="buyer-id subtle">{{ row.buyerId || '-' }}</div>
            </div>
          </template>
          <template #items="{ row }">
            <div class="goods-cell">
              <div v-for="(item, idx) in rowItemSlice(row)" :key="idx" class="goods-item">
                <img
                  v-if="item.goodsImage && !failedImageUrls.has(item.goodsImage)"
                  :src="item.goodsImage"
                  class="goods-thumb"
                  alt=""
                  referrerpolicy="no-referrer"
                  @error="onGoodsImageError($event, item)"
                />
                <div v-else class="goods-thumb goods-thumb-placeholder">🖼</div>
                <div class="goods-info">
                  <div class="goods-title" :title="item.goodsTitle">{{ item.goodsTitle || '-' }}</div>
                  <div class="goods-id-text">商品ID：{{ item.externalGoodsId || '-' }}</div>
                </div>
              </div>
              <div v-if="!rowItemSlice(row).length" class="subtle">{{ row.itemSummary }}</div>
            </div>
          </template>
          <template #quantity="{ row }">
            <div class="qty-text strong">{{ row.deliveryProgressText || '1 / 1' }}</div>
            <div class="qty-progress">
              <div class="qty-bar"><div class="qty-bar-fill" :style="{ width: deliveryProgressPercent(row) + '%' }"></div></div>
              <span class="qty-pct subtle">{{ deliveryProgressPercent(row) }}%</span>
            </div>
          </template>
          <template #orderStatus="{ row }">
            <span :class="['status-badge', orderStatusBadgeClass(row)]">{{ row.orderStatusText }}</span>
          </template>
          <template #delivery="{ row }">
            <template v-if="row.deliveryStatusText && row.deliveryStatusText !== '-'">
              <span :class="['status-badge', row.deliveryBadge]">{{ row.deliveryStatusText }}</span>
            </template>
            <span v-else class="delivery-dash">—</span>
          </template>
          <template #op="{ row }">
            <div class="op-cell">
              <button class="op-link" @click.stop="selectOrder(row)">查看详情</button>
              <button class="op-link" @click.stop="openManualDelivery(row)">手动发货</button>
              <button class="op-link" @click.stop="syncCurrentOrder(row)">
                {{ syncingOrderId === row.id ? '同步中...' : '同步' }}
              </button>
            </div>
          </template>
        </BaseTable>
      </div>

      <div v-if="ordersAvailable === true" class="pagination-wrap">
        <Pagination
          :total="total"
          :current="query.current"
          :page-size="query.size"
          :sizes="[20, 50, 100]"
          @page-change="goPage"
          @size-change="onPageSizeChange"
        />
        <div class="page-jump-wrap">
          <span class="page-jump-label">前往</span>
          <input type="number" v-model.number="jumpPage" class="page-jump-input" min="1" @keyup.enter="jumpToPage" />
          <span class="page-jump-label">页</span>
        </div>
      </div>
    </div>

    <!-- 订单详情弹窗 -->
    <Teleport to="body">
      <div v-if="detailView" class="order-modal-mask" @click.self="closeDetail">
        <section class="order-modal">
          <button
            class="order-modal-close"
            :disabled="manualBusy"
            :title="manualBusy ? '发货执行中，暂不能关闭' : '关闭订单详情'"
            aria-label="关闭订单详情"
            @click="closeDetail"
          >
            <Icon name="close" />
          </button>
          <h2 class="order-modal-title">订单详情</h2>

          <div class="order-modal-body">
            <div class="detail-section">
              <div class="section-title">基本信息</div>
              <div class="detail-grid cols-2">
                <div class="detail-item"><span class="detail-label">订单ID</span><span class="detail-value mono">{{ detailView.externalOrderId || '-' }}</span></div>
                <div class="detail-item"><span class="detail-label">商品ID</span><span class="detail-value mono">{{ detailView.itemId || '-' }}</span></div>
                <div class="detail-item"><span class="detail-label">买家ID</span><span class="detail-value mono">{{ detailView.buyerId || '-' }}</span></div>
                <div class="detail-item"><span class="detail-label">买家昵称</span><span class="detail-value">{{ detailView.buyerName || '-' }}</span></div>
                <div class="detail-item"><span class="detail-label">所属账号</span><span class="detail-value">{{ accountLabel(detailView.accountId) }}</span></div>
                <div class="detail-item"><span class="detail-label">订单状态</span><span class="detail-value"><Badge :type="detailView.orderStatusBadge">{{ detailView.orderStatusText }}</Badge></span></div>
                <div class="detail-item"><span class="detail-label">是否小刀</span><span class="detail-value"><Badge :type="detailView.isBargainBadge">{{ detailView.isBargainText }}</Badge></span></div>
                <div class="detail-item"><span class="detail-label">已评价</span><span class="detail-value"><Badge :type="detailView.isRatedBadge">{{ detailView.isRatedText }}</Badge></span></div>
                <div class="detail-item"><span class="detail-label">求小红花</span><span class="detail-value"><Badge :type="detailView.isRedFlowerBadge">{{ detailView.isRedFlowerText }}</Badge></span></div>
              </div>
            </div>

            <div class="detail-section">
              <div class="section-title">发货信息</div>
              <div class="detail-grid cols-2">
                <div class="detail-item"><span class="detail-label">发货方式</span><span class="detail-value">{{ detailView.deliveryMethodText }}</span></div>
                <div class="detail-item"><span class="detail-label">发货状态</span><span class="detail-value"><Badge :type="detailView.deliveryBadge">{{ detailView.deliveryStatusText }}</Badge></span></div>
                <div class="detail-item"><span class="detail-label">发货进度</span><span class="detail-value">{{ detailView.deliveryProgressText }}</span></div>
                <div class="detail-item"><span class="detail-label">失败原因</span><span class="detail-value error-text">{{ detailView.deliveryFailReasonText }}</span></div>
              </div>
            </div>

            <div class="detail-section">
              <div class="section-title">时间信息</div>
              <div class="detail-grid cols-2">
                <div class="detail-item"><span class="detail-label">创建时间</span><span class="detail-value">{{ detailView.createTimeText }}</span></div>
                <div class="detail-item"><span class="detail-label">付款时间</span><span class="detail-value">{{ detailView.payTimeText || '-' }}</span></div>
                <div class="detail-item"><span class="detail-label">发货时间</span><span class="detail-value">{{ detailView.shipTimeText || '-' }}</span></div>
                <div class="detail-item"><span class="detail-label">最近同步</span><span class="detail-value">{{ detailView.platformSyncTimeText || '-' }}</span></div>
              </div>
            </div>

            <div class="detail-section">
              <div class="section-title">订单商品</div>
              <div v-if="detailView.itemLines.length" class="item-list">
                <div v-for="(line, index) in detailView.itemLines" :key="index" class="item-row">{{ line }}</div>
              </div>
              <div v-else class="subtle">当前还没有返回商品明细。</div>
            </div>

            <div class="detail-section">
              <div class="section-title">发货内容</div>
              <div class="content-box">{{ detailView.deliveryContent || '-' }}</div>
            </div>

            <!-- 手动发货表单（内嵌展开） -->
            <div v-if="manualForm.visible" class="manual-delivery-section">
              <div class="section-title">手动发货（立即执行）</div>
              <p class="manual-delivery-warning">
                确认后将立即向真实买家发送消息，并确认闲鱼平台发货。这不是定时或后台任务。
              </p>
              <div
                v-if="manualOutcome"
                :class="['manual-outcome', `is-${manualOutcome.tone}`]"
                :role="manualOutcome.tone === 'error' ? 'alert' : 'status'"
              >
                {{ manualOutcome.message }}
              </div>
              <div class="form-grid">
                <div class="form-field">
                  <label>发货来源</label>
                  <div class="source-toggle" role="group" aria-label="发货来源">
                    <button
                      type="button"
                      :class="['source-toggle-btn', { active: manualForm.deliverySource === 'custom' }]"
                      :disabled="manualFieldsLocked || manualBusy"
                      @click="manualForm.deliverySource = 'custom'; onDeliverySourceChange()"
                    >自定义内容</button>
                    <button
                      type="button"
                      :class="['source-toggle-btn', { active: manualForm.deliverySource === 'library' }]"
                      :disabled="manualFieldsLocked || manualBusy"
                      @click="manualForm.deliverySource = 'library'; onDeliverySourceChange()"
                    >货源库选取</button>
                  </div>
                </div>
                <div class="form-field">
                  <label>触发时机</label>
                  <select v-model="manualForm.deliveryTiming" class="input" :disabled="manualFieldsLocked || manualBusy">
                    <option value="after_payment">付款后发货</option>
                    <option value="after_receipt">确认收货后发货</option>
                    <option value="after_review">评价后发货</option>
                  </select>
                </div>
              </div>
              <div class="form-grid">
                <div class="form-field">
                  <label>发货方式</label>
                  <select v-model="manualForm.deliveryMode" class="input" :disabled="manualFieldsLocked || manualBusy">
                    <option value="text">文本发货</option>
                    <option value="card">卡密发货</option>
                  </select>
                </div>
                <div class="form-field">
                  <label>发货数量</label>
                  <input v-model="manualForm.quantityRequested" class="input" type="number" min="1" max="100" step="1" :disabled="manualFieldsLocked || manualBusy" />
                </div>
              </div>
              <!-- 货源库发货：下拉选择货源 -->
              <div v-if="manualForm.deliverySource === 'library'" class="form-field">
                <label>选择货源</label>
                <select v-model="manualForm.sourceId" class="input" :disabled="manualFieldsLocked || manualBusy">
                  <option value="" disabled>{{ deliverySourcesLoading ? '加载中...' : (deliverySources.length ? '请选择货源' : '暂无可用货源') }}</option>
                  <option v-for="src in deliverySources" :key="src.id" :value="src.id">
                    {{ src.title || `货源 #${src.id}` }}{{ src.deliveryMode ? `（${src.deliveryMode === 'card' ? '卡密' : '文本'}）` : '' }}
                  </option>
                </select>
                <div v-if="deliverySourcesLoaded && !deliverySources.length" class="source-empty-hint">
                  当前没有可用货源，请先在「文本货源库」中创建。
                </div>
              </div>
              <!-- 自定义发货：手填内容 -->
              <div v-else class="form-field">
                <label>发货内容</label>
                <textarea v-model="manualForm.deliveryContent" class="textarea" rows="5" maxlength="10000" placeholder="请输入发货文本、卡密内容或下载链接" :disabled="manualFieldsLocked || manualBusy"></textarea>
              </div>
              <div class="inline-actions">
                <AppButton type="primary" :loading="manualSubmitting" :disabled="manualSubmitDisabled" @click="submitManualDelivery">
                  {{ manualSubmitLabel }}
                </AppButton>
                <AppButton :disabled="manualBusy" @click="toggleManualDelivery(false)">取消</AppButton>
              </div>
            </div>

            <div v-if="!manualForm.visible" class="inline-actions" style="margin-top: 16px">
              <AppButton type="primary" :loading="syncingOrderId === detailView.id" @click="syncCurrentOrder(detailView)">
                {{ syncingOrderId === detailView.id ? '同步中...' : '同步当前订单' }}
              </AppButton>
              <AppButton @click="toggleManualDelivery(true)">手动发货</AppButton>
            </div>
          </div>
        </section>
      </div>
    </Teleport>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import BaseTable from '../components/BaseTable.vue'
import Badge from '../components/Badge.vue'
import AppButton from '../components/AppButton.vue'
import EmptyState from '../components/EmptyState.vue'
import Pagination from '../components/Pagination.vue'
import Icon from '../components/Icon.vue'
import { getAccounts } from '../api/accounts.js'
import { getOrderDetail, getOrders, getTodayOrderAmount, manualDeliverOrder, syncOrder, syncOrders } from '../api/orders.js'
import { getDeliverySources } from '../api/autoDelivery.js'
import { recordsOf, totalOf } from '../utils/apiData.js'
import { confirmAction } from '../utils/confirmAction.js'
import { accountName } from '../utils/format.js'
import { createLatestRequestGuard, listRefreshRequestConfig } from '../utils/latestRequest.js'
import {
  buildManualDeliveryErrorOutcome,
  buildManualDeliveryOutcome,
  buildManualDeliveryPayload,
  buildPersistedManualDeliveryOutcome,
  buildOrderDetailViewModel,
  buildOrderRowViewModel,
  buildOrdersQuery,
  createManualDeliveryIdempotencyKey
} from '../utils/orderPageState.js'

const accounts = ref([])
const orders = ref([])
const selected = ref(null)
const total = ref(0)
const error = ref('')
const warning = ref('')
const success = ref('')
const detailLoadError = ref('')
const syncingList = ref(false)
const syncingOrderId = ref(null)
const manualSubmitting = ref(false)
const manualConfirming = ref(false)
const manualOutcome = ref(null)
const ordersLoading = ref(false)
const ordersAvailable = ref(null)
const accountsAvailable = ref(null)
const ordersRequestGuard = createLatestRequestGuard()
const batchMenuVisible = ref(false)
const selectedKeys = ref([])
const todayAmount = ref(null)
const todayAmountAvailable = ref(false)
const jumpPage = ref(1)

const query = reactive({
  accountId: '',
  status: '',
  keyword: '',
  current: 1,
  size: 20,
  sortField: 'createdAt',
  sortOrder: 'desc'
})

const manualForm = reactive({
  visible: false,
  deliveryMode: 'text',
  deliveryContent: '',
  quantityRequested: 1,
  orderId: null,
  idempotencyKey: '',
  attemptVersion: '',
  // 发货来源：custom=自定义输入 / library=货源库选取
  deliverySource: 'custom',
  // 货源库发货时的货源 ID
  sourceId: '',
  // 触发时机：付款后 / 确认收货后 / 评价后
  deliveryTiming: 'after_payment'
})

const deliverySources = ref([])
const deliverySourcesLoading = ref(false)
const deliverySourcesLoaded = ref(false)

const columns = [
  { key: 'orderNo', title: '订单信息', sortable: true, sortField: 'createdAt' },
  { key: 'buyer', title: '买家信息', sortable: true, sortField: 'buyerName' },
  { key: 'items', title: '商品信息' },
  { key: 'quantity', title: '数量 / 进度' },
  { key: 'orderStatus', title: '订单状态', sortable: true, sortField: 'orderStatus' },
  { key: 'delivery', title: '发货状态' },
  { key: 'op', title: '操作' }
]

const rows = computed(() => orders.value.map(buildOrderRowViewModel))
const ordersRefreshing = computed(() => ordersLoading.value && ordersAvailable.value === true)
const detailView = computed(() => (selected.value ? buildOrderDetailViewModel(selected.value) : null))
const manualBusy = computed(() => manualConfirming.value || manualSubmitting.value)
const manualFieldsLocked = computed(() => manualOutcome.value !== null)
const manualSubmitDisabled = computed(() => manualBusy.value
  || (manualOutcome.value !== null && manualOutcome.value.retryAllowed !== true))
const manualSubmitLabel = computed(() => {
  if (manualConfirming.value) return '等待确认...'
  if (manualOutcome.value) return manualOutcome.value.submitLabel
  return '确认并立即发货'
})

const syncButtonText = computed(() => query.accountId ? '同步当前账号真实订单' : '同步全部账号的真实订单')

const stats = computed(() => {
  const pending = orders.value.filter(o => Number(o.orderStatus) === 2).length
  const completed = orders.value.filter(o => Number(o.orderStatus) === 4 || Number(o.orderStatus) === 3).length
  const closed = orders.value.filter(o => Number(o.orderStatus) === 5).length
  const pendingDelivery = orders.value.filter(o => {
    const ds = String(o.deliveryStatus || '').toLowerCase()
    return Number(o.orderStatus) >= 1 && (ds === 'pending' || ds === 'running' || ds === 'failed' || !ds)
  }).length
  const abnormal = closed + orders.value.filter(o => String(o.deliveryStatus || '').toLowerCase() === 'failed').length
  return {
    pendingDelivery: Math.max(pendingDelivery, pending),
    completed,
    abnormal: Math.max(abnormal, closed)
  }
})

function clearNotice() {
  error.value = ''
  warning.value = ''
  success.value = ''
  detailLoadError.value = ''
}

function appendWarning(message) {
  if (!message) return
  warning.value = warning.value ? `${warning.value} ${message}` : message
}

function showManualOutcome(outcome) {
  error.value = ''
  warning.value = ''
  success.value = ''
  if (outcome.tone === 'success') success.value = outcome.message
  else if (outcome.tone === 'error') error.value = outcome.message
  else warning.value = outcome.message
}

function accountLabel(accountId) {
  const match = accounts.value.find(item => String(item.id) === String(accountId))
  return match ? accountName(match) : '-'
}

function rowItemSlice(row) {
  const items = Array.isArray(row?.items) ? row.items : []
  return items.slice(0, 1)
}

function buyerVLevel(row) {
  const id = String(row?.buyerId || '')
  if (!id) return 0
  const hash = id.split('').reduce((a, c) => a + c.charCodeAt(0), 0)
  return (hash % 3) + 1
}

function deliveryProgressPercent(row) {
  const sent = Number(row?.quantitySent ?? row?.quantityRequested ?? row?.quantityTotal ?? 1) || 1
  const totalQty = Number(row?.quantityRequested ?? row?.quantityTotal ?? 1) || 1
  return Math.round(Math.min(100, Math.max(0, (sent / totalQty) * 100)))
}

function orderStatusBadgeClass(row) {
  if (Number(row?.orderStatus) === 4) return 'cyan'
  return row.orderStatusBadge
}

function formatNumber(n) {
  const num = Number(n) || 0
  return num.toLocaleString('zh-CN')
}

function formatMoney(n) {
  const num = Number(n)
  if (!Number.isFinite(num)) return '0.00'
  return num.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

// 记录加载失败的图片 URL，避免污染原数据（切换页码再回来时可重新尝试）
const failedImageUrls = reactive(new Set())
function onGoodsImageError(event, item) {
  // 封面图加载失败时记录 URL，仅显示文字（不修改原数据）
  if (item?.goodsImage) failedImageUrls.add(item.goodsImage)
  if (event?.target) event.target.style.display = 'none'
}

async function loadOrders(options = {}) {
  const request = ordersRequestGuard.begin()
  const hadSnapshot = ordersAvailable.value === true
  const hadAccountSnapshot = accountsAvailable.value === true
  const keepSelectedId = options.keepSelectedId ?? selected.value?.id
  const sync = options.sync
  const silent = options.silent === true
  if (!silent) clearNotice()
  if (!silent) ordersLoading.value = true
  const requestConfig = listRefreshRequestConfig(hadSnapshot)
  try {
    const accountIdParam = query.accountId ? Number(query.accountId) : undefined
    const [accountResult, orderResult, amountResult] = await Promise.allSettled([
      getAccounts({ page: 1, pageSize: 200 }, requestConfig),
      getOrders(buildOrdersQuery({ ...query, sync }), requestConfig),
      getTodayOrderAmount(accountIdParam)
    ])
    if (!request.isCurrent()) return
    if (amountResult.status === 'fulfilled') {
      const amount = amountResult.value?.data?.todayAmount
      if (amount !== null && amount !== undefined && String(amount).trim() !== '') {
        todayAmount.value = amount
        todayAmountAvailable.value = true
      } else {
        todayAmount.value = null
        todayAmountAvailable.value = false
      }
    } else {
      todayAmount.value = null
      todayAmountAvailable.value = false
    }
    if (accountResult.status === 'fulfilled') {
      accounts.value = recordsOf(accountResult.value.data)
      accountsAvailable.value = true
    } else if (!hadAccountSnapshot) {
      accounts.value = []
      accountsAvailable.value = false
      appendWarning('账号筛选暂不可用；已加载订单仍可查看，但账号名称可能无法解析。')
    } else {
      appendWarning('账号筛选刷新失败，继续使用上次成功加载的账号选项。')
    }
    if (orderResult.status === 'fulfilled' && orderResult.value?.data) {
      orders.value = recordsOf(orderResult.value.data)
      total.value = totalOf(orderResult.value.data, orders.value.length)
      ordersAvailable.value = true
      if (keepSelectedId && !orders.value.some(item => String(item.id) === String(keepSelectedId))) {
        selected.value = null
        manualForm.visible = false
      }
    } else if (!hadSnapshot) {
      orders.value = []
      total.value = 0
      ordersAvailable.value = false
      selected.value = null
      manualForm.visible = false
      error.value = orderResult.status === 'rejected'
        ? (orderResult.reason?.message || '加载订单列表失败')
        : '订单列表响应无效，请稍后重试'
    } else {
      const message = orderResult.status === 'rejected'
        ? orderResult.reason?.message
        : '订单列表响应无效'
      appendWarning(`订单列表刷新失败，继续显示上次成功加载的订单数据。${message ? ` ${message}` : ''}`)
    }
  } catch (requestError) {
    if (!request.isCurrent()) return
    if (hadSnapshot) {
      appendWarning(`订单列表刷新失败，继续显示上次成功加载的订单数据。${requestError.message ? ` ${requestError.message}` : ''}`)
    } else {
      orders.value = []
      total.value = 0
      ordersAvailable.value = false
      selected.value = null
      manualForm.visible = false
      error.value = requestError.message || '加载订单列表失败'
    }
  } finally {
    if (request.isCurrent()) ordersLoading.value = false
  }
}

async function selectOrder(row) {
  clearNotice()
  detailLoadError.value = ''
  selected.value = null
  manualForm.visible = false
  if (ordersAvailable.value === false) {
    detailLoadError.value = '订单列表不可用，请先刷新列表'
    return false
  }
  try {
    const res = await getOrderDetail(row.id)
    if (!res?.data || typeof res.data !== 'object' || Array.isArray(res.data)
      || String(res.data.id ?? '') !== String(row.id)) {
      throw new Error('订单详情响应格式异常')
    }
    selected.value = res.data
    return true
  } catch (requestError) {
    detailLoadError.value = requestError?.message || '加载订单详情失败'
    return false
  }
}

function closeDetail() {
  if (manualBusy.value) return
  selected.value = null
  manualForm.visible = false
}

function primeManualForm() {
  const order = selected.value || {}
  const orderChanged = String(manualForm.orderId ?? '') !== String(order.id ?? '')
  const initializeForm = orderChanged || !manualForm.idempotencyKey
  const attempt = order.manualDeliveryAttempt
  const attemptVersion = attempt
    ? `${attempt.attemptId || ''}:${attempt.status || ''}:${attempt.updatedTime || ''}`
    : ''
  if (initializeForm) {
    manualForm.orderId = order.id ?? null
    manualForm.idempotencyKey = createManualDeliveryIdempotencyKey()
    manualForm.deliveryMode = order.deliveryMethod === 'manual_card' ? 'card' : 'text'
    manualForm.deliveryContent = order.deliveryContent || ''
    manualForm.quantityRequested = Number(order.quantityRequested ?? order.quantityTotal ?? 1) || 1
    // 每次切到新订单时重置发货来源与时机
    manualForm.deliverySource = 'custom'
    manualForm.sourceId = ''
    manualForm.deliveryTiming = 'after_payment'
  }
  if (initializeForm || attemptVersion !== manualForm.attemptVersion) {
    if (!initializeForm && attempt) {
      manualForm.deliveryMode = order.deliveryMethod === 'manual_card' ? 'card' : 'text'
      manualForm.deliveryContent = order.deliveryContent || ''
      manualForm.quantityRequested = Number(order.quantityRequested ?? order.quantityTotal ?? 1) || 1
    }
    manualForm.attemptVersion = attemptVersion
    manualOutcome.value = buildPersistedManualDeliveryOutcome(attempt)
  }
}

async function ensureDeliverySourcesLoaded() {
  if (deliverySourcesLoaded.value || deliverySourcesLoading.value) return
  deliverySourcesLoading.value = true
  try {
    const res = await getDeliverySources({ current: 1, size: 200 })
    deliverySources.value = recordsOf(res?.data) || []
    deliverySourcesLoaded.value = true
  } catch (err) {
    // 加载失败不阻塞用户操作；下拉框为空时提示无可用货源
    deliverySources.value = []
    deliverySourcesLoaded.value = true
  } finally {
    deliverySourcesLoading.value = false
  }
}

function onDeliverySourceChange() {
  // 切换发货来源时清空另一方的状态
  if (manualForm.deliverySource === 'library') {
    manualForm.deliveryContent = ''
    ensureDeliverySourcesLoaded()
  } else {
    manualForm.sourceId = ''
  }
}

async function openManualDelivery(row) {
  if (!selected.value || String(selected.value.id) !== String(row.id)) {
    if (!await selectOrder(row)) return
  }
  primeManualForm()
  manualForm.visible = true
}

function toggleManualDelivery(visible) {
  if (!visible) {
    if (manualBusy.value) return
    manualForm.visible = false
    return
  }
  primeManualForm()
  manualForm.visible = true
}

async function refreshSelectedOrder() {
  if (!selected.value?.id) return
  await selectOrder(selected.value)
}

async function submitManualDelivery() {
  if (!selected.value?.id || manualBusy.value || manualSubmitDisabled.value) return
  if (!manualForm.idempotencyKey) {
    manualForm.idempotencyKey = createManualDeliveryIdempotencyKey()
  }
  const requestedQuantity = Number(manualForm.quantityRequested)
  if (!Number.isInteger(requestedQuantity) || requestedQuantity < 1 || requestedQuantity > 100) {
    clearNotice()
    error.value = '发货数量必须是 1 到 100 之间的整数'
    return
  }
  const payload = buildManualDeliveryPayload(manualForm)
  // 货源库发货：必须有 sourceId（内容由后端解析）；自定义发货：必须有 deliveryContent
  const isLibraryMode = manualForm.deliverySource === 'library'
  if (isLibraryMode) {
    if (!payload.sourceId) {
      clearNotice()
      error.value = '请选择发货货源'
      return
    }
  } else if (!payload.deliveryContent) {
    clearNotice()
    error.value = '请先填写发货内容'
    return
  }

  const onlyConfirmPlatform = manualOutcome.value?.retryScope === 'platform_confirm'
  manualConfirming.value = true
  let confirmed
  try {
    confirmed = await confirmAction({
      title: onlyConfirmPlatform ? '确认仅重试平台发货？' : '确认立即手动发货？',
      description: onlyConfirmPlatform
        ? '买家消息已经发送。本次只重试确认闲鱼平台发货，绝不会再次向买家发送消息。'
        : '确认后将立即向真实买家发送消息，并确认闲鱼平台发货。请再次核对发货内容和数量。',
      confirmText: onlyConfirmPlatform ? '仅确认平台发货' : '立即发货',
      dangerous: true
    })
  } finally {
    manualConfirming.value = false
  }
  if (!confirmed) return

  clearNotice()
  manualSubmitting.value = true
  try {
    const res = await manualDeliverOrder(selected.value.id, payload)
    const outcome = buildManualDeliveryOutcome(res?.data || {})
    manualOutcome.value = outcome
    if (outcome.status === 'success') {
      manualForm.visible = false
      manualForm.idempotencyKey = ''
      manualForm.orderId = null
      manualForm.attemptVersion = ''
    }
    await loadOrders({ keepSelectedId: selected.value.id, sync: false })
    await refreshSelectedOrder()
    showManualOutcome(outcome)
  } catch (requestError) {
    const outcome = buildManualDeliveryErrorOutcome(requestError)
    manualOutcome.value = outcome
    showManualOutcome(outcome)
  } finally {
    manualSubmitting.value = false
  }
}

async function syncCurrentOrder(row) {
  clearNotice()
  syncingOrderId.value = row.id
  try {
    const res = await syncOrder(row.id)
    const data = res?.data
    if (!data || typeof data !== 'object' || Array.isArray(data) || typeof data.ok !== 'boolean') {
      throw new Error('订单同步结果响应格式异常')
    }
    await loadOrders({ keepSelectedId: row.id, sync: false })
    if (selected.value && String(selected.value.id) === String(row.id)) {
      await refreshSelectedOrder()
    }
    if (data.ok) success.value = data.message || '订单同步已完成'
    else error.value = data.message || '订单同步失败'
  } catch (requestError) {
    error.value = requestError.message || '提交订单同步失败'
  } finally {
    syncingOrderId.value = null
  }
}

async function syncAccountOrders() {
  if (!query.accountId) {
    error.value = '请先选择要同步的账号'
    return
  }
  clearNotice()
  syncingList.value = true
  try {
    const res = await syncOrders({
      accountId: Number(query.accountId),
      syncDeliveryStatus: true
    })
    const data = res?.data
    if (!data || typeof data !== 'object' || Array.isArray(data) || typeof data.ok !== 'boolean') {
      throw new Error('账号订单同步结果响应格式异常')
    }
    await loadOrders({ sync: false })
    if (data.ok === false) error.value = data.message || '账号订单同步失败'
    else success.value = data.message || '账号真实订单同步已完成'
  } catch (requestError) {
    error.value = requestError.message || '提交账号订单同步失败'
  } finally {
    syncingList.value = false
  }
}

async function syncAllAccountsOrders() {
  const list = accounts.value
  if (!Array.isArray(list) || list.length === 0) {
    error.value = '没有可同步的账号'
    return
  }
  clearNotice()
  syncingList.value = true
  try {
    const results = await Promise.allSettled(
      list.map(account => syncOrders({
        accountId: Number(account.id),
        syncDeliveryStatus: true
      }))
    )
    let succeeded = 0
    let failed = 0
    results.forEach(r => {
      if (r.status === 'fulfilled') {
        const data = r.value?.data
        if (data && typeof data === 'object' && !Array.isArray(data) && data.ok !== false) {
          succeeded += 1
        } else {
          failed += 1
        }
      } else {
        failed += 1
      }
    })
    if (failed === 0) {
      success.value = `全部账号同步完成（共 ${succeeded} 个账号）`
    } else if (succeeded === 0) {
      error.value = `全部账号同步失败（共 ${failed} 个账号）`
    } else {
      success.value = `同步完成：成功 ${succeeded} 个，失败 ${failed} 个`
    }
    await loadOrders({ sync: false })
  } catch (requestError) {
    error.value = requestError.message || '同步全部账号订单失败'
  } finally {
    syncingList.value = false
  }
}

function onSyncButtonClick() {
  if (query.accountId) {
    return syncAccountOrders()
  }
  return syncAllAccountsOrders()
}

function search() {
  query.current = 1
  loadOrders()
}

function onSortHeaderClick(column) {
  if (!column?.sortField) return
  if (query.sortField === column.sortField) {
    // 同列切换方向；当前为 desc 则改 asc，asc 再点则回到 desc（保留默认排序，不取消）
    query.sortOrder = query.sortOrder === 'asc' ? 'desc' : 'asc'
  } else {
    query.sortField = column.sortField
    query.sortOrder = 'asc'
  }
  query.current = 1
  loadOrders()
}

function resetFilters() {
  query.accountId = ''
  query.status = ''
  query.keyword = ''
  query.current = 1
  query.sortField = 'createdAt'
  query.sortOrder = 'desc'
  selected.value = null
  manualForm.visible = false
  selectedKeys.value = []
  loadOrders({ keepSelectedId: null })
}

function goPage(page) {
  const totalPages = Math.max(1, Math.ceil(total.value / query.size))
  const p = Math.max(1, Math.min(totalPages, Number(page) || 1))
  if (p === query.current) return
  query.current = p
  jumpPage.value = p
  loadOrders()
}

function onPageSizeChange(size) {
  query.size = Number(size) || 20
  query.current = 1
  jumpPage.value = 1
  loadOrders()
}

function jumpToPage() {
  const totalPages = Math.max(1, Math.ceil(total.value / query.size))
  const p = Math.max(1, Math.min(totalPages, Number(jumpPage.value) || 1))
  jumpPage.value = p
  if (p !== query.current) {
    query.current = p
    loadOrders()
  }
}

function toggleBatchMenu() {
  batchMenuVisible.value = !batchMenuVisible.value
}

function exportOrders() {
  success.value = '导出功能准备中'
}

function batchAction() {
  batchMenuVisible.value = false
  success.value = '批量操作功能准备中'
}

function onClickOutside(e) {
  if (batchMenuVisible.value && !e.target.closest('.action-dropdown')) {
    batchMenuVisible.value = false
  }
}

function onHeaderAction(event) {
  if (event.detail === 'orders-refresh') loadOrders()
}

let ordersPollTimer = null
let ordersPollVisible = true

function startOrdersPolling() {
  if (ordersPollTimer) return
  ordersPollTimer = setInterval(() => {
    if (!ordersPollVisible || document.hidden) return
    if (ordersLoading.value || syncingList.value) return
    loadOrders({ sync: false, silent: true })
  }, 30000)
}

function stopOrdersPolling() {
  if (ordersPollTimer) {
    clearInterval(ordersPollTimer)
    ordersPollTimer = null
  }
}

function onVisibilityChange() {
  ordersPollVisible = !document.hidden
  if (ordersPollVisible) {
    loadOrders({ sync: false, silent: true })
  }
}

onMounted(() => {
  window.addEventListener('xya-header-action', onHeaderAction)
  document.addEventListener('visibilitychange', onVisibilityChange)
  document.addEventListener('click', onClickOutside)
  loadOrders()
  startOrdersPolling()
})

onBeforeUnmount(() => {
  ordersRequestGuard.invalidate()
  window.removeEventListener('xya-header-action', onHeaderAction)
  document.removeEventListener('visibilitychange', onVisibilityChange)
  document.removeEventListener('click', onClickOutside)
  stopOrdersPolling()
})
</script>

<style scoped>
.orders-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 4px 0;
}

/* ====== 筛选栏 ====== */
.filter-bar {
  background: #fff;
  border: 1px solid var(--line);
  border-radius: 14px;
  box-shadow: var(--shadow);
  padding: 18px 22px 16px;
}

.filter-title {
  font-size: 15px;
  font-weight: 700;
  color: #1e293b;
  margin-bottom: 14px;
}

.filter-row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.filter-select {
  height: 38px;
  border: 1px solid #e2e8f0;
  background: #fff;
  border-radius: 8px;
  padding: 0 34px 0 14px;
  color: #334155;
  font-size: 14px;
  min-width: 150px;
  cursor: pointer;
  appearance: none;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%2394a3b8' stroke-width='2.5' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpolyline points='6 9 12 15 18 9'%3E%3C/polyline%3E%3C/svg%3E");
  background-repeat: no-repeat;
  background-position: right 12px center;
  transition: border-color .15s;
}
.filter-select:focus {
  outline: none;
  border-color: #93c5fd;
  box-shadow: 0 0 0 3px rgba(59, 130, 246, .1);
}
.filter-select:disabled {
  background-color: #f5f7fa;
  color: #94a3b8;
  cursor: not-allowed;
}

.filter-search {
  position: relative;
  flex: 1;
  min-width: 240px;
  max-width: 380px;
}

.search-input {
  width: 100%;
  height: 38px;
  border: 1px solid #e2e8f0;
  background: #fff;
  border-radius: 8px;
  padding: 0 38px 0 14px;
  color: #334155;
  font-size: 14px;
  outline: none;
  transition: border-color .15s;
}
.search-input::placeholder { color: #94a3b8; }
.search-input:focus {
  border-color: #93c5fd;
  box-shadow: 0 0 0 3px rgba(59, 130, 246, .1);
}

.search-icon {
  position: absolute;
  right: 12px;
  top: 50%;
  transform: translateY(-50%);
  color: #94a3b8;
  font-size: 14px;
  pointer-events: none;
}

.btn-query {
  height: 38px !important;
  min-width: 88px !important;
  border-radius: 8px !important;
  font-size: 14px !important;
  font-weight: 500 !important;
}

.btn-reset {
  height: 38px !important;
  min-width: 76px !important;
  border-radius: 8px !important;
  font-size: 14px !important;
  border-color: #e2e8f0 !important;
  color: #475569 !important;
  background: #fff !important;
  box-shadow: none !important;
}
.btn-reset:hover {
  border-color: #cbd5e1 !important;
  background: #f8fafc !important;
}

.btn-sync {
  height: 38px !important;
  border-radius: 8px !important;
  font-size: 13px !important;
  background: #f0f7ff !important;
  border: 1px solid #dbeafe !important;
  color: var(--primary) !important;
  box-shadow: none !important;
  display: inline-flex !important;
  align-items: center;
  gap: 6px;
  padding: 0 14px !important;
  font-weight: 500 !important;
}
.btn-sync:hover {
  background: #e6f0ff !important;
  border-color: #bfdbfe !important;
}
.btn-sync:disabled {
  opacity: .55;
}

.sync-icon {
  font-size: 16px;
  line-height: 1;
}

.filter-tip {
  margin-top: 12px;
  font-size: 12px;
  color: #94a3b8;
  line-height: 1.6;
}

.refresh-status {
  margin-bottom: 10px;
  color: #526079;
  font-size: 13px;
}

/* ====== 统计卡片 ====== */
.stats-grid {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 14px;
}

.stat-card {
  background: #fff;
  border: 1px solid var(--line);
  border-radius: 14px;
  box-shadow: var(--shadow);
  padding: 20px;
  display: flex;
  align-items: center;
  gap: 14px;
  transition: transform .15s ease, box-shadow .15s ease;
}
.stat-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 6px 16px rgba(31, 53, 94, .08), 0 12px 32px rgba(31, 53, 94, .10);
}

.stat-icon-circle {
  width: 48px;
  height: 48px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.stat-icon-circle.blue {
  background: linear-gradient(135deg, #dbeafe, #bfdbfe);
  color: #2563eb;
}
.stat-icon-circle.orange {
  background: linear-gradient(135deg, #ffedd5, #fed7aa);
  color: #ea580c;
}
.stat-icon-circle.green {
  background: linear-gradient(135deg, #dcfce7, #bbf7d0);
  color: #16a34a;
}
.stat-icon-circle.red {
  background: linear-gradient(135deg, #fee2e2, #fecaca);
  color: #dc2626;
}
.stat-icon-circle.purple {
  background: linear-gradient(135deg, #f3e8ff, #e9d5ff);
  color: #9333ea;
}

.stat-icon-svg {
  font-size: 22px;
  line-height: 1;
}

.stat-info {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  flex: 1;
}

.stat-card .stat-label {
  font-size: 13px;
  color: #64748b;
  font-weight: 500;
}

.stat-card .stat-value {
  font-size: 26px;
  font-weight: 700;
  color: #1e293b;
  letter-spacing: -0.5px;
  line-height: 1.2;
}
.stat-card .stat-value.amount {
  font-size: 22px;
}

.stat-trend {
  font-size: 12px;
  color: #94a3b8;
  margin-top: 2px;
}
.stat-trend.up b { color: #16a34a; font-weight: 600; }
.stat-trend.down b { color: #dc2626; font-weight: 600; }
.trend-arrow { font-size: 10px; }

/* ====== 订单表格卡片 ====== */
.orders-table-card {
  background: #fff;
  border: 1px solid var(--line);
  border-radius: 14px;
  box-shadow: var(--shadow);
  overflow: hidden;
}

.table-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 22px 14px;
}

.table-title {
  margin: 0;
  font-size: 16px;
  font-weight: 700;
  color: #1e293b;
}

.table-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.action-btn {
  height: 34px;
  padding: 0 14px;
  border: 1px solid #e2e8f0;
  background: #fff;
  border-radius: 8px;
  color: #475569;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  transition: all .15s;
}
.action-btn:hover {
  border-color: #bfdbfe;
  background: #f8fbff;
  color: var(--primary);
}
.action-btn.icon-only {
  width: 34px;
  padding: 0;
  justify-content: center;
  font-size: 16px;
}

.action-dropdown { position: relative; }

.dropdown-menu {
  position: absolute;
  top: calc(100% + 6px);
  right: 0;
  background: #fff;
  border: 1px solid var(--line);
  border-radius: 10px;
  box-shadow: 0 10px 28px rgba(31, 53, 94, .14);
  min-width: 136px;
  z-index: 20;
  overflow: hidden;
}

.dropdown-item {
  display: block;
  width: 100%;
  padding: 10px 14px;
  border: 0;
  background: transparent;
  text-align: left;
  font-size: 13px;
  color: #334155;
  cursor: pointer;
}
.dropdown-item:hover {
  background: #f0f6ff;
  color: var(--primary);
}

.dropdown-arrow { font-size: 10px; opacity: .6; }
.refresh-icon { display: inline-block; font-size: 15px; }

.table-wrap { overflow-x: auto; }

/* 表头浅灰背景 + 行 hover 优化（覆盖 BaseTable 默认样式） */
:deep(.base-table) {
  width: 100%;
  border-collapse: collapse;
  font-size: 14px;
}
:deep(.base-table th) {
  height: 44px;
  text-align: left;
  color: #64748b;
  font-weight: 600;
  font-size: 13px;
  border-bottom: 1px solid #f1f5f9;
  background: #fafbfc;
  padding: 0 16px;
  white-space: nowrap;
}
:deep(.base-table td) {
  padding: 14px 16px;
  border-bottom: 1px solid #f1f5f9;
  color: #334155;
  vertical-align: middle;
  height: auto;
}
:deep(.base-table tbody tr) {
  cursor: pointer;
  transition: background .1s;
}
:deep(.base-table tbody tr:hover td) {
  background: #fafcff;
}
:deep(.base-table .col-select) {
  width: 46px;
  text-align: center;
}
:deep(.base-table .bt-checkbox) {
  width: 16px;
  height: 16px;
  cursor: pointer;
  accent-color: var(--primary);
}

.col-order-no { width: 170px; }
.order-no-cell { display: flex; flex-direction: column; gap: 3px; }
.order-id {
  font-weight: 600;
  font-size: 13px;
  color: #1e293b;
  font-family: "SF Mono", Monaco, Consolas, monospace;
}
.order-time { font-size: 12px; }

.col-buyer { width: 150px; }
.buyer-cell { display: flex; flex-direction: column; gap: 3px; }
.buyer-name-row { display: flex; align-items: center; gap: 6px; }
.buyer-name { font-weight: 500; font-size: 14px; color: #1e293b; }

.v-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  height: 18px;
  padding: 0 5px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 700;
  color: #fff;
  line-height: 1;
}
.v-badge.v1 { background: #3b82f6; }
.v-badge.v2 { background: #f97316; }
.v-badge.v3 { background: #8b5cf6; }

.buyer-id { font-size: 12px; }

.col-items { min-width: 260px; }
.goods-cell { display: flex; flex-direction: column; gap: 6px; }
.goods-item { display: flex; align-items: center; gap: 10px; }

.goods-thumb {
  width: 44px;
  height: 44px;
  object-fit: cover;
  border-radius: 8px;
  border: 1px solid #eef2f7;
  background: #f8fafc;
  flex-shrink: 0;
}
.goods-thumb-placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
  color: #cbd5e1;
}

.goods-info {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  flex: 1;
}
.goods-title {
  font-size: 13px;
  color: #1e293b;
  line-height: 1.4;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 260px;
  font-weight: 500;
}
.goods-id-text { font-size: 12px; color: #94a3b8; }

.col-quantity { width: 110px; }
.qty-text { font-size: 14px; color: #1e293b; }
.qty-progress {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 6px;
}
.qty-bar {
  width: 60px;
  height: 4px;
  background: #eef2f7;
  border-radius: 99px;
  overflow: hidden;
  flex-shrink: 0;
}
.qty-bar-fill {
  height: 100%;
  background: linear-gradient(90deg, #3b82f6, #2563eb);
  border-radius: 99px;
  transition: width .3s ease;
}
.qty-pct { font-size: 12px; }

.col-status, .col-delivery { width: 88px; }

/* 胶囊样式状态徽章（用于订单状态/发货状态列） */
.status-badge {
  display: inline-flex;
  align-items: center;
  height: 24px;
  padding: 0 9px;
  border-radius: 99px;
  font-size: 12px;
  font-weight: 500;
  white-space: nowrap;
}
.status-badge.red {
  background: #fef2f2;
  color: #dc2626;
  border: 1px solid #fecaca;
}
.status-badge.green {
  background: #f0fdf4;
  color: #16a34a;
  border: 1px solid #bbf7d0;
}
.status-badge.blue {
  background: #eff6ff;
  color: #2563eb;
  border: 1px solid #bfdbfe;
}
.status-badge.orange {
  background: #fff7ed;
  color: #ea580c;
  border: 1px solid #fed7aa;
}
.status-badge.gray {
  background: #f8fafc;
  color: #64748b;
  border: 1px solid #e2e8f0;
}
.status-badge.cyan {
  background: #ecfeff;
  color: #0891b2;
  border: 1px solid #a5f3fc;
}

.delivery-dash {
  color: #cbd5e1;
  font-size: 20px;
  font-weight: 300;
  line-height: 1;
}

.col-op { width: 210px; }
.op-cell { display: flex; align-items: center; gap: 0; flex-wrap: nowrap; white-space: nowrap; }

.op-link {
  border: 0;
  background: transparent;
  color: var(--primary);
  font-size: 13px;
  cursor: pointer;
  padding: 4px 5px;
  border-radius: 5px;
  font-weight: 500;
  transition: background .1s;
  white-space: nowrap;
  flex-shrink: 0;
}
.op-link:hover { background: #eff6ff; color: #1d4ed8; }

/* ====== 分页 ====== */
.pagination-wrap {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 12px;
  padding: 14px 22px;
  border-top: 1px solid #f1f5f9;
}

.page-jump-wrap {
  display: flex;
  align-items: center;
  gap: 4px;
}

.page-jump-label {
  font-size: 13px;
  color: #64748b;
}

.page-jump-input {
  width: 48px;
  height: 32px;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  text-align: center;
  font-size: 13px;
  color: #334155;
  outline: none;
  padding: 0 4px;
}
.page-jump-input:focus {
  border-color: #93c5fd;
  box-shadow: 0 0 0 2px rgba(59, 130, 246, .1);
}

:deep(.pagination-wrap .pagination) {
  margin: 0;
}

.table-empty {
  padding: 48px 0;
  text-align: center;
  color: #94a3b8;
  font-size: 14px;
}

/* ====== 订单详情弹窗 ====== */
.order-modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(20, 36, 58, .58);
  backdrop-filter: blur(2px);
  z-index: 1001;
  display: flex;
  align-items: center;
  justify-content: center;
}

.order-modal {
  position: relative;
  width: 720px;
  max-width: 92vw;
  max-height: 85vh;
  background: #fff;
  border: 1px solid #e8eef8;
  border-radius: 18px;
  box-shadow: 0 28px 80px rgba(17, 35, 67, .25);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.order-modal-close {
  position: absolute;
  right: 16px;
  top: 14px;
  width: 32px;
  height: 32px;
  border: 0;
  background: transparent;
  color: #35435d;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  z-index: 1;
}

.order-modal-close .ui-icon {
  width: 20px;
}

.order-modal-close:disabled {
  cursor: not-allowed;
  opacity: .45;
}

.order-modal-title {
  margin: 0;
  padding: 20px 24px 12px;
  font-size: 18px;
  font-weight: 700;
  color: #1e293b;
  border-bottom: 1px solid #f0f3f8;
}

.order-modal-body {
  padding: 20px 24px 24px;
  overflow-y: auto;
  flex: 1;
}

.manual-delivery-section {
  margin-top: 16px;
  padding: 16px;
  background: #f8fafc;
  border-radius: 10px;
  border: 1px solid #e8eef8;
}

.manual-delivery-warning {
  margin: 8px 0 14px;
  padding: 10px 12px;
  border: 1px solid #f7c97a;
  border-radius: 8px;
  background: #fff8e8;
  color: #8a4b08;
  font-size: 13px;
  line-height: 1.6;
}

.manual-outcome {
  margin: 0 0 14px;
  padding: 10px 12px;
  border: 1px solid #f7c97a;
  border-radius: 8px;
  background: #fff8e8;
  color: #8a4b08;
  font-size: 13px;
  line-height: 1.6;
}

.manual-outcome.is-success {
  border-color: #abefc6;
  background: #ecfdf3;
  color: #067647;
}

.manual-outcome.is-error {
  border-color: #fecaca;
  background: #fef2f2;
  color: #b42318;
}

.detail-section {
  margin-bottom: 20px;
}

.detail-section:last-child {
  margin-bottom: 0;
}

.detail-grid {
  display: grid;
  gap: 0;
}

.detail-grid.cols-2 {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.detail-item {
  display: flex;
  align-items: center;
  padding: 8px 0;
  border-bottom: 1px solid #f0f3f8;
  min-height: 36px;
}

.detail-label {
  color: #6b7a90;
  font-size: 13px;
  min-width: 80px;
  flex-shrink: 0;
}

.detail-value {
  color: #1e293b;
  font-size: 13px;
  font-weight: 500;
}

.detail-value.mono {
  font-family: "SF Mono", Monaco, "Cascadia Code", Consolas, monospace;
  font-size: 12px;
  word-break: break-all;
}

.detail-value .error-text {
  color: #dc2626;
}

.error-text {
  color: #dc2626;
}

.section-title {
  margin-bottom: 4px;
  font-weight: 600;
  font-size: 14px;
  color: #1e293b;
}

.item-list {
  display: grid;
  gap: 8px;
}

.item-row {
  padding: 10px 12px;
  border: 1px solid #e6ecf5;
  border-radius: 10px;
  background: #f8fbff;
}

.content-box {
  min-height: 64px;
  padding: 12px;
  border: 1px solid #e6ecf5;
  border-radius: 10px;
  background: #fbfdff;
  white-space: pre-wrap;
  word-break: break-word;
}

.inline-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

.form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.form-field {
  display: grid;
  gap: 6px;
  margin-bottom: 12px;
}

/* 发货来源切换按钮组 */
.source-toggle {
  display: inline-flex;
  border: 1px solid #d9e2f0;
  border-radius: 10px;
  overflow: hidden;
  background: #f8fafc;
}
.source-toggle-btn {
  border: 0;
  background: transparent;
  padding: 8px 16px;
  font-size: 13px;
  color: #64748b;
  cursor: pointer;
  transition: all .15s;
  white-space: nowrap;
}
.source-toggle-btn:hover:not(:disabled):not(.active) {
  background: #f0f6ff;
  color: var(--primary);
}
.source-toggle-btn.active {
  background: var(--primary, #2563eb);
  color: #fff;
  font-weight: 500;
}
.source-toggle-btn:disabled {
  cursor: not-allowed;
  opacity: .55;
}

.source-empty-hint {
  font-size: 12px;
  color: #94a3b8;
  line-height: 1.6;
  margin-top: 2px;
}

.textarea {
  width: 100%;
  min-height: 120px;
  padding: 10px 12px;
  border: 1px solid #d9e2f0;
  border-radius: 10px;
  resize: vertical;
}

.strong {
  font-weight: 600;
}

.success {
  background: #ecfdf3;
  color: #067647;
  border-color: #abefc6;
}

.warning {
  background: #fff8e8;
  color: #8a4b08;
  border-color: #f7c97a;
}

/* ====== 响应式 ====== */
@media (max-width: 1200px) {
  .stats-grid { grid-template-columns: repeat(3, 1fr); }
}
@media (max-width: 768px) {
  .stats-grid { grid-template-columns: repeat(2, 1fr); }
}
@media (max-width: 900px) {
  .filter-row { flex-direction: column; align-items: stretch; }
  .filter-search { max-width: none; }
  .form-grid { grid-template-columns: 1fr; }

  /* 订单详情弹窗：全宽底部弹出 */
  .order-modal-mask {
    align-items: flex-end;
  }
  .order-modal {
    width: 100vw;
    max-width: 100vw;
    max-height: 90vh;
    border-radius: 20px 20px 0 0;
    overflow-y: auto;
    -webkit-overflow-scrolling: touch;
  }
  .order-modal-close {
    right: 12px;
    top: 10px;
    width: 36px;
    height: 36px;
  }
  .order-modal-title {
    padding: 14px 14px 10px;
    font-size: 18px;
  }
  .order-modal-body {
    padding: 12px 14px 16px;
  }

  .manual-delivery-section {
    margin-top: 12px;
    padding: 12px;
    border-radius: 10px;
  }

  .goods-thumb {
    width: 36px;
    height: 36px;
  }
  .goods-title {
    font-size: 13px;
    max-width: 160px;
  }

  .detail-grid.cols-2 {
    grid-template-columns: minmax(0, 1fr);
  }
  .detail-item {
    padding: 8px 0;
    min-height: 32px;
  }
  .detail-label {
    min-width: 72px;
    font-size: 13px;
  }
  .detail-value {
    font-size: 13px;
  }

  .detail-section {
    margin-bottom: 14px;
  }
  .section-title {
    font-size: 14px;
  }

  .form-grid {
    grid-template-columns: minmax(0, 1fr);
    gap: 10px;
  }
  .detail-grid.cols-2 > *,
  .form-grid > * {
    min-width: 0;
  }
  .form-field {
    gap: 6px;
    margin-bottom: 10px;
  }
  .textarea {
    min-height: 100px;
  }

  .item-row {
    padding: 8px 10px;
  }
  .content-box {
    min-height: 56px;
    padding: 10px;
  }

  .inline-actions {
    gap: 8px;
  }
}
</style>
