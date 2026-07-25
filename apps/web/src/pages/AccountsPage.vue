<template>
  <div class="grid wide-right" v-bind="$attrs">
    <div>
      <div class="alert-line">
        账号列表、手动添加、删除、资料刷新、WebSocket 状态与扫码登录均由当前部署的服务端处理。
        扫码前请确认这是你信任的部署；若服务不可用，页面会显示明确的不可用状态与重试入口。
      </div>
      <div v-if="error" class="global-notice error">{{ error }}</div>
      <div v-if="accountListWarning" class="global-notice warning" role="status">{{ accountListWarning }}</div>
      <div v-if="qrSuccessMsg" class="global-notice success">{{ qrSuccessMsg }}</div>
      <div v-if="captchaErrorMsg" class="global-notice error">{{ captchaErrorMsg }}</div>
      <div class="grid stat-grid" style="grid-template-columns:repeat(5,1fr)">
        <StatCard title="账号总数" :value="accountMetric(stats.total)" change="全部记录" icon="users" />
        <StatCard title="正常账号" :value="accountMetric(stats.normal)" change="当前页" icon="account" color="green" />
        <StatCard title="需验证" :value="accountMetric(stats.verify)" change="当前页" icon="shield" color="orange" />
        <StatCard title="WS在线" :value="accountMetric(stats.wsOnline)" change="当前页已探测" icon="link" color="purple" />
        <StatCard title="Cookie异常" :value="accountMetric(stats.cookieWarn)" change="当前页已确认" icon="opportunity" color="orange" />
      </div>
      <div class="toolbar">
        <input v-model="keyword" class="input large" placeholder="搜索昵称 / UID / 备注" @keyup.enter="loadAccounts">
        <select v-model="statusFilter" class="input" style="max-width:150px">
          <option value="all">全部状态</option>
          <option value="normal">正常</option>
          <option value="verify">需验证</option>
          <option value="cookieWarn">Cookie异常</option>
          <option value="wsOnline">WS在线</option>
        </select>
        <AppButton :loading="loading" @click="loadAccounts">刷新</AppButton>
      </div>
      <CardPanel>
        <div v-if="accountsRefreshing" class="refresh-status" role="status" aria-live="polite">
          正在刷新账号列表，现有数据仍可查看。
        </div>
        <EmptyState v-if="loading && dataAvailable !== true" icon="⏳" title="账号加载中" description="正在读取账号与认证状态。" />
        <EmptyState v-else-if="dataAvailable === false" icon="⚠️" title="账号列表暂不可用" description="当前无法确认账号记录，不会把失败显示为空列表。">
          <template #actions><AppButton @click="loadAccounts">重新加载</AppButton></template>
        </EmptyState>
        <BaseTable v-else-if="dataAvailable === true" :columns="cols" :rows="rows" :row-class="rowClass">
          <template #account="{ row }"><button type="button" class="product-cell account-selector" :aria-label="`查看账号 ${row.name}`" @click="selectAccount(row.raw)"><img v-if="row.avatar" :src="row.avatar" class="avatar small" alt=""><span v-else class="avatar small avatar-img" aria-hidden="true"></span><span class="account-selector-copy"><strong>{{ row.name }}</strong><em>{{ row.tag }}</em></span></button></template>
          <template #status="{ row }"><Badge :type="row.statusType">{{ row.statusText }}</Badge></template>
          <template #cookie="{ row }"><Badge :type="row.cookieType">{{ row.cookie }}</Badge></template>
          <template #ws="{ row }"><span><i :class="['dot', row.wsConnected === true ? '' : (row.wsConnected === false ? 'red' : 'orange')] "></i>{{ row.ws }}</span></template>
          <template #op="{ row }">
            <button class="link" @click="selectAccount(row.raw)">详情</button>
            <button class="link" @click="refreshProfile(row.raw.id)">刷新资料</button>
            <button class="link" @click="openRescanModal(row.raw)">重新扫码</button>
            <button class="link" :disabled="isAccountSolving(row.raw.id)" @click="solveCaptcha(row.raw)">{{ isAccountSolving(row.raw.id) ? '求解中' : '滑块求解' }}</button>
            <button class="link" :disabled="isWsBusy(row.raw.id) || row.wsConnected == null || row.wsPending" @click="toggleWs(row.raw)">{{ isWsBusy(row.raw.id) ? '确认中...' : (row.wsPending ? '启动中' : (row.wsConnected === true ? '断开' : (row.wsConnected === false ? '连接' : '状态未知'))) }}</button>
            <button class="link danger-text" @click="removeAccount(row.raw.id)">删除</button>
          </template>
        </BaseTable>
        <Pagination v-if="dataAvailable === true" :total="total" :current="current" :page-size="pageSize" @page-change="goPage" />
      </CardPanel>
    </div>
    <div class="right-drawer">
      <aside class="right-drawer account-detail-drawer">
  <div class="detail-title-row">
    <h3>账号详情</h3>
    <button
      class="detail-close"
      type="button"
      aria-label="关闭账号详情"
      @click="closeDetail"
    >
      ×
    </button>
  </div>

  <template v-if="selected">
    <!-- 账号基础信息 -->
    <section class="account-summary">
      <img
        v-if="selected.avatarUrl || selected.avatar"
        :src="selected.avatarUrl || selected.avatar"
        class="detail-avatar"
        alt=""
      >
      <div v-else class="detail-avatar avatar-fallback"></div>

      <div class="account-summary-main">
        <div class="account-name-row">
          <strong>{{ accountTitle(selected) }}</strong>
          <span
            v-if="isCurrentAccount(selected)"
            class="current-account-tag"
          >
            当前账号
          </span>
        </div>

        <div class="account-meta-line">
          UID：{{ selected.externalUid || selected.unb || selected.id || '-' }}
        </div>

        <div class="account-meta-line account-meta-split">
          <span>地区：{{ selected.province && selected.city ? `${selected.province} ${selected.city}` : (selected.ipLocation || selected.province || '-') }}</span>
          <i></i>
          <span>注册时间：{{ accountRegisterDate(selected) }}</span>
        </div>
      </div>

      <span :class="['online-state', { offline: selectedWs.connected === false, unknown: selectedWs.connected == null }]">
        <i></i>
        {{ selectedWs.connected === true ? '在线' : (selectedWs.connected === false ? '离线' : '状态未知') }}
      </span>
    </section>

    <!-- Cookie 状态警告 -->
    <div v-if="selected && accountCookieStatus(selected) === 0" class="cookie-warn-banner">
      <Icon name="help" />
      <span>{{ accountLoginHint(selected) }}</span>
    </div>
    <div v-else-if="selected && accountCookieStatus(selected) === 2" class="cookie-warn-banner cookie-expired">
      <Icon name="help" />
      <span>{{ accountLoginHint(selected) }}</span>
    </div>

    <section class="drawer-section diagnosis-card">
      <h4>连接诊断</h4>
      <div v-for="item in accountDiagnostics" :key="item.title" class="diagnosis-item" :class="item.level">
        <span><i></i>{{ item.title }}</span>
        <b>{{ item.text }}</b>
        <small>{{ item.tip }}</small>
      </div>
      <div class="diagnosis-actions">
        <button type="button" class="link" @click="refreshProfile(selected.id)">刷新资料</button>
        <button type="button" class="link" @click="openCookieEdit(selected)">更新 Cookie</button>
        <button type="button" class="link" @click="openRescanModal(selected)">重新扫码</button>
        <button type="button" class="link" :disabled="selectedWs.connected == null || selectedWsPending || isWsBusy(selected.id)" @click="toggleWs(selected)">{{ selectedWsPending ? '启动中' : (selectedWs.connected === true ? '断开连接' : (selectedWs.connected === false ? '启动连接' : '状态未知')) }}</button>
      </div>
    </section>

    <!-- 闲鱼主页资料（刷新资料后展示） -->
    <section v-if="selected.displayName || selected.followers != null || selected.soldCount != null" class="profile-stats-card">
      <h4>闲鱼主页资料</h4>

      <div v-if="selected.introduction" class="profile-intro">
        <span class="profile-intro-label">简介</span>
        <p>{{ selected.introduction }}</p>
      </div>

      <div class="profile-stats-grid">
        <div v-if="selected.followers != null" class="profile-stat-item">
          <b>{{ fmtNumber(selected.followers) }}</b>
          <span>粉丝</span>
        </div>
        <div v-if="selected.following != null" class="profile-stat-item">
          <b>{{ fmtNumber(selected.following) }}</b>
          <span>关注</span>
        </div>
        <div v-if="selected.soldCount != null" class="profile-stat-item">
          <b>{{ fmtNumber(selected.soldCount) }}</b>
          <span>已售</span>
        </div>
        <div v-if="selected.reviewNum != null" class="profile-stat-item">
          <b>{{ fmtNumber(selected.reviewNum) }}</b>
          <span>评价</span>
        </div>
      </div>

      <div class="profile-extra">
        <div v-if="selected.sellerLevel" class="profile-extra-item">
          <span>卖家等级</span>
          <b>{{ selected.sellerLevel }}</b>
        </div>
        <div v-if="selected.praiseRatio" class="profile-extra-item">
          <span>好评率</span>
          <b>{{ selected.praiseRatio }}</b>
        </div>
        <div v-if="selected.fishShopScore != null" class="profile-extra-item">
          <span>鱼小铺分数</span>
          <b>{{ selected.fishShopScore }}</b>
        </div>
        <div v-if="selected.fishShopUser" class="profile-extra-item">
          <span>鱼小铺</span>
          <b class="green-tag">已开通</b>
        </div>
      </div>
    </section>

    <!-- 快捷操作 -->
    <section class="drawer-section quick-section">
      <h4>快捷操作</h4>

      <div class="quick-actions">
        <button
          type="button"
          @click="openCookieEdit(selected)"
        >
          <span>✎</span>
          编辑Cookie
        </button>

        <button
          type="button"
          @click="refreshProfile(selected.id)"
        >
          <span>↻</span>
          刷新资料
        </button>

        <button
          type="button"
          @click="dispatchAccountAction('sync-products')"
        >
          <span>◇</span>
          同步商品
        </button>

        <button
          type="button"
          @click="emit('navigate', 'auto-reply')"
        >
          <span>↻</span>
          自动回复
        </button>

        <button
          type="button"
          @click="emit('navigate', 'auto-delivery')"
        >
          <span>⇪</span>
          自动发货
        </button>

        <button
          type="button"
          @click="emit('navigate', 'connections')"
        >
          <span>⇄</span>
          连接管理
        </button>

        <button
          type="button"
          @click="emit('navigate', 'messages')"
        >
          <span>✉</span>
          在线消息
        </button>

        <button
          type="button"
          @click="checkSelectedAuth"
        >
          <span>ⓘ</span>
          登录验证
        </button>
        <button
          type="button"
          :disabled="captchaSolving || isAccountSolving(selected.id)"
          @click="solveCaptcha(selected)"
        >
          <span>{{ (captchaSolving || isAccountSolving(selected.id)) ? '⏳' : '🧩' }}</span>
          {{ (captchaSolving || isAccountSolving(selected.id)) ? '求解中' : '滑块求解' }}
        </button>
        <button
          type="button"
          @click="openRescanModal(selected)"
        >
          <span>◫</span>
          重新扫码
        </button>

        <button
          type="button"
          @click="openAutoRateModal(selected)"
        >
          <span>✦</span>
          自动评价
        </button>

        <button
          type="button"
          @click="openStrategyModal(selected)"
        >
          <span>⌛</span>
          消息等待
        </button>

        <button
          type="button"
          @click="openStrategyModal(selected)"
          title="求小红花配置在消息等待弹窗中"
        >
          <span>❀</span>
          求小红花
        </button>

        <button
          type="button"
          @click="openUnifiedConfigModal"
        >
          <span>≡</span>
          批量设置
        </button>
</div>
      <div class="retired-feature-note">
        <Icon name="help" />
        自动评价和消息等待配置已保存到账号级策略，后续运行时执行器将复用这些参数。闲鱼授权请使用扫码或 Cookie。
      </div>
    </section>
  </template>

  <div v-else class="empty-state detail-empty">
    请选择一个账号查看详情
  </div>
</aside>
    </div>
  </div>

  <Teleport to="body">
    <div v-if="modal" class="modal-mask" @click.self="closeModal">
      <section v-if="modal==='scan'" class="xy-modal scan-modal">
        <button class="modal-close" @click="closeModal"><Icon name="close" /></button>
        <h2>{{ qr.mode === 'rescan' ? '重新扫码更新账号' : '扫码添加闲鱼账号' }}</h2>
        <div class="scan-steps">
          <div class="scan-step" :class="{ active: qrReady }"><b>1</b><span>{{ qrReady ? '二维码已生成' : '等待生成二维码' }}</span></div>
          <i></i><div class="scan-step" :class="{active: qr.status==='scanned'}"><b>2</b><span>扫码确认</span></div>
          <i></i><div class="scan-step" :class="{active: qr.status==='confirmed'}"><b>3</b><span>{{ qr.mode === 'rescan' ? '更新账号 Cookie' : '自动添加账号' }}</span></div>
        </div>
        <div class="scan-main">
          <div>
            <div class="qr-box">
              <img v-if="qr.qrUrl" :src="qr.qrUrl" alt="闲鱼登录二维码">
              <div v-else class="qr-unavailable" role="status">
                <Icon :name="qrGenerationFailed ? 'warning' : 'refresh'" />
                <span>{{ qrGenerationFailed ? '二维码生成失败' : '尚未生成可扫码二维码' }}</span>
              </div>
              <span v-if="qr.loading" class="qr-loading"></span>
            </div>
            <p class="qr-tip">{{ qr.message || '正在自动生成二维码，请稍候...' }}</p>
          </div>
          <div class="scan-guide">
            <h4>{{ qr.mode === 'rescan' ? '重新扫码流程' : '添加流程' }}</h4>
            <p>1. {{ qrReady ? '系统已生成真实二维码' : '点击生成并等待真实二维码返回' }}</p>
            <p>2. 使用闲鱼 App 扫码并确认登录</p>
            <p>3. {{ qr.mode === 'rescan' ? '系统会把新的登录 Cookie 回写到当前账号并刷新状态' : '系统自动添加账号并刷新资料' }}</p>
            <div class="session-box">
              <h4>会话信息</h4>
              <div v-if="qr.accountId"><span>目标账号：</span><b>{{ selected?.nickname || selected?.displayName || selected?.externalUid || qr.accountId }}</b></div>
              <div><span>会话 ID：</span><b>{{ qr.sessionId || '-' }}</b></div>
              <div><span>当前状态：</span><b>{{ qr.status || '-' }}</b><button class="inline-link" @click="startQrLogin"><Icon name="refresh" /> 生成/刷新二维码</button></div>
            </div>
          </div>
        </div>
        <div class="notice-box">
          <b><Icon name="help" /> 说明</b>
          <p>{{ qrReady ? '二维码已生成，可使用闲鱼 App 扫码登录。' : '当前没有可扫码二维码；占位区域不是二维码，请生成成功后再扫码。' }}</p>
          <p>{{ qr.mode === 'rescan' ? '登录成功后会更新当前账号 Cookie，并重新同步该账号状态。' : '登录成功后将自动添加账号并刷新资料。' }}</p>
          <p>闲鱼 App 显示的登录地点由部署服务器的网络出口决定。若地点与预期不符，请取消扫码并联系当前部署的管理员核验服务器区域。</p>
        </div>
        <div class="modal-actions"><AppButton @click="closeModal">取消</AppButton><AppButton type="primary" :loading="qr.loading" :disabled="qr.loading" @click="startQrLogin">{{ qrReady ? '刷新二维码' : '生成二维码' }}</AppButton></div>
      </section>

      <section v-if="modal==='manual'" class="xy-modal manual-modal">
        <button class="modal-close" @click="closeModal"><Icon name="close" /></button>
        <h2>手动添加账号</h2>
        <label class="field-label">账号备注 <span>可选</span></label>
        <div class="modal-input-wrap"><input v-model="manual.accountNote" placeholder="请输入备注名称（可选，不填写显示闲鱼昵称）"><em>{{ manual.accountNote.length }}/50</em></div>
        <label class="field-label required">Cookie <span>必填</span></label>
        <textarea v-model="manual.cookie" class="cookie-area" placeholder="请输入闲鱼账号 Cookie 字符串"></textarea>

        <!-- Cookie 解析预览 -->
        <div v-if="manualCookieParsed" class="cookie-parse-preview">
          <div v-if="!manualCookieParsed.validation.valid" class="cookie-parse-error">
            <Icon name="help" /> {{ manualCookieParsed.validation.error }}
          </div>
          <div v-if="manualCookieParsed.validation.valid" class="cookie-parse-fields">
            <div class="cookie-parse-header">
              <span>解析结果</span>
              <em>共 {{ manualCookieParsed.keyFields.parsedCount }} 个字段</em>
            </div>
            <div class="cookie-field-grid">
              <div class="cookie-field-item" :class="{ missing: !manualCookieParsed.keyFields.unb }">
                <label>unb（身份标识）<span class="required-mark">*必填</span></label>
                <code>{{ manualCookieParsed.masked.unb }}</code>
              </div>
              <div class="cookie-field-item" :class="{ missing: !manualCookieParsed.keyFields.mH5Tk }">
                <label>_m_h5_tk（签名Token）</label>
                <code>{{ manualCookieParsed.masked.mH5Tk }}</code>
              </div>
              <div v-if="manualCookieParsed.keyFields.userId" class="cookie-field-item">
                <label>user_id</label>
                <code>{{ manualCookieParsed.masked.userId }}</code>
              </div>
            </div>
            <div v-if="manualCookieParsed.validation.warning" class="cookie-parse-warning">
              <Icon name="help" /> {{ manualCookieParsed.validation.warning }}
            </div>
          </div>
        </div>

        <div v-if="manualError" class="input-error">{{ manualError }}</div>
        <div class="modal-hint"><Icon name="help" /> 提交后调用 /api/xianyu/accounts/manual-cookie，后端会解析并保存账号信息</div>
        <div class="usage-box">
          <h4><Icon name="map" /> 使用说明</h4>
          <div><span><Icon name="shield" /></span>Cookie 为空时会进行前端校验</div>
          <div><span><Icon name="shield" /></span>添加成功后自动刷新账号列表</div>
          <div><span><Icon name="shield" /></span>请勿把 Cookie 暴露给不可信页面或日志</div>
        </div>
        <div class="manual-actions"><AppButton @click="closeModal">取消</AppButton><AppButton type="primary" :disabled="manualCookieParsed && !manualCookieParsed.validation.valid" @click="submitManual">{{ submitting ? '添加中...' : '添加' }}</AppButton></div>
      </section>

      <section v-if="modal==='cookieEdit'" class="xy-modal manual-modal">
        <button class="modal-close" @click="closeModal"><Icon name="close" /></button>
        <h2>编辑账号 Cookie</h2>
        <div class="edit-account-info">
          <span>账号：</span><b>{{ selected?.nickname || selected?.displayName || selected?.externalUid || selected?.unb || selected?.id }}</b>
          <span v-if="selected?.unb" class="current-unb-tag">UNB: {{ maskValue(selected.unb) }}</span>
        </div>
        <label class="field-label required">Cookie <span>必填</span></label>
        <textarea v-model="cookieEdit.cookie" class="cookie-area" placeholder="请输入闲鱼账号 Cookie 字符串（从浏览器 F12 开发者工具中复制）"></textarea>

        <!-- Cookie 解析预览 -->
        <div v-if="cookieEditParsed" class="cookie-parse-preview">
          <!-- 格式校验错误 -->
          <div v-if="!cookieEditParsed.validation.valid" class="cookie-parse-error">
            <Icon name="help" /> {{ cookieEditParsed.validation.error }}
          </div>

          <!-- 身份校验警告（防串号） -->
          <div v-if="cookieEditParsed.validation.valid && !cookieEditParsed.identity.valid" class="cookie-parse-error cookie-identity-error">
            <Icon name="shield" /> {{ cookieEditParsed.identity.error }}
          </div>

          <!-- 解析结果展示 -->
          <div v-if="cookieEditParsed.validation.valid" class="cookie-parse-fields">
            <div class="cookie-parse-header">
              <span>解析结果</span>
              <em>共 {{ cookieEditParsed.keyFields.parsedCount }} 个字段</em>
            </div>
            <div class="cookie-field-grid">
              <div class="cookie-field-item" :class="{ missing: !cookieEditParsed.keyFields.unb }">
                <label>unb（身份标识）<span class="required-mark">*必填</span></label>
                <code>{{ cookieEditParsed.masked.unb }}</code>
              </div>
              <div class="cookie-field-item" :class="{ missing: !cookieEditParsed.keyFields.mH5Tk }">
                <label>_m_h5_tk（签名Token）</label>
                <code>{{ cookieEditParsed.masked.mH5Tk }}</code>
              </div>
              <div v-if="cookieEditParsed.keyFields.userId" class="cookie-field-item">
                <label>user_id</label>
                <code>{{ cookieEditParsed.masked.userId }}</code>
              </div>
              <div v-if="cookieEditParsed.keyFields.loginToken" class="cookie-field-item">
                <label>_cookie_login_token_</label>
                <code>{{ cookieEditParsed.masked.loginToken }}</code>
              </div>
            </div>
            <!-- 警告信息 -->
            <div v-if="cookieEditParsed.validation.warning" class="cookie-parse-warning">
              <Icon name="help" /> {{ cookieEditParsed.validation.warning }}
            </div>
          </div>
        </div>

        <div v-if="cookieEditError" class="input-error">{{ cookieEditError }}</div>
        <div class="modal-hint"><Icon name="help" /> 提交后自动提取 unb、_m_h5_tk 等关键字段并重置 Cookie 状态为正常。保存后建议重新连接 WebSocket。</div>
        <div class="usage-box">
          <h4><Icon name="map" /> 使用说明</h4>
          <div><span><Icon name="shield" /></span>遇到"被挤爆"滑块验证时需要更换 Cookie</div>
          <div><span><Icon name="shield" /></span>Cookie 从浏览器 F12 → Application → Cookies 中复制</div>
          <div><span><Icon name="shield" /></span>请勿把 Cookie 暴露给不可信页面或日志</div>
        </div>
        <div class="manual-actions"><AppButton @click="closeModal">取消</AppButton><AppButton type="primary" :disabled="cookieEditSubmitting || (cookieEditParsed && !cookieEditParsed.validation.valid)" @click="submitCookieEdit">{{ cookieEditSubmitting ? '保存中...' : '保存' }}</AppButton></div>
      </section>

      <section v-if="modal==='autoRate'" class="xy-modal manual-modal auto-rate-modal">
        <button class="modal-close" @click="closeModal"><Icon name="close" /></button>
        <h2>自动评价</h2>
        <div class="edit-account-info">
          <span>账号：</span><b>{{ selected?.nickname || selected?.displayName || selected?.externalUid || selected?.id || '-' }}</b>
        </div>
        <label class="auto-rate-toggle">
          <input v-model="autoRateForm.enabled" type="checkbox" :disabled="!autoRateLoaded">
          <span>启用该账号的自动评价</span>
        </label>
        <label class="field-label">评价方式</label>
        <select v-model="autoRateForm.rateType" class="input large" :disabled="!autoRateLoaded">
          <option value="text">固定文本</option>
          <option value="api">外部 API</option>
        </select>
        <template v-if="autoRateForm.rateType === 'text'">
          <label class="field-label required">评价内容 <span>必填</span></label>
          <textarea
            v-model="autoRateForm.textContent"
            class="cookie-area"
            :disabled="!autoRateLoaded"
            placeholder="请输入默认评价内容，例如：交易顺利，感谢支持。"
          ></textarea>
        </template>
        <template v-else>
          <label class="field-label required">API 地址 <span>必填</span></label>
          <input
            v-model="autoRateForm.apiUrl"
            class="input large"
            :disabled="!autoRateLoaded"
            placeholder="https://example.com/xianyu/auto-rate"
          >
          <label class="field-label">兜底评价内容 <span>可选</span></label>
          <textarea
            v-model="autoRateForm.textContent"
            class="cookie-area"
            :disabled="!autoRateLoaded"
            placeholder="当外部接口不可用时，可回退到这段文本。"
          ></textarea>
        </template>
        <div v-if="autoRateError" class="input-error">{{ autoRateError }}</div>
        <div class="modal-hint"><Icon name="help" /> 当前先保存账号级自动评价配置，后续自动执行链路会直接复用这里的参数。</div>
        <div class="manual-actions">
          <AppButton @click="closeModal">取消</AppButton>
          <AppButton type="primary" :disabled="!autoRateLoaded || autoRateSaving" @click="saveAutoRateConfig">{{ autoRateSaving ? '保存中...' : '保存' }}</AppButton>
        </div>
      </section>

      <section v-if="modal==='strategy'" class="xy-modal manual-modal auto-rate-modal">
        <button class="modal-close" @click="closeModal"><Icon name="close" /></button>
        <h2>消息等待 / 求小红花</h2>
        <div class="edit-account-info">
          <span>账号：</span><b>{{ selected?.nickname || selected?.displayName || selected?.externalUid || selected?.id || '-' }}</b>
        </div>
        <label class="field-label required">相同消息等待时间（秒） <span>0-86400</span></label>
        <input
          v-model="strategyForm.messageExpireTime"
          class="input large"
          type="number"
          min="0"
          max="86400"
          placeholder="3600"
          :disabled="!strategyLoaded"
        >
        <label class="auto-rate-toggle">
          <input v-model="strategyForm.scheduledRedelivery" type="checkbox" :disabled="!strategyLoaded">
          <span>开启该账号的定时补发货</span>
        </label>
        <label class="auto-rate-toggle">
          <input v-model="strategyForm.requestRedFlower" type="checkbox" :disabled="!strategyLoaded">
          <span>开启该账号的求小红花（订单完成后主动请求买家评价）</span>
        </label>
        <div v-if="strategyError" class="input-error">{{ strategyError }}</div>
        <div class="modal-hint"><Icon name="help" /> 该配置会作为账号级策略保存，后续消息等待、定时补发货与求小红花会直接复用这里的参数。</div>
        <div class="manual-actions">
          <AppButton @click="closeModal">取消</AppButton>
          <AppButton type="primary" :disabled="!strategyLoaded || strategySaving" @click="saveStrategyConfig">{{ strategySaving ? '保存中...' : '保存' }}</AppButton>
        </div>
      </section>

      <section v-if="modal==='unifiedConfig'" class="xy-modal manual-modal auto-rate-modal">
        <button class="modal-close" @click="closeModal"><Icon name="close" /></button>
        <h2>批量设置 / 统一应用</h2>
        <div class="edit-account-info">
          <span>基准账号：</span><b>{{ selected?.nickname || selected?.displayName || selected?.externalUid || selected?.id || '-' }}</b>
        </div>
        <p class="modal-subtitle">以当前选中的账号为基准，将自动评价、消息等待等配置快速应用到当前列表中的账号。</p>
        <div class="modal-hint"><Icon name="help" /> 当前将对 {{ accounts.length }} 个账号执行批量操作；如只想作用于部分账号，请先通过搜索缩小列表范围。</div>
        <div v-if="unifiedConfigError" class="input-error">{{ unifiedConfigError }}</div>
        <div v-if="unifiedConfigSuccess" class="global-notice success" style="margin-top:12px">{{ unifiedConfigSuccess }}</div>
        <div class="batch-auth-grid">
          <button type="button" class="batch-auth-card" :disabled="unifiedConfigBusy" @click="applyCurrentAutoRateToVisibleAccounts">
            <strong>同步自动评价</strong>
            <span>将当前账号的自动评价配置应用到当前列表中的账号。</span>
          </button>
          <button type="button" class="batch-auth-card" :disabled="unifiedConfigBusy" @click="applyCurrentStrategyToVisibleAccounts">
            <strong>同步消息等待</strong>
            <span>将消息等待、求小红花等策略配置同步到当前列表中的账号。</span>
          </button>
          <button type="button" class="batch-auth-card" :disabled="unifiedConfigBusy" @click="runBatchAuthCheckForVisibleAccounts">
            <strong>统一登录校验</strong>
            <span>批量检查当前列表账号的登录状态、Cookie 和会话有效性。</span>
          </button>
        </div>
        <div v-if="unifiedConfigBusy" class="modal-hint" style="margin-top:14px"><Icon name="help" /> 正在执行：{{ unifiedConfigTaskText }}</div>
        <div class="manual-actions">
          <AppButton @click="closeModal">关闭</AppButton>
        </div>
      </section>

      <section v-if="modal==='confirmDelete'" class="xy-modal confirm-delete-modal">
        <button class="modal-close" @click="closeModal"><Icon name="close" /></button>
        <div class="confirm-delete-icon">
          <Icon name="warning" />
        </div>
        <h2>确认删除该闲鱼账号？</h2>
        <p class="confirm-delete-desc">删除后将移除该账号的本地连接、资料和后续自动化配置关联，请确认已不再运营该账号。</p>
        <div class="confirm-delete-actions">
          <AppButton @click="closeModal">取消</AppButton>
          <AppButton type="danger" @click="executeDelete">确认删除</AppButton>
        </div>
      </section>
    </div>
  </Teleport>
</template>
<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import StatCard from '../components/StatCard.vue'; import CardPanel from '../components/CardPanel.vue'; import BaseTable from '../components/BaseTable.vue'; import Badge from '../components/Badge.vue'; import AppButton from '../components/AppButton.vue'; import Icon from '../components/Icon.vue'; import Pagination from '../components/Pagination.vue'; import EmptyState from '../components/EmptyState.vue'
import { checkAccountAuth, deleteAccount, getAccounts, createAccountByCookie, refreshAccountProfile, updateAccountCookie, getAccountAutoRateConfig, saveAccountAutoRateConfig, getAccountStrategyConfig, saveAccountStrategyConfig } from '../api/accounts.js'
import { startWebSocket, stopWebSocket, websocketStatus } from '../api/websocket.js'
import { useDebouncedRef } from '../composables/useDebouncedRef.js'
import { useCaptchaSolver } from '../composables/useCaptchaSolver.js'
const emit = defineEmits(['navigate'])
import { generateQrLogin, getQrLoginStatus, cleanupQrLogin } from '../api/qrlogin.js'
import { accountName } from '../utils/format.js'
import { accountAuthState, accountCookieBadgeType, accountCookieLabel, accountCookieStatus, accountLoginHint } from '../utils/accountAuth.js'
import { extractKeyFields, maskKeyFields, validateCookie, checkIdentity, maskValue } from '../utils/cookie.js'
import { confirmAction } from '../utils/confirmAction.js'
import { createLatestRequestGuard, listRefreshRequestConfig } from '../utils/latestRequest.js'
import { friendlyError } from '../utils/friendlyError.js'

const { solveManually, isAccountSolving, getAccountSolveStatus } = useCaptchaSolver()
const modal = ref('')
const manual = reactive({ accountNote:'', cookie:'' })
const manualError = ref('')
const submitting = ref(false)
const loading = ref(false)
const dataAvailable = ref(null)
const error = ref('')
const accountListWarning = ref('')
const keyword = ref('')
const debouncedKeyword = useDebouncedRef(keyword, 300)
const statusFilter = ref('all')
const accounts = ref([])
const current = ref(1)
const pageSize = ref(20)
const total = ref(0)
const selected = ref(null)
const selectedId = ref(null)
const wsMap = reactive({})
const wsBusyMap = reactive({})
const selectedWs = computed(() => wsMap[selected.value?.id] || {})
const accountsRefreshing = computed(() => loading.value && dataAvailable.value === true)
const accountsRequestGuard = createLatestRequestGuard()
const WS_START_PHASES = new Set(['starting', 'refresh_token', 'connecting', 'registering', 'syncing', 'accepted', 'pending', 'recovering'])
const WS_FAILURE_PHASES = new Set(['failed', 'error', 'stopped', 'cookie_expired'])
const WS_STATUS_POLL_INTERVAL = 300
const WS_STATUS_POLL_LIMIT = 10
const selectedWsPending = computed(() => isWsPending(selected.value?.id))
let qrTimer = null
const qr = reactive({ loading:false, sessionId:'', qrUrl:'', status:'', message:'', mode:'create', accountId:null })
const qrReady = computed(() => Boolean(qr.sessionId && qr.qrUrl))
const qrGenerationFailed = computed(() => qr.status === 'error')
const qrSuccessMsg = ref('')
const captchaErrorMsg = ref('')
const cookieEdit = reactive({ accountId: null, cookie: '' })
const cookieEditError = ref('')
const cookieEditSubmitting = ref(false)
const batchAuthBusy = ref(false)
const batchAuthError = ref('')
const batchAuthSuccess = ref('')

// 自动评价配置
const autoRateSaving = ref(false)
const autoRateError = ref('')
const autoRateLoaded = ref(false)
const autoRateForm = reactive({ enabled: false, rateType: 'text', textContent: '', apiUrl: '' })

// 消息等待策略配置
const strategySaving = ref(false)
const strategyError = ref('')
const strategyLoaded = ref(false)
const strategyForm = reactive({ messageExpireTime: 3600, scheduledRedelivery: false, requestRedFlower: false })

// 批量设置统一配置
const unifiedConfigBusy = ref(false)
const unifiedConfigError = ref('')
const unifiedConfigSuccess = ref('')
const unifiedConfigTaskText = ref('')
const pendingDeleteId = ref(null)     // 待删除的账号ID

const captchaSolving = ref(false)
const captchaMessage = ref('')

// Cookie 编辑弹窗 - 实时解析预览
const cookieEditParsed = computed(() => {
  if (!cookieEdit.cookie.trim()) return null
  const validation = validateCookie(cookieEdit.cookie)
  const keyFields = extractKeyFields(cookieEdit.cookie)
  const masked = maskKeyFields(keyFields)
  // 身份校验：与当前选中账号的 unb 对比
  const identity = selected.value
    ? checkIdentity(keyFields.unb, selected.value)
    : { valid: true, error: null, unbChanged: false }
  return { validation, keyFields, masked, identity }
})

// 手动添加账号弹窗 - 实时解析预览
const manualCookieParsed = computed(() => {
  if (!manual.cookie.trim()) return null
  const validation = validateCookie(manual.cookie)
  const keyFields = extractKeyFields(manual.cookie)
  const masked = maskKeyFields(keyFields)
  return { validation, keyFields, masked }
})

function accountTitle(account){ return accountName(account) }
function accountStatus(status){ return ({ 1:'正常', '-1':'需手机验证', '-2':'需人机验证' }[status] || '未知') }

function accountRegisterDate(account) {
  const value =
    account?.registerTime ||
    account?.createdTime ||
    account?.createTime

  if (!value) return '-'

  return String(value)
    .replace('T', ' ')
    .slice(0, 10)
}

function fmtNumber(num) {
  if (num == null) return '-'
  if (num >= 10000) {
    return (num / 10000).toFixed(1).replace(/\.0$/, '') + '万'
  }
  return String(num)
}

function isCurrentAccount(account) {
  return (
    account?.isCurrentAccount === true ||
    account?.current === true ||
    account?.id === accounts.value[0]?.id
  )
}

function closeDetail() {
  selected.value = null
}

function dispatchAccountAction(action) {
  if (!selected.value) return
  switch (action) {
    case 'sync-products':
      emit('navigate', 'products')
      break
    case 'activity-list':
      emit('navigate', 'logs')
      break
    default:
      if (import.meta.env.DEV) {
        console.warn('[AccountsPage] 未知的账号操作:', action)
      }
  }
}



const accountDiagnostics = computed(() => {
  const a = selected.value || {}
  const ws = selectedWs.value || {}
  const authState = accountAuthState(a)
  const verify = a.status === -1 || a.status === -2
  const accountStateKnown = a.status !== undefined && a.status !== null
  return [
    {
      title: 'Cookie 状态',
      level: authState === true ? 'ok' : (authState === false ? 'danger' : 'warn'),
      text: accountCookieLabel(a),
      tip: authState === true ? '认证探测已确认可用。' : accountLoginHint(a)
    },
    {
      title: '账号验证',
      level: !accountStateKnown || verify ? 'warn' : 'ok',
      text: !accountStateKnown ? '状态未知' : (verify ? accountStatus(a.status) : '无需处理'),
      tip: !accountStateKnown ? '账号状态尚未加载，请刷新后再执行敏感操作。' : (verify ? '请先在闲鱼完成手机/人机验证，再回到系统刷新。' : '账号记录未标记为待验证。')
    },
    {
      title: '消息连接',
      level: ws.connected === true ? 'ok' : 'warn',
      text: ws.connected === true ? 'WebSocket 在线' : (ws.connected === false ? (isWsPending(a.id) ? '启动中' : '未连接') : '状态未知'),
      tip: ws.connected === true ? '状态探测已确认连接在线。' : (ws.connected === false ? '自动回复前请启动并等待连接确认。' : '连接探测失败，当前不会按离线处理。')
    }
  ]
})

function resetQrState() {
  qr.loading = false
  qr.sessionId = ''
  qr.qrUrl = ''
  qr.status = ''
  qr.message = ''
  qr.mode = 'create'
  qr.accountId = null
}

function openModal(type){
  modal.value = type
  if(type === 'manual') {
    manual.accountNote=''
    manual.cookie=''
    manualError.value=''
  }
  if(type === 'scan') {
    qr.mode = 'create'
    qr.accountId = null
    startQrLogin()
  }
}
function closeModal(){
  modal.value = ''
  batchAuthError.value = ''
  batchAuthSuccess.value = ''
  unifiedConfigError.value = ''
  unifiedConfigSuccess.value = ''
  unifiedConfigTaskText.value = ''
  unifiedConfigBusy.value = false
  autoRateError.value = ''
  strategyError.value = ''
  stopQrPolling()
  resetQrState()
}
function openRescanModal(account) {
  if (!account?.id) return
  modal.value = 'scan'
  qr.mode = 'rescan'
  qr.accountId = account.id
  startQrLogin()
}

function requireResponseObject(res, label) {
  const data = res?.data
  if (!data || typeof data !== 'object' || Array.isArray(data)) {
    throw new Error(`${label}响应格式异常`)
  }
  return data
}

function autoRateConfigOf(res) {
  const data = requireResponseObject(res, '自动评价配置')
  if (typeof data.enabled !== 'boolean' || !['text', 'api'].includes(data.rateType)) {
    throw new Error('自动评价配置缺少有效开关或评价方式')
  }
  if (typeof data.textContent !== 'string' || typeof data.apiUrl !== 'string') {
    throw new Error('自动评价配置内容响应格式异常')
  }
  return data
}

function strategyConfigOf(res) {
  const data = requireResponseObject(res, '账号策略配置')
  const messageExpireTime = Number(data.messageExpireTime)
  if (!Number.isFinite(messageExpireTime) || messageExpireTime < 0 || messageExpireTime > 86400) {
    throw new Error('账号策略等待时间响应格式异常')
  }
  if (typeof data.scheduledRedelivery !== 'boolean') {
    throw new Error('账号策略开关响应格式异常')
  }
  return { ...data, messageExpireTime }
}

async function openAutoRateModal(account = selected.value) {
  if (!account?.id) return
  modal.value = 'autoRate'
  autoRateError.value = ''
  autoRateSaving.value = false
  autoRateLoaded.value = false
  autoRateForm.enabled = false
  autoRateForm.rateType = 'text'
  autoRateForm.textContent = ''
  autoRateForm.apiUrl = ''
  try {
    const res = await getAccountAutoRateConfig(account.id)
    const data = autoRateConfigOf(res)
    autoRateForm.enabled = data.enabled
    autoRateForm.rateType = data.rateType
    autoRateForm.textContent = data.textContent
    autoRateForm.apiUrl = data.apiUrl
    autoRateLoaded.value = true
  } catch (e) {
    autoRateError.value = e.message || '加载自动评价配置失败'
  }
}

async function saveAutoRateConfig() {
  if (!selected.value?.id || !autoRateLoaded.value || autoRateSaving.value) return
  autoRateError.value = ''
  if (autoRateForm.rateType === 'text' && !autoRateForm.textContent.trim()) {
    autoRateError.value = '请输入评价内容'
    return
  }
  if (autoRateForm.rateType === 'api' && !autoRateForm.apiUrl.trim()) {
    autoRateError.value = '请输入 API 地址'
    return
  }
  autoRateSaving.value = true
  try {
    const res = await saveAccountAutoRateConfig(selected.value.id, {
      enabled: autoRateForm.enabled,
      rateType: autoRateForm.rateType,
      textContent: autoRateForm.textContent.trim(),
      apiUrl: autoRateForm.apiUrl.trim(),
    })
    autoRateConfigOf(res)
    closeModal()
    qrSuccessMsg.value = '自动评价配置已保存'
    setTimeout(() => { if (qrSuccessMsg.value === '自动评价配置已保存') qrSuccessMsg.value = '' }, 4000)
    await loadAccounts()
  } catch (e) {
    autoRateError.value = e.message || '保存自动评价配置失败'
  } finally {
    autoRateSaving.value = false
  }
}

async function openStrategyModal(account = selected.value) {
  if (!account?.id) return
  modal.value = 'strategy'
  strategyError.value = ''
  strategySaving.value = false
  strategyLoaded.value = false
  strategyForm.messageExpireTime = 3600
  strategyForm.scheduledRedelivery = false
  strategyForm.requestRedFlower = false
  try {
    const res = await getAccountStrategyConfig(account.id)
    const data = strategyConfigOf(res)
    strategyForm.messageExpireTime = data.messageExpireTime
    strategyForm.scheduledRedelivery = data.scheduledRedelivery
    strategyForm.requestRedFlower = data.requestRedFlower === true
    strategyLoaded.value = true
  } catch (e) {
    strategyError.value = e.message || '加载账号策略配置失败'
  }
}

async function solveCaptcha(account = selected.value) {
  if (!account?.id) return
  if (captchaSolving.value) return
  captchaSolving.value = true
  captchaMessage.value = ''
  captchaErrorMsg.value = ''
  try {
    const result = await solveManually(account.id, 'manual_retry', {
      openReason: '账号页面手动触发滑块求解',
      solveReason: '用户在账号详情页点击滑块求解按钮',
      // 前台手动求解：弹出 Chrome 窗口便于人工观察滑块过程
      // 注意：crawler 容器 CRAWLER_FORCE_HEADLESS=true 会强制覆盖为无头
      headless: false,
    })
    // 优先用 errorCode 映射友好提示（约束7），兜底用后端 error 文案
    const displayMessage = friendlyError(result.errorCode || result.message, result.message)
    captchaMessage.value = displayMessage
    if (result.success) {
      qrSuccessMsg.value = displayMessage
      setTimeout(() => { if (qrSuccessMsg.value === displayMessage) qrSuccessMsg.value = '' }, 6000)
      await loadAccounts()
    } else {
      captchaErrorMsg.value = displayMessage
      setTimeout(() => { if (captchaErrorMsg.value === displayMessage) captchaErrorMsg.value = '' }, 10000)
    }
  } catch (e) {
    const friendlyMsg = friendlyError(e, '滑块求解请求失败，请稍后重试')
    captchaMessage.value = friendlyMsg
    captchaErrorMsg.value = friendlyMsg
    setTimeout(() => { if (captchaErrorMsg.value === friendlyMsg) captchaErrorMsg.value = '' }, 10000)
  } finally {
    captchaSolving.value = false
  }
}

async function saveStrategyConfig() {
  if (!selected.value?.id || !strategyLoaded.value || strategySaving.value) return
  strategyError.value = ''
  const messageExpireTime = Number(strategyForm.messageExpireTime)
  if (!Number.isFinite(messageExpireTime) || messageExpireTime < 0 || messageExpireTime > 86400) {
    strategyError.value = '消息等待时间需在 0 到 86400 秒之间'
    return
  }
  strategySaving.value = true
  try {
    const res = await saveAccountStrategyConfig(selected.value.id, {
      messageExpireTime: Math.round(messageExpireTime),
      scheduledRedelivery: strategyForm.scheduledRedelivery,
      requestRedFlower: strategyForm.requestRedFlower,
    })
    strategyConfigOf(res)
    closeModal()
    qrSuccessMsg.value = '账号策略已保存'
    setTimeout(() => { if (qrSuccessMsg.value === '账号策略已保存') qrSuccessMsg.value = '' }, 4000)
    await loadAccounts()
  } catch (e) {
    strategyError.value = e.message || '保存账号策略失败'
  } finally {
    strategySaving.value = false
  }
}

function openUnifiedConfigModal() {
  if (!selected.value?.id) return
  unifiedConfigError.value = ''
  unifiedConfigSuccess.value = ''
  unifiedConfigTaskText.value = ''
  unifiedConfigBusy.value = false
  modal.value = 'unifiedConfig'
}

function visibleAccountsForUnifiedConfig() {
  return accounts.value.filter(account => Number(account?.id) > 0)
}

async function applyUnifiedAction(taskText, runner) {
  if (unifiedConfigBusy.value) return
  const visibleAccounts = visibleAccountsForUnifiedConfig()
  if (!selected.value?.id) {
    unifiedConfigError.value = '请先选择一个基准账号'
    return
  }
  if (visibleAccounts.length === 0) {
    unifiedConfigError.value = '当前页面没有可处理的账号'
    return
  }
  unifiedConfigBusy.value = true
  unifiedConfigError.value = ''
  unifiedConfigSuccess.value = ''
  unifiedConfigTaskText.value = taskText
  try {
    const summary = await runner(visibleAccounts)
    const failed = Number(summary?.failed || 0)
    const success = Number(summary?.success || 0)
    unifiedConfigSuccess.value = `${taskText}完成，成功 ${success} 个账号${failed ? `，失败 ${failed} 个` : ''}`
    if (summary?.message) {
      unifiedConfigError.value = summary.message
    }
    await loadAccounts()
  } catch (e) {
    unifiedConfigError.value = e.message || `${taskText}失败`
  } finally {
    unifiedConfigBusy.value = false
    unifiedConfigTaskText.value = ''
  }
}

async function applyCurrentAutoRateToVisibleAccounts() {
  await applyUnifiedAction('同步自动评价', async (visibleAccounts) => {
    const sourceRes = await getAccountAutoRateConfig(selected.value.id)
    const source = autoRateConfigOf(sourceRes)
    const payload = {
      enabled: source.enabled,
      rateType: source.rateType,
      textContent: source.textContent,
      apiUrl: source.apiUrl,
    }
    let success = 0
    let failed = 0
    const errors = []
    for (const account of visibleAccounts) {
      try {
        await saveAccountAutoRateConfig(account.id, payload)
        success += 1
      } catch (e) {
        failed += 1
        errors.push(`${accountTitle(account)}: ${e.message || '保存失败'}`)
      }
    }
    return {
      success,
      failed,
      message: errors.slice(0, 3).join('; '),
    }
  })
}

async function applyCurrentStrategyToVisibleAccounts() {
  await applyUnifiedAction('同步消息等待', async (visibleAccounts) => {
    const sourceRes = await getAccountStrategyConfig(selected.value.id)
    const source = strategyConfigOf(sourceRes)
    const payload = {
      messageExpireTime: source.messageExpireTime,
      scheduledRedelivery: source.scheduledRedelivery,
      requestRedFlower: source.requestRedFlower === true,
    }
    let success = 0
    let failed = 0
    const errors = []
    for (const account of visibleAccounts) {
      try {
        await saveAccountStrategyConfig(account.id, payload)
        success += 1
      } catch (e) {
        failed += 1
        errors.push(`${accountTitle(account)}: ${e.message || '保存失败'}`)
      }
    }
    return {
      success,
      failed,
      message: errors.slice(0, 3).join('; '),
    }
  })
}

async function runBatchAuthCheckForVisibleAccounts() {
  await applyUnifiedAction('统一登录校验', async (visibleAccounts) => {
    let success = 0
    let failed = 0
    const errors = []
    for (const account of visibleAccounts) {
      try {
        await checkAccountAuth(account.id)
        success += 1
      } catch (e) {
        failed += 1
        errors.push(`${accountTitle(account)}: ${e.message || '校验失败'}`)
      }
    }
    return {
      success,
      failed,
      message: errors.slice(0, 3).join('; '),
    }
  })
}

function handleHeaderAction(e){ if(e.detail === 'open-scan-account') openModal('scan'); if(e.detail === 'open-manual-account') openModal('manual'); if(e.detail === 'refresh-accounts') loadAccounts() }

const cols=[{key:'account',title:'账号信息'},{key:'uid',title:'UID'},{key:'area',title:'地区'},{key:'level',title:'等级'},{key:'status',title:'账号状态'},{key:'cookie',title:'Cookie状态'},{key:'ws',title:'WS状态'},{key:'sync',title:'资料同步'},{key:'op',title:'操作'}]

const rowClass = (row) => row.raw?.id === selectedId.value ? 'row-selected' : ''

const rows = computed(() => {
  const kw = (debouncedKeyword.value || '').trim().toLowerCase()
  const searchFields = ['nickname', 'displayName', 'accountNote', 'externalUid', 'unb', 'province', 'city', 'ipLocation', 'remark']
  return accounts.value
  .filter(a => {
    // 状态筛选
    if (statusFilter.value === 'normal' && a.status !== 1) return false
    if (statusFilter.value === 'verify' && a.status !== -1 && a.status !== -2) return false
    if (statusFilter.value === 'cookieWarn' && accountAuthState(a) !== false) return false
    if (statusFilter.value === 'wsOnline' && wsMap[a.id]?.connected !== true) return false
    // 关键词筛选
    if (!kw) return true
    // 仅匹配用户可见字段，避免匹配 id/时间戳等无关字段导致搜索结果不准
    return searchFields.some(f => String(a[f] || '').toLowerCase().includes(kw))
  })
  .map(a => {
    const ws = wsMap[a.id] || {}
    const wsConnected = typeof ws.connected === 'boolean' && ws.statusUnavailable !== true ? ws.connected : null
    return {
      raw: a,
      name: accountTitle(a),
      avatar: a.avatarUrl || a.avatar,
      tag: a.accountNote && (a.nickname || a.displayName) ? (a.nickname || a.displayName) : '',
      uid: a.externalUid || a.unb || a.id,
      area: a.province && a.city ? `${a.province} ${a.city}` : (a.ipLocation || a.province || '-'),
      level: a.accountLevel || a.sellerLevel || a.fishShopLevel || '-',
      statusText: accountStatus(a.status),
      statusType: a.status === 1 ? 'green' : 'orange',
      cookie: accountCookieLabel(a),
      cookieType: accountCookieBadgeType(a),
      ws: wsConnected === true ? '在线' : (wsConnected === false ? (isWsPending(a.id) ? '启动中' : '离线') : '状态未知'),
      wsConnected,
      wsPending: isWsPending(a.id),
      sync: a.lastSyncTime || a.profileUpdatedTime || a.updatedTime || '-'
    }
  })
})

const stats = computed(() => ({
  total: total.value,
  normal: accounts.value.filter(a => a.status === 1).length,
  verify: accounts.value.filter(a => a.status === -1 || a.status === -2).length,
  wsOnline: Object.values(wsMap).filter(s => s?.connected === true).length,
  cookieWarn: accounts.value.filter(a => accountAuthState(a) === false).length
}))

function accountMetric(value) {
  return dataAvailable.value === true ? value : '—'
}

async function loadAccounts() {
  const request = accountsRequestGuard.begin()
  const hadSnapshot = dataAvailable.value === true
  loading.value = true
  error.value = ''
  accountListWarning.value = ''
  try {
    const requestConfig = listRefreshRequestConfig(hadSnapshot)
    const res = await getAccounts({ current: current.value, size: pageSize.value }, requestConfig)
    if (!request.isCurrent()) return
    // Support both array and object response formats
    const list = Array.isArray(res.data) ? res.data : (res.data?.records || res.data?.accounts || res.data?.list || res.data?.rows || [])
    accounts.value = list
    total.value = Number(res.data?.total ?? res.data?.totalCount ?? res.data?.count ?? list.length) || 0
    dataAvailable.value = true
    // 更新 selected，确保指向新数组中的对象（避免指向旧引用导致 cookie_status 不同步）
    if (selected.value) {
      const updated = accounts.value.find(a => a.id === selected.value.id)
      if (updated) {
        selected.value = updated
      } else {
        selected.value = null
        selectedId.value = null
      }
    } else if (accounts.value.length) {
      selected.value = accounts.value[0]
      selectedId.value = accounts.value[0].id
    }
    const statusResults = await Promise.allSettled(accounts.value.map(a => loadWsStatus(a.id, {
      isCurrent: request.isCurrent,
      requestConfig,
    })))
    if (!request.isCurrent()) return
    const failedStatusCount = statusResults.filter(result => (
      result.status === 'rejected' || result.value?.statusUnavailable === true
    )).length
    if (failedStatusCount) accountListWarning.value = `${failedStatusCount} 个账号的连接状态暂不可用；未知状态不会显示为离线。`
  } catch (e) {
    if (!request.isCurrent()) return
    if (hadSnapshot) {
      accountListWarning.value = `账号列表刷新失败，继续显示上次成功加载的账号数据。${e.message ? ` ${e.message}` : ''}`
    } else {
      accounts.value = []
      total.value = 0
      selected.value = null
      selectedId.value = null
      dataAvailable.value = false
      error.value = e.message || '账号列表加载失败'
    }
  } finally {
    if (request.isCurrent()) loading.value = false
  }
}
function goPage(p) {
  current.value = p
  loadAccounts()
}

function selectAccount(account) {
  selected.value = account
  selectedId.value = account.id
  loadWsStatus(account.id).catch(() => {})
}

async function loadWsStatus(accountId, options = {}) {
  if (!accountId) return
  const isCurrent = typeof options.isCurrent === 'function' ? options.isCurrent : () => true
  try {
    const res = await websocketStatus(accountId, options.requestConfig)
    if (!isCurrent()) return null
    const data = res.data || {}
    if (typeof data.connected !== 'boolean') throw new Error('连接状态响应无法确认')
    wsMap[accountId] = data
    return data
  } catch (e) {
    if (!isCurrent()) return null
    wsMap[accountId] = {
      connected: null,
      statusUnavailable: true,
      status: '状态未知',
      refreshError: e.message || '连接状态探测失败'
    }
    throw e
  }
}

async function checkSelectedAuth() {
  if (!selected.value?.id) return
  try {
    const res = await checkAccountAuth(selected.value.id)
    const data = res.data || {}
    const account = accounts.value.find(item => item.id === selected.value.id)
    if (account) {
      account.cookieStatus = data.cookieStatus
      account.authUsable = data.usable
      account.loginStatusCode = data.loginStatusCode
      account.loginStatusMessage = data.loginStatusMessage
      account.loginCheckTime = data.checkedAt
    }
    if (selected.value?.id === account?.id) {
      selected.value = { ...selected.value, ...account }
    }
    qrSuccessMsg.value = data.loginStatusMessage || '登录校验已完成'
    setTimeout(() => {
      if (qrSuccessMsg.value === (data.loginStatusMessage || '登录校验已完成')) qrSuccessMsg.value = ''
    }, 4000)
    await loadAccounts()
  } catch (e) {
    error.value = e.message || '登录校验失败'
  }
}

async function refreshProfile(accountId, options = {}) {
  try {
    error.value = ''
    // 扫码/SSE 自动触发时标记为 background，避免闲鱼 API 响应慢导致
    // 全局"正在同步数据..."横幅遮挡页面；手动点击仍为 foreground。
    const requestConfig = options.background ? { uiMode: 'background', suppressGlobalError: true } : {}
    const res = await refreshAccountProfile(accountId, requestConfig)
    const data = res.data?.account || res.data || res
    // 刷新成功后的提示
    const displayName = data.displayName || data.nickname || ''
    qrSuccessMsg.value = displayName ? `资料刷新成功: ${displayName}` : '资料刷新成功'
    setTimeout(() => { if (qrSuccessMsg.value && qrSuccessMsg.value.startsWith('资料刷新')) qrSuccessMsg.value = '' }, 4000)
    // 更新选中账号的详情
    if (selected.value && selected.value.id === accountId) {
      selected.value = { ...selected.value, ...data }
    }
    await loadAccounts()
  } catch (e) {
    error.value = e.message || '刷新资料失败'
  }
}

function isWsBusy(accountId) { return !!wsBusyMap[accountId] }

function isWsPending(accountId) {
  const state = wsMap[accountId] || {}
  const phase = String(state.phase || state.status || '').toLowerCase()
  return state.connected !== true && state.statusUnavailable !== true && WS_START_PHASES.has(phase)
}

async function toggleWs(account) {
  if (!account?.id || isWsBusy(account.id)) return
  const initialState = wsMap[account.id] || {}
  if (initialState.connected == null || initialState.statusUnavailable === true) {
    error.value = '连接状态未知，请先刷新并确认状态；系统不会在未知状态下提交启动或断开命令。'
    return
  }
  if (isWsPending(account.id)) {
    error.value = '启动命令仍在处理中，请稍后刷新；系统不会重复提交。'
    return
  }
  wsBusyMap[account.id] = true
  error.value = ''
  try {
    if (initialState.connected === true) {
      await stopWebSocket(account.id)
      qrSuccessMsg.value = '断开请求已提交，正在确认状态'
      await new Promise(resolve => setTimeout(resolve, 800))
      const stoppedState = await loadWsStatus(account.id)
      if (stoppedState.connected === false) {
        qrSuccessMsg.value = 'WebSocket 已确认断开'
      } else {
        error.value = '断开请求已提交，但连接仍显示在线，请稍后刷新核对。'
      }
    } else {
      const res = await startWebSocket(account.id)
      const data = res?.data || {}
      wsMap[account.id] = { ...initialState, ...data }
      if (typeof data.connected !== 'boolean') {
        throw new Error('WebSocket 启动响应缺少连接状态')
      }
      if (data.connected === true) {
        qrSuccessMsg.value = data.optimistic
          ? 'WS 连接已提交，未检测到滑块/验证'
          : 'WS 连接已就绪，正在接收消息'
      } else {
        qrSuccessMsg.value = data.message || '连接请求返回未连接状态，请刷新后确认'
      }
      if (data.optimistic) {
        // 乐观确认：后端 12 秒内未检测到验证失败，8 秒后刷新实际状态
        setTimeout(() => loadWsStatus(account.id), 8000)
      } else {
        // 已确认连接/恢复中：短暂等待后刷新状态
        await new Promise(resolve => setTimeout(resolve, 1200))
        await loadWsStatus(account.id)
      }
      // 连接成功后刷新账号列表，同步 Cookie 状态
      // （后端自动登录校验已将 cookie_status 更新为 1，但前端 accounts 列表仍为旧值）
      loadAccounts()
    }
  } catch (e) {
    error.value = e.message || 'WebSocket 操作失败'
  } finally {
    wsBusyMap[account.id] = false
  }
}

async function removeAccount(accountId) {
  pendingDeleteId.value = accountId
  modal.value = 'confirmDelete'
}

async function executeDelete() {
  if (pendingDeleteId.value === null) return
  const id = pendingDeleteId.value
  pendingDeleteId.value = null
  closeModal()
  try {
    await deleteAccount(id)
    if (selected.value?.id === id) selected.value = null
    await loadAccounts()
  } catch (e) {
    error.value = e.message || '删除失败'
  }
}

async function submitManual() {
  manualError.value = ''
  if (!manual.cookie.trim()) return (manualError.value = '请输入 Cookie 字符串')
  // 前端预校验
  const validation = validateCookie(manual.cookie)
  if (!validation.valid) {
    manualError.value = validation.error
    return
  }
  submitting.value = true
  try {
    const keyFields = extractKeyFields(manual.cookie)
    await createAccountByCookie({
      accountNote: manual.accountNote.trim(),
      cookie: manual.cookie.trim(),
      extractedUnb: keyFields.unb,
      extractedMH5Tk: keyFields.mH5Tk,
    })
    closeModal()
    await loadAccounts()
  } catch (e) {
    manualError.value = e.message || '添加账号失败'
  } finally {
    submitting.value = false
  }
}

function openCookieEdit(account) {
  if (!account) return
  cookieEdit.accountId = account.id
  cookieEdit.cookie = ''
  cookieEditError.value = ''
  cookieEditSubmitting.value = false
  modal.value = 'cookieEdit'
}

async function submitCookieEdit() {
  cookieEditError.value = ''
  if (!cookieEdit.cookie.trim()) {
    cookieEditError.value = '请输入 Cookie 字符串'
    return
  }
  // 前端预校验
  const validation = validateCookie(cookieEdit.cookie)
  if (!validation.valid) {
    cookieEditError.value = validation.error
    return
  }
  // 身份校验（防串号）
  if (selected.value) {
    const keyFields = extractKeyFields(cookieEdit.cookie)
    const identity = checkIdentity(keyFields.unb, selected.value)
    if (!identity.valid) {
      cookieEditError.value = identity.error
      return
    }
  }
  cookieEditSubmitting.value = true
  try {
    // 提取关键字段一并传给后端
    const keyFields = extractKeyFields(cookieEdit.cookie)
    await updateAccountCookie(cookieEdit.accountId, cookieEdit.cookie.trim(), {
      unb: keyFields.unb,
      mH5Tk: keyFields.mH5Tk,
    })
    closeModal()
    qrSuccessMsg.value = 'Cookie 更新成功'
    setTimeout(() => { if (qrSuccessMsg.value === 'Cookie 更新成功') qrSuccessMsg.value = '' }, 4000)
    await loadAccounts()
  } catch (e) {
    cookieEditError.value = e.message || '更新 Cookie 失败'
  } finally {
    cookieEditSubmitting.value = false
  }
}

async function startQrLogin() {
  qr.loading = true
  qr.message = ''
  qr.sessionId = ''
  qr.qrUrl = ''
  qr.status = ''
  stopQrPolling()
  try {
    const res = qr.accountId
      ? await generateQrLogin({ accountId: qr.accountId })
      : await generateQrLogin()
    const data = res.data || {}
    qr.sessionId = data.sessionId || data.id || ''
    qr.qrUrl = data.qrCodeBase64 || data.qrImage || data.qrUrl || data.qrcodeUrl || data.qrCodeUrl || data.url || ''
    if (!qr.sessionId || !qr.qrUrl) throw new Error('二维码服务响应不完整，请稍后重试')
    qr.status = data.status || 'pending'
    qr.message = data.message || '请使用闲鱼 App 扫码'
    startQrPolling()
  } catch (e) {
    qr.sessionId = ''
    qr.qrUrl = ''
    qr.status = 'error'
    qr.message = e.message || '生成二维码失败'
  } finally {
    qr.loading = false
  }
}

function startQrPolling() {
  stopQrPolling()
  // 使用 setTimeout 链式轮询代替 setInterval，确保前一次请求返回后再发起下一次。
  // 避免闲鱼 API 响应慢时请求堆积，耗尽数据库连接池。
  qrTimer = setTimeout(scheduleQrPoll, 2000)
}
function stopQrPolling() { if (qrTimer) { clearTimeout(qrTimer); qrTimer = null } }
async function scheduleQrPoll() {
  await checkQrStatus()
  // checkQrStatus 内部可能调用 stopQrPolling（confirmed/error/expired），
  // 只有轮询仍在进行时才安排下一次。
  if (qrTimer) qrTimer = setTimeout(scheduleQrPoll, 2000)
}
async function checkQrStatus() {
  if (!qr.sessionId) return
  try {
    const res = qr.accountId
      ? await getQrLoginStatus(qr.sessionId, { accountId: qr.accountId })
      : await getQrLoginStatus(qr.sessionId)
    const data = res.data || {}
    const prevStatus = qr.status
    qr.status = data.status || qr.status
    qr.message = data.message || qr.message

    if (qr.status === 'confirmed') {
      if (data.accountId) {
        stopQrPolling()
        const isRescan = qr.mode === 'rescan'
        closeModal()
        const externalUid = data.externalUid || data.unb || ''
        qrSuccessMsg.value = externalUid
          ? `账号 ${externalUid.slice(0, 8)}... 登录成功`
          : '账号登录成功'
        if (isRescan) {
          qrSuccessMsg.value = data.message || (data.authUsable === false
            ? (data.loginStatusMessage || '重新扫码成功，但统一登录校验未通过')
            : '账号 Cookie 已更新')
        }
        if (!isRescan) {
          current.value = 1
        }
        try {
          await loadAccounts()
          const nextAccountId = Number(data.accountId || 0) || null
          if (nextAccountId && !isRescan) {
            const target = accounts.value.find(a => a.id === nextAccountId)
            if (target) {
              selectAccount(target)
            }
            // 扫码成功后自动刷新账号资料（background，避免闲鱼 API 慢时全局 loading 遮挡页面）
            refreshProfile(nextAccountId, { background: true }).catch(() => {
              if (import.meta.env.DEV) console.warn('自动刷新资料失败')
            })
          }
        } catch {
          if (import.meta.env.DEV) console.warn('账号列表刷新失败')
          qrSuccessMsg.value = '账号已保存，但列表刷新失败，请手动刷新'
        }
        setTimeout(() => { if (qrSuccessMsg.value) qrSuccessMsg.value = '' }, 6000)
      } else if (prevStatus !== 'confirmed' || data.credentialsAvailable) {
        qr.message = data.credentialsAvailable
          ? '扫码已确认，服务端正在安全同步账号信息...'
          : (data.message || '扫码已确认，等待服务端完成账号同步...')
      }
    }
    if (qr.status === 'error') {
      stopQrPolling()
      qr.message = data.message || '账号保存失败，请重试'
      if (data.errorCode) {
        if (import.meta.env.DEV) console.warn('扫码保存返回失败状态')
      }
    }
    if (['expired','failed'].includes(qr.status)) stopQrPolling()
  } catch (e) {
    qr.message = e.message || '查询扫码状态失败'
  }
}

async function handleSseEvent(e) {
  const event = e.detail
  if (!event) return
  if (event.type === 'account_added') {
    qrSuccessMsg.value = '账号登录成功'
    current.value = 1
    try {
      await loadAccounts()
      const addedAccountId = Number(event.accountId || event.account_id || 0) || null
      if (addedAccountId) {
        const target = accounts.value.find(a => a.id === addedAccountId)
        if (target) {
          selectAccount(target)
        }
        // 自动刷新账号资料（background，避免闲鱼 API 慢时全局 loading 遮挡页面）
        refreshProfile(addedAccountId, { background: true }).catch(() => {
          if (import.meta.env.DEV) console.warn('自动刷新资料失败')
        })
      } else if (accounts.value[0]) {
        selectAccount(accounts.value[0])
      }
    } catch {
      if (import.meta.env.DEV) console.warn('账号列表刷新失败')
    }
    setTimeout(() => { qrSuccessMsg.value = '' }, 4000)
  } else if (event.type === 'cookie_status_changed') {
    // 从服务端重新拉取账号列表，确保 cookie 状态与后端一致
    const targetId = event.accountId
    const newStatus = event.cookieStatus
    // 优先使用后端返回的真实状态码与消息，避免前端硬编码误判（如滑块求解失败
    // 但 Cookie 实际正常的场景，后端会返回 status_message 说明真实原因）
    const backendCode = event.loginStatusCode || ''
    const backendMessage = event.loginStatusMessage || ''
    // 先更新本地缓存，避免列表闪现旧状态
    const account = accounts.value.find(a => a.id === targetId)
    if (account) {
      account.cookieStatus = newStatus
      account.authUsable = Number(newStatus) === 1
      account.loginStatusCode = backendCode || (Number(newStatus) === 1 ? 'OK' : 'COOKIE_EXPIRED')
      account.loginStatusMessage = backendMessage || (Number(newStatus) === 1 ? '账号登录状态正常' : 'Cookie 已失效，请重新登录闲鱼账号')
      if (selected.value && selected.value.id === targetId) {
        selected.value.cookieStatus = newStatus
        selected.value.authUsable = Number(newStatus) === 1
        selected.value.loginStatusCode = backendCode || (Number(newStatus) === 1 ? 'OK' : 'COOKIE_EXPIRED')
        selected.value.loginStatusMessage = backendMessage || (Number(newStatus) === 1 ? '账号登录状态正常' : 'Cookie 已失效，请重新登录闲鱼账号')
      }
    }
    // 显示提示信息：仅当 Cookie 真正失效时提示，且使用后端返回的真实消息
    // （避免滑块求解失败但 Cookie 正常时弹出误导性"已失效"提示）
    if (newStatus === 0) {
      const hint = backendMessage || `账号 ${targetId} Cookie 已失效，请更换 Cookie 或重新扫码登录`
      qrSuccessMsg.value = hint
      setTimeout(() => { if (qrSuccessMsg.value === hint) qrSuccessMsg.value = '' }, 8000)
    }
    // 从服务端重新拉取，确保数据同步
    loadAccounts()
  }
}

onMounted(() => {
  window.addEventListener('xya-header-action', handleHeaderAction)
  window.addEventListener('xya-sse-event', handleSseEvent)
  loadAccounts()
})
onBeforeUnmount(() => {
  accountsRequestGuard.invalidate()
  window.removeEventListener('xya-header-action', handleHeaderAction)
  window.removeEventListener('xya-sse-event', handleSseEvent)
  stopQrPolling()
  void cleanupQrLogin()
})
</script>

<style scoped>
.account-selector { width: 100%; padding: 0; border: 0; background: transparent; color: inherit; font: inherit; text-align: left; cursor: pointer; }
.account-selector-copy { min-width: 0; }
.refresh-status {
  margin-bottom: 10px;
  color: #526079;
  font-size: 13px;
}

.qr-unavailable {
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 18px;
  box-sizing: border-box;
  color: #667085;
  background: #f8fafc;
  text-align: center;
  font-size: 12px;
}

.grid.wide-right {
  grid-template-columns: minmax(0, 1fr) 392px;
  gap: 16px;
  align-items: start;
}

.account-detail-drawer {
  min-width: 0;
  padding: 15px 14px 14px;
  border: 1px solid #e6edf5;
  border-radius: 9px;
  background: #fff;
  box-shadow: 0 2px 12px rgba(39, 72, 118, 0.045);
  color: #2a3851;
}

.detail-title-row,
.account-summary,
.account-name-row,
.health-footer,
.quick-actions button {
  display: flex;
  align-items: center;
}

.detail-title-row {
  justify-content: space-between;
}

.detail-title-row h3,
.account-detail-drawer h4 {
  margin: 0;
  color: #1e2d47;
  font-weight: 700;
}

.detail-title-row h3 {
  font-size: 15px;
  line-height: 24px;
}

.account-detail-drawer h4 {
  font-size: 14px;
  line-height: 22px;
}

.detail-close {
  width: 26px;
  height: 26px;
  border: 0;
  background: transparent;
  color: #51627c;
  font-size: 24px;
  font-weight: 300;
  line-height: 20px;
  cursor: pointer;
}

.account-summary {
  position: relative;
  gap: 12px;
  margin-top: 14px;
  padding: 0 0 13px;
}

.detail-avatar {
  width: 56px;
  height: 56px;
  flex: 0 0 56px;
  border-radius: 50%;
  object-fit: cover;
  background: linear-gradient(135deg, #e7f0fa, #f7fbff);
  box-shadow: inset 0 0 0 1px #e5edf5;
}

.avatar-fallback {
  position: relative;
}

.avatar-fallback::before {
  position: absolute;
  inset: 13px;
  border-radius: 50%;
  background: #cfdae7;
  content: '';
}

.account-summary-main {
  min-width: 0;
  padding-right: 4px;
}

.account-name-row {
  gap: 8px;
  min-height: 22px;
}

.account-name-row strong {
  max-width: 128px;
  overflow: hidden;
  color: #23324b;
  font-size: 15px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.current-account-tag,
.plan-tag {
  display: inline-flex;
  align-items: center;
  min-height: 21px;
  padding: 0 9px;
  border-radius: 4px;
  background: #edf4ff;
  color: #4087ff;
  font-size: 11px;
  font-weight: 600;
  white-space: nowrap;
}

.account-meta-line {
  margin-top: 4px;
  color: #687891;
  font-size: 12px;
  line-height: 18px;
  white-space: nowrap;
}

.account-meta-split {
  display: flex;
  align-items: center;
  gap: 10px;
}

.account-meta-split i {
  width: 1px;
  height: 12px;
  background: #e2e9f1;
}

.online-state {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-left: auto;
  align-self: flex-start;
  padding-top: 3px;
  color: #19bd78;
  font-size: 12px;
  font-weight: 600;
  white-space: nowrap;
}

.online-state i,
.health-metrics i {
  display: inline-block;
  border-radius: 50%;
  background: #18bf78;
}

.online-state i {
  width: 7px;
  height: 7px;
}

.online-state.offline {
  color: #9aa8ba;
}

.online-state.offline i {
  background: #9aa8ba;
}

.online-state.unknown {
  color: #a05b00;
}

.online-state.unknown i {
  background: #ffb547;
}

.health-card,
.profile-stats-card {
  border: 1px solid #e8eef5;
  border-radius: 8px;
  background: #fff;
  box-shadow: 0 2px 7px rgba(31, 65, 113, 0.035);
}

.health-card {
  padding: 11px 12px 10px;
}

.health-content {
  display: grid;
  grid-template-columns: 134px 1fr;
  align-items: center;
  min-height: 110px;
}

.health-ring-wrap {
  display: flex;
  justify-content: center;
}

.health-ring {
  position: relative;
  width: 88px;
  height: 88px;
  border-radius: 50%;
  background:
    conic-gradient(
      #16bf78 var(--health-score),
      #e8f1ee 0
    );
}

.health-ring::before {
  position: absolute;
  inset: 8px;
  border-radius: 50%;
  background: #fff;
  content: '';
}

.health-ring-inner {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  flex-direction: column;
  justify-content: center;
}

.health-ring-inner strong {
  color: #22304a;
  font-size: 22px;
  line-height: 24px;
}

.health-ring-inner small {
  margin-left: 1px;
  font-size: 10px;
}

.health-ring-inner span {
  margin-top: 2px;
  color: #6d7c91;
  font-size: 11px;
}

.health-metrics {
  display: grid;
  gap: 8px;
}

.health-metrics div {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  color: #5d6d84;
  font-size: 12px;
}

.health-metrics span {
  display: flex;
  align-items: center;
  gap: 9px;
  white-space: nowrap;
}

.health-metrics i {
  width: 6px;
  height: 6px;
}

.health-metrics b {
  color: #19bd78;
  font-size: 12px;
  font-weight: 700;
  white-space: nowrap;
}

.health-footer {
  justify-content: space-between;
  gap: 12px;
  padding-top: 4px;
  color: #78879b;
  font-size: 11px;
}

.text-action {
  border: 0;
  background: transparent;
  color: #3486ff;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  white-space: nowrap;
}

.text-action b {
  font-size: 18px;
  font-weight: 400;
  vertical-align: -1px;
}

.drawer-section {
  padding: 17px 3px 0;
}

.activity-list {
  display: grid;
  gap: 8px;
  margin-top: 10px;
}

.activity-list div {
  display: grid;
  grid-template-columns: 94px minmax(0, 1fr);
  gap: 10px;
  color: #617189;
  font-size: 12px;
  line-height: 17px;
}

.activity-list time {
  color: #74849a;
}

.activity-more {
  padding: 7px 0 0;
}

.quick-section {
  margin-top: 11px;
  padding-top: 15px;
  border-top: 1px solid #eef2f6;
}

.quick-actions {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
  margin-top: 10px;
  position: relative;
}

.quick-actions button {
  justify-content: center;
  gap: 7px;
  min-width: 0;
  height: 38px;
  padding: 0 5px;
  border: 1px solid #e1e9f2;
  border-radius: 5px;
  background: #fff;
  color: #53627a;
  font-size: 12px;
  cursor: pointer;
  white-space: nowrap;
}

.quick-actions button:hover {
  border-color: #bcd4ff;
  color: #3383ff;
}

.quick-actions button span {
  color: #5d7190;
  font-size: 17px;
  line-height: 1;
}

.quick-actions .more-action {
  grid-column: 3;
}

.modal-subtitle {
  margin: 10px 0 0;
  color: #66768f;
  font-size: 13px;
  line-height: 1.6;
}

.batch-auth-grid {
  display: grid;
  gap: 10px;
  margin-top: 14px;
}

.batch-auth-card {
  width: 100%;
  padding: 14px 16px;
  border: 1px solid #dbe5f1;
  border-radius: 10px;
  background: #fff;
  text-align: left;
  cursor: pointer;
  transition: border-color .15s ease, box-shadow .15s ease, transform .15s ease;
}

.batch-auth-card:hover:not(:disabled) {
  border-color: #82aef7;
  box-shadow: 0 8px 20px rgba(64, 110, 188, 0.08);
  transform: translateY(-1px);
}

.batch-auth-card:disabled {
  cursor: not-allowed;
  opacity: .65;
}

.batch-auth-card strong {
  display: block;
  color: #20304a;
  font-size: 14px;
}

.batch-auth-card span {
  display: block;
  margin-top: 6px;
  color: #6b7b92;
  font-size: 12px;
  line-height: 1.6;
}

.more-actions-backdrop {
  position: fixed;
  inset: 0;
  z-index: 240;
}

.more-actions-menu {
  position: absolute;
  right: 0;
  top: 100%;
  margin-top: 6px;
  z-index: 250;
  background: #fff;
  border: 1px solid #e8eef8;
  border-radius: 12px;
  box-shadow: 0 12px 32px rgba(31, 53, 94, .14);
  padding: 6px;
  display: flex;
  flex-direction: column;
  min-width: 160px;
}

.more-actions-menu button {
  appearance: none;
  border: none;
  background: transparent;
  padding: 10px 14px;
  font-size: 14px;
  color: #2c3e50;
  text-align: left;
  border-radius: 8px;
  cursor: pointer;
  transition: background .15s;
}

.more-actions-menu button:hover {
  background: #f1f6ff;
  color: #2d5bff;
}

.detail-empty {
  min-height: 280px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #9aa8ba;
}

/* Table row selection highlight */
.base-table tbody tr.row-selected {
  background: #e6f4ff;
  box-shadow: inset 3px 0 0 #1677ff;
}
.base-table tbody tr.row-selected:hover {
  background: #d6edff;
}

/* ===== 闲鱼主页资料卡片 ===== */
.profile-stats-card {
  margin-top: 13px;
  padding: 12px 12px 11px;
}

.profile-stats-card h4 {
  margin: 0 0 10px 0;
}

.profile-intro {
  margin-bottom: 10px;
  padding: 8px 10px;
  border-radius: 5px;
  background: #f7f9fc;
}

.profile-intro-label {
  display: block;
  margin-bottom: 4px;
  color: #8e9cb0;
  font-size: 11px;
}

.profile-intro p {
  margin: 0;
  color: #3a4b63;
  font-size: 12px;
  line-height: 18px;
  word-break: break-all;
}

.profile-stats-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 8px;
  margin-bottom: 10px;
}

.profile-stat-item {
  text-align: center;
  padding: 8px 4px;
  border-radius: 5px;
  background: #f7f9fc;
}

.profile-stat-item b {
  display: block;
  color: #1e2d47;
  font-size: 16px;
  font-weight: 700;
  line-height: 22px;
}

.profile-stat-item span {
  color: #7a8ca5;
  font-size: 11px;
}

.profile-extra {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 6px 12px;
}

.profile-extra-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 5px 0;
  color: #687891;
  font-size: 12px;
}

.profile-extra-item b {
  color: #34425a;
  font-weight: 600;
}

.green-tag {
  color: #13be77 !important;
}

/* Cookie 状态警告横幅 */
.cookie-warn-banner {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 12px;
  padding: 10px 12px;
  border-radius: 6px;
  background: #fff3e0;
  border: 1px solid #ffcc80;
  color: #e65100;
  font-size: 12px;
  line-height: 18px;
}

.cookie-warn-banner.cookie-expired {
  background: #ffebee;
  border-color: #ef9a9a;
  color: #c62828;
}

/* 编辑账号信息 */
.edit-account-info {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 14px;
  padding: 8px 12px;
  border-radius: 5px;
  background: #f5f7fa;
  font-size: 13px;
  color: #58687a;
}

.edit-account-info b {
  color: #1e2d47;
  font-weight: 600;
}

@media (max-width: 1480px) {
  .grid.wide-right {
    grid-template-columns: minmax(0, 1fr) 360px;
  }

  .account-meta-split {
    display: block;
  }

  .account-meta-split i {
    display: none;
  }

  .health-content {
    grid-template-columns: 118px minmax(0, 1fr);
  }
}

/* ===== Cookie 解析预览 ===== */
.cookie-parse-preview {
  margin-top: 10px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  overflow: hidden;
}

.cookie-parse-error {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 10px 12px;
  background: #fff3e0;
  border-bottom: 1px solid #ffcc80;
  color: #e65100;
  font-size: 12px;
  line-height: 18px;
}

.cookie-parse-error.cookie-identity-error {
  background: #ffebee;
  border-color: #ef9a9a;
  color: #c62828;
}

.cookie-parse-fields {
  padding: 10px 12px;
  background: #f8fafc;
}

.cookie-parse-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.cookie-parse-header span {
  font-size: 13px;
  font-weight: 600;
  color: #334155;
}

.cookie-parse-header em {
  font-size: 11px;
  color: #94a3b8;
  font-style: normal;
}

.cookie-field-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 6px;
}

.cookie-field-item {
  padding: 6px 8px;
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 5px;
}

.cookie-field-item.missing {
  background: #fff8f8;
  border-color: #fecaca;
}

.cookie-field-item label {
  display: block;
  font-size: 11px;
  color: #64748b;
  margin-bottom: 2px;
}

.cookie-field-item code {
  display: block;
  font-size: 12px;
  color: #334155;
  font-family: 'SF Mono', 'Cascadia Code', 'Consolas', monospace;
  word-break: break-all;
}

.cookie-field-item.missing code {
  color: #dc2626;
}

.required-mark {
  color: #ef4444;
  font-size: 10px;
  margin-left: 4px;
}

.cookie-parse-warning {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  margin-top: 8px;
  padding: 6px 8px;
  background: #fffbeb;
  border: 1px solid #fde68a;
  border-radius: 5px;
  color: #92400e;
  font-size: 11px;
  line-height: 16px;
}

.current-unb-tag {
  display: inline-flex;
  align-items: center;
  margin-left: 8px;
  padding: 2px 8px;
  background: #eef2ff;
  border: 1px solid #c7d2fe;
  border-radius: 4px;
  color: #4338ca;
  font-size: 11px;
  font-weight: 500;
}

.diagnosis-card{border:1px solid #e8eef8;border-radius:18px;padding:16px;background:#fbfdff;margin:14px 0}
.diagnosis-card h4{margin:0 0 12px;color:#16213e}
.diagnosis-item{padding:10px 0;border-bottom:1px solid #eef3fa}
.diagnosis-item:last-child{border-bottom:0}
.diagnosis-item span{display:flex;align-items:center;gap:8px;color:#526079;font-weight:700}
.diagnosis-item i{width:9px;height:9px;border-radius:50%;background:#16bf78}.diagnosis-item.warn i{background:#f79009}.diagnosis-item.danger i{background:#ef4444}
.diagnosis-item b{display:block;margin-top:5px;color:#16213e}.diagnosis-item small{display:block;margin-top:4px;color:#667085;line-height:1.5}
.diagnosis-actions{display:flex;gap:10px;flex-wrap:wrap;margin-top:12px}

/* 删除确认弹窗 */
.confirm-delete-modal {
  width: 420px;
  text-align: center;
  padding: 40px 36px 28px;
}

.confirm-delete-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 64px;
  height: 64px;
  margin: 0 auto 18px;
  border-radius: 50%;
  background: #fef2f2;
}

.confirm-delete-icon .ui-icon {
  width: 32px;
  height: 32px;
}

.confirm-delete-modal h2 {
  margin: 0 0 12px;
  font-size: 18px;
  font-weight: 700;
  color: #1e293b;
}

.confirm-delete-desc {
  margin: 0 0 28px;
  font-size: 13px;
  line-height: 1.6;
  color: #64748b;
}

.confirm-delete-actions {
  display: flex;
  gap: 12px;
  justify-content: center;
}

.confirm-delete-actions .app-btn {
  min-width: 120px;
  height: 40px;
  font-size: 14px;
}

/* ===== 移动端响应式 (max-width: 900px) ===== */
@media (max-width: 900px) {
  /* 双列网格 → 单列堆叠 */
  .grid.wide-right {
    grid-template-columns: minmax(0, 1fr);
    gap: 12px;
  }

  /* 账号详情抽屉：减小内边距 */
  .account-detail-drawer {
    padding: 12px;
  }

  /* 详情标题字号收敛 */
  .detail-title-row h3 {
    font-size: 16px;
    line-height: 22px;
  }

  .account-detail-drawer h4 {
    font-size: 14px;
    line-height: 20px;
  }

  /* 账号摘要：保持 flex 但收紧间距 */
  .account-summary {
    gap: 10px;
    margin-top: 10px;
    padding: 0 0 10px;
    flex-wrap: wrap;
  }

  .detail-avatar {
    width: 48px;
    height: 48px;
    flex: 0 0 48px;
  }

  .account-name-row strong {
    max-width: 100%;
    font-size: 14px;
  }

  /* 在线状态换行 */
  .online-state {
    margin-left: 0;
  }

  /* 健康卡：双列 → 单列 */
  .health-content {
    grid-template-columns: minmax(0, 1fr);
    gap: 12px;
    min-height: auto;
  }

  .health-ring {
    width: 76px;
    height: 76px;
  }

  .health-ring-inner strong {
    font-size: 18px;
    line-height: 20px;
  }

  /* 最近活动：时间列变窄 */
  .activity-list div {
    grid-template-columns: 80px minmax(0, 1fr);
    gap: 8px;
  }

  /* 快捷操作：3列 → 3列保持（按钮多）但减小内边距 */
  .quick-actions {
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 8px;
  }

  .quick-actions button {
    height: 40px;
    padding: 0 4px;
    font-size: 11px;
  }

  .quick-actions button span {
    font-size: 15px;
  }

  /* 闲鱼主页资料：4列 → 2列 */
  .profile-stats-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 8px;
  }

  .profile-stat-item b {
    font-size: 15px;
  }

  /* 资料附加项：2列 → 1列 */
  .profile-extra {
    grid-template-columns: minmax(0, 1fr);
    gap: 4px;
  }

  /* Cookie 解析字段网格：2列 → 1列 */
  .cookie-field-grid {
    grid-template-columns: minmax(0, 1fr);
  }

  /* 网格子元素防止溢出 */
  .health-content > *,
  .profile-stats-grid > *,
  .profile-extra > *,
  .cookie-field-grid > * {
    min-width: 0;
  }

  /* 诊断卡内边距收敛 */
  .diagnosis-card {
    padding: 12px;
    border-radius: 14px;
    margin: 10px 0;
  }

  .diagnosis-actions {
    gap: 8px;
  }

  /* 批量登录检查卡片内边距收敛 */
  .batch-auth-card {
    padding: 12px;
  }

  /* 删除确认弹窗：固定宽度 → 全宽底部弹出 */
  .confirm-delete-modal {
    width: 100%;
    max-width: 420px;
    padding: 24px 18px 20px;
  }

  .confirm-delete-icon {
    width: 56px;
    height: 56px;
    margin: 0 auto 14px;
  }

  .confirm-delete-modal h2 {
    font-size: 17px;
  }

  .confirm-delete-desc {
    margin: 0 0 20px;
    font-size: 13px;
  }

  .confirm-delete-actions .app-btn {
    min-width: 100px;
    height: 40px;
  }

  /* 更多操作菜单：固定定位底部弹出 */
  .more-actions-menu {
    position: fixed;
    left: 12px;
    right: 12px;
    top: auto;
    bottom: 12px;
    margin: 0;
    min-width: 0;
    border-radius: 14px;
  }

  /* 宽表格横向滚动 */
  .base-table {
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
  }

  .base-table tbody {
    white-space: nowrap;
  }

  /* 编辑账号信息行允许换行 */
  .edit-account-info {
    flex-wrap: wrap;
    gap: 6px;
    font-size: 12px;
  }

  /* Cookie 警告横幅内边距收敛 */
  .cookie-warn-banner {
    padding: 8px 10px;
    font-size: 12px;
    line-height: 16px;
  }
}
</style>
