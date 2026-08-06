<template>
  <div>
    <div v-if="error" class="global-notice error">{{ error }}</div>
    <div v-if="warning" class="global-notice warning" role="status">{{ warning }}</div>
    <div v-if="success" class="global-notice success">{{ success }}</div>

    <div class="dr-tabs">
      <button type="button" class="dr-tab" :class="{ active: activeTab === 'records' }" @click="switchTab('records')">
        发货记录
      </button>
      <button type="button" class="dr-tab" :class="{ active: activeTab === 'sessions' }" @click="switchTab('sessions')">
        声明会话
        <span v-if="waitingCount > 0" class="dr-tab-badge">{{ waitingCount }}</span>
      </button>
    </div>

    <template v-if="activeTab === 'records'">
    <CardPanel title="发货记录筛选">
      <div class="toolbar wrap">
        <select v-model="query.status" class="input narrow">
          <option value="">全部状态</option>
          <option value="0">待处理</option>
          <option value="1">进行中</option>
          <option value="2">成功</option>
          <option value="3">失败</option>
          <option value="6">缺货</option>
          <option value="7">配置错误</option>
        </select>
        <select v-model="query.timing" class="input narrow">
          <option value="">全部时机</option>
          <option value="after_payment">付款后</option>
          <option value="after_receipt">收货后</option>
          <option value="after_review">评价后</option>
        </select>
        <select v-model="query.deliveryMode" class="input narrow">
          <option value="">全部方式</option>
          <option value="text">文本</option>
          <option value="card">卡密</option>
        </select>
        <input v-model="query.goodsKeyword" class="input grow" placeholder="商品关键词" />
        <input v-model="query.buyerKeyword" class="input grow" placeholder="买家关键词" />
        <input v-model="query.orderKeyword" class="input grow" placeholder="订单号 / 外部订单号" />
        <AppButton type="primary" :loading="recordsLoading" @click="search">搜索</AppButton>
        <AppButton @click="resetFilters">重置</AppButton>
        <AppButton
          :disabled="recordsAvailable !== true || selectedIds.length === 0 || batchRetrying"
          :loading="batchRetrying"
          @click="batchRetry"
        >
          {{ batchRetrying ? '重试中...' : `重试选中 (${selectedIds.length})` }}
        </AppButton>
        <AppButton :disabled="recordsAvailable !== true || exportLoading" @click="exportCsv">
          {{ exportLoading ? '导出中...' : '导出 CSV' }}
        </AppButton>
      </div>
    </CardPanel>

    <CardPanel title="发货记录" style="margin-top: 16px">
      <div v-if="recordsRefreshing" class="refresh-status" role="status" aria-live="polite">
        正在刷新发货记录，现有数据仍可查看。
      </div>
      <EmptyState
        v-if="recordsLoading && recordsAvailable !== true"
        icon="⏳"
        title="正在加载发货记录"
        description="正在读取实际发货执行结果，请稍候。"
      />
      <EmptyState
        v-else-if="recordsAvailable === false"
        icon="⚠️"
        title="发货记录暂不可用"
        description="当前无法确认发货记录与执行结果；请求失败不会显示为空记录。"
      >
        <template #actions><AppButton @click="load">重新加载</AppButton></template>
      </EmptyState>
      <BaseTable
        v-else-if="recordsAvailable === true"
        v-model:selected-keys="selectedIds"
        :columns="columns"
        :rows="rows"
        :row-key="row => row.id"
        selectable
        @row-click="showDetail"
      >
        <template #goods="{ row }">
          <div class="goods-cell">
            <img
              v-if="row.goodsCoverPic"
              :src="row.goodsCoverPic"
              alt=""
              loading="lazy"
              referrerpolicy="no-referrer"
              class="goods-thumb"
              @error="onGoodsThumbError(row, $event)"
            />
            <span class="cell-ellipsis" :title="row.goodsTitleText">{{ row.goodsTitleText }}</span>
          </div>
        </template>
        <template #status="{ row }">
          <Badge :type="row.deliveryBadge">{{ row.deliveryStatusText }}</Badge>
        </template>
        <template #timing="{ row }">
          {{ row.timingText }}
        </template>
        <template #mode="{ row }">
          {{ row.deliveryModeText }}
        </template>
        <template #progress="{ row }">
          {{ row.deliveryProgressText }}
        </template>
        <template #errorMessage="{ row }">
          <span class="cell-ellipsis" :title="row.errorMessage || ''">{{ row.errorMessage || '-' }}</span>
        </template>
        <template #op="{ row }">
          <div class="inline-actions">
            <button class="link" @click.stop="showDetail(row)">详情</button>
            <button
              v-if="row.canRetry"
              class="link"
              :disabled="retryingId === row.id"
              @click.stop="retryRecord(row)"
            >{{ retryingId === row.id ? '重试中...' : '重试' }}</button>
            <button
              v-if="row.canScheduleRedelivery"
              class="link"
              @click.stop="openSchedule(row)"
            >安排重新发货</button>
          </div>
        </template>
      </BaseTable>
      <Pagination v-if="recordsAvailable === true" :total="total" :current="query.current" :page-size="query.size" @page-change="goPage" />
    </CardPanel>

    <CardPanel v-if="detailView" title="发货记录详情" style="margin-top: 16px">
      <div class="detail-grid">
        <div><b>记录 ID：</b> {{ detailView.id || '-' }}</div>
        <div><b>订单号：</b> {{ detailView.orderId || '-' }}</div>
        <div><b>外部订单号：</b> {{ detailView.externalOrderId || '-' }}</div>
        <div><b>商品：</b> {{ detailView.goodsTitleText }}</div>
        <div><b>商品 ID：</b> {{ detailView.goodsId || '-' }}</div>
        <div><b>买家：</b> {{ detailView.buyerNameText }}</div>
        <div><b>卖家：</b> {{ detailView.sellerNameText }}</div>
        <div><b>购买时间：</b> {{ detailView.purchaseTimeText || '-' }}</div>
        <div><b>状态：</b> {{ detailView.deliveryStatusText }}</div>
        <div><b>进度：</b> {{ detailView.deliveryProgressText }}</div>
        <div><b>时机：</b> {{ detailView.timingText }}</div>
        <div><b>方式：</b> {{ detailView.deliveryModeText }}</div>
        <div><b>创建时间：</b> {{ detailView.createdTimeText }}</div>
        <div><b>完成时间：</b> {{ detailView.completedTimeText }}</div>
        <div><b>平台同步：</b> {{ detailView.platformSyncTimeText }}</div>
        <div><b>结果：</b> {{ detailView.resultText }}</div>
      </div>

      <div class="panel-block">
        <div class="section-title">发货内容</div>
        <div class="content-box">{{ detailView.deliveryContentText }}</div>
      </div>

      <div class="panel-block">
        <div class="section-title">错误信息</div>
        <div class="content-box">{{ detailView.errorMessageText }}</div>
      </div>

      <div v-if="detailView.canRetry || detailView.canScheduleRedelivery" class="panel-block detail-actions">
        <AppButton
          v-if="detailView.canRetry"
          type="primary"
          :loading="retryingId === detailView.id"
          @click="retryRecord(detailView)"
        >{{ retryingId === detailView.id ? '重试中...' : '重试发货' }}</AppButton>
        <AppButton
          v-if="detailView.canScheduleRedelivery"
          @click="openSchedule(detailView)"
        >安排重新发货</AppButton>
        <span v-if="retryMessage" class="retry-message">{{ retryMessage }}</span>
      </div>
    </CardPanel>

    <CardPanel v-if="redeliveryTarget" title="安排重新发货" style="margin-top: 16px">
      <div class="form-field">
        <label>记录</label>
        <div class="content-box compact">
          #{{ redeliveryTarget.id }} / {{ redeliveryTarget.goodsTitleText || redeliveryTarget.goodsTitle || '-' }}
        </div>
      </div>
      <div class="form-field">
        <label>Cron 表达式</label>
        <input v-model="redeliveryForm.cronExpression" class="input" placeholder="0 0/15 * * * ?" />
      </div>
      <div class="inline-actions">
        <AppButton type="primary" :loading="scheduling" @click="submitScheduleRedelivery">
          {{ scheduling ? '安排中...' : '创建重新发货任务' }}
        </AppButton>
        <AppButton @click="closeSchedule">取消</AppButton>
      </div>
    </CardPanel>
    </template>

    <template v-else>
    <CardPanel title="声明会话筛选">
      <div class="toolbar wrap">
        <select v-model="sessionQuery.status" class="input narrow">
          <option value="">全部状态</option>
          <option value="declaring">发送中</option>
          <option value="waiting">等待买家确认</option>
          <option value="confirmed">已确认</option>
          <option value="cancelled">已取消</option>
        </select>
        <AppButton type="primary" @click="searchSessions">搜索</AppButton>
        <AppButton @click="resetSessionFilters">重置</AppButton>
      </div>
    </CardPanel>

    <CardPanel title="声明会话列表" style="margin-top: 16px">
      <EmptyState
        v-if="sessionsLoading && sessionsAvailable !== true"
        icon="⏳"
        title="正在加载声明会话"
        description="正在读取声明会话，请稍候。"
      />
      <EmptyState
        v-else-if="sessionsAvailable === false"
        icon="⚠️"
        title="声明会话暂不可用"
        :description="sessionsLoadError || '当前无法加载声明会话；请求失败不会显示为空记录。'"
      >
        <template #actions><AppButton @click="loadSessions">重新加载</AppButton></template>
      </EmptyState>
      <BaseTable
        v-else-if="sessionsAvailable === true"
        :columns="sessionColumns"
        :rows="sessionRows"
        :row-key="row => row.id"
      >
        <template #status="{ row }">
          <Badge :type="row.statusBadgeType">{{ row.statusText }}</Badge>
        </template>
        <template #goodsTitle="{ row }">
          <span class="cell-ellipsis" :title="row.goodsTitle || ''">{{ row.goodsTitle || '-' }}</span>
        </template>
        <template #statementContent="{ row }">
          <span class="cell-ellipsis" :title="row.statementContent || ''">{{ row.statementContent || '-' }}</span>
        </template>
        <template #sentAt="{ row }">
          {{ row.sentAtText }}
        </template>
        <template #confirmedAt="{ row }">
          {{ row.confirmedAtText }}
        </template>
        <template #op="{ row }">
          <div class="inline-actions">
            <button
              v-if="row.status === 'waiting'"
              class="link"
              @click.stop="confirmSession(row)"
            >确认发货</button>
            <button
              v-if="row.status === 'waiting'"
              class="link danger-text"
              @click.stop="cancelSession(row)"
            >取消订单</button>
            <button class="link" @click.stop="viewStatement(row)">查看声明</button>
          </div>
        </template>
      </BaseTable>
      <Pagination
        v-if="sessionsAvailable === true"
        :total="sessionsTotal"
        :current="sessionQuery.current"
        :page-size="sessionQuery.size"
        @page-change="goSessionPage"
      />
    </CardPanel>

    <CardPanel v-if="statementView" title="声明文案详情" style="margin-top: 16px">
      <div class="detail-grid">
        <div><b>订单号：</b> {{ statementView.orderId || '-' }}</div>
        <div><b>买家：</b> {{ statementView.buyerNick || '-' }}</div>
        <div><b>商品：</b> {{ statementView.goodsTitle || '-' }}</div>
        <div><b>状态：</b> {{ statementView.statusText || '-' }}</div>
        <div><b>发送时间：</b> {{ statementView.sentAtText || '-' }}</div>
        <div><b>确认/取消时间：</b> {{ statementView.confirmedAtText || '-' }}</div>
      </div>
      <div class="panel-block">
        <div class="section-title">声明文案</div>
        <div class="content-box">{{ statementView.statementContent || '-' }}</div>
      </div>
    </CardPanel>
    </template>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import CardPanel from '../components/CardPanel.vue'
import BaseTable from '../components/BaseTable.vue'
import Badge from '../components/Badge.vue'
import AppButton from '../components/AppButton.vue'
import Pagination from '../components/Pagination.vue'
import EmptyState from '../components/EmptyState.vue'
import {
  cancelDeliveryStatementSession,
  confirmDeliveryStatementSession,
  getDeliveryRecordDetail,
  getDeliveryRecords,
  listDeliveryStatementSessions,
  retryDeliveryRecord,
  scheduleRedelivery
} from '../api/autoDelivery.js'
import { camelizeKeys, recordsOf, recordsOfOrThrow, totalOf } from '../utils/apiData.js'
import { createLatestRequestGuard, listRefreshRequestConfig } from '../utils/latestRequest.js'
import {
  buildDeliveryRecordDetailViewModel,
  buildDeliveryRecordRowViewModel,
  buildScheduleRedeliveryPayload
} from '../utils/deliveryRecordsPageState.js'

const records = ref([])
const total = ref(0)
const selectedIds = ref([])
const detail = ref(null)
const redeliveryTarget = ref(null)
const error = ref('')
const warning = ref('')
const success = ref('')
const recordsAvailable = ref(null)
const recordsLoading = ref(true)
const exportLoading = ref(false)
const batchRetrying = ref(false)
const scheduling = ref(false)
const recordsRequestGuard = createLatestRequestGuard()
const retryingId = ref(null)
const retryMessage = ref('')
const redeliveryForm = reactive({
  cronExpression: '0 0/15 * * * ?'
})

const query = reactive({
  status: '',
  timing: '',
  deliveryMode: '',
  goodsKeyword: '',
  buyerKeyword: '',
  orderKeyword: '',
  current: 1,
  size: 20
})

// ─── 声明会话 tab ───
const activeTab = ref('records')
const sessions = ref([])
const sessionsTotal = ref(0)
const sessionsAvailable = ref(null)
const sessionsLoading = ref(false)
const sessionsLoadError = ref('')
const statementView = ref(null)
const sessionQuery = reactive({
  status: '',
  current: 1,
  size: 20
})

const sessionColumns = [
  { key: 'id', title: 'ID' },
  { key: 'orderId', title: '订单号' },
  { key: 'goodsTitle', title: '商品' },
  { key: 'buyerNick', title: '买家' },
  { key: 'status', title: '状态' },
  { key: 'sentAt', title: '声明发送时间' },
  { key: 'confirmedAt', title: '确认/取消时间' },
  { key: 'statementContent', title: '声明文案' },
  { key: 'op', title: '操作' }
]

const SESSION_STATUS_TEXT = {
  declaring: '发送中',
  waiting: '等待买家确认',
  confirmed: '已确认',
  cancelled: '已取消'
}

const SESSION_STATUS_BADGE = {
  declaring: 'blue',
  waiting: 'orange',
  confirmed: 'green',
  cancelled: 'red'
}

function formatDateTime(value) {
  if (!value) return '-'
  const str = String(value).replace('T', ' ').replace(/\..*$/, '')
  return str || '-'
}

const sessionRows = computed(() =>
  sessions.value.map(row => {
    const r = camelizeKeys(row)
    return {
      ...r,
      statusText: SESSION_STATUS_TEXT[r.status] || r.status || '-',
      statusBadgeType: SESSION_STATUS_BADGE[r.status] || 'gray',
      sentAtText: formatDateTime(r.sentAt),
      confirmedAtText: formatDateTime(r.confirmedAt || r.cancelledAt)
    }
  })
)

const waitingCount = computed(() =>
  sessions.value.filter(s => {
    const r = camelizeKeys(s)
    return r.status === 'waiting'
  }).length
)

const columns = [
  { key: 'id', title: 'ID' },
  { key: 'orderId', title: '订单号' },
  { key: 'goods', title: '商品' },
  { key: 'buyerNameText', title: '买家' },
  { key: 'sellerNameText', title: '卖家' },
  { key: 'timing', title: '时机' },
  { key: 'mode', title: '方式' },
  { key: 'status', title: '状态' },
  { key: 'progress', title: '进度' },
  { key: 'errorMessage', title: '错误' },
  { key: 'purchaseTimeText', title: '购买时间' },
  { key: 'createdTimeText', title: '创建时间' },
  { key: 'op', title: '操作' }
]

const rows = computed(() => records.value.map(buildDeliveryRecordRowViewModel))
const recordsRefreshing = computed(() => recordsLoading.value && recordsAvailable.value === true)
const detailView = computed(() => (detail.value ? buildDeliveryRecordDetailViewModel(detail.value) : null))

function onGoodsThumbError(_row, event) {
  if (event?.target) event.target.style.display = 'none'
}

function clearNotice() {
  error.value = ''
  warning.value = ''
  success.value = ''
}

function buildQuery() {
  return {
    status: query.status === '' ? undefined : Number(query.status),
    timing: query.timing || undefined,
    deliveryMode: query.deliveryMode || undefined,
    goodsKeyword: query.goodsKeyword || undefined,
    buyerKeyword: query.buyerKeyword || undefined,
    orderKeyword: query.orderKeyword || undefined,
    current: query.current,
    size: query.size
  }
}

async function load() {
  const request = recordsRequestGuard.begin()
  const hadSnapshot = recordsAvailable.value === true
  clearNotice()
  recordsLoading.value = true
  try {
    const res = await getDeliveryRecords(buildQuery(), listRefreshRequestConfig(hadSnapshot))
    if (!request.isCurrent()) return
    records.value = camelizeKeys(recordsOfOrThrow(res.data, '发货记录响应格式异常'))
    total.value = totalOf(res.data, records.value.length)
    recordsAvailable.value = true
  } catch (requestError) {
    if (!request.isCurrent()) return
    if (hadSnapshot) {
      warning.value = `发货记录刷新失败，继续显示上次成功加载的发货记录。${requestError.message ? ` ${requestError.message}` : ''}`
    } else {
      records.value = []
      total.value = 0
      detail.value = null
      recordsAvailable.value = false
      error.value = requestError.message || '加载发货记录失败'
    }
  } finally {
    if (request.isCurrent()) recordsLoading.value = false
  }
}

async function showDetail(row) {
  clearNotice()
  detail.value = null
  try {
    const res = await getDeliveryRecordDetail(row.id)
    if (!res.data) throw new Error('发货记录详情响应为空')
    detail.value = camelizeKeys(res.data)
  } catch (requestError) {
    detail.value = null
    error.value = requestError.message || '加载发货记录详情失败'
  }
}


function search() {
  query.current = 1
  load()
}

function resetFilters() {
  query.status = ''
  query.timing = ''
  query.deliveryMode = ''
  query.goodsKeyword = ''
  query.buyerKeyword = ''
  query.orderKeyword = ''
  query.current = 1
  load()
}

function goPage(page) {
  query.current = page
  load()
}

async function retryRecord(row) {
  if (!row || !row.id) return
  if (retryingId.value === row.id) return
  clearNotice()
  retryMessage.value = ''
  retryingId.value = row.id
  try {
    const res = await retryDeliveryRecord(row.id)
    const data = camelizeKeys(res.data || {})
    const status = data.status || ''
    const message = data.message || '重试请求已提交'
    if (status === 'success') {
      success.value = `重试成功：${message}`
    } else if (status === 'failed') {
      error.value = `重试失败：${message}`
    } else if (status === 'in_progress' || status === 'pending' || status === 'message_sent') {
      warning.value = `重试进行中：${message}`
    } else if (status === 'unknown') {
      warning.value = `重试结果未知：${message}`
    } else {
      success.value = message
    }
    retryMessage.value = message
    // 重新加载列表与详情
    await load()
    if (detail.value && detail.value.id === row.id) {
      try {
        const detailRes = await getDeliveryRecordDetail(row.id)
        detail.value = camelizeKeys(detailRes.data)
      } catch (_e) { /* ignore detail refresh error */ }
    }
  } catch (requestError) {
    error.value = requestError.message || '重试发货记录失败'
    retryMessage.value = error.value
  } finally {
    retryingId.value = null
  }
}

async function batchRetry() {
  if (batchRetrying.value) return
  if (!recordsAvailable.value || !selectedIds.value.length) return
  clearNotice()
  batchRetrying.value = true
  let successCount = 0
  let failedCount = 0
  for (const id of selectedIds.value) {
    try {
      await retryDeliveryRecord(id)
      successCount += 1
    } catch {
      failedCount += 1
    }
  }
  if (successCount) {
    success.value = `已请求重试 ${successCount} 条记录${failedCount ? `，${failedCount} 条失败` : ''}`
  } else if (failedCount) {
    error.value = `${failedCount} 条记录重试失败`
  }
  await load()
  batchRetrying.value = false
}

function openSchedule(row) {
  redeliveryTarget.value = row
  redeliveryForm.cronExpression = '0 0/15 * * * ?'
}

function closeSchedule() {
  redeliveryTarget.value = null
}

async function submitScheduleRedelivery() {
  if (!redeliveryTarget.value?.id) return
  if (scheduling.value) return
  clearNotice()
  const payload = buildScheduleRedeliveryPayload(redeliveryForm)
  if (!payload.cronExpression) {
    error.value = 'Cron 表达式必填'
    return
  }
  scheduling.value = true
  try {
    await scheduleRedelivery(redeliveryTarget.value.id, payload)
    success.value = `已为记录 #${redeliveryTarget.value.id} 创建重新发货任务`
    redeliveryTarget.value = null
    await load()
  } catch (requestError) {
    error.value = requestError.message || '创建重新发货任务失败'
  } finally {
    scheduling.value = false
  }
}

function escapeCsv(value) {
  return `"${String(value ?? '').replaceAll('"', '""')}"`
}

async function exportCsv() {
  if (recordsAvailable.value !== true || exportLoading.value) {
    error.value = '发货记录尚不可用，请重新加载成功后再导出。'
    return
  }
  clearNotice()
  exportLoading.value = true
  const EXPORT_MAX_LIMIT = 2000   // 单次导出最大条数，防止浏览器内存压力
  const EXPORT_PAGE_SIZE = 100    // 分页拉取每页大小（后端 PageUtils 限制 max=100）
  const totalCount = total.value || 0
  if (totalCount > EXPORT_MAX_LIMIT) {
    error.value = `当前共 ${totalCount} 条记录，超过单次导出上限 ${EXPORT_MAX_LIMIT} 条，请添加筛选条件缩小范围后再导出`
    exportLoading.value = false
    return
  }
  try {
    success.value = '正在准备导出数据...'
    const exportRows = []
    // 总数为 0 时也尝试拉取一页（可能 total 尚未加载）
    const targetCount = Math.max(totalCount, query.size)
    const totalPages = Math.max(1, Math.ceil(targetCount / EXPORT_PAGE_SIZE))
    for (let page = 1; page <= totalPages; page++) {
      const res = await getDeliveryRecords({
        ...buildQuery(),
        current: page,
        size: EXPORT_PAGE_SIZE
      })
      const pageRecords = camelizeKeys(recordsOf(res.data)).map(buildDeliveryRecordRowViewModel)
      exportRows.push(...pageRecords)
      if (pageRecords.length < EXPORT_PAGE_SIZE) break  // 已到末页
      if (exportRows.length >= EXPORT_MAX_LIMIT) {
        exportRows.length = EXPORT_MAX_LIMIT
        break
      }
      success.value = `正在导出 ${exportRows.length} / ${targetCount} 条...`
    }
    if (!exportRows.length) {
      success.value = ''
      error.value = '没有可导出的发货记录'
      return
    }

    const headers = ['ID', '订单号', '商品', '买家', '卖家', '时机', '方式', '状态', '进度', '错误', '订单时间', '创建时间']
    const lines = [
      headers.join(','),
      ...exportRows.map(row => ([
        row.id,
        row.orderId,
        row.goodsTitleText,
        row.buyerNameText,
        row.sellerNameText,
        row.timingText,
        row.deliveryModeText,
        row.deliveryStatusText,
        row.deliveryProgressText,
        row.errorMessage || '',
        row.purchaseTimeText,
        row.createdTimeText
      ]).map(escapeCsv).join(','))
    ]

    const blob = new Blob(['\uFEFF' + lines.join('\n')], { type: 'text/csv;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `delivery-records-${Date.now()}.csv`
    link.click()
    URL.revokeObjectURL(url)
    success.value = `已导出 ${exportRows.length} 条发货记录`
  } catch (requestError) {
    success.value = ''
    error.value = requestError.message || '导出发货记录失败'
  } finally {
    exportLoading.value = false
  }
}

function onHeaderAction(event) {
  if (event.detail === 'delivery-records-refresh') load()
  if (event.detail === 'delivery-records-export') exportCsv()
}

// ─── 声明会话相关函数 ───
function switchTab(tab) {
  if (activeTab.value === tab) return
  activeTab.value = tab
  clearNotice()
  if (tab === 'sessions' && sessionsAvailable.value !== true && !sessionsLoading.value) {
    loadSessions()
  }
}

async function loadSessions() {
  clearNotice()
  sessionsLoadError.value = ''
  sessionsAvailable.value = false
  sessions.value = []
  sessionsTotal.value = 0
  statementView.value = null
  sessionsLoading.value = true
  try {
    const res = await listDeliveryStatementSessions({
      status: sessionQuery.status || undefined,
      current: sessionQuery.current,
      size: sessionQuery.size
    })
    sessions.value = camelizeKeys(recordsOfOrThrow(res.data, '声明会话响应格式异常'))
    sessionsTotal.value = totalOf(res.data, sessions.value.length)
    sessionsAvailable.value = true
  } catch (requestError) {
    sessionsLoadError.value = requestError?.message || '加载声明会话失败'
    sessionsAvailable.value = false
  } finally {
    sessionsLoading.value = false
  }
}

function searchSessions() {
  sessionQuery.current = 1
  loadSessions()
}

function resetSessionFilters() {
  sessionQuery.status = ''
  sessionQuery.current = 1
  loadSessions()
}

function goSessionPage(page) {
  sessionQuery.current = page
  loadSessions()
}

function viewStatement(row) {
  statementView.value = row
}

async function confirmSession(row) {
  clearNotice()
  if (!window.confirm(`确认发货？将立即为订单 ${row.orderId || ''} 触发自动发货流程`)) return
  try {
    await confirmDeliveryStatementSession(row.id)
    success.value = `已确认会话 #${row.id}，已触发发货`
    await loadSessions()
  } catch (requestError) {
    error.value = requestError.message || '确认会话失败'
  }
}

async function cancelSession(row) {
  clearNotice()
  if (!window.confirm(`取消订单 ${row.orderId || ''} 的发货声明？将通知买家转人工客服，且不会发货`)) return
  try {
    await cancelDeliveryStatementSession(row.id)
    success.value = `已取消会话 #${row.id}，已通知买家`
    await loadSessions()
  } catch (requestError) {
    error.value = requestError.message || '取消会话失败'
  }
}

onMounted(() => {
  window.addEventListener('xya-header-action', onHeaderAction)
  load()
})

onBeforeUnmount(() => {
  recordsRequestGuard.invalidate()
  window.removeEventListener('xya-header-action', onHeaderAction)
})
</script>

<style scoped>
.dr-tabs {
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
  border-bottom: 1px solid #e6ecf5;
}
.dr-tab {
  position: relative;
  padding: 10px 18px;
  border: none;
  background: none;
  cursor: pointer;
  font-size: 14px;
  font-weight: 600;
  color: #526079;
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
}
.dr-tab.active {
  color: #1677ff;
  border-bottom-color: #1677ff;
}
.dr-tab-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 18px;
  height: 18px;
  padding: 0 5px;
  margin-left: 4px;
  border-radius: 9px;
  background: #ff4d4f;
  color: #fff;
  font-size: 11px;
  font-weight: 700;
}
.wrap {
  flex-wrap: wrap;
}

.narrow {
  max-width: 160px;
}

.grow {
  flex: 1 1 180px;
}

.refresh-status {
  margin-bottom: 10px;
  color: #526079;
  font-size: 13px;
}

.warning {
  background: #fff8e8;
  color: #8a4b08;
  border-color: #f7c97a;
}

.detail-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px 16px;
}

.panel-block {
  margin-top: 16px;
}

.section-title {
  margin-bottom: 8px;
  font-weight: 600;
}

.content-box {
  min-height: 56px;
  padding: 12px;
  border: 1px solid #e6ecf5;
  border-radius: 10px;
  background: #fbfdff;
  white-space: pre-wrap;
  word-break: break-word;
}

.content-box.compact {
  min-height: auto;
}

.cell-ellipsis {
  display: inline-block;
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.inline-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

.detail-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  padding-top: 12px;
  border-top: 1px solid #e6ecf5;
}

.retry-message {
  color: #526079;
  font-size: 13px;
}

.form-field {
  display: grid;
  gap: 6px;
  margin-bottom: 12px;
}

.success {
  background: #ecfdf3;
  color: #067647;
  border-color: #abefc6;
}

/* ───── 移动端适配 ───── */
@media (max-width: 900px) {
  /* 筛选工具栏：narrow / grow 全宽堆叠 */
  .narrow {
    max-width: 100%;
    width: 100%;
  }
  .grow {
    flex: 1 1 100%;
    width: 100%;
  }

  /* 详情双列网格 → 单列堆叠 */
  .detail-grid {
    grid-template-columns: minmax(0, 1fr);
    gap: 8px;
  }
  .detail-grid > * {
    min-width: 0;
  }

  /* 面板间距收窄 */
  .panel-block {
    margin-top: 12px;
  }
  .section-title {
    margin-bottom: 6px;
  }

  /* 内容框内边距收窄 */
  .content-box {
    min-height: 48px;
    padding: 10px;
  }

  /* 错误信息省略宽度收窄 */
  .cell-ellipsis {
    max-width: 140px;
  }

  .inline-actions {
    gap: 8px;
  }

  .form-field {
    gap: 6px;
    margin-bottom: 10px;
  }
}
.goods-cell { display: flex; align-items: center; gap: 8px; min-width: 120px; }
.goods-thumb { width: 40px; height: 40px; object-fit: cover; border-radius: 6px; flex-shrink: 0; }
</style>
