<template>
  <div class="xya-msg-page">
    <div class="xya-msg-layout">
      <aside class="xya-msg-sidebar">
        <div class="xya-msg-sidebar-head">
          <div>
            <div class="xya-msg-title-row">
              <h2>在线消息</h2>
              <span class="xya-msg-count">{{ displayList.length }}</span>
            </div>
            <p>聚合管理买家咨询，实时沟通并转化与服务效率</p>
          </div>
          <button class="xya-msg-icon-btn" type="button" :disabled="loading || conversationRefreshing" aria-label="刷新会话" @click="reload">
            <svg viewBox="0 0 24 24" fill="none">
              <path d="M20 12a8 8 0 1 1-2.34-5.66" />
              <path d="M20 4v6h-6" />
            </svg>
          </button>
          <span class="xya-msg-realtime-badge" :class="{ connected: realtimeMode.key === 'realtime', polling: realtimeMode.key === 'polling' }" :title="realtimeMode.title">
            <span class="xya-msg-realtime-dot"></span>
            <span class="xya-msg-realtime-label">{{ realtimeMode.label }}</span>
          </span>
        </div>

        <div class="xya-msg-tabs">
          <button type="button" :class="{ active: conversationFilter === 'all' }" @click="setConversationFilter('all')">全部消息 {{ conversations.length }}</button>
          <button type="button" :class="{ active: conversationFilter === 'unreplied' }" @click="setConversationFilter('unreplied')">未回复 {{ unrepliedCount }}</button>
          <button type="button" :class="{ active: conversationFilter === 'inProgress' }" @click="setConversationFilter('inProgress')">进行中 {{ inProgressCount }}</button>
          <button type="button" :class="{ active: conversationFilter === 'completed' }" @click="setConversationFilter('completed')">已完成 {{ completedCount }}</button>
          <button type="button" :class="{ active: conversationFilter === 'robot' }" @click="setConversationFilter('robot')">机器人 {{ robotCount }}</button>
        </div>

        <div class="xya-msg-search-row">
          <label class="xya-msg-search-box">
            <svg viewBox="0 0 24 24" fill="none">
              <circle cx="11" cy="11" r="7" />
              <path d="m20 20-3.5-3.5" />
            </svg>
            <input v-model="keyword" type="text" placeholder="搜索联系人、商品或关键词" />
          </label>
        </div>

        <div class="xya-msg-filter-row">
          <select v-model="query.xianyuAccountId" class="xya-msg-select">
            <option value="" disabled>请选择账号</option>
            <option v-for="a in accounts" :key="a.id" :value="a.id">{{ accountName(a) }}</option>
          </select>
          <button type="button" class="xya-msg-filter-btn" @click="setConversationFilter('all')">全部会话</button>
          <button type="button" class="xya-msg-filter-btn" @click="toggleSortOrder">排序：{{ sortDesc ? '最新消息' : '最早消息' }}</button>
        </div>

        <div v-if="error" class="xya-msg-alert" role="alert">{{ error }}</div>
        <div v-if="refreshNotice" class="xya-msg-alert xya-msg-refresh-alert" role="status" aria-live="polite">{{ refreshNotice }}</div>

        <div class="xya-msg-conversation-list" :aria-busy="loading || conversationRefreshing">
          <div v-if="loading && displayList.length === 0" class="xya-msg-empty">正在加载会话...</div>
          <div v-else-if="error && displayList.length === 0" class="xya-msg-empty" style="color:#ef4444">{{ error }}</div>
          <div v-else-if="displayList.length === 0 && !loading" class="xya-msg-empty">
            <p style="margin-bottom:8px">暂无会话数据</p>
            <p style="font-size:11px;color:#99a4b4">
              {{ accounts.length ? `当前账号：${accountLabel || query.xianyuAccountId}。可能尚未收到买家消息，或 WebSocket 未连接。` : '还没有可用的闲鱼账号，请先添加账号。' }}
            </p>
            <div style="margin-top:12px;display:flex;gap:8px;justify-content:center;flex-wrap:wrap">
              <button v-if="accounts.length" type="button" class="xya-msg-mini-btn" @click="startCurrentConnection">启动当前账号连接</button>
              <button type="button" class="xya-msg-mini-btn" @click="reload">刷新会话</button>
              <button type="button" class="xya-msg-mini-btn" @click="emit('navigate', accounts.length ? 'connections' : 'accounts')">{{ accounts.length ? '去连接管理' : '去添加账号' }}</button>
            </div>
          </div>
	          <button
	            v-for="c in pagedDisplayList"
            :key="getConversationIdentityKey(c) || c.sid"
            type="button"
            :class="['xya-msg-conversation', { active: conversationDedupeKey(selected) === conversationDedupeKey(c) }]"
            @click="selectChat(c)"
          >
            <div class="xya-msg-avatar-wrap">
              <img v-if="conversationAvatarUrl(c)" :src="conversationAvatarUrl(c)" class="xya-msg-avatar avatar-image" alt="" @error="markAvatarFailed($event.target.src)" />
              <div v-else class="xya-msg-avatar">{{ avatarText(resolveConversationName(c)) }}</div>
              <span v-if="c.onlineStatus === 'online'" class="xya-msg-online-dot" title="在线"></span>
              <span v-else-if="c.onlineStatus === 'unknown'" class="xya-msg-online-dot unknown" title="在线状态未知"></span>
            </div>
            <div class="xya-msg-conversation-main">
	            <div class="xya-msg-conversation-top">
	                <div class="xya-msg-name-line">
	                  <strong>{{ resolveConversationName(c) }}</strong>
	                  <span v-if="c.badgeText" class="xya-msg-buyer-tag">{{ c.badgeText }}</span>
	                </div>
	                <span class="xya-msg-conversation-time">{{ c.time }}</span>
	              </div>
	              <p class="xya-msg-preview">
	                <span v-if="c.lastIsAutoReply" class="xya-msg-ai-tag inline">AI回复</span>
	                {{ c.msg || '暂无消息内容' }}
	              </p>
              <div class="xya-msg-product-line">
                <img class="xya-msg-product-thumb" :src="conversationThumb(c)" alt="商品缩略图" />
                <span>{{ c.product }}</span>
                <em>{{ c.goodsPriceText }}</em>
                <i v-if="c.unreadCount" class="xya-msg-unread">{{ c.unreadCount > 99 ? '99+' : c.unreadCount }}</i>
              </div>
            </div>
          </button>
          <div v-if="canLoadMoreConversations" class="xya-msg-conversation-more">
            <button type="button" class="xya-msg-more-btn" :disabled="loadingMoreConversations" @click="loadMoreConversations">{{ loadingMoreConversations ? '加载中...' : '查看更多' }}</button>
          </div>
        </div>
        <div class="xya-msg-footer-note">{{ backgroundRefreshing ? '正在后台同步 · ' : '' }}共 {{ displayList.length }} 条会话</div>
      </aside>

      <section class="xya-msg-chat-panel">
        <header class="xya-msg-chat-head">
          <div class="xya-msg-chat-user">
            <img v-if="conversationAvatarUrl(selected)" :src="conversationAvatarUrl(selected)" class="xya-msg-avatar large avatar-image" alt="" @error="markAvatarFailed($event.target.src)" />
            <div v-else class="xya-msg-avatar large">{{ avatarText(resolveConversationName(selected)) }}</div>
            <div>
              <div class="xya-msg-chat-user-line">
                <strong>{{ resolveConversationName(selected) || '请选择会话' }}</strong>
                <span v-if="selected?.badgeText" class="xya-msg-buyer-tag">{{ selected.badgeText }}</span>
              </div>
              <p>{{ selected?.product || '暂未选择商品' }}</p>
            </div>
          </div>
          <div class="xya-msg-chat-actions">
            <button type="button" class="xya-msg-head-btn" :disabled="!selected?.sid || !conversationDbId(selected) || statusUpdating" @click="transferSession">
              <svg viewBox="0 0 24 24" fill="none"><path d="M15 8l-6 4 6 4V8Z" /></svg>
              转接
            </button>
            <button type="button" class="xya-msg-head-btn danger" :disabled="!selected?.sid || !conversationDbId(selected) || statusUpdating" @click="endSession">
              <svg viewBox="0 0 24 24" fill="none"><path d="M12 8v4" /><circle cx="12" cy="16" r="1" /><circle cx="12" cy="12" r="9" /></svg>
              结束会话
            </button>
          </div>
        </header>

        <div class="xya-msg-chat-body">
          <div ref="messagesContainer" class="xya-msg-chat-stream">
            <div v-if="contextLoading && contextMessages.length === 0" class="xya-msg-empty soft">加载聊天记录中...</div>
            <div v-else-if="selected && !contextAvailable" class="xya-msg-empty soft">
              聊天记录暂不可用，已暂停发送以避免发错会话。
              <button type="button" class="xya-msg-mini-btn" @click="loadContext">重试加载</button>
            </div>
            <div v-else-if="contextMessages.length === 0" class="xya-msg-empty soft">{{ selected ? '当前会话暂无聊天记录' : '点击左侧会话查看聊天记录' }}</div>
            <template v-else>
              <div v-if="hasMoreContext" class="xya-msg-load-more">
                <button type="button" class="xya-msg-mini-btn" :disabled="contextLoadingMore" @click="loadMoreContext">
                  {{ contextLoadingMore ? '加载中...' : '加载更早消息' }}
                </button>
              </div>
	              <div
	                v-for="m in decoratedMessages"
	                :key="m.id"
	                :class="['xya-msg-bubble-row', { me: m.isMe }]"
	              >
                <img v-if="!m.isMe && conversationAvatarUrl(selected)" :src="conversationAvatarUrl(selected)" class="xya-msg-avatar small avatar-image" alt="" @error="markAvatarFailed($event.target.src)" />
                <div v-else-if="!m.isMe" class="xya-msg-avatar small">{{ avatarText(resolveConversationName(selected)) }}</div>
	                <div class="xya-msg-bubble-stack">
	                  <div v-if="m.showDate" class="xya-msg-time-divider">{{ m.showDate }}</div>
	                  <div v-if="m.isAiReply" class="xya-msg-ai-row" :class="{ me: m.isMe }">
	                    <span class="xya-msg-ai-tag">AI自动回复</span>
	                  </div>
	                  <div :class="['xya-msg-bubble', { me: m.isMe }]">
                    <template v-if="m.imageUrls?.length">
                      <img
                        v-for="img in m.imageUrls"
                        :key="img"
                        :src="img"
                        class="xya-msg-inline-image"
                        alt="图片消息"
                        @click="openImagePreview(img)"
                      />
                    </template>
                    <template v-else-if="m.media?.type === 'audio'">
                      <div class="xya-msg-audio-wrap">
                        <audio v-if="m.media?.url || m.media?.fileUrl || m.media?.downloadUrl" :src="m.media.url || m.media.fileUrl || m.media.downloadUrl" controls preload="none" class="xya-msg-audio-player"></audio>
                        <span v-else>语音消息（暂不支持播放）</span>
                      </div>
                    </template>
                    <template v-else-if="m.card?.title || m.card?.subtitle">
                      <div class="xya-msg-card-bubble">
                        <img v-if="m.card.image" :src="m.card.image" class="xya-msg-card-image" alt="商品图片" @error="onCardImageError" />
                        <div class="xya-msg-card-info">
                          <b>{{ m.card.title }}</b>
                          <p v-if="m.card.subtitle">{{ m.card.subtitle }}</p>
                        </div>
                      </div>
                    </template>
                    <template v-else>
                      {{ m.displayText || m.msgContent || '[非文本消息]' }}
                    </template>
                  </div>
                  <div class="xya-msg-bubble-meta" :class="{ me: m.isMe }">
                    <span>{{ formatMessageDay(m.messageTime) }}</span>
                    <span v-if="m.isMe" :class="{ 'xya-msg-status-failed': m.sendStatus === 'failed', 'xya-msg-status-unknown': m.sendStatus === 'unknown', 'xya-msg-status-sending': m.sendStatus === 'sending' }">
                      {{ m.sendStatus === 'sending' ? '发送中' : m.sendStatus === 'failed' ? '明确未发送' : m.sendStatus === 'unknown' ? '结果待核对' : (m.readStateText || '已发') }}
                    </span>
                    <button v-if="m.isMe && shouldRetryManualMessage(m)" type="button" class="xya-msg-retry-btn" title="平台明确未接收，可安全重试" @click="retrySendMessage(m)">
                      <svg viewBox="0 0 24 24" fill="none" width="14" height="14"><path d="M20 12a8 8 0 1 1-2.34-5.66" stroke="currentColor" stroke-width="2" /><path d="M20 4v6h-6" stroke="currentColor" stroke-width="2" /></svg>
                    </button>
                  </div>
                </div>
                <div v-if="m.isMe" class="xya-msg-avatar small self">{{ selfAvatarText }}</div>
              </div>
            </template>
          </div>

          <div class="xya-msg-chat-bottom">
            <div class="xya-msg-quick-tools">
              <button v-for="item in quickActions" :key="item" type="button" class="xya-msg-tool-chip" @click="handleQuickAction(item)">{{ item }}</button>
            </div>
          </div>
        </div>

        <footer v-if="selected" class="xya-msg-editor">
          <template v-if="isConversationDeleted(deletedConversations, selected)">
            <div class="xya-msg-deleted-hint">
              该会话已被删除或已过期，无法发送消息。
            </div>
          </template>
          <template v-else>
          <div class="xya-msg-editor-box">
            <textarea
              v-model="draft"
              placeholder="输入消息，Enter 发送，Shift+Enter 换行"
              :disabled="sending || isSystemConversation(selected) || !messageDataAvailable"
              @keydown="handleEditorKeydown"
            />
            <div v-if="showEmojiPanel" class="xya-msg-emoji-panel">
              <button
                v-for="item in emojiList"
                :key="item"
                type="button"
                class="xya-msg-emoji-btn"
                @click="insertEmoji(item)"
              >
                {{ item }}
              </button>
            </div>
            <div class="xya-msg-image-url-row">
              <input
                v-model="imageUrl"
                type="text"
                class="xya-msg-image-url-input"
                placeholder="粘贴图片URL，多个用逗号分隔"
                :disabled="sending || !messageDataAvailable"
              />
              <button
                type="button"
                class="xya-msg-image-url-send"
                :disabled="!imageUrl.trim() || sending || !messageDataAvailable"
                @click="sendImageByUrl"
              >
                发送图片
              </button>
            </div>
            <div class="xya-msg-editor-actions">
              <div class="xya-msg-editor-icons">
                <button type="button" class="xya-msg-icon-btn" aria-label="表情" @click="toggleEmojiPanel">
                  <svg viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="9" /><path d="M8.5 14.5c1 1 2.17 1.5 3.5 1.5s2.5-.5 3.5-1.5" /><circle cx="9" cy="10" r="1" /><circle cx="15" cy="10" r="1" /></svg>
                </button>
                <button type="button" class="xya-msg-icon-btn" aria-label="图片" :disabled="sending || !messageDataAvailable" @click="triggerImagePick">
                  <svg viewBox="0 0 24 24" fill="none"><rect x="4" y="5" width="16" height="14" rx="2" /><path d="m8 14 2.5-2.5L15 16l2-2 3 3" /><circle cx="9" cy="9" r="1" /></svg>
                </button>
                <input
                  ref="imageInput"
                  class="xya-msg-hidden-input"
                  type="file"
                  accept="image/*"
                  @change="handleImagePick"
                />
              </div>
              <button class="xya-msg-send-btn" type="button" :disabled="!canSend" @click="sendText">
                <svg viewBox="0 0 24 24" fill="none"><path d="M4 11.5 20 4l-4.5 16-3.5-6L4 11.5Z" /><path d="m11.8 14 8.2-10" /></svg>
                {{ sending ? '发送中' : '发送' }}
              </button>
            </div>
          </div>
          </template>
        </footer>
      </section>

      <aside class="xya-msg-detail-panel">
        <section class="xya-msg-card">
          <h3>相关商品</h3>
          <div class="xya-msg-product-card">
            <img class="xya-msg-product-large" :src="conversationThumb(selected)" alt="商品图片" />
            <div class="xya-msg-product-info">
              <strong>{{ selected?.product || '暂无关联商品' }}</strong>
              <b>{{ selected?.goodsPriceText || '-' }}</b>
              <span v-if="selected?.xyGoodsId">ID: {{ selected.xyGoodsId }}</span>
            </div>
          </div>
          <div class="xya-msg-card-actions">
            <button v-if="selected?.xyGoodsId" type="button" class="xya-msg-secondary-btn" @click="viewGoofishItem(selected.xyGoodsId)">查看商品</button>
            <button v-else type="button" class="xya-msg-secondary-btn" disabled>查看商品</button>
            <button v-if="selected?.xyGoodsId" type="button" class="xya-msg-secondary-btn" @click="sendGoodsLink(selected.xyGoodsId)">发送商品</button>
            <button v-else type="button" class="xya-msg-secondary-btn" disabled>发送商品</button>
          </div>
        </section>

        <section class="xya-msg-card">
          <h3 class="xya-msg-order-head">
            客户订单
            <span v-if="customerOrders.length" class="xya-msg-order-count">({{ customerOrders.length }})</span>
            <button
              type="button"
              class="xya-msg-order-refresh"
              :class="{ spinning: loadingCustomerOrders }"
              :disabled="!selected || loadingCustomerOrders"
              :title="loadingCustomerOrders ? '加载中…' : '刷新订单'"
              @click="refreshCustomerOrders"
            >&#x21bb;</button>
          </h3>
          <template v-if="selected">
            <div v-if="loadingCustomerOrders && !customerOrders.length" class="xya-msg-card-empty">正在加载客户订单…</div>
            <div v-else-if="customerOrdersError" class="xya-msg-card-empty xya-msg-order-error">{{ customerOrdersError }}</div>
            <div v-else-if="!customerOrders.length" class="xya-msg-card-empty">暂未匹配到该客户订单。</div>
            <div v-else class="xya-msg-order-list">
              <div v-for="order in customerOrders" :key="order.id" class="xya-msg-order-item">
                <div class="xya-msg-order-item-main">
                  <div class="xya-msg-order-cover-wrap">
                    <span class="xya-msg-order-cover-placeholder">&#128230;</span>
                    <img
                      v-if="resolveOrderCover(order)"
                      :src="resolveOrderCover(order)"
                      class="xya-msg-order-cover"
                      alt=""
                      @error="onOrderCoverError"
                    />
                  </div>
                  <div class="xya-msg-order-item-info">
                    <div class="xya-msg-order-title" :title="orderItemTitle(resolveOrderFirstItem(order))">
                      {{ orderItemTitle(resolveOrderFirstItem(order)) }}
                    </div>
                    <div class="xya-msg-order-meta">
                      <span class="xya-msg-order-amount">{{ formatOrderAmount(order.totalAmount) }}<template v-if="Number(order.quantityTotal) > 0"> &times; {{ order.quantityTotal }}</template></span>
                      <span class="xya-msg-order-status" :class="customerOrderStatusClass(order.orderStatus)">{{ customerOrderStatusText(order.orderStatus) }}</span>
                    </div>
                    <div class="xya-msg-order-no" :title="order.externalOrderId || order.id">订单号：{{ order.externalOrderId || order.id }}</div>
                    <div v-if="order.deliveryFailReason" class="xya-msg-order-fail">{{ order.deliveryFailReason }}</div>
                    <div class="xya-msg-order-time">{{ formatOrderTime(order.createTime) }}</div>
                  </div>
                </div>
                <div class="xya-msg-card-actions">
                  <button type="button" class="xya-msg-secondary-btn" :disabled="loadingOrderDetail" @click="viewOrderDetail(order.id)">订单详情</button>
                </div>
              </div>
            </div>
          </template>
          <div v-else class="xya-msg-card-empty">选中会话后可查看该客户订单。</div>
        </section>

        <section class="xya-msg-card">
          <h3>自动回复状态</h3>
          <div v-if="aiSettingsLoading && !aiSettingsAvailable" class="xya-msg-inline-state" role="status" aria-live="polite">
            正在读取 AI 自动回复状态...
          </div>
          <div v-else-if="!aiSettingsAvailable" class="xya-msg-inline-state error" role="alert">
            AI 自动回复状态暂不可用，已禁止切换以避免误操作。
            <button type="button" class="xya-msg-link-btn" :disabled="aiSettingsLoading" @click="loadAiCsSetting">重新读取</button>
          </div>
          <div v-else-if="aiSettingsRefreshNotice" class="xya-msg-inline-state" role="status" aria-live="polite">
            {{ aiSettingsRefreshNotice }}
          </div>
          <div class="xya-msg-status-list">
            <div class="xya-msg-status-row">
              <span>当前会话自动回复</span>
              <label class="xya-msg-switch">
                <input type="checkbox" :checked="aiAutoReplyEnabled" :disabled="aiSwitchLoading || aiSettingsLoading || !aiSettingsAvailable || !query.xianyuAccountId" @change="toggleAiAutoReply($event)">
                <span class="xya-msg-switch-slider"></span>
              </label>
            </div>
            <div class="xya-msg-status-row"><span>当前状态</span><b :class="aiSettingsAvailable && aiAutoReplyEnabled ? 'green' : 'gray'">{{ !aiSettingsAvailable ? '状态未知' : (aiAutoReplyEnabled ? 'AI 自动接待' : '人工接待') }}</b></div>
            <div class="xya-msg-status-row"><span>作用范围</span><b>{{ aiScopeLabel }}</b></div>
            <div class="xya-msg-status-row"><span>全局主开关</span><b :class="aiSettingsAvailable && aiGlobalEnabled ? 'green' : 'gray'">{{ !aiSettingsAvailable ? '状态未知' : (aiGlobalEnabled ? '已开启' : '已关闭') }}</b></div>
          </div>

          <!-- 会话级自动回复状态机（3 态：运行中 / 人工暂停中 / 手动关闭） -->
          <div v-if="aiSettingsAvailable && aiAutoReplyEnabled && conversationAutoReplyLoaded" class="xya-msg-conv-state">
            <div class="xya-msg-status-row">
              <span>会话级状态</span>
              <b :class="conversationAutoReplyStatusClass">{{ conversationAutoReplyStatusText }}</b>
            </div>
            <div class="xya-msg-status-row">
              <span>会话级开关</span>
              <button
                type="button"
                class="xya-msg-conv-toggle-btn"
                :class="conversationAutoReplyButtonClass"
                :disabled="conversationAutoReplyLoading || !selected?.id"
                :title="conversationAutoReplyButtonTitle"
                @click="toggleConversationAutoReply"
              >{{ conversationAutoReplyButtonText }}</button>
            </div>
            <div v-if="conversationAutoReplyHint" class="xya-msg-conv-hint">{{ conversationAutoReplyHint }}</div>
          </div>
          <button type="button" class="xya-msg-secondary-btn wide" @click="emit('navigate', 'settings-ai-cs')">配置自动回复</button>
        </section>

        <section class="xya-msg-card">
          <div class="xya-msg-card-head">
            <h3>快捷回复模板</h3>
            <button type="button" class="xya-msg-link-btn" @click="showTemplateModal = true">更多模板</button>
          </div>
          <div v-if="!templatesAvailable" class="subtle" style="padding:14px 4px">
            快捷回复服务暂不可用
            <button type="button" class="xya-msg-mini-btn" :disabled="templatesLoading" @click="loadQuickTemplates">{{ templatesLoading ? '重试中...' : '重试' }}</button>
          </div>
          <div v-else-if="quickTemplates.length === 0" class="subtle" style="padding:14px 4px">
            暂无快捷回复模板
          </div>
          <div v-else class="xya-msg-template-list">
            <button v-for="item in quickTemplates" :key="item.id || item.title" type="button" class="xya-msg-template-item" :title="item.text" @click="insertTemplate(item)">
              <strong>{{ item.title }}</strong>
              <span>{{ item.text }}</span>
            </button>
          </div>
        </section>
      </aside>

      <!-- 快捷回复模板管理弹窗 -->
      <div v-if="showTemplateModal" class="xya-msg-modal-mask" @click.self="showTemplateModal = false">
        <div class="xya-msg-modal">
          <div class="xya-msg-modal-head">
            <h3>管理快捷回复模板</h3>
            <button type="button" class="xya-msg-icon-btn" @click="showTemplateModal = false">关闭</button>
          </div>
          <div class="xya-msg-modal-body">
            <div class="xya-msg-template-edit-row">
              <input v-model="editingTemplate.title" type="text" placeholder="模板标题（如：亲切问候）" class="xya-msg-modal-input" />
              <textarea v-model="editingTemplate.content" placeholder="模板内容（如：您好，有什么可以帮您？）" class="xya-msg-modal-textarea" rows="2"></textarea>
              <div class="xya-msg-template-edit-actions">
                <button v-if="editingTemplate.id" type="button" class="xya-msg-mini-btn" @click="resetTemplateEdit">取消编辑</button>
                <button type="button" class="xya-msg-send-btn" :disabled="!templatesAvailable || !editingTemplate.title?.trim() || !editingTemplate.content?.trim()" @click="saveTemplate">
                  {{ editingTemplate.id ? '更新' : '添加' }}
                </button>
              </div>
            </div>
            <div class="xya-msg-template-manage-list">
              <div v-for="item in allTemplates" :key="item.id" class="xya-msg-template-manage-item">
                <div class="xya-msg-template-manage-info">
                  <strong>{{ item.title }}</strong>
                  <span>{{ item.content }}</span>
                </div>
                <div class="xya-msg-template-manage-actions">
                  <button type="button" class="xya-msg-mini-btn" @click="editTemplate(item)">编辑</button>
                  <button type="button" class="xya-msg-mini-btn danger" @click="deleteTemplate(item.id)">删除</button>
                </div>
              </div>
              <div v-if="!templatesAvailable" class="subtle" style="padding:20px;text-align:center">
                模板服务暂不可用，请重试加载
              </div>
              <div v-else-if="allTemplates.length === 0" class="subtle" style="padding:20px;text-align:center">
                暂无模板，请在上方添加
</div>
            </div>
          </div>
        </div>
      </div>

      <div v-if="previewImageUrl" class="xya-msg-modal-mask xya-msg-image-preview-mask" @click.self="closeImagePreview">
        <div class="xya-msg-image-preview">
          <div class="xya-msg-image-preview-actions">
            <button type="button" class="xya-msg-link-btn" @click="openImagePreviewInNewTab">新窗口打开</button>
            <button type="button" class="xya-msg-icon-btn" @click="closeImagePreview">关闭</button>
          </div>
          <img :src="previewImageUrl" class="xya-msg-image-preview-img" alt="聊天图片预览" />
        </div>
      </div>

      <div v-if="showOrderDetailModal" class="xya-msg-modal-mask" @click.self="closeOrderDetailModal">
        <div class="xya-msg-modal xya-msg-order-modal">
          <div class="xya-msg-modal-head">
            <h3>订单详情</h3>
            <button type="button" class="xya-msg-icon-btn" @click="closeOrderDetailModal">关闭</button>
          </div>
          <div class="xya-msg-modal-body xya-msg-order-modal-body">
            <div v-if="loadingOrderDetail" class="xya-msg-card-empty">正在加载订单详情…</div>
            <div v-else-if="orderDetailError" class="xya-msg-card-empty xya-msg-order-error">{{ orderDetailError }}</div>
            <template v-else-if="orderDetailData">
              <div class="xya-msg-order-detail-section">
                <div class="xya-msg-order-detail-row">
                  <span class="xya-msg-order-detail-label">订单号</span>
                  <strong class="xya-msg-order-detail-value">{{ orderDetailData.externalOrderId || orderDetailData.id || '-' }}</strong>
                </div>
                <div class="xya-msg-order-detail-row">
                  <span class="xya-msg-order-detail-label">订单状态</span>
                  <span class="xya-msg-order-status" :class="customerOrderStatusClass(orderDetailData.orderStatus)">{{ customerOrderStatusText(orderDetailData.orderStatus) }}</span>
                </div>
                <div class="xya-msg-order-detail-row">
                  <span class="xya-msg-order-detail-label">商品总额</span>
                  <strong class="xya-msg-order-detail-value">{{ formatOrderAmount(orderDetailData.totalAmount) }}</strong>
                </div>
                <div class="xya-msg-order-detail-row">
                  <span class="xya-msg-order-detail-label">买家昵称</span>
                  <span class="xya-msg-order-detail-value">{{ orderDetailData.buyerName || '-' }}</span>
                </div>
                <div class="xya-msg-order-detail-row">
                  <span class="xya-msg-order-detail-label">买家ID</span>
                  <span class="xya-msg-order-detail-value">{{ orderDetailData.buyerId || '-' }}</span>
                </div>
                <div class="xya-msg-order-detail-row">
                  <span class="xya-msg-order-detail-label">下单时间</span>
                  <span class="xya-msg-order-detail-value">{{ formatOrderTime(orderDetailData.createTime) }}</span>
                </div>
                <div v-if="orderDetailData.payTime" class="xya-msg-order-detail-row">
                  <span class="xya-msg-order-detail-label">付款时间</span>
                  <span class="xya-msg-order-detail-value">{{ formatOrderTime(orderDetailData.payTime) }}</span>
                </div>
                <div v-if="orderDetailData.shipTime" class="xya-msg-order-detail-row">
                  <span class="xya-msg-order-detail-label">发货时间</span>
                  <span class="xya-msg-order-detail-value">{{ formatOrderTime(orderDetailData.shipTime) }}</span>
                </div>
                <div v-if="orderDetailData.deliveryMethod" class="xya-msg-order-detail-row">
                  <span class="xya-msg-order-detail-label">发货方式</span>
                  <span class="xya-msg-order-detail-value">{{ orderDetailData.deliveryMethod }}</span>
                </div>
                <div v-if="orderDetailData.deliveryStatus" class="xya-msg-order-detail-row">
                  <span class="xya-msg-order-detail-label">发货状态</span>
                  <span class="xya-msg-order-detail-value">{{ orderDetailData.deliveryStatus }}</span>
                </div>
                <div v-if="orderDetailData.deliveryFailReason" class="xya-msg-order-detail-row">
                  <span class="xya-msg-order-detail-label">失败原因</span>
                  <span class="xya-msg-order-detail-value xya-msg-order-fail">{{ orderDetailData.deliveryFailReason }}</span>
                </div>
              </div>
              <div v-if="orderDetailData.items && orderDetailData.items.length" class="xya-msg-order-detail-section">
                <h4 class="xya-msg-order-detail-subtitle">商品明细</h4>
                <div v-for="item in orderDetailData.items" :key="item.id" class="xya-msg-order-detail-item">
                  <div class="xya-msg-order-detail-item-title">{{ item.goodsTitle || '-' }}</div>
                  <div class="xya-msg-order-detail-item-meta">
                    <span>&yen;{{ Number(item.goodsPrice || 0).toFixed(2) }}</span>
                    <span>&times;{{ Math.max(Number(item.goodsCount) || 1, 1) }}</span>
                    <span v-if="item.specSummary || (item.specName && item.specValue)" class="xya-msg-order-detail-spec">{{ item.specSummary || item.specName + ': ' + item.specValue }}</span>
                  </div>
                </div>
              </div>
            </template>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { openExternalUrl } from '../utils/externalUrl.js'
import { getAccounts } from '../api/accounts.js'
import { onlineConversations, messageContext, updateConversationStatus, markConversationRead } from '../api/messages.js'
import { getConversations } from '../api/conversations.js'
import { uploadImage } from '../api/misc.js'
import { sendImageMessage, sendMessage, startWebSocket, websocketStatus } from '../api/websocket.js'
import { accountName, shortText, timeText } from '../utils/format.js'
import { recordsOf, unwrap } from '../utils/apiData.js'
import { confirmAction } from '../utils/confirmAction.js'
import { useDebouncedRef } from '../composables/useDebouncedRef.js'
import { getAutoReplyRules } from '../api/autoReply.js'
import {
  listQuickReplyTemplates, saveQuickReplyTemplate, deleteQuickReplyTemplate
} from '../api/quickReply.js'
import {
  applyAvatarIdentityResults,
  applyConversationUnreadState,
  compareConversationStatus,
  confirmTemplateDeletion,
  createAvatarLookupState,
  createLatestRequestGuard,
  createMessageBackgroundRequestConfig,
  createSingleFlightTask,
  didPreservedConversationIdentityChange,
  disposeAvatarLookupState,
  extractImageMessageUrls,
  extractMessageDisplayText,
  findConversationMatchIndex,
  findConversationByIdentity,
  findPreservedConversation,
  getConversationRecordId,
  getConversationIdentityKey,
  isConversationDeleted,
  matchesAccountSelection,
  mergeSelectedConversationSnapshot,
  parseImageUrlBatchInput,
  parseMessageTimestamp,
  planAvatarLookups,
  pruneDeletedConversationMarks,
  resolveConversationOnlineStatus,
  resolveManualMessageError,
  resolveManualMessageOutcome,
  resolveRealtimeMode,
  resolveRetryMessageAction,
  resetAvatarLookupState,
  resolveImageBatchPreviewState,
  resolveAccountSwitchState,
  resolveConversationGoodsTitle,
  resolveBackgroundRefreshSnapshot,
  shouldApplyContextLoadResult,
  shouldApplyConversationLoadResult,
  shouldEnableMainComposerSend,
  shouldRetryManualMessage,
  shouldMarkConversationAsRead,
  settleAvatarLookups,
  sortConversationSnapshots,
  sortMessagesByTime,
  isSameConversationByPayload
} from '../utils/messagesPageState.js'
import { getBusinessSettings } from '../api/businessSettings.js'
import {
  getAutoReplyScopeProducts,
  updateProductAutoReplyScope,
  updateAccountAutoReplyScope,
  batchUpdateAutoReplyScope,
  getAutoReplyScopeStatus,
  toggleConversationAutoReply as toggleConversationAutoReplyApi,
  getConversationAutoReplyStatus,
} from '../api/autoReplyScope.js'
import { queryUserAvatars } from '../api/messages.js'
import { getCustomerOrders, getOrderDetail } from '../api/orders.js'
import { createRequestGate } from '../utils/requestLifecycle.js'

const emit = defineEmits(['navigate'])

const defaultThumb = '/xya/utility_icons/utility_icons_012.png'
const DEFAULT_VISIBLE_CONVERSATIONS = 15
const AVATAR_RETRY_BASE_MS = 30_000
const AVATAR_RETRY_MAX_MS = 5 * 60_000
const MESSAGE_BACKGROUND_REQUEST_CONFIG = createMessageBackgroundRequestConfig()
const emojiList = ['😊', '😂', '😄', '😉', '😍', '🥹', '😆', '😁', '👏', '🎉', '❤️', '😭']
const accounts = ref([])
const conversations = ref([])
const selected = ref(null)

// === 客户订单板块状态 ===
const customerOrders = ref([])
const loadingCustomerOrders = ref(false)
const customerOrdersError = ref('')
const customerOrdersGate = createRequestGate()
const showOrderDetailModal = ref(false)
const orderDetailData = ref(null)
const loadingOrderDetail = ref(false)
const orderDetailError = ref('')
const CUSTOMER_ORDER_STATUS_META = {
  0: { text: '待付款', className: 'warning' },
  1: { text: '已付款', className: 'info' },
  2: { text: '待发货', className: 'warning' },
  3: { text: '已发货', className: 'success' },
  4: { text: '已完成', className: 'success' },
  5: { text: '已关闭', className: 'muted' },
}

// === 客户订单板块函数 ===
function customerOrderStatusMeta(status) {
  return CUSTOMER_ORDER_STATUS_META[Number(status)] || { text: String(status ?? '-'), className: 'muted' }
}

function customerOrderStatusText(status) {
  return customerOrderStatusMeta(status).text
}

function customerOrderStatusClass(status) {
  return customerOrderStatusMeta(status).className
}

function formatOrderAmount(amount) {
  const value = Number(amount)
  if (!Number.isFinite(value) || value <= 0) return '--'
  return '\u00a5' + value.toFixed(2)
}

function formatOrderTime(time) {
  return timeText(time)
}

function resolveOrderCover(order) {
  const items = Array.isArray(order?.items) ? order.items : []
  for (const item of items) {
    const image = item?.goodsImage
    if (image && typeof image === 'string' && image.trim()) {
      return normalizeDisplayImage(image)
    }
  }
  return ''
}

function resolveOrderFirstItem(order) {
  const items = Array.isArray(order?.items) ? order.items : []
  return items.length ? items[0] : null
}

function orderItemTitle(item) {
  const title = item?.goodsTitle
  if (title && String(title).trim()) return title
  const externalId = item?.externalGoodsId
  return externalId ? '商品 ' + externalId : '未命名商品'
}

function onOrderCoverError(event) {
  const target = event?.target
  if (!target) return
  target.style.display = 'none'
  const placeholder = target.parentElement?.querySelector('.xya-msg-order-cover-placeholder')
  if (placeholder) placeholder.style.display = 'inline-flex'
}

function resolveCustomerBuyerId(conversation) {
  return normalizePeerUserId(conversation?.peerUserId || conversation?.peerExternalUid || conversation?.externalBuyerId || '')
}

async function loadCustomerOrders(silent = false) {
  const conversation = selected.value
  const buyerId = resolveCustomerBuyerId(conversation)
  const accountId = Number(conversation?.xianyuAccountId || conversation?.accountId || 0) || undefined
  if (!buyerId || !accountId) {
    customerOrders.value = []
    customerOrdersError.value = ''
    return
  }
  if (!silent) {
    loadingCustomerOrders.value = true
    customerOrdersError.value = ''
  }
  const gate = customerOrdersGate.begin()
  try {
    const res = await getCustomerOrders(accountId, buyerId, 10)
    if (!customerOrdersGate.isCurrent(gate)) return
    const data = unwrap(res)
    const records = Array.isArray(data?.records) ? data.records : (Array.isArray(data?.list) ? data.list : (Array.isArray(data) ? data : []))
    customerOrders.value = records
    customerOrdersError.value = ''
  } catch (e) {
    if (!customerOrdersGate.isCurrent(gate)) return
    customerOrders.value = []
    customerOrdersError.value = (e && e.message) || '获取客户订单失败'
  } finally {
    if (customerOrdersGate.isCurrent(gate)) {
      loadingCustomerOrders.value = false
    }
  }
}

async function refreshCustomerOrders() {
  await loadCustomerOrders(false)
}

async function viewOrderDetail(orderId) {
  if (!orderId) return
  loadingOrderDetail.value = true
  orderDetailError.value = ''
  orderDetailData.value = null
  showOrderDetailModal.value = true
  try {
    const res = await getOrderDetail(orderId)
    const data = unwrap(res)
    orderDetailData.value = data
  } catch (e) {
    orderDetailError.value = (e && e.message) || '加载订单详情失败'
  } finally {
    loadingOrderDetail.value = false
  }
}

function closeOrderDetailModal() {
  showOrderDetailModal.value = false
}

// 选中会话变化时加载客户订单
watch(() => {
  const conv = selected.value
  return conv ? (conv.sid || '') + '|' + resolveCustomerBuyerId(conv) : ''
}, () => {
  if (!selected.value) {
    customerOrders.value = []
    customerOrdersError.value = ''
    return
  }
  loadCustomerOrders(false).catch(() => {})
})

const deletedConversations = ref(new Set())
const contextMessages = ref([])
const conversationsAvailable = ref(false)
const contextAvailable = ref(false)
const draft = ref('')
const imageUrl = ref('')
const keyword = ref('')
const debouncedKeyword = useDebouncedRef(keyword, 300)
const conversationFilter = ref('all')
const sortDesc = ref(true)
const loading = ref(false)
const conversationRefreshing = ref(false)
const contextLoading = ref(false)
const contextRefreshing = ref(false)
const contextLoadingMore = ref(false)
const sending = ref(false)
const sendingImage = ref(false)
const error = ref('')
const conversationRefreshNotice = ref('')
const contextRefreshNotice = ref('')
const showEmojiPanel = ref(false)
const previewImageUrl = ref('')
const statusUpdating = ref(false)
const visibleConversationCount = ref(DEFAULT_VISIBLE_CONVERSATIONS)
const messagesContainer = ref(null)
const imageInput = ref(null)
const events = ref([])
const hasMoreContext = ref(false)
const query = reactive({ xianyuAccountId: '', pageSize: 20 })
const contextQuery = reactive({ limit: 50, offset: 0 })
// 会话列表真分页状态（cursor 分页，模仿目标项目 ChatNew.tsx）
const conversationCursor = ref(null)     // 下一页的 cursor
const conversationHasMore = ref(false)   // 是否还有更多会话可加载
const loadingMoreConversations = ref(false)  // 是否正在加载下一页
const userInfoCacheRef = ref({})          // 当前账号 cid -> {avatar, nick}，切换账号时清空
let avatarLookupState = createAvatarLookupState('')
let conversationLoadRequestId = 0
let contextLoadRequestId = 0
let sseDebounceTimer = null

function invalidateContextLoads() {
  contextLoadRequestId += 1
  contextLoading.value = false
  contextRefreshing.value = false
  contextLoadingMore.value = false
}

function invalidateMessageLoads() {
  conversationLoadRequestId += 1
  loading.value = false
  conversationRefreshing.value = false
  loadingMoreConversations.value = false
  invalidateContextLoads()
}

// ---- 实时消息与轮询配置 ----
const POLL_INTERVAL_SSE_HEALTHY = 30000   // SSE 健康时降低轮询频率到 30s（仅做 WS 健康检查）
const POLL_INTERVAL_FALLBACK = 2000       // SSE 不健康时的快速轮询间隔
const SSE_STALE_TIMEOUT = 15000
let pollingTimer = null
const pollingActive = ref(false)       // 轮询是否正在运行
const lastSseActivity = ref(0)
const sseHealthy = ref(false)
const pollRequestGuard = createLatestRequestGuard()
let visibilityChangeHandler = null
let lastWsHealthCheck = 0              // 上次 WebSocket 健康检查时间戳
let initialMountActive = true
// ---- end ----

const quickActions = ['快捷回复', '常用语库', '邀请下单', '结束会话']
const quickTemplates = ref([])
const allTemplates = ref([])
const templatesAvailable = ref(false)
const templatesLoading = ref(false)
const showTemplateModal = ref(false)
const editingTemplate = reactive({ id: null, title: '', content: '' })

const realtimeMode = computed(() => resolveRealtimeMode({
  sseHealthy: sseHealthy.value,
  pollingActive: pollingActive.value
}))
const backgroundRefreshing = computed(() => conversationRefreshing.value || contextRefreshing.value)
const refreshNotice = computed(() => [conversationRefreshNotice.value, contextRefreshNotice.value].filter(Boolean).join('；'))
const messageDataAvailable = computed(() => conversationsAvailable.value && contextAvailable.value)

// AI 自动回复状态
const aiAutoReplyEnabled = ref(false)

// 会话级自动回复状态机（同步商业版 V1.13）
// autoReplyPaused: 0=运行中 1=已暂停
// autoReplyManualDisabled: 0=可自动恢复 1=手动关闭（禁止自动恢复，仅用户手动开启）
// runningEnabled: 综合账号级/全局开关 + 会话级暂停后的实际运行状态
const conversationAutoReplyState = ref({
  autoReplyPaused: 0,
  autoReplyManualDisabled: 0,
  lastManualReplyAt: 0,
  lastAutoReplyAt: 0,
  effectiveEnabled: null,
  runningEnabled: null,
  pausedReason: '',
})
const conversationAutoReplyLoaded = ref(false)
const conversationAutoReplyLoading = ref(false)

const conversationAutoReplyStatusText = computed(() => {
  const s = conversationAutoReplyState.value
  if (s.autoReplyManualDisabled === 1) return '已手动关闭'
  if (s.autoReplyPaused === 1) return '人工暂停中'
  if (s.runningEnabled === true) return '运行中'
  if (s.runningEnabled === false) return '已关闭'
  return '状态未知'
})

const conversationAutoReplyStatusClass = computed(() => {
  const s = conversationAutoReplyState.value
  if (s.autoReplyManualDisabled === 1) return 'danger-text'
  if (s.autoReplyPaused === 1) return 'warning-text'
  if (s.runningEnabled === true) return 'green'
  return 'gray'
})

const conversationAutoReplyButtonText = computed(() => {
  const s = conversationAutoReplyState.value
  if (s.autoReplyManualDisabled === 1) return '手动关闭·点击开启'
  if (s.autoReplyPaused === 1) return '人工暂停中·点击开启'
  if (s.runningEnabled === true) return '运行中·点击关闭'
  if (s.runningEnabled === false) return '已关闭·点击开启'
  return '切换会话级自动回复'
})

const conversationAutoReplyButtonClass = computed(() => {
  const s = conversationAutoReplyState.value
  if (s.autoReplyManualDisabled === 1) return 'is-danger'
  if (s.autoReplyPaused === 1) return 'is-warning'
  if (s.runningEnabled === true) return 'is-success'
  return 'is-default'
})

const conversationAutoReplyButtonTitle = computed(() => {
  const s = conversationAutoReplyState.value
  if (s.autoReplyManualDisabled === 1) return '已手动关闭，仅可手动开启（不会自动恢复）'
  if (s.autoReplyPaused === 1) return '人工回复后自动暂停，1分钟后或买家发送"开启自动回复"将自动恢复'
  if (s.runningEnabled === true) return '自动回复运行中，点击将手动关闭'
  return '点击开启会话级自动回复'
})

const conversationAutoReplyHint = computed(() => {
  const s = conversationAutoReplyState.value
  if (s.autoReplyPaused === 1 && s.autoReplyManualDisabled === 0 && s.lastManualReplyAt) {
    const elapsed = Date.now() - Number(s.lastManualReplyAt || 0)
    const remain = Math.max(0, 60 - Math.floor(elapsed / 1000))
    if (remain > 0) return `人工暂停中，${remain} 秒后买家发新消息将自动恢复`
  }
  return ''
})
const aiSwitchLoading = ref(false)
const aiSettingsLoading = ref(false)
const aiSettingsAvailable = ref(false)
const aiSettingsRefreshNotice = ref('')
const aiGlobalEnabled = ref(false)
const aiAccountScopes = ref({})
const aiScopeProducts = ref([])
const aiSettingsRequestGuard = createLatestRequestGuard()
const aiMutationRequestGuard = createLatestRequestGuard()

const aiScopeLabel = computed(() => {
  if (selected.value?.xyGoodsId) return '当前商品'
  if (query.xianyuAccountId) return '当前账号'
  return '未选择'
})

async function loadQuickTemplates() {
  templatesLoading.value = true
  try {
    const res = await listQuickReplyTemplates({ size: 100 })
    const list = res.data?.records || res.data || []
    allTemplates.value = list.map(t => ({ id: t.id, title: t.title || '', content: t.content || '', text: t.content || '' }))
    // 侧边栏只显示前 6 条快捷模板
    quickTemplates.value = allTemplates.value.slice(0, 6)
    templatesAvailable.value = true
  } catch {
    if (import.meta.env.DEV) console.warn('[loadQuickTemplates] failed')
    // 降级：尝试使用旧的自动回复规则接口
    try {
      const res = await getAutoReplyRules({ size: 50 })
      const rules = res.data?.records || res.data || []
      const list = rules.map(r => ({ id: r.id, title: r.ruleName || '回复', content: r.replyContent || '', text: r.replyContent || '' })).filter(t => t.content)
      allTemplates.value = list
      quickTemplates.value = list.slice(0, 6)
      templatesAvailable.value = true
    } catch {
      allTemplates.value = []
      quickTemplates.value = []
      templatesAvailable.value = false
    }
  } finally {
    templatesLoading.value = false
  }
}

function insertTemplate(item) {
  const text = item.content || item.text || ''
  if (!text) return
  // 追加到输入框末尾，便于组合多条内容
  draft.value = draft.value ? `${draft.value}\n${text}` : text
}

function editTemplate(item) {
  editingTemplate.id = item.id
  editingTemplate.title = item.title
  editingTemplate.content = item.content
}

function resetTemplateEdit() {
  editingTemplate.id = null
  editingTemplate.title = ''
  editingTemplate.content = ''
}

async function saveTemplate() {
  const title = (editingTemplate.title || '').trim()
  const content = (editingTemplate.content || '').trim()
  if (!title || !content) return
  try {
    await saveQuickReplyTemplate({
      id: editingTemplate.id || null,
      title,
      content
    })
    resetTemplateEdit()
    await loadQuickTemplates()
  } catch (e) {
    if (import.meta.env.DEV) console.error('[saveTemplate] failed')
    alert('保存失败: ' + (e.message || '网络错误'))
  }
}

async function deleteTemplate(id) {
  try {
    const deleted = await confirmTemplateDeletion(
      () => confirmAction({ title: '确认删除此模板？', description: '删除后模板内容将无法恢复。', dangerous: true }),
      deleteQuickReplyTemplate,
      id
    )
    if (!deleted) return
    await loadQuickTemplates()
  } catch (e) {
    if (import.meta.env.DEV) console.error('[deleteTemplate] failed')
    alert('删除失败: ' + (e.message || '网络错误'))
  }
}


// 查看闲鱼官方商品页面
function viewGoofishItem(itemId) {
  if (!itemId) return
  openExternalUrl(`https://www.goofish.com/item?itemId=${encodeURIComponent(itemId)}`)
}

// 发送商品：将商品链接填入输入框
function sendGoodsLink(itemId) {
  if (!itemId) return
  const link = `https://www.goofish.com/item?itemId=${itemId}`
  draft.value = draft.value ? `${draft.value}\n${link}` : link
}

async function loadAiCsSetting({ background = false } = {}) {
  const requestedAccountId = Number(query.xianyuAccountId || 0)
  const requestIdentity = `account:${requestedAccountId}`
  const requestToken = aiSettingsRequestGuard.begin(requestIdentity)
  const hadSnapshot = aiSettingsAvailable.value
  aiSettingsLoading.value = true
  aiSettingsRefreshNotice.value = ''
  const requestConfig = background ? createMessageBackgroundRequestConfig() : {}
  const isCurrentRequest = () => aiSettingsRequestGuard.isCurrent(
    requestToken,
    `account:${Number(query.xianyuAccountId || 0)}`
  )
  try {
    const [settingRes, scopeRes, productsRes] = await Promise.all([
      getBusinessSettings('ai-customer-service', requestConfig),
      getAutoReplyScopeStatus(undefined, requestConfig),
      requestedAccountId ? getAutoReplyScopeProducts(requestedAccountId, requestConfig) : Promise.resolve({ data: { items: [] } })
    ])
    if (!isCurrentRequest()) return { loaded: false, stale: true }
    const config = settingRes?.data ?? settingRes ?? {}
    const scopeData = scopeRes?.data ?? scopeRes ?? {}
    const productData = productsRes?.data ?? productsRes ?? {}
    aiGlobalEnabled.value = Boolean(config.enabled) && scopeData?.global_enabled !== false
    aiAccountScopes.value = scopeData?.account_scopes || {}
    aiScopeProducts.value = productData?.items || []
    aiSettingsAvailable.value = true
    refreshAiScopeState()
    return { loaded: true, stale: false }
  } catch {
    if (!isCurrentRequest()) return { loaded: false, stale: true }
    if (import.meta.env.DEV) console.warn('[loadAiCsSetting] failed')
    if (hadSnapshot) {
      aiSettingsRefreshNotice.value = 'AI 自动回复状态刷新失败，已保留上次确认状态；稍后将自动重试。'
    } else {
      aiSettingsAvailable.value = false
      aiAutoReplyEnabled.value = false
    }
    return { loaded: false, stale: false }
  } finally {
    if (isCurrentRequest()) aiSettingsLoading.value = false
  }
}

function refreshAiScopeState() {
  if (!aiSettingsAvailable.value) {
    aiAutoReplyEnabled.value = false
    return
  }
  const goodsId = String(selected.value?.xyGoodsId || '')
  if (goodsId) {
    const product = aiScopeProducts.value.find(item => String(item.goodsId || '') === goodsId)
    aiAutoReplyEnabled.value = Boolean(product?.effective_enabled)
    return
  }
  const accountId = String(query.xianyuAccountId || '')
  if (accountId) {
    aiAutoReplyEnabled.value = aiGlobalEnabled.value && aiAccountScopes.value[accountId] === true
    // 账号级有效后，加载会话级状态
    loadConversationAutoReplyStatus()
    return
  }
  aiAutoReplyEnabled.value = false
  conversationAutoReplyLoaded.value = false
}

async function toggleAiAutoReply(event) {
  if (aiSwitchLoading.value || aiSettingsLoading.value || !aiSettingsAvailable.value) {
    if (event?.target) event.target.checked = aiAutoReplyEnabled.value
    return
  }
  const newValue = !aiAutoReplyEnabled.value
  const requestedAccountId = Number(query.xianyuAccountId || 0)
  const requestedGoodsId = String(selected.value?.xyGoodsId || '')
  const requestIdentity = `account:${requestedAccountId}|goods:${requestedGoodsId || 'account'}`
  const requestToken = aiMutationRequestGuard.begin(requestIdentity)
  const isCurrentRequest = () => aiMutationRequestGuard.isCurrent(
    requestToken,
    `account:${Number(query.xianyuAccountId || 0)}|goods:${String(selected.value?.xyGoodsId || '') || 'account'}`
  )
  aiSwitchLoading.value = true
  try {
    if (newValue && !await ensureAiCustomerServiceEnabled()) {
      // A cancelled guard must not leave the native checkbox visually enabled.
      if (event?.target) event.target.checked = aiAutoReplyEnabled.value
      return
    }
    if (!isCurrentRequest()) return
    if (requestedGoodsId) {
      const product = aiScopeProducts.value.find(item => String(item.goodsId || '') === requestedGoodsId)
      if (!product?.id) throw new Error('未找到当前商品的本地记录')
      await updateProductAutoReplyScope(product.id, newValue)
    } else if (requestedAccountId) {
      await updateAccountAutoReplyScope(requestedAccountId, newValue)
    } else {
      throw new Error('请先选择账号')
    }
    if (!isCurrentRequest()) return
    await loadAiCsSetting({ background: true })
  } catch (e) {
    if (isCurrentRequest()) alert('切换失败: ' + (e.message || '网络错误'))
  } finally {
    if (isCurrentRequest()) aiSwitchLoading.value = false
  }
}

// 加载会话级自动回复状态（在选中会话后调用）
async function loadConversationAutoReplyStatus() {
  const accountId = Number(query.xianyuAccountId || 0)
  const sid = String(selected.value?.sid || selected.value?.sId || '')
  const peerUserId = String(selected.value?.peerUserId || selected.value?.peerId || selected.value?.buyerId || '')
  if (!accountId || (!sid && !peerUserId)) {
    conversationAutoReplyLoaded.value = false
    return
  }
  conversationAutoReplyLoading.value = true
  try {
    const res = await getConversationAutoReplyStatus({
      accountId,
      sid,
      peerUserId,
    })
    const data = res?.data ?? res
    if (data && typeof data === 'object') {
      conversationAutoReplyState.value = {
        autoReplyPaused: Number(data.autoReplyPaused ?? 0),
        autoReplyManualDisabled: Number(data.autoReplyManualDisabled ?? 0),
        lastManualReplyAt: Number(data.lastManualReplyAt || 0),
        lastAutoReplyAt: Number(data.lastAutoReplyAt || 0),
        effectiveEnabled: data.effectiveEnabled ?? null,
        runningEnabled: data.runningEnabled ?? null,
        pausedReason: data.pausedReason || '',
      }
      conversationAutoReplyLoaded.value = true
    } else {
      conversationAutoReplyLoaded.value = false
    }
  } catch (e) {
    conversationAutoReplyLoaded.value = false
  } finally {
    conversationAutoReplyLoading.value = false
  }
}

// 切换会话级自动回复（手动开启/关闭）
async function toggleConversationAutoReply() {
  if (conversationAutoReplyLoading.value || !selected.value?.id) return
  const accountId = Number(query.xianyuAccountId || 0)
  const sid = String(selected.value?.sid || selected.value?.sId || '')
  const peerUserId = String(selected.value?.peerUserId || selected.value?.peerId || selected.value?.buyerId || '')
  if (!accountId || (!sid && !peerUserId)) {
    alert('会话信息缺失，无法切换会话级自动回复')
    return
  }
  const s = conversationAutoReplyState.value
  // 当前为运行中 → 手动关闭；否则 → 手动开启
  const newEnabled = !(s.runningEnabled === true)
  conversationAutoReplyLoading.value = true
  try {
    const res = await toggleConversationAutoReplyApi({
      accountId,
      sid,
      peerUserId,
      enabled: newEnabled,
    })
    const data = res?.data ?? res
    if (data && typeof data === 'object') {
      conversationAutoReplyState.value = {
        ...conversationAutoReplyState.value,
        autoReplyPaused: Number(data.autoReplyPaused ?? (newEnabled ? 0 : 1)),
        autoReplyManualDisabled: Number(data.autoReplyManualDisabled ?? (newEnabled ? 0 : 1)),
        effectiveEnabled: conversationAutoReplyState.value.effectiveEnabled,
        runningEnabled: newEnabled ? (conversationAutoReplyState.value.effectiveEnabled ?? true) : false,
        pausedReason: newEnabled ? '' : 'manual_disable',
      }
      if (!newEnabled && conversationAutoReplyState.value.lastManualReplyAt === 0) {
        // 手动关闭不影响 lastManualReplyAt
      }
    }
  } catch (e) {
    alert('切换会话级自动回复失败: ' + (e.message || '网络错误'))
  } finally {
    conversationAutoReplyLoading.value = false
  }
}

async function ensureAiCustomerServiceEnabled() {
  if (aiGlobalEnabled.value) return true
  const confirmed = await confirmAction({
    title: 'AI 客服尚未开启',
    description: '请先在「AI 客服配置」中开启主开关，当前会话的自动回复开关才会生效。是否前往配置？',
    confirmText: '前往配置'
  })
  if (confirmed) emit('navigate', 'settings-ai-cs')
  return false
}

const accountLabel = computed(() => accountName(accounts.value.find(a => a.id === Number(query.xianyuAccountId)) || {}))
const selfAvatarText = computed(() => avatarText(accountLabel.value || '我'))

function selectedAccountRecord() {
  return accounts.value.find(a => Number(a?.id) === Number(query.xianyuAccountId)) || null
}

function isAccountAuthUsable(account) {
  if (!account || typeof account !== 'object') return false
  if (account.authUsable === true) return true
  return Number(account.cookieStatus || 0) === 1 && String(account.loginStatusCode || '').toUpperCase() === 'OK'
}

async function ensureSelectedAccountWsReady() {
  const accountId = Number(query.xianyuAccountId || 0)
  if (!accountId) return false
  const account = selectedAccountRecord()
  if (!isAccountAuthUsable(account)) return false

  const wsStatusRes = await websocketStatus(accountId)
  const status = unwrap(wsStatusRes.data)
  if (status?.connected) return true

  await startWebSocket(accountId)
  return false
}

function normalizeSid(value) {
  const v = String(value || '').trim()
  if (!v) return ''
  let result = v.startsWith('sid:') ? v.slice(4) : v
  return result.endsWith('@goofish') ? result.slice(0, -8) : result
}

function normalizePeerUserId(value) {
  const v = String(value || '').trim()
  if (!v || v.startsWith('sid:')) return ''
  return v.endsWith('@goofish') ? v.slice(0, -8) : v
}

function normalizeGoofishId(value) {
  const sid = normalizeSid(value)
  const peer = normalizePeerUserId(value)
  return peer || sid
}

function toGoofishId(value) {
  const normalized = normalizeGoofishId(value)
  return normalized ? `${normalized}@goofish` : ''
}

function resolveReceiverId(conv) {
  if (!conv) return ''
  return normalizePeerUserId(
    conv.peerUserId ||
    conv.peerExternalUid ||
    conv.externalBuyerId ||
    conv.raw?.peerUserId ||
    conv.raw?.peerExternalUid ||
    conv.raw?.externalBuyerId ||
    conv.receiverUserId ||
    conv.raw?.receiverUserId ||
    ''
  )
}


function stableHash(value) {
  const text = String(value || '')
  let hash = 2166136261
  for (let i = 0; i < text.length; i += 1) {
    hash ^= text.charCodeAt(i)
    hash = Math.imul(hash, 16777619)
  }
  return (hash >>> 0).toString(36)
}

function stableGeneratedMessageId(item, rawContent = '') {
  const sid = normalizeSid(item.conversationId || item.sid || item.sId || item.sessionId || item.cid || '')
  const direction = String(item.direction || '').toUpperCase()
  const sender = normalizeGoofishId(item.senderUserId || item.fromUserId || '')
  const receiver = normalizeGoofishId(item.receiverUserId || item.toUserId || '')
  const time = parseMessageTimestamp(item.messageTime || item.createdTime || item.sendTime || item.time || 0)
  const content = String(rawContent || item.msgContent || item.content || item.message || '').trim()
  return `gen_${stableHash(`${sid}:${direction}:${sender}:${receiver}:${time}:${content}`)}`
}

function messageIdentity(message) {
  if (!message) return ''
  const pnmId = String(message.pnmId || message.pnm_id || message.messageUid || message.message_uid || message.messageId || '').trim()
  if (pnmId) return `pnm:${pnmId}`
  const id = String(message.id || '').trim()
  if (id && !id.startsWith('temp_') && !id.startsWith('gen_')) return `id:${id}`
  const direction = String(message.direction || '').toUpperCase()
  const sender = normalizeGoofishId(message.senderUserId || message.fromUserId || '')
  const receiver = normalizeGoofishId(message.receiverUserId || message.toUserId || '')
  const time = parseMessageTimestamp(message.messageTime || message.createdTime || 0)
  const content = String(message.msgContent || message.content || '').trim()
  return `fallback:${direction}:${sender}:${receiver}:${time}:${content}`
}

function dedupeMessages(list) {
  const seen = new Set()
  return (Array.isArray(list) ? list : []).filter(item => {
    const key = messageIdentity(item)
    if (!key || seen.has(key)) return false
    seen.add(key)
    return true
  })
}

function normalizeContextMessageList(list) {
  return sortMessagesByTime(dedupeMessages(list))
}

function shouldKeepLocalPendingContextMessage(message, sid, peerUserId) {
  if (!message) return false
  const messageSid = normalizeSid(message.sid || message.sId || message.sessionId || '')
  if (sid && messageSid && messageSid !== sid) return false
  if (['sending', 'failed', 'unknown'].includes(message.sendStatus)) return true
  const sender = normalizePeerUserId(message.senderUserId || message.fromUserId || '')
  const receiver = normalizePeerUserId(message.receiverUserId || message.toUserId || '')
  const peer = normalizePeerUserId(message.peerExternalUid || message.peerUserId || '')
  const hasResolvedPeerIdentity = Boolean(sender || receiver || peer)
  if (hasResolvedPeerIdentity) return false
  return !peerUserId || Boolean(sid)
}

function isSystemConversation(conv) {
  if (!conv) return true
  const sid = (conv.sid || conv.sId || conv.conversationId || '').toLowerCase()
  return sid.includes('system') || sid.includes('sys_') || sid.startsWith('sys')
}

const canSend = computed(() => {
  return shouldEnableMainComposerSend({
    accountId: query.xianyuAccountId,
    conversationSid: selected.value?.sid,
    isSystemConversation: isSystemConversation(selected.value),
    sending: sending.value,
    isDeletedConversation: isConversationDeleted(deletedConversations.value, selected.value),
    draftText: draft.value,
    conversationsAvailable: conversationsAvailable.value,
    contextAvailable: contextAvailable.value
  })
})

function isConversationCompleted(c) {
  return ['completed', 'closed', 'transferred'].includes(c.sessionStatus) || c.closed === true
}
function isConversationInProgress(c) {
  return !isConversationCompleted(c)
}
function setConversationFilter(type) {
  conversationFilter.value = type
  visibleConversationCount.value = DEFAULT_VISIBLE_CONVERSATIONS
}
function toggleSortOrder() {
  sortDesc.value = !sortDesc.value
}
const displayList = computed(() => {
  const kw = debouncedKeyword.value.toLowerCase()
  let list = conversations.value.filter(c => {
    const matchKeyword = !kw ||
      (c.name || '').toLowerCase().includes(kw) ||
      (c.product || '').toLowerCase().includes(kw) ||
      (c.msg || '').toLowerCase().includes(kw)
    if (!matchKeyword) return false
    if (conversationFilter.value === 'unreplied') return Number(c.unreadCount || 0) > 0
    if (conversationFilter.value === 'inProgress') return isConversationInProgress(c)
    if (conversationFilter.value === 'completed') return isConversationCompleted(c)
    if (conversationFilter.value === 'robot') return !!c.botEnabled
    return true
  })
  list = sortConversationSnapshots(list, { descending: sortDesc.value })
  return list
})

const pagedDisplayList = computed(() => displayList.value.slice(0, visibleConversationCount.value))
const canLoadMoreConversations = computed(() =>
  displayList.value.length > visibleConversationCount.value || conversationHasMore.value
)

const decoratedMessages = computed(() => {
  let prevDayKey = ''
  const sorted = sortMessagesByTime(contextMessages.value)
  return sorted.map(item => {
    // 用日期 key 判断是否需要显示分隔符（避免同一天多次显示）
    const date = new Date(item.messageTime)
    const dayKey = date.getFullYear() + '-' + (date.getMonth() + 1) + '-' + date.getDate()
    const showDate = dayKey !== prevDayKey ? formatDateLabel(item.messageTime) : ''
    prevDayKey = dayKey
    return {
      ...item,
      isMe: isMe(item),
      isAiReply: Number(item.isAutoReply ?? item.is_auto_reply ?? 0) === 1,
      showDate,
      sendStatus: item.sendStatus || null,
      readStateText: item.sendStatus === 'sending' ? '发送中'
        : item.sendStatus === 'failed' ? '明确未发送'
        : item.sendStatus === 'unknown' ? '结果待核对'
        : item.read_status === 2 || item.readStatus === 2 ? '已读'
        : item.read_status === 1 || item.readStatus === 1 ? '已送达'
        : '已发送'
    }
  })
})

const unrepliedCount = computed(() => conversations.value.filter(item => item.unreadCount > 0).length)
const inProgressCount = computed(() => conversations.value.filter(item => isConversationInProgress(item)).length)
const completedCount = computed(() => conversations.value.filter(item => isConversationCompleted(item)).length)
const robotCount = computed(() => conversations.value.filter(item => item.botEnabled).length)
watch(debouncedKeyword, () => {
  visibleConversationCount.value = DEFAULT_VISIBLE_CONVERSATIONS
})

function normalizeDisplayImage(url) {
  const value = String(url || '').trim()
  if (!value) return ''
  if (value.startsWith('//')) return `https:${value}`
  if (value.startsWith('http://') || value.startsWith('https://')) return value
  if (value.startsWith('/uploads/')) return value
  if (value.startsWith('/')) return `https://img.alicdn.com${value}`
  return value
}

function openImagePreview(url) {
  const normalized = normalizeDisplayImage(url)
  if (!normalized) return
  previewImageUrl.value = normalized
}

// 商品卡片图片加载失败时隐藏图片，只显示文字
function onCardImageError(e) {
  const img = e?.target
  if (img && img.parentNode) {
    img.style.display = 'none'
  }
}

function closeImagePreview() {
  previewImageUrl.value = ''
}

function openImagePreviewInNewTab() {
  if (!previewImageUrl.value) return
  openExternalUrl(previewImageUrl.value)
}

function avatarText(name) {
  return String(name || '用户').trim().slice(0, 1) || '用'
}

function conversationThumb(conv) {
  return normalizeDisplayImage(conv?.goodsCoverPic || conv?.raw?.goodsCoverPic) || defaultThumb
}

// 已加载失败的头像 URL 集合，避免坏链 img 反复重试闪烁
const failedAvatarUrls = reactive({})

function markAvatarFailed(url) {
  if (url) failedAvatarUrls[url] = true
}

function conversationAvatarUrl(conv) {
  const direct = normalizeDisplayImage(
    conv?.avatarUrl || conv?.buyerAvatar || conv?.raw?.buyerAvatar || conv?.raw?.avatarUrl || ''
  )
  if (direct && !failedAvatarUrls[direct]) return direct
  // 服务器未返回头像或图片加载失败时，回退到内存缓存（fetchMissingAvatars 已查询过的）
  const cid = String(conv?.sid || conv?.conversationId || '').trim()
  if (cid) {
    const cached = userInfoCacheRef.value[cid]
    if (cached && cached.avatar) {
      const cachedUrl = normalizeDisplayImage(cached.avatar)
      if (cachedUrl && !failedAvatarUrls[cachedUrl]) return cachedUrl
    }
  }
  return ''
}

// 从缓存恢复昵称（当服务器返回的 name 为空或纯数字 ID 时）
function resolveConversationName(conv) {
  const directName = conv?.name
  if (directName && !/^\d+$/.test(directName)) return directName
  const cid = String(conv?.sid || conv?.conversationId || '').trim()
  if (cid) {
    const cached = userInfoCacheRef.value[cid]
    if (cached && cached.nick) return cached.nick
  }
  return directName || ''
}

function formatMessageDay(value) {
  // 对齐参考项目 ChatNew.tsx formatTime：今天显示 HH:mm，非今天显示 MM/DD HH:mm
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  const now = new Date()
  const isToday = date.getFullYear() === now.getFullYear() &&
    date.getMonth() === now.getMonth() &&
    date.getDate() === now.getDate()
  const hour = `${date.getHours()}`.padStart(2, '0')
  const minute = `${date.getMinutes()}`.padStart(2, '0')
  if (isToday) return `${hour}:${minute}`
  const month = `${date.getMonth() + 1}`.padStart(2, '0')
  const day = `${date.getDate()}`.padStart(2, '0')
  return `${month}/${day} ${hour}:${minute}`
}

function formatDateLabel(value) {
  // 日期分隔符：只显示日期，不显示时间，避免与气泡 meta 冗余
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  const now = new Date()
  const isToday = date.getFullYear() === now.getFullYear() &&
    date.getMonth() === now.getMonth() &&
    date.getDate() === now.getDate()
  if (isToday) return '今天'
  const yesterday = new Date(now)
  yesterday.setDate(yesterday.getDate() - 1)
  const isYesterday = date.getFullYear() === yesterday.getFullYear() &&
    date.getMonth() === yesterday.getMonth() &&
    date.getDate() === yesterday.getDate()
  if (isYesterday) return '昨天'
  const month = `${date.getMonth() + 1}`.padStart(2, '0')
  const day = `${date.getDate()}`.padStart(2, '0')
  if (date.getFullYear() === now.getFullYear()) return `${month}月${day}日`
  return `${date.getFullYear()}年${month}月${day}日`
}

function formatConversationTime(value) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  const now = new Date()
  const isToday = date.getFullYear() === now.getFullYear() &&
    date.getMonth() === now.getMonth() &&
    date.getDate() === now.getDate()
  const hour = `${date.getHours()}`.padStart(2, '0')
  const minute = `${date.getMinutes()}`.padStart(2, '0')
  if (isToday) return `${hour}:${minute}`
  const yesterday = new Date(now)
  yesterday.setDate(yesterday.getDate() - 1)
  const isYesterday = date.getFullYear() === yesterday.getFullYear() &&
    date.getMonth() === yesterday.getMonth() &&
    date.getDate() === yesterday.getDate()
  if (isYesterday) return '昨天'
  const month = `${date.getMonth() + 1}`.padStart(2, '0')
  const day = `${date.getDate()}`.padStart(2, '0')
  if (date.getFullYear() === now.getFullYear()) return `${month}/${day}`
  return `${date.getFullYear()}/${month}/${day}`
}

function time(value) {
  return formatConversationTime(value) || formatMessageDay(value) || timeText(value)
}

function isMe(message) {
  if (!message) return false
  if (String(message.direction || '').toUpperCase() === 'OUT') return true
  const account = accounts.value.find(a => a.id === Number(query.xianyuAccountId))
  if (!account || !(account.externalUid || account.unb)) return false
  const ownId = String(account.externalUid || account.unb)
  const senderId = String(message.senderUserId || '')
  if (!senderId) return false
  if (senderId.includes(ownId) || ownId.includes(senderId)) return true
  return senderId === ownId
}

async function loadMoreConversations() {
  // 优先展示本地已加载但未显示的会话
  if (displayList.value.length > visibleConversationCount.value) {
    visibleConversationCount.value += DEFAULT_VISIBLE_CONVERSATIONS
    return
  }
  // 本地已全部展示，但后端还有更多会话 → 触发真分页加载下一页
  if (!conversationHasMore.value || loadingMoreConversations.value) return
  const requestedAccountId = Number(query.xianyuAccountId || 0)
  if (!requestedAccountId) return
  loadingMoreConversations.value = true
  try {
    const res = await onlineConversations(requestedAccountId, {
      cursor: conversationCursor.value,
      pageSize: query.pageSize,
    })
    const raw = unwrap(res?.data)
    const list = Array.isArray(raw)
      ? raw
      : (Array.isArray(raw?.conversations) ? raw.conversations : [])
    const hasMore = Array.isArray(raw) ? false : !!raw?.hasMore
    const nextCursor = Array.isArray(raw) ? null : raw?.nextCursor
    const nextBatch = dedupeConversations(list
      .map(toDisplayConversation)
      .filter(item => item && item.sid))
    // 追加到会话列表（去重）
    const existingKeys = new Set(conversations.value.map(c => conversationDedupeKey(c)))
    const newItems = nextBatch.filter(c => !existingKeys.has(conversationDedupeKey(c)))
    conversations.value = [...conversations.value, ...newItems]
    conversationCursor.value = nextCursor
    conversationHasMore.value = hasMore
    visibleConversationCount.value += DEFAULT_VISIBLE_CONVERSATIONS
    // 后台分批查询缺头像会话的头像
    fetchMissingAvatars(requestedAccountId, newItems).catch(() => {})
  } catch (e) {
    if (import.meta.env.DEV) console.error('[MSG] loadMoreConversations failed')
    error.value = '加载更多会话失败: ' + (e.message || '网络错误')
  } finally {
    loadingMoreConversations.value = false
  }
}

function handleQuickAction(item) {
  if (item === '快捷回复') {
    const firstTemplate = quickTemplates.value[0]
    if (firstTemplate) insertTemplate(firstTemplate)
    else showTemplateModal.value = true
    return
  }
  if (item === '常用语库') {
    showTemplateModal.value = true
    return
  }
  if (item === '邀请下单') {
    const invitation = '您可以直接拍下当前商品，我这边会尽快为您安排交付～'
    draft.value = draft.value ? `${draft.value}\n${invitation}` : invitation
    return
  }
  if (item === '结束会话') {
    endSession()
  }
}

function toggleEmojiPanel() {
  showEmojiPanel.value = !showEmojiPanel.value
}

function insertEmoji(emoji) {
  draft.value = `${draft.value}${emoji}`
  showEmojiPanel.value = false
}

function triggerImagePick() {
  imageInput.value?.click()
}

function getUploadImageUrl(payload) {
  const data = unwrap(payload)
  return data?.imageUrl || data?.url || data?.data?.url || data?.data?.imageUrl || payload?.imageUrl || payload?.url || payload?.data?.url || payload?.data?.imageUrl || ''
}

function updateConversationPreview(conversationSid, updater) {
  const key = conversationDedupeKey(conversationSid)
  if (!key) return
  conversations.value = conversations.value.map(item => conversationDedupeKey(item) === key ? updater(item) : item)
  if (conversationDedupeKey(selected.value) === key) {
    const nextSelected = conversations.value.find(item => conversationDedupeKey(item) === key)
    if (nextSelected) selected.value = nextSelected
  }
}

function setConversationUnread(targetConversation, unreadCount) {
  const key = conversationDedupeKey(targetConversation)
  if (!key) return
  conversations.value = conversations.value.map(item =>
    conversationDedupeKey(item) === key ? applyConversationUnreadState(item, unreadCount, normalizeConversationStatus) : item
  )
  if (conversationDedupeKey(selected.value) === key && selected.value) {
    selected.value = applyConversationUnreadState(selected.value, unreadCount, normalizeConversationStatus)
  }
}

function upsertConversationFromEvent(payload, { currentChat = false } = {}) {
  const sid = normalizeSid(payload.sId || payload.sid || payload.cid || payload.sessionId || '')
  if (!sid) return
  const peerUserId = normalizePeerUserId(
    payload.peerUserId || payload.peerExternalUid || payload.senderUserId || payload.receiverUserId || ''
  )
    const existingIndex = findConversationMatchIndex(conversations.value, {
      sid,
      sId: sid,
      sessionId: sid,
      peerUserId,
      xianyuAccountId: Number(payload.xianyuAccountId || payload.accountId || query.xianyuAccountId || 0) || undefined,
      accountId: Number(payload.accountId || payload.xianyuAccountId || query.xianyuAccountId || 0) || undefined
    })
  const previewText = shortText(extractMessageDisplayText(payload), 42)
  const now = parseMessageTimestamp(payload.messageTime || payload.createdTime || payload.sendTime || Date.now()) || Date.now()

  if (existingIndex >= 0) {
    const existing = conversations.value[existingIndex]
    const nextUnreadCount = currentChat ? 0 : (Number(existing.unreadCount) || 0) + (String(payload.direction || '').toUpperCase() === 'IN' ? 1 : 0)
    const merged = {
      ...existing,
      peerUserId: peerUserId || existing.peerUserId,
      peerKey: payload.peerKey || existing.peerKey || (peerUserId ? peerUserId : `sid:${sid}`),
      peerExternalUid: peerUserId || existing.peerExternalUid,
      externalBuyerId: peerUserId || existing.externalBuyerId,
      name: payload.peerUserName || payload.peerNick || payload.senderUserName || existing.name,
      msg: previewText || existing.msg,
      time: time(now),
      lastMessageTime: now,
      unreadCount: nextUnreadCount,
      xyGoodsId: payload.xyGoodsId || existing.xyGoodsId,
      product: resolveConversationGoodsTitle({ ...existing, ...payload }, existing.product || existing.goodsTitle || '未关联商品'),
      goodsTitle: resolveConversationGoodsTitle({ ...existing, ...payload }, existing.goodsTitle || existing.product || '未关联商品'),
      goodsCoverPic: payload.goodsCoverPic || existing.goodsCoverPic || '',
      contentType: payload.contentType ?? existing.contentType,
      messageKind: payload.contentType === 2 ? 'image' : existing.messageKind,
      badgeText: conversationBadgeText(existing.statusCode, existing.statusText, nextUnreadCount)
    }
    conversations.value = [merged, ...conversations.value.filter((_, index) => index !== existingIndex)]
    if (conversationDedupeKey(selected.value) === conversationDedupeKey(merged)) selected.value = merged
    return
  }

  const newConv = toDisplayConversation({
    sid,
    sId: sid,
    xianyuAccountId: Number(payload.xianyuAccountId || payload.accountId || query.xianyuAccountId || 0) || undefined,
    accountId: Number(payload.accountId || payload.xianyuAccountId || query.xianyuAccountId || 0) || undefined,
    peerUserId,
    peerKey: payload.peerKey || (peerUserId ? peerUserId : `sid:${sid}`),
    peerUserName: payload.peerUserName || payload.peerNick || payload.senderUserName || peerUserId || `会话 ${String(sid).slice(-4)}`,
    buyerName: payload.peerUserName || payload.peerNick || payload.senderUserName || peerUserId || `会话 ${String(sid).slice(-4)}`,
    lastMessage: extractMessageDisplayText(payload),
    lastMessageTime: now,
    lastContentType: payload.contentType,
    unreadCount: currentChat ? 0 : (String(payload.direction || '').toUpperCase() === 'IN' ? 1 : 0),
    goodsTitle: resolveConversationGoodsTitle(payload),
    goodsId: payload.xyGoodsId || '',
    goodsCoverPic: payload.goodsCoverPic || '',
    reminderContent: payload.reminderContent || '',
    senderUserId: payload.senderUserId || '',
    receiverUserId: payload.receiverUserId || '',
    messageCount: 1,
    peerExternalUid: peerUserId,
    externalBuyerId: peerUserId
  })
  if (newConv) {
    conversations.value = [newConv, ...conversations.value]
  }
}

function conversationDbId(conv) {
  const id = conv?.id || conv?.rawId || conv?.conversationDbId
  return Number.isFinite(Number(id)) ? Number(id) : null
}

async function persistConversationStatus(action, localPatch, successText) {
  if (!selected.value?.sid) return
  const id = conversationDbId(selected.value)
  if (!id) {
    error.value = '当前会话缺少数据库 ID，无法持久化状态。请先刷新会话列表后重试。'
    return
  }
  if (statusUpdating.value) return
  statusUpdating.value = true
  try {
    const res = await updateConversationStatus(id, { action })
    const updated = unwrap(res.data) || {}
    updateConversationPreview(selected.value, item => ({ ...item, ...localPatch, statusCode: updated.status ?? item.statusCode }))
    events.value.unshift({ text: successText, time: new Date().toLocaleTimeString('zh-CN', { hour12: false }) })
    events.value = events.value.slice(0, 20)
  } catch (e) {
    error.value = `${successText}失败: ${e.message || '网络错误'}`
  } finally {
    statusUpdating.value = false
  }
}

async function transferSession() {
  if (!selected.value?.sid) return
  if (!await confirmAction({ title: '确认将当前会话标记为已转接？', description: '该操作会写入后台状态和审计日志。' })) return
  await persistConversationStatus('transferred', { sessionStatus: 'transferred', badgeText: '已转接', unreadCount: 0 }, '会话已转接')
}

async function endSession() {
  if (!selected.value?.sid) return
  if (!await confirmAction({ title: '确认结束当前会话？', description: '结束后会从进行中列表移入已完成，并清零未读数。', dangerous: true })) return
  await persistConversationStatus('completed', { sessionStatus: 'completed', unreadCount: 0, badgeText: '已完成' }, '会话已结束')
}

async function handleImagePick(event) {
  const file = event.target.files?.[0]
  if (!file) return
  try {
    await sendImage(file)
  } finally {
    event.target.value = ''
  }
}

async function sendImage(file) {
  if (!messageDataAvailable.value) {
    error.value = '会话或聊天记录不可用，已暂停发送；请先重试加载。'
    return
  }
  if (!selected.value) {
    error.value = '请先选择会话'
    return
  }
  if (!query.xianyuAccountId) {
    error.value = '请先选择账号'
    return
  }
  const receiverId = resolveReceiverId(selected.value) || normalizeSid(selected.value.sid) || ''
  sendingImage.value = true
  sending.value = true
  showEmojiPanel.value = false
  error.value = ''
  const account = accounts.value.find(a => a.id === Number(query.xianyuAccountId))
  const tempId = `temp_image_${Date.now()}`
  const idempotencyKey = createManualMessageIdempotencyKey('image')
  let sendAttempted = false
  const optimisticMsg = {
    id: tempId,
    sId: selected.value.sid,
    sid: selected.value.sid,
    pnmId: tempId,
    contentType: 2,
    imageUrls: [],
    msgContent: '[图片]',
    displayText: '[图片]',
    senderUserId: toGoofishId(account?.unb || account?.externalUid || ''),
    direction: 'OUT',
    messageTime: Date.now(),
    messageKind: 'image',
    readStatus: 0,
    sendStatus: 'sending',
    retrySafe: false,
    idempotencyKey
  }
  const previousConversation = findConversationByIdentity(conversations.value, selected.value)
  contextMessages.value = normalizeContextMessageList([...contextMessages.value, optimisticMsg])
  updateConversationPreview(selected.value, item => ({
    ...item,
    msg: '[图片]',
    time: time(Date.now()),
    lastMessageTime: Date.now()
  }))
  await nextTick()
  scrollToBottom()

  try {
    const uploadRes = await uploadImage(Number(query.xianyuAccountId), file)
    const imageUrlValue = getUploadImageUrl(uploadRes)
    if (!imageUrlValue) {
      throw new Error('上传成功但未返回图片地址')
    }
    // Update optimistic message with uploaded image URL (still sending)
    contextMessages.value = contextMessages.value.map(item => item.id === tempId
      ? { ...item, imageUrls: [imageUrlValue], msgContent: imageUrlValue, displayText: '[图片]' }
      : item)
    sendAttempted = true
    const imgFileRes = await sendImageMessage({
      xianyuAccountId: Number(query.xianyuAccountId),
      cid: selected.value.sid,
      sid: selected.value.sid,
      sId: selected.value.sid,
      sessionId: selected.value.sid,
      toId: receiverId,
      peerUserId: receiverId,
      imageUrl: imageUrlValue,
      xyGoodsId: selected.value.xyGoodsId,
      idempotencyKey
    })
    const outcome = resolveManualMessageOutcome(imgFileRes)
    contextMessages.value = contextMessages.value.map(item =>
      item.id === tempId ? applyManualMessageOutcome(item, outcome) : item
    )
    if (outcome.status === 'confirmed') {
      events.value.unshift({
        text: '平台已确认图片发送',
        time: new Date().toLocaleTimeString('zh-CN', { hour12: false })
      })
      events.value = events.value.slice(0, 20)
      await loadConversations(true, { background: true })
    } else {
      if (outcome.status === 'failed' && outcome.retrySafe && previousConversation) {
        updateConversationPreview(previousConversation, () => previousConversation)
      }
      error.value = manualMessageErrorText(outcome, '图片')
    }
  } catch (e) {
    if (sendAttempted) {
      const outcome = resolveManualMessageError(e)
      contextMessages.value = contextMessages.value.map(item => item.id === tempId
        ? applyManualMessageOutcome(item, outcome)
        : item)
      error.value = manualMessageErrorText(outcome, '图片')
    } else {
      contextMessages.value = contextMessages.value.map(item => item.id === tempId
        ? { ...item, sendStatus: 'failed', retrySafe: false, sendErrorCode: 'image_upload_failed' }
        : item)
      if (previousConversation) {
        updateConversationPreview(previousConversation, () => previousConversation)
      }
      error.value = '图片尚未发送：上传或校验失败，请重新选择图片。'
    }
  } finally {
    sendingImage.value = false
    sending.value = false
  }
}

function validateImageUrl(url) {
  try {
    const parsed = new URL(url)
    const host = parsed.hostname.toLowerCase()
    if (parsed.protocol !== 'https:') return '图片链接仅支持 HTTPS 地址'
    if (host === 'localhost' || host === '127.0.0.1' || host === '::1' || host.startsWith('10.') || host.startsWith('192.168.') || /^172\.(1[6-9]|2\d|3[0-1])\./.test(host)) {
      return '不允许发送内网或本机图片地址'
    }
    if (url.length > 500) return '图片链接不能超过 500 个字符'
    return ''
  } catch {
    return '图片链接格式错误'
  }
}

async function sendImageByUrl() {
  if (!messageDataAvailable.value) {
    error.value = '会话或聊天记录不可用，已暂停发送；请先重试加载。'
    return
  }
  const urls = parseImageUrlBatchInput(imageUrl.value)
  if (!urls.length) return
  const urlError = urls.map(validateImageUrl).find(Boolean)
  if (urlError) {
    error.value = urlError
    return
  }
  if (!selected.value) {
    error.value = '请先选择会话'
    return
  }
  if (!query.xianyuAccountId) {
    error.value = '请先选择账号'
    return
  }
  const receiverId = resolveReceiverId(selected.value) || normalizeSid(selected.value.sid) || ''
  sending.value = true
  showEmojiPanel.value = false
  error.value = ''
  const account = accounts.value.find(a => a.id === Number(query.xianyuAccountId))
  const optimisticMessages = urls.map((url, index) => {
    const tempId = `temp_image_url_${Date.now()}_${index}`
    return {
      id: tempId,
      sId: selected.value.sid,
      sid: selected.value.sid,
      pnmId: tempId,
      contentType: 2,
      imageUrls: [url],
      msgContent: url,
      displayText: '[图片]',
      senderUserId: toGoofishId(account?.unb || account?.externalUid || ''),
      direction: 'OUT',
      messageTime: Date.now() + index,
      messageKind: 'image',
      readStatus: 0,
      sendStatus: 'sending',
      retrySafe: false,
      idempotencyKey: createManualMessageIdempotencyKey('image')
    }
  })
  const previousConversation = findConversationByIdentity(conversations.value, selected.value)
  const batchMessageIds = new Set(optimisticMessages.map(item => item.id))
  contextMessages.value = normalizeContextMessageList([...contextMessages.value, ...optimisticMessages])
  updateConversationPreview(selected.value, item => ({
    ...item,
    msg: '[图片]',
    time: time(Date.now()),
    lastMessageTime: Date.now()
  }))
  imageUrl.value = ''
  await nextTick()
  scrollToBottom()

  try {
    let confirmedCount = 0
    let failedCount = 0
    let unknownCount = 0
    for (const optimisticMessage of optimisticMessages) {
      const url = optimisticMessage.imageUrls?.[0] || optimisticMessage.msgContent
      let outcome
      try {
        const imgRes = await sendImageMessage({
          xianyuAccountId: Number(query.xianyuAccountId),
          cid: selected.value.sid,
          sid: selected.value.sid,
          sId: selected.value.sid,
          sessionId: selected.value.sid,
          toId: receiverId,
          peerUserId: receiverId,
          imageUrl: url,
          xyGoodsId: selected.value.xyGoodsId,
          idempotencyKey: optimisticMessage.idempotencyKey
        })
        outcome = resolveManualMessageOutcome(imgRes)
      } catch (sendError) {
        outcome = resolveManualMessageError(sendError)
      }
      contextMessages.value = contextMessages.value.map(item =>
        item.id === optimisticMessage.id
          ? applyManualMessageOutcome(item, outcome)
          : item
      )
      if (outcome.status === 'confirmed') confirmedCount += 1
      else if (outcome.status === 'failed' && outcome.retrySafe) failedCount += 1
      else unknownCount += 1
    }
    const previewState = resolveImageBatchPreviewState(
      contextMessages.value.filter(item => batchMessageIds.has(item.id)),
      previousConversation
    )
    if (previousConversation && previewState.shouldRestorePrevious) {
      updateConversationPreview(previousConversation, () => previousConversation)
    } else if (!previewState.shouldRestorePrevious) {
      updateConversationPreview(selected.value, item => ({
        ...item,
        msg: previewState.nextPreviewText,
        time: time(Date.now()),
        lastMessageTime: Date.now()
      }))
    }
    if (confirmedCount > 0) {
      events.value.unshift({
        text: `平台已确认 ${confirmedCount} 张图片发送`,
        time: new Date().toLocaleTimeString('zh-CN', { hour12: false })
      })
      events.value = events.value.slice(0, 20)
      await loadConversations(true, { background: true })
    }
    if (unknownCount > 0) {
      error.value = `${unknownCount} 张图片发送结果待核对，请先在闲鱼 App 检查；系统已禁止直接重试。`
    } else if (failedCount > 0) {
      error.value = `${failedCount} 张图片被平台明确拒绝，可在排查后安全重试。`
    }
  } catch {
    const outcome = resolveManualMessageError()
    contextMessages.value = contextMessages.value.map(item =>
      batchMessageIds.has(item.id) && item.sendStatus === 'sending'
        ? applyManualMessageOutcome(item, outcome)
        : item
    )
    error.value = manualMessageErrorText(outcome, '图片')
  } finally {
    sending.value = false
  }
}

function normalizeConversationStatus(status, statusText) {
  return compareConversationStatus(status, statusText)
}

function conversationBadgeText(status, statusText, unreadCount) {
  if (Number(unreadCount || 0) > 0) return '新消息'
  const normalized = normalizeConversationStatus(status, statusText)
  if (normalized === 'completed') return '已完成'
  if (normalized === 'closed') return '已关闭'
  if (normalized === 'transferred') return '已转接'
  return '会话'
}

function resolveConversationDisplayName(dto, sid, peerUserId) {
  const candidates = [
    dto.peerUserName,
    dto.peerNick,
    dto.buyerName,
    dto.senderUserName,
    dto.raw?.senderUserName,
    dto.raw?.peerUserName,
    dto.raw?.buyerName
  ].map(v => String(v || '').trim()).filter(Boolean)

  const blocked = ['买家', '闲鱼买家', 'STIMULATED_SALE_BUY', 'shading_opening', '我', 'null', 'undefined']
  const valid = candidates.find(v => !blocked.includes(v))
  if (valid) return valid
  // peerUserId 的数字部分可作为兜底展示名
  if (peerUserId) {
    const cleaned = String(peerUserId).replace(/@goofish$/i, '').replace(/^sid:/, '')
    if (cleaned && /^\d+$/.test(cleaned)) return `买家${cleaned.slice(-6)}`
    if (cleaned) return cleaned
  }
  if (sid) {
    const cleaned = String(sid).replace(/@goofish$/i, '')
    if (cleaned && /^\d+$/.test(cleaned)) return `买家${cleaned.slice(-6)}`
    return `用户${String(sid).slice(-4)}`
  }
  return '未知用户'
}

function toDisplayConversation(dto) {
  const sid = normalizeSid(dto.sid || dto.sId || dto.sessionId || dto.id || dto.conversationId || dto.externalBuyerId || dto.peerUserId || '')
  if (!sid) {
    if (import.meta.env.DEV) console.warn('[MSG] toDisplayConversation: missing conversation identifier')
    return null
  }
  const peerUserId = normalizePeerUserId(
    dto.peerUserId || dto.peerExternalUid || dto.externalBuyerId || dto.buyerId || dto.senderUserId || dto.receiverUserId || ''
  )
  const lastMsg = extractMessageDisplayText(dto)
  const unreadCount = Number(dto.unreadCount) || 0
  const goodsTitle = resolveConversationGoodsTitle(dto)
  const displayName = resolveConversationDisplayName(dto, sid, peerUserId)
  const lastMessageTime = parseMessageTimestamp(
    dto.lastMessageTime || dto.updatedTime || dto.createdTime || dto.messageTime || 0
  )
  // 仅过滤时间无法解析的会话；保留所有历史会话以匹配目标项目的完整会话列表行为
  if (!lastMessageTime) {
    return null
  }
  return {
    id: dto.id || dto.conversationDbId || dto.conversationId || '',
    rawId: dto.id || dto.conversationDbId || dto.conversationId || '',
    raw: dto,
    xianyuAccountId: Number(dto.xianyuAccountId || dto.accountId || query.xianyuAccountId || 0) || undefined,
    accountId: Number(dto.accountId || dto.xianyuAccountId || query.xianyuAccountId || 0) || undefined,
    sid,
    conversationId: sid,
    peerUserId,
    peerKey: dto.peerKey || (peerUserId ? peerUserId : `sid:${sid}`),
    externalBuyerId: normalizePeerUserId(dto.externalBuyerId || dto.peerExternalUid || dto.peerUserId || ''),
    peerExternalUid: normalizePeerUserId(dto.peerExternalUid || dto.externalBuyerId || dto.peerUserId || ''),
    receiverUserId: normalizePeerUserId(dto.receiverUserId || ''),
    senderUserId: normalizePeerUserId(dto.senderUserId || ''),
    name: displayName,
    avatarUrl: normalizeDisplayImage(dto.buyerAvatar || dto.avatarUrl || dto.peerUserAvatar || dto.otherUserAvatar || ''),
    msg: shortText(lastMsg, 42),
    lastIsAutoReply: Boolean(dto.lastIsAutoReply || dto.isAutoReply || dto.is_auto_reply),
    hasAiReply: Boolean(dto.hasAiReply || dto.lastIsAutoReply || dto.isAutoReply || dto.is_auto_reply),
    product: goodsTitle,
    kind: dto.lastContentType != null ? `type:${dto.lastContentType}` : '-',
    time: time(lastMessageTime),
    xyGoodsId: dto.xyGoodsId || dto.goodsId || dto.itemId || '',
    firstMessageTime: dto.firstMessageTime || dto.createdTime || dto.messageTime,
    lastMessageTime,
    messageKind: dto.lastContentType === 2 ? 'image' : (dto.lastContentType != null ? `type:${dto.lastContentType}` : '-'),
    contentType: dto.lastContentType,
    unreadCount,
    statusCode: dto.status ?? dto.conversationStatus,
    statusText: dto.statusText || '',
    sessionStatus: normalizeConversationStatus(dto.status ?? dto.conversationStatus, dto.statusText),
    goodsTitle,
    goodsPrice: dto.goodsPrice,
    goodsPriceText: dto.goodsPrice ? `￥${dto.goodsPrice}` : '-',
    goodsCoverPic: normalizeDisplayImage(dto.goodsCoverPic || dto.coverPic || dto.imageUrl || ''),
    messageCount: dto.messageCount || 0,
    contextCount: dto.messageCount || 0,
    cardTitle: dto.cardTitle,
    cardSubtitle: dto.cardSubtitle,
    orderStatusText: dto.orderStatusText,
    autoDeliveryStateText: dto.autoDeliveryStateText,
    onlineStatus: resolveConversationOnlineStatus(dto.onlineStatus),
    badgeText: conversationBadgeText(dto.status ?? dto.conversationStatus, dto.statusText, unreadCount),
    registeredAt: dto.registeredAt || '-',
    region: dto.region || '-',
    recentInquiryCount: dto.recentInquiryCount || 0,
    dealAmountText: dto.dealAmount ? `￥${dto.dealAmount}` : '￥0.00',
    botEnabled: Boolean(dto.botEnabled || dto.hasAiReply || dto.lastIsAutoReply)
  }
}

function conversationDedupeKey(item) {
  return getConversationIdentityKey(item) || String(item?.id || item?.rawId || '')
}

function latestConversationTime(item) {
  // item.lastMessageTime 已经由 parseMessageTimestamp 处理，raw.* 可能还是原始时间字段
  const raw = item?.lastMessageTime || item?.raw?.lastMessageTime || item?.raw?.updatedTime || item?.raw?.createdTime || item?.raw?.messageTime || 0
  return parseMessageTimestamp(raw)
}

function dedupeConversations(list) {
  const map = new Map()
  for (const item of Array.isArray(list) ? list : []) {
    if (!item || !item.sid) continue
    const key = conversationDedupeKey(item)
    if (!key) continue
    const prev = map.get(key)
    if (!prev) {
      map.set(key, item)
      continue
    }
    const keep = latestConversationTime(item) >= latestConversationTime(prev) ? item : prev
    const other = keep === item ? prev : item
    map.set(key, {
      ...other,
      ...keep,
      id: getConversationRecordId(keep) || getConversationRecordId(other) || keep.id || other.id || '',
      rawId: getConversationRecordId(keep) || getConversationRecordId(other) || keep.rawId || other.rawId || '',
      conversationDbId: getConversationRecordId(keep) || getConversationRecordId(other) || keep.conversationDbId || other.conversationDbId || '',
      unreadCount: Math.max(Number(prev.unreadCount || 0), Number(item.unreadCount || 0)),
      messageCount: Math.max(Number(prev.messageCount || 0), Number(item.messageCount || 0)),
      contextCount: Math.max(Number(prev.contextCount || 0), Number(item.contextCount || 0)),
      badgeText: conversationBadgeText(keep.statusCode, keep.statusText, Math.max(Number(prev.unreadCount || 0), Number(item.unreadCount || 0)))
    })
  }
  return Array.from(map.values()).sort((a, b) => latestConversationTime(b) - latestConversationTime(a))
}

function normalizeMessages(data) {
  const list = Array.isArray(data) ? data : (data?.records || data?.list || data?.rows || data?.messages || [])
  const sorted = [...list].sort((a, b) => {
    const ta = parseMessageTimestamp(a.messageTime || a.createdTime || 0)
    const tb = parseMessageTimestamp(b.messageTime || b.createdTime || 0)
    return ta - tb
  })
  return sorted.map(item => {
    const contentType = Number(item.contentType ?? item.messageType ?? 1)
    const rawContent = extractMessageDisplayText(item)
    const inferredImages = extractImageMessageUrls(item)
    // 解析 contentType=8 商品卡片消息：从 completeMsg.rawPayload.sessionInfo.extensions 提取商品信息
    const productCard = extractProductCardFromMessage(item)
    return {
      ...item,
      id: item.id || item.messageId || stableGeneratedMessageId(item, rawContent),
      pnmId: item.pnmId || item.messageUid || item.uuid || '',
      imageUrls: inferredImages,
      msgContent: rawContent,
      displayText: extractMessageDisplayText(item, rawContent),
      senderUserId: item.senderUserId || item.fromUserId || '',
      receiverUserId: item.receiverUserId || item.toUserId || '',
      peerExternalUid: item.peerExternalUid || item.peerUserId || '',
      contentType,
      messageTime: parseMessageTimestamp(item.messageTime || item.createdTime || item.sendTime || item.time || Date.now()),
      readStatus: item.readStatus ?? item.isRead ?? item.read ?? 0,
      direction: String(item.direction || '').toUpperCase() || 'IN',
      isAutoReply: Number(item.isAutoReply ?? item.is_auto_reply ?? 0),
      card: productCard
    }
  })
}

// 从 contentType=8 商品卡片消息中提取商品信息（标题、图片、商品ID）
// completeMsg 是 JSON 字符串，结构：
//   rawPayload.sessionInfo.extensions.{itemTitle, itemMainPic, itemId}
// 或 rawPayload.sessionInfo.extensions.{itemTitle, itemMainPic, itemId}
function extractProductCardFromMessage(message) {
  if (!message) return null
  const contentType = Number(message.contentType ?? message.messageType ?? 1)
  if (contentType !== 8) return null
  // 解析 completeMsg JSON 字符串
  let completeMsg = message.completeMsg || message.complete_msg
  if (typeof completeMsg === 'string') {
    try { completeMsg = JSON.parse(completeMsg) } catch { return null }
  }
  if (!completeMsg || typeof completeMsg !== 'object') return null
  // 从 rawPayload.sessionInfo.extensions 提取商品信息
  const rawPayload = completeMsg.rawPayload || completeMsg.raw_payload || completeMsg
  const sessionInfo = rawPayload?.sessionInfo || rawPayload?.session_info || {}
  const extensions = sessionInfo?.extensions || {}
  const itemTitle = extensions.itemTitle || extensions.item_title || completeMsg.msgContent || message.msgContent || ''
  const itemMainPic = extensions.itemMainPic || extensions.item_main_pic || extensions.itemPic || ''
  const itemId = extensions.itemId || extensions.item_id || message.xyGoodsId || ''
  // 也尝试从 content.sessionArouse 提取（商品卡片消息的另一种结构）
  const content = rawPayload?.content || {}
  const sessionArouse = content?.sessionArouse || {}
  const arouseTimeStamp = sessionArouse?.arouseTimeStamp || 0
  // 如果有商品标题或图片，返回商品卡片对象
  if (itemTitle || itemMainPic) {
    return {
      title: itemTitle,
      subtitle: itemId ? `商品ID: ${itemId}` : '',
      image: itemMainPic,
      itemId,
      arouseTimeStamp
    }
  }
  return null
}

async function loadAccountsData() {
  try {
    const res = await getAccounts({ size: 200 })
    accounts.value = recordsOf(res.data)
    if (!query.xianyuAccountId && accounts.value.length) {
      const preferredAccount = accounts.value.find(isAccountAuthUsable) || accounts.value[0]
      query.xianyuAccountId = preferredAccount?.id || ''
    }
  } catch (e) {
    if (import.meta.env.DEV) console.error('[MSG] loadAccountsData failed')
    error.value = '加载账号列表失败: ' + (e.message || '网络错误')
  }
}

async function loadConversations(preserveSelected = true, { background = preserveSelected } = {}) {
  const requestedAccountId = Number(query.xianyuAccountId || 0)
  if (!requestedAccountId) {
    conversationsAvailable.value = false
    contextAvailable.value = false
    conversations.value = []
    selected.value = null
    contextMessages.value = []
    if (!error.value) error.value = '请先在账号下拉框中选择一个闲鱼账号'
    return
  }
  const requestId = ++conversationLoadRequestId
  const isBackgroundRefresh = background === true
  if (isBackgroundRefresh) {
    conversationRefreshing.value = true
  } else {
    loading.value = true
    error.value = ''
  }
  conversationRefreshNotice.value = ''
  const requestConfig = isBackgroundRefresh ? createMessageBackgroundRequestConfig() : {}
  try {
    // 首次加载：cursor=null，获取第一页
    const res = await onlineConversations(requestedAccountId, {
      cursor: null,
      pageSize: query.pageSize,
    }, requestConfig)
    const raw = unwrap(res?.data)
    // 兼容两种格式：新格式 {conversations, hasMore, nextCursor} 或旧格式平铺列表
    let list = Array.isArray(raw)
      ? raw
      : (Array.isArray(raw?.conversations) ? raw.conversations : (Array.isArray(raw?.records) ? raw.records : []))
    let hasMore = Array.isArray(raw) ? false : !!raw?.hasMore
    let nextCursor = Array.isArray(raw) ? null : raw?.nextCursor
    if (!list.length) {
      const fallbackRes = await getConversations({
        xianyuAccountId: requestedAccountId,
        page: 1,
        pageSize: query.pageSize,
      }, requestConfig)
      const fallbackRaw = unwrap(fallbackRes?.data)
      list = Array.isArray(fallbackRaw)
        ? fallbackRaw
        : (Array.isArray(fallbackRaw?.records) ? fallbackRaw.records : [])
      hasMore = false
      nextCursor = null
    }
    const nextConversations = dedupeConversations(list
      .map(toDisplayConversation)
      .filter(item => item && item.sid))
    if (!shouldApplyConversationLoadResult({
      requestId,
      latestRequestId: conversationLoadRequestId,
      requestedAccountId,
      currentAccountId: query.xianyuAccountId
    })) {
      return
    }
    const snapshot = resolveBackgroundRefreshSnapshot({
      background: isBackgroundRefresh,
      available: conversationsAvailable.value,
      currentItems: conversations.value,
      nextItems: nextConversations
    })
    if (snapshot.preserve) {
      conversationRefreshNotice.value = '后台同步暂未返回有效会话，已保留当前列表并将在下一轮自动重试'
      return
    }
    conversationsAvailable.value = true
    // 轮询模式(preserveSelected=true)下，若用户已通过"查看更多"加载了后续页，
    // 仅刷新第一页内容并保留后续页与 cursor 分页状态，避免轮询重置分页进度
    const hadPaginated = preserveSelected && conversationCursor.value != null
    if (hadPaginated) {
      const existingKeys = new Set(nextConversations.map(c => conversationDedupeKey(c)))
      const tail = conversations.value.filter(c => !existingKeys.has(conversationDedupeKey(c)))
      conversations.value = [...nextConversations, ...tail]
      // 保留 conversationCursor / conversationHasMore / visibleConversationCount 不变
    } else {
      conversations.value = nextConversations
      conversationCursor.value = nextCursor
      conversationHasMore.value = hasMore
      if (!preserveSelected) {
        visibleConversationCount.value = DEFAULT_VISIBLE_CONVERSATIONS
      }
    }
    deletedConversations.value = pruneDeletedConversationMarks(deletedConversations.value, conversations.value)
    if (!conversations.value.length) {
      selected.value = null
      contextMessages.value = []
      contextAvailable.value = false
      hasMoreContext.value = false
      // 清空编辑状态，避免切换账号后残留上一个账号的草稿
      draft.value = ''
      imageUrl.value = ''
      showEmojiPanel.value = false
      return
    }
    const matched = preserveSelected ? findPreservedConversation(conversations.value, selected.value) : null
    if (matched) {
      if (preserveSelected && conversationDedupeKey(selected.value) === conversationDedupeKey(matched)) {
        // 无感更新：保持当前会话上下文，仅合并新字段，避免不必要的重渲染
        // 已选中会话视为已读，保留阅读态但同步最新会话状态与预览字段
        selected.value = mergeSelectedConversationSnapshot(selected.value, matched, { preserveUnreadAsRead: true })
        setConversationUnread(selected.value, 0)
        // 不触发 selectChat，避免打断用户当前输入或滚动位置
      } else if (!preserveSelected || didPreservedConversationIdentityChange(selected.value, matched)) {
        // 主动 reload 时，正常切换会话（不阻塞会话列表的 loading 状态）
        selectChat(matched).catch(() => {})
        return
      } else {
        // preserveSelected=true 但对象引用不同的兜底分支
        selected.value = matched
      }
    } else if (!selected.value || !preserveSelected) {
      // 仅在无选中会话或主动 reload 时，才选择第一条会话
      // 不阻塞：让会话列表立即展示，聊天上下文后台异步加载
      selectChat(conversations.value[0]).catch(() => {})
      return
    }
    // preserveSelected=true 且未找到匹配时，保持当前选中态不变
    // 后台分批查询缺头像会话的头像
    fetchMissingAvatars(requestedAccountId, nextConversations, MESSAGE_BACKGROUND_REQUEST_CONFIG).catch(() => {})
  } catch (e) {
    if (!shouldApplyConversationLoadResult({
      requestId,
      latestRequestId: conversationLoadRequestId,
      requestedAccountId,
      currentAccountId: query.xianyuAccountId
    })) {
      return
    }
    if (import.meta.env.DEV) console.error('[MSG] loadConversations failed')
    const snapshot = resolveBackgroundRefreshSnapshot({
      background: isBackgroundRefresh,
      available: conversationsAvailable.value,
      currentItems: conversations.value,
      failed: true
    })
    if (snapshot.preserve) {
      conversationRefreshNotice.value = '会话后台同步暂时失败，当前内容已保留，系统将自动重试'
      return
    }
    conversationsAvailable.value = false
    contextAvailable.value = false
    conversations.value = []
    selected.value = null
    contextMessages.value = []
    hasMoreContext.value = false
    conversationCursor.value = null
    conversationHasMore.value = false
    draft.value = ''
    imageUrl.value = ''
    error.value = '会话加载失败: ' + (e.message || '网络错误')
  } finally {
    if (shouldApplyConversationLoadResult({
      requestId,
      latestRequestId: conversationLoadRequestId,
      requestedAccountId,
      currentAccountId: query.xianyuAccountId
    })) {
      if (isBackgroundRefresh) conversationRefreshing.value = false
      else loading.value = false
    }
  }
}

/**
 * 分批查询缺头像会话的头像（模仿目标项目 ChatNew.tsx 的头像查询逻辑）
 * - 找出缺头像或昵称为纯数字的会话
 * - 以 cid 去重，并跨轮询排除正在请求或退避中的 cid
 * - 每批 3 个，调用 POST /msg/avatars
 * - 每批完成只合并头像/昵称；账号切换或卸载后的响应不再写入页面
 */
function resetAvatarLookupsForAccount(accountId) {
  const normalizedAccountId = String(accountId || '').trim()
  if (avatarLookupState.disposed || avatarLookupState.accountId === normalizedAccountId) return
  avatarLookupState = resetAvatarLookupState(avatarLookupState, accountId)
  userInfoCacheRef.value = {}
  for (const url of Object.keys(failedAvatarUrls)) delete failedAvatarUrls[url]
}

function isAvatarLookupActive(accountId) {
  const requestedAccountId = String(accountId || '').trim()
  const currentAccountId = String(query.xianyuAccountId || '').trim()
  return Boolean(
    requestedAccountId &&
    !avatarLookupState.disposed &&
    avatarLookupState.accountId === requestedAccountId &&
    currentAccountId === requestedAccountId
  )
}

function hasCompleteAvatarIdentity(conversation) {
  const name = String(resolveConversationName(conversation) || '').trim()
  return Boolean(conversationAvatarUrl(conversation) && name && !/^\d+$/.test(name))
}

async function fetchMissingAvatars(accountId, convs, requestConfig = MESSAGE_BACKGROUND_REQUEST_CONFIG) {
  if (!accountId || !Array.isArray(convs) || !convs.length) return
  const requestedAccountId = String(accountId).trim()
  if (!isAvatarLookupActive(requestedAccountId)) {
    const currentAccountId = String(query.xianyuAccountId || '').trim()
    if (avatarLookupState.disposed || currentAccountId !== requestedAccountId) return
    // 初次账号加载可能早于 watcher flush；只允许当前账号建立自己的隔离状态。
    resetAvatarLookupsForAccount(requestedAccountId)
  }

  // 找出缺头像或昵称为纯数字的会话
  const missing = convs.filter(c => {
    const cid = String(c?.sid || '').trim()
    if (!cid) return false
    const hasAvatar = !!conversationAvatarUrl(c)
    const resolvedName = String(resolveConversationName(c) || '').trim()
    const hasValidName = !!resolvedName && !/^\d+$/.test(resolvedName)
    return !hasAvatar || !hasValidName
  })
  if (!missing.length) return

  const conversationsByCid = new Map()
  for (const conversation of missing) {
    const cid = String(conversation?.sid || '').trim()
    if (!conversationsByCid.has(cid)) conversationsByCid.set(cid, [])
    conversationsByCid.get(cid).push(conversation)
  }
  const planned = planAvatarLookups(avatarLookupState, {
    accountId: requestedAccountId,
    cids: Array.from(conversationsByCid.keys()),
    now: Date.now()
  })
  avatarLookupState = planned.state
  if (!planned.cids.length) return

  // 分批查询（每批 3 个），每批完成立即更新 UI
  const BATCH_SIZE = 3
  for (let i = 0; i < planned.cids.length; i += BATCH_SIZE) {
    if (!isAvatarLookupActive(requestedAccountId)) return
    const batchCids = planned.cids.slice(i, i + BATCH_SIZE)
    const batchCidSet = new Set(batchCids)
    const batch = batchCids.map(cid => ({ cid }))
    try {
      const res = await queryUserAvatars(accountId, batch, requestConfig)
      if (!isAvatarLookupActive(requestedAccountId)) return
      const payloadItems = res?.data?.items || res?.data?.data?.items || []
      const items = (Array.isArray(payloadItems) ? payloadItems : []).filter(item =>
        batchCidSet.has(String(item?.cid || '').trim())
      )
      // 合并当前账号缓存；供应商部分响应不能抹掉已确认的头像或昵称。
      for (const item of items) {
        const cid = String(item?.cid || '').trim()
        if (!cid) continue
        const previous = userInfoCacheRef.value[cid] || {}
        userInfoCacheRef.value[cid] = {
          avatar: String(item.avatar || previous.avatar || '').trim(),
          nick: String(item.nick || previous.nick || '').trim()
        }
      }
      const patched = applyAvatarIdentityResults(conversations.value, selected.value, items)
      conversations.value = patched.conversations
      selected.value = patched.selected

      const resolvedCids = batchCids.filter(cid => {
        const matchingConversations = conversationsByCid.get(cid) || []
        return matchingConversations.length > 0 && matchingConversations.every(hasCompleteAvatarIdentity)
      })
      avatarLookupState = settleAvatarLookups(avatarLookupState, {
        accountId: requestedAccountId,
        requestedCids: batchCids,
        resolvedCids,
        now: Date.now(),
        baseRetryMs: AVATAR_RETRY_BASE_MS,
        maxRetryMs: AVATAR_RETRY_MAX_MS
      })
    } catch {
      if (!isAvatarLookupActive(requestedAccountId)) return
      avatarLookupState = settleAvatarLookups(avatarLookupState, {
        accountId: requestedAccountId,
        requestedCids: batchCids,
        resolvedCids: [],
        now: Date.now(),
        baseRetryMs: AVATAR_RETRY_BASE_MS,
        maxRetryMs: AVATAR_RETRY_MAX_MS
      })
      if (import.meta.env.DEV) console.warn('[MSG] fetchMissingAvatars batch failed')
    }
  }
}

async function reload() {
  if (conversationsAvailable.value) {
    await loadConversations(true, { background: true })
  } else {
    await loadConversations(false)
  }
}

async function startCurrentConnection() {
  if (!query.xianyuAccountId) {
    error.value = '请先选择账号'
    return
  }
  try {
    error.value = ''
    await startWebSocket(Number(query.xianyuAccountId))
    events.value.unshift({ text: '连接请求已提交，请稍后刷新会话', time: new Date().toLocaleTimeString('zh-CN', { hour12: false }) })
    events.value = events.value.slice(0, 20)
    setTimeout(() => reload(), 1500)
  } catch (e) {
    error.value = '启动连接失败: ' + (e.message || '网络错误')
  }
}

async function refreshAll(scrollBottom = false) {
  await Promise.all([
    loadConversations(true, { background: true }),
    loadContext(scrollBottom, { background: true })
  ])
}

async function selectChat(chat) {
  if (!chat?.sid) return
  showEmojiPanel.value = false
  if (conversationDedupeKey(selected.value) !== conversationDedupeKey(chat)) {
    invalidateContextLoads()
  }

  // 如果该会话已被标记为删除，直接显示提示并清空消息
  if (isConversationDeleted(deletedConversations.value, chat)) {
    selected.value = { ...chat, unreadCount: 0 }
    contextMessages.value = []
    contextAvailable.value = false
    hasMoreContext.value = false
    setConversationUnread(chat, 0)
    return
  }

  if (conversationDedupeKey(selected.value) === conversationDedupeKey(chat)) {
    selected.value = { ...selected.value, unreadCount: 0 }
    setConversationUnread(chat, 0)
    const id = conversationDbId(chat)
    if (id && shouldMarkConversationAsRead(chat)) {
      markConversationRead(id, MESSAGE_BACKGROUND_REQUEST_CONFIG).catch(() => {})
    }
    // Mark incoming messages as read locally
    contextMessages.value = contextMessages.value.map(m =>
      !isMe(m) && (m.readStatus === 0 || m.read_status === 0)
        ? { ...m, readStatus: 1, read_status: 1 }
        : m
    )
    if (!contextMessages.value.length) {
      contextQuery.offset = 0
      await loadContext()
    }
    return
  }

  // 切换到新会话前，先把"上一个正在查看的会话"标记为已读。
  // 原因：查看上一个会话期间，可能又通过 SSE 收到了新消息，本地 unreadCount 虽被置 0，
  // 但后端 xianyu_conversation.unread_count 可能仍 > 0，导致侧栏仍显示未读红点。
  const previousConversation = selected.value
  if (previousConversation && conversationDedupeKey(previousConversation) !== conversationDedupeKey(chat)) {
    const previousId = conversationDbId(previousConversation)
    if (previousId) {
      markConversationRead(previousId, MESSAGE_BACKGROUND_REQUEST_CONFIG).catch(() => {})
      setConversationUnread(previousConversation, 0)
    }
  }

  selected.value = { ...chat, unreadCount: 0 }
  setConversationUnread(chat, 0)
  const id = conversationDbId(chat)
  if (id && shouldMarkConversationAsRead(chat)) {
    markConversationRead(id, MESSAGE_BACKGROUND_REQUEST_CONFIG).catch(() => {})
  }
  contextQuery.offset = 0
  await loadContext()
}

async function loadContext(scrollBottom = true, { background = false } = {}) {
  const requestedAccountId = Number(query.xianyuAccountId || 0)
  const requestedConversation = selected.value ? { ...selected.value } : null
  if (!requestedConversation?.sid || !requestedAccountId) return
  const requestId = ++contextLoadRequestId
  const isBackgroundRefresh = background === true
  if (isBackgroundRefresh) {
    contextRefreshing.value = true
  } else {
    contextLoading.value = true
    contextAvailable.value = false
    error.value = ''
  }
  contextRefreshNotice.value = ''
  const requestConfig = isBackgroundRefresh ? createMessageBackgroundRequestConfig() : {}
  const shouldApplyResult = () => shouldApplyContextLoadResult({
    requestId,
    latestRequestId: contextLoadRequestId,
    requestedAccountId,
    currentAccountId: query.xianyuAccountId,
    requestedConversation,
    currentConversation: selected.value
  })
  try {
    const rawPeerKey = String(requestedConversation.peerKey || '')
    const sid = normalizeSid(requestedConversation.sid) || (rawPeerKey.startsWith('sid:') ? rawPeerKey.slice(4) : '')
    const peerUserId = normalizePeerUserId(
      requestedConversation.peerUserId ||
      requestedConversation.externalBuyerId ||
      requestedConversation.peerExternalUid ||
      requestedConversation.receiverUserId ||
      ''
    )
    const basePayload = {
      xianyuAccountId: requestedAccountId,
      sid,
      sId: sid,
      sessionId: sid,
      peerUserId,
      limit: contextQuery.limit,
      offset: contextQuery.offset
    }
    let res = await messageContext(basePayload, requestConfig)
    let nextMessages = normalizeMessages(unwrap(res.data))
    if (!nextMessages.length && peerUserId) {
      res = await messageContext({
        ...basePayload,
        sid: '',
        sId: '',
        sessionId: ''
      }, requestConfig)
      nextMessages = normalizeMessages(unwrap(res.data))
    }
    const nextHasMoreContext = nextMessages.length >= contextQuery.limit
    // 保留本地发送中或发送失败的乐观消息，避免轮询覆盖掉它们
    const localPendingMessages = contextMessages.value.filter(m =>
      shouldKeepLocalPendingContextMessage(m, sid, peerUserId)
    )
    const merged = localPendingMessages.length ? [...nextMessages, ...localPendingMessages] : nextMessages
    if (!shouldApplyResult()) return
    const snapshot = resolveBackgroundRefreshSnapshot({
      background: isBackgroundRefresh,
      available: contextAvailable.value,
      currentItems: contextMessages.value,
      nextItems: nextMessages
    })
    if (snapshot.preserve) {
      contextRefreshNotice.value = '后台同步暂未返回有效聊天记录，已保留当前消息并将在下一轮自动重试'
      return
    }
    hasMoreContext.value = nextHasMoreContext
    contextMessages.value = normalizeContextMessageList(merged)
    contextAvailable.value = true
    contextQuery.offset = 0
    await nextTick()
    if (scrollBottom && shouldApplyResult()) scrollToBottom()
  } catch (e) {
    if (!shouldApplyResult()) return
    const snapshot = resolveBackgroundRefreshSnapshot({
      background: isBackgroundRefresh,
      available: contextAvailable.value,
      currentItems: contextMessages.value,
      failed: true
    })
    if (snapshot.preserve) {
      contextRefreshNotice.value = '聊天记录后台同步暂时失败，当前消息已保留，系统将自动重试'
      return
    }
    error.value = '上下文加载失败: ' + (e.message || '网络错误')
    contextAvailable.value = false
    contextMessages.value = []
    hasMoreContext.value = false
  } finally {
    if (shouldApplyResult()) {
      if (isBackgroundRefresh) contextRefreshing.value = false
      else contextLoading.value = false
    }
  }
}

async function loadMoreContext() {
  const requestedAccountId = Number(query.xianyuAccountId || 0)
  const requestedConversation = selected.value ? { ...selected.value } : null
  if (!requestedConversation?.sid || !requestedAccountId || contextLoadingMore.value) return
  const requestId = ++contextLoadRequestId
  contextLoadingMore.value = true
  const shouldApplyResult = () => shouldApplyContextLoadResult({
    requestId,
    latestRequestId: contextLoadRequestId,
    requestedAccountId,
    currentAccountId: query.xianyuAccountId,
    requestedConversation,
    currentConversation: selected.value
  })
  try {
    const newOffset = contextQuery.offset + contextQuery.limit
    const rawPeerKey = String(requestedConversation.peerKey || '')
    const sid = normalizeSid(requestedConversation.sid) || (rawPeerKey.startsWith('sid:') ? rawPeerKey.slice(4) : '')
    const peerUserId = normalizePeerUserId(
      requestedConversation.peerUserId ||
      requestedConversation.externalBuyerId ||
      requestedConversation.peerExternalUid ||
      requestedConversation.receiverUserId ||
      ''
    )
    const basePayload = {
      xianyuAccountId: requestedAccountId,
      sid,
      sId: sid,
      sessionId: sid,
      peerUserId,
      limit: contextQuery.limit,
      offset: newOffset
    }
    let res = await messageContext(basePayload)
    let newMessages = normalizeMessages(unwrap(res.data))
    if (!newMessages.length && peerUserId) {
      res = await messageContext({
        ...basePayload,
        sid: '',
        sId: '',
        sessionId: ''
      })
      newMessages = normalizeMessages(unwrap(res.data))
    }
    if (!shouldApplyResult()) return
    hasMoreContext.value = newMessages.length >= contextQuery.limit
    if (newMessages.length > 0) {
      const merged = [...newMessages, ...contextMessages.value]
      contextMessages.value = normalizeContextMessageList(merged)
      contextQuery.offset = newOffset
    }
  } catch (e) {
    if (!shouldApplyResult()) return
    error.value = '加载更多历史消息失败: ' + (e.message || '网络错误')
  } finally {
    if (requestId === contextLoadRequestId) {
      contextLoadingMore.value = false
    }
  }
}

function scrollToBottom() {
  const el = messagesContainer.value
  if (el) {
    el.scrollTop = el.scrollHeight
  }
}

function createManualMessageIdempotencyKey(kind = 'message') {
  const uuid = globalThis.crypto?.randomUUID?.().replace(/[^A-Za-z0-9]/g, '')
  const entropy = uuid || `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`
  return `web-${kind}-${entropy}`.slice(0, 128)
}

function applyManualMessageOutcome(message, outcome) {
  if (outcome.status === 'confirmed') {
    return {
      ...message,
      sendStatus: 'sent',
      retrySafe: false,
      readStatus: 1,
      pnmId: outcome.uuid || message.pnmId,
      id: outcome.uuid || message.id
    }
  }
  if (outcome.status === 'failed' && outcome.retrySafe) {
    return {
      ...message,
      sendStatus: 'failed',
      retrySafe: true,
      sendErrorCode: outcome.errorCode
    }
  }
  return {
    ...message,
    sendStatus: 'unknown',
    retrySafe: false,
    sendErrorCode: outcome.errorCode || 'message_result_unknown'
  }
}

function manualMessageErrorText(outcome, label = '消息') {
  if (outcome.status === 'failed' && outcome.retrySafe) {
    return outcome.message || `${label}明确未发送，排查账号或会话状态后可安全重试。`
  }
  return outcome.message || `${label}发送结果未确认，请先在闲鱼 App 核对；请勿直接重试。`
}

async function sendText() {
  if (!messageDataAvailable.value) {
    error.value = '会话或聊天记录不可用，已暂停发送；请先重试加载。'
    return
  }
  const text = draft.value.trim()
  if (!text) return
  if (text.length > 1000) {
    error.value = '消息不能超过 1000 字'
    return
  }
  if (!selected.value) {
    error.value = '请先选择会话'
    return
  }
  if (!query.xianyuAccountId) {
    error.value = '请先选择账号'
    return
  }
  const receiverId = resolveReceiverId(selected.value) || normalizeSid(selected.value.sid) || ''
  sending.value = true
  showEmojiPanel.value = false
  error.value = ''
  const account = accounts.value.find(a => a.id === Number(query.xianyuAccountId))
  const tempId = `temp_${Date.now()}`
  const idempotencyKey = createManualMessageIdempotencyKey('text')
  const optimisticMsg = {
    id: tempId,
    pnmId: tempId,
    sId: selected.value.sid,
    sid: selected.value.sid,
    contentType: 1,
    msgContent: text,
    displayText: text,
    senderUserId: toGoofishId(account?.unb || account?.externalUid || ''),
    direction: 'OUT',
    messageTime: Date.now(),
    messageKind: 'text',
    readStatus: 0,
    sendStatus: 'sending',
    retrySafe: false,
    idempotencyKey
  }
  const previousConversation = findConversationByIdentity(conversations.value, selected.value)
  contextMessages.value = normalizeContextMessageList([...contextMessages.value, optimisticMsg])
  updateConversationPreview(selected.value, item => ({
    ...item,
    msg: shortText(text, 42),
    time: time(Date.now()),
    lastMessageTime: Date.now()
  }))
  draft.value = ''
  await nextTick()
  scrollToBottom()

  try {
    const wsStatusRes = await websocketStatus(Number(query.xianyuAccountId))
    const wsStatus = unwrap(wsStatusRes.data)
    if (!wsStatus?.connected) {
      try {
        await startWebSocket(Number(query.xianyuAccountId))
        await new Promise(resolve => setTimeout(resolve, 3000))
      } catch {
        if (import.meta.env.DEV) console.warn('[MSG] WebSocket start failed before send')
      }
    }
    const payload = {
      xianyuAccountId: Number(query.xianyuAccountId),
      cid: selected.value.sid,
      sid: selected.value.sid,
      sId: selected.value.sid,
      sessionId: selected.value.sid,
      toId: receiverId,
      peerUserId: receiverId,
      text,
      content: text,
      message: text,
      xyGoodsId: selected.value.xyGoodsId,
      idempotencyKey
    }
    const sendRes = await sendMessage(payload)
    const outcome = resolveManualMessageOutcome(sendRes)
    contextMessages.value = normalizeContextMessageList(
      contextMessages.value.map(item =>
        item.id === tempId ? applyManualMessageOutcome(item, outcome) : item
      )
    )
    if (outcome.status === 'confirmed') {
      events.value.unshift({
        text: '平台已确认消息发送',
        time: new Date().toLocaleTimeString('zh-CN', { hour12: false })
      })
      events.value = events.value.slice(0, 20)
      // 只刷新会话列表，不立即刷新消息上下文。
      // 消息上下文的刷新交给 SSE 的 sseDebounceTimer（500ms 后），
      // 避免 loadContext 过早用 DB 数据覆盖本地 sent 消息，导致后续 SSE 事件无法合并而重复显示。
      await loadConversations(true, { background: true })
    } else {
      if (outcome.status === 'failed' && outcome.retrySafe && previousConversation) {
        updateConversationPreview(previousConversation, () => previousConversation)
      }
      error.value = manualMessageErrorText(outcome)
    }
  } catch (e) {
    const outcome = resolveManualMessageError(e)
    contextMessages.value = contextMessages.value.map(item =>
      item.id === tempId ? applyManualMessageOutcome(item, outcome) : item
    )
    error.value = manualMessageErrorText(outcome)
  } finally {
    sending.value = false
  }
}

function handleEditorKeydown(event) {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    sendText()
  }
}

async function retrySendMessage(message) {
  if (!messageDataAvailable.value) {
    error.value = '会话或聊天记录不可用，已暂停重试；请先重试加载。'
    return
  }
  if (!shouldRetryManualMessage(message)) {
    error.value = '该消息发送结果并非明确失败，请先在闲鱼 App 核对，系统已禁止直接重试。'
    return
  }
  if (!message.idempotencyKey) {
    error.value = '该消息缺少幂等凭据，无法安全重试；请重新核对会话后新建消息。'
    return
  }
  const retryAction = resolveRetryMessageAction(message)
  if (retryAction.kind === 'unsupported') {
    error.value = retryAction.reason === 'image'
      ? '该图片消息缺少可重试的图片地址，请重新发送图片。'
      : '该消息缺少可重试内容，请重新发送。'
    return
  }
  contextMessages.value = contextMessages.value.map(item =>
    item.id === message.id ? { ...item, sendStatus: 'sending', retrySafe: false } : item
  )
  error.value = ''
  const receiverId = resolveReceiverId(selected.value) || normalizeSid(selected.value.sid) || ''
  try {
    let retryRes
    if (retryAction.kind === 'image') {
      retryRes = await sendImageMessage({
        xianyuAccountId: Number(query.xianyuAccountId),
        cid: selected.value.sid,
        sid: selected.value.sid,
        sId: selected.value.sid,
        sessionId: selected.value.sid,
        toId: receiverId,
        peerUserId: receiverId,
        imageUrl: retryAction.imageUrl,
        xyGoodsId: selected.value.xyGoodsId,
        idempotencyKey: message.idempotencyKey
      })
    } else {
      const text = retryAction.text
      retryRes = await sendMessage({
        xianyuAccountId: Number(query.xianyuAccountId),
        cid: selected.value.sid,
        sid: selected.value.sid,
        sId: selected.value.sid,
        sessionId: selected.value.sid,
        toId: receiverId,
        peerUserId: receiverId,
        text,
        content: text,
        message: text,
        xyGoodsId: selected.value.xyGoodsId,
        idempotencyKey: message.idempotencyKey
      })
    }
    const outcome = resolveManualMessageOutcome(retryRes)
    contextMessages.value = contextMessages.value.map(item =>
      item.id === message.id ? applyManualMessageOutcome(item, outcome) : item
    )
    if (outcome.status === 'confirmed') {
      if (retryAction.kind === 'image') {
        await loadConversations(true, { background: true })
      } else {
        await refreshAll(true)
      }
    } else {
      error.value = manualMessageErrorText(outcome, retryAction.kind === 'image' ? '图片' : '消息')
    }
  } catch (e) {
    const outcome = resolveManualMessageError(e)
    contextMessages.value = contextMessages.value.map(item =>
      item.id === message.id ? applyManualMessageOutcome(item, outcome) : item
    )
    error.value = manualMessageErrorText(outcome, retryAction.kind === 'image' ? '图片' : '消息')
  }
}

function onSse(event) {
  // 记录 SSE 事件到达时间，用于判断连接健康状态
  lastSseActivity.value = Date.now()
  const detail = event?.detail
  const data = detail?.payload || detail || {}
  const eventType = detail?.type || data.type || data.event || 'message'
  const incomingSid = normalizeSid(data.sId || data.sid || data.cid || data.sessionId || '')
  const incomingPeerUserId = normalizePeerUserId(
    data.peerUserId || data.peerExternalUid || data.senderUserId || data.receiverUserId || ''
  )
  const isRelevant = eventType === 'message' || data.message || data.content || data.msgContent || incomingSid
  if (!isRelevant) return
  if (!matchesAccountSelection(String(query.xianyuAccountId || ''), data)) return

  events.value.unshift({
    text: shortText(data.message || data.content || data.msgContent || JSON.stringify(data), 40),
    time: new Date().toLocaleTimeString('zh-CN', { hour12: false })
  })
  events.value = events.value.slice(0, 20)

  const normalizedIncoming = normalizeMessages([data])[0]
  const currentChat = isSameConversationByPayload(selected.value, data)
  const incomingIdentity = messageIdentity(normalizedIncoming)
  const exists = contextMessages.value.some(item => messageIdentity(item) === incomingIdentity)

  // 对自己发送的 OUT 消息：同一条消息会通过三个源到达前端，且 pnmId 各不相同：
  //   1. 前端乐观更新（pnmId=temp_xxx）
  //   2. 后端 misc.py SSE 广播（pnmId=sent_uuid，后端生成的 UUID）
  //   3. WS 回环 SSE 广播（pnmId=real_pnmId，闲鱼服务端真实 pnmId）
  // 此外 loadContext 可能用 DB 数据（pnmId="" 或 message_uid）覆盖本地 sent 消息，
  // 导致后续 SSE 事件无法基于 sendStatus/pnmId 合并。
  // 因此这里改为基于「OUT 方向 + 内容相同 + 时间窗口内」合并，不依赖 sendStatus 和 pnmId，
  // 确保三个源 + DB 数据都能合并成同一条消息。
  const isOutgoing = String(normalizedIncoming.direction || '').toUpperCase() === 'OUT'
  let mergedWithPending = false
  if (currentChat && !exists && isOutgoing) {
    const incomingContent = String(normalizedIncoming.msgContent || normalizedIncoming.content || '').trim()
    const incomingTime = parseMessageTimestamp(normalizedIncoming.messageTime || normalizedIncoming.createdTime || 0)
    if (incomingContent) {
      contextMessages.value = contextMessages.value.map(item => {
        if (mergedWithPending) return item
        // 只匹配 OUT 方向的本地消息（IN 方向是买家消息，不合并）
        if (String(item.direction || '').toUpperCase() !== 'OUT') return item
        const itemContent = String(item.msgContent || item.content || '').trim()
        if (itemContent !== incomingContent) return item
        const itemTime = parseMessageTimestamp(item.messageTime || item.createdTime || 0)
        // 同会话 + 内容相同 + 60 秒时间窗口内，视为同一条消息
        if (itemTime && incomingTime && Math.abs(itemTime - incomingTime) > 60000) return item
        // 命中合并：更新 pnmId（优先用 incoming 的真实 pnmId）、状态等
        mergedWithPending = true
        const itemPnmId = String(item.pnmId || '')
        const incomingPnmId = String(normalizedIncoming.pnmId || '')
        return {
          ...item,
          pnmId: incomingPnmId || itemPnmId,
          id: (normalizedIncoming.id && !String(normalizedIncoming.id).startsWith('temp_')) ? normalizedIncoming.id : item.id,
          sendStatus: 'sent',
          retrySafe: false,
          readStatus: 1,
          messageTime: normalizedIncoming.messageTime || item.messageTime
        }
      })
    }
  }

  if (currentChat && !exists && !mergedWithPending) {
    contextMessages.value = normalizeContextMessageList([...contextMessages.value, normalizedIncoming])
    nextTick(() => scrollToBottom())
  }

  upsertConversationFromEvent({ ...data, sid: incomingSid, peerUserId: incomingPeerUserId }, { currentChat })

  if (sseDebounceTimer) clearTimeout(sseDebounceTimer)
  sseDebounceTimer = setTimeout(async () => {
    if (currentChat) {
      await loadContext(false, { background: true })
    }
    await loadConversations(true, { background: true })
  }, 500)
}

// ---- 轮询后备逻辑 ----
function getPollingInterval() {
  const now = Date.now()
  // 从未收到 SSE 事件或 SSE 已过期时，使用快速轮询
  if (lastSseActivity.value === 0) {
    sseHealthy.value = false
    return POLL_INTERVAL_FALLBACK
  }
  const elapsed = now - lastSseActivity.value
  // 如果 SSE 长时间未到达，则缩短轮询间隔
  if (elapsed > SSE_STALE_TIMEOUT) {
    sseHealthy.value = false
    return POLL_INTERVAL_FALLBACK
  }
  sseHealthy.value = true
  // SSE 健康时降低轮询频率，仅做 WebSocket 健康检查，避免与 SSE 重复刷新
  return POLL_INTERVAL_SSE_HEALTHY
}

const runPollMessages = createSingleFlightTask(async function pollMessages() {
  const requestedAccountId = Number(query.xianyuAccountId || 0)
  if (!requestedAccountId) return
  const requestToken = pollRequestGuard.begin(`account:${requestedAccountId}`)
  const isCurrentRequest = () => pollRequestGuard.isCurrent(
    requestToken,
    `account:${Number(query.xianyuAccountId || 0)}`
  )
  const requestedAccount = accounts.value.find(account => Number(account?.id) === requestedAccountId) || null
  try {
    // 定期检查 WebSocket 连接健康状态（每 30s 检查一次）
    const now = Date.now()
    if (now - lastWsHealthCheck > 30000) {
      lastWsHealthCheck = now
      try {
        if (isAccountAuthUsable(requestedAccount)) {
          const statusRes = await websocketStatus(requestedAccountId, MESSAGE_BACKGROUND_REQUEST_CONFIG)
          if (!isCurrentRequest()) return
          const status = unwrap(statusRes.data)
          if (!status?.connected) {
            if (import.meta.env.DEV) {
              console.warn('[MSG] 轮询检测到 WebSocket 断开，尝试重启')
            }
            await startWebSocket(requestedAccountId, MESSAGE_BACKGROUND_REQUEST_CONFIG).catch(() => {})
            if (!isCurrentRequest()) return
          }
        }
      } catch {
        // 健康检查失败不应中断轮询
      }
    }
    // SSE 健康时跳过会话列表刷新（SSE 会实时推送新消息），仅做 WS 健康检查
    // SSE 不健康时才需要轮询刷新会话列表和消息流
    if (!sseHealthy.value) {
      if (!isCurrentRequest()) return
      await loadConversations(true, { background: true })
      if (!isCurrentRequest()) return
      // 当前若有选中会话，也同步刷新其消息流
      if (selected.value?.sid) {
        await loadContext(false, { background: true })
      }
    }
  } catch {
    if (import.meta.env.DEV) console.warn('[MSG] polling failed; will retry')
  }
})

function startPolling() {
  stopPolling()
  if (!query.xianyuAccountId) return
  pollingActive.value = true
  pollingTimer = setTimeout(async function tick() {
    if (!pollingActive.value) return
    try {
      await runPollMessages()
    } catch {
      if (import.meta.env.DEV) console.warn('[MSG] runPollMessages failed')
    }
    if (pollingActive.value) {
      pollingTimer = setTimeout(tick, getPollingInterval())
    }
  }, getPollingInterval())
}

function stopPolling() {
  pollingActive.value = false
  if (pollingTimer) {
    clearTimeout(pollingTimer)
    pollingTimer = null
  }
}

// ---- end ----

onMounted(async () => {
  window.addEventListener('xya-sse-event', onSse)
  loadQuickTemplates()
  try {
    await loadAccountsData()
    if (query.xianyuAccountId) {
      try {
        await ensureSelectedAccountWsReady()
      } catch {
        if (import.meta.env.DEV) console.warn('[MSG] initial WebSocket start failed')
      }
    }
    await Promise.all([
      loadConversations(false),
      loadAiCsSetting()
    ])
    // 启动轮询后备机制：在 SSE 真正恢复前，先使用快速轮询
    startPolling()
  } catch (e) {
    if (import.meta.env.DEV) console.error('[MSG] initialization failed')
    error.value = '初始化失败: ' + (e.message || '未知错误')
  } finally {
    initialMountActive = false
  }

  // 页面可见性变化时暂停/恢复轮询
  visibilityChangeHandler = () => {
    if (document.hidden) {
      stopPolling()
    } else {
      if (query.xianyuAccountId) {
        startPolling()
        // 从后台切回时立即刷新一次
        loadConversations(true, { background: true }).catch(() => {})
      }
    }
  }
  document.addEventListener('visibilitychange', visibilityChangeHandler)
})

onBeforeUnmount(() => {
  invalidateMessageLoads()
  pollRequestGuard.invalidate()
  aiSettingsRequestGuard.invalidate()
  aiMutationRequestGuard.invalidate()
  avatarLookupState = disposeAvatarLookupState(avatarLookupState)
  userInfoCacheRef.value = {}
  window.removeEventListener('xya-sse-event', onSse)
  if (sseDebounceTimer) clearTimeout(sseDebounceTimer)
  stopPolling()
  if (visibilityChangeHandler) {
    document.removeEventListener('visibilitychange', visibilityChangeHandler)
    visibilityChangeHandler = null
  }
})

// 账号切换时重新启动轮询
watch(() => query.xianyuAccountId, () => {
  if (initialMountActive) return
  invalidateMessageLoads()
  pollRequestGuard.invalidate()
  lastWsHealthCheck = 0
  aiSettingsRequestGuard.invalidate()
  aiMutationRequestGuard.invalidate()
  aiSwitchLoading.value = false
  aiSettingsAvailable.value = false
  aiSettingsLoading.value = Boolean(query.xianyuAccountId)
  aiSettingsRefreshNotice.value = ''
  aiAutoReplyEnabled.value = false
  aiGlobalEnabled.value = false
  aiAccountScopes.value = {}
  aiScopeProducts.value = []
  resetAvatarLookupsForAccount(query.xianyuAccountId)
  conversationsAvailable.value = false
  contextAvailable.value = false
  conversationRefreshNotice.value = ''
  contextRefreshNotice.value = ''
  if (query.xianyuAccountId) {
    const nextState = resolveAccountSwitchState({
      selectedAccountId: query.xianyuAccountId,
      previousSelectedConversation: selected.value,
      deletedConversations: deletedConversations.value,
      contextMessages: contextMessages.value,
      error: error.value
    })
    selected.value = nextState.selected
    contextMessages.value = nextState.contextMessages
    hasMoreContext.value = nextState.hasMoreContext
    deletedConversations.value = nextState.deletedConversations
    error.value = nextState.error
    contextQuery.offset = 0
    // 切换账号时重置 SSE 状态，让轮询先使用快速间隔
    lastSseActivity.value = 0
    sseHealthy.value = false
    startPolling()
    // 优先加载会话列表（用户最关注的视觉反馈）
    loadConversations(false).catch(() => {})
    // AI 客服设置延后加载，避免阻塞会话列表展示
    setTimeout(() => {
      loadAiCsSetting({ background: true }).catch(() => {})
    }, 0)
  } else {
    selected.value = null
    contextMessages.value = []
    hasMoreContext.value = false
    deletedConversations.value = new Set()
    contextQuery.offset = 0
    // 清空编辑状态，避免切换账号后残留上一个账号的草稿
    draft.value = ''
    imageUrl.value = ''
    showEmojiPanel.value = false
    stopPolling()
  }
}, { flush: 'sync' })

watch(() => selected.value?.xyGoodsId, () => {
  aiMutationRequestGuard.invalidate()
  aiSwitchLoading.value = false
  refreshAiScopeState()
})
</script>

<style scoped>
.xya-msg-page {
  width: 100%;
}

.xya-msg-layout {
  height: calc(100vh - 188px);
  min-height: calc(100vh - 188px);
  display: grid;
  grid-template-columns: 334px minmax(0, 1fr) 288px;
  gap: 16px;
  align-items: stretch;
}

.xya-msg-sidebar,
.xya-msg-chat-panel,
.xya-msg-card {
  background: rgba(255, 255, 255, 0.96);
  border: 1px solid #edf2fb;
  box-shadow: 0 14px 40px rgba(23, 61, 135, 0.08);
}

.xya-msg-sidebar,
.xya-msg-chat-panel,
.xya-msg-detail-panel {
  min-height: 0;
}

.xya-msg-sidebar {
  height: 100%;
  border-radius: 24px;
  padding: 16px 0 14px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.xya-msg-sidebar-head,
.xya-msg-search-row,
.xya-msg-filter-row,
.xya-msg-footer-note,
.xya-msg-alert {
  padding-left: 16px;
  padding-right: 16px;
}

.xya-msg-sidebar-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 14px;
}

.xya-msg-sidebar-head h2,
.xya-msg-card h3 {
  margin: 0;
  font-size: 18px;
  font-weight: 800;
  color: #14213d;
}

.xya-msg-sidebar-head p,
.xya-msg-chat-user p,
.xya-msg-profile-head p,
.xya-msg-template-item span,
.xya-msg-product-info span,
.xya-msg-status-row span,
.xya-msg-metric-grid span {
  margin: 4px 0 0;
  color: #6b7a90;
  font-size: 12px;
  line-height: 1.5;
}

.xya-msg-title-row,
.xya-msg-name-line,
.xya-msg-chat-user-line,
.xya-msg-card-head,
.xya-msg-status-row,
.xya-msg-editor-actions,
.xya-msg-product-line,
.xya-msg-conversation-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.xya-msg-ai-row {
  display: flex;
  margin-bottom: 6px;
}

.xya-msg-ai-row.me {
  justify-content: flex-end;
}

.xya-msg-ai-tag {
  display: inline-flex;
  align-items: center;
  height: 20px;
  padding: 0 8px;
  border-radius: 999px;
  background: rgba(22, 163, 74, 0.1);
  color: #15803d;
  font-size: 11px;
  font-weight: 700;
  line-height: 20px;
  white-space: nowrap;
}

.xya-msg-ai-tag.inline {
  margin-right: 6px;
  vertical-align: middle;
}

.xya-msg-title-row {
  justify-content: flex-start;
}

.xya-msg-count,
.xya-msg-buyer-tag,
.xya-msg-unread {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 700;
}

.xya-msg-count {
  min-width: 28px;
  padding: 0 10px;
  height: 24px;
  background: #e9f1ff;
  color: #2563eb;
}

.xya-msg-realtime-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 9px 3px 7px;
  border-radius: 999px;
  font-size: 11px;
  line-height: 1;
  background: #f8fafc;
  color: #64748b;
  border: 1px solid #cbd5e1;
  transition: all .3s ease;
  flex-shrink: 0;
}
.xya-msg-realtime-badge.connected {
  background: #f0fdf4;
  color: #16a34a;
  border-color: #bbf7d0;
}
.xya-msg-realtime-badge.polling {
  background: #fefce8;
  color: #ca8a04;
  border-color: #fde68a;
}
.xya-msg-realtime-dot {
  display: inline-block;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #94a3b8;
}
.xya-msg-realtime-badge.connected .xya-msg-realtime-dot {
  background: #22c55e;
  animation: xya-msg-dot-pulse 2s ease-in-out infinite;
}
.xya-msg-realtime-badge.polling .xya-msg-realtime-dot {
  background: #eab308;
  animation: xya-msg-dot-blink 1s ease-in-out infinite;
}
@keyframes xya-msg-dot-pulse {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.6; transform: scale(0.85); }
}
@keyframes xya-msg-dot-blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.3; }
}
.xya-msg-realtime-label {
  font-weight: 600;
}

.xya-msg-tabs,
.xya-msg-quick-tools,
.xya-msg-editor-tabs,
.xya-msg-template-list,
.xya-msg-card-actions,
.xya-msg-editor-icons {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.xya-msg-tabs {
  padding: 0 16px 14px;
  overflow-x: auto;
}

.xya-msg-tabs button,
.xya-msg-filter-btn,
.xya-msg-tool-chip,
.xya-msg-editor-tabs button,
.xya-msg-link-btn,
.xya-msg-mini-btn,
.xya-msg-more-btn,
.xya-msg-secondary-btn,
.xya-msg-template-item,
.xya-msg-head-btn,
.xya-msg-image-url-send,
.xya-msg-send-btn,
.xya-msg-retry-btn,
.xya-msg-icon-btn {
  border: 1px solid #dbe6f6;
  background: #fff;
  color: #38506b;
  border-radius: 999px;
  cursor: pointer;
  transition: all .2s ease;
}

.xya-msg-tabs button,
.xya-msg-filter-btn,
.xya-msg-tool-chip,
.xya-msg-editor-tabs button,
.xya-msg-link-btn,
.xya-msg-mini-btn,
.xya-msg-more-btn,
.xya-msg-secondary-btn,
.xya-msg-head-btn,
.xya-msg-image-url-send,
.xya-msg-send-btn {
  padding: 8px 14px;
  font-size: 12px;
  font-weight: 700;
}

.xya-msg-tabs button.active,
.xya-msg-editor-tabs button.active,
.xya-msg-send-btn,
.xya-msg-image-url-send {
  background: linear-gradient(135deg, #2563eb, #3b82f6);
  border-color: transparent;
  color: #fff;
  box-shadow: 0 10px 24px rgba(37, 99, 235, 0.22);
}

.xya-msg-tabs button:hover,
.xya-msg-filter-btn:hover,
.xya-msg-tool-chip:hover,
.xya-msg-editor-tabs button:hover,
.xya-msg-link-btn:hover,
.xya-msg-mini-btn:hover,
.xya-msg-more-btn:hover,
.xya-msg-secondary-btn:hover,
.xya-msg-head-btn:hover,
.xya-msg-image-url-send:hover,
.xya-msg-send-btn:hover,
.xya-msg-template-item:hover,
.xya-msg-icon-btn:hover,
.xya-msg-retry-btn:hover {
  transform: translateY(-1px);
  box-shadow: 0 10px 22px rgba(22, 42, 84, 0.10);
}

.xya-msg-icon-btn {
  width: 38px;
  height: 38px;
  border-radius: 12px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0;
}

.xya-msg-icon-btn svg,
.xya-msg-head-btn svg,
.xya-msg-send-btn svg,
.xya-msg-search-box svg {
  width: 18px;
  height: 18px;
  stroke: currentColor;
  stroke-width: 1.8;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.xya-msg-search-row,
.xya-msg-filter-row {
  display: flex;
  gap: 10px;
  margin-bottom: 12px;
}

.xya-msg-search-box {
  flex: 1;
  display: flex;
  align-items: center;
  gap: 8px;
  height: 42px;
  padding: 0 14px;
  border-radius: 14px;
  background: #f8fbff;
  border: 1px solid #e5edf9;
}

.xya-msg-search-box input,
.xya-msg-select,
.xya-msg-image-url-input,
.xya-msg-editor textarea {
  width: 100%;
  border: 0;
  outline: 0;
  background: transparent;
  font-size: 13px;
  color: #172b4d;
}

.xya-msg-filter-row {
  align-items: center;
  flex-wrap: wrap;
}

.xya-msg-select,
.xya-msg-image-url-input {
  height: 42px;
  border-radius: 14px;
  border: 1px solid #dbe6f6;
  background: #fff;
  padding: 0 14px;
}

.xya-msg-select {
  min-width: 140px;
}

.xya-msg-alert {
  margin-bottom: 12px;
  color: #ef4444;
  font-size: 12px;
}

.xya-msg-refresh-alert {
  color: #64748b;
}

.xya-msg-conversation-list {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 0 10px;
}

.xya-msg-conversation {
  width: 100%;
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 14px 12px;
  border: 0;
  background: transparent;
  border-radius: 18px;
  cursor: pointer;
  text-align: left;
  margin-bottom: 6px;
}

.xya-msg-conversation:hover,
.xya-msg-conversation.active {
  background: linear-gradient(135deg, rgba(37, 99, 235, 0.08), rgba(59, 130, 246, 0.03));
}

.xya-msg-avatar-wrap {
  position: relative;
}

.xya-msg-avatar {
  width: 44px;
  height: 44px;
  border-radius: 16px;
  background: linear-gradient(135deg, #2563eb, #60a5fa);
  color: #fff;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  font-weight: 800;
  flex-shrink: 0;
}

.xya-msg-avatar.avatar-image {
  object-fit: cover;
  overflow: hidden;
  padding: 0;
}

.xya-msg-avatar.large {
  width: 54px;
  height: 54px;
  border-radius: 18px;
  font-size: 20px;
}

.xya-msg-avatar.small {
  width: 34px;
  height: 34px;
  border-radius: 12px;
  font-size: 13px;
}

.xya-msg-avatar.small.self {
  background: linear-gradient(135deg, #14b8a6, #22c55e);
}

.xya-msg-online-dot {
  position: absolute;
  right: 1px;
  bottom: 1px;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: #22c55e;
  border: 2px solid #fff;
}

.xya-msg-online-dot.unknown {
  background: #94a3b8;
}

.xya-msg-conversation-main {
  flex: 1;
  min-width: 0;
}

.xya-msg-name-line {
  flex: 1;
  justify-content: flex-start;
  min-width: 0;
}

.xya-msg-name-line strong,
.xya-msg-chat-user-line strong,
.xya-msg-product-info strong,
.xya-msg-template-item strong {
  color: #12233f;
  font-size: 14px;
  font-weight: 800;
}

.xya-msg-buyer-tag {
  padding: 4px 9px;
  background: #eff6ff;
  color: #2563eb;
}

.xya-msg-conversation-time,
.xya-msg-bubble-meta,
.xya-msg-footer-note,
.xya-msg-time-divider,
.xya-msg-danger-text {
  color: #8190a5;
  font-size: 11px;
}

.xya-msg-preview,
.xya-msg-product-line span,
.xya-msg-product-info b,
.xya-msg-metric-grid b,
.xya-msg-status-row b,
.xya-msg-card-bubble p,
.xya-msg-bubble {
  color: #31445f;
  font-size: 13px;
}

.xya-msg-preview {
  margin: 8px 0;
  line-height: 1.5;
}

.xya-msg-product-line {
  justify-content: flex-start;
  font-size: 12px;
  color: #6b7a90;
}

.xya-msg-product-thumb,
.xya-msg-product-large {
  object-fit: cover;
  background: #eef4ff;
}

.xya-msg-product-thumb {
  width: 28px;
  height: 28px;
  border-radius: 10px;
}

.xya-msg-product-large {
  width: 72px;
  height: 72px;
  border-radius: 18px;
}

.xya-msg-product-line em,
.xya-msg-product-info b {
  color: #ef4444;
  font-style: normal;
  font-weight: 800;
}

.xya-msg-unread {
  min-width: 22px;
  padding: 0 6px;
  height: 22px;
  background: #ef4444;
  color: #fff;
  margin-left: auto;
}

.xya-msg-conversation-more,
.xya-msg-load-more {
  display: flex;
  justify-content: center;
  padding: 10px 0 4px;
}

.xya-msg-more-btn,
.xya-msg-mini-btn,
.xya-msg-link-btn,
.xya-msg-retry-btn {
  background: #f8fbff;
}

.xya-msg-footer-note {
  margin-top: 8px;
}

.xya-msg-chat-panel {
  height: 100%;
  border-radius: 26px;
  padding: 18px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.xya-msg-chat-head,
.xya-msg-chat-user,
.xya-msg-chat-actions,
.xya-msg-profile-head,
.xya-msg-product-card,
.xya-msg-image-url-row {
  display: flex;
  align-items: center;
  gap: 12px;
}

.xya-msg-chat-head {
  justify-content: space-between;
  padding-bottom: 14px;
  border-bottom: 1px solid #eef3fa;
}

.xya-msg-chat-user {
  min-width: 0;
}

.xya-msg-chat-user > div:last-child {
  min-width: 0;
}

.xya-msg-chat-user-line {
  justify-content: flex-start;
}

.xya-msg-chat-actions {
  flex-wrap: wrap;
  justify-content: flex-end;
}

.xya-msg-head-btn.danger {
  color: #ef4444;
  border-color: #fecaca;
  background: #fff5f5;
}

.xya-msg-chat-body {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  padding-top: 14px;
}

.xya-msg-chat-stream {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding-right: 6px;
}

.xya-msg-empty {
  padding: 28px 16px;
  text-align: center;
  color: #8a94a6;
  font-size: 13px;
}

.xya-msg-empty.soft {
  background: #f8fbff;
  border-radius: 18px;
}

.xya-msg-bubble-row {
  display: flex;
  align-items: flex-end;
  gap: 10px;
  margin-bottom: 16px;
}

.xya-msg-bubble-row.me {
  justify-content: flex-end;
}

.xya-msg-bubble-stack {
  max-width: min(70%, 560px);
}

.xya-msg-time-divider {
  text-align: center;
  margin: 0 0 8px;
}

.xya-msg-bubble {
  padding: 12px 14px;
  border-radius: 18px 18px 18px 6px;
  background: #f6f9ff;
  line-height: 1.65;
  white-space: pre-wrap;
  word-break: break-word;
}

.xya-msg-bubble.me {
  background: linear-gradient(135deg, #2563eb, #3b82f6);
  color: #fff;
  border-radius: 18px 18px 6px 18px;
}

.xya-msg-bubble-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 6px;
}

.xya-msg-bubble-meta.me {
  justify-content: flex-end;
}

.xya-msg-status-failed {
  color: #ef4444;
}

.xya-msg-status-sending {
  color: #f59e0b;
}

.xya-msg-status-unknown {
  color: #d97706;
  font-weight: 600;
}

.xya-msg-inline-image {
  display: block;
  max-width: 220px;
  border-radius: 14px;
  cursor: zoom-in;
}

.xya-msg-card-bubble b,
.xya-msg-product-info b,
.xya-msg-metric-grid b,
.xya-msg-status-row b,
.green {
  color: #0f766e;
  font-weight: 800;
}

/* 商品卡片消息（contentType=8）渲染 */
.xya-msg-card-bubble {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 200px;
  max-width: 280px;
}
.xya-msg-card-image {
  width: 100%;
  max-height: 180px;
  object-fit: cover;
  border-radius: 8px;
  cursor: zoom-in;
}
.xya-msg-card-info {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.xya-msg-card-info b {
  font-size: 13px;
  line-height: 1.4;
  color: inherit;
  font-weight: 600;
}
.xya-msg-card-info p {
  font-size: 11px;
  color: #6b7280;
  margin: 0;
}

.xya-msg-chat-bottom {
  display: grid;
  gap: 12px;
  padding-top: 14px;
}

.xya-msg-editor-box,
.xya-msg-card {
  border-radius: 22px;
  background: #f9fbff;
  border: 1px solid #e7effb;
}

.xya-msg-editor-box {
  padding: 14px;
}

.xya-msg-tool-chip {
  border-radius: 12px;
  padding: 8px 12px;
}

.xya-msg-editor {
  padding-top: 14px;
}

.xya-msg-deleted-hint {
  padding: 28px 14px;
  text-align: center;
  color: #999;
  font-size: 14px;
  line-height: 1.6;
  background: #f9fbff;
  border: 1px solid #e7effb;
  border-radius: 8px;
}

.xya-msg-editor-tabs {
  margin-bottom: 10px;
}

.xya-msg-editor textarea {
  min-height: 120px;
  resize: vertical;
  line-height: 1.6;
}

.xya-msg-emoji-panel {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 8px;
  margin-top: 10px;
}

.xya-msg-emoji-btn {
  border: 1px solid #dbe6f6;
  border-radius: 12px;
  background: #fff;
  padding: 8px 0;
  cursor: pointer;
}

.xya-msg-image-url-row {
  margin-top: 12px;
}

.xya-msg-image-url-input {
  flex: 1;
}

.xya-msg-hidden-input {
  display: none;
}

.xya-msg-send-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.xya-msg-detail-panel {
  display: flex;
  flex-direction: column;
  gap: 16px;
  overflow: auto;
}

.xya-msg-card {
  padding: 16px;
}

.xya-msg-card + .xya-msg-card {
  margin-top: 0;
}

.xya-msg-product-card,
.xya-msg-card-actions,
.xya-msg-profile-grid,
.xya-msg-metric-grid,
.xya-msg-status-list,
.xya-msg-template-list {
  margin-top: 12px;
}

.xya-msg-product-info,
.xya-msg-status-list,
.xya-msg-template-list {
  display: grid;
  gap: 8px;
}

.xya-msg-inline-state {
  margin-top: 12px;
  padding: 10px 12px;
  border: 1px solid #dbeafe;
  border-radius: 12px;
  background: #eff6ff;
  color: #475569;
  font-size: 12px;
  line-height: 1.55;
}

.xya-msg-inline-state.error {
  border-color: #fecaca;
  background: #fff1f2;
  color: #b91c1c;
}

.xya-msg-profile-grid,
.xya-msg-metric-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.xya-msg-profile-grid div,
.xya-msg-metric-grid div {
  padding: 12px;
  background: #fff;
  border: 1px solid #e8eef8;
  border-radius: 16px;
}

.xya-msg-secondary-btn.wide,
.xya-msg-template-item {
  width: 100%;
}

.xya-msg-template-item {
  border-radius: 16px;
  padding: 12px 14px;
  text-align: left;
  background: #fff;
}

@media (max-width: 1440px) {
  .xya-msg-layout {
    grid-template-columns: 300px minmax(0, 1fr);
  }

  .xya-msg-detail-panel {
    grid-column: 1 / -1;
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 1080px) {
  .xya-msg-layout {
    height: auto;
    min-height: 0;
    grid-template-columns: 1fr;
  }

  .xya-msg-sidebar,
  .xya-msg-chat-panel,
  .xya-msg-detail-panel {
    min-height: auto;
  }

  .xya-msg-detail-panel {
    grid-template-columns: 1fr;
  }

  .xya-msg-bubble-stack {
    max-width: 86%;
  }
}

/* === AI 鑷姩鍥炲寮€鍏?=== */
.xya-msg-switch {
  position: relative;
  display: inline-block;
  width: 40px;
  height: 22px;
  flex-shrink: 0;
}
.xya-msg-switch input {
  opacity: 0;
  width: 0;
  height: 0;
}
.xya-msg-switch-slider {
  position: absolute;
  cursor: pointer;
  top: 0; left: 0; right: 0; bottom: 0;
  background-color: #ccc;
  transition: .3s;
  border-radius: 22px;
}
.xya-msg-switch-slider:before {
  position: absolute;
  content: "";
  height: 16px;
  width: 16px;
  left: 3px;
  bottom: 3px;
  background-color: white;
  transition: .3s;
  border-radius: 50%;
}
.xya-msg-switch input:checked + .xya-msg-switch-slider {
  background: linear-gradient(135deg, #2563eb, #3b82f6);
}
.xya-msg-switch input:checked + .xya-msg-switch-slider:before {
  transform: translateX(18px);
}
.xya-msg-switch input:focus-visible + .xya-msg-switch-slider {
  box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.22);
}
.xya-msg-switch input:disabled + .xya-msg-switch-slider {
  cursor: not-allowed;
  opacity: 0.55;
}
.xya-msg-status-row b.gray {
  color: #99a4b4;
}

/* === 蹇嵎鍥炲妯℃澘绠＄悊寮圭獥 === */
.xya-msg-modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}
.xya-msg-modal {
  background: #fff;
  border-radius: 16px;
  width: 90%;
  max-width: 560px;
  max-height: 80vh;
  display: flex;
  flex-direction: column;
  box-shadow: 0 20px 60px rgba(0,0,0,0.2);
}
.xya-msg-modal-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 20px;
  border-bottom: 1px solid #e7effb;
}
.xya-msg-modal-head h3 {
  margin: 0;
  font-size: 16px;
  color: #14213d;
}
.xya-msg-modal-body {
  padding: 16px 20px;
  overflow-y: auto;
  flex: 1;
}
.xya-msg-template-edit-row {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-bottom: 14px;
  border-bottom: 1px solid #e7effb;
  margin-bottom: 14px;
}
.xya-msg-modal-input {
  padding: 8px 12px;
  border: 1px solid #dbe6f6;
  border-radius: 8px;
  font-size: 14px;
  outline: none;
}
.xya-msg-modal-input:focus {
  border-color: #2563eb;
}
.xya-msg-modal-textarea {
  padding: 8px 12px;
  border: 1px solid #dbe6f6;
  border-radius: 8px;
  font-size: 14px;
  outline: none;
  resize: vertical;
  font-family: inherit;
}
.xya-msg-modal-textarea:focus {
  border-color: #2563eb;
}
.xya-msg-template-edit-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
.xya-msg-template-manage-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.xya-msg-template-manage-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 12px;
  background: #f9fbff;
  border: 1px solid #e7effb;
  border-radius: 10px;
  gap: 10px;
}
.xya-msg-template-manage-info {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.xya-msg-template-manage-info strong {
  font-size: 13px;
  color: #14213d;
}
.xya-msg-template-manage-info span {
  font-size: 12px;
  color: #6b7a90;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.xya-msg-template-manage-actions {
  display: flex;
  gap: 6px;
  flex-shrink: 0;
}
.xya-msg-mini-btn.danger {
  color: #ef4444;
  border-color: #fecaca;
}

.xya-msg-image-preview-mask {
  padding: 24px;
  background: rgba(15, 23, 42, 0.82);
  z-index: 1100;
}

.xya-msg-image-preview {
  width: min(92vw, 1040px);
  max-height: 92vh;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.xya-msg-image-preview-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

.xya-msg-image-preview-img {
  width: 100%;
  max-height: calc(92vh - 60px);
  object-fit: contain;
  border-radius: 22px;
  background: rgba(15, 23, 42, 0.18);
  box-shadow: 0 24px 72px rgba(15, 23, 42, 0.28);
}

/* === 移动端适配 (max-width: 900px) === */
@media (max-width: 900px) {
  .xya-msg-layout {
    grid-template-columns: 1fr;
    gap: 10px;
    height: auto;
    min-height: 0;
  }
  .xya-msg-sidebar,
  .xya-msg-chat-panel {
    border-radius: 14px;
    height: auto;
    max-height: none;
  }
  .xya-msg-sidebar {
    padding: 12px 0 10px;
  }
  .xya-msg-sidebar-head,
  .xya-msg-search-row,
  .xya-msg-filter-row,
  .xya-msg-footer-note,
  .xya-msg-alert {
    padding-left: 12px;
    padding-right: 12px;
  }
  .xya-msg-sidebar-head {
    margin-bottom: 10px;
    gap: 8px;
  }
  .xya-msg-sidebar-head h2,
  .xya-msg-card h3 {
    font-size: 16px;
  }
  .xya-msg-sidebar-head p {
    font-size: 11px;
  }
  .xya-msg-tabs {
    padding: 0 12px 10px;
    gap: 6px;
  }
  .xya-msg-tabs button,
  .xya-msg-filter-btn,
  .xya-msg-tool-chip,
  .xya-msg-editor-tabs button,
  .xya-msg-link-btn,
  .xya-msg-mini-btn,
  .xya-msg-more-btn,
  .xya-msg-secondary-btn,
  .xya-msg-head-btn,
  .xya-msg-image-url-send,
  .xya-msg-send-btn {
    padding: 7px 10px;
    font-size: 11px;
  }
  .xya-msg-search-box {
    height: 38px;
    padding: 0 10px;
  }
  .xya-msg-select,
  .xya-msg-image-url-input {
    height: 38px;
  }
  .xya-msg-icon-btn {
    width: 34px;
    height: 34px;
  }
  .xya-msg-conversation {
    padding: 10px;
    gap: 10px;
    border-radius: 14px;
  }
  .xya-msg-avatar {
    width: 38px;
    height: 38px;
    font-size: 14px;
  }
  .xya-msg-avatar.large {
    width: 44px;
    height: 44px;
    font-size: 16px;
  }
  .xya-msg-avatar.small {
    width: 28px;
    height: 28px;
    font-size: 11px;
  }
  .xya-msg-name-line strong,
  .xya-msg-chat-user-line strong,
  .xya-msg-product-info strong,
  .xya-msg-template-item strong {
    font-size: 13px;
  }
  .xya-msg-chat-panel {
    padding: 12px;
    border-radius: 16px;
  }
  .xya-msg-chat-head {
    padding-bottom: 10px;
    gap: 8px;
    flex-wrap: wrap;
  }
  .xya-msg-chat-actions {
    gap: 6px;
  }
  .xya-msg-chat-body {
    padding-top: 10px;
  }
  .xya-msg-bubble-row {
    gap: 8px;
    margin-bottom: 12px;
  }
  .xya-msg-bubble-stack {
    max-width: 85%;
  }
  .xya-msg-bubble {
    padding: 10px 12px;
    font-size: 13px;
    border-radius: 14px 14px 14px 4px;
  }
  .xya-msg-bubble.me {
    border-radius: 14px 14px 4px 14px;
  }
  .xya-msg-inline-image {
    max-width: 160px;
  }
  .xya-msg-card-bubble {
    min-width: 160px;
    max-width: 220px;
  }
  .xya-msg-chat-bottom {
    padding-top: 10px;
    gap: 8px;
  }
  .xya-msg-quick-tools {
    gap: 6px;
  }
  .xya-msg-editor {
    padding-top: 10px;
  }
  .xya-msg-editor-box {
    padding: 10px;
    border-radius: 14px;
  }
  .xya-msg-editor textarea {
    min-height: 80px;
    font-size: 13px;
  }
  .xya-msg-editor-actions {
    flex-wrap: wrap;
    gap: 8px;
  }
  .xya-msg-image-url-row {
    flex-wrap: wrap;
  }
  .xya-msg-detail-panel {
    gap: 10px;
  }
  .xya-msg-card {
    padding: 12px;
    border-radius: 14px;
  }
  .xya-msg-product-large {
    width: 56px;
    height: 56px;
    border-radius: 12px;
  }
  .xya-msg-profile-grid,
  .xya-msg-metric-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 8px;
  }
  .xya-msg-template-item {
    padding: 10px 12px;
    border-radius: 12px;
  }
  .xya-msg-modal-mask {
    align-items: flex-end;
  }
  .xya-msg-modal {
    width: 100%;
    max-width: none;
    max-height: 88vh;
    border-radius: 14px 14px 0 0;
    margin: 0;
  }
  .xya-msg-modal-head,
  .xya-msg-modal-body {
    padding-left: 14px;
    padding-right: 14px;
  }
  .xya-msg-modal-head {
    padding-top: 12px;
    padding-bottom: 12px;
  }
  .xya-msg-modal-head h3 {
    font-size: 15px;
  }
  .xya-msg-template-manage-item {
    flex-direction: column;
    align-items: flex-start;
    gap: 8px;
  }
  .xya-msg-template-manage-actions {
    width: 100%;
    justify-content: flex-end;
  }
  .xya-msg-image-preview {
    width: 94vw;
    max-height: 80vh;
  }
  .xya-msg-image-preview-img {
    max-height: calc(80vh - 60px);
    border-radius: 14px;
  }
  .xya-msg-emoji-panel {
    grid-template-columns: repeat(6, minmax(0, 1fr));
    gap: 6px;
  }
  .xya-msg-emoji-btn {
    padding: 6px 0;
  }
}

/* === 客户订单板块 === */
.xya-msg-order-head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.xya-msg-order-count {
  font-size: 13px;
  font-weight: 500;
  color: #7384a8;
}

.xya-msg-order-refresh {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  padding: 0;
  border: none;
  border-radius: 10px;
  background: transparent;
  cursor: pointer;
  color: #3259cf;
  font-size: 18px;
  line-height: 1;
  transition: background 0.2s ease;
}

.xya-msg-order-refresh:hover:not(:disabled) {
  background: #eef3ff;
}

.xya-msg-order-refresh:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.xya-msg-order-refresh.spinning {
  animation: xya-msg-spin 0.9s linear infinite;
}

@keyframes xya-msg-spin {
  to { transform: rotate(360deg); }
}

.xya-msg-card-empty {
  padding: 14px 8px;
  color: #7384a8;
  font-size: 13px;
  text-align: center;
}

.xya-msg-order-list {
  display: grid;
  gap: 12px;
  margin-top: 4px;
}

.xya-msg-order-item {
  padding: 12px;
  border-radius: 16px;
  background: linear-gradient(180deg, #fbfdff 0%, #f6faff 100%);
  border: 1px solid #e7eef9;
  transition: border-color 0.2s ease, box-shadow 0.2s ease, transform 0.2s ease;
}

.xya-msg-order-item:hover {
  border-color: #c2d5f5;
  box-shadow: 0 8px 22px rgba(23, 61, 135, 0.08);
  transform: translateY(-1px);
}

.xya-msg-order-item-main {
  display: grid;
  grid-template-columns: 64px minmax(0, 1fr);
  gap: 12px;
  align-items: flex-start;
}

.xya-msg-order-cover-wrap {
  position: relative;
  width: 64px;
  height: 64px;
  border-radius: 14px;
  overflow: hidden;
  background: #eef3fb;
  flex-shrink: 0;
  box-shadow: 0 2px 6px rgba(23, 61, 135, 0.08);
}

.xya-msg-order-cover-placeholder {
  position: absolute;
  inset: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 26px;
  opacity: 0.7;
}

.xya-msg-order-cover {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.xya-msg-order-item-info {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.xya-msg-order-title {
  font-size: 13.5px;
  font-weight: 600;
  color: #112147;
  line-height: 1.45;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  word-break: break-word;
}

.xya-msg-order-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.xya-msg-order-amount {
  font-size: 14px;
  color: #e6532e;
  font-weight: 700;
  letter-spacing: -0.2px;
}

.xya-msg-order-status {
  display: inline-flex;
  align-items: center;
  padding: 3px 9px;
  border-radius: 9px;
  font-size: 11.5px;
  font-weight: 600;
  background: #eef3ff;
  color: #3259cf;
  letter-spacing: 0.2px;
}

.xya-msg-order-status.warning {
  background: #fff4eb;
  color: #c56a18;
}

.xya-msg-order-status.success {
  background: #eefaf2;
  color: #1d8a52;
}

.xya-msg-order-status.info {
  background: #eef3ff;
  color: #3259cf;
}

.xya-msg-order-status.muted {
  background: #f0f2f7;
  color: #7384a8;
}

.xya-msg-order-no {
  font-size: 11.5px;
  color: #9aa8c4;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}

.xya-msg-order-time {
  font-size: 11.5px;
  color: #9aa8c4;
  font-variant-numeric: tabular-nums;
}

.xya-msg-order-fail {
  font-size: 12px;
  color: #e5484d;
  line-height: 1.5;
  background: #fdecec;
  padding: 4px 8px;
  border-radius: 8px;
  margin-top: 2px;
}

.xya-msg-order-error {
  color: #e5484d;
}

.xya-msg-order-item .xya-msg-card-actions {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px dashed #e7eef9;
}

/* === 订单详情弹窗 === */
.xya-msg-order-modal {
  width: min(560px, 100%);
  max-height: min(82vh, 880px);
}

.xya-msg-order-modal-body {
  overflow: auto;
}

.xya-msg-order-detail-section {
  display: grid;
  gap: 10px;
}

.xya-msg-order-detail-section + .xya-msg-order-detail-section {
  margin-top: 18px;
  padding-top: 18px;
  border-top: 1px solid #edf3fc;
}

.xya-msg-order-detail-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.xya-msg-order-detail-label {
  flex-shrink: 0;
  width: 84px;
  color: #7384a8;
  font-size: 13px;
}

.xya-msg-order-detail-value {
  flex: 1;
  text-align: right;
  color: #112147;
  font-size: 13px;
  word-break: break-all;
}

.xya-msg-order-detail-subtitle {
  margin: 0 0 10px;
  font-size: 14px;
  font-weight: 700;
  color: #102147;
}

.xya-msg-order-detail-item {
  padding: 10px 12px;
  border-radius: 14px;
  background: #f8fbff;
  border: 1px solid #e7eef9;
}

.xya-msg-order-detail-item + .xya-msg-order-detail-item {
  margin-top: 8px;
}

.xya-msg-order-detail-item-title {
  font-size: 13px;
  font-weight: 600;
  color: #112147;
  line-height: 1.5;
}

.xya-msg-order-detail-item-meta {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 4px;
  font-size: 12px;
  color: #516286;
}

.xya-msg-order-detail-spec {
  color: #7384a8;
}

/* 会话级自动回复状态机样式 */
.xya-msg-conv-state {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px dashed #e4e9f2;
}
.xya-msg-conv-state .xya-msg-status-row {
  align-items: center;
}
.xya-msg-conv-toggle-btn {
  padding: 6px 12px;
  border: 1px solid #d6dbe5;
  border-radius: 6px;
  background: #fff;
  color: #4a5568;
  font-size: 12px;
  cursor: pointer;
  transition: all 0.2s;
  white-space: nowrap;
}
.xya-msg-conv-toggle-btn:hover:not(:disabled) {
  border-color: #4096ff;
  color: #4096ff;
}
.xya-msg-conv-toggle-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
.xya-msg-conv-toggle-btn.is-success {
  border-color: #52c41a;
  color: #389e0d;
  background: #f6ffed;
}
.xya-msg-conv-toggle-btn.is-warning {
  border-color: #faad14;
  color: #d48806;
  background: #fffbe6;
}
.xya-msg-conv-toggle-btn.is-danger {
  border-color: #ff4d4f;
  color: #cf1322;
  background: #fff2f0;
}
.xya-msg-conv-hint {
  margin-top: 6px;
  padding: 6px 10px;
  background: #fffbe6;
  border: 1px solid #ffe58f;
  border-radius: 4px;
  color: #d48806;
  font-size: 12px;
  line-height: 1.5;
}
.danger-text {
  color: #cf1322;
}
.warning-text {
  color: #d48806;
}</style>
