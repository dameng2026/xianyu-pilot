<template>
  <div class="notify-settings-shell">
    <div v-if="loading" class="global-notice" role="status" aria-live="polite">正在加载通知配置与投递记录...</div>
    <div v-if="error" class="global-notice error" role="alert">{{ error }}</div>
    <div v-if="success" class="global-notice success" role="status">{{ success }}</div>

    <section
      v-if="testAttempt.visible"
      class="notification-test-attempt"
      :class="`is-${testAttempt.status || 'unknown'}`"
      role="status"
    >
      <div>
        <strong>通知测试发送安全状态</strong>
        <span v-if="testAttempt.attemptId">Attempt #{{ testAttempt.attemptId }}</span>
      </div>
      <p>{{ testAttempt.message }}</p>
      <p v-if="testAttempt.status === 'unknown'" class="notification-resolution-warning">
        高风险操作：只有在你已从通知服务方人工核对、确认不会再收到本次测试后，才能关闭未知意图。
        关闭只解除本渠道测试锁，不会撤回可能已送达的消息，也不会自动发送新测试。
      </p>
      <dl>
        <div><dt>渠道</dt><dd>{{ testAttempt.channelKey || '-' }}</dd></div>
        <div><dt>状态</dt><dd>{{ testAttempt.status || 'unknown' }}</dd></div>
        <div><dt>retrySafe</dt><dd>{{ String(testAttempt.retrySafe) }}</dd></div>
        <div><dt>logPersisted</dt><dd>{{ String(testAttempt.logPersisted) }}</dd></div>
      </dl>
      <button
        type="button"
        class="notify-link-button"
        :disabled="testing || resolving || saving || loading || !testAttempt.replaySafe"
        @click="recoverNotificationTestAttempt"
      >
        {{ testAttempt.status === 'confirmed' ? '使用原意图补写审计日志' : '使用原意图核对状态' }}
      </button>
      <button
        v-if="testAttempt.status === 'unknown' && testAttempt.replaySafe && testAttempt.attemptId"
        type="button"
        class="notification-resolution-button"
        :disabled="testing || resolving || saving || loading"
        @click="resolveUnknownNotificationTestAttempt"
      >
        {{ resolving ? '正在关闭未知意图...' : '已人工核对，关闭未知意图' }}
      </button>
    </section>

    <section class="notify-hero">
      <div class="notify-hero-copy">
        <span class="notify-hero-pill">Notification Control Center</span>
        <h1>通知设置</h1>
        <p>
          统一管理通知渠道、事件触发规则、投递健康与应用内提醒。左侧选择渠道，中央信息完成配置，
          右侧实时查看规则与最近发送结果，让通知管理从“表单堆叠”升级为“工作台”。
        </p>
      </div>

      <aside class="notify-health-card">
        <div class="notify-health-top">
          <span class="notify-health-icon">
            <Icon name="notifyHealth" />
          </span>
          <div>
            <b>投递健康度</b>
            <strong>{{ heroHealthLabel }}</strong>
          </div>
        </div>
        <div class="notify-health-progress">
          <span :style="{ width: `${healthPercent}%` }"></span>
        </div>
        <div class="notify-health-foot">
          <span>今日应用内通知 {{ visualTodayDisplay }}</span>
          <span>待处理提醒 {{ visualPendingDisplay }}</span>
          <span>平均耗时 {{ averageLatencyDisplay }}</span>
        </div>
      </aside>
    </section>

    <section class="notify-summary-grid">
      <article
        v-for="card in summaryCards"
        :key="card.label"
        class="notify-summary-card"
        :class="card.tone"
      >
        <span class="notify-summary-icon" :class="card.tone">
          <Icon :name="card.icon" />
        </span>
        <div class="notify-summary-copy">
          <small>{{ card.label }}</small>
          <strong>{{ card.value }}</strong>
          <span>{{ card.detail }}</span>
        </div>
      </article>
    </section>

    <EmptyState
      v-if="settingsAvailable === false"
      icon="⚠️"
      title="通知配置暂不可用"
      description="当前无法确认已保存的渠道与规则；为避免用默认空配置覆盖真实设置，编辑、保存和测试均已禁用。"
    >
      <template #actions><AppButton :disabled="loading" @click="load(true)">重新加载配置</AppButton></template>
    </EmptyState>

    <section v-if="settingsAvailable === true" class="notify-workspace-grid">
      <div class="notify-left-column">
        <section class="notify-panel">
          <div class="notify-panel-head">
            <div>
              <h3>通知渠道</h3>
              <p>常用渠道聚合在这里，突出当前选中项与连接状态。</p>
            </div>
            <span class="notify-tiny-chip">{{ channels.length }} 个渠道</span>
          </div>

          <div class="notify-panel-body">
            <div class="notify-channel-list">
              <button
                v-for="channel in channels"
                :key="channel.key"
                type="button"
                class="notify-channel-item"
                :class="[channel.accent, { active: channel.key === selectedKey }]"
                @click="selectedKey = channel.key"
              >
                <span class="notify-channel-icon" :class="channel.accent">
                  <Icon :name="channel.icon" />
                </span>
                <div class="notify-channel-copy">
                  <strong>{{ channel.name }}</strong>
                  <p>{{ renderChannelDescription(channel) }}</p>
                  <span
                    class="notify-channel-state"
                    :class="{ off: !channel.enabled, muted: !isChannelConfigured(channel) }"
                  >
                    {{ channelStatusText(channel) }}
                  </span>
                </div>
              </button>

              <button type="button" class="notify-add-channel" @click="addChannel">
                <Icon name="plus" />
                <span>添加自定义渠道</span>
              </button>
            </div>
          </div>
        </section>

        <section class="notify-side-note">
          <div class="notify-side-note-head">
            <strong>设计亮点</strong>
          </div>
          <p>
            用更强的主次关系取代“列表 + 表单 + 表格”的平铺结构，让通知管理更直观。
          </p>
          <ul>
            <li>先看整体健康概况，再选择渠道配置</li>
            <li>当前激活渠道在左侧列表中更突出</li>
            <li>规则与日志独立放在右侧，减少视觉抢占</li>
          </ul>
        </section>
      </div>

      <div class="notify-center-column">
        <section v-if="selectedChannel" class="notify-panel notify-config-card">
          <div class="notify-panel-head">
            <div>
              <h3>{{ selectedChannel.name }} 配置</h3>
              <p>把基础信息、连接参数、模板与发送策略收拢到同一个工作区，减少来回切换。</p>
            </div>
            <span class="notify-tiny-chip">当前生效</span>
          </div>

          <div class="notify-panel-body notify-config-body">
            <div class="notify-status-strip" :class="{ disconnected: !isChannelConfigured(selectedChannel) }">
              <div class="notify-strip-main">
                <span class="notify-strip-icon">
                  <Icon :name="isChannelConfigured(selectedChannel) ? 'notifyStatus' : 'notifyAlert'" />
                </span>
                <div class="notify-strip-copy">
                  <strong>{{ selectedChannelStatus.title }}</strong>
                  <span>{{ selectedChannelStatus.description }}</span>
                </div>
              </div>
              <button
                type="button"
                class="notify-link-button"
                :disabled="testing || resolving || saving || settingsDirty || !isChannelConfigured(selectedChannel)"
                :aria-busy="testing"
                @click="test"
              >
                {{ testing ? '测试中...' : '测试连接' }}
              </button>
            </div>

            <section class="notify-config-section">
              <h4>基础信息</h4>
              <div class="notify-form-grid two">
                <label class="notify-field">
                  <span>渠道名称</span>
                  <input v-model="selectedChannel.name" type="text" />
                </label>

                <div class="notify-field">
                  <span>启用状态</span>
                  <div class="notify-segment">
                    <button
                      type="button"
                      :class="{ active: selectedChannel.enabled }"
                      :aria-pressed="selectedChannel.enabled"
                      @click="selectedChannel.enabled = true"
                    >
                      启用
                    </button>
                    <button
                      type="button"
                      :class="{ active: !selectedChannel.enabled }"
                      :aria-pressed="!selectedChannel.enabled"
                      @click="selectedChannel.enabled = false"
                    >
                      停用
                    </button>
                  </div>
                </div>
              </div>
            </section>

            <section v-if="selectedChannel.type === 'email'" class="notify-config-section">
              <h4>SMTP 配置</h4>
              <div class="notify-form-grid two">
                <label class="notify-field">
                  <span>SMTP Host</span>
                  <input v-model="selectedChannel.smtpHost" type="text" placeholder="smtp.example.com" />
                </label>
                <label class="notify-field">
                  <span>端口</span>
                  <input v-model.number="selectedChannel.smtpPort" type="number" min="1" max="65535" />
                </label>
                <label class="notify-field">
                  <span>登录账号</span>
                  <input v-model="selectedChannel.smtpUser" type="text" placeholder="bot@example.com" />
                </label>
                <label class="notify-field">
                  <span>授权码</span>
                  <div class="notify-input-wrap">
                    <input
                      v-model="selectedChannel.smtpPass"
                      type="password"
                      autocomplete="new-password"
                      :placeholder="selectedChannel.smtpPassConfigured ? '已配置，留空保留' : '请输入 SMTP 授权码'"
                    />
                    <button
                      v-if="selectedChannel.smtpPassConfigured"
                      type="button"
                      class="notify-input-action notify-clear-action"
                      title="清除已保存的 SMTP 授权码"
                      @click="clearSecretField(selectedChannel, 'smtpPass', 'smtpPassConfigured')"
                    >
                      <Icon name="close" />
                    </button>
                  </div>
                </label>
                <label class="notify-field">
                  <span>发件人</span>
                  <input v-model="selectedChannel.fromEmail" type="text" placeholder="bot@example.com" />
                </label>
                <label class="notify-field">
                  <span>收件人</span>
                  <input v-model="selectedChannel.receiver" type="text" placeholder="ops@example.com" />
                </label>
              </div>
            </section>

            <section v-else-if="selectedChannel.type === 'feishu_app'" class="notify-config-section">
              <h4>飞书自建应用凭证</h4>
              <div class="notify-form-grid two">
                <label class="notify-field">
                  <span>App ID</span>
                  <input v-model="selectedChannel.appId" type="text" placeholder="cli_xxxxxxxxxxxxxxx" />
                </label>
                <label class="notify-field">
                  <span>App Secret</span>
                  <div class="notify-input-wrap">
                    <input
                      v-model="selectedChannel.secret"
                      type="password"
                      autocomplete="new-password"
                      :placeholder="selectedChannel.secretConfigured ? '已配置，留空保留' : '飞书自建应用 App Secret'"
                    />
                    <button
                      v-if="selectedChannel.secretConfigured"
                      type="button"
                      class="notify-input-action notify-clear-action"
                      title="清除已保存的 App Secret"
                      @click="clearSecretField(selectedChannel, 'secret', 'secretConfigured')"
                    >
                      <Icon name="close" />
                    </button>
                  </div>
                </label>
                <label class="notify-field">
                  <span>Verification Token</span>
                  <div class="notify-input-wrap">
                    <input
                      v-model="selectedChannel.verificationToken"
                      type="password"
                      autocomplete="new-password"
                      :placeholder="selectedChannel.verificationTokenConfigured ? '已配置，留空保留' : '事件订阅的 Verification Token'"
                    />
                    <button
                      v-if="selectedChannel.verificationTokenConfigured"
                      type="button"
                      class="notify-input-action notify-clear-action"
                      title="清除已保存的 Verification Token"
                      @click="clearSecretField(selectedChannel, 'verificationToken', 'verificationTokenConfigured')"
                    >
                      <Icon name="close" />
                    </button>
                  </div>
                </label>
                <label class="notify-field">
                  <span>Encrypt Key（可选）</span>
                  <div class="notify-input-wrap">
                    <input
                      v-model="selectedChannel.encryptKey"
                      type="password"
                      autocomplete="new-password"
                      :placeholder="selectedChannel.encryptKeyConfigured ? '已配置，留空保留' : '启用加密时填写'"
                    />
                    <button
                      v-if="selectedChannel.encryptKeyConfigured"
                      type="button"
                      class="notify-input-action notify-clear-action"
                      title="清除已保存的 Encrypt Key"
                      @click="clearSecretField(selectedChannel, 'encryptKey', 'encryptKeyConfigured')"
                    >
                      <Icon name="close" />
                    </button>
                  </div>
                </label>
                <label class="notify-field">
                  <span>接收者 ID（receiveId）</span>
                  <input v-model="selectedChannel.receiveId" type="text" placeholder="接收消息的用户/群 open_id 或 chat_id" />
                </label>
                <label class="notify-field">
                  <span>接收者 ID 类型</span>
                  <select v-model="selectedChannel.receiveIdType">
                    <option value="open_id">open_id（个人）</option>
                    <option value="user_id">user_id（个人）</option>
                    <option value="chat_id">chat_id（群聊）</option>
                    <option value="union_id">union_id（个人）</option>
                  </select>
                </label>
              </div>
              <div class="notify-tips">
                <p>
                  <strong>配置步骤：</strong>
                </p>
                <ol>
                  <li>前往 <a href="https://open.feishu.cn/" target="_blank">飞书开放平台</a> 注册账号并创建团队（免费，无需企业认证）</li>
                  <li>创建自建应用 → 获取 App ID 和 App Secret</li>
                  <li>启用「机器人」能力</li>
                  <li>配置「事件订阅」→ 请求地址填：<code>POST {{ windowLocationOrigin }}/api/feishu/webhook</code></li>
                  <li>订阅事件：<code>im.message.receive_v1</code>（接收用户消息）</li>
                  <li>复制 Verification Token（如启用加密还需 Encrypt Key）填入上方表单</li>
                  <li>在飞书中与机器人发起对话，获取你的 open_id 填入「接收者 ID」</li>
                  <li>权限管理勾选：发送消息、上传图片、读取用户信息</li>
                </ol>
                <p style="margin-top: 8px; color: #ff9800;">
                  <strong>提示：</strong>账号 Session 过期时，系统会通过此渠道主动推送通知。飞书对话不提供二维码自动登录；请前往 Web 管理端的账号管理页，选择账号后使用“重新扫码”安全登录。
                </p>
              </div>
            </section>

            <section v-else-if="selectedChannel.type === 'pushplus'" class="notify-config-section">
              <h4>接入凭证</h4>
              <div class="notify-form-grid two">
                <label class="notify-field notify-field-full">
                  <span>PushPlus Token</span>
                  <input v-model="selectedChannel.receiver" type="text" placeholder="请输入 PushPlus Token" />
                </label>
                <label class="notify-field">
                  <span>超时时间（秒）</span>
                  <input v-model.number="selectedChannel.timeoutSeconds" type="number" min="1" max="120" />
                </label>
                <label class="notify-field">
                  <span>重试次数</span>
                  <input v-model.number="selectedChannel.retryCount" type="number" min="0" max="10" />
                </label>
              </div>
            </section>

            <section v-else class="notify-config-section">
              <h4>投递地址与签名</h4>
              <div class="notify-form-grid two">
                <label class="notify-field notify-field-full">
                  <span>Webhook 地址</span>
                  <div class="notify-input-wrap">
                    <input
                      v-model="selectedChannel.webhookUrl"
                      type="text"
                      :placeholder="selectedChannel.webhookUrlConfigured ? `已配置${selectedChannel.webhookUrlHost ? `：${selectedChannel.webhookUrlHost}` : ''}，留空保留` : 'https://notify.example.com/webhook/xianyu-assistant'"
                    />
                    <button
                      v-if="selectedChannel.webhookUrl"
                      type="button"
                      class="notify-input-action"
                      title="复制当前 Webhook 地址"
                      @click="copyWebhookUrl"
                    >
                      <Icon name="copy" />
                    </button>
                    <button
                      v-if="selectedChannel.webhookUrlConfigured"
                      type="button"
                      class="notify-input-action notify-clear-action"
                      title="清除已保存的 Webhook 地址"
                      @click="clearSecretField(selectedChannel, 'webhookUrl', 'webhookUrlConfigured')"
                    >
                      <Icon name="close" />
                    </button>
                  </div>
                </label>
              </div>

              <div class="notify-form-grid three">
                <label class="notify-field">
                  <span>超时时间（秒）</span>
                  <input v-model.number="selectedChannel.timeoutSeconds" type="number" min="1" max="120" />
                </label>
                <label class="notify-field">
                  <span>重试次数</span>
                  <input v-model.number="selectedChannel.retryCount" type="number" min="0" max="10" />
                </label>
                <label class="notify-field">
                  <span>失败策略</span>
                  <select v-model="selectedChannel.failurePolicy" class="notify-select">
                    <option value="retry">立即重试</option>
                    <option value="queue">稍后队列</option>
                  </select>
                </label>
              </div>

              <div v-if="selectedChannel.type !== 'webhook'" class="notify-form-grid two">
                <label v-if="selectedChannel.type !== 'webhook'" class="notify-field">
                  <span>签名密钥</span>
                  <div class="notify-input-wrap">
                    <input
                      v-model="selectedChannel.secret"
                      type="password"
                      autocomplete="new-password"
                      :placeholder="selectedChannel.secretConfigured ? '已配置，留空保留' : '可选，用于签名验证'"
                    />
                    <button
                      v-if="selectedChannel.secretConfigured"
                      type="button"
                      class="notify-input-action notify-clear-action"
                      title="清除已保存的签名密钥"
                      @click="clearSecretField(selectedChannel, 'secret', 'secretConfigured')"
                    >
                      <Icon name="close" />
                    </button>
                  </div>
                </label>
              </div>
            </section>

            <section class="notify-config-section">
              <div class="notify-section-headline">
                <h4>消息模板</h4>
                <button type="button" class="notify-inline-chip" @click="applyReferenceTemplateVariables">
                  <Icon name="notifyMagic" />
                  插入变量
                </button>
              </div>
              <label class="notify-field">
                <span>通知正文</span>
                <textarea v-model="selectedChannel.template"></textarea>
                <small v-pre>当前支持变量：{{title}}、{{content}}、{{event}}、{{account}}、{{time}}</small>
              </label>
            </section>

            <section class="notify-config-section">
              <h4>发送策略</h4>
              <div class="notify-form-grid two">
                <div class="notify-field">
                  <span>发送模式</span>
                  <div class="notify-segment">
                    <button type="button" :class="{ active: sendMode === 'single' }" @click="sendMode = 'single'">
                      单条即时
                    </button>
                    <button type="button" :class="{ active: sendMode === 'batch' }" @click="sendMode = 'batch'">
                      批量汇总
                    </button>
                  </div>
                </div>

                <div class="notify-field">
                  <span>重复抑制</span>
                  <div class="notify-chip-group">
                    <button
                      type="button"
                      :class="{ active: channelDedupeWindow === 0 }"
                      @click="setChannelDedupeWindow(0)"
                    >
                      关闭
                    </button>
                    <button
                      type="button"
                      :class="{ active: channelDedupeWindow === 10 }"
                      @click="setChannelDedupeWindow(10)"
                    >
                      10 分钟内合并
                    </button>
                  </div>
                </div>
              </div>
            </section>

            <div class="notify-config-actions">
              <span v-if="settingsDirty" class="notify-unsaved-hint">有未保存更改；测试只使用已保存配置，请先保存。</span>
              <AppButton type="primary" :loading="saving" :disabled="testing || loading || settingsAvailable !== true" @click="save">保存当前页面配置</AppButton>
              <AppButton :loading="testing" :disabled="saving || loading || settingsAvailable !== true || settingsDirty || !isChannelConfigured(selectedChannel)" @click="test">发送测试通知</AppButton>
            </div>
          </div>
        </section>

        <section v-if="selectedChannel && selectedChannelTutorial" class="notify-panel notify-tutorial-card">
          <div class="notify-panel-head">
            <div>
              <h3>{{ selectedChannel.name }} 配置教程</h3>
              <p>{{ selectedChannelTutorial.summary }}</p>
            </div>
            <span class="notify-tiny-chip">{{ selectedChannelTutorial.eta }}</span>
          </div>

          <div class="notify-panel-body notify-tutorial-body">
            <div class="notify-tutorial-hero" :class="selectedChannel.accent">
              <div class="notify-tutorial-hero-main">
                <span class="notify-tutorial-icon" :class="selectedChannel.accent">
                  <Icon :name="selectedChannelTutorial.icon || selectedChannel.icon" />
                </span>
                <div class="notify-tutorial-copy">
                  <strong>{{ selectedChannelTutorial.eyebrow }}</strong>
                  <p>{{ selectedChannelTutorial.goal }}</p>
                </div>
              </div>

              <div class="notify-tutorial-prep">
                <span v-for="item in selectedChannelTutorial.preparation" :key="item">
                  {{ item }}
                </span>
              </div>
            </div>

            <div class="notify-tutorial-grid">
              <section class="notify-tutorial-block">
                <div class="notify-tutorial-block-head">
                  <span class="notify-tutorial-head-icon">
                    <Icon name="notifyMagic" />
                  </span>
                  <div>
                    <small>STEP GUIDE</small>
                    <h4>按步骤完成配置</h4>
                  </div>
                </div>

                <div class="notify-tutorial-steps">
                  <article
                    v-for="(step, index) in selectedChannelTutorial.steps"
                    :key="`${step.title}-${index}`"
                    class="notify-tutorial-step"
                  >
                    <span class="notify-step-index">{{ index + 1 }}</span>
                    <div class="notify-step-copy">
                      <strong>{{ step.title }}</strong>
                      <p>{{ step.detail }}</p>
                      <small>{{ step.tip }}</small>
                    </div>
                  </article>
                </div>
              </section>

              <section class="notify-tutorial-block">
                <div class="notify-tutorial-block-head">
                  <span class="notify-tutorial-head-icon">
                    <Icon name="notifySectionBasic" />
                  </span>
                  <div>
                    <small>FIELD GUIDE</small>
                    <h4>页面字段怎么填</h4>
                  </div>
                </div>

                <div class="notify-tutorial-fields">
                  <article
                    v-for="field in selectedChannelTutorial.fields"
                    :key="field.label"
                    class="notify-tutorial-field"
                  >
                    <strong>{{ field.label }}</strong>
                    <p>{{ field.detail }}</p>
                  </article>
                </div>
              </section>
            </div>

            <div class="notify-tutorial-foot">
              <section class="notify-tutorial-block">
                <div class="notify-tutorial-block-head">
                  <span class="notify-tutorial-head-icon">
                    <Icon name="notifyStatus" />
                  </span>
                  <div>
                    <small>CHECK LIST</small>
                    <h4>保存前检查</h4>
                  </div>
                </div>

                <ul class="notify-tutorial-checklist">
                  <li v-for="item in selectedChannelTutorial.checklist" :key="item">
                    <span class="notify-check-dot"></span>
                    <span>{{ item }}</span>
                  </li>
                </ul>
              </section>

              <section class="notify-tutorial-block notify-tutorial-block-highlight">
                <div class="notify-tutorial-block-head">
                  <span class="notify-tutorial-head-icon">
                    <Icon name="notifyAlert" />
                  </span>
                  <div>
                    <small>BEST PRACTICE</small>
                    <h4>推荐操作顺序</h4>
                  </div>
                </div>

                <div class="notify-tutorial-highlight">
                  <p>{{ selectedChannelTutorial.tip }}</p>
                </div>
              </section>
            </div>
          </div>
        </section>
      </div>

      <div class="notify-right-column">
        <section class="notify-panel notify-rules-card">
          <div class="notify-panel-head">
            <div>
              <h3>通知规则矩阵</h3>
              <p>把“触发事件”和“应用内通知”并排展示，减少切换心智。</p>
            </div>
            <span class="notify-tiny-chip">{{ events.length }} 项规则</span>
          </div>

          <div class="notify-rules-headline">
            <span>通知事件</span>
            <span>启用</span>
            <span>应用内通知</span>
          </div>

          <div class="notify-rules-groups">
            <section
              v-for="group in groupedEvents"
              :key="group.key"
              class="notify-rule-group"
            >
              <div class="notify-rule-group-title">
                <span class="notify-group-icon" :class="group.tone">
                  <Icon :name="group.icon" />
                </span>
                <strong>{{ group.label }}</strong>
              </div>

              <div
                v-for="event in group.items"
                :key="event.event"
                class="notify-rule-row"
              >
                <b>{{ event.event }}</b>
                <div class="notify-switch-button">
                  <ToggleSwitch :on="!!event.enabled" :label="`${event.event} 启用`" @click="toggleEvent(event, 'enabled')" />
                </div>
                <div class="notify-switch-button">
                  <ToggleSwitch :on="!!event.app" :label="`${event.event} 应用内通知`" @click="toggleEvent(event, 'app')" />
                </div>
              </div>
            </section>
          </div>

          <div class="notify-rule-actions">
            <button type="button" @click="toggleAllEvents(true)">全选</button>
            <button type="button" @click="toggleAllEvents(false)">清空</button>
          </div>
        </section>

        <section class="notify-panel notify-logs-card">
          <div class="notify-panel-head">
            <div>
              <h3>最近通知日志</h3>
              <p>把发送结果和事件来源压缩成便于快速扫描的列表。</p>
            </div>
          </div>

          <div class="notify-panel-body notify-log-list">
            <EmptyState
              v-if="recentLogRows.length === 0"
              icon="📨"
              title="暂无通知日志"
              :description="logsLoaded ? '发送测试通知或触发业务事件后，实际投递结果会显示在这里。' : '日志服务当前不可用，请稍后重试。'"
            />
            <article
              v-for="log in recentLogRows"
              :key="log.id"
              class="notify-log-row"
            >
              <div class="notify-log-main">
                <span class="notify-log-icon" :class="log.tone">
                  <Icon :name="log.icon" />
                </span>
                <div class="notify-log-copy">
                  <strong>{{ log.title }}</strong>
                  <span>{{ log.detail }}</span>
                  <small>{{ log.time }} · {{ log.meta }}</small>
                </div>
              </div>
              <Badge :type="log.badgeType">{{ log.badge }}</Badge>
            </article>
          </div>
        </section>

        <section class="notify-preview-card">
          <div class="notify-preview-head">
            <strong>应用内通知预览</strong>
            <span class="notify-preview-tag">{{ previewIsExample ? '示例预览' : '实时数据' }}</span>
          </div>

          <div class="notify-preview-phone">
            <div class="notify-phone-top"></div>
            <div class="notify-phone-screen">
              <div class="notify-phone-time">19:00</div>

              <article
                v-for="item in previewMessages"
                :key="item.id"
                class="notify-toast"
                :class="item.tone"
              >
                <span class="notify-toast-icon">
                  <Icon :name="item.icon" />
                </span>
                <div class="notify-toast-copy">
                  <strong>{{ item.title }}</strong>
                  <span>{{ item.content }}</span>
                </div>
              </article>

              <div class="notify-mini-lines">
                <div></div>
                <div style="width: 84%"></div>
                <div style="width: 72%"></div>
              </div>
            </div>
          </div>

          <div class="notify-foot-note">
            <span>{{ previewIsExample ? '当前无实时通知，以下内容仅用于预览样式' : '已展示最近的实际应用内通知' }}</span>
            <span>支持亮色通知样式</span>
          </div>
        </section>
      </div>
    </section>

  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import AppButton from '../../components/AppButton.vue'
import Badge from '../../components/Badge.vue'
import EmptyState from '../../components/EmptyState.vue'
import Icon from '../../components/Icon.vue'
import ToggleSwitch from '../../components/ToggleSwitch.vue'
import {
  getNotificationDeliveryLogs,
  getNotificationSettings,
  getNotifications,
  resolveNotificationTestAttempt,
  saveNotificationSettings,
  testNotification
} from '../../api/notification.js'
import { dateTime, recordsOf } from '../../utils/apiData.js'
import { withBrowserIntentLock } from '../../utils/browserIntentLock.js'

defineProps({ active: String })

const CHANNEL_LIBRARY = [
  {
    key: 'webhook',
    type: 'webhook',
    name: '通用 Webhook',
    icon: 'notifyWebhook',
    accent: 'blue',
    description: '适用于自动化服务、自建中台或通用回调接口。'
  },
  {
    key: 'feishu',
    type: 'feishu',
    name: '飞书机器人',
    icon: 'notifyFeishu',
    accent: 'cyan',
    description: '适合向团队协作群与运营告警群发送通知。'
  },
  {
    key: 'feishu_app',
    type: 'feishu_app',
    name: '飞书自建应用（双向对话）',
    icon: 'notifyFeishu',
    accent: 'cyan',
    description: '支持双向对话、账号状态查询与 Session 过期通知。飞书二维码自动登录当前不可用，请在 Web 账号管理页使用“重新扫码”。需在飞书开放平台创建自建应用。'
  },
  {
    key: 'dingtalk',
    type: 'dingtalk',
    name: '钉钉机器人',
    icon: 'notifyDingtalk',
    accent: 'indigo',
    description: '订单履约与异常提醒可同步到钉钉工作群。'
  },
  {
    key: 'wechat_work',
    type: 'wechat_work',
    name: '企业微信机器人',
    icon: 'notifyWechat',
    accent: 'green',
    description: '适合账号状态播报、库存预警与经营日报。'
  },
  {
    key: 'pushplus',
    type: 'pushplus',
    name: '短信通知',
    icon: 'notifySms',
    accent: 'purple',
    description: '适合高优先级告警与客诉通知，确保及时触达。'
  },
  {
    key: 'email',
    type: 'email',
    name: '邮件 SMTP',
    icon: 'notifyEmail',
    accent: 'purple',
    description: '适合管理周报、汇总报表与慢时效提醒。'
  }
]

const CHANNEL_TUTORIALS = {
  webhook: {
    icon: 'notifyWebhook',
    eyebrow: '推荐给自动化服务与自建中台',
    summary: '给当前通知渠道补一份一步一步的接入说明，保存前就能知道该填什么、先测什么。',
    goal: '适合把通知转发给自建接口、工作流服务或任何支持 HTTP 回调的系统。',
    eta: '约 2 分钟完成',
    preparation: ['准备一个可访问的 https 地址', '接收端支持 POST JSON', '建议先用测试环境验证'],
    steps: [
      {
        title: '先准备接收接口',
        detail: '在你的服务里创建一个接收通知的 POST 接口，确保收到请求后可以返回 200。',
        tip: '如果先只做回显也没关系，先把链路打通最重要。'
      },
      {
        title: '把完整地址填到 Webhook 地址',
        detail: '直接粘贴完整的 https 链接，不要填 localhost、内网 IP 或需要登录才能访问的地址。',
        tip: '推荐保留固定路径，例如 /webhook/xianyu-assistant，后续排查更方便。'
      },
      {
        title: '按稳定性设置超时、重试和失败策略',
        detail: '接口响应快就保持默认；如果你的回调会串联更多服务，可以适当增加超时和重试次数。',
        tip: '不确定怎么配时，先用 10 秒超时、3 次重试。'
      },
      {
        title: '保存后立即发送测试通知',
        detail: '保存配置后点测试按钮，确认你的服务已经成功收到并处理消息，再上线正式事件。',
        tip: '测试通过后，再逐步打开更多通知事件。'
      }
    ],
    fields: [
      { label: 'Webhook 地址', detail: '必须是公网 HTTPS 地址；localhost、内网 IP、重定向和含账号密码的链接会被拒绝。' },
      { label: '超时时间', detail: '表示等待接口响应的最长时间；你的接口越慢，这里越要留足余量。' },
      { label: '重试次数', detail: '接口偶发超时或抖动时会自动补发，接口已做幂等时可以保持默认值。' },
      { label: '消息模板', detail: '建议先用简洁文本模板，确认字段都能收到后，再扩展成结构化内容。' }
    ],
    checklist: [
      '接收接口已经能返回 200，不会因为鉴权或路由缺失直接报错。',
      'Webhook 地址是可由服务端访问的公网 HTTPS 地址，不是浏览器页面地址或内网地址。',
      '测试通知收到了标题、正文和事件信息，消息内容没有出现空字段。'
    ],
    tip: '最稳的顺序是：先填地址，再保存一次，然后发测试通知，最后再去细调模板、去重与失败策略。'
  },
  feishu: {
    icon: 'notifyFeishu',
    eyebrow: '适合团队群通知协作',
    summary: '飞书机器人的接入重点就是先在群里创建机器人，再把地址和签名信息一并带回来。',
    goal: '适合运营群、履约群和告警群，能让问题和订单状态第一时间同步给团队。',
    eta: '约 1 分钟完成',
    preparation: ['先进入目标飞书群', '确认有添加机器人的权限', '如启用签名请一并保存密钥'],
    steps: [
      {
        title: '在飞书群里添加自定义机器人',
        detail: '进入群设置，创建机器人并复制官方生成的 Webhook 地址。',
        tip: '建议机器人名称和这里的渠道名称保持一致，后续排查更直观。'
      },
      {
        title: '如果开了安全设置，同时复制签名密钥',
        detail: '飞书如果启用了签名校验，需要把 secret 一起带回来，否则测试时会直接失败。',
        tip: '地址和签名是一对，少一个都可能导致 403。'
      },
      {
        title: '回到页面填写地址、签名和发送参数',
        detail: '先把 Webhook 地址和签名密钥填好，再根据群消息量决定超时和重试配置。',
        tip: '消息很多的群建议保留去重窗口，避免短时间刷屏。'
      },
      {
        title: '保存并发送测试消息到群里',
        detail: '确认群里实际收到了测试通知，再决定是否开启更多业务事件。',
        tip: '如果没收到，优先检查机器人是否被群管理员禁用。'
      }
    ],
    fields: [
      { label: 'Webhook 地址', detail: '使用飞书后台生成的机器人地址，建议整段复制，避免丢失 token 部分。' },
      { label: '签名密钥', detail: '只有在机器人开启签名校验时才需要填写；没开可以先留空。' },
      { label: '消息模板', detail: '先用纯文本模板验证链路，稳定后再加入变量和更详细的提示内容。' },
      { label: '重复抑制', detail: '运营群建议开启 10 分钟合并，防止短时间重复触发同类提醒。' }
    ],
    checklist: [
      '机器人已经成功加入目标群，群里没有关键词或管理员限制导致消息被拦截。',
      '如果开启了签名，当前页面填写的 secret 与飞书后台展示的一致。',
      '测试消息已经在目标群出现，且通知内容能看懂、不刷屏。'
    ],
    tip: '推荐先只开一类高价值提醒，比如新订单或异常提醒，确认团队接收节奏没问题后再逐步扩充。'
  },
  dingtalk: {
    icon: 'notifyDingtalk',
    eyebrow: '适合工作群中的流程提醒',
    summary: '钉钉机器人的关键在于安全设置，地址、加签和关键词通常要成对检查。',
    goal: '适合把订单履约、异常告警和代发提醒同步到钉钉工作群。',
    eta: '约 1 分钟完成',
    preparation: ['先在钉钉群创建机器人', '确认安全设置方式', '准备 access_token 和 secret'],
    steps: [
      {
        title: '创建钉钉群机器人并复制 Webhook',
        detail: '在群机器人设置里创建自定义机器人，复制完整地址，确保 access_token 没有缺失。',
        tip: '建议直接用复制按钮，手动复制最容易漏掉参数。'
      },
      {
        title: '根据安全方式补充签名或关键词',
        detail: '如果机器人启用了加签，把 secret 填到签名密钥；如果用了关键词，模板正文里要包含关键词。',
        tip: '关键词校验没通过时，消息通常不会入群。'
      },
      {
        title: '在页面中配置超时、重试和模板',
        detail: '钉钉群消息通常响应较快，默认超时和重试已经够用，重点是模板要清晰。',
        tip: '建议把事件名、账号名和时间都保留在模板里。'
      },
      {
        title: '保存后用测试消息校验安全设置',
        detail: '如果测试失败，优先回到钉钉后台检查安全设置是否与当前页面完全一致。',
        tip: '大多数接入失败都不是地址错，而是安全校验没对上。'
      }
    ],
    fields: [
      { label: 'Webhook 地址', detail: '使用机器人后台生成的完整地址，地址里通常已经包含 access_token。' },
      { label: '签名密钥', detail: '只有开启“加签”时才填写；如果开启的是关键词或 IP 白名单，请同步满足对应规则。' },
      { label: '失败策略', detail: '建议保留“立即重试”，偶发网络抖动时成功率会更高。' },
      { label: '消息模板', detail: '记得让模板包含群里要求的关键词，否则钉钉可能直接拒绝投递。' }
    ],
    checklist: [
      '钉钉群机器人没有被停用，安全设置与当前填写内容完全匹配。',
      '如果使用关键词校验，测试通知正文里已经包含关键词。',
      '群里已经收到测试消息，内容没有被裁剪成看不懂的一行。'
    ],
    tip: '最省事的排查顺序是：先看机器人后台安全设置，再核对地址和 secret，最后才去改模板内容。'
  },
  wechat_work: {
    icon: 'notifyWechat',
    eyebrow: '适合稳定的企业群通知',
    summary: '企业微信机器人的配置最简单，通常只需要机器人地址，再根据群规则决定是否收敛消息频次。',
    goal: '适合库存预警、账号状态播报和每日汇总，强调稳定送达与可读性。',
    eta: '约 1 分钟完成',
    preparation: ['先在企业微信群添加机器人', '复制机器人 Webhook 地址', '明确通知接收群'],
    steps: [
      {
        title: '在企业微信群生成机器人地址',
        detail: '进入群机器人配置页，复制企业微信提供的完整 Webhook 地址。',
        tip: '建议先找一个测试群验证，确认效果后再切到正式群。'
      },
      {
        title: '把地址填进页面并保留默认发送参数',
        detail: '企业微信对基础文本消息兼容很好，先填地址并沿用默认超时、重试即可。',
        tip: '如果没有特别需求，不必一开始就把参数改得很复杂。'
      },
      {
        title: '梳理模板中的关键信息顺序',
        detail: '把事件、账号、时间放在前两行，群成员扫一眼就能看懂，不需要长段文字。',
        tip: '企业微信群消息通常偏碎片化，短句更有效。'
      },
      {
        title: '保存后发送测试，再决定是否开启合并策略',
        detail: '当群里消息很多时，再开启 10 分钟合并，避免同一类事件短时间刷屏。',
        tip: '先确认送达，再控制频率，效率最高。'
      }
    ],
    fields: [
      { label: 'Webhook 地址', detail: '企业微信机器人地址一般带有 key 参数，复制时不要截断。' },
      { label: '超时与重试', detail: '如果网络环境稳定可以保持默认；跨区网络时可适当提高超时。' },
      { label: '消息模板', detail: '建议控制在几行内，标题突出事件，正文补充账号与时间即可。' },
      { label: '重复抑制', detail: '群消息多时建议开启合并；如果是强提醒类告警，可以暂时关闭。' }
    ],
    checklist: [
      '机器人已经加入正确的企业微信群，不会把正式通知发到测试群。',
      '测试消息可以正常显示标题、正文和时间，没有出现空白或乱码。',
      '群消息频率可接受，必要时已开启 10 分钟内合并。'
    ],
    tip: '推荐先把模板收敛成“标题 + 一行说明 + 时间”三段式，团队最容易快速阅读。'
  },
  pushplus: {
    icon: 'notifySms',
    eyebrow: '适合个人强提醒和高优先级触达',
    summary: '当前页面里短信通知走的是 PushPlus 方式，最核心的就是拿到可用 Token 并确认对应账号已激活。',
    goal: '适合高优先级告警、客诉通知或需要第一时间推送到个人设备的消息。',
    eta: '约 1 分钟完成',
    preparation: ['先登录 PushPlus', '复制个人 Token', '确认接收端已绑定公众号或客户端'],
    steps: [
      {
        title: '先到 PushPlus 控制台复制 Token',
        detail: '登录后进入个人中心或发送配置页，复制当前账号的 Token，作为这个渠道的唯一凭证。',
        tip: '如果 Token 复制错账号，通知会发到别人的接收端。'
      },
      {
        title: '把 Token 填到 PushPlus Token 字段',
        detail: '当前页面把 Token 放在接收凭证字段里，直接整段粘贴即可，不需要额外加前缀。',
        tip: 'Token 一般较长，粘贴后可先保存一次，避免刷新丢失。'
      },
      {
        title: '按告警级别设置模板和重试',
        detail: '强提醒类通知建议模板更短、更直接，保留事件、账号和时间就够用了。',
        tip: '如果担心重复打扰，记得配合重复抑制一起使用。'
      },
      {
        title: '保存并发送测试，确认手机端真的收到',
        detail: '不只看页面成功提示，要去实际接收端确认消息能收到、能打开、能看懂。',
        tip: '这是 PushPlus 最容易被忽略的一步。'
      }
    ],
    fields: [
      { label: 'PushPlus Token', detail: '这是唯一必填项，决定消息最终推送到哪个账号或设备。' },
      { label: '超时时间', detail: '通常保持默认即可，只有在网络较慢或代理转发时才需要调大。' },
      { label: '重试次数', detail: '用于覆盖偶发网络抖动；高优先级消息建议保留默认重试。' },
      { label: '消息模板', detail: '推荐用短句模板，方便在移动端通知栏直接看清重点。' }
    ],
    checklist: [
      'Token 对应的 PushPlus 账号已经正常激活，并且接收端可用。',
      '测试通知已经真实出现在手机或接收设备上，而不是只有页面显示成功。',
      '模板足够简短，移动端一屏内就能看到核心信息。'
    ],
    tip: '先确认 Token 和接收端没问题，再去细调模板文案；否则很容易把问题误判成格式问题。'
  },
  email: {
    icon: 'notifyEmail',
    eyebrow: '适合日报、周报和汇总类通知',
    summary: 'SMTP 渠道的关键是邮箱服务商的授权方式，通常要用授权码而不是登录密码。',
    goal: '适合发送慢时效通知、日报周报、汇总报表，以及需要留档的提醒。',
    eta: '约 2 分钟完成',
    preparation: ['确认邮箱已开启 SMTP', '准备服务器地址与端口', '获取授权码或应用专用密码'],
    steps: [
      {
        title: '先在邮箱后台开启 SMTP 服务',
        detail: '常见邮箱都需要在网页后台手动开启 SMTP，并生成授权码或应用专用密码。',
        tip: '如果你直接用登录密码，很多邮箱都会报鉴权失败。'
      },
      {
        title: '填写服务器地址、端口和登录账号',
        detail: '把 SMTP Host、端口和登录账号按邮箱服务商要求填写完整，端口常见为 465 或 587。',
        tip: '端口错了通常会直接连接失败。'
      },
      {
        title: '填写授权码、发件人和收件人',
        detail: '授权码用于登录 SMTP；发件人一般与登录账号一致，收件人填写实际接收邮箱。',
        tip: '先只填一个收件人最容易验证成功。'
      },
      {
        title: '保存后发送测试邮件',
        detail: '到收件箱、垃圾箱都看一下，确认邮件真的送达，标题和正文也符合预期。',
        tip: '邮箱渠道更适合结构完整、可留档的通知。'
      }
    ],
    fields: [
      { label: 'SMTP Host', detail: '填写邮箱服务商提供的服务器地址，例如 smtp.example.com。' },
      { label: '端口', detail: '常见端口为 465 或 587，按邮箱服务商文档填写，错误端口会导致连接失败。' },
      { label: '登录账号 / 授权码', detail: '登录账号一般是完整邮箱地址；密码推荐使用授权码而不是网页登录密码。' },
      { label: '发件人 / 收件人', detail: '发件人建议与登录账号一致，收件人先填自己，测试通过后再扩展到正式收件组。' }
    ],
    checklist: [
      '邮箱后台已经开启 SMTP，当前填写的是授权码或应用密码，不是普通登录密码。',
      '端口与 Host 对应正确，测试邮件在收件箱或垃圾箱中都能找到。',
      '邮件标题、正文和变量内容完整，适合作为正式通知模板继续使用。'
    ],
    tip: '邮箱配置建议一步一步来：先确认 SMTP 能登录，再确认能送达，最后再优化模板排版和收件范围。'
  }
}

const EVENT_GROUPS = [
  {
    key: 'order',
    label: '履约与订单',
    icon: 'notifyOrder',
    tone: '',
    events: ['激活提醒', '新订单提醒', '自动发货成功', '自动发货失败', '代发货提醒']
  },
  {
    key: 'risk',
    label: '账号与风控',
    icon: 'notifyRisk',
    tone: 'amber',
    events: ['账号掉线', 'Cookie 到期', '人机验证', '人机验证成功']
  },
  {
    key: 'ops',
    label: '经营与汇总',
    icon: 'notifyReport',
    tone: 'green',
    events: ['库存预警', '整点报表', '应用内通知']
  }
]

const EXAMPLE_PREVIEW = [
  {
    id: 'preview-1',
    title: '自动化任务提醒',
    content: '这是通知样式示例，真实任务执行结果会使用后端返回的内容。',
    tone: 'info',
    icon: 'notifyPreview'
  },
  {
    id: 'preview-2',
    title: '库存预警提醒',
    content: '这是预警样式示例，不代表当前存在真实库存异常。',
    tone: 'warn',
    icon: 'notifyAlert'
  }
]

const channels = ref(normalizeChannels([]))
const events = ref(normalizeEvents([]))
const notifications = ref([])
const deliveryLogs = ref([])
const selectedKey = ref('webhook')
const sendMode = ref('single')
const inAppEnabled = ref(false)
const error = ref('')
const success = ref('')
const saving = ref(false)
const testing = ref(false)
const resolving = ref(false)
const loading = ref(false)
const settingsAvailable = ref(null)
const baselineSettings = ref('')
const logsLoaded = ref(false)
const notificationsLoaded = ref(false)

const NOTIFICATION_TEST_INTENT_PREFIX = 'xya.notification-test-intent:v1:'
const NOTIFICATION_TEST_KEY_PATTERN = /^[A-Za-z0-9_.:-]{16,128}$/
const testAttempt = reactive({
  visible: false,
  status: '',
  attemptId: null,
  channelKey: '',
  retrySafe: false,
  replaySafe: false,
  logPersisted: false,
  success: null,
  message: '',
})

const selectedChannel = computed(() =>
  channels.value.find(channel => channel.key === selectedKey.value) || channels.value[0] || null
)

const selectedChannelTutorial = computed(() => {
  const channel = selectedChannel.value
  if (!channel) return null
  return CHANNEL_TUTORIALS[channel.type] || CHANNEL_TUTORIALS[channel.key] || CHANNEL_TUTORIALS.webhook
})

const healthPercent = computed(() => {
  if (!deliveryLogs.value.length) return 0
  const okCount = deliveryLogs.value.filter(log => toBool(log.success, false)).length
  return Math.max(0, Math.min(100, Number(((okCount / deliveryLogs.value.length) * 100).toFixed(1))))
})

const heroHealthLabel = computed(() => {
  if (!logsLoaded.value) return '状态不可用'
  if (!deliveryLogs.value.length) return '暂无投递记录'
  return `${healthPercent.value.toFixed(1)}%`
})

const connectedChannelCount = computed(() =>
  channels.value.filter(channel => channel.enabled && isChannelConfigured(channel)).length
)

const enabledEventCount = computed(() => events.value.filter(event => event.enabled).length)
const settingsDirty = computed(() => (
  settingsAvailable.value === true && JSON.stringify(buildSettingsPayload()) !== baselineSettings.value
))

const pendingCount = computed(() => {
  const failures = deliveryLogs.value.filter(log => !toBool(log.success, false)).length
  const unread = notifications.value.filter(item => !toBool(item.readFlag, true)).length
  return failures + unread
})

const averageLatencyMs = computed(() => {
  if (!deliveryLogs.value.length) return 0
  const values = deliveryLogs.value
    .map(log => Number(log.costMs || 0))
    .filter(value => Number.isFinite(value) && value > 0)
  if (!values.length) return 0
  return Math.round(values.reduce((sum, value) => sum + value, 0) / values.length)
})

const averageLatencyDisplay = computed(() => {
  const value = averageLatencyMs.value
  if (!value) return '--'
  if (value >= 1000) return `${(value / 1000).toFixed(1)}s`
  return `${value}ms`
})

function isToday(value) {
  if (!value) return false
  const date = new Date(value)
  const now = new Date()
  return !Number.isNaN(date.getTime()) && date.toDateString() === now.toDateString()
}

const visualTodayCount = computed(() => notifications.value.filter(item => isToday(item.createdTime || item.time)).length)
const visualPendingCount = computed(() => pendingCount.value)
const visualTodayDisplay = computed(() => notificationsLoaded.value ? String(visualTodayCount.value) : '—')
const visualPendingDisplay = computed(() => (
  logsLoaded.value || notificationsLoaded.value ? String(visualPendingCount.value) : '—'
))

const summaryCards = computed(() => [
  {
    label: '已启用渠道',
    value: settingsAvailable.value === true ? String(connectedChannelCount.value) : '—',
    detail: settingsAvailable.value === true ? (connectedChannelCount.value ? '已启用且完成必要配置' : '暂无已连接渠道') : '通知配置暂不可用',
    icon: 'notifyChannel',
    tone: 'blue'
  },
  {
    label: '已开启事件',
    value: settingsAvailable.value === true ? String(enabledEventCount.value) : '—',
    detail: settingsAvailable.value === true ? (enabledEventCount.value ? '当前已开启的实际触发规则' : '暂无已开启规则') : '通知配置暂不可用',
    icon: 'notifyEvent',
    tone: 'green'
  },
  {
    label: '今日通知数',
    value: visualTodayDisplay.value,
    detail: notificationsLoaded.value ? '基于当前加载的应用内通知' : '通知数据暂不可用',
    icon: 'notifyClock',
    tone: 'indigo'
  },
  {
    label: '发送成功率',
    value: logsLoaded.value && deliveryLogs.value.length ? `${healthPercent.value.toFixed(1)}%` : '—',
    detail: logsLoaded.value
      ? (deliveryLogs.value.length ? `基于最近 ${deliveryLogs.value.length} 条实际投递记录` : '暂无投递记录，无法计算成功率')
      : '投递日志暂不可用',
    icon: 'notifyRate',
    tone: 'cyan'
  },
  {
    label: '待处理提醒',
    value: visualPendingDisplay.value,
    detail: logsLoaded.value || notificationsLoaded.value
      ? (pendingCount.value ? '基于已加载的失败投递与未读应用内通知' : '已加载来源中无待处理提醒')
      : '投递日志与应用内通知暂不可用',
    icon: 'notifyAlert',
    tone: 'orange'
  }
])

const groupedEvents = computed(() => {
  const assigned = new Set()
  const groups = EVENT_GROUPS.map(group => {
    const items = events.value.filter(event => group.events.includes(event.event))
    items.forEach(event => assigned.add(event.event))
    return { ...group, items }
  }).filter(group => group.items.length)

  const extras = events.value.filter(event => !assigned.has(event.event))
  if (extras.length) {
    groups.push({
      key: 'extra',
      label: '其他提醒',
      icon: 'notifyMagic',
      items: extras
    })
  }
  return groups
})

const recentLogRows = computed(() => {
  if (!logsLoaded.value || !deliveryLogs.value.length) return []
  return deliveryLogs.value.slice(0, 3).map(log => {
    const ok = toBool(log.success, false)
    return {
      id: log.id || `${log.channelKey}-${log.createdTime}`,
      title: `${log.channelName || renderChannelName(log.channelKey)} · ${log.eventType || '测试通知'}`,
      detail: `${dateTime(log.createdTime)} · 响应 ${formatCost(log.costMs)}`,
      time: dateTime(log.createdTime),
      meta: log.message || (ok ? '发送成功' : '发送失败'),
      tone: ok ? 'green' : 'orange',
      icon: resolveLogIcon(log),
      badge: ok ? '成功' : '失败',
      badgeType: ok ? '' : 'orange'
    }
  })
})

const previewMessages = computed(() => {
  if (!notifications.value.length) return EXAMPLE_PREVIEW
  return notifications.value.slice(0, 2).map(item => ({
    id: item.id || `${item.type}-${item.createdTime}`,
    title: item.title || '应用内通知',
    content: item.content || '你有一条新的通知，请前往消息中心查看。',
    tone: Number(item.priority || 0) >= 2 ? 'warn' : 'info',
    icon: Number(item.priority || 0) >= 2 ? 'notifyAlert' : 'notifyPreview'
  }))
})
const previewIsExample = computed(() => !notificationsLoaded.value || notifications.value.length === 0)

const selectedChannelStatus = computed(() => {
  const channel = selectedChannel.value
  if (!channel) {
    return {
      title: '未选择通知渠道',
      description: '请先在左侧选择要编辑的通知渠道。'
    }
  }

  if (!isChannelConfigured(channel)) {
    return {
      title: `${channel.name} 尚未完成连接配置`,
      description: '填写通道地址、签名密钥或邮件凭证后即可保存并发起测试发送。'
    }
  }

  const latestLog = deliveryLogs.value.find(log => String(log.channelKey || '') === String(channel.key))
  if (!latestLog) {
    return {
      title: `${renderConnectionTarget(channel)} 配置已填写`,
      description: '尚无该渠道的实际投递记录，请保存后发送测试通知进行验证。'
    }
  }
  const ok = toBool(latestLog.success, false)
  return {
    title: ok ? `${renderConnectionTarget(channel)} 最近投递成功` : `${renderConnectionTarget(channel)} 最近投递失败`,
    description: `${dateTime(latestLog.createdTime)} · 响应 ${formatCost(latestLog.costMs)}${latestLog.message ? ` · ${latestLog.message}` : ''}`
  }
})

const channelDedupeWindow = computed(() => Number(selectedChannel.value?.dedupeWindow || 0))

// 飞书自建应用事件订阅 URL 提示用：当前页面 origin（自动化服务监听同源 /api/feishu/webhook）
const windowLocationOrigin = computed(() => {
  if (typeof window !== 'undefined' && window.location) {
    return window.location.origin
  }
  return 'http://your-server-host:12401'
})

function createChannelSeed(meta, overrides = {}) {
  return {
    key: meta.key,
    type: meta.type,
    name: meta.name,
    enabled: false,
    method: 'POST',
    contentType: 'application/json',
    webhookUrl: '',
    webhookUrlConfigured: false,
    webhookUrlHost: '',
    receiver: '',
    secret: '',
    secretConfigured: false,
    timeoutSeconds: 10,
    retryCount: 3,
    template: '【{title}】\n{{content}}\n\n事件：{event}\n账号：{account}\n时间：{time}\n\n来自 XianYuAssistant 通知中心',
    failurePolicy: 'retry',
    dedupeWindow: 10,
    smtpHost: '',
    smtpPort: 465,
    smtpUser: '',
    smtpPass: '',
    smtpPassConfigured: false,
    fromEmail: '',
    // 飞书自建应用专属字段
    appId: '',
    verificationToken: '',
    verificationTokenConfigured: false,
    encryptKey: '',
    encryptKeyConfigured: false,
    receiveId: '',
    receiveIdType: 'open_id',
    // 清除标记：用户点击清除按钮后置为 true，提交后由后端清空对应密钥
    clearWebhookUrl: false,
    clearSecret: false,
    clearSmtpPass: false,
    clearVerificationToken: false,
    clearEncryptKey: false,
    icon: meta.icon,
    accent: meta.accent,
    ...overrides
  }
}

function normalizeChannels(raw) {
  const list = Array.isArray(raw) ? raw : []
  const map = new Map(list.map(item => [String(item?.key || item?.type || item?.name || Math.random()), item]))
  const base = CHANNEL_LIBRARY.map(meta => {
    const merged = createChannelSeed(meta, map.get(meta.key) || {})
    return normalizeChannelRecord(merged, meta)
  })

  const extras = list
    .filter(item => item && !CHANNEL_LIBRARY.some(meta => meta.key === String(item.key)))
    .map((item, index) =>
      normalizeChannelRecord(
        createChannelSeed(
          {
            key: String(item.key || `custom-${index + 1}`),
            type: String(item.type || 'webhook'),
            name: String(item.name || `自定义渠道 ${index + 1}`),
            icon: 'notifyWebhook',
            accent: 'blue',
            description: '自定义扩展通知渠道。'
          },
          item
        )
      )
    )

  return [...base, ...extras]
}

function normalizeChannelRecord(item, meta) {
  const fallbackMeta = meta || CHANNEL_LIBRARY.find(entry => entry.key === item.key) || {
    icon: 'notifyWebhook',
    accent: 'blue'
  }

  const rawWebhookUrl = String(item.webhookUrl || '')
  const rawSecret = String(item.secret || '')
  const rawSmtpPass = String(item.smtpPass || '')
  const rawVerificationToken = String(item.verificationToken || item.verification_token || '')
  const rawEncryptKey = String(item.encryptKey || item.encrypt_key || '')
  return {
    ...item,
    key: String(item.key || fallbackMeta.key || `custom-${Date.now()}`),
    type: String(item.type || fallbackMeta.type || 'webhook'),
    name: String(item.name || fallbackMeta.name || '自定义渠道'),
    webhookUrl: '',
    webhookUrlConfigured: toBool(item.webhookUrlConfigured, /^https?:\/\//i.test(rawWebhookUrl)),
    webhookUrlHost: String(item.webhookUrlHost || extractHost(rawWebhookUrl) || ''),
    secret: '',
    secretConfigured: toBool(item.secretConfigured, Boolean(rawSecret)),
    smtpPass: '',
    smtpPassConfigured: toBool(item.smtpPassConfigured, Boolean(rawSmtpPass)),
    verificationToken: '',
    verificationTokenConfigured: toBool(item.verificationTokenConfigured, Boolean(rawVerificationToken)),
    encryptKey: '',
    encryptKeyConfigured: toBool(item.encryptKeyConfigured, Boolean(rawEncryptKey)),
    enabled: toBool(item.enabled, false),
    method: String(item.method || 'POST').toUpperCase() === 'GET' ? 'GET' : 'POST',
    timeoutSeconds: toNumber(item.timeoutSeconds, 10),
    retryCount: toNumber(item.retryCount, 3),
    template: normalizeChannelTemplate(item.template),
    failurePolicy: String(item.failurePolicy || 'retry'),
    dedupeWindow: toNumber(item.dedupeWindow, 10),
    smtpPort: toNumber(item.smtpPort, 465),
    // 飞书自建应用专属字段
    appId: String(item.appId || item.app_id || ''),
    receiveId: String(item.receiveId || item.receive_id || ''),
    receiveIdType: String(item.receiveIdType || item.receive_id_type || 'open_id'),
    // 清除标记：加载时重置为 false
    clearWebhookUrl: false,
    clearSecret: false,
    clearSmtpPass: false,
    clearVerificationToken: false,
    clearEncryptKey: false,
    icon: fallbackMeta.icon,
    accent: fallbackMeta.accent
  }
}

function normalizeEvents(raw) {
  const list = Array.isArray(raw) ? raw : []
  const allNames = EVENT_GROUPS.flatMap(group => group.events)
  const map = new Map(list.map(item => [String(item?.event || ''), item]))
  const normalized = allNames.map(name => {
    const source = map.get(name) || {}
    return {
      event: name,
      enabled: toBool(source.enabled, false),
      app: toBool(source.app, false)
    }
  })

  const extras = list
    .filter(item => item?.event && !allNames.includes(String(item.event)))
    .map(item => ({
      event: String(item.event),
      enabled: toBool(item.enabled, false),
      app: toBool(item.app, false)
    }))

  return [...normalized, ...extras]
}

function renderChannelDescription(channel) {
  return CHANNEL_LIBRARY.find(item => item.key === channel?.key)?.description
    || CHANNEL_LIBRARY.find(item => item.type === channel?.type)?.description
    || '用于承接系统事件、业务提醒与应用内消息同步。'
}

function renderChannelName(key) {
  return channels.value.find(channel => channel.key === key)?.name
    || CHANNEL_LIBRARY.find(channel => channel.key === key)?.name
    || '通知渠道'
}

function toBool(value, fallback = false) {
  if (typeof value === 'boolean') return value
  if (typeof value === 'number') return value === 1
  if (typeof value === 'string') return ['1', 'true', 'yes', 'on'].includes(value.toLowerCase())
  return fallback
}

function toNumber(value, fallback = 0) {
  const next = Number(value)
  return Number.isFinite(next) ? next : fallback
}

function isChannelConfigured(channel) {
  if (!channel) return false
  if (channel.type === 'email') {
    return Boolean(channel.smtpHost && channel.smtpUser && (channel.smtpPass || channel.smtpPassConfigured) && channel.receiver)
  }
  if (channel.type === 'pushplus') {
    return Boolean(channel.receiver)
  }
  if (channel.type === 'feishu_app') {
    return Boolean(
      channel.appId
      && (channel.secret || channel.secretConfigured)
      && (channel.verificationToken || channel.verificationTokenConfigured)
      && channel.receiveId
    )
  }
  return /^https:\/\//i.test(String(channel.webhookUrl || '')) || (Boolean(channel.webhookUrlConfigured) && channel.webhookUrlSecure !== false)
}

function channelStatusText(channel) {
  if (!channel.enabled) return '未启用'
  return isChannelConfigured(channel) ? '已启用' : '待完成配置'
}

function renderConnectionTarget(channel) {
  if (!channel) return '通知中心'
  if (channel.type === 'email') return channel.smtpHost || 'SMTP'
  if (channel.type === 'pushplus') return maskValue(channel.receiver, 6, 4) || '短信网关'
  if (channel.type === 'feishu_app') return channel.appId ? `AppID: ${channel.appId.slice(0, 12)}...` : '飞书自建应用'
  return extractHost(channel.webhookUrl) || channel.webhookUrlHost || channel.name
}

function extractHost(value) {
  try {
    return new URL(String(value)).host
  } catch {
    return ''
  }
}

function maskValue(value, start = 4, end = 4) {
  const text = String(value || '')
  if (!text) return ''
  if (text.length <= start + end) return text
  return `${text.slice(0, start)}...${text.slice(-end)}`
}

function normalizeChannelTemplate(value) {
  const text = String(value || '').trim()
  if (!text) {
    return '【{{title}}】 {{content}}\n事件：{{event}} 账号：{{account}} 时间：{{time}}\n来自 XianYuAssistant 通知中心'
  }
  return text
}

function applyReferenceTemplateVariables() {
  if (!selectedChannel.value) return
  const text = String(selectedChannel.value.template || '').trim()
  if (text.includes('{{event}}') && text.includes('{{account}}') && text.includes('{{time}}')) {
    selectedChannel.value.template = normalizeChannelTemplate(text)
    return
  }
  if (!text) {
    selectedChannel.value.template = normalizeChannelTemplate(text)
    return
  }
  selectedChannel.value.template = `${text}\n事件：{{event}} 账号：{{account}} 时间：{{time}}`.trim()
}

function formatCost(value) {
  const ms = Number(value || 0)
  if (!Number.isFinite(ms) || ms <= 0) return '--'
  if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`
  return `${ms}ms`
}

function resolveLogIcon(log) {
  if (!toBool(log.success, false)) return 'notifyAlert'
  const match = CHANNEL_LIBRARY.find(channel => channel.key === log.channelKey)
  return match?.icon || 'notifyLog'
}

function setChannelDedupeWindow(value) {
  if (settingsAvailable.value !== true) return
  if (!selectedChannel.value) return
  selectedChannel.value.dedupeWindow = value
}

function clearNotices() {
  error.value = ''
  success.value = ''
}

function notificationTestStorageKey(channelKey) {
  return `${NOTIFICATION_TEST_INTENT_PREFIX}${channelKey}`
}

function notificationTestPayload(channel = selectedChannel.value) {
  return {
    channelKey: String(channel?.key || ''),
    title: '通知渠道测试',
    content: `来自 ${channel?.name || '通知中心'} 的连接测试`,
  }
}

function clearTestAttemptView() {
  Object.assign(testAttempt, {
    visible: false,
    status: '',
    attemptId: null,
    channelKey: '',
    retrySafe: false,
    replaySafe: false,
    logPersisted: false,
    success: null,
    message: '',
  })
}

function applyPersistedTestAttempt(record) {
  Object.assign(testAttempt, {
    visible: true,
    status: String(record?.status || 'unknown'),
    attemptId: Number(record?.attemptId || 0) || null,
    channelKey: String(record?.channelKey || ''),
    retrySafe: record?.retrySafe === true,
    replaySafe: record?.replaySafe !== false,
    logPersisted: record?.logPersisted === true,
    success: typeof record?.success === 'boolean' ? record.success : null,
    message: String(record?.message || '该通知测试仍需使用原意图核对。'),
  })
}

function loadNotificationTestIntent(channelKey = selectedKey.value) {
  clearTestAttemptView()
  const normalizedChannelKey = String(channelKey || '')
  if (!normalizedChannelKey) return null
  try {
    const record = JSON.parse(
      window.localStorage.getItem(notificationTestStorageKey(normalizedChannelKey)) || 'null',
    )
    if (
      !record
      || record.channelKey !== normalizedChannelKey
      || !NOTIFICATION_TEST_KEY_PATTERN.test(String(record.idempotencyKey || ''))
    ) return null
    applyPersistedTestAttempt(record)
    return record
  } catch {
    error.value = '浏览器无法读取通知测试意图；为避免重复发送，测试功能已禁用。'
    return null
  }
}

function persistNotificationTestIntent(payload) {
  const storageKey = notificationTestStorageKey(payload.channelKey)
  const existingRaw = window.localStorage.getItem(storageKey)
  if (existingRaw) {
    const existing = JSON.parse(existingRaw)
    if (
      !NOTIFICATION_TEST_KEY_PATTERN.test(String(existing?.idempotencyKey || ''))
      || existing?.channelKey !== payload.channelKey
      || existing?.title !== payload.title
      || existing?.content !== payload.content
    ) {
      throw new Error('已有通知测试意图与当前渠道或内容不一致，已禁止生成新幂等键。')
    }
    applyPersistedTestAttempt(existing)
    return existing
  }
  const randomId = window.crypto?.randomUUID?.()
  if (!randomId) throw new Error('浏览器无法生成安全的通知测试幂等键，测试已禁用。')
  const record = {
    ...payload,
    idempotencyKey: `notify-test:${randomId}`,
    status: 'pending',
    attemptId: null,
    retrySafe: true,
    replaySafe: true,
    logPersisted: false,
    success: null,
    message: '通知测试意图已保存，等待后端确认是否发送。',
    createdAt: Date.now(),
  }
  if (!NOTIFICATION_TEST_KEY_PATTERN.test(record.idempotencyKey)) {
    throw new Error('通知测试幂等键格式校验失败，测试已禁用。')
  }
  window.localStorage.setItem(storageKey, JSON.stringify(record))
  const readBack = JSON.parse(window.localStorage.getItem(storageKey) || 'null')
  if (readBack?.idempotencyKey !== record.idempotencyKey) {
    throw new Error('通知测试意图持久化校验失败，测试已禁用。')
  }
  applyPersistedTestAttempt(record)
  return record
}

function rememberNotificationTestAttempt(intent, data, message) {
  const record = {
    ...intent,
    status: String(data?.attemptStatus || 'unknown'),
    attemptId: Number(data?.attemptId || 0) || null,
    retrySafe: data?.retrySafe === true,
    replaySafe: data?.replaySafe !== false,
    logPersisted: data?.logPersisted === true,
    success: typeof data?.success === 'boolean' ? data.success : null,
    message: String(message || '通知测试结果需要使用原意图核对。'),
    updatedAt: Date.now(),
  }
  window.localStorage.setItem(
    notificationTestStorageKey(intent.channelKey),
    JSON.stringify(record),
  )
  applyPersistedTestAttempt(record)
  return record
}

function finishNotificationTestIntent(intent) {
  window.localStorage.removeItem(notificationTestStorageKey(intent.channelKey))
  clearTestAttemptView()
}

function applySettings(data) {
  channels.value = normalizeChannels(data?.channels)
  events.value = normalizeEvents(data?.events)
  sendMode.value = String(data?.sendMode || 'single')
  inAppEnabled.value = toBool(data?.inApp, false)
  ensureSelectedChannel()
  baselineSettings.value = JSON.stringify(buildSettingsPayload())
}

function ensureSelectedChannel() {
  if (!channels.value.some(channel => channel.key === selectedKey.value)) {
    selectedKey.value = channels.value[0]?.key || ''
  }
}

function serializeChannels() {
  return channels.value.map(channel => ({
    key: channel.key,
    type: channel.type,
    name: channel.name,
    enabled: !!channel.enabled,
    method: channel.method || 'POST',
    contentType: channel.contentType || 'application/json',
    webhookUrl: channel.webhookUrl || '',
    receiver: channel.receiver || '',
    secret: channel.secret || '',
    timeoutSeconds: toNumber(channel.timeoutSeconds, 10),
    retryCount: toNumber(channel.retryCount, 3),
    failurePolicy: channel.failurePolicy || 'retry',
    template: channel.template || '',
    dedupeWindow: toNumber(channel.dedupeWindow, 0),
    smtpHost: channel.smtpHost || '',
    smtpPort: toNumber(channel.smtpPort, 465),
    smtpUser: channel.smtpUser || '',
    smtpPass: channel.smtpPass || '',
    fromEmail: channel.fromEmail || '',
    appId: channel.appId || '',
    verificationToken: channel.verificationToken || '',
    encryptKey: channel.encryptKey || '',
    receiveId: channel.receiveId || '',
    receiveIdType: channel.receiveIdType || 'open_id',
    clearWebhookUrl: !!channel.clearWebhookUrl,
    clearSecret: !!channel.clearSecret,
    clearSmtpPass: !!channel.clearSmtpPass,
    clearVerificationToken: !!channel.clearVerificationToken,
    clearEncryptKey: !!channel.clearEncryptKey
  }))
}

function serializeEvents() {
  return events.value.map(event => ({
    event: event.event,
    enabled: !!event.enabled,
    app: !!event.app
  }))
}

function buildSettingsPayload() {
  return {
    inApp: inAppEnabled.value,
    sendMode: sendMode.value,
    channels: serializeChannels(),
    events: serializeEvents()
  }
}

async function persistSettings() {
  await saveNotificationSettings(buildSettingsPayload())
}

async function load(showErrorNotice = false) {
  if (loading.value) return
  loading.value = true
  settingsAvailable.value = null
  if (showErrorNotice) clearNotices()
  const failures = []

  try {
    const settingsRes = await getNotificationSettings()
    applySettings(settingsRes?.data || {})
    settingsAvailable.value = true
  } catch (err) {
    settingsAvailable.value = false
    baselineSettings.value = ''
    failures.push(err.message || '通知设置加载失败')
  }

  const [noticeRes, logRes] = await Promise.allSettled([
    getNotifications({ current: 1, size: 6 }),
    getNotificationDeliveryLogs({ current: 1, size: 6 })
  ])

  if (noticeRes.status === 'fulfilled') {
    notifications.value = recordsOf(noticeRes.value?.data)
    notificationsLoaded.value = true
  } else {
    notifications.value = []
    notificationsLoaded.value = false
    failures.push('应用内通知加载失败')
  }

  if (logRes.status === 'fulfilled') {
    deliveryLogs.value = recordsOf(logRes.value?.data)
    logsLoaded.value = true
  } else {
    deliveryLogs.value = []
    logsLoaded.value = false
    failures.push('投递日志加载失败')
  }

  if (failures.length) error.value = `${[...new Set(failures)].join('；')}，请稍后重试`
  loading.value = false
}

async function save() {
  if (saving.value || testing.value || resolving.value || loading.value) return
  if (settingsAvailable.value !== true) {
    error.value = '通知配置状态未知，重新加载成功前禁止保存。'
    return
  }
  clearNotices()
  saving.value = true
  try {
    await persistSettings()
    success.value = '通知设置已保存'
    await load(false)
  } catch (err) {
    error.value = err.message || '通知设置保存失败'
  } finally {
    saving.value = false
  }
}

async function test() {
  if (testing.value || resolving.value || saving.value || loading.value) return
  clearNotices()
  if (settingsAvailable.value !== true) {
    error.value = '通知配置状态未知，重新加载成功前禁止发送测试。'
    return
  }
  if (settingsDirty.value) {
    error.value = '当前有未保存更改。测试只使用已保存配置，请先保存后再发送。'
    return
  }
  if (!selectedChannel.value || !isChannelConfigured(selectedChannel.value)) {
    error.value = '请先完成当前渠道的必要配置再发送测试通知'
    return
  }

  const payload = notificationTestPayload(selectedChannel.value)
  let intent
  try {
    intent = await withBrowserIntentLock(
      `notification-test:${payload.channelKey}`,
      () => persistNotificationTestIntent(payload),
    )
  } catch (err) {
    error.value = err?.message || '无法取得跨标签页通知测试锁，测试发送已禁用。'
    return
  }
  if (!intent) return

  testing.value = true
  try {
    const res = await testNotification({
      channelKey: payload.channelKey,
      title: payload.title,
      content: payload.content,
      idempotencyKey: intent.idempotencyKey,
    })
    const data = res?.data || {}
    if (data.attemptStatus === 'confirmed' && data.logPersisted === true) {
      try {
        finishNotificationTestIntent(intent)
      } catch {
        error.value = '通知结果与审计日志已确认，但浏览器无法清理旧意图；后续点击只会安全重放原结果。'
      }
      if (data.success === true) {
        success.value = '当前渠道测试发送成功，审计日志已记录'
      } else {
        error.value = '渠道明确返回发送失败，审计日志已记录；请检查渠道配置。'
      }
    } else {
      const message = data.attemptStatus === 'confirmed'
        ? '通知结果已确认、审计日志待修复；必须使用原测试意图补写，系统不会重发渠道消息。'
        : '通知测试状态尚未完成；必须使用原测试意图继续核对。'
      rememberNotificationTestAttempt(intent, data, message)
      error.value = message
    }
    await load(false)
  } catch (err) {
    const data = err?.data || {}
    const definitePreSendRejection = (
      (Number(err?.code || 0) === 400 || Number(err?.code || 0) === 422)
      && !data.attemptId
    )
    if (definitePreSendRejection) {
      try {
        finishNotificationTestIntent(intent)
      } catch {
        // Keeping the original key is safe; a later replay cannot duplicate a
        // provider send because the backend rejected before creating an attempt.
      }
      error.value = err?.message || '通知测试请求未执行，请修正配置后重试。'
    } else {
      const fallbackStatus = String(data.attemptStatus || 'unknown')
      const message = fallbackStatus === 'blocked'
        ? '该渠道被另一测试意图锁定；仅持有原意图的浏览器可完成核对或审计日志修复。'
        : fallbackStatus === 'in_progress'
          ? '当前渠道测试正在发送；请保留此页面并使用同一意图核对。'
          : fallbackStatus === 'confirmed' && data.logPersisted === false
            ? '通知结果已确认、审计日志待修复；使用原意图只会补日志，不会重发。'
            : '通知测试结果未知；禁止生成新幂等键，只能使用原意图核对且不会自动重发。'
      try {
        rememberNotificationTestAttempt(intent, {
          ...data,
          attemptStatus: fallbackStatus,
          retrySafe: data.retrySafe === true,
          replaySafe: data.replaySafe !== false,
          logPersisted: data.logPersisted === true,
        }, message)
      } catch {
        applyPersistedTestAttempt({
          ...intent,
          status: fallbackStatus,
          retrySafe: false,
          replaySafe: data.replaySafe !== false,
          logPersisted: false,
          message,
        })
      }
      error.value = err?.message ? `${message} ${err.message}` : message
    }
  } finally {
    testing.value = false
  }
}

async function recoverNotificationTestAttempt() {
  if (!testAttempt.replaySafe) {
    error.value = '当前浏览器不持有可恢复的原始测试意图，请回到原浏览器完成核对。'
    return
  }
  await test()
}

async function resolveUnknownNotificationTestAttempt() {
  if (
    resolving.value
    || testing.value
    || testAttempt.status !== 'unknown'
    || !testAttempt.replaySafe
    || !testAttempt.attemptId
  ) return

  const channelKey = String(testAttempt.channelKey || selectedKey.value || '')
  const intent = loadNotificationTestIntent(channelKey)
  if (!intent || !NOTIFICATION_TEST_KEY_PATTERN.test(String(intent.idempotencyKey || ''))) {
    error.value = '当前浏览器没有原始通知测试意图，禁止关闭未知状态。'
    return
  }
  const confirmed = window.confirm(
    '请再次确认：你已在通知服务方人工核对本次测试，确认不会再收到该测试消息。关闭未知意图不会撤回可能已送达的消息，也不会自动发送新测试。',
  )
  if (!confirmed) return

  clearNotices()
  resolving.value = true
  try {
    const res = await withBrowserIntentLock(
      `notification-test:${channelKey}`,
      () => resolveNotificationTestAttempt(testAttempt.attemptId, {
        channelKey,
        idempotencyKey: intent.idempotencyKey,
      }),
    )
    if (res?.data?.attemptStatus !== 'resolved' || res?.data?.providerCalled !== false) {
      throw new Error('后端未明确确认未知意图已安全关闭')
    }
    finishNotificationTestIntent(intent)
    success.value = '未知通知测试已人工核对并关闭；本次操作未发送新通知。需要新测试时请再次主动点击测试连接。'
    await load(false)
  } catch (err) {
    error.value = err?.message || '关闭未知通知测试失败；原意图与渠道锁已保留。'
    loadNotificationTestIntent(channelKey)
  } finally {
    resolving.value = false
  }
}

async function copyWebhookUrl() {
  const url = selectedChannel.value?.webhookUrl
  if (!url) {
    error.value = '当前渠道还没有可复制的 Webhook 地址'
    return
  }

  clearNotices()
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(url)
    } else {
      const input = document.createElement('textarea')
      input.value = url
      input.style.position = 'fixed'
      input.style.left = '-9999px'
      document.body.appendChild(input)
      input.select()
      document.execCommand('copy')
      input.remove()
    }
    success.value = 'Webhook 地址已复制'
  } catch {
    error.value = '复制失败，请手动复制当前地址'
  }
}

function clearSecretField(channel, field, configuredField) {
  if (!channel) return
  const clearFlag = `clear${field[0].toUpperCase()}${field.slice(1)}`
  channel[field] = ''
  channel[configuredField] = false
  channel[clearFlag] = true
  clearNotices()
  success.value = '已标记清除，保存后生效'
}

function addChannel() {
  if (settingsAvailable.value !== true) return
  clearNotices()
  const count = channels.value.filter(channel => String(channel.key).startsWith('custom-webhook-')).length + 1
  const meta = {
    key: `custom-webhook-${Date.now()}`,
    type: 'webhook',
    name: `自定义 Webhook ${count}`,
    icon: 'notifyWebhook',
    accent: 'blue'
  }
  channels.value.push(createChannelSeed(meta))
  selectedKey.value = meta.key
  success.value = '已新增自定义渠道，请补充地址后保存'
}

function toggleEvent(event, field) {
  if (settingsAvailable.value !== true) return
  event[field] = !event[field]
}

function toggleAllEvents(next) {
  if (settingsAvailable.value !== true) return
  events.value = events.value.map(event => ({
    ...event,
    enabled: next,
    app: next
  }))
}

function onHeaderAction(event) {
  if (event.detail === 'notify-save') save()
  if (event.detail === 'notify-test') test()
  if (event.detail === 'notify-refresh') load(true)
}

watch(selectedKey, channelKey => {
  loadNotificationTestIntent(channelKey)
})

onMounted(async () => {
  window.addEventListener('xya-header-action', onHeaderAction)
  await load(true)
  loadNotificationTestIntent(selectedKey.value)
})

onBeforeUnmount(() => {
  window.removeEventListener('xya-header-action', onHeaderAction)
})
</script>

<style scoped>
.notification-test-attempt {
  display: grid;
  gap: 10px;
  padding: 14px 16px;
  border: 1px solid #f3c568;
  border-radius: 14px;
  background: #fff8e6;
  color: #714900;
}

.notification-test-attempt.is-unknown,
.notification-test-attempt.is-blocked {
  border-color: #ee958b;
  background: #fff1ef;
  color: #8b3026;
}

.notification-test-attempt > div,
.notification-test-attempt dl {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px 16px;
  margin: 0;
}

.notification-test-attempt p {
  margin: 0;
  line-height: 1.65;
}

.notification-test-attempt dl > div {
  display: grid;
  gap: 2px;
  min-width: 120px;
}

.notification-test-attempt dt {
  font-size: 11px;
  opacity: .72;
}

.notification-test-attempt dd {
  margin: 0;
  overflow-wrap: anywhere;
  font-size: 12px;
  font-weight: 800;
}

.notification-test-attempt .notify-link-button {
  justify-self: start;
}

.notification-resolution-warning {
  padding: 10px 12px;
  border-radius: 10px;
  background: rgba(176, 46, 33, .09);
  font-weight: 700;
}

.notification-resolution-button {
  justify-self: start;
  padding: 9px 14px;
  border: 1px solid #d55a4d;
  border-radius: 9px;
  background: #fff;
  color: #a9362a;
  font-weight: 800;
  cursor: pointer;
}

.notification-resolution-button:disabled {
  cursor: not-allowed;
  opacity: .55;
}

.notify-unsaved-hint {
  flex: 1 1 100%;
  color: #a05b00;
  font-size: 12px;
}

.notify-settings-shell {
  display: grid;
  gap: 18px;
  padding-bottom: 28px;
}

.notify-hero {
  position: relative;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 340px;
  gap: 18px;
  padding: 28px;
  border-radius: 30px;
  overflow: hidden;
  border: 1px solid rgba(218, 229, 246, 0.96);
  background:
    radial-gradient(circle at 0% 0%, rgba(47, 107, 255, 0.15), transparent 30%),
    radial-gradient(circle at 100% 0%, rgba(23, 184, 150, 0.12), transparent 24%),
    linear-gradient(180deg, rgba(255, 255, 255, 0.97), rgba(242, 247, 255, 0.95));
  box-shadow: 0 24px 60px rgba(32, 76, 177, 0.12);
}

.notify-hero::before,
.notify-hero::after {
  content: '';
  position: absolute;
  border-radius: 30px;
  pointer-events: none;
}

.notify-hero::before {
  width: 220px;
  height: 220px;
  right: 240px;
  top: -80px;
  background: rgba(255, 255, 255, 0.54);
  transform: rotate(22deg);
}

.notify-hero::after {
  width: 180px;
  height: 180px;
  right: -32px;
  bottom: -56px;
  background: rgba(61, 136, 255, 0.12);
}

.notify-hero-copy {
  position: relative;
  z-index: 1;
}

.notify-hero-pill {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 8px 14px;
  border-radius: 999px;
  background: rgba(47, 107, 255, 0.08);
  color: #365fc6;
  font-size: 13px;
  font-weight: 800;
  letter-spacing: 0.02em;
}

.notify-hero-pill::before {
  content: '';
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: linear-gradient(135deg, #2f6bff, #5aaeff);
}

.notify-hero-copy h1 {
  margin: 14px 0 12px;
  font-size: 42px;
  line-height: 1.04;
  letter-spacing: -0.04em;
  color: #16315d;
}

.notify-hero-copy p {
  max-width: 760px;
  margin: 0;
  font-size: 15px;
  line-height: 1.85;
  color: #6780a4;
}

.notify-hero-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 18px;
}

.notify-hero-tags span {
  padding: 8px 12px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.82);
  border: 1px solid #dfe9f7;
  color: #50698f;
  font-size: 12px;
  font-weight: 700;
}

.notify-health-card {
  position: relative;
  z-index: 1;
  padding: 22px;
  border-radius: 24px;
  color: #fff;
  background: linear-gradient(135deg, rgba(18, 55, 112, 0.98), rgba(48, 107, 255, 0.95));
  box-shadow: 0 24px 44px rgba(32, 76, 177, 0.22);
}

.notify-health-card::before {
  content: '';
  position: absolute;
  width: 160px;
  height: 160px;
  right: -36px;
  top: -54px;
  border-radius: 48px;
  background: rgba(255, 255, 255, 0.12);
  transform: rotate(18deg);
}

.notify-health-top {
  position: relative;
  z-index: 1;
  display: flex;
  align-items: center;
  gap: 16px;
}

.notify-health-icon {
  width: 52px;
  height: 52px;
  border-radius: 18px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(255, 255, 255, 0.12);
  border: 1px solid rgba(255, 255, 255, 0.16);
}

.notify-health-icon :deep(.ui-icon-img),
.notify-health-icon :deep(.ui-icon) {
  width: 28px;
  height: 28px;
}

.notify-health-top b,
.notify-health-top strong {
  display: block;
}

.notify-health-top b {
  font-size: 14px;
  opacity: 0.82;
  margin-bottom: 6px;
}

.notify-health-top strong {
  font-size: 30px;
  line-height: 1.05;
}

.notify-health-progress {
  position: relative;
  z-index: 1;
  height: 10px;
  margin: 20px 0 14px;
  overflow: hidden;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.2);
}

.notify-health-progress span {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: linear-gradient(90deg, #7af1df, #ffffff);
}

.notify-health-foot {
  position: relative;
  z-index: 1;
  display: flex;
  justify-content: space-between;
  gap: 12px;
  font-size: 12px;
  opacity: 0.92;
}

.notify-summary-grid {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 14px;
}

.notify-summary-card {
  min-height: 122px;
  padding: 18px;
  border-radius: 24px;
  border: 1px solid rgba(228, 236, 247, 0.96);
  background: rgba(255, 255, 255, 0.96);
  box-shadow: 0 16px 36px rgba(36, 67, 128, 0.08);
  display: flex;
  gap: 14px;
  align-items: flex-start;
}

.notify-summary-icon {
  flex: none;
  width: 52px;
  height: 52px;
  border-radius: 18px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.notify-summary-icon :deep(.ui-icon-img),
.notify-summary-icon :deep(.ui-icon) {
  width: 30px;
  height: 30px;
}

.notify-summary-icon.blue,
.notify-channel-icon.blue { background: linear-gradient(135deg, rgba(47, 107, 255, 0.12), rgba(90, 174, 255, 0.16)); color: #3666d0; }
.notify-summary-icon.green,
.notify-channel-icon.green { background: linear-gradient(135deg, rgba(22, 191, 120, 0.14), rgba(91, 220, 160, 0.16)); color: #16a46b; }
.notify-summary-icon.cyan,
.notify-channel-icon.cyan { background: linear-gradient(135deg, rgba(35, 192, 232, 0.14), rgba(90, 174, 255, 0.16)); color: #1985c6; }
.notify-summary-icon.orange,
.notify-channel-icon.orange { background: linear-gradient(135deg, rgba(255, 180, 73, 0.18), rgba(255, 111, 127, 0.14)); color: #ef9a29; }
.notify-summary-icon.indigo,
.notify-channel-icon.indigo { background: linear-gradient(135deg, rgba(74, 99, 255, 0.14), rgba(141, 162, 255, 0.16)); color: #4d63dd; }
.notify-summary-icon.purple,
.notify-channel-icon.purple { background: linear-gradient(135deg, rgba(139, 92, 246, 0.14), rgba(191, 168, 255, 0.16)); color: #7a56d6; }

.notify-summary-copy {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.notify-summary-copy small {
  font-size: 12px;
  color: #8193af;
  font-weight: 700;
}

.notify-summary-copy strong {
  font-size: 30px;
  line-height: 1.02;
  color: #17325d;
  letter-spacing: -0.03em;
}

.notify-summary-copy span {
  font-size: 12px;
  color: #1ab78b;
  font-weight: 700;
}

.notify-workspace-grid {
  display: grid;
  grid-template-columns: 286px minmax(0, 1fr) 382px;
  gap: 16px;
  align-items: start;
}

.notify-left-column,
.notify-center-column,
.notify-right-column {
  display: grid;
  gap: 16px;
  min-width: 0;
}

.notify-panel {
  background: rgba(255, 255, 255, 0.96);
  border: 1px solid rgba(228, 236, 247, 0.96);
  border-radius: 28px;
  overflow: hidden;
  box-shadow: 0 20px 42px rgba(36, 67, 128, 0.1);
}

.notify-panel-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  padding: 20px 22px 16px;
  border-bottom: 1px solid #edf2fb;
}

.notify-panel-head h3 {
  margin: 0 0 8px;
  font-size: 24px;
  line-height: 1.1;
  color: #18345f;
}

.notify-panel-head p {
  margin: 0;
  font-size: 14px;
  line-height: 1.7;
  color: #7d8fab;
}

.notify-tiny-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 7px 12px;
  border-radius: 999px;
  background: rgba(47, 107, 255, 0.08);
  color: #3d66cb;
  font-size: 12px;
  font-weight: 700;
  white-space: nowrap;
}

.notify-panel-body {
  padding: 18px;
}

.notify-channel-list {
  display: grid;
  gap: 10px;
}

.notify-channel-item {
  width: 100%;
  padding: 14px;
  border: 1px solid #e3ebf7;
  border-radius: 20px;
  background: #f7faff;
  display: flex;
  align-items: flex-start;
  gap: 12px;
  text-align: left;
  transition: transform 0.18s ease, box-shadow 0.18s ease, border-color 0.18s ease, background 0.18s ease;
}

.notify-channel-item:hover {
  transform: translateY(-1px);
  box-shadow: 0 12px 24px rgba(36, 67, 128, 0.08);
}

.notify-channel-item.active {
  border-color: rgba(47, 107, 255, 0.3);
  background: linear-gradient(135deg, rgba(47, 107, 255, 0.12), rgba(23, 184, 150, 0.08));
  box-shadow: inset 0 0 0 1px rgba(47, 107, 255, 0.06);
}

.notify-channel-icon {
  width: 44px;
  height: 44px;
  border-radius: 16px;
  flex: none;
  display: flex;
  align-items: center;
  justify-content: center;
}

.notify-channel-icon :deep(.ui-icon-img),
.notify-channel-icon :deep(.ui-icon) {
  width: 24px;
  height: 24px;
}

.notify-channel-copy {
  min-width: 0;
}

.notify-channel-copy strong {
  display: block;
  margin-bottom: 6px;
  font-size: 16px;
  color: #18355f;
}

.notify-channel-copy p {
  margin: 0 0 8px;
  font-size: 13px;
  line-height: 1.6;
  color: #7b8daa;
}

.notify-channel-state {
  display: inline-flex;
  align-items: center;
  padding: 5px 10px;
  border-radius: 999px;
  background: rgba(20, 184, 109, 0.12);
  color: #159c61;
  font-size: 12px;
  font-weight: 700;
}

.notify-channel-state.off,
.notify-channel-state.muted {
  background: rgba(148, 164, 191, 0.16);
  color: #7e90aa;
}

.notify-add-channel {
  height: 48px;
  border-radius: 18px;
  border: 1px dashed #bfd0ed;
  background: linear-gradient(135deg, rgba(47, 107, 255, 0.05), rgba(90, 174, 255, 0.03));
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: #4b6eb0;
  font-weight: 700;
}

.notify-add-channel :deep(.ui-icon-img),
.notify-add-channel :deep(.ui-icon) {
  width: 18px;
  height: 18px;
}

.notify-side-note {
  padding: 18px;
  border-radius: 22px;
  color: #fff;
  background: linear-gradient(135deg, rgba(23, 59, 116, 0.98), rgba(45, 111, 255, 0.92));
  box-shadow: 0 18px 32px rgba(32, 76, 177, 0.18);
}

.notify-side-note-head {
  margin-bottom: 12px;
}

.notify-side-note-head strong {
  font-size: 18px;
}

.notify-side-note p {
  margin: 0 0 12px;
  font-size: 13px;
  line-height: 1.8;
  color: rgba(255, 255, 255, 0.84);
}

.notify-side-note ul {
  margin: 0;
  padding-left: 18px;
  font-size: 13px;
  line-height: 1.9;
  color: rgba(255, 255, 255, 0.92);
}

.notify-config-body {
  display: grid;
  gap: 16px;
}

.notify-status-strip {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 14px;
  padding: 14px 16px;
  border-radius: 20px;
  border: 1px solid rgba(20, 184, 109, 0.18);
  background: linear-gradient(135deg, rgba(20, 184, 109, 0.08), rgba(47, 107, 255, 0.05));
}

.notify-status-strip.disconnected {
  border-color: rgba(255, 180, 73, 0.24);
  background: linear-gradient(135deg, rgba(255, 180, 73, 0.12), rgba(47, 107, 255, 0.05));
}

.notify-strip-main {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  min-width: 0;
}

.notify-strip-icon {
  flex: none;
  width: 36px;
  height: 36px;
  border-radius: 14px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #169d73;
  background: linear-gradient(135deg, rgba(20, 184, 109, 0.16), rgba(47, 107, 255, 0.12));
}

.notify-status-strip.disconnected .notify-strip-icon {
  color: #ef9a29;
  background: linear-gradient(135deg, rgba(255, 180, 73, 0.16), rgba(255, 111, 127, 0.12));
}

.notify-strip-icon :deep(.ui-icon-img),
.notify-strip-icon :deep(.ui-icon) {
  width: 22px;
  height: 22px;
}

.notify-strip-copy {
  min-width: 0;
}

.notify-strip-copy strong,
.notify-strip-copy span {
  display: block;
}

.notify-strip-copy strong {
  margin-bottom: 4px;
  font-size: 15px;
  color: #18355f;
}

.notify-strip-copy span {
  font-size: 13px;
  line-height: 1.65;
  color: #6880a2;
}

.notify-link-button {
  border: 0;
  background: transparent;
  color: #2f6bff;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-weight: 800;
  white-space: nowrap;
}

.notify-link-button :deep(.ui-icon-img),
.notify-link-button :deep(.ui-icon) {
  width: 16px;
  height: 16px;
}

.notify-config-section {
  display: grid;
  gap: 12px;
}

.notify-config-section h4 {
  margin: 0;
  font-size: 15px;
  color: #6c82a5;
  letter-spacing: 0.02em;
}

.notify-section-headline {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.notify-inline-chip {
  height: 34px;
  padding: 0 12px;
  border-radius: 999px;
  border: 1px solid rgba(47, 107, 255, 0.12);
  background: rgba(47, 107, 255, 0.06);
  color: #315fc6;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  font-weight: 700;
  white-space: nowrap;
}

.notify-inline-chip :deep(.ui-icon-img),
.notify-inline-chip :deep(.ui-icon) {
  width: 16px;
  height: 16px;
}

.notify-form-grid {
  display: grid;
  gap: 12px;
}

.notify-form-grid.two {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.notify-form-grid.three {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.notify-field {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.notify-field span {
  font-size: 13px;
  font-weight: 700;
  color: #6c7f9f;
}

.notify-field small {
  font-size: 12px;
  color: #8a9ab3;
}

.notify-field-full {
  grid-column: 1 / -1;
}

.notify-field input,
.notify-field textarea,
.notify-select {
  width: 100%;
  border-radius: 16px;
  border: 1px solid #d9e4f4;
  background: linear-gradient(180deg, #ffffff, #f7faff);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.94);
  color: #16335f;
}

.notify-field input {
  height: 46px;
  padding: 0 14px;
}

.notify-select {
  height: 46px;
  padding: 0 40px 0 14px;
  appearance: none;
  background-image:
    linear-gradient(45deg, transparent 50%, #6c7f9f 50%),
    linear-gradient(135deg, #6c7f9f 50%, transparent 50%),
    linear-gradient(180deg, #ffffff, #f7faff);
  background-position:
    calc(100% - 20px) 20px,
    calc(100% - 14px) 20px,
    0 0;
  background-size: 6px 6px, 6px 6px, 100% 100%;
  background-repeat: no-repeat;
}

.notify-input-wrap {
  position: relative;
  display: flex;
  align-items: center;
}

.notify-input-wrap input {
  padding-right: 52px;
}

.notify-input-action {
  position: absolute;
  right: 10px;
  width: 32px;
  height: 32px;
  border: 0;
  border-radius: 12px;
  background: rgba(47, 107, 255, 0.08);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: #2f6bff;
}

.notify-input-action :deep(.ui-icon-img),
.notify-input-action :deep(.ui-icon) {
  width: 16px;
  height: 16px;
}

.notify-field textarea {
  min-height: 124px;
  padding: 14px;
  resize: vertical;
  line-height: 1.8;
}

.notify-segment {
  display: flex;
  gap: 6px;
  padding: 5px;
  border-radius: 16px;
  border: 1px solid #d9e4f4;
  background: linear-gradient(180deg, #ffffff, #f7faff);
}

.notify-segment button {
  flex: 1;
  height: 36px;
  border: 0;
  border-radius: 12px;
  background: transparent;
  color: #6780a5;
  font-size: 13px;
  font-weight: 700;
}

.notify-segment button.active {
  color: #fff;
  background: linear-gradient(135deg, #2f6bff, #4c9fff);
  box-shadow: 0 12px 24px rgba(47, 107, 255, 0.2);
}

.notify-chip-group {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.notify-chip-group button,
.notify-rule-actions button {
  height: 38px;
  padding: 0 14px;
  border-radius: 12px;
  border: 1px solid #d8e3f4;
  background: #fff;
  color: #5f7397;
  font-size: 13px;
  font-weight: 700;
}

.notify-chip-group button.active {
  color: #2f6bff;
  border-color: rgba(47, 107, 255, 0.26);
  background: rgba(47, 107, 255, 0.08);
}

.notify-config-actions {
  display: flex;
  gap: 12px;
  margin-top: 4px;
}

.notify-tutorial-body {
  display: grid;
  gap: 16px;
}

.notify-tutorial-hero {
  display: grid;
  gap: 14px;
  padding: 18px;
  border-radius: 24px;
  border: 1px solid rgba(214, 226, 244, 0.96);
  background: linear-gradient(135deg, rgba(47, 107, 255, 0.08), rgba(255, 255, 255, 0.98));
}

.notify-tutorial-hero.cyan {
  background: linear-gradient(135deg, rgba(23, 184, 150, 0.12), rgba(255, 255, 255, 0.98));
}

.notify-tutorial-hero.indigo {
  background: linear-gradient(135deg, rgba(90, 84, 255, 0.12), rgba(255, 255, 255, 0.98));
}

.notify-tutorial-hero.green {
  background: linear-gradient(135deg, rgba(20, 184, 109, 0.12), rgba(255, 255, 255, 0.98));
}

.notify-tutorial-hero.purple {
  background: linear-gradient(135deg, rgba(154, 92, 255, 0.12), rgba(255, 255, 255, 0.98));
}

.notify-tutorial-hero-main {
  display: flex;
  align-items: flex-start;
  gap: 14px;
}

.notify-tutorial-icon {
  width: 52px;
  height: 52px;
  border-radius: 18px;
  flex: none;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, rgba(47, 107, 255, 0.12), rgba(90, 174, 255, 0.18));
  color: #2f6bff;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.7);
}

.notify-tutorial-icon.cyan {
  background: linear-gradient(135deg, rgba(23, 184, 150, 0.14), rgba(91, 220, 160, 0.18));
  color: #14916f;
}

.notify-tutorial-icon.indigo {
  background: linear-gradient(135deg, rgba(90, 84, 255, 0.14), rgba(138, 132, 255, 0.18));
  color: #4f4ae1;
}

.notify-tutorial-icon.green {
  background: linear-gradient(135deg, rgba(20, 184, 109, 0.14), rgba(116, 217, 160, 0.2));
  color: #169c61;
}

.notify-tutorial-icon.purple {
  background: linear-gradient(135deg, rgba(154, 92, 255, 0.14), rgba(214, 164, 255, 0.2));
  color: #7f4de2;
}

.notify-tutorial-icon :deep(.ui-icon-img),
.notify-tutorial-icon :deep(.ui-icon) {
  width: 28px;
  height: 28px;
}

.notify-tutorial-copy {
  min-width: 0;
}

.notify-tutorial-copy strong,
.notify-tutorial-copy p {
  display: block;
}

.notify-tutorial-copy strong {
  margin-bottom: 6px;
  font-size: 18px;
  color: #18355f;
}

.notify-tutorial-copy p {
  margin: 0;
  font-size: 14px;
  line-height: 1.75;
  color: #647b9d;
}

.notify-tutorial-prep {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.notify-tutorial-prep span {
  display: inline-flex;
  align-items: center;
  min-height: 34px;
  padding: 0 12px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.88);
  border: 1px solid rgba(214, 226, 244, 0.96);
  color: #5f7698;
  font-size: 12px;
  font-weight: 700;
}

.notify-tutorial-grid,
.notify-tutorial-foot {
  display: grid;
  grid-template-columns: minmax(0, 1.1fr) minmax(0, 0.9fr);
  gap: 16px;
}

.notify-tutorial-block {
  padding: 18px;
  border-radius: 24px;
  border: 1px solid rgba(231, 238, 249, 0.98);
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.98), rgba(246, 250, 255, 0.98));
}

.notify-tutorial-block-highlight {
  background: linear-gradient(135deg, rgba(255, 180, 73, 0.12), rgba(47, 107, 255, 0.04), rgba(255, 255, 255, 0.98));
}

.notify-tutorial-block-head {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 14px;
}

.notify-tutorial-head-icon {
  width: 42px;
  height: 42px;
  border-radius: 16px;
  flex: none;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, rgba(47, 107, 255, 0.12), rgba(90, 174, 255, 0.16));
  color: #2f6bff;
}

.notify-tutorial-head-icon :deep(.ui-icon-img),
.notify-tutorial-head-icon :deep(.ui-icon) {
  width: 22px;
  height: 22px;
}

.notify-tutorial-block-head small {
  display: block;
  margin-bottom: 4px;
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0.08em;
  color: #87a0c6;
}

.notify-tutorial-block-head h4 {
  margin: 0;
  font-size: 18px;
  color: #18355f;
}

.notify-tutorial-steps {
  display: grid;
}

.notify-tutorial-step {
  display: grid;
  grid-template-columns: 36px minmax(0, 1fr);
  gap: 12px;
  padding: 12px 0;
  border-top: 1px solid #edf3fb;
}

.notify-tutorial-step:first-child {
  padding-top: 0;
  border-top: 0;
}

.notify-step-index {
  width: 36px;
  height: 36px;
  border-radius: 14px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #2f6bff, #5aaeff);
  color: #fff;
  font-size: 13px;
  font-weight: 800;
  box-shadow: 0 12px 20px rgba(47, 107, 255, 0.2);
}

.notify-step-copy {
  min-width: 0;
}

.notify-step-copy strong,
.notify-step-copy p,
.notify-step-copy small {
  display: block;
}

.notify-step-copy strong {
  margin-bottom: 5px;
  font-size: 15px;
  color: #18355f;
}

.notify-step-copy p {
  margin: 0 0 5px;
  font-size: 13px;
  line-height: 1.75;
  color: #647b9d;
}

.notify-step-copy small {
  font-size: 12px;
  line-height: 1.65;
  color: #8b9bb5;
}

.notify-tutorial-fields {
  display: grid;
  gap: 10px;
}

.notify-tutorial-field {
  padding: 14px;
  border-radius: 18px;
  border: 1px solid #ebf1fb;
  background: rgba(255, 255, 255, 0.9);
}

.notify-tutorial-field strong,
.notify-tutorial-field p {
  display: block;
}

.notify-tutorial-field strong {
  margin-bottom: 6px;
  font-size: 14px;
  color: #1c3b68;
}

.notify-tutorial-field p {
  margin: 0;
  font-size: 13px;
  line-height: 1.72;
  color: #6d82a5;
}

.notify-tutorial-checklist {
  margin: 0;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 10px;
}

.notify-tutorial-checklist li {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  font-size: 13px;
  line-height: 1.72;
  color: #5f7798;
}

.notify-check-dot {
  width: 18px;
  height: 18px;
  margin-top: 2px;
  border-radius: 50%;
  flex: none;
  background: linear-gradient(135deg, rgba(20, 184, 109, 0.18), rgba(47, 107, 255, 0.12));
  position: relative;
}

.notify-check-dot::after {
  content: '';
  position: absolute;
  left: 6px;
  top: 4px;
  width: 5px;
  height: 8px;
  border-right: 2px solid #16a34a;
  border-bottom: 2px solid #16a34a;
  transform: rotate(40deg);
}

.notify-tutorial-highlight {
  padding: 16px;
  border-radius: 20px;
  background: rgba(255, 255, 255, 0.82);
  border: 1px solid rgba(255, 255, 255, 0.9);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.7);
}

.notify-tutorial-highlight p {
  margin: 0;
  font-size: 13px;
  line-height: 1.9;
  color: #5f7698;
}

.notify-rules-headline {
  display: grid;
  grid-template-columns: 1fr 64px 92px;
  gap: 8px;
  padding: 14px 18px 0;
  color: #8092af;
  font-size: 12px;
  font-weight: 700;
}

.notify-rules-headline span:nth-child(2),
.notify-rules-headline span:nth-child(3) {
  text-align: center;
}

.notify-rules-groups {
  padding: 4px 18px 0;
}

.notify-rule-group {
  padding: 14px 14px 8px;
  border-radius: 20px;
  border: 1px solid #ebf1fb;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.96), rgba(246, 250, 255, 0.96));
}

.notify-rule-group + .notify-rule-group {
  margin-top: 10px;
}

.notify-rule-group-title {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
}

.notify-group-icon {
  width: 34px;
  height: 34px;
  border-radius: 14px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, rgba(47, 107, 255, 0.12), rgba(90, 174, 255, 0.16));
  color: #2f6bff;
}

.notify-group-icon.amber {
  background: linear-gradient(135deg, rgba(255, 180, 73, 0.2), rgba(255, 111, 127, 0.14));
  color: #eb8d18;
}

.notify-group-icon.green {
  background: linear-gradient(135deg, rgba(22, 191, 120, 0.16), rgba(91, 220, 160, 0.18));
  color: #16a46b;
}

.notify-group-icon :deep(.ui-icon-img),
.notify-group-icon :deep(.ui-icon) {
  width: 20px;
  height: 20px;
}

.notify-rule-group strong {
  font-size: 15px;
  color: #18345f;
}

.notify-rule-row {
  display: grid;
  grid-template-columns: 1fr 64px 92px;
  gap: 8px;
  align-items: center;
  padding: 10px 0;
  border-top: 1px solid #edf2fb;
}

.notify-rule-row b {
  font-size: 14px;
  color: #41536f;
}

.notify-switch-button {
  display: flex;
  justify-content: center;
  padding: 0;
}

.notify-switch-button :deep(.switch) {
  cursor: pointer;
}

.notify-rule-actions {
  display: flex;
  gap: 8px;
  padding: 16px 18px 18px;
}

.notify-log-list {
  display: grid;
  gap: 2px;
  padding-top: 12px;
}

.notify-log-row {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  padding: 12px 0;
  border-bottom: 1px solid #edf2fb;
}

.notify-log-row:last-child {
  border-bottom: 0;
}

.notify-log-main {
  min-width: 0;
  display: flex;
  gap: 12px;
}

.notify-log-icon {
  width: 40px;
  height: 40px;
  border-radius: 14px;
  flex: none;
  display: flex;
  align-items: center;
  justify-content: center;
}

.notify-log-icon.blue { background: rgba(47, 107, 255, 0.1); color: #2f6bff; }
.notify-log-icon.green { background: rgba(20, 184, 109, 0.1); color: #16a34a; }
.notify-log-icon.orange { background: rgba(255, 180, 73, 0.16); color: #ea8a00; }

.notify-log-icon :deep(.ui-icon-img),
.notify-log-icon :deep(.ui-icon) {
  width: 22px;
  height: 22px;
}

.notify-log-copy {
  min-width: 0;
}

.notify-log-copy strong,
.notify-log-copy span,
.notify-log-copy small {
  display: block;
}

.notify-log-copy strong {
  margin-bottom: 4px;
  color: #18355f;
  font-size: 14px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.notify-log-copy span,
.notify-log-copy small {
  font-size: 12px;
  color: #7589a8;
  line-height: 1.6;
}

.notify-preview-card {
  padding: 20px;
  border-radius: 28px;
  background: rgba(255, 255, 255, 0.96);
  border: 1px solid rgba(228, 236, 247, 0.96);
  box-shadow: 0 20px 42px rgba(36, 67, 128, 0.1);
}

.notify-preview-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}

.notify-preview-head strong {
  font-size: 22px;
  color: #18345f;
}

.notify-preview-tag {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 7px 12px;
  border-radius: 999px;
  background: rgba(47, 107, 255, 0.08);
  color: #3d66cb;
  font-size: 12px;
  font-weight: 700;
}

.notify-preview-phone {
  width: 100%;
  max-width: 284px;
  margin: 0 auto;
  padding: 14px 12px 18px;
  border-radius: 34px;
  border: 1px solid rgba(27, 67, 128, 0.16);
  background: linear-gradient(180deg, #12376f, #1c4e97 22%, #eaf1ff 22.2%, #f6f9ff 100%);
  box-shadow: 0 24px 48px rgba(20, 54, 112, 0.22);
}

.notify-phone-top {
  width: 94px;
  height: 26px;
  margin: 0 auto 12px;
  border-radius: 0 0 16px 16px;
  background: #0f2750;
}

.notify-phone-screen {
  min-height: 364px;
  padding: 16px;
  border-radius: 24px;
  background: linear-gradient(180deg, #f7faff, #eef4ff);
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.notify-phone-time {
  margin-bottom: 4px;
  font-size: 13px;
  font-weight: 800;
  color: #5d7091;
  text-align: center;
}

.notify-toast {
  display: flex;
  gap: 10px;
  padding: 12px;
  border-radius: 18px;
  background: rgba(255, 255, 255, 0.96);
  border: 1px solid #e1eaf8;
  box-shadow: 0 10px 22px rgba(36, 67, 128, 0.08);
}

.notify-toast.warn {
  background: linear-gradient(135deg, rgba(255, 180, 73, 0.16), rgba(255, 255, 255, 0.96));
}

.notify-toast-icon {
  flex: none;
  width: 36px;
  height: 36px;
  border-radius: 14px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(47, 107, 255, 0.08);
  color: #2f6bff;
}

.notify-toast.warn .notify-toast-icon {
  background: rgba(255, 180, 73, 0.2);
  color: #ef9a29;
}

.notify-toast-icon :deep(.ui-icon-img),
.notify-toast-icon :deep(.ui-icon) {
  width: 20px;
  height: 20px;
}

.notify-toast-copy {
  min-width: 0;
}

.notify-toast-copy strong,
.notify-toast-copy span {
  display: block;
}

.notify-toast-copy strong {
  margin-bottom: 4px;
  font-size: 14px;
  color: #18355f;
}

.notify-toast-copy span {
  font-size: 12px;
  line-height: 1.65;
  color: #7589a8;
}

.notify-mini-lines {
  display: grid;
  gap: 10px;
  margin-top: auto;
}

.notify-mini-lines div {
  height: 10px;
  border-radius: 999px;
  background: #dce6fa;
}

.notify-foot-note {
  margin-top: 14px;
  display: flex;
  justify-content: space-between;
  gap: 12px;
  font-size: 12px;
  color: #7b8daa;
}

@media (max-width: 1680px) {
  .notify-summary-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .notify-workspace-grid {
    grid-template-columns: 286px minmax(0, 1fr);
  }

  .notify-right-column {
    grid-column: 1 / -1;
    grid-template-columns: minmax(0, 1.2fr) minmax(0, 1fr);
    align-items: start;
  }

  .notify-preview-card {
    height: 100%;
  }
}

@media (max-width: 1320px) {
  .notify-hero,
  .notify-workspace-grid,
  .notify-right-column,
  .notify-summary-grid,
  .notify-form-grid.two {
    grid-template-columns: minmax(0, 1fr);
  }

  .notify-health-card {
    min-height: 0;
  }

  .notify-tutorial-grid,
  .notify-tutorial-foot {
    grid-template-columns: minmax(0, 1fr);
  }

  .notify-tutorial-hero-main {
    align-items: flex-start;
  }

  .notify-rules-headline,
  .notify-rule-row {
    grid-template-columns: minmax(0, 1fr) 64px 64px;
  }
}

@media (max-width: 900px) {
  .notify-settings-shell {
    gap: 12px;
  }

  .notify-hero {
    padding: 14px;
  }

  .notify-hero-copy h1 {
    font-size: 24px;
  }

  .notify-hero-copy p {
    font-size: 13px;
  }

  .notify-health-card {
    padding: 14px;
  }

  .notify-health-top strong {
    font-size: 22px;
  }

  .notify-summary-grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .notify-summary-card {
    min-height: 0;
    padding: 14px;
  }

  .notify-summary-copy strong {
    font-size: 22px;
  }

  .notify-workspace-grid {
    grid-template-columns: minmax(0, 1fr);
    gap: 12px;
  }

  .notify-form-grid.three {
    grid-template-columns: minmax(0, 1fr);
  }

  .notify-panel-head {
    padding: 14px;
  }

  .notify-panel-head h3 {
    font-size: 18px;
  }

  .notify-panel-body {
    padding: 14px;
  }

  .notify-channel-item {
    padding: 12px;
  }

  .notify-config-body {
    gap: 12px;
  }

  .notify-status-strip {
    flex-direction: column;
    align-items: flex-start;
    gap: 10px;
    padding: 12px;
  }

  .notify-tutorial-hero {
    padding: 14px;
  }

  .notify-tutorial-hero-main {
    flex-direction: column;
    gap: 10px;
  }

  .notify-tutorial-block {
    padding: 14px;
  }

  .notify-tutorial-step {
    grid-template-columns: 28px minmax(0, 1fr);
    gap: 10px;
  }

  .notify-step-index {
    width: 28px;
    height: 28px;
    font-size: 12px;
  }

  .notify-rules-headline,
  .notify-rule-row {
    grid-template-columns: minmax(0, 1fr) 52px 52px;
  }

  .notify-rule-actions {
    flex-wrap: wrap;
    padding: 12px 14px 14px;
  }

  .notify-preview-card {
    padding: 14px;
  }

  .notify-preview-head strong {
    font-size: 18px;
  }

  .notify-config-actions {
    flex-direction: column;
  }

  .notify-hero > *,
  .notify-workspace-grid > *,
  .notify-right-column > *,
  .notify-summary-grid > *,
  .notify-form-grid.two > *,
  .notify-form-grid.three > *,
  .notify-tutorial-grid > *,
  .notify-tutorial-foot > *,
  .notify-rules-headline > *,
  .notify-rule-row > * {
    min-width: 0;
  }
}

</style>
