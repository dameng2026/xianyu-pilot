<template>
  <div>
    <div class="page-head">
      <div>
        <h1>自动发货</h1>
        <p>按商品配置自动发货时机，支持文本发货、卡密发货，以及引用货源库快速配置。</p>
      </div>
    </div>

    <div v-if="error" class="global-notice error">{{ error }}</div>
    <div v-if="dependenciesError" class="global-notice warn">{{ dependenciesError }}</div>
    <div v-if="statsError" class="global-notice warn">{{ statsError }}</div>
    <div v-if="sourcesError" class="global-notice warn">{{ sourcesError }}</div>
    <div v-if="success" class="global-notice success">{{ success }}</div>

    <div class="stat-row">
      <StatCard title="今日发货成功" :value="statsMetric(stats.todaySuccess)" change="今日实际记录" icon="shield" color="green" />
      <StatCard title="今日失败" :value="statsMetric(stats.todayFail)" change="今日实际记录" icon="warning" color="orange" />
      <StatCard title="待处理发货" :value="statsMetric(stats.pendingOrders)" change="待处理记录" icon="clock" />
      <StatCard title="库存不足" :value="statsMetric(stats.lowStockGoods)" change="需关注" icon="warning" color="red" />
      <StatCard title="已启用自动发货" :value="statsMetric(stats.enabledGoods)" change="已确认配置" icon="product" />
    </div>

    <div class="delivery-body">
      <div class="filter-panel">
        <CardPanel title="筛选条件">
          <div class="filter-section">
            <label class="filter-label">闲鱼账号</label>
            <select v-model="query.accountId" class="input" style="width:100%" @change="loadGoods">
              <option value="">全部账号</option>
              <option v-for="account in accounts" :key="account.id" :value="account.id">{{ accountName(account) }}</option>
            </select>
          </div>
          <div class="filter-section">
            <label class="filter-label">搜索商品</label>
            <input v-model="query.keyword" class="input" placeholder="标题 / ID" style="width:100%" @keyup.enter="loadGoods" />
          </div>
          <div class="filter-section">
            <label class="filter-label">发货形式</label>
            <select v-model="query.deliveryType" class="input" style="width:100%">
              <option value="">全部</option>
              <option value="text">文本发货</option>
              <option value="card">卡密发货</option>
              <option value="none">未配置</option>
            </select>
          </div>
          <div class="filter-section">
            <label class="filter-label">配置状态</label>
            <select v-model="query.configStatus" class="input" style="width:100%">
              <option value="">全部</option>
              <option value="configured">已配置</option>
              <option value="unconfigured">未配置</option>
            </select>
          </div>
          <div class="filter-section">
            <label class="filter-label">商品状态</label>
            <select v-model="query.goodsStatus" class="input" style="width:100%">
              <option value="">全部</option>
              <option value="0">在售</option>
              <option value="1">下架</option>
            </select>
          </div>
          <AppButton type="primary" style="width:100%;margin-top:8px" @click="applyFilter">应用筛选</AppButton>
          <AppButton style="width:100%;margin-top:6px" @click="resetFilter">重置筛选</AppButton>
        </CardPanel>

        <CardPanel title="快捷操作" style="margin-top:12px">
          <AppButton type="primary" style="width:100%;margin-bottom:8px" :disabled="!goodsAvailable || filteredConfigUnknownCount > 0" @click="showBatchDialog = true">批量设置</AppButton>
          <AppButton style="width:100%;margin-bottom:8px" @click="goSourceLibrary">打开货源库</AppButton>
          <AppButton type="danger" style="width:100%;margin-bottom:8px" :disabled="!goodsAvailable || filteredConfigUnknownCount > 0" @click="batchDelete">批量删除配置</AppButton>
          <AppButton style="width:100%;margin-bottom:8px" @click="scanPendingOrders">扫描待发货订单</AppButton>
          <AppButton style="width:100%" :loading="recoverLoading" @click="recoverPendingDeliveries">立即补发未发货订单</AppButton>
        </CardPanel>
      </div>

      <div class="main-content">
        <div class="timing-notice">
          <span class="timing-notice-icon">i</span>
          <span><b>付款后发货</b>会由系统定时扫描自动执行；<b>确认收货后赠送</b>和<b>好评后赠送</b>可在发货记录页手动触发，也可接入后续事件自动化。漏发的订单每 10 分钟自动补发一次，也可点击"立即补发未发货订单"手动触发。</span>
        </div>

        <CardPanel>
          <div class="toolbar" style="margin-bottom:12px">
            <span class="table-info">共 <b>{{ goodsAvailable ? filteredGoods.length : '—' }}</b> 个商品</span>
            <span style="margin-left:12px" class="subtle">点击状态列可快速进入对应时机配置。</span>
          </div>
          <div v-if="filteredConfigUnknownCount > 0" class="global-notice error">
            {{ filteredConfigUnknownCount }} 个商品的发货配置暂不可用；为避免覆盖未知配置，批量操作已禁用。
          </div>
          <EmptyState v-if="!goodsAvailable" icon="⚠️" title="商品与发货配置暂不可用" description="当前无法确认商品是否已配置自动发货。">
            <template #actions><AppButton @click="loadGoods">重新加载</AppButton></template>
          </EmptyState>
          <BaseTable v-else :columns="columns" :rows="tableRows">
            <template #goodsInfo="{ row }">
              <div class="goods-cell">
                <img v-if="row.imageUrl" :src="row.imageUrl" class="goods-thumb" alt="" />
                <div v-else class="goods-thumb placeholder"></div>
                <div class="goods-detail">
                  <div class="goods-title" :title="row.title">{{ row.title }}</div>
                  <div class="goods-meta">
                    <span>ID: {{ row.id }}</span>
                    <span class="price">{{ row.price }}</span>
                  </div>
                </div>
              </div>
            </template>
            <template #category="{ row }">
              <Badge>{{ row.category || '-' }}</Badge>
            </template>
            <template #account="{ row }">
              <span class="subtle">{{ accountName(row._account) || '-' }}</span>
            </template>
            <template #payDelivery="{ row }">
                <button type="button" class="delivery-status" :class="statusClass(row._config?.payDelivery, row._configUnavailable)" @click="openConfig(row, 'payDelivery')">
                <span class="status-dot" :class="statusDotClass(row._config?.payDelivery)"></span>
                {{ statusLabel(row._config?.payDelivery, row._configUnavailable) }}
              </button>
            </template>
            <template #confirmDelivery="{ row }">
              <button type="button" class="delivery-status" :class="statusClass(row._config?.confirmDelivery, row._configUnavailable)" @click="openConfig(row, 'confirmDelivery')">
                <span class="status-dot" :class="statusDotClass(row._config?.confirmDelivery)"></span>
                {{ statusLabel(row._config?.confirmDelivery, row._configUnavailable) }}
              </button>
            </template>
            <template #reviewDelivery="{ row }">
              <button type="button" class="delivery-status" :class="statusClass(row._config?.reviewDelivery, row._configUnavailable)" @click="openConfig(row, 'reviewDelivery')">
                <span class="status-dot" :class="statusDotClass(row._config?.reviewDelivery)"></span>
                {{ statusLabel(row._config?.reviewDelivery, row._configUnavailable) }}
              </button>
            </template>
            <template #op="{ row }">
              <button class="link" :disabled="row._configUnavailable" @click="openConfig(row, null)">配置</button>
              <button class="link danger-text" :disabled="row._configUnavailable" @click="removeConfig(row)">禁用</button>
            </template>
            <template #empty>
              <EmptyState icon="📦" title="暂无商品" description="请先同步商品，或调整当前筛选条件。">
                <template #actions>
                  <AppButton type="primary" @click="loadGoods">刷新数据</AppButton>
                </template>
              </EmptyState>
            </template>
          </BaseTable>
          <Pagination :total="filteredGoods.length" :current="current" :page-size="pageSize" @page-change="goPage" />
        </CardPanel>

        <div v-if="configTarget" class="modal-overlay" @click.self="closeConfig">
          <div class="modal-content config-modal">
            <div class="config-modal-header">
              <div class="config-modal-title">
                <h3>配置自动发货</h3>
                <p class="subtle">商品：{{ configTarget.goods.title }}（ID：{{ configTarget.goods.id }}）</p>
              </div>
              <button class="config-modal-close" type="button" aria-label="关闭" @click="closeConfig">×</button>
            </div>

          <div class="config-tabs">
            <button v-for="timing in configTimings" :key="timing.key" :class="['config-tab', { active: configTiming === timing.key }]" @click="switchTiming(timing.key)">
              {{ timing.label }}
            </button>
            <button v-if="skuList.length" :class="['config-tab', { active: configTiming === 'skuRules' }]" @click="switchTiming('skuRules')">
              多规格配置
            </button>
          </div>

          <div v-if="configTiming === 'skuRules'" class="form-grid sku-rules-grid">
            <div class="form-section">
              <div class="form-section-title">
                多规格发货配置
                <span class="subtle" v-if="skuList.length">（共 {{ skuList.length }} 个 SKU）</span>
              </div>
              <p v-if="skuError" class="danger-text">{{ skuError }}</p>
              <p v-else-if="!skuList.length && !skuLoading" class="subtle">该商品暂无 SKU 数据。</p>
              <p v-else class="subtle">为每个 SKU 独立配置「付款后发货 / 确认收货后赠送 / 好评后赠送」三个时机的发货规则；未命中时回退到商品通用配置。</p>
              <div class="toolbar" style="justify-content:flex-start; margin-bottom: 12px;">
                <AppButton size="small" :disabled="!isMultiSpecGoods" @click="applyToAllSkus('payDelivery')">应用通用付款配置</AppButton>
                <AppButton size="small" :disabled="!isMultiSpecGoods" @click="applyToAllSkus('confirmDelivery')">应用通用收货配置</AppButton>
                <AppButton size="small" :disabled="!isMultiSpecGoods" @click="applyToAllSkus('reviewDelivery')">应用通用好评配置</AppButton>
                <span v-if="skuSuccess" class="sku-success-text">{{ skuSuccess }}</span>
              </div>
            </div>
            <div v-if="skuLoading" class="form-section"><p class="subtle">加载 SKU 数据中...</p></div>
            <div v-else>
              <div v-for="(rule, idx) in skuRules" :key="rule.skuId || idx" class="sku-card">
                <div class="sku-card-header">
                  <div class="sku-card-title">{{ rule.propertyText || rule.propertyKey || ('SKU ' + (idx + 1)) }}</div>
                  <span class="subtle">SKU ID: {{ rule.skuId }}</span>
                </div>
                <div class="sku-timing-grid">
                  <div v-for="timing in configTimings" :key="timing.key" class="sku-timing-cell">
                    <div class="sku-timing-header">
                      <label class="checkbox-label">
                        <input type="checkbox" :checked="rule[timing.key].enabled === 1" @change="toggleSkuTiming(rule, timing.key, $event.target.checked)" />
                        {{ timing.label }}
                      </label>
                    </div>
                    <div v-if="rule[timing.key].enabled === 1" class="sku-timing-body">
                      <div class="form-row">
                        <label>发货模式</label>
                        <select v-model="rule[timing.key].mode" class="input" style="max-width:180px">
                          <option value="text">文本发货</option>
                          <option value="card">卡密发货</option>
                        </select>
                      </div>
                      <div v-if="rule[timing.key].mode === 'text'" class="form-row">
                        <label>关联货源库</label>
                        <select v-model="rule[timing.key].sourceId" class="input" style="max-width:280px" :disabled="sourcesAvailable === false">
                          <option value="">不关联</option>
                          <option v-for="source in textSources" :key="source.id" :value="source.id">{{ source.title }}</option>
                        </select>
                      </div>
                      <div v-if="rule[timing.key].mode === 'card'" class="form-row">
                        <label>卡密分组</label>
                        <select v-model="rule[timing.key].cardGroupId" class="input" style="max-width:280px" :disabled="cardGroupsAvailable === false">
                          <option value="">请选择</option>
                          <option v-for="group in cardGroups" :key="group.id" :value="group.id">{{ group.groupName }}（余 {{ group.remainCount || 0 }}）</option>
                        </select>
                      </div>
                      <div class="form-row">
                        <label>{{ rule[timing.key].mode === 'text' ? '发货内容' : '卡密模板' }}</label>
                        <textarea
                          v-if="rule[timing.key].mode === 'text'"
                          v-model="rule[timing.key].content"
                          rows="2"
                          style="width:100%"
                          placeholder="买家将收到的发货内容"
                        ></textarea>
                        <textarea
                          v-else
                          v-model="rule[timing.key].cardTemplate"
                          rows="2"
                          style="width:100%"
                          placeholder="例如：您的卡密为：{卡密}"
                        ></textarea>
                      </div>
                      <div class="form-row">
                        <label>自动确认发货</label>
                        <label class="checkbox-label">
                          <input type="checkbox" v-model="rule[timing.key].autoConfirmShipment" />
                          发送成功后确认发货
                        </label>
                      </div>
                    </div>
                    <div v-else class="sku-timing-body-disabled">
                      未启用，将回退到商品通用「{{ timing.label }}」配置
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <div v-if="configTiming !== 'skuRules'" class="form-grid">
            <div class="form-section">
              <div class="form-section-title">基础设置</div>
              <div class="form-row">
                <label>启用{{ currentTimingLabel }}</label>
                <select v-model.number="configForm.enabled" class="input" style="max-width:200px">
                  <option :value="1">启用</option>
                  <option :value="0">停用</option>
                </select>
              </div>

              <div class="form-row">
                <label>发货模式</label>
                <select v-model="configForm.mode" class="input" style="max-width:220px">
                  <option value="text">文本发货</option>
                  <option value="card">卡密发货</option>
                </select>
              </div>
            </div>

            <div v-if="configForm.mode === 'text'" class="form-section">
              <div class="form-section-title">发货内容</div>
              <div class="form-row">
                <label>关联货源库</label>
                <div class="toolbar" style="justify-content:flex-start">
                <select v-model="configForm.sourceId" class="input" style="max-width:320px" :disabled="sourcesAvailable === false">
                  <option value="">不使用货源库，直接手写内容</option>
                  <option v-for="source in textSources" :key="source.id" :value="source.id">{{ source.title }}</option>
                </select>
                <AppButton @click="goSourceLibrary">管理货源库</AppButton>
              </div>
              <div v-if="sourcesAvailable === false" class="global-notice error">货源库暂不可用，无法确认可关联的文本货源。</div>
              <div v-if="configForm.sourceId" class="subtle">
                已关联货源：{{ sourceTitle(configForm.sourceId) }}
              </div>
            </div>

              <div class="form-row">
                <label>正文内容</label>
                <textarea
                  v-model="configForm.content"
                  rows="5"
                  :placeholder="configForm.sourceId ? '已引用货源库正文，可继续补充或覆盖' : '请输入买家将收到的发货内容'"
                ></textarea>
              </div>
            </div>

            <div v-if="configForm.mode === 'card'" class="form-section">
              <div class="form-section-title">卡密配置</div>
              <div class="form-row">
                <label>卡密来源</label>
                <select v-model="configForm.cardSource" class="input" style="max-width:320px">
                  <option value="existing">绑定已有卡密分组</option>
                  <option value="direct">直接录入卡密（自动入库）</option>
                </select>
              </div>

              <div v-if="configForm.cardSource === 'existing'" class="form-row">
                <label>绑定卡密分组</label>
                <select v-model="configForm.cardGroupId" class="input" style="max-width:320px" :disabled="cardGroupsAvailable === false">
                  <option value="">请选择</option>
                  <option v-for="group in cardGroups" :key="group.id" :value="group.id">{{ group.groupName }}（余 {{ group.remainCount || 0 }}）</option>
                </select>
                <div v-if="cardGroupsAvailable === false" class="global-notice error">卡密分组暂不可用，无法安全保存卡密发货配置。</div>
              </div>

              <div v-if="configForm.cardSource === 'direct'" class="form-row">
                <label>录入卡密（每行一条，支持换行）</label>
                <textarea
                  v-model="configForm.cardKeysText"
                  rows="8"
                  placeholder="每行一条卡密，支持换行录入；例如：&#10;CARD-AAAA-BBBB&#10;CARD-CCCC-DDDD&#10;支持 ---- 或逗号分隔卡号和密码"
                ></textarea>
                <span class="subtle">已录入 <b>{{ cardKeyCount }}</b> 条卡密，保存后将自动创建卡密分组并入库，归属于当前商品「{{ configTarget.goods.title }}」。用户付款后系统将自动发放一张卡密并扣减库存。</span>
              </div>

              <div class="form-row">
                <label>卡密模板</label>
                <textarea v-model="configForm.cardTemplate" rows="3" placeholder="例如：您的卡密为：{卡密}"></textarea>
              </div>
            </div>

            <div class="form-section">
              <div class="form-section-title">消息样式</div>
              <div class="form-row">
                <label>消息头部</label>
                <textarea v-model="configForm.header" rows="2" placeholder="可选，发货正文前的说明"></textarea>
              </div>

              <div v-if="configForm.mode === 'card'" class="form-row">
                <label>消息底部</label>
                <textarea
                  v-model="configForm.footer"
                  rows="2"
                  placeholder="可选，卡密内容后的补充说明"
                ></textarea>
              </div>

              <div class="form-row">
                <label>分段发送</label>
                <label class="checkbox-label">
                  <input v-model="configForm.segmentSend" type="checkbox" />
                  使用 `{分段}` 拆成多条消息发送
                </label>
              </div>
            </div>

            <div class="form-section">
              <div class="form-section-title">高级设置</div>
              <div class="form-row">
                <label>失败重试次数</label>
                <input v-model.number="configForm.retryCount" type="number" min="0" max="10" class="input" style="max-width:120px" />
              </div>

              <div class="form-row">
                <label>库存预警阈值</label>
                <input v-model.number="configForm.alertThreshold" type="number" min="0" class="input" style="max-width:120px" />
              </div>

              <div class="form-row">
                <label>库存不足自动停用</label>
                <label class="checkbox-label">
                  <input v-model="configForm.autoDisableOnLowStock" type="checkbox" />
                  自动停用
                </label>
              </div>

              <div class="form-row">
                <label>自动确认发货</label>
                <label class="checkbox-label">
                  <input v-model="configForm.autoConfirmShipment" type="checkbox" />
                  发送成功后调用闲鱼平台确认发货，并把订单标记为已发货
                </label>
              </div>
            </div>
          </div>

          <div class="config-modal-footer">
            <AppButton @click="closeConfig">取消</AppButton>
            <AppButton v-if="configTiming === 'skuRules'" type="primary" :loading="skuSaving" @click="saveSkuRules">保存 SKU 配置</AppButton>
            <AppButton v-else type="primary" :loading="configSaving" :disabled="configSaveDisabled" @click="saveConfig">保存配置</AppButton>
          </div>
          </div>
        </div>
      </div>
    </div>

    <div v-if="showBatchDialog" class="modal-overlay" @click.self="showBatchDialog = false">
      <div class="modal-content">
        <h3>批量设置发货配置</h3>
        <p class="subtle">将影响 <b>{{ filteredGoods.length }}</b> 个商品</p>
        <div class="form-grid">
          <div class="form-row">
            <label>发货时机</label>
            <select v-model="batchForm.action" class="input">
              <option value="payDelivery">付款后发货</option>
              <option value="confirmDelivery">确认收货后赠送</option>
              <option value="reviewDelivery">好评后赠送</option>
            </select>
          </div>
          <div class="form-row">
            <label>启用状态</label>
            <select v-model.number="batchForm.enabled" class="input">
              <option :value="1">启用</option>
              <option :value="0">停用</option>
            </select>
          </div>
          <div class="form-row">
            <label>发货模式</label>
            <select v-model="batchForm.mode" class="input">
              <option value="">保持不变</option>
              <option value="text">文本发货</option>
              <option value="card">卡密发货</option>
            </select>
          </div>
          <div v-if="batchForm.mode === 'card'" class="form-row">
            <label>卡密分组</label>
            <select v-model="batchForm.cardGroupId" class="input">
              <option value="">请选择</option>
              <option v-for="group in cardGroups" :key="group.id" :value="group.id">{{ group.groupName }}</option>
            </select>
          </div>
          <div v-if="batchForm.mode === 'text'" class="form-row">
            <label>货源库</label>
            <select v-model="batchForm.sourceId" class="input">
              <option value="">不指定货源库</option>
              <option v-for="source in textSources" :key="source.id" :value="source.id">{{ source.title }}</option>
            </select>
          </div>
        </div>
        <div class="toolbar" style="justify-content:flex-end">
          <AppButton @click="showBatchDialog = false">取消</AppButton>
          <AppButton type="primary" :loading="batchLoading" :disabled="batchSubmitDisabled" @click="submitBatch">确认执行</AppButton>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import StatCard from '../components/StatCard.vue'
import CardPanel from '../components/CardPanel.vue'
import Badge from '../components/Badge.vue'
import AppButton from '../components/AppButton.vue'
import BaseTable from '../components/BaseTable.vue'
import EmptyState from '../components/EmptyState.vue'
import Pagination from '../components/Pagination.vue'
import { confirmAction } from '../utils/confirmAction.js'
import { getAccounts } from '../api/accounts.js'
import { getGoods } from '../api/goods.js'
import { getCards, createCard, batchCreateCardItems } from '../api/cards.js'
import {
  batchDeleteDeliveryRules,
  batchSetDeliveryRules,
  getDeliverySources,
  getDeliveryStats,
  getGoodsDeliveryConfigs,
  saveGoodsDeliveryConfig,
  scanPendingOrders as scanApi,
  recoverPendingDeliveries as recoverApi,
  getGoodsSkus,
  getGoodsSkuRules,
  saveGoodsSkuRules
} from '../api/autoDelivery.js'
import { accountName } from '../utils/format.js'
import { recordsOf } from '../utils/apiData.js'

const emit = defineEmits(['navigate'])
const AUTO_DELIVERY_FOCUS_GOODS_KEY = 'xya:auto-delivery-focus-goods-id'
const accounts = ref([])
const cardGroups = ref([])
const textSources = ref([])
const allGoods = ref([])
const error = ref('')
const success = ref('')
const statsError = ref('')
const sourcesError = ref('')
const dependenciesError = ref('')
const statsAvailable = ref(false)
const goodsAvailable = ref(false)
const sourcesAvailable = ref(null)
const accountsAvailable = ref(null)
const cardGroupsAvailable = ref(null)
const configSaving = ref(false)
const showBatchDialog = ref(false)
const skuList = ref([])
const skuRules = ref([])
const skuLoading = ref(false)
const skuSaving = ref(false)
const skuError = ref('')
const skuSuccess = ref('')
const isMultiSpecGoods = computed(() => Array.isArray(skuList.value) && skuList.value.length > 0)
const batchLoading = ref(false)
const current = ref(1)
const pageSize = ref(20)

const stats = reactive({
  todaySuccess: 0,
  todayFail: 0,
  pendingOrders: 0,
  lowStockGoods: 0,
  enabledGoods: 0
})

const query = reactive({
  accountId: '',
  keyword: '',
  deliveryType: '',
  configStatus: '',
  goodsStatus: ''
})

const configTarget = ref(null)
const configTiming = ref('payDelivery')
const configTimings = [
  { key: 'payDelivery', label: '付款后发货' },
  { key: 'confirmDelivery', label: '确认收货后赠送' },
  { key: 'reviewDelivery', label: '好评后赠送' }
]

const configForm = reactive({
  enabled: 1,
  mode: 'text',
  sourceId: '',
  cardGroupId: '',
  cardSource: 'existing',
  cardKeysText: '',
  sourceTitle: '',
  cardTemplate: '',
  header: '',
  content: '',
  footer: '',
  segmentSend: false,
  retryCount: 3,
  alertThreshold: 5,
  autoDisableOnLowStock: false,
  // 卡密/文本发送成功后是否调用闲鱼平台接口确认发货并把订单标记为已发货。
  // 不开启时只发买家消息，订单状态需手动同步。
  autoConfirmShipment: false
})

const batchForm = reactive({
  action: 'payDelivery',
  enabled: 1,
  mode: '',
  cardGroupId: '',
  sourceId: ''
})

const columns = [
  { key: 'goodsInfo', title: '商品信息' },
  { key: 'category', title: '分类' },
  { key: 'account', title: '所属账号' },
  { key: 'payDelivery', title: '付款后发货' },
  { key: 'confirmDelivery', title: '确认收货后赠送' },
  { key: 'reviewDelivery', title: '好评后赠送' },
  { key: 'op', title: '操作' }
]

const currentTimingLabel = computed(() => configTimings.find(item => item.key === configTiming.value)?.label || '')

const cardKeyCount = computed(() => {
  return String(configForm.cardKeysText || '')
    .split(/\n+/)
    .map(s => s.trim())
    .filter(Boolean)
    .length
})

const filteredGoods = computed(() => {
  return allGoods.value.filter(goods => {
    if (query.accountId && String(goods.accountId) !== String(query.accountId)) return false
    if (query.keyword) {
      const keyword = query.keyword.toLowerCase()
      if (!String(goods.title || '').toLowerCase().includes(keyword) && !String(goods.id).includes(keyword)) return false
    }
    if (query.goodsStatus !== '' && String(goods.status) !== String(query.goodsStatus)) return false

    const cfg = goods._config || {}
    const timings = [cfg.payDelivery, cfg.confirmDelivery, cfg.reviewDelivery].filter(Boolean)
    const hasText = timings.some(item => item.mode === 'text')
    const hasCard = timings.some(item => item.mode === 'card')

    if (query.deliveryType === 'text' && !hasText) return false
    if (query.deliveryType === 'card' && !hasCard) return false
    if (query.deliveryType === 'none' && timings.length > 0) return false

    if (query.configStatus === 'configured' && timings.length === 0) return false
    if (query.configStatus === 'unconfigured' && timings.length > 0) return false

    return true
  })
})

const filteredConfigUnknownCount = computed(() => (
  filteredGoods.value.filter(goods => goods._configUnavailable).length
))

const hasUnavailableGoods = computed(() => filteredConfigUnknownCount.value > 0)

const configSaveDisabled = computed(() => {
  if (!configTarget.value) return true
  if (configTarget.value.goods?._configUnavailable) return true
  if (configForm.mode === 'card') {
    if (configForm.cardSource === 'existing' && cardGroupsAvailable.value === false) return true
    if (configForm.cardSource === 'existing' && !configForm.cardGroupId) return true
    if (configForm.cardSource === 'direct' && cardKeyCount.value === 0) return true
  }
  if (configForm.mode === 'text' && configForm.sourceId && sourcesAvailable.value === false) return true
  return false
})

const batchSubmitDisabled = computed(() => {
  if (!goodsAvailable.value || hasUnavailableGoods.value) return true
  if (!filteredGoods.value.length) return true
  if (batchForm.mode === 'card' && (!batchForm.cardGroupId || cardGroupsAvailable.value === false)) return true
  return false
})

const tableRows = computed(() => {
  const start = (current.value - 1) * pageSize.value
  return filteredGoods.value.slice(start, start + pageSize.value).map(goods => ({
    ...goods,
    _config: goods._config || {},
    _account: accounts.value.find(account => String(account.id) === String(goods.accountId))
  }))
})

watch(() => configForm.sourceId, value => {
  const source = textSources.value.find(item => String(item.id) === String(value))
  if (source) {
    configForm.sourceTitle = source.title
    if (!configForm.content || configForm.content === configForm._lastSourceContent) {
      configForm.content = source.content || ''
      configForm._lastSourceContent = source.content || ''
    }
  } else {
    configForm.sourceTitle = ''
  }
})

function goPage(page) {
  current.value = page
}

function sourceTitle(id) {
  return textSources.value.find(item => String(item.id) === String(id))?.title || ''
}

function statusLabel(cfg, unavailable = false) {
  if (unavailable) return '状态未知'
  if (!cfg) return '未配置'
  if (Number(cfg.enabled) === 0) return '已停用'
  if (cfg.sourceId) return `货源：${cfg.sourceTitle || sourceTitle(cfg.sourceId) || '已关联'}`
  return cfg.mode === 'card' ? '卡密发货' : '文本发货'
}

function statusClass(cfg, unavailable = false) {
  if (unavailable) return 'status-disabled'
  if (!cfg) return 'status-none'
  if (Number(cfg.enabled) === 0) return 'status-disabled'
  return 'status-enabled'
}

function statusDotClass(cfg) {
  if (!cfg) return 'dot-gray'
  if (Number(cfg.enabled) === 0) return 'dot-gray'
  return 'dot-green'
}

async function loadStats() {
  statsAvailable.value = false
  statsError.value = ''
  try {
    const res = await getDeliveryStats()
    const data = res.data || {}
    Object.assign(stats, {
      todaySuccess: data.todaySuccess || 0,
      todayFail: data.todayFail || 0,
      pendingOrders: data.pendingOrders || 0,
      lowStockGoods: data.lowStockGoods || 0,
      enabledGoods: data.enabledGoods || 0
    })
    statsAvailable.value = true
  } catch (e) {
    statsError.value = `自动发货统计暂不可用，未加载指标以"—"显示。${e.message ? ` ${e.message}` : ''}`
  }
}

function statsMetric(value) {
  return statsAvailable.value ? value : '—'
}

function appendError(message) {
  const text = String(message || '').trim()
  if (!text) return
  const parts = error.value ? error.value.split('；') : []
  if (!parts.includes(text)) error.value = [...parts, text].filter(Boolean).join('；')
}

async function loadSources() {
  sourcesAvailable.value = null
  sourcesError.value = ''
  try {
    const res = await getDeliverySources({ current: 1, size: 200 })
    textSources.value = recordsOf(res.data)
    sourcesAvailable.value = true
  } catch (e) {
    textSources.value = []
    sourcesAvailable.value = false
    sourcesError.value = `文本货源库暂不可用。${e.message ? ` ${e.message}` : ''}`
  }
}

async function loadAll() {
  error.value = ''
  dependenciesError.value = ''
  const [accountResult, cardResult] = await Promise.allSettled([
    getAccounts({ page: 1, pageSize: 200 }),
    getCards({ size: 200 })
  ])
  const depErrors = []
  if (accountResult.status === 'fulfilled') {
    accounts.value = recordsOf(accountResult.value.data)
    accountsAvailable.value = true
  } else {
    accounts.value = []
    accountsAvailable.value = false
    depErrors.push('账号筛选暂不可用')
  }
  if (cardResult.status === 'fulfilled') {
    cardGroups.value = recordsOf(cardResult.value.data)
    cardGroupsAvailable.value = true
  } else {
    cardGroups.value = []
    cardGroupsAvailable.value = false
    depErrors.push('卡密分组暂不可用')
  }
  if (depErrors.length) {
    dependenciesError.value = `${depErrors.join('；')}。相关筛选与卡密发货可能受影响。`
  }
  await Promise.allSettled([loadSources(), loadGoods(), loadStats()])
}

async function loadGoods() {
  goodsAvailable.value = false
  try {
    current.value = 1
    const fetchSize = 500
    const validEnabledValues = [true, false, 0, 1]
    const validModeValues = ['text', 'card']
    const withConfig = []
    let page = 1
    let expectedPages = 1
    let configFailures = 0
    let invalidConfigs = 0
    do {
      const res = await getGoods({ current: page, size: fetchSize })
      const list = recordsOf(res.data)
      const totalCount = Number(res.data?.total ?? list.length)
      expectedPages = Math.max(1, Math.ceil(totalCount / fetchSize))
      const goodsIds = list.map(goods => Number(goods.id)).filter(Number.isInteger)
      let configMap = new Map()
      try {
        if (goodsIds.length) {
          const configRes = await getGoodsDeliveryConfigs(goodsIds)
          configMap = new Map(recordsOf(configRes.data).map(item => [String(item.goodsId), item.config || {}]))
        }
      } catch {
        configFailures += list.length
      }
      for (const goods of list) {
        const hasConfigResult = configMap.has(String(goods.id))
        if (!hasConfigResult && configMap.size) configFailures += 1
        let configUnavailable = goodsIds.length > 0 && !hasConfigResult
        let configData = hasConfigResult ? configMap.get(String(goods.id)) : {}
        if (configData && typeof configData === 'object') {
          for (const timingKey of ['payDelivery', 'confirmDelivery', 'reviewDelivery']) {
            const timing = configData[timingKey]
            if (timing && typeof timing === 'object' && Object.keys(timing).length > 0) {
              const enabled = timing.enabled
              const mode = timing.mode
              if (enabled !== undefined && !validEnabledValues.includes(enabled)) {
                configUnavailable = true
                invalidConfigs += 1
                break
              }
              if (mode !== undefined && mode !== '' && !validModeValues.includes(mode)) {
                configUnavailable = true
                invalidConfigs += 1
                break
              }
            }
          }
        }
        withConfig.push({
          ...goods,
          _config: configData,
          _configUnavailable: configUnavailable
        })
      }
      if (!list.length || list.length < fetchSize) break
      page += 1
    } while (page <= expectedPages)

    allGoods.value = withConfig
    goodsAvailable.value = true
    if (configFailures) appendError(`${configFailures} 个商品的发货配置暂不可用。`)
    if (invalidConfigs) appendError(`${invalidConfigs} 个商品的发货配置字段异常（enabled 或 mode 非法），已标记为不可用。`)
    const focusedGoodsId = consumeFocusedGoodsId()
    if (focusedGoodsId) {
      const target = withConfig.find(goods => String(goods.id) === String(focusedGoodsId))
      if (target) {
        focusGoodsConfig(target)
      }
    }
  } catch (e) {
    allGoods.value = []
    goodsAvailable.value = false
    appendError(e.message || '商品加载失败')
  }
}

function applyFilter() {
  current.value = 1
}

function resetFilter() {
  Object.assign(query, {
    accountId: '',
    keyword: '',
    deliveryType: '',
    configStatus: '',
    goodsStatus: ''
  })
  current.value = 1
}

function fillConfigForm(config = {}) {
  Object.assign(configForm, {
    enabled: config.enabled !== undefined ? Number(config.enabled) : 1,
    mode: config.mode || 'text',
    sourceId: config.sourceId || '',
    sourceTitle: config.sourceTitle || '',
    cardGroupId: config.cardGroupId || '',
    cardSource: config.cardGroupId ? 'existing' : (config.cardSource || 'existing'),
    cardKeysText: '',
    cardTemplate: config.cardTemplate || '',
    header: config.header || '',
    content: config.content || '',
    footer: config.footer || '',
    segmentSend: !!config.segmentSend,
    retryCount: config.retryCount ?? 3,
    alertThreshold: config.alertThreshold ?? 5,
    autoDisableOnLowStock: !!config.autoDisableOnLowStock,
    autoConfirmShipment: !!config.autoConfirmShipment,
    _lastSourceContent: config.content || ''
  })
}

function consumeFocusedGoodsId() {
  const goodsId = sessionStorage.getItem(AUTO_DELIVERY_FOCUS_GOODS_KEY)
  if (goodsId) {
    sessionStorage.removeItem(AUTO_DELIVERY_FOCUS_GOODS_KEY)
  }
  return goodsId
}

function focusGoodsConfig(goods) {
  if (!goods) return false
  const config = goods._config || {}
  const preferredTiming = configTimings.find(item => config?.[item.key] && typeof config[item.key] === 'object' && Object.keys(config[item.key]).length > 0)?.key || 'payDelivery'
  if (!openConfig(goods, preferredTiming)) return false
  success.value = `已定位到商品“${goods.title || goods.id}”的自动发货配置`
  return true
}

function openConfig(goods, timing) {
  if (goods?._configUnavailable) {
    appendError('该商品发货配置状态未知，为避免覆盖已有规则，暂不允许编辑。')
    return false
  }
  configTarget.value = { goods }
  configTiming.value = timing || 'payDelivery'
  fillConfigForm(goods._config?.[configTiming.value] || {})
  // 异步加载 SKU 数据（多规格发货功能），失败时不阻断配置弹窗
  loadSkuData(goods.id)
  return true
}

function switchTiming(timing) {
  configTiming.value = timing
  if (timing === 'skuRules') return  // SKU 配置 tab 不复用主表单
  fillConfigForm(configTarget.value?.goods?._config?.[timing] || {})
}

function closeConfig() {
  configTarget.value = null
  skuList.value = []
  skuRules.value = []
  skuError.value = ''
  skuSuccess.value = ''
}

async function loadSkuData(goodsId) {
  skuList.value = []
  skuRules.value = []
  skuError.value = ''
  if (!goodsId) return
  skuLoading.value = true
  try {
    const [skuRes, rulesRes] = await Promise.all([
      getGoodsSkus(goodsId),
      getGoodsSkuRules(goodsId)
    ])
    const skus = recordsOf(skuRes.data) || []
    const rules = rulesRes.data
    skuList.value = skus
    skuRules.value = Array.isArray(rules) ? mergeSkuRules(skus, rules) : []
  } catch (e) {
    // SKU 接口失败不影响主配置流程，仅记录告警
    skuError.value = e?.message || 'SKU 信息加载失败，多规格配置暂不可用'
    skuList.value = []
    skuRules.value = []
  } finally {
    skuLoading.value = false
  }
}

// 将 SKU 列表与已保存的 SKU 规则合并，确保每个 SKU 都有一条含三时机配置的可编辑规则
function mergeSkuRules(skus, savedRules) {
  const ruleMap = new Map()
  for (const rule of savedRules) {
    if (rule && rule.skuId) ruleMap.set(String(rule.skuId), rule)
  }
  return skus.map(sku => {
    const saved = ruleMap.get(String(sku.skuId)) || {}
    return {
      skuId: sku.skuId,
      propertyKey: sku.propertyKey || saved.propertyKey || '',
      propertyText: sku.propertyText || saved.propertyText || '',
      payDelivery: normalizeSkuTimingConfig(saved.payDelivery),
      confirmDelivery: normalizeSkuTimingConfig(saved.confirmDelivery),
      reviewDelivery: normalizeSkuTimingConfig(saved.reviewDelivery)
    }
  })
}

function normalizeSkuTimingConfig(raw) {
  const cfg = raw && typeof raw === 'object' ? raw : {}
  return {
    enabled: cfg.enabled === 1 || cfg.enabled === true ? 1 : 0,
    mode: ['text', 'card'].includes(cfg.mode) ? cfg.mode : 'text',
    sourceId: cfg.sourceId || '',
    sourceTitle: cfg.sourceTitle || '',
    cardGroupId: cfg.cardGroupId || '',
    cardTemplate: cfg.cardTemplate || '',
    header: cfg.header || '',
    content: cfg.content || '',
    footer: cfg.footer || '',
    autoConfirmShipment: !!cfg.autoConfirmShipment
  }
}

// 切换 SKU 单个时机的启用状态（启用时若无默认值则给 text 模式）
function toggleSkuTiming(rule, timing, enabled) {
  const cfg = rule[timing]
  cfg.enabled = enabled ? 1 : 0
  if (!enabled) return
  if (!cfg.mode) cfg.mode = 'text'
}

// 批量应用：将商品通用配置应用到所有 SKU 的指定时机
function applyToAllSkus(timing) {
  if (!isMultiSpecGoods.value) return
  const sourceConfig = configTarget.value?.goods?._config?.[timing] || {}
  const template = normalizeSkuTimingConfig(sourceConfig)
  template.enabled = sourceConfig.enabled === 1 || sourceConfig.enabled === true ? 1 : 0
  for (const rule of skuRules.value) {
    rule[timing] = JSON.parse(JSON.stringify(template))
  }
  skuSuccess.value = `已将「${currentTimingLabelFor(timing)}」的通用配置应用到全部 ${skuRules.value.length} 个 SKU`
  setTimeout(() => { skuSuccess.value = '' }, 3000)
}

function currentTimingLabelFor(timing) {
  return configTimings.find(item => item.key === timing)?.label || timing
}

async function saveSkuRules() {
  if (!configTarget.value || !isMultiSpecGoods.value) return
  skuSaving.value = true
  error.value = ''
  success.value = ''
  skuError.value = ''
  skuSuccess.value = ''
  try {
    // 仅提交启用了至少一个时机的 SKU 规则；未启用的 SKU 不写入（运行时回退商品通用配置）
    const payload = skuRules.value
      .filter(rule => ['payDelivery', 'confirmDelivery', 'reviewDelivery']
        .some(t => rule[t]?.enabled === 1))
      .map(rule => ({
        skuId: rule.skuId,
        propertyKey: rule.propertyKey,
        propertyText: rule.propertyText,
        payDelivery: cleanSkuTimingForSave(rule.payDelivery),
        confirmDelivery: cleanSkuTimingForSave(rule.confirmDelivery),
        reviewDelivery: cleanSkuTimingForSave(rule.reviewDelivery)
      }))
    await saveGoodsSkuRules(configTarget.value.goods.id, payload)
    skuSuccess.value = `已保存 ${payload.length} 个 SKU 的发货规则`
    await loadSkuData(configTarget.value.goods.id)
    await loadGoods()
  } catch (e) {
    skuError.value = e.message || '保存 SKU 配置失败'
    error.value = e.message || '保存 SKU 配置失败'
  } finally {
    skuSaving.value = false
  }
}

function cleanSkuTimingForSave(cfg) {
  const c = cfg || {}
  return {
    enabled: c.enabled === 1 ? 1 : 0,
    mode: c.mode || 'text',
    sourceId: c.mode === 'text' && c.sourceId ? Number(c.sourceId) : null,
    sourceTitle: c.mode === 'text' ? (c.sourceTitle || '') : '',
    cardGroupId: c.mode === 'card' && c.cardGroupId ? Number(c.cardGroupId) : null,
    cardTemplate: c.cardTemplate || '',
    header: c.header || '',
    content: c.content || '',
    footer: c.footer || '',
    autoConfirmShipment: !!c.autoConfirmShipment
  }
}

async function saveConfig() {
  if (!configTarget.value) return
  if (configTarget.value.goods?._configUnavailable) {
    appendError('该商品发货配置状态未知，暂不允许保存。')
    return
  }
  if (configForm.mode === 'card' && cardGroupsAvailable.value === false && configForm.cardSource === 'existing') {
    appendError('卡密分组暂不可用，无法安全保存卡密发货配置。')
    return
  }
  if (configForm.mode === 'card' && configForm.cardSource === 'existing' && !configForm.cardGroupId) {
    appendError('卡密发货必须选择卡密分组。')
    return
  }
  if (configForm.mode === 'card' && configForm.cardSource === 'direct' && cardKeyCount.value === 0) {
    appendError('直接录入卡密时至少输入一条卡密。')
    return
  }
  if (configForm.mode === 'text' && configForm.sourceId && sourcesAvailable.value === false) {
    appendError('文本货源库暂不可用，无法确认当前关联货源。')
    return
  }
  configSaving.value = true
  error.value = ''
  success.value = ''
  try {
    let cardGroupId = null
    if (configForm.mode === 'card') {
      if (configForm.cardSource === 'direct') {
        const goods = configTarget.value.goods
        const groupName = `商品-${goods.title || goods.id}-卡密组`
        const groupRes = await createCard({
          groupName,
          cardType: 'unique',
          alertThreshold: configForm.alertThreshold || 5,
          status: 1,
          remark: `由自动发货配置自动创建，归属商品ID：${goods.id}，标题：${goods.title || ''}`
        })
        cardGroupId = groupRes?.data?.id || groupRes?.id
        if (!cardGroupId) {
          throw new Error('创建卡密分组失败，未返回分组ID')
        }
        const lines = String(configForm.cardKeysText || '')
          .split(/\n+/)
          .map(s => s.trim())
          .filter(Boolean)
        const payload = lines.map(line => {
          const sepIdx = line.indexOf('----')
          if (sepIdx > 0) {
            return { content: line, cardContent: line.slice(0, sepIdx), password: line.slice(sepIdx + 4) }
          }
          const commaIdx = line.indexOf(',')
          if (commaIdx > 0) {
            return { content: line, cardContent: line.slice(0, commaIdx), password: line.slice(commaIdx + 1) }
          }
          const tabIdx = line.indexOf('\t')
          if (tabIdx > 0) {
            return { content: line, cardContent: line.slice(0, tabIdx), password: line.slice(tabIdx + 1) }
          }
          return { content: line }
        })
        await batchCreateCardItems(cardGroupId, { items: payload })
        configForm.cardGroupId = cardGroupId
        try {
          const cardRes = await getCards({ size: 200 })
          cardGroups.value = recordsOf(cardRes.data)
          cardGroupsAvailable.value = true
        } catch { /* 刷新卡密分组失败不影响保存 */ }
      } else {
        cardGroupId = Number(configForm.cardGroupId) || null
      }
    }
    await saveGoodsDeliveryConfig(configTarget.value.goods.id, {
      timing: configTiming.value,
      enabled: configForm.enabled,
      mode: configForm.mode,
      sourceId: configForm.mode === 'text' && configForm.sourceId ? Number(configForm.sourceId) : null,
      sourceTitle: configForm.mode === 'text' ? configForm.sourceTitle : '',
      cardGroupId: configForm.mode === 'card' ? cardGroupId : null,
      cardTemplate: configForm.cardTemplate,
      header: configForm.header,
      content: configForm.content,
      footer: configForm.footer,
      segmentSend: configForm.segmentSend,
      retryCount: configForm.retryCount,
      alertThreshold: configForm.alertThreshold,
      autoDisableOnLowStock: configForm.autoDisableOnLowStock,
      autoConfirmShipment: !!configForm.autoConfirmShipment
    })
    success.value = '配置已保存'
    await Promise.all([loadGoods(), loadStats()])
    closeConfig()
  } catch (e) {
    error.value = e.message || '保存失败'
  } finally {
    configSaving.value = false
  }
}

async function removeConfig(goods) {
  if (goods?._configUnavailable) {
    appendError('该商品发货配置状态未知，暂不允许执行禁用。')
    return
  }
  if (!await confirmAction({
    title: '确认禁用该商品自动发货？',
    description: '会将三个发货时机全部停用，但保留已填写内容。',
    dangerous: true,
    confirmText: '禁用'
  })) return

  try {
    for (const timing of ['payDelivery', 'confirmDelivery', 'reviewDelivery']) {
      await saveGoodsDeliveryConfig(goods.id, { timing, enabled: 0, mode: 'text', sourceId: null, sourceTitle: '' })
    }
    success.value = '已禁用该商品发货配置'
    await Promise.all([loadGoods(), loadStats()])
  } catch (e) {
    error.value = e.message || '禁用失败'
  }
}

async function submitBatch() {
  if (!goodsAvailable.value || filteredConfigUnknownCount.value > 0) {
    appendError('商品或发货配置状态不完整，批量设置已取消。')
    return
  }
  if (!filteredGoods.value.length) {
    appendError('当前筛选范围没有可配置商品。')
    return
  }
  if (batchForm.mode === 'card' && (!batchForm.cardGroupId || cardGroupsAvailable.value === false)) {
    appendError('批量卡密发货需要可用的卡密分组。')
    return
  }
  batchLoading.value = true
  try {
    const goodsIds = filteredGoods.value.map(goods => goods.id)
    await batchSetDeliveryRules({
      goodsIds,
      timing: batchForm.action,
      enabled: batchForm.enabled,
      mode: batchForm.mode || undefined,
      cardGroupId: batchForm.mode === 'card' && batchForm.cardGroupId ? Number(batchForm.cardGroupId) : null,
      sourceId: batchForm.mode === 'text' && batchForm.sourceId ? Number(batchForm.sourceId) : null,
      sourceTitle: batchForm.mode === 'text' ? sourceTitle(batchForm.sourceId) : ''
    })
    success.value = `已批量更新 ${goodsIds.length} 个商品`
    showBatchDialog.value = false
    await Promise.all([loadGoods(), loadStats()])
  } catch (e) {
    error.value = e.message || '批量配置失败'
  } finally {
    batchLoading.value = false
  }
}

async function batchDelete() {
  if (!goodsAvailable.value || filteredConfigUnknownCount.value > 0 || !filteredGoods.value.length) {
    appendError('商品或发货配置状态不完整，批量删除已取消。')
    return
  }
  if (!await confirmAction({
    title: '确认批量删除发货配置？',
    description: `将删除当前筛选出的 ${filteredGoods.value.length} 个商品配置。`,
    dangerous: true,
    confirmText: '删除'
  })) return
  try {
    await batchDeleteDeliveryRules({ goodsIds: filteredGoods.value.map(goods => goods.id) })
    success.value = '批量删除完成'
    await Promise.all([loadGoods(), loadStats()])
  } catch (e) {
    error.value = e.message || '删除失败'
  }
}

async function scanPendingOrders() {
  try {
    const res = await scanApi()
    const scanned = res?.data
    if (scanned !== undefined && scanned !== null) {
      if (!Number.isSafeInteger(Number(scanned))) {
        throw new Error('待发货订单扫描响应格式异常，未返回有效数量。')
      }
    }
    await loadStats()
    const countText = Number.isSafeInteger(Number(scanned)) ? `（本次扫描 ${Number(scanned)} 条）` : ''
    success.value = `已触发待发货订单扫描${countText}，请前往发货记录页查看结果。`
  } catch (e) {
    error.value = e.message || '扫描失败'
  }
}

const recoverLoading = ref(false)
async function recoverPendingDeliveries() {
  recoverLoading.value = true
  try {
    const res = await recoverApi()
    const d = res?.data || {}
    success.value = d.message || `补发扫描完成：扫描 ${d.scanned ?? 0} 单，补发 ${d.recovered ?? 0} 单，跳过 ${d.skipped ?? 0} 单，失败 ${d.failed ?? 0} 单`
    await loadStats()
  } catch (e) {
    error.value = e.message || '补发失败'
  } finally {
    recoverLoading.value = false
  }
}

function goSourceLibrary() {
  emit('navigate', 'delivery-source-library')
}

function onHeaderAction(event) {
  if (event.detail === 'delivery-batch') showBatchDialog.value = true
  if (event.detail === 'delivery-refresh') loadAll()
}

onMounted(() => {
  window.addEventListener('xya-header-action', onHeaderAction)
  loadAll()
})

onBeforeUnmount(() => {
  window.removeEventListener('xya-header-action', onHeaderAction)
})
</script>

<style scoped>
.page-head {
  margin-bottom: 10px;
}

.page-head h1 {
  margin: 0;
  font-size: 30px;
}

.page-head p {
  margin: 10px 0 0;
  color: #667491;
}

.stat-row {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 16px;
  margin: 14px 0 18px;
}

.delivery-body {
  display: grid;
  grid-template-columns: 280px minmax(0, 1fr);
  gap: 18px;
}

.filter-section {
  margin-bottom: 10px;
}

.filter-label {
  display: block;
  font-size: 13px;
  color: #526079;
  margin-bottom: 4px;
  font-weight: 500;
}

.table-info {
  font-size: 14px;
  color: #526079;
}

.goods-cell {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 220px;
}

.goods-thumb {
  width: 44px;
  height: 44px;
  border-radius: 8px;
  object-fit: cover;
  background: #f0f4ff;
}

.goods-thumb.placeholder {
  background: #f0f4ff;
}

.goods-detail {
  min-width: 0;
}

.goods-title {
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.goods-meta {
  display: flex;
  gap: 10px;
  font-size: 12px;
  color: #667085;
}

.goods-meta .price {
  color: #ef4444;
  font-weight: 600;
}

.delivery-status {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border-radius: 20px;
  font-size: 13px;
  cursor: pointer;
  white-space: nowrap;
  font-weight: 500;
  border: 0;
  font-family: inherit;
}

.status-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
}

.dot-green { background: #16bf78; }
.dot-gray { background: #c4cddb; }
.status-enabled { background: #ecfdf3; color: #067647; }
.status-none { background: #f5f6fa; color: #667085; }
.status-disabled { background: #f5f6fa; color: #98a2b3; }

.config-tabs {
  display: flex;
  gap: 4px;
  margin-bottom: 16px;
  background: #f5f6fa;
  border-radius: 10px;
  padding: 3px;
}

.config-tab {
  flex: 1;
  padding: 8px 12px;
  border: none;
  border-radius: 8px;
  background: transparent;
  cursor: pointer;
}

.config-tab.active {
  background: #fff;
  color: #2d5bff;
  box-shadow: 0 1px 3px rgba(0, 0, 0, .08);
}

.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, .35);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}

.modal-content {
  background: #fff;
  border-radius: 20px;
  padding: 28px;
  max-width: 560px;
  width: 90%;
  box-shadow: 0 20px 60px rgba(0, 0, 0, .2);
}

.config-modal {
  max-width: 680px;
  max-height: 88vh;
  overflow-y: auto;
  -webkit-overflow-scrolling: touch;
  padding: 0;
  display: flex;
  flex-direction: column;
  animation: config-modal-in .22s ease-out;
}

@keyframes config-modal-in {
  from {
    opacity: 0;
    transform: translateY(12px) scale(.98);
  }
  to {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
}

.config-modal h3 {
  margin: 0 0 4px;
  font-size: 18px;
}

.config-modal .form-row textarea {
  min-height: 60px;
}

/* ===== 弹窗头部 ===== */
.config-modal-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 20px 24px 16px;
  border-bottom: 1px solid #eef1f6;
  background: #fff;
  position: sticky;
  top: 0;
  z-index: 2;
}

.config-modal-title {
  flex: 1;
  min-width: 0;
}

.config-modal-title h3 {
  margin: 0 0 4px;
  font-size: 18px;
  font-weight: 700;
  color: #15213d;
  line-height: 1.3;
}

.config-modal-title .subtle {
  font-size: 13px;
  color: #8c98ae;
  line-height: 1.5;
  word-break: break-all;
}

.config-modal-close {
  flex-shrink: 0;
  width: 32px;
  height: 32px;
  border-radius: 8px;
  border: 0;
  background: #f5f6fa;
  color: #526079;
  font-size: 22px;
  line-height: 1;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  transition: background .15s, color .15s;
  font-family: inherit;
  padding: 0;
}

.config-modal-close:hover {
  background: #ffe5e5;
  color: #e5484d;
}

.config-modal-close:focus-visible {
  outline: 2px solid #2d5bff;
  outline-offset: 2px;
}

/* ===== 弹窗内容区（tabs + form-grid 外层 padding） ===== */
.config-modal > .config-tabs,
.config-modal > .form-grid {
  padding-left: 24px;
  padding-right: 24px;
}

.config-modal > .config-tabs {
  margin-top: 16px;
}

.config-modal > .form-grid {
  padding-top: 4px;
  padding-bottom: 8px;
}

/* ===== 分区容器 ===== */
.form-section {
  border: 1px solid #eef1f6;
  border-radius: 12px;
  padding: 14px 16px 12px;
  background: #fbfcfe;
  margin-bottom: 4px;
}

.form-section-title {
  font-size: 13px;
  font-weight: 700;
  color: #2d5bff;
  margin-bottom: 12px;
  padding-left: 10px;
  position: relative;
  line-height: 1.4;
  letter-spacing: .2px;
}

.form-section-title::before {
  content: '';
  position: absolute;
  left: 0;
  top: 50%;
  transform: translateY(-50%);
  width: 3px;
  height: 14px;
  background: linear-gradient(180deg, #2d5bff, #6a8dff);
  border-radius: 2px;
}

.form-section .form-row {
  margin-bottom: 4px;
}

.form-section .form-row:last-child {
  margin-bottom: 0;
}

/* ===== 弹窗底部 ===== */
.config-modal-footer {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 10px;
  padding: 14px 24px;
  border-top: 1px solid #eef1f6;
  background: #fff;
  position: sticky;
  bottom: 0;
  z-index: 2;
}

.form-grid {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.form-row {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.form-row textarea {
  width: 100%;
  min-height: 60px;
  padding: 8px 12px;
  border: 1px solid #dbe1ed;
  border-radius: 10px;
  font-size: 14px;
  font-family: inherit;
  resize: vertical;
  box-sizing: border-box;
}

.checkbox-label {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: #526079;
}

.subtle {
  color: #98a2b3;
  font-size: 13px;
}

.timing-notice {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 10px 14px;
  margin-bottom: 12px;
  background: linear-gradient(90deg, #fff8e6, #fffbf2);
  border: 1px solid #ffd98a;
  border-radius: 12px;
  font-size: 13px;
  color: #6b4f12;
  line-height: 1.6;
}

.timing-notice-icon {
  flex-shrink: 0;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: #f5a623;
  color: #fff;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: bold;
}

@media (max-width: 1400px) {
  .stat-row {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .delivery-body {
    grid-template-columns: minmax(0, 1fr);
  }
}

/* ===== 移动端响应式 (max-width: 900px) ===== */
@media (max-width: 900px) {
  /* 页头大字号收敛 */
  .page-head h1 {
    font-size: 20px;
  }

  .page-head p {
    margin: 6px 0 0;
    font-size: 13px;
    line-height: 1.6;
  }

  /* 统计行：5列 → 2列 */
  .stat-row {
    grid-template-columns: repeat(2, minmax(0, 1fr)) !important;
    gap: 10px;
    margin: 10px 0 12px;
  }
  .stat-row > * {
    min-width: 0;
  }

  /* 主体两列已通过 1400px 变单列，这里收敛间距 */
  .delivery-body {
    grid-template-columns: minmax(0, 1fr);
    gap: 12px;
  }
  .delivery-body > * {
    min-width: 0;
  }
  .filter-panel,
  .main-content {
    min-width: 0;
  }

  /* 筛选区收敛 */
  .filter-section {
    margin-bottom: 8px;
  }

  .filter-label {
    font-size: 12px;
    margin-bottom: 3px;
  }

  /* 商品单元格最小宽度解除，允许压缩 */
  .goods-cell {
    min-width: 0;
    gap: 8px;
  }

  .goods-thumb {
    width: 38px;
    height: 38px;
    flex: 0 0 38px;
  }

  .goods-title {
    font-size: 13px;
  }

  .goods-meta {
    gap: 8px;
    font-size: 11px;
  }

  /* 配置标签页收敛 */
  .config-tabs {
    gap: 3px;
    margin-bottom: 12px;
    border-radius: 8px;
  }

  .config-tab {
    padding: 8px 6px;
    font-size: 12px;
  }

  /* 时机提示收敛 */
  .timing-notice {
    padding: 8px 12px;
    margin-bottom: 10px;
    font-size: 12px;
    line-height: 1.55;
    border-radius: 10px;
  }

  .timing-notice-icon {
    width: 18px;
    height: 18px;
    font-size: 11px;
  }

  /* 模态框：底部全宽弹出 */
  .modal-overlay {
    align-items: flex-end;
  }

  .modal-content {
    max-width: 100%;
    width: 100%;
    padding: 16px;
    border-radius: 16px 16px 0 0;
    max-height: 90vh;
    overflow-y: auto;
    -webkit-overflow-scrolling: touch;
  }

  .config-modal {
    max-width: 100%;
    max-height: 90vh;
    padding: 0;
  }

  .modal-content h3 {
    font-size: 17px;
  }

  /* 配置弹窗：移动端 padding 收敛 */
  .config-modal-header {
    padding: 14px 16px 12px;
  }

  .config-modal-title h3 {
    font-size: 16px;
  }

  .config-modal-title .subtle {
    font-size: 12px;
  }

  .config-modal-close {
    width: 28px;
    height: 28px;
    font-size: 20px;
  }

  .config-modal > .config-tabs,
  .config-modal > .form-grid {
    padding-left: 16px;
    padding-right: 16px;
  }

  .config-modal > .config-tabs {
    margin-top: 12px;
  }

  .config-modal-footer {
    padding: 12px 16px;
  }

  .form-section {
    padding: 12px 12px 10px;
  }

  .form-section-title {
    font-size: 12px;
    margin-bottom: 10px;
  }

  /* 表单行收敛 */
  .form-grid {
    gap: 10px;
  }

  .form-row textarea {
    min-height: 54px;
    padding: 8px 10px;
    font-size: 13px;
    border-radius: 8px;
  }

  .checkbox-label {
    font-size: 12px;
  }

  /* 宽表格横向滚动 */
  .base-table {
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
  }

  .base-table tbody {
    white-space: nowrap;
  }

  /* 表格工具栏收敛 */
  .table-info {
    font-size: 13px;
  }

  .subtle {
    font-size: 12px;
  }
}

.sku-card {
  border: 1px solid #e5e7eb;
  border-radius: 10px;
  padding: 14px 16px;
  margin-bottom: 12px;
  background: #fafbfc;
}
.sku-card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
}
.sku-card-title {
  font-weight: 600;
  font-size: 14px;
  color: #1f2937;
}
.sku-card-body {
  border-top: 1px dashed #e5e7eb;
  padding-top: 10px;
}
.sku-rules-grid {
  max-height: 60vh;
  overflow-y: auto;
}
.sku-success-text {
  color: #16a34a;
  font-size: 13px;
}
.sku-timing-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}
.sku-timing-cell {
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  padding: 10px;
  background: #fff;
}
.sku-timing-header {
  margin-bottom: 8px;
  font-weight: 600;
  font-size: 13px;
}
.sku-timing-body {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.sku-timing-body-disabled {
  color: #98a2b3;
  font-size: 12px;
  padding: 4px 0;
}
.sku-timing-body .form-row {
  margin-bottom: 0;
}
@media (max-width: 900px) {
  .sku-timing-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
