<template>
  <div class="m-msg">
    <!-- 会话列表视图 -->
    <template v-if="!activeChat">
      <div class="m-page-header">
        <h1>消息</h1>
        <p class="m-page-sub">买家会话与消息通知</p>
      </div>

      <!-- 账号筛选 -->
      <div v-if="accounts.length > 1" class="m-account-chips">
        <button
          class="m-chip"
          :class="{ active: selectedAccountId === '' }"
          @click="selectAccount('')"
        >
          全部账号
        </button>
        <button
          v-for="acc in accounts"
          :key="acc.id"
          class="m-chip"
          :class="{ active: selectedAccountId === String(acc.id) }"
          @click="selectAccount(String(acc.id))"
        >
          <span class="m-chip-dot" :class="{ online: acc.wsStatus === 'online' }"></span>
          {{ acc.remark || acc.nickname || acc.uid || `账号${acc.id}` }}
        </button>
      </div>

      <!-- 会话筛选 Tab -->
      <div class="m-filter-tabs">
        <button
          v-for="tab in filterTabs"
          :key="tab.key"
          class="m-filter-tab"
          :class="{ active: filterType === tab.key }"
          @click="filterType = tab.key"
        >
          {{ tab.label }}
          <span v-if="tab.count > 0" class="m-filter-count">{{ tab.count }}</span>
        </button>
      </div>

      <!-- 加载中 -->
      <div v-if="loading && !conversationAvailable" class="m-loading">
        <div class="m-loading-spinner"></div>
        <span>加载会话中...</span>
      </div>

      <div v-if="conversationError" class="m-state-warning" role="alert">
        <span>{{ conversationError }}</span>
        <button type="button" @click="loadConversations">重试</button>
      </div>

      <!-- 空态 -->
      <div v-if="!loading && !conversationAvailable" class="m-empty">
        <div class="m-empty-icon"><MIcon name="warning" :size="48" /></div>
        <div class="m-empty-text">会话列表暂不可用</div>
        <div class="m-empty-desc">当前无法确认是否存在买家消息，请稍后重试。</div>
      </div>

      <div v-else-if="conversationAvailable && filteredConversations.length === 0" class="m-empty">
        <div class="m-empty-icon">
          <MIcon name="chat" :size="48" />
        </div>
        <div class="m-empty-text">{{ emptyText }}</div>
        <div class="m-empty-desc">{{ accounts.length ? '当买家发来消息时，会在这里显示' : '请先添加并连接店铺账号' }}</div>
      </div>

      <!-- 会话列表 -->
      <div v-else-if="conversationAvailable" class="m-msg-list">
        <div
          v-for="conv in filteredConversations"
          :key="getConversationIdentityKey(conv) || conv.id || conv.sid"
          class="m-msg-item"
          @click="openConversation(conv)"
        >
          <div class="m-msg-avatar" :class="{ 'is-robot': conv.botEnabled }">
            <MIcon :name="conv.botEnabled ? 'bot' : 'user'" :size="22" />
            <span v-if="conv.unreadCount > 0" class="m-msg-dot"></span>
          </div>
          <div class="m-msg-body">
            <div class="m-msg-top">
              <span class="m-msg-name">{{ resolvePeerName(conv) }}</span>
              <span class="m-msg-time">{{ formatTime(conv.lastMessageTime || conv.updatedAt) }}</span>
            </div>
            <div class="m-msg-bottom">
              <span class="m-msg-preview">
                <span v-if="conv.lastIsAutoReply" class="m-ai-badge inline">AI</span>
                {{ conv.lastMessage || conv.lastContent || conv.product || '暂无消息内容' }}
              </span>
              <span v-if="conv.unreadCount > 0" class="m-msg-badge">{{ conv.unreadCount > 99 ? '99+' : conv.unreadCount }}</span>
              <span v-else-if="isCompleted(conv)" class="m-msg-status m-msg-status-done">已完成</span>
            </div>
            <div v-if="conv.goodsTitle || conv.product" class="m-msg-goods">
              <MIcon name="bag" :size="12" /> {{ conv.goodsTitle || conv.product }}
            </div>
          </div>
        </div>
      </div>

      <div class="m-msg-tip">
        <MIcon name="shield" :size="16" />
        <span>复杂消息操作建议在桌面版进行</span>
        <button class="m-tip-btn" @click="$emit('force-desktop')">桌面版</button>
      </div>
    </template>

    <!-- 聊天详情视图 -->
    <template v-else>
      <div class="m-chat-header">
        <button class="m-chat-back" @click="closeChat">
          <MIcon name="chevronLeft" :size="22" />
        </button>
        <div class="m-chat-title">
          <div class="m-chat-name">{{ resolvePeerName(activeChat) }}</div>
          <div v-if="activeChat.goodsTitle || activeChat.product" class="m-chat-goods">
            <MIcon name="bag" :size="11" /> {{ activeChat.goodsTitle || activeChat.product }}
          </div>
        </div>
        <button class="m-chat-more" @click="$emit('force-desktop')">
          <MIcon name="desktop" :size="18" />
        </button>
      </div>

      <div ref="chatBoxRef" class="m-chat-body">
        <div v-if="chatError" class="m-chat-load-warning" role="alert">
          <MIcon name="warning" :size="18" />
          <span>{{ chatError }}</span>
          <button type="button" class="m-inline-retry" :disabled="chatLoading" @click="loadChatMessages">重试</button>
        </div>
        <div v-if="chatLoading && chatMessages.length === 0" class="m-chat-loading">
          <div class="m-loading-spinner"></div>
          <span>加载消息中...</span>
        </div>
        <div v-else-if="chatMessages.length === 0 && !chatError" class="m-chat-empty">
          <MIcon name="chat" :size="40" />
          <span>暂无消息记录</span>
        </div>
        <div v-if="chatMessages.length > 0" class="m-chat-list">
          <div
            v-for="msg in chatMessages"
            :key="msg.id || msg.uuid || msg.pnmId"
            class="m-bubble"
            :class="isOutgoing(msg) ? 'out' : 'in'"
          >
            <div v-if="!isOutgoing(msg)" class="m-bubble-avatar">
              <MIcon name="user" :size="18" />
            </div>
            <div class="m-bubble-content">
              <div v-if="msg.isAutoReply" class="m-ai-row" :class="{ out: isOutgoing(msg) }">
                <span class="m-ai-badge">AI自动回复</span>
              </div>
              <div v-if="msg.imageUrl" class="m-bubble-image-wrap">
                <img :src="msg.imageUrl" class="m-bubble-image" alt="图片" @click="previewImage(msg.imageUrl)" />
              </div>
              <div v-if="msg.content || msg.text || msg.msgContent || msg.displayText" class="m-bubble-text">{{ msg.content || msg.text || msg.displayText || msg.msgContent }}</div>
              <div class="m-bubble-time">{{ formatTime(msg.createdAt || msg.timestamp || msg.sendTime || msg.messageTime) }}</div>
              <div v-if="isOutgoing(msg) && msg.sendStatus" class="m-bubble-send-status" :class="msg.sendStatus">
                {{ msg.sendStatus === 'sending' ? '发送中' : msg.sendStatus === 'confirmed' ? '平台已确认' : msg.sendStatus === 'failed' ? '明确未发送' : '结果待核对' }}
                <button v-if="msg.sendStatus === 'failed' && msg.retrySafe" type="button" @click="restoreDraft(msg)">复制到输入框</button>
              </div>
            </div>
            <div v-if="isOutgoing(msg)" class="m-bubble-avatar out-avatar">
              <MIcon name="user" :size="18" />
            </div>
          </div>
        </div>
      </div>

      <div v-if="sendError" class="m-send-error" role="alert">{{ sendError }}</div>
      <div class="m-chat-input">
        <input
          v-model="draft"
          type="text"
          placeholder="输入消息..."
          @keyup.enter="sendCurrentMessage"
        />
        <button class="m-chat-send" :disabled="!draft.trim() || sending || chatLoading || Boolean(chatBlockingError)" @click="sendCurrentMessage">
          <MIcon name="send" :size="18" />
        </button>
      </div>
    </template>

    <div class="m-safe-bottom"></div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { openExternalUrl } from '../utils/externalUrl.js'
import MIcon from './MIcon.vue'
import { getAccounts } from '../api/accounts.js'
import { onlineConversations, messageContext, markConversationRead } from '../api/messages.js'
import { sendMessage } from '../api/websocket.js'
import { recordsOf } from '../utils/apiData.js'
import {
  createLatestOnlyGuard,
  createSingleFlight,
  mergeRemoteMessagesWithLocalAttempts,
} from '../utils/mobileMessagesSafety.js'
import {
  extractMessageDisplayText,
  findConversationMatchIndex,
  findPreservedConversation,
  getConversationIdentityKey,
  getConversationRecordId,
  isSameConversationByPayload,
  matchesAccountSelection,
  mergeConversationSnapshots,
  resolveManualMessageError,
  resolveManualMessageOutcome
} from '../utils/messagesPageState.js'

defineEmits(['force-desktop', 'navigate'])

const accounts = ref([])
const conversations = ref([])
const MOBILE_CONVERSATION_LIMIT = 2000
const loading = ref(true)
const conversationAvailable = ref(false)
const conversationError = ref('')
const accountsAvailable = ref(false)
const selectedAccountId = ref('')
const filterType = ref('all')

const activeChat = ref(null)
const chatMessages = ref([])
const chatLoading = ref(false)
const chatError = ref('')
const chatBlockingError = computed(() => Boolean(chatError.value) && chatMessages.value.length === 0)
const chatBoxRef = ref(null)
const draft = ref('')
const sending = ref(false)
const sendError = ref('')
let pollingTimer = null
const conversationRequestGuard = createLatestOnlyGuard()
const chatRequestGuard = createLatestOnlyGuard()
const POLLING_INTERVAL_MS = 15000
const ACCOUNT_FETCH_CONCURRENCY = 4

const filterTabs = computed(() => [
  { key: 'all', label: '全部', count: conversations.value.length },
  { key: 'unreplied', label: '未回复', count: conversations.value.filter(c => Number(c.unreadCount || 0) > 0).length },
  { key: 'inProgress', label: '进行中', count: conversations.value.filter(c => !isCompleted(c)).length },
  { key: 'completed', label: '已完成', count: conversations.value.filter(c => isCompleted(c)).length },
  { key: 'robot', label: '机器人', count: conversations.value.filter(c => !!c.botEnabled).length }
])

const emptyText = computed(() => {
  if (filterType.value === 'unreplied') return '暂无未回复消息'
  if (filterType.value === 'inProgress') return '暂无进行中会话'
  if (filterType.value === 'completed') return '暂无已完成会话'
  if (filterType.value === 'robot') return '暂无机器人会话'
  return '暂无新消息'
})

const filteredConversations = computed(() => {
  return conversations.value.filter(c => {
    if (filterType.value === 'unreplied') return Number(c.unreadCount || 0) > 0
    if (filterType.value === 'inProgress') return !isCompleted(c)
    if (filterType.value === 'completed') return isCompleted(c)
    if (filterType.value === 'robot') return !!c.botEnabled
    return true
  })
})

function isCompleted(c) {
  return ['completed', 'closed', 'transferred'].includes(c.sessionStatus) || c.closed === true
}

function normalizeSid(value) {
  const raw = String(value || '').trim()
  if (!raw) return ''
  const sid = raw.startsWith('sid:') ? raw.slice(4) : raw
  return sid.endsWith('@goofish') ? sid.slice(0, -8) : sid
}

function normalizePeerUserId(value) {
  const raw = String(value || '').trim()
  if (!raw || raw.startsWith('sid:')) return ''
  return raw.endsWith('@goofish') ? raw.slice(0, -8) : raw
}

function messageIdentity(message) {
  const pnmId = String(message?.pnmId || message?.messageUid || message?.uuid || '').trim()
  if (pnmId) return `pnm:${pnmId}`
  const id = String(message?.id || '').trim()
  if (id && !id.startsWith('temp_')) return `id:${id}`
  const sid = normalizeSid(message?.sid || message?.sId || '')
  const direction = String(message?.direction || '').toUpperCase()
  const sender = normalizePeerUserId(message?.senderUserId || message?.fromUserId || '')
  const receiver = normalizePeerUserId(message?.receiverUserId || message?.toUserId || '')
  const content = String(message?.content || message?.text || message?.displayText || message?.msgContent || '').trim()
  const messageTime = Number(message?.messageTime || message?.createdAt || message?.timestamp || message?.sendTime || 0)
  return `fallback:${sid}:${direction}:${sender}:${receiver}:${messageTime}:${content}`
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

function isSameConversation(conv, payload) {
  return isSameConversationByPayload(conv, payload)
}

function resolvePeerName(c) {
  return c.peerUserName || c.buyerName || c.peerName || c.peerNick || (c.peerUserId ? `买家${String(c.peerUserId).slice(-4)}` : '买家')
}

function mapConversationItem(item, fallbackAccountId) {
  return {
    ...item,
    xianyuAccountId: Number(item.xianyuAccountId || item.accountId || fallbackAccountId || 0) || undefined,
    sid: normalizeSid(item.sid || item.sId || item.sessionId || item.conversationId || item.id),
    peerUserId: normalizePeerUserId(item.peerUserId || item.peerExternalUid || item.externalBuyerId || ''),
    lastIsAutoReply: Boolean(item.lastIsAutoReply || item.isAutoReply || item.is_auto_reply),
    botEnabled: Boolean(item.botEnabled || item.hasAiReply || item.lastIsAutoReply)
  }
}

async function fetchConversationBatch(accountId, requestConfig) {
  const pageSize = 100
  const items = []
  const seenCursors = new Set()
  let cursor
  let hasMore = false
  while (items.length < MOBILE_CONVERSATION_LIMIT) {
    const res = await onlineConversations(accountId, { cursor, pageSize }, requestConfig)
    const data = res?.data || {}
    items.push(...recordsOf(data).map(item => mapConversationItem(item, accountId)))
    hasMore = data.hasMore === true
    if (!hasMore) break
    const nextCursor = data.nextCursor
    const cursorKey = String(nextCursor ?? '')
    if (!cursorKey || seenCursors.has(cursorKey)) {
      throw new Error('会话分页游标无效')
    }
    seenCursors.add(cursorKey)
    cursor = nextCursor
  }
  return {
    items: items.slice(0, MOBILE_CONVERSATION_LIMIT),
    truncated: hasMore && items.length >= MOBILE_CONVERSATION_LIMIT
  }
}

async function fetchConversationResults(accountIds, requestConfig) {
  const results = []
  for (let index = 0; index < accountIds.length; index += ACCOUNT_FETCH_CONCURRENCY) {
    const batch = accountIds.slice(index, index + ACCOUNT_FETCH_CONCURRENCY)
    results.push(...await Promise.allSettled(batch.map(accountId => fetchConversationBatch(accountId, requestConfig))))
  }
  return results
}

async function loadAccounts() {
  try {
    const pageSize = 200
    const loadedAccounts = []
    let page = 1
    let total = 0
    let hasMore = true
    while (hasMore) {
      const res = await getAccounts({ current: page, size: pageSize })
      const records = recordsOf(res?.data)
      total = Number(res?.data?.total ?? records.length) || 0
      loadedAccounts.push(...records)
      hasMore = records.length === pageSize && loadedAccounts.length < total
      if (hasMore) page += 1
    }
    accounts.value = loadedAccounts
    accountsAvailable.value = true
    return true
  } catch {
    accounts.value = []
    accountsAvailable.value = false
    conversationError.value = '账号列表暂不可用，当前无法加载会话。'
    return false
  }
}

async function loadConversations({ background = false } = {}) {
  const selectionSnapshot = String(selectedAccountId.value || '')
  const requestToken = conversationRequestGuard.begin(`account:${selectionSnapshot || 'all'}`)
  const hadSuccessfulSnapshot = conversationAvailable.value === true
  if (!background) loading.value = true
  const requestConfig = {
    uiMode: background ? 'background' : 'foreground',
    suppressGlobalError: true,
  }
  if (!accountsAvailable.value) {
    if (conversationRequestGuard.isCurrent(requestToken, `account:${selectionSnapshot || 'all'}`)) {
      conversationAvailable.value = false
      conversationError.value = '账号列表暂不可用，当前无法加载会话。'
      loading.value = false
    }
    return { loaded: false }
  }
  try {
    const accountIds = selectionSnapshot
      ? [Number(selectionSnapshot)]
      : accounts.value
          .map(account => Number(account?.id || 0))
          .filter(accountId => accountId > 0)
    if (!accountIds.length) {
      if (!conversationRequestGuard.isCurrent(requestToken, `account:${selectionSnapshot || 'all'}`)) return { loaded: false, stale: true }
      conversations.value = []
      conversationAvailable.value = true
      return { loaded: true }
    }
    const results = await fetchConversationResults(accountIds, requestConfig)
    if (!conversationRequestGuard.isCurrent(requestToken, `account:${selectionSnapshot || 'all'}`)) return { loaded: false, stale: true }
    const batches = results.filter(item => item.status === 'fulfilled').map(item => item.value)
    const failedCount = results.length - batches.length
    if (!batches.length) throw new Error('all conversation requests failed')
    const truncatedCount = batches.filter(batch => batch.truncated).length
    const merged = mergeConversationSnapshots(batches.flatMap(batch => batch.items)).filter(item => getConversationIdentityKey(item))
    conversations.value = merged
    conversationAvailable.value = true
    const partialWarnings = []
    if (failedCount > 0) partialWarnings.push(`${failedCount} 个账号的会话暂不可用；已加载账号仍可查看。`)
    if (truncatedCount > 0) partialWarnings.push(`${truncatedCount} 个账号的会话超过移动端安全展示上限；当前仅展示每个账号最近 ${MOBILE_CONVERSATION_LIMIT} 条，请使用桌面版继续查看。`)
    conversationError.value = partialWarnings.join(' ')
    if (activeChat.value) {
      const nextActive = findPreservedConversation(conversations.value, activeChat.value)
      if (nextActive) {
        activeChat.value = { ...activeChat.value, ...nextActive }
      }
    }
    return { loaded: true }
  } catch {
    if (!conversationRequestGuard.isCurrent(requestToken, `account:${selectionSnapshot || 'all'}`)) return { loaded: false, stale: true }
    if (hadSuccessfulSnapshot) {
      conversationError.value = '会话刷新失败，已保留上次成功加载的结果；当前状态可能已变化。'
    } else {
      conversations.value = []
      conversationAvailable.value = false
      conversationError.value = '会话列表暂不可用，当前不会把加载失败显示为“暂无消息”。'
    }
    return { loaded: false, preserved: hadSuccessfulSnapshot }
  } finally {
    if (conversationRequestGuard.isCurrent(requestToken, `account:${selectionSnapshot || 'all'}`)) loading.value = false
  }
}

function selectAccount(accId) {
  if (selectedAccountId.value === accId) return
  selectedAccountId.value = accId
  conversationError.value = ''
  if (activeChat.value) {
    closeChat()
  }
  loadConversations()
  startPolling()
}

function formatTime(ts) {
  if (!ts) return ''
  const d = new Date(ts)
  const now = new Date()
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const msgDay = new Date(d.getFullYear(), d.getMonth(), d.getDate())
  const diffDays = Math.floor((today - msgDay) / (1000 * 60 * 60 * 24))
  if (diffDays === 0) {
    return `${d.getHours().toString().padStart(2,'0')}:${d.getMinutes().toString().padStart(2,'0')}`
  }
  if (diffDays === 1) return '昨天'
  if (diffDays < 7) return `${diffDays}天前`
  return `${d.getMonth()+1}/${d.getDate()}`
}

async function openConversation(conv) {
  activeChat.value = conv
  chatMessages.value = []
  chatLoading.value = true
  chatError.value = ''
  sendError.value = ''
  await nextTick()
  const openedIdentity = getConversationIdentityKey(conv)
  const messageLoad = await loadChatMessages()
  if (!messageLoad.loaded) return
  if (!activeChat.value || getConversationIdentityKey(activeChat.value) !== openedIdentity) return
  if (conv.unreadCount > 0) {
    try {
      const recordId = getConversationRecordId(conv)
      if (recordId) {
        await markConversationRead(recordId, { uiMode: 'silent', suppressGlobalError: true })
      }
      conv.unreadCount = 0
      conversations.value = conversations.value.map(item =>
        isSameConversation(item, conv) ? { ...item, unreadCount: 0 } : item
      )
      if (activeChat.value && getConversationIdentityKey(activeChat.value) === openedIdentity) {
        activeChat.value = { ...activeChat.value, unreadCount: 0 }
      }
    } catch { /* Read receipts are best-effort. */ }
  }
}

async function loadChatMessages({ background = false } = {}) {
  const conv = activeChat.value
  if (!conv) return { loaded: false }
  const conversationIdentity = getConversationIdentityKey(conv)
  const requestToken = chatRequestGuard.begin(conversationIdentity)
  const previousMessages = chatMessages.value
  if (!background) chatLoading.value = true
  const requestConfig = {
    uiMode: background ? 'background' : 'foreground',
    suppressGlobalError: true,
  }
  try {
    const sid = normalizeSid(conv.sid)
    const peerUserId = normalizePeerUserId(conv.peerUserId || conv.peerExternalUid || conv.externalBuyerId || '')
    const basePayload = {
      xianyuAccountId: Number(conv.xianyuAccountId || conv.accountId || selectedAccountId.value),
      sid,
      sId: sid,
      sessionId: sid,
      peerUserId,
      limit: 50,
      offset: 0
    }
    let res = await messageContext(basePayload, requestConfig)
    let nextMessages = normalizeMessages(res?.data)
    if (!nextMessages.length && peerUserId) {
      if (!chatRequestGuard.isCurrent(requestToken, getConversationIdentityKey(activeChat.value))) return { loaded: false, stale: true }
      res = await messageContext({ ...basePayload, sid: '', sId: '', sessionId: '' }, requestConfig)
      nextMessages = normalizeMessages(res?.data)
    }
    if (!chatRequestGuard.isCurrent(requestToken, getConversationIdentityKey(activeChat.value))) return { loaded: false, stale: true }
    chatMessages.value = dedupeMessages(mergeRemoteMessagesWithLocalAttempts(nextMessages, previousMessages))
    chatError.value = ''
    return { loaded: true }
  } catch {
    if (!chatRequestGuard.isCurrent(requestToken, getConversationIdentityKey(activeChat.value))) return { loaded: false, stale: true }
    chatMessages.value = previousMessages
    chatError.value = previousMessages.length
      ? '消息刷新失败，已保留上次成功加载的记录。'
      : '消息记录暂不可用，当前无法确认会话是否为空。'
    return { loaded: false, preserved: previousMessages.length > 0 }
  } finally {
    if (chatRequestGuard.isCurrent(requestToken, getConversationIdentityKey(activeChat.value))) {
      chatLoading.value = false
      await nextTick()
      scrollToBottom()
    }
  }
}

function normalizeMessages(data) {
  if (!data) return []
  const list = Array.isArray(data)
    ? data
    : Array.isArray(data.records)
      ? data.records
      : Array.isArray(data.list)
        ? data.list
        : Array.isArray(data.messages)
          ? data.messages
          : []
  return list.map(item => ({
    ...item,
    xianyuAccountId: Number(item.xianyuAccountId || item.accountId || 0) || undefined,
    accountId: Number(item.accountId || item.xianyuAccountId || 0) || undefined,
    sid: normalizeSid(item.sid || item.sId || item.sessionId || item.conversationId || ''),
    peerUserId: normalizePeerUserId(item.peerUserId || item.peerExternalUid || item.externalBuyerId || item.senderUserId || item.receiverUserId || ''),
    content: extractMessageDisplayText(item),
    text: extractMessageDisplayText(item),
    imageUrl: item.imageUrl || item.url || item.media?.url || '',
    messageTime: Number(item.messageTime || item.createdAt || item.timestamp || item.sendTime || 0),
    isAutoReply: Number(item.isAutoReply ?? item.is_auto_reply ?? 0)
  }))
}

function upsertConversationFromEvent(payload) {
  const sid = normalizeSid(payload.sId || payload.sid || payload.sessionId || payload.conversationId || '')
  if (!sid) return
  const accountId = Number(payload.accountId || payload.xianyuAccountId || activeChat.value?.xianyuAccountId || selectedAccountId.value || 0) || undefined
  const peerUserId = normalizePeerUserId(
    payload.peerUserId || payload.peerExternalUid || payload.senderUserId || payload.receiverUserId || ''
  )
  const preview = extractMessageDisplayText(payload)
  const nextConversation = {
    ...payload,
    xianyuAccountId: accountId,
    sid,
    peerUserId,
    lastMessage: preview,
    lastContent: preview,
    lastMessageTime: Number(payload.messageTime || Date.now()),
    updatedAt: Number(payload.messageTime || Date.now()),
    lastIsAutoReply: Boolean(payload.isAutoReply || payload.is_auto_reply),
    botEnabled: Boolean(payload.isAutoReply || payload.is_auto_reply || payload.botEnabled),
  }
  const incomingUnread = String(payload.direction || '').toUpperCase() === 'IN'
  const existingIndex = findConversationMatchIndex(conversations.value, nextConversation)
  if (existingIndex >= 0) {
    const existing = conversations.value[existingIndex]
    const merged = {
      ...existing,
      ...nextConversation,
      unreadCount: isSameConversation(activeChat.value, nextConversation)
        ? 0
        : Math.max(Number(existing.unreadCount || 0), Number(nextConversation.unreadCount || 0)) + (incomingUnread ? 1 : 0),
    }
    conversations.value = [merged, ...conversations.value.filter((_, index) => index !== existingIndex)]
  } else {
    conversations.value = [{ ...nextConversation, unreadCount: incomingUnread ? 1 : 0 }, ...conversations.value]
  }
}

function onSse(event) {
  const data = event?.detail?.payload || event?.detail || {}
  if (!matchesAccountSelection(selectedAccountId.value, data)) return
  const normalized = normalizeMessages([data])[0]
  if (!normalized) return
  if (activeChat.value && isSameConversation(activeChat.value, data)) {
    chatMessages.value = dedupeMessages([...chatMessages.value, normalized])
    activeChat.value = { ...activeChat.value, unreadCount: 0 }
    nextTick(() => scrollToBottom())
  }
  upsertConversationFromEvent(data)
}

const pollLatestMessages = createSingleFlight(async () => {
  if (typeof document !== 'undefined' && document.hidden) return
  await loadConversations({ background: true })
  if (!sending.value && (activeChat.value?.sid || activeChat.value?.peerUserId)) {
    await loadChatMessages({ background: true })
  }
})

function startPolling() {
  stopPolling()
  pollingTimer = setInterval(() => {
    pollLatestMessages().catch(() => {})
  }, POLLING_INTERVAL_MS)
}

function stopPolling() {
  if (pollingTimer) {
    clearInterval(pollingTimer)
    pollingTimer = null
  }
}

function isOutgoing(msg) {
  const dir = String(msg.direction || msg.msgDirection || '').toUpperCase()
  if (dir === 'OUT' || dir === 'SEND') return true
  if (dir === 'IN' || dir === 'RECV') return false
  return msg.fromSelf === true || msg.self === true || msg.isSelf === true
}

function scrollToBottom() {
  if (chatBoxRef.value) {
    chatBoxRef.value.scrollTop = chatBoxRef.value.scrollHeight
  }
}

function previewImage(url) {
  if (url) openExternalUrl(url)
}

function createMessageIdempotencyKey() {
  const uuid = globalThis.crypto?.randomUUID?.().replace(/[^A-Za-z0-9]/g, '')
  const entropy = uuid || `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`
  return `mobile-text-${entropy}`.slice(0, 128)
}

async function sendCurrentMessage() {
  const text = draft.value.trim()
  if (!text || sending.value || chatLoading.value || chatBlockingError.value) return
  const conv = activeChat.value
  if (!conv) return
  sendError.value = ''
  sending.value = true
  chatRequestGuard.invalidate()
  chatLoading.value = false
  const accId = Number(conv.xianyuAccountId || conv.accountId || selectedAccountId.value)
  const tempId = `temp_${Date.now()}`
  const idempotencyKey = createMessageIdempotencyKey()
  const optimistic = {
    id: tempId,
    pnmId: tempId,
    content: text,
    direction: 'OUT',
    createdAt: new Date().toISOString(),
    sendStatus: 'sending',
    retrySafe: false,
    idempotencyKey
  }
  chatMessages.value.push(optimistic)
  draft.value = ''
  await nextTick()
  scrollToBottom()
  try {
    const res = await sendMessage({
      xianyuAccountId: accId,
      cid: conv.sid,
      sid: conv.sid,
      sId: conv.sid,
      sessionId: conv.sid,
      toId: conv.peerUserId,
      peerUserId: conv.peerUserId,
      text,
      content: text,
      message: text,
      xyGoodsId: conv.xyGoodsId || conv.goodsId || '',
      msgType: 'text',
      idempotencyKey
    })
    const outcome = resolveManualMessageOutcome(res)
    const target = chatMessages.value.find(m => m.id === tempId)
    if (target) {
      const platformMessageId = outcome.uuid || ''
      target.pnmId = platformMessageId || target.pnmId || ''
      target.id = platformMessageId || tempId
      target.sendStatus = outcome.status
      target.retrySafe = outcome.retrySafe
    }
    if (outcome.status !== 'confirmed') {
      sendError.value = outcome.status === 'failed' && outcome.retrySafe
        ? (outcome.message || '平台明确未接收消息，可在排查后重新发送。')
        : (outcome.message || '发送结果未确认，请先在闲鱼 App 核对；请勿直接重发。')
    }
  } catch (error) {
    const outcome = resolveManualMessageError(error)
    const target = chatMessages.value.find(m => m.id === tempId)
    if (target) {
      target.sendStatus = outcome.status
      target.retrySafe = false
    }
    sendError.value = outcome.message
  } finally {
    sending.value = false
    await nextTick()
    scrollToBottom()
  }
}

function restoreDraft(message) {
  const content = String(message?.content || message?.text || '').trim()
  if (!content) return
  draft.value = content
  sendError.value = '内容已复制到输入框。发送前请先在闲鱼 App 核对，避免重复发送。'
}

function closeChat() {
  chatRequestGuard.invalidate()
  activeChat.value = null
  chatMessages.value = []
  draft.value = ''
  chatError.value = ''
  sendError.value = ''
}

onMounted(async () => {
  const accountsLoaded = await loadAccounts()
  if (accountsLoaded) await loadConversations()
  else loading.value = false
  window.addEventListener('xya-sse-event', onSse)
  startPolling()
})

onBeforeUnmount(() => {
  window.removeEventListener('xya-sse-event', onSse)
  conversationRequestGuard.invalidate()
  chatRequestGuard.invalidate()
  stopPolling()
})
</script>

<style scoped>
.m-msg {
  padding: 12px 16px 0;
  display: flex;
  flex-direction: column;
  min-height: calc(100vh - 120px);
  width: 100%;
  max-width: 100%;
  min-width: 0;
  box-sizing: border-box;
  overflow-x: hidden;
}
.m-page-header { margin-bottom: 20px; }
.m-page-header h1 { margin: 0 0 4px; font-size: 26px; font-weight: 800; color: #15213d; }
.m-page-sub { margin: 0; font-size: 13px; color: #8c98ae; }

.m-account-chips {
  display: flex;
  gap: 8px;
  overflow-x: auto;
  overflow-y: hidden;
  padding-bottom: 10px;
  margin-left: -16px;
  margin-right: -16px;
  padding-left: 16px;
  padding-right: 16px;
  -webkit-overflow-scrolling: touch;
  scrollbar-width: none;
}
.m-account-chips::-webkit-scrollbar { display: none; }
.m-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  background: white;
  border: 1px solid #e8edf5;
  color: #51607a;
  font-size: 13px;
  font-weight: 500;
  padding: 10px 16px;
  min-height: 44px;
  box-sizing: border-box;
  border-radius: 100px;
  white-space: nowrap;
  cursor: pointer;
  flex-shrink: 0;
  transition: all 0.15s;
}
.m-chip.active {
  background: linear-gradient(135deg, #0d6bff, #2580ff);
  color: white;
  border-color: transparent;
  box-shadow: 0 4px 10px rgba(13,107,255,0.25);
}
.m-chip-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #c4cddb;
  flex-shrink: 0;
}
.m-chip-dot.online { background: #16bf78; box-shadow: 0 0 0 2px rgba(22,191,120,0.18); }

.m-filter-tabs {
  display: flex;
  gap: 6px;
  margin-bottom: 12px;
  margin-left: -16px;
  margin-right: -16px;
  padding-left: 16px;
  padding-right: 16px;
  overflow-x: auto;
  overflow-y: hidden;
  -webkit-overflow-scrolling: touch;
  scrollbar-width: none;
}
.m-filter-tabs::-webkit-scrollbar { display: none; }
.m-filter-tab {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  background: transparent;
  border: none;
  color: #72809a;
  font-size: 13px;
  font-weight: 500;
  padding: 10px 14px;
  min-height: 44px;
  box-sizing: border-box;
  border-radius: 100px;
  cursor: pointer;
  white-space: nowrap;
  flex-shrink: 0;
  transition: all 0.15s;
}
.m-filter-tab.active {
  background: rgba(13,107,255,0.1);
  color: #0d6bff;
  font-weight: 600;
}
.m-filter-count {
  background: rgba(13,107,255,0.12);
  color: #0d6bff;
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 100px;
  font-weight: 600;
}
.m-filter-tab.active .m-filter-count {
  background: #0d6bff;
  color: white;
}
.m-state-warning {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 12px;
  padding: 12px 14px;
  border: 1px solid #f6d58a;
  border-radius: 14px;
  color: #8a4b08;
  background: #fff8e8;
  font-size: 12px;
  line-height: 1.5;
}
.m-state-warning button,
.m-inline-retry {
  min-height: 40px;
  padding: 0 14px;
  flex-shrink: 0;
  border: 1px solid #e2ad3b;
  border-radius: 12px;
  color: #744006;
  background: white;
  font-weight: 600;
  cursor: pointer;
}

.m-chat-load-warning {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 10px 12px 0;
  padding: 9px 10px;
  border: 1px solid #fecaca;
  border-radius: 10px;
  background: #fff7f7;
  color: #b42318;
  font-size: 12px;
}

.m-chat-load-warning span { flex: 1; }

.m-loading {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  padding: 50px 20px;
  color: #8c98ae;
  font-size: 13px;
}
.m-loading-spinner {
  width: 28px;
  height: 28px;
  border: 3px solid #e8edf5;
  border-top-color: #0d6bff;
  border-radius: 50%;
  animation: m-spin 0.8s linear infinite;
}
@keyframes m-spin { to { transform: rotate(360deg); } }

.m-empty {
  text-align: center;
  padding: 60px 20px;
}
.m-empty-icon {
  width: 80px;
  height: 80px;
  margin: 0 auto 16px;
  border-radius: 50%;
  background: linear-gradient(135deg, #e8f1ff, #d4e4ff);
  color: #0d6bff;
  display: flex;
  align-items: center;
  justify-content: center;
}
.m-empty-text { font-size: 16px; font-weight: 600; color: #15213d; margin-bottom: 6px; }
.m-empty-desc { font-size: 13px; color: #8c98ae; }

.m-msg-list {
  background: white;
  border-radius: 16px;
  border: 1px solid #f0f4fa;
  overflow: hidden;
  box-shadow: 0 2px 8px rgba(31,53,94,0.05);
}
.m-msg-item {
  display: flex;
  gap: 12px;
  padding: 14px 16px;
  min-width: 0;
  border-bottom: 1px solid #f4f7fc;
  cursor: pointer;
  transition: background 0.15s;
}
.m-msg-item:last-child { border-bottom: none; }
.m-msg-item:active { background: #f8faff; }

.m-msg-avatar {
  width: 44px;
  height: 44px;
  border-radius: 50%;
  background: linear-gradient(135deg, #e8f1ff, #d4e4ff);
  color: #0d6bff;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  position: relative;
}
.m-msg-avatar.is-robot {
  background: linear-gradient(135deg, #f0ebff, #e2d8ff);
  color: #8b5cf6;
}
.m-msg-dot {
  position: absolute;
  top: 0;
  right: 0;
  width: 10px;
  height: 10px;
  background: #ff5252;
  border: 2px solid white;
  border-radius: 50%;
}
.m-msg-body { flex: 1; min-width: 0; }
.m-msg-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 4px;
}
.m-msg-name { font-size: 15px; font-weight: 600; color: #15213d; max-width: 60%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.m-msg-time { font-size: 11px; color: #b0bacb; flex-shrink: 0; }
.m-msg-bottom {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.m-msg-preview {
  font-size: 13px;
  color: #8c98ae;
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.m-ai-row {
  display: flex;
  margin-bottom: 4px;
}
.m-ai-row.out {
  justify-content: flex-end;
}
.m-ai-badge {
  display: inline-flex;
  align-items: center;
  height: 18px;
  padding: 0 7px;
  border-radius: 999px;
  background: rgba(22, 163, 74, 0.12);
  color: #15803d;
  font-size: 10px;
  font-weight: 700;
  line-height: 18px;
  white-space: nowrap;
}
.m-ai-badge.inline {
  margin-right: 6px;
  vertical-align: middle;
}
.m-msg-badge {
  min-width: 20px;
  height: 20px;
  border-radius: 10px;
  background: #ff5252;
  color: white;
  font-size: 11px;
  font-weight: 600;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0 6px;
  flex-shrink: 0;
}
.m-msg-status {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 100px;
  flex-shrink: 0;
}
.m-msg-status-done {
  background: #f0f4fa;
  color: #72809a;
}
.m-msg-goods {
  font-size: 11px;
  color: #0d6bff;
  margin-top: 6px;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  background: rgba(13,107,255,0.08);
  padding: 3px 8px;
  border-radius: 6px;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.m-msg-tip {
  margin-top: 20px;
  background: #f8faff;
  border-radius: 14px;
  padding: 12px 14px;
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: #72809a;
}
.m-msg-tip :deep(svg) { color: #8b5cf6; flex-shrink: 0; }
.m-msg-tip span { flex: 1; min-width: 0; }
.m-tip-btn {
  margin-left: auto;
  background: linear-gradient(135deg, #0d6bff, #2580ff);
  color: white;
  border: none;
  border-radius: 100px;
  padding: 10px 16px;
  min-height: 44px;
  box-sizing: border-box;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  flex-shrink: 0;
}

/* 聊天详情视图 */
.m-chat-header {
  position: sticky;
  top: 0;
  z-index: 10;
  background: rgba(245, 248, 255, 0.95);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  padding: 10px 4px 10px 0;
  display: flex;
  align-items: center;
  gap: 10px;
  border-bottom: 1px solid rgba(231, 237, 247, 0.7);
  margin: -12px -16px 0;
  padding-left: 8px;
  padding-right: 12px;
}
.m-chat-back {
  background: none;
  border: none;
  color: #15213d;
  width: 44px;
  height: 44px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  flex-shrink: 0;
}
.m-chat-back:active { background: rgba(13,107,255,0.08); }
.m-chat-title { flex: 1; min-width: 0; }
.m-chat-name {
  font-size: 16px;
  font-weight: 700;
  color: #15213d;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.m-chat-goods {
  font-size: 11px;
  color: #0d6bff;
  margin-top: 2px;
  display: inline-flex;
  align-items: center;
  gap: 3px;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.m-chat-more {
  background: none;
  border: none;
  color: #72809a;
  width: 44px;
  height: 44px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  flex-shrink: 0;
}
.m-chat-more:active { background: rgba(13,107,255,0.08); }

.m-chat-body {
  flex: 1;
  overflow-y: auto;
  -webkit-overflow-scrolling: touch;
  padding: 14px 0;
  min-height: 300px;
}
.m-chat-loading, .m-chat-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  padding: 60px 20px;
  color: #b0bacb;
  font-size: 13px;
}
.m-chat-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 0 4px;
}
.m-bubble {
  display: flex;
  gap: 8px;
  align-items: flex-end;
  max-width: 100%;
}
.m-bubble.in { justify-content: flex-start; }
.m-bubble.out { justify-content: flex-end; }
.m-bubble-avatar {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background: linear-gradient(135deg, #e8f1ff, #d4e4ff);
  color: #0d6bff;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.m-bubble-avatar.out-avatar {
  background: linear-gradient(135deg, #0d6bff, #2580ff);
  color: white;
}
.m-bubble-content {
  max-width: 75%;
  min-width: 0;
  padding: 10px 14px;
  border-radius: 16px;
  font-size: 14px;
  line-height: 1.5;
  word-wrap: break-word;
  overflow-wrap: break-word;
}
.m-bubble.in .m-bubble-content {
  background: white;
  color: #15213d;
  border: 1px solid #eef2fa;
  border-bottom-left-radius: 4px;
  box-shadow: 0 1px 3px rgba(31,53,94,0.06);
}
.m-bubble.out .m-bubble-content {
  background: linear-gradient(135deg, #0d6bff, #2580ff);
  color: white;
  border-bottom-right-radius: 4px;
  box-shadow: 0 2px 8px rgba(13,107,255,0.2);
}
.m-bubble-text { white-space: pre-wrap; }
.m-bubble-image-wrap { margin-bottom: 4px; }
.m-bubble-image {
  max-width: 100%;
  max-height: 200px;
  border-radius: 10px;
  display: block;
  cursor: pointer;
}
.m-bubble-time {
  font-size: 10px;
  margin-top: 4px;
  opacity: 0.7;
}
.m-bubble.in .m-bubble-time { color: #b0bacb; }
.m-bubble.out .m-bubble-time { color: rgba(255,255,255,0.8); }
.m-bubble-send-status {
  margin-top: 5px;
  font-size: 10px;
  color: rgba(255,255,255,0.85);
}
.m-bubble-send-status.failed { color: #fff3bf; }
.m-bubble-send-status button {
  display: block;
  min-height: 32px;
  margin-top: 5px;
  padding: 0 10px;
  border: 1px solid rgba(255,255,255,0.65);
  border-radius: 10px;
  color: white;
  background: rgba(0,0,0,0.08);
  font-size: 11px;
  cursor: pointer;
}

.m-send-error {
  margin: 0 -16px;
  padding: 10px 16px;
  border-top: 1px solid #f6d58a;
  color: #8a4b08;
  background: #fff8e8;
  font-size: 12px;
  line-height: 1.5;
}

.m-chat-input {
  position: sticky;
  bottom: 0;
  background: white;
  border-top: 1px solid #eef2fa;
  padding: 10px 10px max(10px, env(safe-area-inset-bottom));
  display: flex;
  gap: 8px;
  align-items: center;
  margin: 0 -16px;
  padding-left: 12px;
  padding-right: 12px;
  box-shadow: 0 -2px 12px rgba(31,53,94,0.06);
}
.m-chat-input input {
  flex: 1;
  height: 44px;
  border: 1px solid #e8edf5;
  border-radius: 100px;
  padding: 0 16px;
  font-size: 14px;
  background: #f8faff;
  outline: none;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.m-chat-input input:focus { border-color: #0d6bff; background: white; box-shadow: 0 0 0 3px rgba(13,107,255,0.1); }
.m-chat-send {
  width: 44px;
  height: 44px;
  border-radius: 50%;
  border: none;
  background: linear-gradient(135deg, #0d6bff, #2580ff);
  color: white;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  flex-shrink: 0;
  box-shadow: 0 4px 10px rgba(13,107,255,0.3);
  transition: transform 0.1s;
}
.m-chat-send:active { transform: scale(0.95); }
.m-chat-send:disabled {
  background: #d4ddea;
  box-shadow: none;
  cursor: not-allowed;
}

.m-safe-bottom { height: 80px; }

@media (max-width: 360px) {
  .m-msg { padding: 10px 12px 0; }
  .m-page-header h1 { font-size: 22px; }
  .m-page-sub { font-size: 12px; }
  .m-chip { padding: 8px 12px; font-size: 12px; }
  .m-filter-tab { padding: 8px 12px; font-size: 12px; }
  .m-msg-item { padding: 12px; gap: 10px; }
  .m-msg-avatar { width: 40px; height: 40px; }
  .m-msg-name { font-size: 14px; }
  .m-msg-preview { font-size: 12px; }
  .m-msg-goods { font-size: 10px; padding: 2px 6px; }
  .m-msg-tip { padding: 10px 12px; font-size: 11px; }
  .m-bubble-content { padding: 9px 12px; font-size: 13px; }
  .m-chat-header { padding-left: 6px; padding-right: 10px; }
  .m-chat-input { padding-left: 10px; padding-right: 10px; }
  .m-chat-input input { font-size: 13px; padding: 0 12px; }
}
</style>
