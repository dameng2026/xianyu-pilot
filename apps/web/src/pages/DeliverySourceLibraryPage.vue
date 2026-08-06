<template>
  <div>
    <div v-if="error" class="global-notice error">{{ error }}</div>
    <div v-if="success" class="global-notice success">{{ success }}</div>

    <div class="stats-grid">
      <div class="stat-card">
        <div class="stat-icon-circle blue"><span class="stat-icon-svg">📦</span></div>
        <div class="stat-info">
          <div class="stat-label">货源总数</div>
          <div class="stat-value">{{ stats.total }}</div>
          <div class="stat-trend muted">统一管理的货源条目</div>
        </div>
      </div>
      <div class="stat-card">
        <div class="stat-icon-circle orange"><span class="stat-icon-svg">🔑</span></div>
        <div class="stat-info">
          <div class="stat-label">卡密发货</div>
          <div class="stat-value">{{ stats.cardSources }}</div>
          <div class="stat-trend muted">从卡密分组自动扣减</div>
        </div>
      </div>
      <div class="stat-card">
        <div class="stat-icon-circle green"><span class="stat-icon-svg">📝</span></div>
        <div class="stat-info">
          <div class="stat-label">文本发货</div>
          <div class="stat-value">{{ stats.textSources }}</div>
          <div class="stat-trend muted">固定文案直接发送</div>
        </div>
      </div>
      <div class="stat-card">
        <div class="stat-icon-circle purple"><span class="stat-icon-svg">🔗</span></div>
        <div class="stat-info">
          <div class="stat-label">已配置商品</div>
          <div class="stat-value">{{ stats.totalConfigured }}</div>
          <div class="stat-trend muted">货源绑定商品总数</div>
        </div>
      </div>
      <div class="stat-card">
        <div class="stat-icon-circle orange"><span class="stat-icon-svg">🏷</span></div>
        <div class="stat-info">
          <div class="stat-label">可选商品</div>
          <div class="stat-value">{{ candidateLibraryTotal }}</div>
          <div class="stat-trend muted">可配置的商品池规模</div>
        </div>
      </div>
      <div class="stat-card">
        <div class="stat-icon-circle" :class="aiStatus.configured ? 'green' : 'gray'"><span class="stat-icon-svg">{{ aiStatus.configured ? '✓' : '!' }}</span></div>
        <div class="stat-info">
          <div class="stat-label">AI 推荐</div>
          <div class="stat-value">{{ aiStatus.configured ? '可用' : '未配置' }}</div>
          <div class="stat-trend" :class="aiStatus.configured ? 'muted' : 'down'">
            {{ aiStatus.configured ? 'AI 模型已就绪' : '请先配置通用模型' }}
          </div>
        </div>
      </div>
      <div class="stat-card">
        <div class="stat-icon-circle red"><span class="stat-icon-svg">⚠</span></div>
        <div class="stat-info">
          <div class="stat-label">库存预警</div>
          <div class="stat-value">{{ stats.lowStock }}</div>
          <div class="stat-trend" :class="stats.lowStock > 0 ? 'down' : 'muted'">
            {{ stats.lowStock > 0 ? '卡密库存不足，请补充' : '库存充足' }}
          </div>
        </div>
      </div>
    </div>

    <CardPanel title="货源库">
      <div class="toolbar">
        <input v-model="query.keyword" class="input" placeholder="搜索标题 / 正文 / 备注" :disabled="sourcesLoading || Boolean(mutationBusy)" @keyup.enter="searchSources" />
        <AppButton type="primary" :disabled="sourcesLoading || Boolean(mutationBusy)" @click="searchSources">搜索</AppButton>
        <AppButton :disabled="sourcesAvailable !== true || Boolean(mutationBusy)" @click="openCreate">新增货源</AppButton>
      </div>

      <EmptyState v-if="sourcesLoading && sourcesAvailable !== true" icon="⏳" title="货源库加载中" description="正在读取货源与使用情况。" />
      <EmptyState v-else-if="sourcesAvailable === false" icon="⚠️" title="货源库暂不可用" description="当前无法确认货源记录；新增、编辑、删除和商品绑定均已禁用。">
        <AppButton :disabled="sourcesLoading" @click="loadSources">重试</AppButton>
      </EmptyState>
      <template v-else-if="sourcesAvailable === true">
      <BaseTable :columns="columns" :rows="rows" @row-click="selectSource">
        <template #title="{ row }">
          <div>
            <div class="strong">{{ row.title }}</div>
            <div class="subtle">{{ row.remark || '无备注' }}</div>
          </div>
        </template>
        <template #content="{ row }">
          <div class="content-preview">{{ row.content }}</div>
        </template>
        <template #mode="{ row }">
          <Badge :type="row.deliveryMode === 'card' ? 'orange' : 'gray'">
            {{ row.deliveryMode === 'card' ? '卡密发货' : '文本发货' }}
          </Badge>
        </template>
        <template #stock="{ row }">
          <span v-if="row.deliveryMode === 'card'" :class="['stock-cell', { low: (row.cardRemainCount ?? 0) <= 0 }]">
            剩余 {{ row.cardRemainCount ?? 0 }}
          </span>
          <span v-else class="subtle">文本</span>
        </template>
        <template #usage="{ row }">
          <Badge>{{ row.usageCount || 0 }} 个商品</Badge>
        </template>
        <template #op="{ row }">
          <button class="link" :disabled="Boolean(mutationBusy)" @click.stop="editSource(row)">编辑</button>
          <button class="link" :disabled="detailLoading || analysisLoading || Boolean(mutationBusy)" @click.stop="analyzeSource(row)">AI 推荐</button>
          <button class="link danger-text" :disabled="Boolean(mutationBusy)" @click.stop="removeSource(row)">删除</button>
        </template>
      </BaseTable>
      <Pagination v-if="!mutationBusy" :total="sourceTotal" :current="query.current" :page-size="query.size" @page-change="goSourcePage" />
      </template>
    </CardPanel>

    <CardPanel v-if="editing" :title="editing.id ? '编辑货源' : '新增货源'" style="margin-top:16px" class="source-editor-panel">
      <div class="editor-layout">
        <div class="editor-left">
          <div class="form-field">
            <label class="field-label"><span class="required">*</span>标题</label>
            <div class="field-input-wrap">
              <input v-model="form.title" class="field-input" maxlength="200" placeholder="给用户和 AI 模型看的标题" />
              <span class="char-count">{{ (form.title || '').length }}/200</span>
            </div>
          </div>

          <div class="form-field">
            <label class="field-label">
              <span class="required">*</span>正文
              <button
                v-if="form.deliveryMode === 'card'"
                type="button"
                class="placeholder-btn"
                @click="insertCardPlaceholder"
              >+ 插入 {卡密占位}</button>
            </label>
            <div class="field-input-wrap">
              <textarea
                ref="contentTextareaRef"
                v-model="form.content"
                rows="6"
                class="field-textarea"
                :placeholder="form.deliveryMode === 'card' ? '实际发货文本，需包含 {卡密占位}，发货时会自动替换为认领到的卡密' : '实际发货文本内容'"
                maxlength="5000"
              ></textarea>
              <span class="char-count">{{ (form.content || '').length }}/5000</span>
            </div>
          </div>

          <div class="form-field">
            <label class="field-label">备注（选填）</label>
            <div class="field-input-wrap">
              <textarea v-model="form.remark" rows="3" class="field-textarea" maxlength="500" placeholder="可选备注"></textarea>
              <span class="char-count">{{ (form.remark || '').length }}/500</span>
            </div>
          </div>

          <div v-if="form.deliveryMode === 'text'" class="form-field">
            <label class="field-label">
              多条正文（可选）
              <button type="button" class="placeholder-btn" :disabled="form.segments.length >= 20" @click="addSegment('text')">+ 添加正文段</button>
            </label>
            <div class="segments-editor">
              <div v-if="form.segments.length === 0" class="segments-empty">
                未配置多条正文，将使用上方"正文"字段单条发送。添加多条正文后，发货时将按顺序逐条发送（支持文本+图片混合）。
              </div>
              <div v-for="(seg, idx) in form.segments" :key="seg._uid ?? idx" class="segment-card">
                <div class="segment-header">
                  <span class="segment-index">第 {{ idx + 1 }} 条</span>
                  <div class="segment-type-switch">
                    <button type="button" :class="['segment-type-btn', { active: seg.type === 'text' }]" @click="setSegmentType(idx, 'text')">文本</button>
                    <button type="button" :class="['segment-type-btn', { active: seg.type === 'image' }]" @click="setSegmentType(idx, 'image')">图片</button>
                  </div>
                  <button type="button" class="segment-remove-btn" :disabled="form.segments.length <= 1" @click="removeSegment(idx)">删除</button>
                </div>
                <div class="segment-body">
                  <div v-if="seg.type === 'text'" class="segment-text-area">
                    <textarea
                      v-model="seg.content"
                      rows="3"
                      maxlength="5000"
                      class="field-textarea"
                      placeholder="文本内容（发货时逐条发送）"
                    ></textarea>
                    <span class="char-count">{{ (seg.content || '').length }}/5000</span>
                  </div>
                  <div v-else class="segment-image-row">
                    <input
                      v-model="seg.imageUrl"
                      class="field-input segment-image-url"
                      placeholder="图片 URL（http:// 或 https://）"
                    />
                    <button type="button" class="placeholder-btn" @click="triggerSegmentImageUpload(idx)">上传图片</button>
                    <input
                      :ref="el => setSegmentFileInput(el, idx)"
                      type="file"
                      accept="image/jpeg,image/png,image/gif,image/webp"
                      style="display:none"
                      @change="onSegmentImageSelect($event, idx)"
                    />
                  </div>
                  <div v-if="seg.type === 'image' && seg.imageUrl" class="segment-image-preview">
                    <img :src="seg.imageUrl" alt="" style="max-width:120px;max-height:80px;border-radius:6px" />
                  </div>
                </div>
              </div>
              <div class="segments-tip">
                每条正文为"纯文本"或"单张图片"二选一；如需同时发送文本和图片，请分两条配置。最多 20 条、每条 5000 字符。
              </div>
            </div>
          </div>
        </div>

        <div class="editor-right">
          <div class="setting-card">
            <div class="setting-card-title">发送类型</div>
            <div class="mode-cards">
              <label class="mode-card" :class="{ active: form.deliveryMode === 'text' }">
                <input v-model="form.deliveryMode" type="radio" value="text" @change="onDeliveryModeChange" />
                <span class="mode-card-radio"></span>
                <div class="mode-card-body">
                  <span class="mode-card-title">文本发送</span>
                  <span class="mode-card-desc">通过文本消息发送给买家，适合固定文案内容</span>
                </div>
              </label>
              <label class="mode-card" :class="{ active: form.deliveryMode === 'card' }">
                <input v-model="form.deliveryMode" type="radio" value="card" @change="onDeliveryModeChange" />
                <span class="mode-card-radio"></span>
                <div class="mode-card-body">
                  <span class="mode-card-title">卡密发送</span>
                  <span class="mode-card-desc">从卡密库中选择一张卡密替换占位符后发送</span>
                </div>
              </label>
            </div>
            <div class="info-tip">
              <span class="info-tip-icon">i</span>
              <span>提示：卡密发送将在发送时自动替换占位符，例如 <code>{卡密}</code>、<code>{激活码}</code> 等占位符内容。</span>
            </div>
            <div v-if="form.deliveryMode === 'card'" class="card-group-select">
              <select v-model="form.cardGroupId" class="field-input">
                <option value="" disabled>请选择卡密分组</option>
                <option v-for="g in cardGroups" :key="g.id" :value="g.id">
                  {{ g.groupName }}（剩余 {{ g.remainCount ?? 0 }} / 共 {{ g.totalCount ?? 0 }}）
                </option>
              </select>
              <div v-if="cardGroupsLoading" class="subtle" style="margin-top:8px;font-size:13px">加载中…</div>
              <div v-else-if="cardGroups.length === 0" class="subtle danger-text" style="margin-top:8px;font-size:13px">
                暂无卡密分组，请先到「卡密仓库」创建分组并导入卡密
              </div>
              <div v-else-if="selectedCardGroup" class="stock-display" style="margin-top:10px">
                <span class="stock-label-text">当前剩余：</span>
                <span :class="['stock-value-text', { low: selectedCardRemainCount <= 0 }]">
                  {{ selectedCardRemainCount }} 张
                </span>
              </div>
            </div>
          </div>
        </div>
      </div>
      <div class="form-actions">
        <AppButton type="primary" class="save-btn" :disabled="sourcesAvailable !== true || Boolean(mutationBusy)" @click="saveSource">{{ mutationBusy === 'save' ? '保存中…' : '保存' }}</AppButton>
        <AppButton class="cancel-btn" :disabled="mutationBusy === 'save'" @click="cancelEdit">取消</AppButton>
      </div>
    </CardPanel>


    <template v-if="selected">
      <CardPanel title="货源详情" style="margin-top:16px">
        <EmptyState v-if="detailLoading" icon="⏳" title="货源详情加载中" description="正在读取绑定商品，期间不会开放配置操作。" />
        <EmptyState v-else-if="detailAvailable === false" icon="⚠️" title="货源详情暂不可用" description="当前无法确认绑定关系；为避免把旧商品配置到新货源，所有写操作已禁用。">
          <AppButton :disabled="detailLoading" @click="loadSelectedGoods(selected.id, selected)">重试</AppButton>
        </EmptyState>
        <template v-else-if="detailAvailable === true">
        <div class="source-summary">
          <div class="summary-item">
            <div class="summary-label">当前货源</div>
            <div class="summary-value">{{ selected.title || '-' }}</div>
          </div>
          <div class="summary-item">
            <div class="summary-label">已配置商品</div>
            <div class="summary-value">{{ selected.usageCount || 0 }}</div>
          </div>
          <div class="summary-item">
            <div class="summary-label">可选商品总数</div>
            <div class="summary-value">{{ candidateLibraryTotal }}</div>
          </div>
        </div>
        <div class="subtle source-preview">{{ selected.content || '暂无正文内容' }}</div>
        </template>
      </CardPanel>

      <CardPanel v-if="detailAvailable === true" title="已配置商品" style="margin-top:16px">
        <div class="toolbar">
          <input
            v-model="configuredKeyword"
            class="input"
            placeholder="搜索已配置商品"
            style="max-width:260px"
            :disabled="detailLoading || Boolean(mutationBusy)"
            @keyup.enter="searchConfiguredGoods"
          />
          <AppButton :disabled="detailLoading || Boolean(mutationBusy)" @click="searchConfiguredGoods">搜索</AppButton>
          <AppButton :disabled="detailLoading || Boolean(mutationBusy)" @click="refreshSelectedGoods">刷新商品列表</AppButton>
        </div>
        <BaseTable :columns="configuredColumns" :rows="filteredConfiguredGoods">
          <template #title="{ row }">
            <div class="goods-cell">
              <img v-if="goodsCover(row)" :src="goodsCover(row)" class="goods-thumb" alt="" />
              <div v-else class="goods-thumb placeholder"></div>
              <div class="goods-main">
                <div class="strong">{{ row.title }}</div>
                <div class="subtle">{{ row.category || '-' }}</div>
                <div class="account-chip">
                  <img v-if="accountAvatar(row)" :src="accountAvatar(row)" class="account-avatar" alt="" />
                  <div v-else class="account-avatar placeholder avatar-placeholder"></div>
                  <span class="subtle">{{ accountDisplayLabel(row) }}</span>
                </div>
              </div>
            </div>
          </template>
          <template #bind="{ row }">
            <Badge type="green">{{ bindStateLabel(row) }}</Badge>
          </template>
          <template #single="{ row }">
            <button class="link" :disabled="detailLoading || analysisLoading || Boolean(mutationBusy)" @click.stop="applyOne(row)">再次配置</button>
            <button class="link danger-text" :disabled="detailLoading || analysisLoading || Boolean(mutationBusy)" @click.stop="removeConfiguredGoods(row)">删除</button>
          </template>
        </BaseTable>
        <Pagination
          v-if="!mutationBusy"
          :total="configuredGoodsPage.total"
          :current="configuredGoodsPage.current"
          :page-size="configuredGoodsPage.size"
          @page-change="goConfiguredGoodsPage"
        />
      </CardPanel>

      <CardPanel v-if="detailAvailable === true" :title="goodsView === 'recommend' ? 'AI 推荐商品' : '商品列表'" style="margin-top:16px">
        <div class="toolbar">
          <input
            v-model="goodsKeyword"
            class="input"
            placeholder="搜索商品标题 / 分类"
            style="max-width:260px"
            :disabled="detailLoading || Boolean(mutationBusy) || goodsView === 'recommend'"
            @keyup.enter="searchCandidateGoods"
          />
          <AppButton :disabled="detailLoading || Boolean(mutationBusy) || goodsView === 'recommend'" @click="searchCandidateGoods">搜索</AppButton>
          <AppButton :type="goodsView === 'all' ? 'primary' : 'default'" @click="showAllGoods">全部商品</AppButton>
          <AppButton type="primary" :disabled="detailLoading || analysisLoading || Boolean(mutationBusy)" @click="analyzeSource(selected)">AI 推荐商品</AppButton>
          <select v-model="applyTiming" class="input" style="max-width:200px">
            <option value="payDelivery">付款后发货</option>
            <option value="confirmDelivery">确认收货后赠送</option>
            <option value="reviewDelivery">好评后赠送</option>
          </select>
          <AppButton :disabled="selectedGoodsIds.length === 0 || detailAvailable !== true || Boolean(mutationBusy)" @click="applySelectedGoods">{{ mutationBusy === 'apply' ? '配置中…' : '批量配置' }}</AppButton>
        </div>
        <div v-if="!aiStatus.configured" class="ai-status-tip">
          {{ aiStatusMessage('未配置通用模型，当前仅展示规则匹配候选；完成模型配置后可使用 AI 推荐商品。') }}
        </div>
        <div class="subtle" style="margin-bottom:12px">
          {{ goodsView === 'recommend' ? recommendedHint : '可先查看全部商品，再使用 AI 自动筛选高匹配商品。' }}
        </div>
        <div
          v-if="goodsView === 'recommend' && recommendationPool.candidatePoolTruncated"
          class="ai-status-tip"
        >
          为控制 AI 成本与响应时间，本次仅在部分商品中分析（关键词匹配 + 最新 {{ recommendationPool.candidatePoolLimit }} 个，候选库共 {{ recommendationPool.candidatePoolTotal }} 个）。
        </div>
        <EmptyState
          v-if="goodsView === 'recommend' && analysisLoading && recommendationsAvailable !== true"
          icon="⏳"
          title="AI 推荐分析中"
          description="正在有界候选集中计算匹配结果，分析完成前不会开放配置操作。"
        />
        <EmptyState
          v-else-if="goodsView === 'recommend' && recommendationsAvailable === false"
          icon="⚠️"
          title="AI 推荐暂不可用"
          description="未能确认推荐结果，不会使用上一次结果执行批量配置。"
        >
          <AppButton :disabled="analysisLoading" @click="analyzeSource(selected)">重试</AppButton>
        </EmptyState>
        <BaseTable
          v-else
          v-model:selected-keys="pageSelectedGoodsIds"
          :columns="goodsColumns"
          :rows="filteredDisplayGoods"
          :selectable="true"
          :row-key="row => row.id"
        >
          <template #title="{ row }">
            <div class="goods-cell">
              <img v-if="goodsCover(row)" :src="goodsCover(row)" class="goods-thumb" alt="" />
              <div v-else class="goods-thumb placeholder"></div>
              <div class="goods-main">
                <div class="strong">{{ row.title }}</div>
                <div class="subtle">{{ row.category || '-' }}</div>
                <div class="account-chip">
                  <img v-if="accountAvatar(row)" :src="accountAvatar(row)" class="account-avatar" alt="" />
                  <div v-else class="account-avatar placeholder avatar-placeholder"></div>
                  <span class="subtle">{{ accountDisplayLabel(row) }}</span>
                </div>
              </div>
            </div>
          </template>
          <template #bind="{ row }">
            <Badge :type="row.configured ? 'green' : 'gray'">{{ bindStateLabel(row) }}</Badge>
          </template>
          <template #score="{ row }">
            <Badge :type="confidenceType(row.confidence, row.configured)">
              {{ confidenceLabel(row.confidence, row.configured) }}
            </Badge>
          </template>
          <template #reason="{ row }">
            <span class="subtle">{{ row.reason || (row.configured ? '该商品已配置当前货源' : '可手动配置') }}</span>
          </template>
          <template #single="{ row }">
            <button class="link" :disabled="detailLoading || analysisLoading || detailAvailable !== true || Boolean(mutationBusy)" @click.stop="applyOne(row)">{{ row.configured ? '重新配置' : '配置到该商品' }}</button>
          </template>
        </BaseTable>
        <Pagination
          v-if="!mutationBusy && goodsView === 'recommend'"
          :total="recommendedGoodsPage.total"
          :current="recommendedGoodsPage.current"
          :page-size="recommendedGoodsPage.size"
          @page-change="goRecommendedGoodsPage"
        />
        <Pagination
          v-else-if="!mutationBusy"
          :total="candidateGoodsPage.total"
          :current="candidateGoodsPage.current"
          :page-size="candidateGoodsPage.size"
          @page-change="goCandidateGoodsPage"
        />
      </CardPanel>
    </template>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import CardPanel from '../components/CardPanel.vue'
import BaseTable from '../components/BaseTable.vue'
import AppButton from '../components/AppButton.vue'
import Badge from '../components/Badge.vue'
import EmptyState from '../components/EmptyState.vue'
import Pagination from '../components/Pagination.vue'
import {
  applyDeliverySourceToGoods,
  createDeliverySource,
  deleteDeliverySource,
  getDeliverySourceGoods,
  getDeliverySources,
  recommendDeliverySourceGoods,
  removeDeliverySourceFromGoods,
  updateDeliverySource
} from '../api/autoDelivery.js'
import { getAiProviderStatus } from '../api/aiProvider.js'
import { getCards } from '../api/cards.js'
import { uploadImage } from '../api/misc.js'
import { recordsOf, totalOf } from '../utils/apiData.js'
import { confirmAction } from '../utils/confirmAction.js'
import { accountName } from '../utils/format.js'

const error = ref('')
const success = ref('')
const rows = ref([])
const sourceTotal = ref(0)
const sourcesAvailable = ref(null)
const sourcesLoading = ref(false)
const selected = ref(null)
const detailAvailable = ref(null)
const detailLoading = ref(false)
const analysisLoading = ref(false)
const mutationBusy = ref('')
let sourceRequestSequence = 0
let detailRequestSequence = 0
let recommendationRequestSequence = 0
const editing = ref(null)
// 卡密发货相关
const cardGroups = ref([])
const cardGroupsLoading = ref(false)
const contentTextareaRef = ref(null)
const CARD_PLACEHOLDER = '{卡密占位}'
const configuredGoods = ref([])
const allGoods = ref([])
const recommendedGoods = ref([])
const recommendedCandidateIds = ref([])
const candidateLibraryTotal = ref(0)
const selectedGoodsIds = ref([])
const applyTiming = ref('payDelivery')
const configuredKeyword = ref('')
const configuredAppliedKeyword = ref('')
const goodsKeyword = ref('')
const candidateAppliedKeyword = ref('')
const goodsView = ref('all')
const recommendationsAvailable = ref(null)
const aiStatus = ref({ configured: false, message: '' })
const SOURCE_LIBRARY_FOCUS_GOODS_KEY = 'xya:source-library-focus-goods-id'
const SOURCE_LIBRARY_FOCUS_TIMING_KEY = 'xya:source-library-focus-timing'
const focusedGoodsId = ref('')
const recommendedHint = ref('点击“AI 推荐商品”后，将展示适配度较高的商品。')

const configuredGoodsPage = reactive({ current: 1, size: 20, total: 0 })
const candidateGoodsPage = reactive({ current: 1, size: 20, total: 0 })
const recommendedGoodsPage = reactive({ current: 1, size: 20, total: 0 })
const recommendationPool = reactive({
  candidatePoolLimit: 200,
  candidatePoolSize: 0,
  candidatePoolTotal: 0,
  candidatePoolTruncated: false
})

const query = reactive({
  keyword: '',
  current: 1,
  size: 20
})

const form = reactive({
  title: '',
  content: '',
  remark: '',
  deliveryMode: 'text',
  cardGroupId: '',
  segments: []
})

const columns = [
  { key: 'title', title: '货源信息' },
  { key: 'content', title: '正文' },
  { key: 'mode', title: '发货类型' },
  { key: 'stock', title: '库存' },
  { key: 'usage', title: '已配置商品' },
  { key: 'op', title: '操作' }
]

const configuredColumns = [
  { key: 'title', title: '商品' },
  { key: 'bind', title: '状态' },
  { key: 'single', title: '操作' }
]

const goodsColumns = [
  { key: 'title', title: '商品' },
  { key: 'bind', title: '配置状态' },
  { key: 'score', title: '匹配度' },
  { key: 'reason', title: 'AI/规则理由' },
  { key: 'single', title: '操作' }
]

const configuredGoodsIds = computed(() => new Set(configuredGoods.value.map(row => String(row.id))))

const totalConfiguredCount = computed(() => {
  return rows.value.reduce((sum, row) => sum + (Number(row.usageCount) || 0), 0)
})

// 货源板块统计概览（含卡密/文本模式分布与库存预警）
const stats = computed(() => {
  const list = rows.value || []
  const total = list.length
  const cardSources = list.filter(r => r.deliveryMode === 'card').length
  const textSources = list.filter(r => r.deliveryMode !== 'card').length
  const totalConfigured = list.reduce((sum, r) => sum + (Number(r.usageCount) || 0), 0)
  const lowStock = list.filter(r => r.deliveryMode === 'card' && (r.cardRemainCount ?? 0) <= 0).length
  return { total, cardSources, textSources, totalConfigured, lowStock }
})

const selectedCardGroup = computed(() => {
  const id = form.cardGroupId
  if (!id) return null
  return cardGroups.value.find(g => String(g.id) === String(id)) || null
})

const selectedCardRemainCount = computed(() => {
  const group = selectedCardGroup.value
  return group ? (group.remainCount ?? 0) : 0
})

const normalizedConfiguredGoods = computed(() => decorateGoodsRows(configuredGoods.value, false))
const normalizedAllGoods = computed(() => decorateGoodsRows(allGoods.value, false))
const normalizedRecommendedGoods = computed(() => decorateGoodsRows(recommendedGoods.value, true))
const pagedRecommendedGoods = computed(() => {
  const offset = (recommendedGoodsPage.current - 1) * recommendedGoodsPage.size
  return normalizedRecommendedGoods.value.slice(offset, offset + recommendedGoodsPage.size)
})

const filteredConfiguredGoods = computed(() => normalizedConfiguredGoods.value)

const filteredDisplayGoods = computed(() => {
  return goodsView.value === 'recommend' ? pagedRecommendedGoods.value : normalizedAllGoods.value
})

const pageSelectedGoodsIds = computed({
  get: () => selectedGoodsIds.value,
  set: keys => {
    const visibleIds = new Set(filteredDisplayGoods.value.map(row => String(row.id)))
    const preserved = selectedGoodsIds.value.filter(id => !visibleIds.has(String(id)))
    const merged = [...preserved, ...(keys || [])]
    selectedGoodsIds.value = Array.from(
      new Map(merged.map(id => [String(id), id])).values()
    )
  }
})

function decorateGoodsRows(rows, fromAi) {
  return (rows || []).map(row => {
    const configured = Boolean(row.configured) || configuredGoodsIds.value.has(String(row.id))
    return {
      ...row,
      account: accountOf(row),
      configured,
      confidence: row.confidence || (configured ? 'medium' : 'low'),
      reason: row.reason || (configured ? '该商品已配置当前货源' : (fromAi ? 'AI 推荐商品' : '可手动配置')),
      recommended: fromAi || Boolean(row.recommended)
    }
  })
}

function accountOf(row) {
  return row?.account || {
    id: row?.accountId,
    avatarUrl: row?.accountAvatarUrl || '',
    nickname: row?.accountNickname || '',
    displayName: row?.accountDisplayName || '',
    accountNote: row?.accountRemark || '',
    externalUid: row?.accountExternalUid || ''
  }
}

function goodsCover(row) {
  return row?.coverPic || row?.imageUrl || ''
}

function accountAvatar(row) {
  return accountOf(row)?.avatarUrl || ''
}

function accountDisplayLabel(row) {
  const account = accountOf(row)
  const id = row?.accountId || account?.id
  const label = accountName(account || {})
  if (!id) {
    return label || '-'
  }
  return `${label || '账号'}（${id}）`
}

async function loadAiStatus() {
  try {
    const res = await getAiProviderStatus()
    aiStatus.value = res?.data || { configured: false, message: '' }
  } catch {
    if (import.meta.env.DEV) console.warn('[DeliverySourceLibrary] loadAiStatus failed')
    aiStatus.value = {
      configured: false,
      message: '未能读取 AI 模型配置状态，请稍后重试。'
    }
  }
}

function aiStatusMessage(defaultMessage = '未配置通用模型，请先前往系统设置中的“模型配置”完成配置。') {
  return aiStatus.value?.message || defaultMessage
}

function consumeFocusedContext() {
  focusedGoodsId.value = sessionStorage.getItem(SOURCE_LIBRARY_FOCUS_GOODS_KEY) || ''
  const timing = sessionStorage.getItem(SOURCE_LIBRARY_FOCUS_TIMING_KEY) || ''
  sessionStorage.removeItem(SOURCE_LIBRARY_FOCUS_GOODS_KEY)
  sessionStorage.removeItem(SOURCE_LIBRARY_FOCUS_TIMING_KEY)
  if (timing) {
    applyTiming.value = timing
  }
}

function applyFocusedGoodsContext(sourceRows = []) {
  if (!focusedGoodsId.value) return
  const target = sourceRows.find(row => String(row.id) === String(focusedGoodsId.value))
  if (!target) return
  if (!goodsKeyword.value) {
    goodsKeyword.value = target.title || String(target.id)
  }
  if (!target.configured) {
    selectedGoodsIds.value = [target.id]
  }
}

async function applyGoodsIds(goodsIds, successMessage, expectedSourceId = selected.value?.id) {
  const sourceId = expectedSourceId
  if (
    !sourceId
    || sourcesAvailable.value !== true
    || String(selected.value?.id) !== String(sourceId)
    || !goodsIds.length
    || detailAvailable.value !== true
    || mutationBusy.value
  ) return false
  const detailSequenceAtStart = detailRequestSequence
  mutationBusy.value = 'apply'
  try {
    await applyDeliverySourceToGoods(sourceId, {
      goodsIds,
      timing: applyTiming.value
    })
    success.value = successMessage
    selectedGoodsIds.value = []
    if (
      detailSequenceAtStart === detailRequestSequence
      && String(selected.value?.id) === String(sourceId)
    ) {
      await Promise.all([loadSelectedGoods(sourceId, selected.value), loadSources()])
    } else {
      await loadSources()
    }
    return true
  } finally {
    mutationBusy.value = ''
  }
}

async function loadSources() {
  const requestId = ++sourceRequestSequence
  error.value = ''
  sourcesLoading.value = true
  try {
    const res = await getDeliverySources(query)
    if (requestId !== sourceRequestSequence) return false
    rows.value = recordsOf(res.data)
    sourceTotal.value = totalOf(res.data, rows.value.length)
    sourcesAvailable.value = true
    if (selected.value?.id) {
      const latest = rows.value.find(row => String(row.id) === String(selected.value.id))
      if (latest) {
        selected.value = { ...selected.value, ...latest }
      } else {
        clearSelected()
      }
    }
    return true
  } catch (e) {
    if (requestId !== sourceRequestSequence) return false
    sourcesAvailable.value = false
    clearSelected()
    error.value = e.message || '货源库加载失败'
    return false
  } finally {
    if (requestId === sourceRequestSequence) sourcesLoading.value = false
  }
}

function searchSources() {
  if (mutationBusy.value) return
  query.current = 1
  loadSources()
}

function goSourcePage(page) {
  if (mutationBusy.value) return
  query.current = page
  loadSources()
}

function openCreate() {
  if (sourcesAvailable.value !== true || mutationBusy.value) return
  editing.value = {}
  Object.assign(form, {
    title: '',
    content: '',
    remark: '',
    deliveryMode: 'text',
    cardGroupId: '',
    segments: [makeSegment('text')]
  })
  ensureCardGroupsLoaded()
}

function editSource(row) {
  if (sourcesAvailable.value !== true || mutationBusy.value) return
  editing.value = row
  Object.assign(form, {
    title: row.title || '',
    content: row.content || '',
    remark: row.remark || '',
    deliveryMode: row.deliveryMode === 'card' ? 'card' : 'text',
    cardGroupId: row.cardGroupId ?? '',
    segments: Array.isArray(row.segments) ? row.segments.map(s => ({
      ...s,
      _uid: (segmentUidSeq += 1)
    })) : []
  })
  // 旧数据只有 content 没有 segments 时，回填为第一条文本段
  ensureSegmentsInitialized()
  ensureCardGroupsLoaded()
}

async function ensureCardGroupsLoaded() {
  if (cardGroups.value.length > 0 || cardGroupsLoading.value) return
  cardGroupsLoading.value = true
  try {
    const res = await getCards({ current: 1, size: 200 })
    cardGroups.value = recordsOf(res?.data)
  } catch (e) {
    cardGroups.value = []
    error.value = `卡密分组加载失败：${e.message || '请稍后重试'}`
  } finally {
    cardGroupsLoading.value = false
  }
}

function onDeliveryModeChange() {
  if (form.deliveryMode === 'card') {
    ensureCardGroupsLoaded()
  } else {
    form.cardGroupId = ''
  }
}

function insertCardPlaceholder() {
  const ta = contentTextareaRef.value
  if (!ta) {
    form.content = (form.content || '') + CARD_PLACEHOLDER
    return
  }
  const start = ta.selectionStart ?? form.content.length
  const end = ta.selectionEnd ?? form.content.length
  const before = (form.content || '').slice(0, start)
  const after = (form.content || '').slice(end)
  form.content = before + CARD_PLACEHOLDER + after
  requestAnimationFrame(() => {
    const pos = (before + CARD_PLACEHOLDER).length
    try {
      ta.focus()
      ta.setSelectionRange(pos, pos)
    } catch {
      // 忽略光标设置失败
    }
  })
}

function cancelEdit() {
  editing.value = null
}

function clearSelected() {
  detailRequestSequence += 1
  recommendationRequestSequence += 1
  analysisLoading.value = false
  selected.value = null
  detailAvailable.value = null
  detailLoading.value = false
  configuredGoods.value = []
  allGoods.value = []
  recommendedGoods.value = []
  recommendedCandidateIds.value = []
  candidateLibraryTotal.value = 0
  selectedGoodsIds.value = []
  configuredKeyword.value = ''
  configuredAppliedKeyword.value = ''
  goodsKeyword.value = ''
  candidateAppliedKeyword.value = ''
  goodsView.value = 'all'
  recommendationsAvailable.value = null
  Object.assign(configuredGoodsPage, { current: 1, total: 0 })
  Object.assign(candidateGoodsPage, { current: 1, total: 0 })
  Object.assign(recommendedGoodsPage, { current: 1, total: 0 })
  Object.assign(recommendationPool, {
    candidatePoolSize: 0,
    candidatePoolTotal: 0,
    candidatePoolTruncated: false
  })
}

let segmentUidSeq = 0
function makeSegment(type = 'text') {
  segmentUidSeq += 1
  return { _uid: segmentUidSeq, type, content: '', imageUrl: '', assetId: null }
}

function addSegment(type = 'text') {
  if (form.segments.length >= 20) {
    error.value = '正文条数最多支持 20 条'
    return
  }
  form.segments.push(makeSegment(type))
}

function setSegmentType(idx, type) {
  const seg = form.segments[idx]
  if (!seg || seg.type === type) return
  seg.type = type
  // 切换类型时清空另一字段，强制二选一互斥
  if (type === 'text') {
    seg.imageUrl = ''
    seg.assetId = null
  } else {
    seg.content = ''
  }
}

function ensureSegmentsInitialized() {
  if (!Array.isArray(form.segments) || form.segments.length === 0) {
    // 编辑单条 content 的旧数据时自动回填为第一条 segment（向后兼容）
    const first = makeSegment('text')
    first.content = form.content || ''
    form.segments = [first]
  }
}

function removeSegment(idx) {
  if (form.segments.length <= 1) return
  form.segments.splice(idx, 1)
}

const segmentFileInputs = {}
function setSegmentFileInput(el, idx) {
  if (el) segmentFileInputs[idx] = el
}

function triggerSegmentImageUpload(idx) {
  const input = segmentFileInputs[idx]
  if (input) input.click()
}

async function onSegmentImageSelect(event, idx) {
  const file = event.target.files?.[0]
  if (!file) return
  try {
    // 货源库为租户级资源，accountId=0 表示图片存到租户共享空间（后端跳过账号归属校验）
    const res = await uploadImage(0, file)
    const data = res?.data || {}
    const url = data?.url || data?.imageUrl || res?.url || res?.imageUrl
    if (typeof url === 'string' && url) {
      form.segments[idx].imageUrl = url
    } else {
      throw new Error('上传响应缺少图片 URL')
    }
  } catch (e) {
    error.value = `图片上传失败：${e.message || '请稍后重试'}`
  } finally {
    event.target.value = ''
  }
}

async function saveSource() {
  if (sourcesAvailable.value !== true || mutationBusy.value) return
  error.value = ''
  success.value = ''
  // 卡密发货模式校验
  if (form.deliveryMode === 'card') {
    if (!form.cardGroupId) {
      error.value = '卡密发货模式下必须选择一个卡密分组'
      return
    }
    if (!(form.content || '').includes(CARD_PLACEHOLDER)) {
      error.value = `卡密发货的正文必须包含 ${CARD_PLACEHOLDER} 占位符，否则无法替换实际卡密`
      return
    }
  }
  mutationBusy.value = 'save'
  try {
    const editingId = editing.value?.id
    // 文本发货模式：segments 互斥校验 + 构造清洗后的 segments
    let cleanedSegments = null
    if (form.deliveryMode === 'text' && Array.isArray(form.segments) && form.segments.length > 0) {
      if (form.segments.length > 20) {
        error.value = '正文条数过多，最多支持 20 条'
        return
      }
      const cleaned = []
      for (let i = 0; i < form.segments.length; i++) {
        const seg = form.segments[i]
        const type = seg.type === 'image' ? 'image' : 'text'
        const content = (seg.content || '').trim()
        const imageUrl = (seg.imageUrl || '').trim()
        if (type === 'image') {
          if (!imageUrl) {
            error.value = `第 ${i + 1} 条正文为图片类型，必须上传图片或填写图片 URL`
            return
          }
          if (content) {
            error.value = `第 ${i + 1} 条正文为图片类型，不能同时填写文本（每条只能文本或图片二选一）`
            return
          }
          cleaned.push({ type: 'image', imageUrl })
        } else {
          if (!content) {
            error.value = `第 ${i + 1} 条正文内容不能为空`
            return
          }
          if (imageUrl) {
            error.value = `第 ${i + 1} 条正文为文本类型，不能同时上传图片（每条只能文本或图片二选一）`
            return
          }
          if (content.length > 5000) {
            error.value = `第 ${i + 1} 条正文内容超过 5000 字符`
            return
          }
          cleaned.push({ type: 'text', content })
        }
      }
      cleanedSegments = cleaned
    }
    const payload = {
      title: form.title,
      content: form.content,
      remark: form.remark,
      deliveryMode: form.deliveryMode,
      cardGroupId: form.deliveryMode === 'card' ? form.cardGroupId : null,
      segments: cleanedSegments
    }
    if (editingId) {
      await updateDeliverySource(editingId, payload)
      success.value = '货源已更新'
    } else {
      await createDeliverySource(payload)
      success.value = '货源已新增'
    }
    editing.value = null
    await loadSources()
    if (editingId && String(selected.value?.id) === String(editingId)) {
      await loadSelectedGoods(editingId, selected.value)
    }
  } catch (e) {
    error.value = e.message || '保存失败'
  } finally {
    mutationBusy.value = ''
  }
}

async function removeSource(row) {
  if (sourcesAvailable.value !== true || mutationBusy.value) return
  mutationBusy.value = 'delete-confirm'
  try {
    if (!await confirmAction({
      title: '确认删除该货源？',
      description: '仅未被商品使用的货源可以删除；正在使用的货源会被服务端拒绝，以避免留下孤儿配置。',
      dangerous: true,
      confirmText: '删除'
    })) return
    mutationBusy.value = 'delete'
    await deleteDeliverySource(row.id)
    if (selected.value?.id === row.id) {
      clearSelected()
    }
    success.value = '货源已删除'
    await loadSources()
  } catch (e) {
    error.value = e.message || '删除失败'
  } finally {
    mutationBusy.value = ''
  }
}

function assignPageState(target, payload, fallbackRecords = []) {
  const page = payload || {}
  target.current = Number(page.current) || 1
  target.size = Number(page.size) || target.size
  target.total = totalOf(page, fallbackRecords.length)
  return recordsOf(page).length || Object.prototype.hasOwnProperty.call(page, 'records')
    ? recordsOf(page)
    : fallbackRecords
}

function detailPagingParams() {
  return {
    configuredCurrent: configuredGoodsPage.current,
    configuredSize: configuredGoodsPage.size,
    configuredKeyword: configuredAppliedKeyword.value,
    candidateCurrent: candidateGoodsPage.current,
    candidateSize: candidateGoodsPage.size,
    candidateKeyword: candidateAppliedKeyword.value
  }
}

function clearRecommendationState() {
  recommendationRequestSequence += 1
  analysisLoading.value = false
  recommendedGoods.value = []
  recommendedCandidateIds.value = []
  recommendationsAvailable.value = null
  goodsView.value = 'all'
  Object.assign(recommendedGoodsPage, { current: 1, total: 0 })
  Object.assign(recommendationPool, {
    candidatePoolSize: 0,
    candidatePoolTotal: 0,
    candidatePoolTruncated: false
  })
}

async function loadSelectedGoods(
  sourceId = selected.value?.id,
  candidate = selected.value,
  { resetPaging = false, clearRecommendation = true } = {}
) {
  if (!sourceId || sourcesAvailable.value !== true) return false
  const requestId = ++detailRequestSequence
  recommendationRequestSequence += 1
  analysisLoading.value = false
  selected.value = candidate || { id: sourceId }
  if (resetPaging) {
    configuredGoodsPage.current = 1
    candidateGoodsPage.current = 1
  }
  if (detailAvailable.value !== true || resetPaging) detailAvailable.value = null
  detailLoading.value = true
  if (resetPaging) {
    configuredGoods.value = []
    allGoods.value = []
    selectedGoodsIds.value = []
  }
  if (clearRecommendation) clearRecommendationState()
  try {
    const res = await getDeliverySourceGoods(sourceId, detailPagingParams())
    if (requestId !== detailRequestSequence) return false
    const data = res.data || {}
    selected.value = { ...(candidate || {}), ...(data.source || {}), id: sourceId }
    configuredGoods.value = assignPageState(
      configuredGoodsPage,
      data.configuredGoodsPage,
      data.configuredGoods || []
    )
    allGoods.value = assignPageState(
      candidateGoodsPage,
      data.allGoodsPage,
      data.allGoods || []
    )
    candidateLibraryTotal.value = Number(data.allGoodsTotal ?? candidateGoodsPage.total) || 0
    detailAvailable.value = true
    applyFocusedGoodsContext(normalizedAllGoods.value)
    return true
  } catch (e) {
    if (requestId !== detailRequestSequence) return false
    detailAvailable.value = false
    error.value = e.message || '货源详情与绑定商品加载失败'
    return false
  } finally {
    if (requestId === detailRequestSequence) detailLoading.value = false
  }
}

async function selectSource(row) {
  if (mutationBusy.value) return
  success.value = ''
  error.value = ''
  goodsView.value = 'all'
  configuredKeyword.value = ''
  configuredAppliedKeyword.value = ''
  goodsKeyword.value = ''
  candidateAppliedKeyword.value = ''
  await loadSelectedGoods(row.id, row, { resetPaging: true })
}

function recommendationParams() {
  return {
    candidateLimit: 200
  }
}

function applyRecommendationData(data, { replaceSelection = false } = {}) {
  aiStatus.value = {
    configured: data.configured !== false,
    message: data.message || aiStatus.value.message || ''
  }
  if (data.source) {
    selected.value = { ...(selected.value || {}), ...data.source }
  }
  recommendedGoods.value = data.candidates || []
  recommendedGoodsPage.current = 1
  recommendedGoodsPage.total = totalOf(data.candidatesPage, recommendedGoods.value.length)
  recommendedCandidateIds.value = data.applicableCandidateIds
    || normalizedRecommendedGoods.value.filter(row => !row.configured).map(row => row.id)
  const applicableIds = new Set(recommendedCandidateIds.value.map(id => String(id)))
  Object.assign(recommendationPool, {
    candidatePoolLimit: Number(data.candidatePoolLimit) || 200,
    candidatePoolSize: Number(data.candidatePoolSize) || 0,
    candidatePoolTotal: Number(data.candidatePoolTotal) || 0,
    candidatePoolTruncated: Boolean(data.candidatePoolTruncated)
  })
  recommendationsAvailable.value = true
  goodsView.value = 'recommend'
  recommendedHint.value = data.message || '已根据标题、正文和备注筛选出高适配商品。'
  if (replaceSelection) {
    selectedGoodsIds.value = [...recommendedCandidateIds.value]
    applyFocusedGoodsContext(normalizedRecommendedGoods.value)
  } else {
    selectedGoodsIds.value = selectedGoodsIds.value.filter(id => applicableIds.has(String(id)))
  }
}

async function requestRecommendation(sourceId, { replaceSelection = false } = {}) {
  const requestId = ++recommendationRequestSequence
  const analysisSequence = detailRequestSequence
  analysisLoading.value = true
  goodsView.value = 'recommend'
  if (!recommendedGoodsPage.total) recommendationsAvailable.value = null
  try {
    const res = await recommendDeliverySourceGoods(sourceId, recommendationParams())
    if (
      requestId !== recommendationRequestSequence
      || analysisSequence !== detailRequestSequence
      || String(selected.value?.id) !== String(sourceId)
    ) return false
    const data = res.data || {}
    applyRecommendationData(data, { replaceSelection })
    if (data.configured === false) {
      error.value = data.message || aiStatusMessage('未配置通用模型，暂时无法使用 AI 一键配置。')
      return false
    }
    if (data.errorCode === 'AI_ERROR') {
      error.value = data.message || 'AI 调用失败，已回退为规则匹配候选。'
      return false
    }
    if (!recommendedGoodsPage.total) {
      success.value = recommendedHint.value || '暂未匹配到适合的商品'
    }
    return true
  } catch (e) {
    if (requestId !== recommendationRequestSequence) return false
    recommendationsAvailable.value = false
    recommendedGoods.value = []
    recommendedCandidateIds.value = []
    selectedGoodsIds.value = []
    recommendedGoodsPage.total = 0
    error.value = e.message || 'AI 推荐加载失败'
    return false
  } finally {
    if (requestId === recommendationRequestSequence) analysisLoading.value = false
  }
}

async function analyzeSource(row) {
  if (
    !row
    || sourcesAvailable.value !== true
    || detailLoading.value
    || analysisLoading.value
    || mutationBusy.value
  ) return false
  error.value = ''
  success.value = ''
  const sourceId = row.id
  try {
    const switchingSource = String(selected.value?.id) !== String(sourceId)
    const loaded = await loadSelectedGoods(sourceId, row, { resetPaging: switchingSource })
    if (!loaded) return false
    recommendedGoodsPage.current = 1
    return await requestRecommendation(sourceId, { replaceSelection: true })
  } catch (e) {
    error.value = e.message || 'AI 配置失败'
    return false
  }
}

async function applySelectedGoods() {
  if (!selected.value || selectedGoodsIds.value.length === 0) return
  try {
    await applyGoodsIds(
      [...selectedGoodsIds.value],
      `已配置 ${selectedGoodsIds.value.length} 个商品`
    )
  } catch (e) {
    error.value = e.message || '批量配置失败'
  }
}

async function applyOne(row) {
  if (!selected.value) return
  try {
    await applyGoodsIds([row.id], '已配置到商品')
  } catch (e) {
    error.value = e.message || '配置失败'
  }
}

async function removeConfiguredGoods(row) {
  if (!selected.value?.id || detailLoading.value || analysisLoading.value || mutationBusy.value) return
  mutationBusy.value = 'delete-confirm'
  try {
    if (!await confirmAction({
      title: '确认删除该已配置商品？',
      description: '将解除该商品与当前货源的绑定，并停用对应的发货时机。商品本身不会被删除，可稍后重新配置。',
      dangerous: true,
      confirmText: '删除'
    })) return
    mutationBusy.value = 'remove-binding'
    const sourceId = selected.value.id
    await removeDeliverySourceFromGoods(sourceId, row.id)
    success.value = '已解除商品与货源的绑定'
    await Promise.all([loadSelectedGoods(sourceId, selected.value), loadSources()])
  } catch (e) {
    error.value = e.message || '删除失败'
  } finally {
    mutationBusy.value = ''
  }
}

async function refreshSelectedGoods() {
  error.value = ''
  if (!selected.value?.id || mutationBusy.value) return
  try {
    await Promise.all([loadSelectedGoods(selected.value.id), loadSources()])
  } catch (e) {
    error.value = e.message || '商品列表刷新失败'
  }
}

async function searchConfiguredGoods() {
  if (!selected.value?.id || detailLoading.value || mutationBusy.value) return
  configuredAppliedKeyword.value = configuredKeyword.value.trim()
  configuredGoodsPage.current = 1
  await loadSelectedGoods(selected.value.id, selected.value, { clearRecommendation: false })
}

async function searchCandidateGoods() {
  if (
    !selected.value?.id
    || detailLoading.value
    || mutationBusy.value
    || goodsView.value === 'recommend'
  ) return
  candidateAppliedKeyword.value = goodsKeyword.value.trim()
  candidateGoodsPage.current = 1
  selectedGoodsIds.value = []
  await loadSelectedGoods(selected.value.id, selected.value, { clearRecommendation: false })
}

async function goConfiguredGoodsPage(page) {
  if (!selected.value?.id || detailLoading.value || mutationBusy.value) return
  configuredGoodsPage.current = page
  await loadSelectedGoods(selected.value.id, selected.value, { clearRecommendation: false })
}

async function goCandidateGoodsPage(page) {
  if (!selected.value?.id || detailLoading.value || mutationBusy.value) return
  candidateGoodsPage.current = page
  await loadSelectedGoods(selected.value.id, selected.value, { clearRecommendation: false })
}

async function goRecommendedGoodsPage(page) {
  if (!selected.value?.id || analysisLoading.value || mutationBusy.value) return
  recommendedGoodsPage.current = page
}

function showAllGoods() {
  goodsView.value = 'all'
}

function confidenceLabel(confidence, configured) {
  if (configured) return '已配置'
  if (confidence === 'high') return '高度匹配'
  if (confidence === 'medium') return '中等匹配'
  return '待确认'
}

function confidenceType(confidence, configured) {
  if (configured) return 'green'
  if (confidence === 'high') return 'green'
  if (confidence === 'medium') return 'orange'
  return 'gray'
}

function bindStateLabel(row) {
  return row.configured ? '已配置' : '未配置'
}

function onHeaderAction(event) {
  if (event.detail === 'source-new') openCreate()
  if (event.detail === 'source-refresh' && !mutationBusy.value) {
    loadSources()
    refreshSelectedGoods()
  }
}

onMounted(() => {
  window.addEventListener('xya-header-action', onHeaderAction)
  consumeFocusedContext()
  loadAiStatus()
  loadSources()
})

onBeforeUnmount(() => {
  window.removeEventListener('xya-header-action', onHeaderAction)
})
</script>

<style scoped>
.stats-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 14px;
  margin-bottom: 16px;
}
.stat-card {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 16px;
  background: #fff;
  border: 1px solid #e8eef8;
  border-radius: 12px;
  box-shadow: 0 1px 2px rgba(31,53,94,.04);
}
.stat-icon-circle {
  flex-shrink: 0;
  width: 40px;
  height: 40px;
  border-radius: 50%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
}
.stat-icon-circle.blue { background: #e6f4ff; color: #0d6bff; }
.stat-icon-circle.green { background: #ecfdf3; color: #067647; }
.stat-icon-circle.orange { background: #fff7e6; color: #d97706; }
.stat-icon-circle.purple { background: #f3e8ff; color: #7c3aed; }
.stat-icon-circle.red { background: #fef2f2; color: #dc2626; }
.stat-icon-circle.gray { background: #f5f7fa; color: #64748b; }
.stat-info { min-width: 0; flex: 1; }
.stat-label {
  font-size: 12px;
  color: #667085;
  margin-bottom: 2px;
}
.stat-value {
  font-size: 22px;
  font-weight: 700;
  color: #15223a;
  line-height: 1.2;
}
.stat-trend {
  font-size: 11px;
  margin-top: 2px;
}
.stat-trend.muted { color: #94a3b8; }
.stat-trend.down { color: #dc2626; }

.content-preview {
  max-width: 520px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.strong {
  font-weight: 600;
}

/* ===== 卡密发货相关样式 ===== */
.mode-cards {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px;
}

.mode-card {
  position: relative;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 16px 18px;
  border: 1.5px solid #dde1e8;
  border-radius: 10px;
  background: #f7f8fa;
  cursor: pointer;
  transition: all .18s ease;
  min-height: 76px;
}

.mode-card:hover {
  border-color: #bdd4f9;
  background: #f2f6fd;
}

.mode-card.active {
  border-color: #3b82f6;
  background: #f0f4ff;
  box-shadow: 0 0 0 1px rgba(59, 130, 246, .15);
}

.mode-card input[type='radio'] {
  position: absolute;
  opacity: 0;
  width: 0;
  height: 0;
}

.mode-card-radio {
  flex-shrink: 0;
  width: 20px;
  height: 20px;
  border: 2px solid #c5cad3;
  border-radius: 50%;
  transition: all .18s;
  position: relative;
  background: #fff;
}

.mode-card.active .mode-card-radio {
  border-color: #2563eb;
  border-width: 2px;
}

.mode-card.active .mode-card-radio::after {
  content: '';
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: #2563eb;
}

.mode-card-body {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.mode-card-title {
  font-size: 14px;
  font-weight: 600;
  color: #374151;
}

.mode-card.active .mode-card-title {
  color: #1e40af;
}

.mode-card-desc {
  font-size: 12px;
  color: #6b7280;
  line-height: 1.5;
}

.placeholder-btn {
  margin-left: auto;
  padding: 3px 12px;
  font-size: 12px;
  font-weight: 500;
  border: 1px solid #3b82f6;
  background: rgba(59, 130, 246, .06);
  color: #3b82f6;
  border-radius: 999px;
  cursor: pointer;
  transition: background .2s, color .2s;
}

.placeholder-btn:hover {
  background: #3b82f6;
  color: #fff;
}

.stock-cell {
  font-weight: 600;
  color: #059669;
}

.stock-cell.low {
  color: #dc2626;
}

.stock-display {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
}

.stock-label-text {
  color: #6b7280;
}

.stock-value-text {
  font-weight: 600;
  color: #16a34a;
}

.stock-value-text.low {
  color: #dc2626;
}

.goods-cell {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  min-width: 260px;
}

.goods-thumb {
  width: 48px;
  height: 48px;
  border-radius: 10px;
  object-fit: cover;
  background: #eef2ff;
  flex-shrink: 0;
}

.goods-thumb.placeholder,
.account-avatar.placeholder {
  background: #eef2ff;
}

.goods-main {
  min-width: 0;
}

.account-chip {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 6px;
}

.account-avatar {
  width: 20px;
  height: 20px;
  border-radius: 999px;
  object-fit: cover;
  flex-shrink: 0;
}

.avatar-placeholder {
  position: relative;
}

.avatar-placeholder::before {
  content: '';
  position: absolute;
  inset: 5px;
  border-radius: 999px;
  background: #cbd5e1;
}

.source-summary {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 12px;
}

.summary-item {
  padding: 14px 16px;
  border: 1px solid #e7ecf3;
  border-radius: 12px;
  background: #f8fafc;
}

.summary-label {
  font-size: 12px;
  color: #64748b;
  margin-bottom: 6px;
}

.summary-value {
  font-size: 18px;
  font-weight: 600;
  color: #0f172a;
}

.source-preview {
  white-space: pre-wrap;
  line-height: 1.6;
}

.ai-status-tip {
  margin: 0 0 10px;
  padding: 10px 12px;
  border: 1px solid #fde68a;
  border-radius: 10px;
  background: #fffbeb;
  color: #b45309;
  font-size: 12px;
  line-height: 1.6;
}

/* ───── 移动端适配 ───── */
@media (max-width: 900px) {
  /* 统计网格：5 列 → 2 列堆叠 */
  .stats-grid {
    grid-template-columns: repeat(2, 1fr);
    gap: 10px;
  }
  .mode-cards {
    grid-template-columns: 1fr;
  }
  .stat-card {
    padding: 10px 12px;
    gap: 10px;
  }
  .stat-icon-circle {
    width: 34px;
    height: 34px;
    font-size: 16px;
  }
  .stat-value {
    font-size: 18px;
  }
  .stat-label {
    font-size: 11px;
  }
  .stat-trend {
    font-size: 10px;
  }

  /* 货源正文预览宽度收窄并允许换行 */
  .content-preview {
    max-width: 100%;
    white-space: normal;
    word-break: break-word;
  }

  /* 商品单元格最小宽度解除，允许更紧凑展示 */
  .goods-cell {
    min-width: 0;
    gap: 8px;
  }
  .goods-thumb {
    width: 40px;
    height: 40px;
    border-radius: 8px;
  }

  .account-chip {
    gap: 5px;
    margin-top: 4px;
  }
  .account-avatar {
    width: 18px;
    height: 18px;
  }

  /* 货源摘要：3列 → 单列堆叠 */
  .source-summary {
    grid-template-columns: minmax(0, 1fr);
    gap: 10px;
    margin-bottom: 10px;
  }
  .source-summary > * {
    min-width: 0;
  }
  .summary-item {
    padding: 12px 14px;
  }
  .summary-label {
    font-size: 12px;
    margin-bottom: 4px;
  }
  .summary-value {
    font-size: 16px;
  }

  .source-preview {
    line-height: 1.5;
  }
}

/* ===== 新增货源 / 编辑货源 编辑器视觉（对齐商业版） ===== */
.editor-layout {
  display: grid;
  grid-template-columns: 1fr 420px;
  gap: 40px;
}

.editor-left {
  display: flex;
  flex-direction: column;
  gap: 22px;
  min-width: 0;
}

.editor-right {
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.form-field {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.field-label {
  display: flex;
  align-items: center;
  font-size: 14px;
  font-weight: 600;
  color: #1a2236;
}

.required {
  color: #ef4444;
  margin-right: 4px;
  font-size: 14px;
  line-height: 1;
}

.field-input-wrap {
  position: relative;
}

.field-input {
  width: 100%;
  height: 42px;
  padding: 0 14px;
  padding-right: 56px;
  border: 1px solid #e2e6ed;
  border-radius: 8px;
  background: #fff;
  font-size: 14px;
  color: #334155;
  transition: border-color .15s, box-shadow .15s;
  box-sizing: border-box;
  outline: none;
}

.field-input:focus {
  border-color: #3b82f6;
  box-shadow: 0 0 0 3px rgba(59, 130, 246, .1);
}

.field-input::placeholder {
  color: #b0b7c3;
}

select.field-input {
  padding-right: 40px;
  appearance: none;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='16' height='16' fill='%2394a3b8' viewBox='0 0 16 16'%3E%3Cpath d='M4.5 6l3.5 3.5L11.5 6' stroke='%2394a3b8' stroke-width='1.5' fill='none' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E");
  background-repeat: no-repeat;
  background-position: right 14px center;
  cursor: pointer;
}

.field-textarea {
  width: 100%;
  padding: 12px 14px;
  padding-right: 60px;
  padding-bottom: 28px;
  border: 1px solid #e2e6ed;
  border-radius: 8px;
  background: #fff;
  font-size: 14px;
  color: #334155;
  line-height: 1.7;
  resize: vertical;
  min-height: 160px;
  transition: border-color .15s, box-shadow .15s;
  box-sizing: border-box;
  outline: none;
  font-family: inherit;
}

.field-textarea:focus {
  border-color: #3b82f6;
  box-shadow: 0 0 0 3px rgba(59, 130, 246, .1);
}

.field-textarea::placeholder {
  color: #b0b7c3;
}

.char-count {
  position: absolute;
  right: 12px;
  bottom: 8px;
  font-size: 12px;
  color: #b0b7c3;
  pointer-events: none;
}

/* segments 编辑器（多条正文 + 图片发货） */
.segments-editor {
  display: flex;
  flex-direction: column;
  gap: 12px;
  width: 100%;
}

.segments-empty {
  padding: 10px 12px;
  font-size: 13px;
  color: #64748b;
  background: #f8fafc;
  border: 1px dashed #e2e6ed;
  border-radius: 10px;
  line-height: 1.6;
}

.segment-card {
  border: 1px solid #e2e6ed;
  border-radius: 12px;
  background: #fafbfc;
  padding: 14px 16px 12px;
  transition: border-color .2s, box-shadow .2s;
}

.segment-card:hover {
  border-color: #c5cee0;
  box-shadow: 0 1px 3px rgba(15, 23, 42, .04);
}

.segment-header {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}

.segment-index {
  font-size: 13px;
  font-weight: 600;
  color: #475569;
  flex-shrink: 0;
}

.segment-type-switch {
  display: inline-flex;
  border: 1px solid #d8deeb;
  border-radius: 999px;
  overflow: hidden;
  background: #fff;
}

.segment-type-btn {
  padding: 4px 14px;
  font-size: 12px;
  font-weight: 500;
  color: #64748b;
  background: transparent;
  border: none;
  cursor: pointer;
  transition: background .2s, color .2s;
}

.segment-type-btn.active {
  background: #0d6bff;
  color: #fff;
}

.segment-type-btn:not(.active):hover {
  background: #f1f5f9;
  color: #1a2236;
}

.segment-remove-btn {
  margin-left: auto;
  padding: 3px 10px;
  font-size: 12px;
  color: #dc2626;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 999px;
  cursor: pointer;
  transition: background .2s;
}

.segment-remove-btn:hover:not(:disabled) {
  background: rgba(220, 38, 38, .08);
  border-color: rgba(220, 38, 38, .2);
}

.segment-remove-btn:disabled {
  opacity: .45;
  cursor: not-allowed;
}

.segment-body {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.segment-text-area {
  position: relative;
}

.segment-text-area .field-textarea {
  background: #fff;
}

.segment-image-row {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}

.segment-image-url {
  flex: 1;
  min-width: 200px;
}

.segment-image-preview {
  margin-top: 6px;
}

.add-segment-btn {
  align-self: flex-start;
  padding: 7px 18px;
  font-size: 13px;
  font-weight: 500;
  color: #0d6bff;
  background: rgba(13, 107, 255, .04);
  border: 1px dashed #0d6bff;
  border-radius: 999px;
  cursor: pointer;
  transition: background .2s, color .2s, border-color .2s;
}

.add-segment-btn:hover {
  background: #0d6bff;
  color: #fff;
  border-style: solid;
}

.segments-tip {
  margin-top: 4px;
  padding: 8px 12px;
  font-size: 12px;
  color: #64748b;
  background: rgba(13, 107, 255, .04);
  border-radius: 8px;
  line-height: 1.5;
}

.setting-card {
  border: 1px solid #e2e6ed;
  border-radius: 12px;
  padding: 20px 22px;
  background: #fff;
}

.setting-card-title {
  font-size: 15px;
  font-weight: 700;
  color: #1a2236;
  margin-bottom: 14px;
  display: flex;
  align-items: center;
  gap: 8px;
}

.info-tip {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin-top: 12px;
  padding: 10px 14px;
  border-radius: 8px;
  background: #f0f5ff;
  border: 1px solid #dbeafe;
  color: #2563eb;
  font-size: 13px;
  line-height: 1.6;
}

.info-tip-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: #3b82f6;
  color: #fff;
  font-weight: 700;
  font-size: 11px;
  font-style: normal;
  flex-shrink: 0;
  margin-top: 2px;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
}

.info-tip code {
  padding: 1px 5px;
  background: #dbeafe;
  border-radius: 4px;
  font-family: 'SFMono-Regular', Consolas, monospace;
  color: #1d4ed8;
  font-size: 12px;
  font-weight: 600;
  margin: 0 1px;
}

.card-group-select {
  margin-top: 14px;
}

.form-actions {
  display: flex;
  gap: 12px;
  margin-top: 24px;
  padding-top: 0;
}

.save-btn:deep(.app-btn) {
  min-width: 110px !important;
  height: 44px !important;
  border-radius: 10px !important;
  font-weight: 600 !important;
  font-size: 15px !important;
  background: #2563eb !important;
  color: #fff !important;
  border: none !important;
  box-shadow: 0 4px 12px rgba(37, 99, 235, .28) !important;
  padding: 0 24px !important;
  transition: all .15s !important;
}

.save-btn:deep(.app-btn:hover) {
  background: #1d4ed8 !important;
  box-shadow: 0 6px 16px rgba(37, 99, 235, .35) !important;
}

.cancel-btn:deep(.app-btn) {
  min-width: 100px !important;
  height: 44px !important;
  border-radius: 10px !important;
  font-weight: 500 !important;
  font-size: 15px !important;
  border: 1px solid #d1d5db !important;
  background: #fff !important;
  color: #4b5563 !important;
  padding: 0 24px !important;
  box-shadow: none !important;
  transition: all .15s !important;
}

.cancel-btn:deep(.app-btn:hover) {
  border-color: #9ca3af !important;
  background: #f9fafb !important;
  color: #374151 !important;
}

@media (max-width: 900px) {
  .editor-layout {
    grid-template-columns: 1fr;
    gap: 24px;
  }
}

</style>