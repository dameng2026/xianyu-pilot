<template>
  <div class="profile-center">
    <div v-if="notice.text" :class="['global-notice', notice.type]">
      {{ notice.text }}
    </div>
    <div v-if="overviewStale" class="global-notice warning" role="status">
      个人中心刷新失败，当前保留上次成功加载的数据；统计与安全更新时间可能已变化。
    </div>

    <div class="profile-shell">
      <aside class="profile-side">
        <div class="card-panel profile-side-card">
          <div class="profile-side-head">
            <h2>个人中心</h2>
          </div>

          <div class="profile-side-nav">
            <button
              v-for="item in tabs"
              :key="item.key"
              type="button"
              :class="['profile-side-tab', { active: menuActiveKey === item.key }]"
              @click="activeTab = item.key"
            >
              <span class="profile-side-tab-icon" aria-hidden="true">
                <svg v-if="item.key === 'overview'" viewBox="0 0 24 24" fill="none">
                  <path d="M4.5 10.5L12 4l7.5 6.5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" />
                  <path d="M7.5 9.5v9h9v-9" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" />
                  <path d="M10.5 18.5v-5h3v5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" />
                </svg>
                <svg v-else-if="item.key === 'security'" viewBox="0 0 24 24" fill="none">
                  <path d="M12 3l6.5 3v5.3c0 4.3-2.8 8.2-6.5 9.7-3.7-1.5-6.5-5.4-6.5-9.7V6L12 3z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" />
                </svg>
                <svg v-else viewBox="0 0 24 24" fill="none">
                  <circle cx="12" cy="12" r="7" stroke="currentColor" stroke-width="1.8" />
                  <circle cx="12" cy="12" r="2.5" fill="currentColor" />
                </svg>
              </span>
              <span class="profile-side-tab-label">{{ item.label }}</span>
            </button>
          </div>
        </div>
      </aside>

      <div class="profile-main">
        <EmptyState
          v-if="activeTab !== 'password' && overviewLoading && overviewAvailable !== true"
          icon="⏳"
          title="个人中心加载中"
          description="正在读取账户、统计与安全更新时间。"
        />
        <EmptyState
          v-else-if="activeTab !== 'password' && overviewAvailable === false"
          icon="⚠️"
          title="个人中心暂不可用"
          description="当前无法确认账户信息与统计数据；请求失败不会显示为 0 或“已完成”。"
        >
          <template #actions><button type="button" class="app-btn" :disabled="overviewLoading" @click="loadOverview">重新加载</button></template>
        </EmptyState>
        <div v-else-if="activeTab === 'overview'" class="profile-main-section profile-overview">
          <section class="card-panel welcome-hero">
            <div class="welcome-content">
              <div class="welcome-avatar">
                <svg viewBox="0 0 64 64" width="64" height="64">
                  <defs>
                    <linearGradient id="avG" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stop-color="#5a9fff" />
                      <stop offset="100%" stop-color="#0d6bff" />
                    </linearGradient>
                  </defs>
                  <circle cx="32" cy="32" r="30" fill="url(#avG)" opacity="0.12" />
                  <circle cx="32" cy="24" r="10" fill="url(#avG)" />
                  <path d="M14 52c0-10 8-16 18-16s18 6 18 16" fill="url(#avG)" />
                </svg>
              </div>

              <div class="welcome-text">
                <h2>欢迎回来，{{ overview.nickname || overview.username || '用户' }} <span class="wave">👋</span></h2>
                <p>管理账户资料、安全设置和当前部署能力，保障账户安全，提升使用效率。</p>

                <div class="welcome-tags">
                  <span class="chip plan-chip">{{ planName }}</span>
                  <span class="chip subtle-chip">{{ planBadge }}</span>
                  <span v-if="overview.tenantName" class="chip subtle-chip">{{ overview.tenantName }}</span>
                  <span class="chip">
                    <svg viewBox="0 0 16 16" width="12" height="12" fill="none">
                      <circle cx="8" cy="8" r="7" fill="#16bf78" />
                      <path d="M5 8l2 2 4-4" stroke="#fff" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" />
                    </svg>
                    固定账号密码登录
                  </span>
                </div>
              </div>
            </div>

            <div class="welcome-visual" aria-hidden="true">
              <div class="welcome-visual-glow"></div>
              <img class="welcome-visual-image" src="/xya/profile-center/profile-hero.png" alt="" />
            </div>
          </section>

          <div class="profile-stats">
            <article v-for="item in statCards" :key="item.label" class="stat-card">
              <div class="stat-card-main">
                <div :class="['stat-icon', item.toneClass]">
                  <img class="stat-icon-img" :src="item.iconSrc" alt="" />
                </div>
                <div class="stat-info">
                  <span>{{ item.label }}</span>
                  <strong>{{ item.value }}</strong>
                </div>
              </div>

              <div class="stat-card-foot">
                <em>{{ item.desc }}</em>
                <svg :class="['stat-wave', item.toneClass]" viewBox="0 0 96 18" preserveAspectRatio="none" aria-hidden="true">
                  <path d="M2 11c7 0 7-8 14-8s7 8 14 8 7-8 14-8 7 8 14 8 7-8 14-8 7 8 14 8" />
                </svg>
              </div>
            </article>
          </div>

          <div class="two-col-grid">
            <section class="card-panel member-panel">
              <div class="panel-head">
                <div class="panel-title">
                  <span class="panel-head-mark gold" aria-hidden="true">
                    <img class="panel-head-icon" src="/xya/profile-center/icons/shield.png" alt="" />
                  </span>
                  <h3>开源部署能力</h3>
                </div>
              </div>

              <div class="member-card-inner">
                <div class="member-left">
                  <div class="deployment-badge-visual" aria-hidden="true">
                    <img class="member-card-icon" src="/xya/profile-center/icons/shield.png" alt="" />
                  </div>
                  <div>
                    <div class="member-name-row">
                      <strong>{{ planName }}</strong>
                      <span class="badge gray">{{ planBadge }}</span>
                    </div>
                    <span class="member-sub">{{ planPeriodText }}</span>
                  </div>
                </div>

                <div class="member-actions">
                  <button type="button" class="app-btn primary" @click="handleQuickAction('settings-model')">配置 AI 服务</button>
                </div>
              </div>

              <div class="benefits-row">
                <span class="benefits-label">可用模块：</span>
                <div class="benefits-grid">
                  <div v-for="b in memberBenefits" :key="b.label" class="benefit-feature">
                    <span class="benefit-feature-icon" aria-hidden="true">
                      <img class="benefit-icon" :src="b.iconSrc" alt="" />
                    </span>
                    <span class="benefit-feature-label">{{ b.label }}</span>
                  </div>
                </div>
              </div>
            </section>
          </div>

          <div class="overview-bottom-grid">
            <section class="card-panel account-panel">
              <div class="panel-head">
                <div class="panel-title">
                  <span class="panel-head-mark blue" aria-hidden="true">
                    <img class="panel-head-icon" src="/xya/profile-center/icons/shield.png" alt="" />
                  </span>
                  <h3>账户信息</h3>
                </div>
              </div>

              <div class="account-info-grid">
                <article v-for="item in accountInfoItems" :key="item.label" class="account-info-item">
                  <span class="account-info-label">{{ item.label }}</span>
                  <div class="account-info-value-row">
                    <strong>{{ item.value }}</strong>
                    <span v-if="item.badge" :class="['badge', item.badgeClass]">{{ item.badge }}</span>
                  </div>
                </article>
              </div>
            </section>

            <section class="card-panel quick-panel">
              <div class="panel-head">
                <div class="panel-title">
                  <span class="panel-head-mark mint" aria-hidden="true">
                    <img class="panel-head-icon" src="/xya/profile-center/icons/workflow.png" alt="" />
                  </span>
                  <h3>快捷操作</h3>
                </div>
              </div>

              <div class="quick-grid-2col">
                <button
                  v-for="item in quickActionItems"
                  :key="item.title"
                  type="button"
                  class="quick-card quick-action-btn"
                  @click="handleQuickAction(item.action)"
                >
                  <div :class="['circle-ico', item.tone]">
                    <img class="quick-icon-img" :src="item.iconSrc" alt="" />
                  </div>
                  <div class="quick-card-copy">
                    <b>{{ item.title }}</b>
                    <span>{{ item.desc }}</span>
                  </div>
                </button>
              </div>
            </section>
          </div>
        </div>

        <div v-else-if="activeTab === 'security'" class="card-panel security-panel content-panel">
          <div class="panel-head">
            <div>
              <h3>账号安全</h3>
              <p>当前开源版仅保留固定管理员账号和密码登录，安全设置聚焦密码管理。</p>
            </div>
            <button type="button" class="app-btn" @click="loadOverview">刷新</button>
          </div>

          <div class="security-level-card" :class="securityLevel.tone">
            <div class="security-level-visual" aria-hidden="true">
              <div class="security-level-visual-ring"></div>
              <svg class="security-level-visual-illustration" viewBox="0 0 320 220" fill="none">
                <path class="security-illustration-shape shape-left" d="M55 70c16-18 39-24 59-17-10 8-16 20-16 34 0 9 2 17 7 24-25 7-44 4-60-11-13-12-19-34 10-30Z" />
                <path class="security-illustration-shape shape-right" d="M213 58c12-7 29-8 44-2-8 6-13 15-13 27 0 11 4 21 12 27-17 8-34 6-47-7-12-13-13-33 4-45Z" />
                <ellipse class="security-orbit-glow" cx="140" cy="168" rx="84" ry="28" />
                <ellipse class="security-orbit-line" cx="140" cy="164" rx="96" ry="38" />
                <path class="security-orbit-dash" d="M46 167c18-22 49-35 93-35 44 0 75 13 95 33" />
                <circle class="security-orbit-dot dot-left" cx="50" cy="166" r="4.5" />
                <circle class="security-orbit-dot dot-right" cx="231" cy="135" r="4.5" />
                <circle class="security-orbit-dot dot-top" cx="262" cy="152" r="3.5" />
                <ellipse class="security-stage-shadow" cx="140" cy="174" rx="70" ry="18" />
                <ellipse class="security-stage-plate outer" cx="140" cy="164" rx="76" ry="22" />
                <ellipse class="security-stage-plate middle" cx="140" cy="160" rx="62" ry="18" />
                <ellipse class="security-stage-core" cx="140" cy="156" rx="42" ry="12" />
                <g transform="translate(78 18)">
                  <path class="security-shield-back" d="M78 8 126 28v40c0 33-18 62-48 81-30-19-48-48-48-81V28L78 8Z" />
                  <path class="security-shield-front" d="M74 14 116 31v35c0 29-15 54-42 70C47 120 32 95 32 66V31l42-17Z" />
                  <path class="security-shield-gloss" d="M74 21 103 33v29c0 18-8 35-23 47-17-8-29-22-34-40 8 5 18 7 27 7 12 0 23-4 31-12V34L74 21Z" />
                  <path class="security-shield-outline" d="M74 14 116 31v35c0 29-15 54-42 70C47 120 32 95 32 66V31l42-17Z" />
                  <path class="security-shield-check" d="m56 71 14 14 28-29" />
                </g>
              </svg>
            </div>
            <div class="security-level-main">
              <div class="security-level-topline">
                <div class="security-level-info">
                  <div class="security-level-icon">
                    <svg viewBox="0 0 24 24" width="28" height="28" fill="none">
                      <path d="M12 2L4 6v6c0 5 3.5 9.5 8 10 4.5-.5 8-5 8-10V6l-8-4z" fill="currentColor" opacity="0.18" />
                      <path d="M12 2L4 6v6c0 5 3.5 9.5 8 10 4.5-.5 8-5 8-10V6l-8-4z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" />
                      <path v-if="securityLevel.score >= 3" d="M8.5 12.5l2.5 2.5 4.5-5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
                      <path v-else d="M12 8v4M12 16v.5" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
                    </svg>
                  </div>
                  <div class="security-level-text">
                    <h4>账号安全等级</h4>
                    <strong>{{ securityLevel.label }}</strong>
                    <div class="security-level-summary">
                      <span v-if="!overview.lastSecurityUpdateTime" class="security-risk-pill">
                        尚无管理员密码更新记录
                      </span>
                      <span v-else class="security-risk-pill safe">
                        已记录密码更新；部署安全仍需独立验收
                      </span>
                    </div>
                    <p>{{ securityLevel.desc }}</p>
                  </div>
                </div>
                <span class="security-link-hint">安全等级说明</span>
              </div>

              <div class="security-progress-wrap">
                <div class="security-progress">
                  <div class="security-progress-bar" :style="{ width: securityLevel.percent + '%' }"></div>
                </div>
                <div class="security-progress-labels">
                  <span :class="{ active: securityLevel.score === 1 }">待维护</span>
                  <span :class="{ active: securityLevel.score === 2 }">基础</span>
                  <span :class="{ active: securityLevel.score >= 3 }">部署验收</span>
                </div>
              </div>

              <div class="security-meta">
                <span>上次安全更新：{{ displayDate(overview.lastSecurityUpdateTime) || '暂无' }}</span>
                <span>最近登录：{{ displayDate(overview.lastLoginTime) || '暂无' }}</span>
              </div>
            </div>
          </div>

          <div class="security-grid">
            <div class="security-card enhanced">
              <div class="security-card-head">
                <div class="security-card-icon blue">
                  <svg viewBox="0 0 24 24" width="22" height="22" fill="none">
                    <rect x="5" y="11" width="14" height="9" rx="2" fill="currentColor" opacity="0.18" />
                    <rect x="5" y="11" width="14" height="9" rx="2" stroke="currentColor" stroke-width="1.8" />
                    <path d="M8 11V8a4 4 0 018 0v3" stroke="currentColor" stroke-width="1.8" />
                  </svg>
                </div>
                <span class="badge">已设置</span>
              </div>
              <b>登录密码</b>
              <span>建议定期更换，使用 6 位以上字母、数字组合，不要与其他平台共用。</span>
              <div class="security-card-note">
                <span class="security-card-note-dot"></span>
                <span>{{ securityPasswordHint }}</span>
              </div>
              <button type="button" class="app-btn primary" @click="activeTab = 'password'">修改密码</button>
            </div>
          </div>

          <div class="security-tips">
            <div class="security-tips-copy">
              <h4>安全建议</h4>
              <ul class="security-bullet-list">
                <li v-for="item in securityAdviceList" :key="item">{{ item }}</li>
              </ul>
            </div>
          </div>
        </div>

        <div v-else-if="activeTab === 'password'" class="card-panel form-panel content-panel">
          <div class="panel-head">
            <div>
              <h3>修改密码</h3>
              <p>新密码至少 6 位。修改成功后建议重新登录。</p>
            </div>
            <button type="button" class="app-btn" @click="activeTab = 'security'">返回</button>
          </div>
          <form class="profile-form" @submit.prevent="submitPassword">
            <label>
              <span>当前密码</span>
              <input v-model="passwordForm.oldPassword" type="password" autocomplete="current-password" placeholder="请输入当前密码" />
            </label>
            <label>
              <span>新密码</span>
              <input v-model="passwordForm.newPassword" type="password" autocomplete="new-password" placeholder="至少 6 位" />
            </label>
            <label>
              <span>确认新密码</span>
              <input v-model="passwordForm.confirmPassword" type="password" autocomplete="new-password" placeholder="再次输入新密码" />
            </label>
            <button type="submit" class="app-btn primary submit-btn" :disabled="saving">保存新密码</button>
          </form>
        </div>
      </div>
    </div>
</div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import {
  changeProfilePassword,
  getProfileOverview
} from '../api/profile.js'
import EmptyState from '../components/EmptyState.vue'

const tabs = [
  { key: 'overview', label: '概览' },
  { key: 'security', label: '账号安全' }
]

const activeTab = ref('overview')
const saving = ref(false)
const overview = reactive({})
const overviewAvailable = ref(null)
const overviewLoading = ref(true)
const overviewStale = ref(false)
const notice = reactive({ text: '', type: 'info' })

const passwordForm = reactive({ oldPassword: '', newPassword: '', confirmPassword: '' })

let noticeTimer = null

const stats = computed(() => overview.stats || {})
const planName = computed(() => '开源自托管版')
const planBadge = computed(() => 'SELF-HOSTED')
const menuActiveKey = computed(() => {
  if (activeTab.value === 'password') return 'security'
  return activeTab.value
})
const planPeriodText = computed(() => '功能以当前部署和系统配置为准')

const securityLevel = computed(() => {
  if (overview.lastSecurityUpdateTime) {
    return { score: 2, label: '基础已维护', tone: 'medium', desc: '仅确认管理员密码已有更新记录；不代表部署、网络、备份或终端安全已通过验收', percent: 66 }
  }
  return { score: 1, label: '待维护', tone: 'low', desc: '尚无管理员密码更新时间证据，建议尽快轮换并持续使用高强度密码', percent: 33 }
})

const securityPasswordHint = computed(() => {
  const value = overview.lastSecurityUpdateTime || overview.updatedTime
  if (!value) return '建议尽快完成首次密码更新并定期轮换'
  return `最近安全更新：${displayDate(value)}`
})

const statCards = computed(() => [
  { label: '闲鱼账号', value: stats.value.xianyuAccountCount ?? '—', desc: '已绑定账号', iconSrc: '/xya/profile-center/icons/workflow.png', toneClass: '' },
  { label: '商品总数', value: stats.value.goodsCount ?? '—', desc: '全部商品', iconSrc: '/xya/profile-center/icons/bag.png', toneClass: 'green' },
  { label: '订单总数', value: stats.value.orderCount ?? '—', desc: '全部订单', iconSrc: '/xya/profile-center/icons/audit.png', toneClass: 'orange' },
  { label: '在线会话', value: stats.value.conversationCount ?? '—', desc: '当前会话数', iconSrc: '/xya/profile-center/icons/message.png', toneClass: 'purple' }
])

const memberBenefits = [
  { label: '智能运营', iconSrc: '/xya/profile-center/icons/chart.png' },
  { label: '在线消息', iconSrc: '/xya/profile-center/icons/message.png' },
  { label: '数据赋能', iconSrc: '/xya/profile-center/icons/chart.png' },
  { label: '智能客服', iconSrc: '/xya/profile-center/icons/shield.png' },
  { label: '自动化', iconSrc: '/xya/profile-center/icons/workflow.png' },
  { label: '发布商品', iconSrc: '/xya/profile-center/icons/bag.png' }
]

const quickActionItems = [
  { title: '账户安全', desc: '固定账号密码与密码修改', iconSrc: '/xya/profile-center/icons/shield.png', tone: 'blue-bg', action: 'security' },
  { title: '商品管理', desc: '查看商品、发布商品', iconSrc: '/xya/profile-center/icons/bag.png', tone: 'green-bg', action: 'products' },
  { title: 'AI 服务配置', desc: '接入当前部署使用的模型与凭据', iconSrc: '/xya/profile-center/icons/audit.png', tone: 'orange-bg', action: 'settings-model' }
]

const securityAdviceList = [
  '定期更换密码，避免使用与其他平台相同的密码',
  '不要向任何人透露密码等敏感信息',
  '优先使用长度更长、包含大小写字母和数字的强密码',
  '如发现异常登录，请立即修改密码、撤销现有会话并联系部署管理员核查日志'
]

const accountInfoItems = computed(() => [
  { label: '用户名', value: overview.username || '-' },
  { label: '昵称', value: overview.nickname || '-' },
  { label: '登录方式', value: '固定账号密码' },
  { label: '账户 ID', value: overview.userId ?? '-' },
  { label: '部署实例', value: overview.tenantName || '-' },
  { label: '账号状态', value: formatUserStatus(overview.status) },
  { label: '部署版本', value: planName.value || '-' },
  { label: '最近登录', value: displayDate(overview.lastLoginTime) },
  { label: '安全更新', value: displayDate(overview.lastSecurityUpdateTime) }
])

function showNotice(text, type = 'info') {
  notice.text = text
  notice.type = type
  if (noticeTimer) clearTimeout(noticeTimer)
  noticeTimer = setTimeout(() => { notice.text = '' }, 4200)
}

async function loadOverview() {
  const hadSnapshot = overviewAvailable.value === true
  overviewLoading.value = true
  try {
    const res = await getProfileOverview()
    for (const key of Object.keys(overview)) delete overview[key]
    Object.assign(overview, res.data || {})
    overviewAvailable.value = true
    overviewStale.value = false
    return true
  } catch {
    if (hadSnapshot) {
      overviewStale.value = true
      showNotice('个人中心刷新失败，已保留上次成功加载的数据。', 'warn')
    } else {
      for (const key of Object.keys(overview)) delete overview[key]
      overviewAvailable.value = false
      overviewStale.value = false
      showNotice('个人中心暂不可用，请稍后重新加载。', 'error')
    }
    return false
  } finally {
    overviewLoading.value = false
  }
}

async function submitPassword() {
  if (!passwordForm.oldPassword || !passwordForm.newPassword) return showNotice('请完整填写密码信息', 'warn')
  if (passwordForm.newPassword.length < 6) return showNotice('新密码至少 6 位', 'warn')
  if (passwordForm.newPassword !== passwordForm.confirmPassword) return showNotice('两次输入的新密码不一致', 'warn')
  saving.value = true
  try {
    await changeProfilePassword({ oldPassword: passwordForm.oldPassword, newPassword: passwordForm.newPassword })
    Object.assign(passwordForm, { oldPassword: '', newPassword: '', confirmPassword: '' })
    const refreshed = await loadOverview()
    showNotice(
      refreshed ? '密码已修改' : '密码已修改，但个人中心状态刷新失败，请稍后重新加载。',
      refreshed ? 'success' : 'warn'
    )
  } catch (error) {
    showNotice(error.message || '密码修改失败', 'error')
  } finally {
    saving.value = false
  }
}

function displayDate(value) {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return String(value)
  const pad = n => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function formatUserStatus(value) {
  const status = Number(value)
  if (status === 1) return '正常'
  if (status === 0) return '禁用'
  if (value == null || value === '' || value === 'null' || value === 'undefined') return '-'
  return String(value)
}

function handleQuickAction(action) {
  if (action === 'security') { activeTab.value = action; return }
  if (action === 'products') location.hash = '#/products'
  if (action === 'settings-model') location.hash = '#/settings-model'
}

function onHeaderAction(event) {
  if (event.detail === 'refresh-profile') loadOverview()
}

onMounted(() => {
  window.addEventListener('xya-header-action', onHeaderAction)
  loadOverview()
})

onBeforeUnmount(() => {
  window.removeEventListener('xya-header-action', onHeaderAction)
})
</script>

<style scoped>
.profile-center {
  width: 100%;
}

.profile-shell {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 16px;
  align-items: start;
}

.profile-side,
.profile-main,
.content-panel {
  min-width: 0;
}

.profile-side-card,
.welcome-hero,
.member-panel,
.account-panel,
.quick-panel,
.content-panel {
  border-radius: 24px;
  border: 1px solid rgba(229, 236, 247, 0.96);
  box-shadow: 0 12px 34px rgba(29, 53, 87, 0.05);
}

.profile-side-card {
  position: relative;
  padding: 12px;
  background:
    radial-gradient(circle at top left, rgba(13, 107, 255, 0.06), transparent 34%),
    linear-gradient(180deg, #ffffff 0%, #f7faff 100%);
}

.profile-side-head {
  display: none;
}

.profile-side-nav {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.profile-side-tab {
  position: relative;
  display: flex;
  align-items: center;
  gap: 9px;
  flex: 1 1 180px;
  width: auto;
  min-width: 0;
  padding: 12px 14px;
  border: 1px solid rgba(219, 228, 243, 0.9);
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.86);
  color: #50617d;
  text-align: left;
  cursor: pointer;
  transition: transform 0.18s ease, box-shadow 0.18s ease, border-color 0.18s ease, color 0.18s ease;
}

.profile-side-tab::before {
  content: '';
  position: absolute;
  left: -1px;
  top: 10px;
  bottom: 10px;
  width: 4px;
  border-radius: 999px;
  background: transparent;
  transition: background 0.18s ease;
}

.profile-side-tab:hover {
  transform: translateY(-1px);
  border-color: rgba(13, 107, 255, 0.22);
  box-shadow: 0 12px 28px rgba(13, 107, 255, 0.08);
}

.profile-side-tab.active {
  color: #0d6bff;
  border-color: rgba(13, 107, 255, 0.2);
  background: linear-gradient(135deg, #f8fbff 0%, #eef5ff 100%);
  box-shadow: 0 16px 36px rgba(13, 107, 255, 0.12);
}

.profile-side-tab.active::before {
  background: linear-gradient(180deg, #7cb8ff 0%, #0d6bff 100%);
}

.profile-side-tab-icon {
  width: 32px;
  height: 32px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 10px;
  background: #eef4ff;
  color: #0d6bff;
  flex: 0 0 auto;
}

.profile-side-tab-icon svg {
  width: 16px;
  height: 16px;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.profile-side-tab.active .profile-side-tab-icon {
  background: linear-gradient(145deg, #dbeafe 0%, #bfdbfe 100%);
}

.profile-side-tab-label {
  font-size: 13px;
  font-weight: 700;
}

.profile-main-section {
  display: grid;
  gap: 16px;
}

.welcome-hero {
  position: relative;
  overflow: hidden;
  min-height: 190px;
  padding: 24px 28px 22px;
  background:
    radial-gradient(circle at 14% 50%, rgba(255, 255, 255, 0.08), transparent 18%),
    radial-gradient(circle at 81% 34%, rgba(255, 255, 255, 0.42), transparent 18%),
    radial-gradient(circle at 68% 54%, rgba(229, 240, 255, 0.34), transparent 22%),
    linear-gradient(92deg, #6aaefe 0%, #7dbbff 18%, #9ccaff 57%, #bbdbff 100%);
}

.welcome-hero::before {
  content: '';
  position: absolute;
  inset: 10px 210px 10px 46%;
  background:
    radial-gradient(ellipse at 58% 56%, transparent 58%, rgba(255, 255, 255, 0.18) 59%, transparent 60%),
    radial-gradient(ellipse at 58% 56%, transparent 70%, rgba(255, 255, 255, 0.14) 71%, transparent 72%),
    radial-gradient(ellipse at 58% 56%, transparent 82%, rgba(255, 255, 255, 0.12) 83%, transparent 84%);
  opacity: 0.7;
  pointer-events: none;
}

.welcome-hero::after {
  content: '';
  position: absolute;
  inset: 14px;
  border-radius: 22px;
  border: 1px solid rgba(255, 255, 255, 0.3);
  pointer-events: none;
}

.welcome-content {
  position: relative;
  z-index: 2;
  display: flex;
  align-items: center;
  gap: 22px;
  max-width: calc(100% - 388px);
}

.welcome-avatar {
  width: 80px;
  height: 80px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 24px;
  background: linear-gradient(145deg, rgba(255, 255, 255, 0.92) 0%, rgba(231, 242, 255, 0.85) 100%);
  border: 1px solid rgba(255, 255, 255, 0.46);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.92), 0 20px 40px rgba(22, 76, 163, 0.18);
}

.welcome-avatar svg {
  width: 74px;
  height: 74px;
}

.welcome-text h2 {
  margin: 0;
  font-size: 39px;
  color: #ffffff;
  font-weight: 800;
  line-height: 1.12;
  letter-spacing: -0.02em;
  text-shadow: 0 10px 24px rgba(45, 98, 177, 0.18);
}

.wave {
  font-size: 24px;
}

.welcome-text p {
  margin: 8px 0 14px;
  color: rgba(245, 249, 255, 0.98);
  font-size: 14px;
  max-width: 680px;
}

.welcome-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 8px 12px;
  border-radius: 999px;
  border: 1px solid rgba(255, 255, 255, 0.3);
  background: rgba(255, 255, 255, 0.86);
  color: #4a5f7f;
  font-size: 11px;
  font-weight: 700;
  box-shadow: 0 4px 12px rgba(31, 53, 94, 0.035);
}

.plan-chip {
  color: #1674ff;
  border-color: rgba(255, 255, 255, 0.36);
  background: linear-gradient(135deg, rgba(244, 250, 255, 0.98) 0%, rgba(223, 238, 255, 0.94) 100%);
}

.subtle-chip {
  background: rgba(255, 255, 255, 0.76);
}

.warn-chip {
  background: #fff5e6;
  border-color: #fde3bb;
  color: #d97706;
}

.welcome-visual {
  position: absolute;
  right: 14px;
  top: 0;
  width: 372px;
  height: 188px;
  pointer-events: none;
  z-index: 1;
}

.welcome-visual-glow {
  position: absolute;
  inset: 16px 24px 16px 38px;
  border-radius: 68px;
  background: radial-gradient(circle at 50% 55%, rgba(255, 255, 255, 0.3) 0%, rgba(202, 228, 255, 0.18) 36%, transparent 74%);
  filter: blur(8px);
}

.welcome-visual-image {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: contain;
  object-position: center;
  filter: drop-shadow(0 22px 30px rgba(61, 113, 224, 0.13));
}

.profile-stats {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 14px;
}

.stat-card {
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  min-height: 116px;
  padding: 17px 16px;
  border-radius: 18px;
  border: 1px solid rgba(228, 235, 247, 0.96);
  background:
    radial-gradient(circle at top left, rgba(255, 255, 255, 0.98), transparent 36%),
    linear-gradient(180deg, #ffffff 0%, #fbfdff 100%);
  box-shadow: 0 10px 28px rgba(31, 53, 94, 0.045);
  transition: transform 0.18s ease, box-shadow 0.18s ease, border-color 0.18s ease;
}

.stat-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 1px 2px rgba(31, 53, 94, 0.04), 0 16px 32px rgba(31, 53, 94, 0.08);
  border-color: rgba(13, 107, 255, 0.16);
}

.stat-card-main {
  display: flex;
  align-items: center;
  gap: 10px;
}

.stat-card .stat-icon {
  width: 48px;
  height: 48px;
  border-radius: 16px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(145deg, #edf4ff 0%, #dbeafe 100%);
  color: #0d6bff;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.85);
}

.stat-icon-img {
  width: 28px;
  height: 28px;
  object-fit: contain;
  filter: drop-shadow(0 8px 16px rgba(31, 53, 94, 0.12));
}

.stat-card .stat-icon.green {
  background: linear-gradient(145deg, #e8fbef 0%, #c9f2d8 100%);
  color: #16a34a;
}

.stat-card .stat-icon.orange {
  background: linear-gradient(145deg, #fff6e8 0%, #ffe0ae 100%);
  color: #f59e0b;
}

.stat-card .stat-icon.purple {
  background: linear-gradient(145deg, #f3ecff 0%, #e3d4ff 100%);
  color: #8b5cf6;
}

.stat-info {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.stat-info span {
  font-size: 13px;
  font-weight: 700;
  color: #667491;
}

.stat-info strong {
  font-size: 28px;
  line-height: 1;
  font-weight: 900;
  color: #17213d;
  font-family: 'SF Mono', 'JetBrains Mono', 'Cascadia Code', monospace;
}

.stat-card-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 14px;
}

.stat-card-foot em {
  font-style: normal;
  font-size: 11px;
  color: #7a879e;
}

.stat-wave {
  width: 84px;
  height: 18px;
  flex: 0 0 auto;
}

.stat-wave path {
  fill: none;
  stroke: #5b7cff;
  stroke-width: 3;
  stroke-linecap: round;
}

.stat-wave.green path {
  stroke: #22c55e;
}

.stat-wave.orange path {
  stroke: #fb923c;
}

.stat-wave.purple path {
  stroke: #8b5cf6;
}

.two-col-grid,
.overview-bottom-grid {
  display: grid;
  gap: 16px;
}

.two-col-grid {
  grid-template-columns: minmax(0, 1.5fr) minmax(300px, 1fr);
}

.overview-bottom-grid {
  grid-template-columns: minmax(0, 1.68fr) minmax(320px, 0.96fr);
}

.profile-overview .panel-head {
  margin-bottom: 12px;
}

.profile-overview .panel-head h3 {
  font-size: 17px;
}

.profile-overview .member-panel,
.profile-overview .account-panel,
.profile-overview .quick-panel {
  padding: 18px;
}

.panel-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 18px;
}

.panel-title {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.panel-head-mark {
  width: 24px;
  height: 24px;
  border-radius: 8px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
  border: 1px solid rgba(226, 234, 245, 0.95);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.88);
}

.panel-head-mark.gold {
  background: linear-gradient(145deg, #fff8e5 0%, #ffe8ab 100%);
}

.panel-head-mark.violet {
  background: linear-gradient(145deg, #f5ecff 0%, #ead8ff 100%);
}

.panel-head-mark.blue {
  background: linear-gradient(145deg, #eef5ff 0%, #dbeafe 100%);
}

.panel-head-mark.mint {
  background: linear-gradient(145deg, #ecfbf3 0%, #d7f5e5 100%);
}

.panel-head-icon {
  width: 14px;
  height: 14px;
  object-fit: contain;
}

.panel-head h3 {
  margin: 0;
  font-size: 18px;
  font-weight: 800;
  color: #17213d;
}

.panel-head p {
  margin: 6px 0 0;
  color: #667491;
  font-size: 13px;
  line-height: 1.6;
}

.member-panel {
  background:
    radial-gradient(circle at top left, rgba(255, 233, 183, 0.14), transparent 28%),
    linear-gradient(180deg, #ffffff 0%, #fffdfa 100%);
}

.member-card-inner {
  position: relative;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 14px;
  padding: 16px 18px;
  background:
    radial-gradient(circle at 12% 50%, rgba(255, 236, 179, 0.34), transparent 28%),
    radial-gradient(circle at 92% 18%, rgba(255, 255, 255, 0.9), transparent 22%),
    linear-gradient(135deg, #fffaf1 0%, #ffffff 68%);
  border: 1px solid rgba(248, 227, 177, 0.9);
  border-radius: 18px;
  margin-bottom: 12px;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.88),
    0 12px 24px rgba(234, 179, 8, 0.08);
}

.member-card-inner::after {
  content: '';
  position: absolute;
  inset: 10px;
  border-radius: 14px;
  border: 1px solid rgba(255, 255, 255, 0.56);
  pointer-events: none;
}

.member-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.deployment-badge-visual {
  width: 48px;
  height: 48px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #fef3c7 0%, #fde68a 100%);
  border-radius: 18px;
  box-shadow: 0 16px 28px rgba(234, 179, 8, 0.18);
}

.member-card-icon {
  width: 30px;
  height: 30px;
  object-fit: contain;
  filter: drop-shadow(0 10px 18px rgba(234, 179, 8, 0.18));
}

.member-name-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.member-name-row strong {
  font-size: 24px;
  font-weight: 800;
  color: var(--text);
  line-height: 1.1;
}

.member-sub {
  display: block;
  margin-top: 2px;
  color: #98a2b3;
  font-size: 12px;
}

.member-actions {
  display: flex;
  gap: 10px;
}

.benefits-row {
  display: grid;
  gap: 8px;
}

.benefits-label {
  font-size: 12px;
  color: #667491;
  font-weight: 700;
}

.benefits-grid {
  display: grid;
  grid-template-columns: repeat(7, minmax(0, 1fr));
  gap: 9px;
}

.benefit-feature {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: 10px 8px 9px;
  border-radius: 15px;
  border: 1px solid rgba(229, 236, 247, 0.9);
  background:
    radial-gradient(circle at 50% 0%, rgba(255, 255, 255, 0.9), transparent 36%),
    linear-gradient(180deg, #fbfdff 0%, #f6f9ff 100%);
  box-shadow: 0 8px 18px rgba(31, 53, 94, 0.035);
  text-align: center;
}

.benefit-feature-icon {
  width: 28px;
  height: 28px;
  border-radius: 10px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: radial-gradient(circle at 30% 30%, #ffffff 0%, #e9f1ff 66%, #dce7ff 100%);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.9);
}

.benefit-feature-label {
  font-size: 11px;
  line-height: 1.25;
  color: #51627c;
  font-weight: 700;
}

.benefit-icon {
  width: 15px;
  height: 15px;
  object-fit: contain;
}

.account-panel,
.quick-panel,
.content-panel {
  background:
    radial-gradient(circle at top left, rgba(255, 255, 255, 0.96), transparent 28%),
    linear-gradient(180deg, #ffffff 0%, #fcfdff 100%);
}

.security-panel {
  padding: 20px 18px 18px;
}

.security-panel > .panel-head {
  margin-bottom: 18px;
}

.account-info-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}

.account-info-item {
  min-height: 72px;
  padding: 13px 15px;
  border-radius: 18px;
  border: 1px solid #e7eef8;
  background: linear-gradient(180deg, #fbfdff 0%, #ffffff 100%);
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  gap: 8px;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.88);
}

.account-info-label {
  font-size: 11px;
  font-weight: 700;
  color: #8b98ad;
}

.account-info-value-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.account-info-value-row strong {
  font-size: 14px;
  line-height: 1.3;
  color: #17213d;
  font-weight: 800;
  word-break: break-all;
}

.quick-grid-2col {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}

.quick-action-btn {
  min-height: 96px;
  padding: 16px;
  border: 1px solid #e8eef8;
  border-radius: 18px;
  background:
    radial-gradient(circle at top left, rgba(255, 255, 255, 0.96), transparent 34%),
    linear-gradient(180deg, #ffffff 0%, #f9fbff 100%);
  text-align: left;
  gap: 12px;
  transition: transform 0.18s ease, box-shadow 0.18s ease, border-color 0.18s ease;
}

.quick-action-btn:hover {
  transform: translateY(-2px);
  border-color: rgba(13, 107, 255, 0.16);
  box-shadow: 0 16px 30px rgba(31, 53, 94, 0.07);
}

.quick-action-btn .circle-ico {
  width: 44px;
  height: 44px;
  flex-shrink: 0;
}

.quick-card-copy {
  display: flex;
  flex-direction: column;
  justify-content: center;
  min-width: 0;
  gap: 4px;
}

.quick-icon-img {
  width: 26px;
  height: 26px;
  object-fit: contain;
  filter: drop-shadow(0 8px 14px rgba(31, 53, 94, 0.12));
}

.quick-action-btn b {
  font-size: 13px;
}

.quick-action-btn span {
  font-size: 12px;
}

.quick-action-btn b {
  display: block;
  font-size: 15px;
  font-weight: 700;
  color: var(--text);
  margin-bottom: 0;
}

.quick-action-btn span {
  font-size: 12px;
  color: #7a879e;
  line-height: 1.55;
}

.security-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
}

.security-card {
  border: 1px solid rgba(225, 235, 248, 0.96);
  border-radius: 22px;
  background:
    radial-gradient(circle at top left, rgba(255, 255, 255, 0.98), transparent 34%),
    linear-gradient(180deg, #ffffff 0%, #fbfdff 100%);
  padding: 22px 20px 20px;
  transition: transform 0.18s ease, box-shadow 0.18s ease, border-color 0.18s ease;
  box-shadow: 0 12px 28px rgba(31, 53, 94, 0.05);
}

.security-card.enhanced {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-height: 286px;
}

.security-card.enhanced:hover {
  transform: translateY(-2px);
  box-shadow: 0 1px 2px rgba(31, 53, 94, 0.04), 0 16px 32px rgba(31, 53, 94, 0.1);
  border-color: rgba(13, 107, 255, 0.25);
}

.security-card-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 2px;
}

.security-card-icon {
  width: 56px;
  height: 56px;
  border-radius: 18px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #eef5ff;
  color: var(--primary);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.92);
}

.security-card-icon.green { background: #e7f8f0; color: #16bf78; }
.security-card-icon.orange { background: #fff5e6; color: #ff9f22; }
.security-card-icon.blue { background: #eef5ff; color: var(--primary); }

.security-card b {
  display: block;
  color: var(--text);
  font-size: 18px;
  font-weight: 800;
}

.security-card > span {
  display: block;
  min-height: 50px;
  margin: 0;
  color: #667491;
  font-size: 13px;
  line-height: 1.7;
}

.security-card .badge { margin-bottom: 8px; }
.security-card .app-btn {
  width: 100%;
  min-width: 0;
  margin-top: auto;
  align-self: stretch;
  border-radius: 14px;
  font-size: 15px;
  font-weight: 800;
  box-shadow: 0 14px 24px rgba(13, 107, 255, 0.2);
}

.security-card-note {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-height: 38px;
  margin: 4px 0 6px;
  padding: 0 14px;
  border-radius: 999px;
  background: linear-gradient(90deg, rgba(238, 245, 255, 0.92), rgba(245, 249, 255, 0.78));
  color: #6a7a95;
  font-size: 11px;
  font-weight: 700;
}

.security-card-note.ok {
  background: linear-gradient(90deg, rgba(232, 251, 239, 0.98), rgba(244, 255, 248, 0.84));
  color: #0f9f63;
}

.security-card-note.warn {
  background: linear-gradient(90deg, rgba(255, 247, 234, 0.98), rgba(255, 250, 242, 0.88));
  color: #d97706;
}

.security-card-note-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: currentColor;
  flex: 0 0 auto;
  opacity: 0.9;
}

.security-level-card {
  --security-accent: #ff9f22;
  --security-accent-rgb: 255, 159, 34;
  position: relative;
  display: grid;
  grid-template-columns: 272px minmax(0, 1fr);
  align-items: center;
  gap: 18px;
  padding: 24px 26px 16px;
  border-radius: 24px;
  margin-bottom: 18px;
  border: 1px solid rgba(238, 227, 201, 0.9);
  background:
    radial-gradient(circle at 12% 58%, rgba(var(--security-accent-rgb), 0.14), transparent 22%),
    radial-gradient(circle at 82% 35%, rgba(255, 255, 255, 0.28), transparent 18%),
    linear-gradient(135deg, #fff8ea, #fffefb 70%, #ffffff);
  overflow: hidden;
  box-shadow: 0 22px 46px rgba(31, 53, 94, 0.055);
}

.security-level-card::before {
  content: '';
  position: absolute;
  inset: 12px;
  border-radius: 20px;
  border: 1px solid rgba(255, 255, 255, 0.56);
  pointer-events: none;
}

.security-level-card.high {
  --security-accent: #16bf78;
  --security-accent-rgb: 22, 191, 120;
  background:
    radial-gradient(circle at 12% 58%, rgba(var(--security-accent-rgb), 0.12), transparent 24%),
    radial-gradient(circle at 82% 35%, rgba(255, 255, 255, 0.24), transparent 18%),
    linear-gradient(135deg, #ebfaf1, #ffffff);
  border-color: #bfe7ce;
}

.security-level-card.medium {
  --security-accent: #ff9f22;
  --security-accent-rgb: 255, 159, 34;
  background:
    radial-gradient(circle at 12% 58%, rgba(var(--security-accent-rgb), 0.16), transparent 22%),
    radial-gradient(circle at 82% 35%, rgba(255, 255, 255, 0.24), transparent 18%),
    linear-gradient(135deg, #fff7e9, #ffffff);
  border-color: #fde1b3;
}

.security-level-card.low {
  --security-accent: #ff5b61;
  --security-accent-rgb: 255, 91, 97;
  background:
    radial-gradient(circle at 12% 58%, rgba(var(--security-accent-rgb), 0.12), transparent 24%),
    radial-gradient(circle at 82% 35%, rgba(255, 255, 255, 0.24), transparent 18%),
    linear-gradient(135deg, #fff2f2, #ffffff);
  border-color: #f9cbcb;
}

.security-level-main {
  position: relative;
  z-index: 1;
  min-width: 0;
  padding-right: 6px;
}

.security-level-visual {
  position: relative;
  min-height: 206px;
  display: flex;
  align-items: flex-end;
  justify-content: center;
}

.security-level-visual::after {
  content: '';
  position: absolute;
  left: 40px;
  right: 54px;
  bottom: 26px;
  height: 18px;
  border-radius: 999px;
  background: linear-gradient(90deg, rgba(255, 255, 255, 0.22), rgba(255, 255, 255, 0.64), rgba(255, 255, 255, 0.22));
  opacity: 0.88;
  filter: blur(0.4px);
}

.security-level-visual-ring {
  position: absolute;
  inset: 36px 6px 14px 2px;
  border-radius: 50%;
  background: radial-gradient(circle at center, rgba(var(--security-accent-rgb), 0.24), rgba(var(--security-accent-rgb), 0.08) 42%, transparent 72%);
  filter: blur(1px);
}

.security-level-visual-image {
  position: relative;
  width: 100%;
  height: 204px;
  object-fit: contain;
  object-position: center bottom;
  filter: sepia(0.84) saturate(1.34) hue-rotate(-18deg) brightness(1.05) drop-shadow(0 24px 28px rgba(255, 159, 34, 0.14));
}

.security-level-visual-illustration {
  position: relative;
  width: 100%;
  height: 206px;
  overflow: visible;
  z-index: 1;
}

.security-illustration-shape {
  fill: rgba(var(--security-accent-rgb), 0.08);
}

.security-illustration-shape.shape-left {
  opacity: 1;
}

.security-illustration-shape.shape-right {
  fill: rgba(var(--security-accent-rgb), 0.06);
}

.security-orbit-glow {
  fill: rgba(var(--security-accent-rgb), 0.1);
}

.security-orbit-line,
.security-orbit-dash {
  fill: none;
  stroke: rgba(var(--security-accent-rgb), 0.44);
  stroke-linecap: round;
}

.security-orbit-line {
  stroke-width: 3;
}

.security-orbit-dash {
  stroke-width: 2.5;
  stroke-dasharray: 7 8;
  opacity: 0.88;
}

.security-orbit-dot {
  fill: rgba(var(--security-accent-rgb), 0.92);
}

.security-orbit-dot.dot-top {
  fill: rgba(var(--security-accent-rgb), 0.72);
}

.security-stage-shadow {
  fill: rgba(var(--security-accent-rgb), 0.12);
}

.security-stage-plate {
  stroke: rgba(255, 255, 255, 0.92);
}

.security-stage-plate.outer {
  fill: rgba(255, 255, 255, 0.32);
  stroke-width: 5;
}

.security-stage-plate.middle {
  fill: rgba(255, 255, 255, 0.56);
  stroke-width: 4;
}

.security-stage-core {
  fill: rgba(255, 255, 255, 0.78);
  stroke: rgba(255, 255, 255, 0.94);
  stroke-width: 3;
}

.security-shield-back {
  fill: rgba(var(--security-accent-rgb), 0.22);
}

.security-shield-front {
  fill: var(--security-accent);
  filter: drop-shadow(0 16px 22px rgba(var(--security-accent-rgb), 0.16));
}

.security-shield-gloss {
  fill: rgba(255, 255, 255, 0.28);
}

.security-shield-outline {
  fill: none;
  stroke: rgba(255, 255, 255, 0.7);
  stroke-width: 3;
}

.security-shield-check {
  fill: none;
  stroke: #fff;
  stroke-width: 9;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.security-level-topline {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 20px;
}

.security-level-info {
  display: flex;
  align-items: flex-start;
  gap: 0;
  flex: 1 1 auto;
}

.security-level-icon {
  display: none;
}

.security-level-text h4 {
  margin: 0;
  font-size: 15px;
  color: #667491;
  font-weight: 700;
}

.security-level-text strong {
  display: block;
  margin: 4px 0 10px;
  font-size: 58px;
  line-height: 1;
  font-weight: 900;
  letter-spacing: -0.03em;
  color: var(--text);
}

.security-level-card.high .security-level-text strong { color: #0a8a55; }
.security-level-card.medium .security-level-text strong { color: #b45309; }
.security-level-card.low .security-level-text strong { color: #dc2626; }

.security-level-summary {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}

.security-risk-pill {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 0 12px;
  height: 30px;
  border-radius: 999px;
  background: rgba(255, 247, 234, 0.94);
  color: #c26c06;
  font-size: 12px;
  font-weight: 800;
}

.security-risk-pill::before {
  content: '!';
  width: 18px;
  height: 18px;
  border-radius: 50%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: #ff9f22;
  color: #fff;
  font-size: 11px;
  font-weight: 900;
}

.security-risk-pill.safe {
  background: rgba(232, 251, 239, 0.98);
  color: #0f9f63;
}

.security-risk-pill.safe::before {
  content: '✓';
  background: #16bf78;
}

.security-level-text p {
  margin: 0;
  max-width: 540px;
  font-size: 13px;
  color: #7a879e;
  line-height: 1.5;
}

.security-risk-pill.safe::before {
  content: '✓';
}

.security-link-hint {
  color: #0d6bff;
  font-size: 13px;
  font-weight: 700;
  white-space: nowrap;
  position: relative;
  padding-right: 14px;
}

.security-link-hint::after {
  content: '';
  position: absolute;
  top: 50%;
  right: 1px;
  width: 6px;
  height: 6px;
  border-top: 1.8px solid currentColor;
  border-right: 1.8px solid currentColor;
  transform: translateY(-50%) rotate(45deg);
}

.security-risk-pill.safe::before {
  content: '\2713';
  background: #16bf78;
}

.security-progress-wrap {
  margin-top: 14px;
}

.security-progress {
  position: relative;
  height: 7px;
  border-radius: 999px;
  background:
    linear-gradient(90deg, rgba(255, 187, 71, 0.16) 0 33.333%, rgba(255, 187, 71, 0.1) 33.333% 66.666%, rgba(255, 187, 71, 0.06) 66.666% 100%);
  overflow: hidden;
}

.security-progress::before,
.security-progress::after {
  content: '';
  position: absolute;
  top: 0;
  bottom: 0;
  width: 8px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.92);
  z-index: 1;
}

.security-progress::before {
  left: calc(33.333% - 4px);
}

.security-progress::after {
  left: calc(66.666% - 4px);
}

.security-progress-bar {
  position: relative;
  z-index: 0;
  height: 100%;
  border-radius: 999px;
  box-shadow: 0 8px 16px rgba(255, 159, 34, 0.24);
  transition: width 0.35s ease;
}

.security-level-card.high .security-progress-bar { background: linear-gradient(90deg, #16bf78, #0a8a55); }
.security-level-card.medium .security-progress-bar { background: linear-gradient(90deg, #ffb547, #ff9f22); }
.security-level-card.low .security-progress-bar { background: linear-gradient(90deg, #ff8a8a, #ff5b61); }

.security-progress-labels {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
  margin-top: 11px;
  color: #a0abc0;
  font-size: 13px;
  font-weight: 700;
  text-align: center;
}

.security-progress-labels span.active {
  color: var(--security-accent);
}

.security-meta {
  display: flex;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 10px;
  font-size: 12px;
  color: #7a879e;
}

.security-tips {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 240px;
  align-items: center;
  gap: 20px;
  margin-top: 18px;
  padding: 18px 18px 18px 18px;
  background:
    radial-gradient(circle at top left, rgba(255, 255, 255, 0.96), transparent 34%),
    linear-gradient(180deg, #fcfdff 0%, #f7faff 100%);
  border: 1px solid #e6eefa;
  border-radius: 22px;
}

.security-tips-copy h4 {
  margin: 0 0 12px;
  font-size: 17px;
  font-weight: 800;
  color: var(--text);
}

.security-tips-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}

.security-tip-card {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  min-height: 96px;
  padding: 16px 14px;
  border-radius: 16px;
  border: 1px solid rgba(225, 235, 248, 0.9);
  background: rgba(255, 255, 255, 0.9);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.84);
}

.security-tip-icon {
  width: 44px;
  height: 44px;
  border-radius: 14px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
  background: #eef5ff;
}

.security-tip-icon svg {
  width: 22px;
  height: 22px;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.security-tip-icon.blue { background: linear-gradient(145deg, #eef5ff 0%, #dbeafe 100%); color: #0d6bff; }
.security-tip-icon.mint { background: linear-gradient(145deg, #ecfbf3 0%, #d8f7e7 100%); color: #16bf78; }
.security-tip-icon.violet { background: linear-gradient(145deg, #f5ecff 0%, #e9dcff 100%); color: #8b5cf6; }
.security-tip-icon.sky { background: linear-gradient(145deg, #edf9ff 0%, #d8efff 100%); color: #4f86ff; }

.security-tip-copy {
  min-width: 0;
}

.security-tip-copy strong {
  display: block;
  color: #17213d;
  font-size: 15px;
  font-weight: 800;
}

.security-tip-copy p {
  margin: 6px 0 0;
  color: #667491;
  font-size: 12px;
  line-height: 1.65;
}

.security-tips-visual {
  position: relative;
  min-height: 154px;
}

.security-tips-visual-ring {
  position: absolute;
  inset: 20px 24px 20px 24px;
  border-radius: 50%;
  background: radial-gradient(circle at center, rgba(98, 156, 255, 0.16), rgba(98, 156, 255, 0.04) 54%, transparent 76%);
}

.security-tips-image {
  position: relative;
  width: 100%;
  height: 154px;
  object-fit: contain;
  filter: sepia(0.12) saturate(1.1) hue-rotate(-5deg) drop-shadow(0 18px 28px rgba(76, 131, 224, 0.16));
}

.security-card .app-btn {
  width: auto;
  min-width: 112px;
  margin-top: auto;
  align-self: flex-start;
  border-radius: 12px;
  font-size: 14px;
  box-shadow: 0 10px 20px rgba(13, 107, 255, 0.18);
}

.profile-side-card {
  position: sticky;
  top: 0;
  padding: 16px 12px;
  border-radius: 24px;
  border: 1px solid rgba(229, 236, 247, 0.96);
  background:
    radial-gradient(circle at top left, rgba(13, 107, 255, 0.06), transparent 34%),
    linear-gradient(180deg, #ffffff 0%, #f7faff 100%);
  box-shadow: 0 12px 34px rgba(29, 53, 87, 0.05);
}

.profile-side-nav {
  display: grid;
  grid-template-columns: 1fr;
  justify-content: stretch;
  gap: 6px;
}

.profile-side-tab {
  flex: none;
  width: 100%;
  padding: 10px 11px;
  border-radius: 16px;
}

.profile-side-tab-icon {
  width: 32px;
  height: 32px;
  border-radius: 10px;
}

.profile-side-tab-label {
  font-size: 13px;
}

.profile-side-head {
  display: block;
  margin-bottom: 8px;
}

.profile-side-head h2 {
  margin: 0;
  font-size: 26px;
  font-weight: 800;
  color: #17213d;
}

.profile-shell {
  grid-template-columns: 188px minmax(0, 1fr);
  gap: 14px;
}

.welcome-hero {
  min-height: 176px;
  padding: 20px 24px 18px;
}

.account-info-grid,
.quick-grid-2col {
  gap: 12px;
}

.account-info-item {
  min-height: 62px;
  padding: 11px 13px;
  border-radius: 16px;
}

.account-info-label {
  font-size: 10px;
}

.account-info-value-row strong {
  font-size: 13px;
}

.quick-action-btn {
  min-height: 84px;
  padding: 13px;
  border-radius: 16px;
  gap: 10px;
}

.quick-action-btn .circle-ico {
  width: 40px;
  height: 40px;
}

.quick-icon-img {
  width: 24px;
  height: 24px;
}

.quick-card-copy {
  gap: 3px;
}

.quick-action-btn b {
  font-size: 14px;
}

.quick-action-btn span {
  font-size: 11px;
  line-height: 1.45;
}

.security-level-card {
  grid-template-columns: minmax(0, 1fr);
  align-items: start;
  gap: 10px;
  padding: 14px 16px 10px;
  margin-bottom: 10px;
  box-shadow: 0 14px 30px rgba(31, 53, 94, 0.045);
}

.security-level-card::before {
  display: none;
}

.security-level-card.high,
.security-level-card.medium,
.security-level-card.low {
  background:
    radial-gradient(circle at 10% 50%, rgba(var(--security-accent-rgb), 0.1), transparent 20%),
    linear-gradient(135deg, #fff8ea 0%, #fffdf7 58%, #ffffff 100%);
}

.security-level-card.high {
  border-color: #cde8d9;
  background:
    radial-gradient(circle at 10% 50%, rgba(22, 191, 120, 0.1), transparent 20%),
    linear-gradient(135deg, #effbf4 0%, #fbfffd 58%, #ffffff 100%);
}

.security-level-card.medium {
  border-color: #f7ddb1;
}

.security-level-card.low {
  border-color: #f3c7cb;
  background:
    radial-gradient(circle at 10% 50%, rgba(255, 91, 97, 0.08), transparent 20%),
    linear-gradient(135deg, #fff4f4 0%, #fffdfd 58%, #ffffff 100%);
}

.security-level-main {
  padding-right: 0;
}

.security-level-visual {
  display: none;
}

.security-level-topline {
  gap: 12px;
}

.security-level-info {
  align-items: center;
  gap: 14px;
}

.security-level-icon {
  width: 48px;
  height: 48px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.72);
  color: var(--security-accent);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.92);
}

.security-level-text strong {
  margin: 0 0 5px;
  font-size: 30px;
}

.security-level-text p {
  max-width: none;
  font-size: 11px;
  line-height: 1.45;
}

.security-level-summary {
  margin-bottom: 8px;
}

.security-risk-pill {
  height: 24px;
  padding: 0 9px;
  font-size: 11px;
}

.security-risk-pill::before {
  width: 16px;
  height: 16px;
  font-size: 10px;
}

.security-link-hint {
  font-size: 11px;
}

.security-progress-wrap {
  margin-top: 10px;
}

.security-progress {
  height: 6px;
  background: #edf1f6;
}

.security-progress-bar {
  box-shadow: none;
}

.security-progress-labels {
  margin-top: 8px;
  font-size: 12px;
}

.security-meta {
  margin-top: 8px;
  font-size: 11px;
}

.security-grid {
  gap: 14px;
}

.security-card {
  padding: 16px 16px 14px;
  border-radius: 20px;
  box-shadow: 0 10px 24px rgba(31, 53, 94, 0.04);
}

.security-card.enhanced {
  min-height: 230px;
  gap: 8px;
}

.security-card-icon {
  width: 50px;
  height: 50px;
  border-radius: 16px;
}

.security-card b {
  font-size: 16px;
}

.security-card > span {
  min-height: 42px;
  font-size: 12px;
  line-height: 1.6;
}

.security-card-note {
  min-height: auto;
  margin: 2px 0 4px;
  padding: 0;
  border-radius: 0;
  background: transparent;
  font-size: 11px;
}

.security-card-note.ok,
.security-card-note.warn {
  background: transparent;
}

.security-card-note-dot {
  width: 6px;
  height: 6px;
}

.security-tips {
  display: block;
  margin-top: 14px;
  padding: 16px 18px 14px;
  background:
    radial-gradient(circle at top left, rgba(255, 255, 255, 0.96), transparent 30%),
    linear-gradient(180deg, #f8fbff 0%, #f3f7ff 100%);
  border: 1px solid #e3ebf8;
}

.security-tips-copy h4 {
  margin: 0 0 10px;
  font-size: 16px;
}

.security-bullet-list {
  margin: 0;
  padding-left: 18px;
  display: grid;
  gap: 10px;
  color: #5e6d88;
  font-size: 13px;
  line-height: 1.65;
}

.security-bullet-list li::marker {
  color: #4f86ff;
}

.security-risk-pill.safe::before {
  content: '\2713';
  background: #16bf78;
}

.table-wrap {
  max-height: none;
  overflow-y: visible;
  padding: 0 16px;
}

.base-table th {
  padding: 13px 8px 10px;
  font-size: 11px;
}

.base-table td {
  padding: 11px 8px;
}

.remark-cell,
.time-cell,
.mono,
.pos,
.neg {
  font-size: 11px;
}

.pagination {
  gap: 10px;
}

.form-panel {
  max-width: 880px;
}

.profile-form {
  display: grid;
  gap: 16px;
  max-width: 460px;
}

.profile-form label {
  display: grid;
  gap: 8px;
  color: #34425d;
  font-size: 14px;
  font-weight: 700;
}

.profile-form label span { font-size: 13px; }

.profile-form input {
  width: 100%;
  box-sizing: border-box;
  height: 42px;
  border: 1px solid var(--line);
  background: #fff;
  border-radius: 10px;
  padding: 0 14px;
  outline: none;
  font-size: 14px;
  color: #44536f;
}

.profile-form input:focus {
  border-color: var(--primary);
  box-shadow: 0 0 0 4px rgba(13, 107, 255, 0.08);
}

.code-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 130px;
  gap: 12px;
  align-items: end;
}

.code-label { margin-bottom: 0 !important; }

.submit-btn {
  height: 42px;
  font-size: 14px;
}

.table-wrap {
  max-height: 650px;
  overflow-x: auto;
  overflow-y: auto;
  padding: 0 18px 0;
  scrollbar-gutter: stable;
}

.base-table {
  font-size: 13px;
  border-collapse: separate;
  border-spacing: 0;
}

.base-table th {
  font-weight: 800;
  padding: 18px 12px 13px;
  font-size: 12px;
  white-space: nowrap;
  color: #7f8ba1;
  border-bottom: 1px solid rgba(232, 238, 247, 0.9);
}

.base-table td {
  padding: 16px 12px;
  border-bottom: 1px solid rgba(240, 244, 251, 0.96);
  vertical-align: middle;
}

.time-cell { white-space: nowrap; color: #7b879d; font-size: 12px; }
.mono { font-family: 'SF Mono', 'JetBrains Mono', 'Cascadia Code', monospace; font-size: 12px; font-weight: 700; color: #475569; }

.pos { color: #059669; font-weight: 800; font-family: 'SF Mono', monospace; font-size: 13px; white-space: nowrap; }
.neg { color: #dc2626; font-weight: 800; font-family: 'SF Mono', monospace; font-size: 13px; white-space: nowrap; }

.remark-cell {
  max-width: 240px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: #5d6c86;
  font-size: 12px;
}

.pagination {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: 12px;
  padding: 0;
  font-size: 12px;
  color: #7b879d;
}

.page-size {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: #5d6c86;
  font-size: 12px;
  font-weight: 700;
}

.page-size select {
  height: 30px;
  border: 1px solid rgba(126, 143, 179, 0.22);
  border-radius: 7px;
  padding: 0 8px;
  background: #fff;
  color: var(--text);
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
  outline: none;
}

.page-btn {
  height: 36px;
  border: 1px solid rgba(126, 143, 179, 0.22);
  border-radius: 12px;
  padding: 0 12px;
  background: #fff;
  color: var(--text);
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
}

.page-btn.icon {
  width: 36px;
  padding: 0;
  justify-content: center;
}

.page-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.page-btn:not(:disabled):hover { border-color: var(--primary); color: var(--primary); background: #f5f8ff; }

.page-jump {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: #5d6c86;
  font-size: 12px;
  font-weight: 700;
}

.page-jump.compact {
  gap: 8px;
}

.page-jump input {
  width: 52px;
  height: 36px;
  box-sizing: border-box;
  border: 1px solid rgba(126, 143, 179, 0.22);
  border-radius: 12px;
  padding: 0 8px;
  background: #fff;
  color: var(--text);
  font-size: 12px;
  font-weight: 700;
  text-align: center;
  outline: none;
  -moz-appearance: textfield;
}

.page-jump input::-webkit-outer-spin-button,
.page-jump input::-webkit-inner-spin-button {
  -webkit-appearance: none;
  margin: 0;
}

.page-jump input:focus { border-color: var(--primary); box-shadow: 0 0 0 3px rgba(13, 107, 255, 0.1); }

.total-count { color: #5d6c86; font-size: 12px; font-weight: 700; }

.page-number {
  min-width: 36px;
  height: 36px;
  border: 1px solid rgba(126, 143, 179, 0.18);
  border-radius: 12px;
  background: #fff;
  color: #586780;
  font-size: 12px;
  font-weight: 800;
  cursor: pointer;
}

.page-number.active {
  background: linear-gradient(180deg, #1976ff 0%, #0d6bff 100%);
  border-color: #0d6bff;
  color: #fff;
  box-shadow: 0 12px 20px rgba(13, 107, 255, 0.2);
}

.page-ellipsis {
  color: #94a3b8;
  font-weight: 800;
  padding: 0 2px;
}

@media (max-width: 1280px) {
  .welcome-content {
    max-width: calc(100% - 340px);
  }

  .benefits-grid {
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }
}

@media (max-width: 1200px) {
  .profile-shell {
    grid-template-columns: minmax(0, 1fr);
  }

  .profile-side-card {
    position: static;
  }

  .profile-side-nav {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .profile-side-tab {
    flex: 1 1 calc(33.333% - 7px);
  }

  .two-col-grid,
  .overview-bottom-grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .profile-stats,
  .security-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .benefits-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .security-level-topline {
    flex-direction: column;
    align-items: flex-start;
  }
}

@media (max-width: 900px) {
  .welcome-content {
    max-width: none;
  }

  .welcome-visual {
    display: none;
  }

  .security-level-info {
    align-items: flex-start;
  }

  .welcome-hero {
    padding: 16px;
  }

  .welcome-text h2 {
    font-size: 22px;
  }

  .welcome-text p {
    font-size: 13px;
  }

  .welcome-avatar {
    width: 56px;
    height: 56px;
  }

  .profile-stats {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .stat-info strong {
    font-size: 22px;
  }

  .stat-card {
    padding: 12px;
    min-height: 0;
  }

  .security-grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .account-info-grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .quick-grid-2col {
    grid-template-columns: minmax(0, 1fr);
  }

  .benefits-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .panel-head {
    flex-direction: column;
    align-items: flex-start;
    gap: 8px;
  }

  .profile-overview .member-panel,
  .profile-overview .account-panel,
  .profile-overview .quick-panel {
    padding: 14px;
  }

  .member-card-inner {
    padding: 12px 14px;
    flex-direction: column;
    align-items: flex-start;
  }

  .member-name-row strong {
    font-size: 18px;
  }

  .security-level-card {
    padding: 12px 14px 10px;
  }

  .security-tips {
    grid-template-columns: minmax(0, 1fr);
    padding: 14px;
  }

  .security-card.enhanced {
    min-height: 0;
  }

  .table-wrap {
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
    padding: 0 12px;
  }

  .pagination {
    justify-content: flex-start;
  }

  .profile-shell > *,
  .two-col-grid > *,
  .overview-bottom-grid > *,
  .security-grid > *,
  .account-info-grid > *,
  .quick-grid-2col > *,
  .security-tips > * {
    min-width: 0;
  }
}

@media (max-width: 768px) {
  .profile-stats,
  .quick-grid-2col,
  .account-info-grid,
  .security-grid,
  .benefits-grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .profile-side-head {
    display: none;
  }

  .profile-side-card {
    padding: 10px;
  }

  .profile-side-tab {
    flex: 1 1 100%;
    width: 100%;
  }

  .welcome-hero {
    padding: 24px 20px;
  }

  .welcome-content {
    flex-direction: column;
    align-items: flex-start;
    gap: 18px;
  }

  .welcome-text h2 {
    font-size: 34px;
  }

  .member-card-inner {
    flex-direction: column;
    align-items: flex-start;
  }

  .benefit-feature {
    flex-direction: row;
    justify-content: flex-start;
    text-align: left;
    padding: 12px 14px;
  }

  .member-actions,
  .member-actions .app-btn {
    width: 100%;
  }

  .form-panel {
    max-width: none;
  }

  .profile-form {
    max-width: none;
  }

  .code-row {
    grid-template-columns: minmax(0, 1fr);
  }

  .pagination {
    justify-content: flex-start;
  }

  .security-level-info,
  .security-meta {
    flex-direction: column;
    align-items: flex-start;
  }

  .security-level-topline {
    flex-direction: column;
  }

  .table-wrap {
    max-height: none;
  }

  .profile-stats > *,
  .benefits-grid > *,
  .code-row > * {
    min-width: 0;
  }
}
</style>
