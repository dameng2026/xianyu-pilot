<template>
  <div class="rates-page">
    <!-- 页面标题区域 -->
    <div class="page-header-bar">
      <div class="page-header-left">
        <h1 class="page-title">评价管理</h1>
        <p class="page-subtitle">集中查看买家评价并对未评价订单进行卖家评价（仅鱼小铺账号可用）</p>
      </div>
      <div class="page-header-right">
        <div class="sync-status">
          <span v-if="syncing" class="sync-badge syncing">同步中...</span>
          <span v-else-if="lastSyncTime" class="sync-badge done">最后更新：{{ formatTime(lastSyncTime) }}</span>
          <span v-else class="sync-badge none">尚未同步</span>
        </div>
        <AppButton v-if="activeTab === 'list'" :loading="syncing" :disabled="!accountsAvailable || !accounts.length" class="btn-refresh" @click="onRefreshClick">
          <span class="refresh-icon">↻</span>
          {{ syncing ? '刷新中...' : '刷新' }}
        </AppButton>
        <AppButton v-else-if="activeTab === 'autoLogs'" :loading="autoLogsLoading" class="btn-refresh" @click="loadAutoLogsList">
          <span class="refresh-icon">↻</span>
          {{ autoLogsLoading ? '刷新中...' : '刷新日志' }}
        </AppButton>
      </div>
    </div>

    <!-- Tab 切换 -->
    <div class="tabs-bar">
      <button class="tab-item" :class="{ active: activeTab === 'list' }" @click="switchTab('list')">评价列表</button>
      <button class="tab-item" :class="{ active: activeTab === 'autoLogs' }" @click="switchTab('autoLogs')">
        自动评价日志
        <span v-if="autoLogsTotal > 0" class="tab-count">{{ autoLogsTotal }}</span>
      </button>
    </div>

    <!-- 全局提示 -->
    <div v-if="globalError" class="global-notice error">{{ globalError }}</div>
    <div v-if="globalSuccess" class="global-notice success">{{ globalSuccess }}</div>
    <div v-if="accountsLoadError && activeTab === 'list'" class="global-notice error">账号列表加载失败：{{ accountsLoadError }}</div>
    <div v-if="!accountsAvailable && !accountsLoading && activeTab === 'list'" class="global-notice warn">
      当前没有可用的鱼小铺账号，评价管理功能仅对鱼小铺账号开放。
    </div>

    <!-- ===== Tab: 评价列表 ===== -->
    <template v-if="activeTab === 'list'">
    <!-- 概览卡片 -->
    <div class="stats-grid">
      <div class="stat-card">
        <div class="stat-icon-circle blue"><span class="stat-icon-svg">📄</span></div>
        <div class="stat-info">
          <div class="stat-label">本地评价记录</div>
          <div class="stat-value">{{ formatNumber(overview.total) }}</div>
        </div>
      </div>
      <div class="stat-card">
        <div class="stat-icon-circle orange"><span class="stat-icon-svg">⏳</span></div>
        <div class="stat-info">
          <div class="stat-label">待评价</div>
          <div class="stat-value">{{ formatNumber(overview.pending) }}</div>
        </div>
      </div>
      <div class="stat-card">
        <div class="stat-icon-circle green"><span class="stat-icon-svg">✅</span></div>
        <div class="stat-info">
          <div class="stat-label">已评价</div>
          <div class="stat-value">{{ formatNumber(overview.done) }}</div>
        </div>
      </div>
      <div class="stat-card">
        <div class="stat-icon-circle gray"><span class="stat-icon-svg">🕒</span></div>
        <div class="stat-info">
          <div class="stat-label">最近同步</div>
          <div class="stat-value text-sm">{{ overview.lastSyncTime ? formatTime(overview.lastSyncTime) : '尚未同步' }}</div>
        </div>
      </div>
      <div class="stat-card">
        <div class="stat-icon-circle good"><span class="stat-icon-svg">👍</span></div>
        <div class="stat-info">
          <div class="stat-label">好评</div>
          <div class="stat-value">{{ formatNumber(overview.good) }}</div>
        </div>
      </div>
      <div class="stat-card">
        <div class="stat-icon-circle neutral"><span class="stat-icon-svg">😐</span></div>
        <div class="stat-info">
          <div class="stat-label">中评</div>
          <div class="stat-value">{{ formatNumber(overview.neutral) }}</div>
        </div>
      </div>
      <div class="stat-card">
        <div class="stat-icon-circle bad"><span class="stat-icon-svg">👎</span></div>
        <div class="stat-info">
          <div class="stat-label">差评</div>
          <div class="stat-value">{{ formatNumber(overview.bad) }}</div>
        </div>
      </div>
      <div class="stat-card">
        <div class="stat-icon-circle indigo"><span class="stat-icon-svg">🏷️</span></div>
        <div class="stat-info">
          <div class="stat-label">闲鱼评价总数</div>
          <div class="stat-value">{{ lastTotalCount != null ? formatNumber(lastTotalCount) : '—' }}</div>
          <div v-if="lastTotalCount != null && lastTotalCount > overview.total" class="stat-hint text-warn">
            还有 {{ formatNumber(lastTotalCount - overview.total) }} 条未拉取
          </div>
        </div>
      </div>
    </div>

    <!-- 筛选区域 -->
    <div class="filter-bar">
      <div class="filter-title">评价筛选</div>
      <div class="filter-row">
        <select v-model="query.accountId" class="filter-select" :disabled="!accountsAvailable" @change="onFilterChange">
          <option value="">{{ accountsAvailable ? '全部账号' : '账号列表不可用' }}</option>
          <option v-for="account in accounts" :key="account.id" :value="String(account.id)">
            {{ accountName(account) }}
          </option>
        </select>
        <select v-model="query.category" class="filter-select" @change="onFilterChange">
          <option value="all">全部</option>
          <option value="pending">待评价</option>
          <option value="done">已评价</option>
          <option value="good">好评</option>
          <option value="neutral">中评</option>
          <option value="bad">差评</option>
        </select>
        <div class="filter-search">
          <input v-model="query.keyword" class="search-input" placeholder="搜索订单号 / 商品ID / 商品标题 / 买家昵称" @keyup.enter="onFilterChange" />
          <span class="search-icon">🔍</span>
        </div>
        <AppButton type="primary" class="btn-query" @click="onFilterChange">查询</AppButton>
        <AppButton class="btn-reset" @click="resetFilters">重置</AppButton>
      </div>
      <div class="filter-tip">
        列表默认优先展示本地已缓存评价；如需拉取闲鱼最新评价，请点击右上角"刷新"。
      </div>
    </div>

    <!-- 加载状态 -->
    <div v-if="listLoading && !items.length" class="loading-state">
      <div class="spinner"></div>
      <div>正在加载缓存数据...</div>
    </div>

    <!-- 空状态 -->
    <div v-else-if="!items.length && !listLoading" class="empty-state">
      <div class="empty-icon">📭</div>
      <div class="empty-text">{{ emptyText }}</div>
      <AppButton v-if="!items.length && accountsAvailable" type="primary" @click="onRefreshClick">立即同步</AppButton>
    </div>

    <!-- 评价列表 -->
    <div v-else class="rates-table-wrap">
      <table class="rates-table">
        <thead>
          <tr>
            <th class="col-account">所属账号</th>
            <th class="col-buyer">买家信息</th>
            <th class="col-item">商品信息</th>
            <th class="col-order">订单号</th>
            <th class="col-status">订单状态</th>
            <th class="col-finish">完成时间</th>
            <th class="col-buyer-rate">买家评价</th>
            <th class="col-seller-rate">卖家评价</th>
            <th class="col-rate-status">评价状态</th>
            <th class="col-action">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in items" :key="`${item.accountId}-${item.externalOrderId}`">
            <td class="col-account">
              <div class="account-cell">
                <span class="account-name">{{ item.accountNickname || `账号#${item.accountId}` }}</span>
              </div>
            </td>
            <td class="col-buyer">
              <div class="buyer-cell">
                <img v-if="item.buyerIcon" :src="item.buyerIcon" class="buyer-avatar" alt="买家头像" @error="onAvatarError" />
                <div v-else class="buyer-avatar placeholder">👤</div>
                <span class="buyer-nick">{{ item.buyerNick || '匿名买家' }}</span>
              </div>
            </td>
            <td class="col-item">
              <div class="item-cell">
                <img v-if="item.itemPicUrl" :src="item.itemPicUrl" class="item-pic" alt="商品图" @error="onItemImageError" />
                <div v-else class="item-pic placeholder">📦</div>
                <div class="item-info">
                  <div class="item-title" :title="item.itemTitle || ''">{{ item.itemTitle || '未知商品' }}</div>
                  <div class="item-id">ID: {{ item.externalItemId || '-' }}</div>
                </div>
              </div>
            </td>
            <td class="col-order">
              <span class="order-id" :title="item.externalOrderId">{{ item.externalOrderId }}</span>
            </td>
            <td class="col-status">
              <span class="status-tag" :class="statusClass(item.orderStatus)">{{ item.orderStatus || '-' }}</span>
            </td>
            <td class="col-finish">
              <span v-if="item.finishTime">{{ formatTime(item.finishTime) }}</span>
              <span v-else class="text-muted">-</span>
            </td>
            <td class="col-buyer-rate">
              <div v-if="isBuyerPlaceholder(item)" class="rate-content">
                <div class="rate-level-row">
                  <span class="rate-level placeholder">默认评价</span>
                </div>
                <div v-if="item.buyerRateContent" class="rate-text text-muted" :title="item.buyerRateContent">{{ item.buyerRateContent }}</div>
                <div v-if="item.buyerRateTime" class="rate-time">{{ formatTime(item.buyerRateTime) }}</div>
              </div>
              <div v-else-if="item.buyerRateContent || item.buyerRateLevel" class="rate-content">
                <div class="rate-level-row">
                  <span class="rate-level" :class="rateLevelClass(item.buyerRateLevel)">{{ rateLevelText(item.buyerRateLevel) }}</span>
                </div>
                <div v-if="item.buyerRateContent" class="rate-text" :title="item.buyerRateContent">{{ item.buyerRateContent }}</div>
                <div v-if="item.buyerRateTime" class="rate-time">{{ formatTime(item.buyerRateTime) }}</div>
              </div>
              <span v-else class="text-muted">未评价</span>
            </td>
            <td class="col-seller-rate">
              <div v-if="item.hasSellerRate" class="rate-content">
                <div class="rate-level-row">
                  <span class="rate-level seller" :class="rateLevelClass(item.sellerRateLevel)">{{ rateLevelText(item.sellerRateLevel) }}</span>
                </div>
                <div v-if="item.sellerRateContent" class="rate-text" :title="item.sellerRateContent">{{ item.sellerRateContent }}</div>
                <div v-if="item.sellerRateTime" class="rate-time">{{ formatTime(item.sellerRateTime) }}</div>
              </div>
              <span v-else class="text-muted">未评价</span>
            </td>
            <td class="col-rate-status">
              <span v-if="item.hasSellerRate" class="rate-status-tag done">已评价</span>
              <span v-else-if="item.rateReviewable" class="rate-status-tag pending">待评价</span>
              <span v-else class="rate-status-tag unavailable">不可评价</span>
            </td>
            <td class="col-action">
              <AppButton v-if="canRate(item)" type="primary" size="small" @click="openRateDialog(item)">评价</AppButton>
              <AppButton v-else-if="item.hasSellerRate" size="small" disabled>已评价</AppButton>
              <span v-else class="text-muted text-sm">不可评价</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 分页 -->
    <div v-if="total > 0" class="pagination-wrap">
      <Pagination
        :current="query.page"
        :total="total"
        :page-size="query.pageSize"
        :sizes="[20, 50, 100]"
        @page-change="onPageChange"
        @size-change="onPageSizeChange"
      />
    </div>

    <!-- 评价弹窗 -->
    <div v-if="dialogVisible" class="rate-dialog-mask" @click.self="closeRateDialog">
      <div class="rate-dialog">
        <div class="dialog-header">
          <h3 class="dialog-title">评价订单</h3>
          <button class="dialog-close" @click="closeRateDialog">×</button>
        </div>
        <div class="dialog-body">
          <!-- 订单基本信息 -->
          <div class="dialog-order-info">
            <div class="dialog-info-row">
              <span class="info-label">所属账号：</span>
              <span class="info-value">{{ currentRateItem?.accountNickname || `账号#${currentRateItem?.accountId}` }}</span>
            </div>
            <div class="dialog-info-row">
              <span class="info-label">买家昵称：</span>
              <span class="info-value">{{ currentRateItem?.buyerNick || '匿名买家' }}</span>
            </div>
            <div class="dialog-info-row">
              <span class="info-label">商品标题：</span>
              <span class="info-value">{{ currentRateItem?.itemTitle || '未知商品' }}</span>
            </div>
            <div class="dialog-info-row">
              <span class="info-label">订单号：</span>
              <span class="info-value">{{ currentRateItem?.externalOrderId }}</span>
            </div>
            <div v-if="currentRateItem?.itemPicUrl" class="dialog-info-row">
              <span class="info-label">商品封面：</span>
              <img :src="currentRateItem.itemPicUrl" class="dialog-item-pic" alt="商品图" @error="onDialogImageError" />
            </div>
          </div>

          <!-- 评价等级 -->
          <div class="dialog-section">
            <div class="section-label">评价等级 <span class="required">*</span></div>
            <div class="rate-level-cards">
              <div class="rate-level-card" :class="{ selected: dialogForm.rate === 1 }" @click="selectRateLevel(1)">
                <div class="rate-level-icon good">👍</div>
                <div class="rate-level-name">好评</div>
                <div class="rate-level-desc">rate=1</div>
              </div>
              <div class="rate-level-card" :class="{ selected: dialogForm.rate === -1 }" @click="selectRateLevel(-1)">
                <div class="rate-level-icon neutral">😐</div>
                <div class="rate-level-name">中评</div>
                <div class="rate-level-desc">rate=-1</div>
              </div>
              <div class="rate-level-card" :class="{ selected: dialogForm.rate === 0 }" @click="selectRateLevel(0)">
                <div class="rate-level-icon bad">👎</div>
                <div class="rate-level-name">差评</div>
                <div class="rate-level-desc">rate=0</div>
              </div>
            </div>
            <div v-if="rateLevelWarning" class="rate-level-warning">{{ rateLevelWarning }}</div>
          </div>

          <!-- 匿名评价 -->
          <div class="dialog-section">
            <div class="section-label">匿名评价</div>
            <label class="anonymous-toggle">
              <input v-model="dialogForm.anonymous" type="checkbox" :disabled="submitting" />
              <span class="toggle-text">{{ dialogForm.anonymous ? '已选择匿名' : '不匿名（显示卖家信息）' }}</span>
            </label>
          </div>

          <!-- 评价内容 -->
          <div class="dialog-section">
            <div class="section-label">评价内容</div>
            <textarea
              v-model="dialogForm.feedback"
              class="feedback-input"
              :disabled="submitting"
              placeholder="请输入评价内容（选填，最多 500 字）"
              maxlength="500"
              rows="4"
            ></textarea>
            <div class="feedback-count">{{ dialogForm.feedback.length }} / 500</div>
          </div>
        </div>
        <div class="dialog-footer">
          <AppButton :disabled="submitting" @click="closeRateDialog">取消</AppButton>
          <AppButton type="primary" :loading="submitting" :disabled="!canSubmit" @click="submitRate">确认评价</AppButton>
        </div>
        <div v-if="dialogError" class="dialog-error">{{ dialogError }}</div>
      </div>
    </div>
    </template>
    <!-- ===== /Tab: 评价列表 ===== -->

    <!-- ===== Tab: 自动评价日志 ===== -->
    <template v-if="activeTab === 'autoLogs'">
      <!-- 调度器状态卡片 -->
      <div class="scheduler-status-card">
        <div class="scheduler-status-left">
          <div class="scheduler-status-title">调度器状态</div>
          <div class="scheduler-status-desc">
            <span v-if="autoLogsScheduler.running" class="status-tag success">运行中</span>
            <span v-else-if="autoLogsScheduler.started" class="status-tag warning">已启动但未运行</span>
            <span v-else class="status-tag danger">未启动</span>
            <span v-if="autoLogsScheduler.lastScanAt" class="scheduler-meta">
              最近扫描：{{ formatTime(autoLogsScheduler.lastScanAt) }}
            </span>
            <span v-if="autoLogsScheduler.lastScanResult?.hour != null" class="scheduler-meta">
              上次扫描小时：{{ autoLogsScheduler.lastScanResult.hour }}:00
            </span>
            <span v-if="autoLogsScheduler.lastScanResult?.due_accounts != null" class="scheduler-meta">
              待执行账号：{{ autoLogsScheduler.lastScanResult.due_accounts }}
            </span>
            <span v-if="autoLogsScheduler.lastScanResult?.accounts_run != null" class="scheduler-meta">
              成功执行：{{ autoLogsScheduler.lastScanResult.accounts_run }}
            </span>
            <span v-if="autoLogsScheduler.lastScanResult?.accounts_failed != null" class="scheduler-meta">
              执行失败：{{ autoLogsScheduler.lastScanResult.accounts_failed }}
            </span>
          </div>
          <div v-if="autoLogsScheduler.lastScanResult?.errors?.length" class="scheduler-errors">
            <div class="scheduler-errors-title">最近错误（最多10条）：</div>
            <ul>
              <li v-for="(err, idx) in autoLogsScheduler.lastScanResult.errors" :key="idx">{{ err }}</li>
            </ul>
          </div>
        </div>
      </div>

      <!-- 日志筛选 -->
      <div class="filter-bar">
        <div class="filter-title">自动评价日志筛选</div>
        <div class="filter-row">
          <select v-model="autoLogsQuery.accountId" class="filter-select" @change="onAutoLogsFilterChange">
            <option value="">全部账号</option>
            <option v-for="account in accounts" :key="account.id" :value="String(account.id)">
              {{ accountName(account) }}
            </option>
          </select>
          <AppButton type="primary" class="btn-query" @click="onAutoLogsFilterChange">查询</AppButton>
          <AppButton class="btn-reset" @click="resetAutoLogsFilters">重置</AppButton>
        </div>
        <div class="filter-tip">
          展示每个账号自动评价的执行结果：成功/失败/跳过统计、错误信息与每条订单的处理明细。
          点击"手动执行"可立即对选中账号触发一次自动评价。
        </div>
      </div>

      <!-- 全局提示 -->
      <div v-if="autoLogsError" class="global-notice error">{{ autoLogsError }}</div>
      <div v-if="autoLogsSuccess" class="global-notice success">{{ autoLogsSuccess }}</div>

      <!-- 加载状态 -->
      <div v-if="autoLogsLoading && !autoLogsItems.length" class="loading-state">
        <div class="spinner"></div>
        <div>正在加载自动评价日志...</div>
      </div>

      <!-- 空状态 -->
      <div v-else-if="!autoLogsItems.length && !autoLogsLoading" class="empty-state">
        <div class="empty-icon">📋</div>
        <div class="empty-text">暂无自动评价执行日志</div>
        <div class="empty-sub">启用账号的自动评价功能后，定时任务执行记录会展示在这里</div>
      </div>

      <!-- 日志列表 -->
      <div v-else class="auto-logs-table-wrap">
        <table class="auto-logs-table">
          <thead>
            <tr>
              <th class="col-run-time">执行时间</th>
              <th class="col-account">账号</th>
              <th class="col-trigger">触发方式</th>
              <th class="col-schedule-hour">配置时间</th>
              <th class="col-status">结果</th>
              <th class="col-stats">统计</th>
              <th class="col-duration">耗时</th>
              <th class="col-error">错误信息</th>
              <th class="col-action">操作</th>
            </tr>
          </thead>
          <tbody>
            <template v-for="log in autoLogsItems" :key="log.id">
              <tr>
                <td class="col-run-time">{{ formatTime(log.runTime) }}</td>
                <td class="col-account">{{ autoLogAccountName(log.accountId) }}</td>
                <td class="col-trigger">
                  <span class="trigger-tag" :class="log.triggerType === 'manual' ? 'manual' : 'scheduled'">
                    {{ log.triggerType === 'manual' ? '手动' : '定时' }}
                  </span>
                </td>
                <td class="col-schedule-hour">
                  <span v-if="log.scheduleHour != null">{{ String(log.scheduleHour).padStart(2, '0') }}:00</span>
                  <span v-else class="text-muted">-</span>
                </td>
                <td class="col-status">
                  <span class="log-status-tag" :class="autoLogStatusClass(log.status)">{{ autoLogStatusText(log.status) }}</span>
                </td>
                <td class="col-stats">
                  <div class="stats-cell">
                    <span class="stat-pill success" :title="'待评价：' + log.totalPending">待 {{ log.totalPending }}</span>
                    <span class="stat-pill good" :title="'成功评价：' + log.totalSuccess">成功 {{ log.totalSuccess }}</span>
                    <span class="stat-pill bad" :title="'失败：' + log.totalFailed">失败 {{ log.totalFailed }}</span>
                    <span class="stat-pill skip" :title="'跳过：' + log.totalSkipped">跳过 {{ log.totalSkipped }}</span>
                  </div>
                </td>
                <td class="col-duration">{{ log.durationSeconds?.toFixed(1) || '0.0' }}s</td>
                <td class="col-error">
                  <div v-if="log.errorMessage" class="error-text" :title="log.errorMessage">{{ log.errorMessage }}</div>
                  <span v-else class="text-muted">-</span>
                </td>
                <td class="col-action">
                  <AppButton
                    v-if="log.details && log.details.length"
                    size="small"
                    @click="toggleLogDetails(log.id)"
                  >{{ expandedLogIds.has(log.id) ? '收起明细' : '查看明细' }}</AppButton>
                  <span v-else class="text-muted text-sm">无明细</span>
                </td>
              </tr>
              <tr v-if="expandedLogIds.has(log.id) && log.details?.length" class="log-details-row">
                <td colspan="9">
                  <div class="log-details-wrap">
                    <div class="log-details-title">订单处理明细（{{ log.details.length }} 条）</div>
                    <table class="log-details-table">
                      <thead>
                        <tr>
                          <th>订单号</th>
                          <th>结果</th>
                          <th>原因/错误</th>
                        </tr>
                      </thead>
                      <tbody>
                        <tr v-for="(d, idx) in log.details" :key="idx">
                          <td class="detail-order">{{ d.orderId || '-' }}</td>
                          <td>
                            <span class="detail-status" :class="autoLogDetailStatusClass(d.status)">{{ autoLogDetailStatusText(d.status) }}</span>
                          </td>
                          <td class="detail-reason">{{ d.reason || d.error || '-' }}</td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>

      <!-- 分页 -->
      <div v-if="autoLogsTotal > 0" class="pagination-wrap">
        <Pagination
          :current="autoLogsQuery.page"
          :total="autoLogsTotal"
          :page-size="autoLogsQuery.pageSize"
          :sizes="[20, 50, 100]"
          @page-change="onAutoLogsPageChange"
          @size-change="onAutoLogsPageSizeChange"
        />
      </div>

      <!-- 手动触发区 -->
      <div class="manual-trigger-card">
        <div class="manual-trigger-title">手动触发自动评价</div>
        <div class="manual-trigger-row">
          <select v-model="manualTriggerAccountId" class="filter-select">
            <option value="">请选择账号</option>
            <option v-for="account in accounts" :key="account.id" :value="String(account.id)">
              {{ accountName(account) }}
            </option>
          </select>
          <AppButton
            type="primary"
            :loading="manualTriggering"
            :disabled="!manualTriggerAccountId"
            @click="onManualTrigger"
          >立即执行</AppButton>
        </div>
        <div class="manual-trigger-tip">
          手动触发会立即对该账号执行一次自动评价。执行前会校验：账号已开启自动评价、为鱼小铺账号、Cookie 有效、评价内容已配置。执行结果会写入日志列表。
        </div>
      </div>
    </template>
    <!-- ===== /Tab: 自动评价日志 ===== -->
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onBeforeUnmount, watch } from 'vue'
import AppButton from '../components/AppButton.vue'
import Pagination from '../components/Pagination.vue'
import {
  getRates,
  syncRates,
  getRateSyncStatus,
  getRateOverview,
  createRate,
  getRateFishShopAccounts,
  getAutoRateLogs,
  getAutoRateSchedulerStatus,
  triggerAutoRateRun,
} from '../api/rates'

// ============================================================
// 响应式状态
// ============================================================

const accounts = ref([])
const accountsLoading = ref(false)
const accountsLoadError = ref('')
const accountsAvailable = computed(() => accounts.value.length > 0)

const items = ref([])
const total = ref(0)
const listLoading = ref(false)

const overview = reactive({
  total: 0,
  pending: 0,
  done: 0,
  good: 0,
  neutral: 0,
  bad: 0,
  lastSyncTime: null,
})

const query = reactive({
  accountId: '',
  category: 'all',
  keyword: '',
  page: 1,
  pageSize: 20,
})

const syncing = ref(false)
const lastSyncTime = ref(null)
const cacheExpired = ref(false)
// 闲鱼返回的评价总数（同步时拉取，可能大于本地缓存数）
const lastTotalCount = ref(null)

const globalError = ref('')
const globalSuccess = ref('')

// 评价弹窗状态
const dialogVisible = ref(false)
const currentRateItem = ref(null)
const dialogForm = reactive({
  rate: 1,
  feedback: '',
  anonymous: true,
})
const submitting = ref(false)
const dialogError = ref('')
const rateLevelWarning = ref('')

// 轮询定时器
let pollTimer = null
let visibilityHandler = null
let focusHandler = null

// ============================================================
// Tab 切换 & 自动评价日志
// ============================================================

const activeTab = ref('list') // 'list' | 'autoLogs'

const autoLogsItems = ref([])
const autoLogsTotal = ref(0)
const autoLogsLoading = ref(false)
const autoLogsError = ref('')
const autoLogsSuccess = ref('')
const autoLogsQuery = reactive({
  accountId: '',
  page: 1,
  pageSize: 20,
})
// 调度器状态（展示调度器运行情况与最近一次扫描结果）
const autoLogsScheduler = reactive({
  started: false,
  running: false,
  lastScanAt: null,
  lastScanResult: null,
  scanIntervalSeconds: null,
})
// 展开的日志明细 id 集合
const expandedLogIds = ref(new Set())
// 手动触发
const manualTriggerAccountId = ref('')
const manualTriggering = ref(false)

// ============================================================
// 计算属性
// ============================================================

const canSubmit = computed(() => {
  // 已确认等级：好评(1)、中评(-1)、差评(0)（需求第一节、第五节）
  // 注意：rate=0（差评）是合法值，不可用 if(!rate) 判断（需求第六节）
  if (![1, -1, 0].includes(dialogForm.rate)) return false
  if (dialogForm.feedback.length > 500) return false
  return true
})

const emptyText = computed(() => {
  if (!accountsAvailable.value) return '当前没有可用的鱼小铺账号'
  if (listLoading.value) return '正在加载...'
  if (syncing.value) return '正在同步评价数据...'
  if (query.keyword) return '没有匹配的评价记录'
  if (query.category === 'pending') return '没有待评价的订单'
  if (query.category === 'done') return '没有已评价的订单'
  if (query.category === 'good') return '没有好评记录'
  if (query.category === 'neutral') return '没有中评记录'
  if (query.category === 'bad') return '没有差评记录'
  return '暂无评价记录，请点击"刷新"同步闲鱼数据'
})

// ============================================================
// 账号管理
// ============================================================

function accountName(account) {
  if (!account) return ''
  if (account.nickname) return account.nickname
  if (account.external_uid) return `账号${account.external_uid.slice(-6)}`
  return `账号#${account.id}`
}

async function loadAccounts() {
  accountsLoading.value = true
  accountsLoadError.value = ''
  try {
    const res = await getRateFishShopAccounts()
    accounts.value = res?.data?.accounts || []
  } catch (e) {
    accountsLoadError.value = e?.message || '未知错误'
    accounts.value = []
  } finally {
    accountsLoading.value = false
  }
}

// ============================================================
// 列表查询（缓存优先）
// ============================================================

async function loadList() {
  listLoading.value = true
  try {
    const params = {
      category: query.category,
      page: query.page,
      pageSize: query.pageSize,
    }
    if (query.accountId) params.accountId = Number(query.accountId)
    if (query.keyword && query.keyword.trim()) params.keyword = query.keyword.trim()
    const res = await getRates(params)
    items.value = res?.data?.items || []
    total.value = res?.data?.total || 0
  } catch (e) {
    // 不清空已有数据，避免闪烁
    globalError.value = `评价列表加载失败：${e?.message || '未知错误'}`
    setTimeout(() => { globalError.value = '' }, 5000)
  } finally {
    listLoading.value = false
  }
}

async function loadOverview() {
  try {
    const params = {}
    if (query.accountId) params.accountId = Number(query.accountId)
    const res = await getRateOverview(params.accountId)
    overview.total = res?.data?.total || 0
    overview.pending = res?.data?.pending || 0
    overview.done = res?.data?.done || 0
    overview.good = res?.data?.good || 0
    overview.neutral = res?.data?.neutral || 0
    overview.bad = res?.data?.bad || 0
    overview.lastSyncTime = res?.data?.lastSyncTime || null
  } catch {
    // 概览加载失败不阻塞
  }
}

async function loadSyncStatus() {
  try {
    const params = {}
    if (query.accountId) params.accountId = Number(query.accountId)
    const res = await getRateSyncStatus(params.accountId)
    syncing.value = !!res?.data?.isSyncing
    lastSyncTime.value = res?.data?.lastSyncTime || null
    cacheExpired.value = !!res?.data?.cacheExpired
    // 闲鱼评价总数（单账号模式返回 lastTotalCount，全部账号模式不返回）
    if (res?.data?.lastTotalCount != null) {
      lastTotalCount.value = res.data.lastTotalCount
    } else if (res?.data?.hasCache === false) {
      lastTotalCount.value = null
    }
    // 不在此处自动触发同步，统一由 onMounted / onRefreshClick 控制，避免重复触发
  } catch {
    // 状态查询失败不阻塞
  }
}

// ============================================================
// 同步触发
// ============================================================

async function triggerBackgroundSync(forceFull = false) {
  if (syncing.value) return
  syncing.value = true
  try {
    const data = {}
    if (query.accountId) data.accountId = Number(query.accountId)
    if (forceFull) data.forceFull = true
    await syncRates(data)
    // 同步完成后刷新数据（无闪烁合并）
    await Promise.all([loadList(), loadOverview(), loadSyncStatus()])
  } catch (e) {
    // 后台同步失败不阻塞，保留旧缓存
    globalError.value = `后台同步失败：${e?.message || '未知错误'}`
    setTimeout(() => { globalError.value = '' }, 5000)
    await loadSyncStatus()
  }
}

async function onRefreshClick() {
  if (syncing.value) return
  if (!accountsAvailable.value) {
    globalError.value = '当前没有可用的鱼小铺账号'
    setTimeout(() => { globalError.value = '' }, 3000)
    return
  }
  syncing.value = true
  try {
    const data = {}
    if (query.accountId) data.accountId = Number(query.accountId)
    // 本地缓存为空时强制全量同步，确保一次性获取全部评价
    if (overview.total === 0) data.forceFull = true
    const res = await syncRates(data)
    if (res?.data?.alreadyRunning) {
      globalSuccess.value = '该账号正在同步中，请稍后刷新查看'
    } else if (overview.total === 0) {
      globalSuccess.value = '全量同步已完成'
    } else {
      globalSuccess.value = '同步已完成'
    }
    setTimeout(() => { globalSuccess.value = '' }, 3000)
    await Promise.all([loadList(), loadOverview(), loadSyncStatus()])
  } catch (e) {
    globalError.value = `刷新失败：${e?.message || '未知错误'}`
    setTimeout(() => { globalError.value = '' }, 5000)
    await loadSyncStatus()
  } finally {
    syncing.value = false
  }
}

// ============================================================
// 筛选与分页
// ============================================================

function onFilterChange() {
  query.page = 1
  loadList()
  loadOverview()
}

function resetFilters() {
  query.accountId = ''
  query.category = 'all'
  query.keyword = ''
  query.page = 1
  loadList()
  loadOverview()
}

function onPageChange(page) {
  query.page = page
  loadList()
}

function onPageSizeChange(size) {
  query.pageSize = size
  query.page = 1
  loadList()
}

// ============================================================
// 评价弹窗
// ============================================================

function canRate(item) {
  // 仅当未评价且 rateReviewable=1 时显示评价按钮
  return !item.hasSellerRate && item.rateReviewable
}

function openRateDialog(item) {
  if (!canRate(item)) return
  currentRateItem.value = item
  dialogForm.rate = 1  // 默认好评
  dialogForm.feedback = ''
  dialogForm.anonymous = true
  dialogError.value = ''
  rateLevelWarning.value = ''
  dialogVisible.value = true
}

function closeRateDialog() {
  if (submitting.value) return  // 提交中不允许关闭
  dialogVisible.value = false
  currentRateItem.value = null
  dialogError.value = ''
  rateLevelWarning.value = ''
}

function selectRateLevel(level) {
  if (submitting.value) return
  // 已确认等级：好评(1)、中评(-1)、差评(0)（需求第五节）
  // 注意：差评 level=0 是合法值，不可用 if(!level) 拦截（需求第六节）
  if (![1, -1, 0].includes(level)) return
  dialogForm.rate = level
  dialogError.value = ''
  rateLevelWarning.value = ''
}

async function submitRate() {
  if (submitting.value) return
  if (!canSubmit.value) {
    if (![1, -1, 0].includes(dialogForm.rate)) {
      dialogError.value = '评价等级不合法，仅支持好评(1)、中评(-1)、差评(0)。'
    }
    return
  }
  const item = currentRateItem.value
  if (!item) return

  submitting.value = true
  dialogError.value = ''
  try {
    await createRate({
      accountId: Number(item.accountId),
      orderId: String(item.externalOrderId),
      rate: dialogForm.rate,  // 提交用户实际选择的等级（需求第五节）
      feedback: dialogForm.feedback.trim(),
      anonymous: !!dialogForm.anonymous,
    })
    globalSuccess.value = '评价已提交'
    setTimeout(() => { globalSuccess.value = '' }, 3000)
    dialogVisible.value = false
    currentRateItem.value = null
    // 刷新列表与概览（需求第十四节：更新等级标签、统计等）
    await Promise.all([loadList(), loadOverview(), loadSyncStatus()])
  } catch (e) {
    // 失败后保留用户输入，不改变本地等级（需求第十八节）
    dialogError.value = e?.message || '创建评价失败，请稍后重试'
  } finally {
    submitting.value = false
  }
}

// ============================================================
// Tab 切换 & 自动评价日志
// ============================================================

function switchTab(tab) {
  if (activeTab.value === tab) return
  activeTab.value = tab
  if (tab === 'autoLogs') {
    // 首次切换到日志 tab 时加载数据；账号列表若未加载则一并加载
    if (!accounts.value.length && !accountsLoading.value) {
      loadAccounts()
    }
    if (!autoLogsItems.value.length) {
      loadAutoLogsList()
    }
    loadAutoRateSchedulerStatus()
  }
}

async function loadAutoLogsList() {
  autoLogsLoading.value = true
  autoLogsError.value = ''
  try {
    const params = {
      page: autoLogsQuery.page,
      pageSize: autoLogsQuery.pageSize,
    }
    if (autoLogsQuery.accountId) params.accountId = Number(autoLogsQuery.accountId)
    const res = await getAutoRateLogs(params)
    autoLogsItems.value = res?.data?.records || []
    autoLogsTotal.value = res?.data?.total || 0
  } catch (e) {
    autoLogsError.value = `日志加载失败：${e?.message || '未知错误'}`
  } finally {
    autoLogsLoading.value = false
  }
}

async function loadAutoRateSchedulerStatus() {
  try {
    const res = await getAutoRateSchedulerStatus()
    const data = res?.data || {}
    autoLogsScheduler.started = !!data.started
    autoLogsScheduler.running = !!data.running
    autoLogsScheduler.lastScanAt = data.lastScanAt || null
    autoLogsScheduler.lastScanResult = data.lastScanResult || null
    autoLogsScheduler.scanIntervalSeconds = data.scanIntervalSeconds || null
  } catch {
    // 调度器状态查询失败不阻塞日志展示
  }
}

function onAutoLogsFilterChange() {
  autoLogsQuery.page = 1
  loadAutoLogsList()
}

function resetAutoLogsFilters() {
  autoLogsQuery.accountId = ''
  autoLogsQuery.page = 1
  loadAutoLogsList()
}

function onAutoLogsPageChange(page) {
  autoLogsQuery.page = page
  loadAutoLogsList()
}

function onAutoLogsPageSizeChange(size) {
  autoLogsQuery.pageSize = size
  autoLogsQuery.page = 1
  loadAutoLogsList()
}

function toggleLogDetails(logId) {
  const set = new Set(expandedLogIds.value)
  if (set.has(logId)) {
    set.delete(logId)
  } else {
    set.add(logId)
  }
  expandedLogIds.value = set
}

function autoLogAccountName(accountId) {
  const acc = accounts.value.find(a => a.id === accountId)
  if (!acc) return `账号#${accountId}`
  return accountName(acc)
}

function autoLogStatusClass(status) {
  if (status === 'success') return 'success'
  if (status === 'skip') return 'skip'
  if (status === 'failed') return 'failed'
  if (status === 'partial') return 'partial'
  return 'unknown'
}

function autoLogStatusText(status) {
  if (status === 'success') return '成功'
  if (status === 'skip') return '跳过'
  if (status === 'failed') return '失败'
  if (status === 'partial') return '部分成功'
  return '未知'
}

function autoLogDetailStatusClass(status) {
  if (status === 'success') return 'success'
  if (status === 'skipped') return 'skip'
  if (status === 'failed') return 'failed'
  return 'unknown'
}

function autoLogDetailStatusText(status) {
  if (status === 'success') return '成功'
  if (status === 'skipped') return '跳过'
  if (status === 'failed') return '失败'
  return '未知'
}

async function onManualTrigger() {
  if (manualTriggering.value) return
  if (!manualTriggerAccountId.value) {
    autoLogsError.value = '请选择要触发的账号'
    setTimeout(() => { autoLogsError.value = '' }, 3000)
    return
  }
  manualTriggering.value = true
  autoLogsError.value = ''
  autoLogsSuccess.value = ''
  try {
    const res = await triggerAutoRateRun({ accountId: Number(manualTriggerAccountId.value) })
    if (res?.data?.alreadyInProgress) {
      autoLogsSuccess.value = '该账号正在执行自动评价，请稍后查看日志'
    } else {
      const summary = res?.data?.summary || {}
      const stat = `待评价 ${summary.totalPending ?? 0}，成功 ${summary.totalSuccess ?? 0}，失败 ${summary.totalFailed ?? 0}，跳过 ${summary.totalSkipped ?? 0}`
      autoLogsSuccess.value = `执行完成：${stat}`
    }
    setTimeout(() => { autoLogsSuccess.value = '' }, 6000)
    // 刷新日志列表
    await loadAutoLogsList()
  } catch (e) {
    autoLogsError.value = `手动触发失败：${e?.message || '未知错误'}`
    setTimeout(() => { autoLogsError.value = '' }, 6000)
  } finally {
    manualTriggering.value = false
  }
}

// ============================================================
// 格式化与工具
// ============================================================

function formatNumber(n) {
  if (n === null || n === undefined) return '0'
  return Number(n).toLocaleString('zh-CN')
}

function formatTime(time) {
  if (!time) return '-'
  try {
    const d = new Date(time)
    if (isNaN(d.getTime())) return String(time)
    const yyyy = d.getFullYear()
    const mm = String(d.getMonth() + 1).padStart(2, '0')
    const dd = String(d.getDate()).padStart(2, '0')
    const hh = String(d.getHours()).padStart(2, '0')
    const mi = String(d.getMinutes()).padStart(2, '0')
    return `${yyyy}-${mm}-${dd} ${hh}:${mi}`
  } catch {
    return String(time)
  }
}

function statusClass(status) {
  if (!status) return ''
  const s = String(status)
  if (s.includes('成功') || s.includes('完成')) return 'success'
  if (s.includes('退款') || s.includes('关闭')) return 'danger'
  if (s.includes('发货') || s.includes('进行')) return 'warning'
  return 'info'
}

function rateLevelClass(level) {
  // 评价等级映射（需求第十节、第一节）：
  // rate=1 → 好评、rate=-1 → 中评、rate=0 → 差评
  // 兼容数字与字符串（需求第七节）：1/"1"、-1/"-1"、0/"0"
  if (level === null || level === undefined) return 'unknown'
  const lv = String(level)
  if (lv === '1') return 'good'
  if (lv === '-1') return 'neutral'
  if (lv === '0') return 'bad'
  return 'unknown'
}

function rateLevelText(level) {
  // 评价等级文案（需求第十节）
  if (level === null || level === undefined) return '未知'
  const lv = String(level)
  if (lv === '1') return '好评'
  if (lv === '-1') return '中评'
  if (lv === '0') return '差评'
  return '未知'
}

function isBuyerPlaceholder(item) {
  // 检测买家侧"未做出评价内容"占位记录（需求第十一节）
  // 不得无条件把买家侧 rate=-1 显示为中评
  const content = item?.buyerRateContent
  if (!content || typeof content !== 'string') return false
  return content.includes('未做出评价')
}

function onAvatarError(e) {
  e.target.style.display = 'none'
}

function onItemImageError(e) {
  e.target.style.display = 'none'
}

function onDialogImageError(e) {
  e.target.style.display = 'none'
}

// ============================================================
// 轮询与可见性控制（需求第九节）
// ============================================================

const POLL_INTERVAL = 60 * 1000  // 60秒轮询一次

function startPolling() {
  stopPolling()
  pollTimer = setInterval(async () => {
    // 浏览器标签页隐藏时降低或暂停前台轮询
    if (document.hidden) return
    await loadSyncStatus()
    // 同步完成后无闪烁合并更新页面
    if (!syncing.value) {
      await loadList()
      await loadOverview()
    }
  }, POLL_INTERVAL)
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

function onVisibilityChange() {
  if (!document.hidden) {
    // 页面重新获得焦点时，如果数据已过期，立即触发一次刷新
    loadSyncStatus().then(() => {
      if (cacheExpired.value && !syncing.value && accountsAvailable.value) {
        // 本地缓存为空时全量同步，否则快速刷新
        triggerBackgroundSync(overview.total === 0)
      }
    })
  }
}

function onFocus() {
  onVisibilityChange()
}

// ============================================================
// 生命周期
// ============================================================

onMounted(async () => {
  await loadAccounts()
  // 缓存优先：立即展示本地数据，不等待网络完成
  await Promise.all([loadList(), loadOverview(), loadSyncStatus()])
  // 启动轮询
  startPolling()
  visibilityHandler = onVisibilityChange
  focusHandler = onFocus
  document.addEventListener('visibilitychange', visibilityHandler)
  window.addEventListener('focus', focusHandler)
  // 如果有鱼小铺账号且未在同步中，主动触发同步
  // - 本地缓存为空：强制全量同步（一次性获取全部评价）
  // - 本地缓存不为空但缓存过期：快速刷新
  if (accountsAvailable.value && !syncing.value) {
    const forceFull = overview.total === 0
    if (forceFull || cacheExpired.value) {
      triggerBackgroundSync(forceFull)
    }
  }
})

onBeforeUnmount(() => {
  stopPolling()
  if (visibilityHandler) {
    document.removeEventListener('visibilitychange', visibilityHandler)
  }
  if (focusHandler) {
    window.removeEventListener('focus', focusHandler)
  }
})

// 切换账号时刷新同步状态
watch(() => query.accountId, () => {
  loadSyncStatus()
})
</script>

<style scoped>
.rates-page {
  padding: 20px;
  width: 100%;
  min-width: 0;
  box-sizing: border-box;
}

.page-header-bar {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 20px;
  padding: 16px 20px;
  background: #fff;
  border-radius: 10px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.06);
}

.page-title {
  margin: 0 0 6px 0;
  font-size: 20px;
  font-weight: 600;
  color: #1f2937;
}

.page-subtitle {
  margin: 0;
  font-size: 13px;
  color: #6b7280;
}

.page-header-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.sync-status {
  font-size: 13px;
}

.sync-badge {
  padding: 4px 10px;
  border-radius: 12px;
  font-size: 12px;
}

.sync-badge.syncing {
  background: #fef3c7;
  color: #92400e;
}

.sync-badge.done {
  background: #ecfdf5;
  color: #065f46;
}

.sync-badge.none {
  background: #f3f4f6;
  color: #6b7280;
}

.btn-refresh .refresh-icon {
  margin-right: 4px;
}

.global-notice {
  padding: 10px 14px;
  border-radius: 8px;
  margin-bottom: 12px;
  font-size: 14px;
}

.global-notice.error {
  background: #fef2f2;
  color: #991b1b;
  border: 1px solid #fecaca;
}

.global-notice.success {
  background: #f0fdf4;
  color: #166534;
  border: 1px solid #bbf7d0;
}

.global-notice.warn {
  background: #fffbeb;
  color: #92400e;
  border: 1px solid #fde68a;
}

.stats-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 14px;
  margin-bottom: 20px;
}

.stat-card {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 16px 18px;
  background: #fff;
  border-radius: 10px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.06);
}

.stat-icon-circle {
  width: 44px;
  height: 44px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
  flex-shrink: 0;
}

.stat-icon-circle.blue { background: #dbeafe; }
.stat-icon-circle.indigo { background: #e0e7ff; }
.stat-icon-circle.orange { background: #fed7aa; }
.stat-icon-circle.green { background: #d1fae5; }
.stat-icon-circle.gray { background: #f3f4f6; }
.stat-icon-circle.good { background: #d1fae5; }
.stat-icon-circle.neutral { background: #fef3c7; }
.stat-icon-circle.bad { background: #fee2e2; }

.stat-hint {
  font-size: 11px;
  margin-top: 4px;
}

.stat-hint.text-warn {
  color: #b45309;
}

.stat-label {
  font-size: 13px;
  color: #6b7280;
}

.stat-value {
  font-size: 22px;
  font-weight: 600;
  color: #1f2937;
  margin-top: 4px;
}

.stat-value.text-sm {
  font-size: 13px;
  font-weight: 500;
}

.filter-bar {
  padding: 16px 20px;
  background: #fff;
  border-radius: 10px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.06);
  margin-bottom: 16px;
}

.filter-title {
  font-size: 14px;
  font-weight: 600;
  color: #374151;
  margin-bottom: 12px;
}

.filter-row {
  display: flex;
  gap: 10px;
  align-items: center;
  flex-wrap: wrap;
}

.filter-select {
  padding: 8px 12px;
  border: 1px solid #d1d5db;
  border-radius: 8px;
  font-size: 14px;
  background: #fff;
  min-width: 140px;
  cursor: pointer;
}

.filter-select:disabled {
  background: #f9fafb;
  cursor: not-allowed;
}

.filter-search {
  position: relative;
  flex: 1;
  min-width: 220px;
}

.search-input {
  width: 100%;
  padding: 8px 32px 8px 12px;
  border: 1px solid #d1d5db;
  border-radius: 8px;
  font-size: 14px;
}

.search-icon {
  position: absolute;
  right: 10px;
  top: 50%;
  transform: translateY(-50%);
  color: #9ca3af;
}

.btn-query,
.btn-reset,
.btn-sync {
  padding: 8px 16px;
}

.filter-tip {
  margin-top: 10px;
  font-size: 12px;
  color: #9ca3af;
}

.loading-state,
.empty-state {
  padding: 60px 20px;
  text-align: center;
  background: #fff;
  border-radius: 10px;
  color: #6b7280;
}

.spinner {
  width: 36px;
  height: 36px;
  border: 3px solid #e5e7eb;
  border-top-color: #3b82f6;
  border-radius: 50%;
  margin: 0 auto 12px;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.empty-icon {
  font-size: 48px;
  margin-bottom: 12px;
}

.empty-text {
  margin-bottom: 16px;
  font-size: 14px;
}

.rates-table-wrap {
  background: #fff;
  border-radius: 10px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.06);
  overflow-x: auto;
}

.rates-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.rates-table th {
  padding: 12px 10px;
  text-align: left;
  font-weight: 600;
  color: #374151;
  background: #f9fafb;
  border-bottom: 1px solid #e5e7eb;
  white-space: nowrap;
}

.rates-table td {
  padding: 12px 10px;
  border-bottom: 1px solid #f3f4f6;
  vertical-align: middle;
}

.rates-table tr:hover td {
  background: #f9fafb;
}

.col-account { min-width: 120px; }
.col-buyer { min-width: 140px; }
.col-item { min-width: 200px; }
.col-order { min-width: 140px; }
.col-status { min-width: 100px; }
.col-finish { min-width: 130px; }
.col-buyer-rate { min-width: 180px; }
.col-seller-rate { min-width: 180px; }
.col-rate-status { min-width: 90px; }
.col-action { min-width: 90px; }

.account-name {
  font-weight: 500;
  color: #1f2937;
}

.buyer-cell,
.item-cell {
  display: flex;
  align-items: center;
  gap: 8px;
}

.buyer-avatar,
.item-pic {
  width: 36px;
  height: 36px;
  border-radius: 6px;
  object-fit: cover;
  flex-shrink: 0;
}

.buyer-avatar.placeholder,
.item-pic.placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  background: #f3f4f6;
  font-size: 18px;
}

.buyer-nick {
  color: #374151;
  font-size: 13px;
}

.item-info {
  flex: 1;
  min-width: 0;
}

.item-title {
  font-size: 13px;
  color: #1f2937;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 160px;
}

.item-id {
  font-size: 11px;
  color: #9ca3af;
  margin-top: 2px;
}

.order-id {
  font-family: monospace;
  font-size: 12px;
  color: #4b5563;
}

.status-tag {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 12px;
}

.status-tag.success { background: #d1fae5; color: #065f46; }
.status-tag.danger { background: #fee2e2; color: #991b1b; }
.status-tag.warning { background: #fef3c7; color: #92400e; }
.status-tag.info { background: #dbeafe; color: #1e40af; }

.rate-content {
  font-size: 12px;
}

.rate-level-row {
  margin-bottom: 4px;
}

.rate-level {
  display: inline-block;
  padding: 1px 6px;
  border-radius: 3px;
  font-size: 11px;
  background: #f3f4f6;
  color: #6b7280;
}

.rate-level.good { background: #d1fae5; color: #065f46; }
.rate-level.neutral { background: #fef3c7; color: #92400e; }
.rate-level.bad { background: #fee2e2; color: #991b1b; }
.rate-level.placeholder { background: #f3f4f6; color: #9ca3af; }
.rate-level.unknown { background: #f3f4f6; color: #6b7280; }
.rate-level.seller { background: #dbeafe; color: #1e40af; }

.rate-text {
  color: #374151;
  margin-bottom: 4px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 160px;
}

.rate-time {
  font-size: 11px;
  color: #9ca3af;
}

.text-muted {
  color: #9ca3af;
}

.text-sm {
  font-size: 12px;
}

.rate-status-tag {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 12px;
}

.rate-status-tag.done { background: #d1fae5; color: #065f46; }
.rate-status-tag.pending { background: #fef3c7; color: #92400e; }
.rate-status-tag.unavailable { background: #f3f4f6; color: #9ca3af; }

.pagination-wrap {
  margin-top: 20px;
  display: flex;
  justify-content: center;
}

/* 评价弹窗 */
.rate-dialog-mask {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}

.rate-dialog {
  background: #fff;
  border-radius: 12px;
  width: 90%;
  max-width: 560px;
  max-height: 90vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.dialog-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 20px;
  border-bottom: 1px solid #e5e7eb;
}

.dialog-title {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: #1f2937;
}

.dialog-close {
  border: none;
  background: none;
  font-size: 24px;
  color: #9ca3af;
  cursor: pointer;
  padding: 0;
  line-height: 1;
}

.dialog-body {
  padding: 20px;
  overflow-y: auto;
  flex: 1;
}

.dialog-order-info {
  background: #f9fafb;
  border-radius: 8px;
  padding: 12px;
  margin-bottom: 20px;
}

.dialog-info-row {
  display: flex;
  margin-bottom: 8px;
  font-size: 13px;
}

.dialog-info-row:last-child {
  margin-bottom: 0;
}

.info-label {
  color: #6b7280;
  width: 80px;
  flex-shrink: 0;
}

.info-value {
  color: #1f2937;
  flex: 1;
  word-break: break-all;
}

.dialog-item-pic {
  width: 60px;
  height: 60px;
  border-radius: 6px;
  object-fit: cover;
}

.dialog-section {
  margin-bottom: 20px;
}

.section-label {
  font-size: 14px;
  font-weight: 600;
  color: #374151;
  margin-bottom: 10px;
}

.required {
  color: #ef4444;
}

.rate-level-cards {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 10px;
}

.rate-level-card {
  border: 2px solid #e5e7eb;
  border-radius: 8px;
  padding: 12px;
  text-align: center;
  cursor: pointer;
  transition: all 0.2s;
}

.rate-level-card:hover {
  border-color: #93c5fd;
}

.rate-level-card.selected {
  border-color: #3b82f6;
  background: #eff6ff;
}

.rate-level-card.disabled {
  opacity: 0.6;
}

.rate-level-icon {
  font-size: 24px;
  margin-bottom: 4px;
}

.rate-level-name {
  font-size: 14px;
  font-weight: 600;
  color: #1f2937;
}

.rate-level-desc {
  font-size: 11px;
  color: #9ca3af;
  margin-top: 2px;
}

.rate-level-warning {
  margin-top: 8px;
  padding: 8px 10px;
  background: #fffbeb;
  border: 1px solid #fde68a;
  border-radius: 6px;
  font-size: 12px;
  color: #92400e;
}

.anonymous-toggle {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
}

.anonymous-toggle input[type="checkbox"] {
  width: 16px;
  height: 16px;
  cursor: pointer;
}

.toggle-text {
  font-size: 13px;
  color: #374151;
}

.feedback-input {
  width: 100%;
  padding: 10px;
  border: 1px solid #d1d5db;
  border-radius: 8px;
  font-size: 14px;
  font-family: inherit;
  resize: vertical;
  box-sizing: border-box;
}

.feedback-input:focus {
  outline: none;
  border-color: #3b82f6;
}

.feedback-count {
  text-align: right;
  font-size: 12px;
  color: #9ca3af;
  margin-top: 4px;
}

.dialog-footer {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding: 16px 20px;
  border-top: 1px solid #e5e7eb;
}

.dialog-error {
  padding: 10px 20px;
  background: #fef2f2;
  color: #991b1b;
  font-size: 13px;
  border-top: 1px solid #fecaca;
}

/* ===== Tab 切换栏 ===== */
.tabs-bar {
  display: flex;
  gap: 4px;
  margin-bottom: 16px;
  padding: 6px;
  background: #fff;
  border-radius: 10px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.06);
}

.tab-item {
  position: relative;
  padding: 8px 18px;
  border: none;
  background: transparent;
  border-radius: 6px;
  font-size: 14px;
  color: #6b7280;
  cursor: pointer;
  transition: all 0.2s;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.tab-item:hover {
  color: #3b82f6;
  background: #eff6ff;
}

.tab-item.active {
  color: #fff;
  background: #3b82f6;
  font-weight: 500;
}

.tab-item.active:hover {
  color: #fff;
}

.tab-count {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 18px;
  height: 18px;
  padding: 0 5px;
  border-radius: 9px;
  background: rgba(255, 255, 255, 0.3);
  color: inherit;
  font-size: 11px;
  font-weight: 500;
}

.tab-item:not(.active) .tab-count {
  background: #e5e7eb;
  color: #6b7280;
}

/* ===== 自动评价日志 Tab ===== */
.scheduler-status-card {
  background: #fff;
  border-radius: 10px;
  padding: 16px 20px;
  margin-bottom: 16px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.06);
}

.scheduler-status-left {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.scheduler-status-title {
  font-size: 15px;
  font-weight: 600;
  color: #1f2937;
}

.scheduler-status-desc {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  font-size: 13px;
  color: #4b5563;
}

.scheduler-meta {
  color: #6b7280;
}

.scheduler-errors {
  margin-top: 8px;
  padding: 10px 12px;
  background: #fef2f2;
  border: 1px solid #fecaca;
  border-radius: 6px;
  font-size: 12px;
  color: #991b1b;
}

.scheduler-errors-title {
  font-weight: 600;
  margin-bottom: 4px;
}

.scheduler-errors ul {
  margin: 0;
  padding-left: 18px;
}

.scheduler-errors li {
  margin-bottom: 2px;
  word-break: break-all;
}

.auto-logs-table-wrap {
  background: #fff;
  border-radius: 10px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.06);
  overflow-x: auto;
}

.auto-logs-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.auto-logs-table th {
  padding: 12px 10px;
  text-align: left;
  font-weight: 600;
  color: #374151;
  background: #f9fafb;
  border-bottom: 1px solid #e5e7eb;
  white-space: nowrap;
}

.auto-logs-table td {
  padding: 12px 10px;
  border-bottom: 1px solid #f3f4f6;
  vertical-align: middle;
}

.auto-logs-table tr:hover td {
  background: #f9fafb;
}

.auto-logs-table .log-details-row:hover td {
  background: #fff;
}

.col-run-time { white-space: nowrap; }
.col-schedule-hour { white-space: nowrap; }
.col-duration { white-space: nowrap; color: #6b7280; }

.trigger-tag {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 12px;
}

.trigger-tag.scheduled { background: #dbeafe; color: #1e40af; }
.trigger-tag.manual { background: #fef3c7; color: #92400e; }

.log-status-tag {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 500;
}

.log-status-tag.success { background: #d1fae5; color: #065f46; }
.log-status-tag.skip { background: #f3f4f6; color: #6b7280; }
.log-status-tag.failed { background: #fee2e2; color: #991b1b; }
.log-status-tag.partial { background: #fef3c7; color: #92400e; }
.log-status-tag.unknown { background: #f3f4f6; color: #6b7280; }

.stats-cell {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.stat-pill {
  display: inline-block;
  padding: 1px 6px;
  border-radius: 3px;
  font-size: 11px;
  font-weight: 500;
}

.stat-pill.success { background: #e0e7ff; color: #3730a3; }
.stat-pill.good { background: #d1fae5; color: #065f46; }
.stat-pill.bad { background: #fee2e2; color: #991b1b; }
.stat-pill.skip { background: #f3f4f6; color: #6b7280; }

.error-text {
  color: #991b1b;
  font-size: 12px;
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.log-details-wrap {
  padding: 12px 16px;
  background: #f9fafb;
  border-radius: 6px;
}

.log-details-title {
  font-size: 13px;
  font-weight: 600;
  color: #374151;
  margin-bottom: 8px;
}

.log-details-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
  background: #fff;
  border-radius: 4px;
  overflow: hidden;
}

.log-details-table th {
  padding: 8px 10px;
  text-align: left;
  font-weight: 600;
  color: #374151;
  background: #f3f4f6;
  border-bottom: 1px solid #e5e7eb;
}

.log-details-table td {
  padding: 8px 10px;
  border-bottom: 1px solid #f3f4f6;
}

.detail-order {
  font-family: monospace;
  color: #4b5563;
}

.detail-status {
  display: inline-block;
  padding: 1px 6px;
  border-radius: 3px;
  font-size: 11px;
  font-weight: 500;
}

.detail-status.success { background: #d1fae5; color: #065f46; }
.detail-status.skip { background: #f3f4f6; color: #6b7280; }
.detail-status.failed { background: #fee2e2; color: #991b1b; }
.detail-status.unknown { background: #f3f4f6; color: #6b7280; }

.detail-reason {
  color: #6b7280;
  word-break: break-all;
}

.empty-sub {
  font-size: 12px;
  color: #9ca3af;
  margin-top: 4px;
}

/* ===== 手动触发卡片 ===== */
.manual-trigger-card {
  margin-top: 20px;
  padding: 16px 20px;
  background: #fff;
  border-radius: 10px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.06);
}

.manual-trigger-title {
  font-size: 15px;
  font-weight: 600;
  color: #1f2937;
  margin-bottom: 12px;
}

.manual-trigger-row {
  display: flex;
  gap: 10px;
  align-items: center;
  margin-bottom: 10px;
}

.manual-trigger-tip {
  font-size: 12px;
  color: #9ca3af;
  line-height: 1.6;
}

@media (max-width: 1280px) {
  .stats-grid {
    grid-template-columns: repeat(3, 1fr);
  }
}

@media (max-width: 900px) {
  .stats-grid {
    grid-template-columns: repeat(2, 1fr);
  }
}

@media (max-width: 768px) {
  .rates-page {
    padding: 12px;
  }
  .stats-grid {
    grid-template-columns: repeat(2, 1fr);
  }
  .filter-row {
    flex-direction: column;
    align-items: stretch;
  }
  .filter-select,
  .filter-search {
    width: 100%;
  }
  .rate-level-cards {
    grid-template-columns: 1fr;
  }
}
</style>
