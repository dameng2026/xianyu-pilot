<template>
  <div class="about-page">
    <CardPanel class="about-shell">
      <div class="about-content">
        <section class="hero-card">
          <div class="hero-visual">
            <img :src="heroImage" alt="" aria-hidden="true" />
          </div>
          <div class="hero-brand">
            <div class="brand-mark">
              <span></span>
              <span></span>
            </div>
            <div class="hero-text">
              <div class="hero-title-row">
                <h1>{{ aboutContent.heroTitle }}</h1>
                <Badge>v{{ APP_VERSION }}</Badge>
                <Badge type="blue">{{ aboutContent.heroBadgeText }}</Badge>
              </div>
              <p>{{ aboutContent.heroDescription }}</p>
              <div class="hero-meta">
                <span class="hero-meta-item"><i class="dot dot-green"></i>{{ aboutContent.serviceStatusText }}</span>
                <span class="hero-meta-divider"></span>
                <span class="hero-meta-item">构建于 Vue 3 + Vite</span>
                <span class="hero-meta-divider"></span>
                <span class="hero-meta-item">{{ releaseLabel }}</span>
              </div>
            </div>
          </div>
        </section>

        <div class="metric-row">
          <div class="metric-tile metric-tile-blue">
            <div class="metric-icon">
              <Icon name="aboutVersion" />
            </div>
            <div class="metric-info">
              <span class="metric-label">当前版本</span>
              <b class="metric-value">v{{ APP_VERSION }}</b>
            </div>
          </div>
          <div class="metric-tile metric-tile-green">
            <div class="metric-icon">
              <Icon name="aboutStatus" />
            </div>
            <div class="metric-info">
              <span class="metric-label">服务状态</span>
              <b class="metric-value metric-value-green">{{ aboutContent.serviceStatusText }}</b>
            </div>
          </div>
          <div class="metric-tile metric-tile-purple">
            <div class="metric-icon">
              <Icon name="aboutUpdate" />
            </div>
            <div class="metric-info">
              <span class="metric-label">最后更新</span>
              <b class="metric-value">{{ buildDateText }}</b>
            </div>
          </div>
        </div>

        <UpdateChecker />

        <div class="main-grid">
          <CardPanel title="更新日志" desc="版本迭代与功能演进记录">
            <div class="changelog">
              <div
                v-for="(log, idx) in logs"
                :key="log.v"
                :class="['log-item', `log-${log.tone || 'minor'}`]"
              >
                <div class="log-rail">
                  <span class="log-dot"></span>
                  <span v-if="idx < logs.length - 1" class="log-line"></span>
                </div>
                <div class="log-body">
                  <div class="log-head">
                    <span class="log-ver">{{ log.v }}</span>
                    <span :class="['log-type', `log-type-${log.tone || 'minor'}`]">
                      {{ typeMeta[log.tone || 'minor'].label }}
                    </span>
                    <span class="log-date">{{ log.t }}</span>
                  </div>
                  <p v-if="log.d" class="log-desc">{{ log.d }}</p>
                  <div v-for="section in log.sections" :key="section.t" class="log-change-group">
                    <span class="log-change-label">{{ section.t }}</span>
                    <p v-if="section.d" class="log-change-summary">{{ section.d }}</p>
                    <ul v-if="section.items && section.items.length" class="log-change-list">
                      <li v-for="(entry, entryIdx) in section.items" :key="entryIdx">
                        <b v-if="entry.t" class="log-item-title">{{ entry.t }}</b><span v-if="entry.t" class="log-item-sep">：</span><span class="log-item-desc">{{ entry.d }}</span>
                      </li>
                    </ul>
                  </div>
                  <div v-if="log.tags && log.tags.length" class="log-tags">
                    <span v-for="tag in log.tags" :key="tag" class="log-tag">{{ tag }}</span>
                  </div>
                </div>
              </div>
            </div>
          </CardPanel>

          <CardPanel v-if="sponsorCard" title="赞助支持" desc="支持项目持续维护与迭代" style="margin-top: 16px">
            <div class="sponsor-block">
              <p class="sponsor-headline">{{ sponsorHeadline }}</p>
              <div
                class="sponsor-banner"
                :class="{ placeholder: !sponsorCard.imageUrl }"
                @click="sponsorCard.action && sponsorCard.action()"
              >
                <img v-if="sponsorCard.imageUrl" :src="sponsorCard.imageUrl" :alt="sponsorCard.title || '赞助二维码'" />
                <span v-else class="sponsor-banner-text">{{ sponsorCard.placeholderText || '待后台配置赞助二维码' }}</span>
              </div>
            </div>
          </CardPanel>

          <div class="side-stack">
            <CardPanel title="服务支持" desc="可用入口与响应能力以当前部署方实际配置为准">
              <div class="support-grid">
                <button v-for="support in supports" :key="support.label" class="support-card" type="button" @click="onSupport(support)">
                  <span class="support-icon" :class="support.tone">
                    <Icon :name="support.icon" />
                  </span>
                  <div class="support-text">
                    <b>{{ support.label }}</b>
                    <p>{{ support.desc }}</p>
                  </div>
                </button>
              </div>
            </CardPanel>

            <CardPanel title="交流群与支持" desc="扫码进入交流群、查看联系方式或支持项目持续更新" style="margin-top: 16px">
              <div class="community-grid">
                <button
                  v-for="card in communityCards"
                  :key="card.title"
                  type="button"
                  class="community-card"
                  :class="[card.tone, { 'has-media': card.imageUrl || card.placeholderText }]"
                  @click="card.action"
                >
                  <div class="community-card-body">
                    <div class="community-head">
                      <span class="community-label">{{ card.label }}</span>
                      <b>{{ card.title }}</b>
                    </div>
                    <p class="community-desc">{{ card.desc }}</p>
                    <strong v-if="card.value" class="community-value">{{ card.value }}</strong>
                    <div class="community-foot">
                      <span>{{ card.hint }}</span>
                      <em v-if="card.actionText">{{ card.actionText }} ›</em>
                    </div>
                  </div>
                  <div
                    v-if="card.imageUrl || card.placeholderText"
                    :class="['community-media', { placeholder: !card.imageUrl }]"
                  >
                    <img v-if="card.imageUrl" :src="card.imageUrl" :alt="card.imageAlt || card.title" />
                    <span v-else>{{ card.placeholderText }}</span>
                  </div>
                </button>
              </div>
            </CardPanel>

            <CardPanel title="相关链接" desc="协议、隐私与系统工具" style="margin-top: 16px">
              <div class="link-list">
                <button v-for="link in links" :key="link.label" class="link-row" type="button" @click="link.action">
                  <span class="link-label">
                    <Icon :name="link.icon" />
                    {{ link.label }}
                  </span>
                  <span class="link-action">{{ link.actionText }} <span class="link-arrow">›</span></span>
                </button>
              </div>
            </CardPanel>
          </div>
        </div>
      </div>
    </CardPanel>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { openExternalUrl } from '../../utils/externalUrl.js'
import { showLegalNotice } from '../../utils/legalNotice.js'
import CardPanel from '../../components/CardPanel.vue'
import Badge from '../../components/Badge.vue'
import Icon from '../../components/Icon.vue'
import UpdateChecker from '../../components/UpdateChecker.vue'
import { getAboutContent } from '../../api/system.js'
import {
  createDefaultAboutContent,
  getAboutCommunityCards,
  getAboutLinks,
  getAboutLogs,
  getAboutSupports,
  mergeAboutContent,
} from '../about/about-content-model.js'
import { APP_BUILD_DATE, APP_VERSION, formatBuildDate, formatReleaseLabel } from '../../utils/appMeta.js'

const heroImage = '/xya/illustrations/about-hero.svg'
const buildDateText = formatBuildDate(APP_BUILD_DATE)
const releaseLabel = formatReleaseLabel(APP_BUILD_DATE)

const typeMeta = {
  major: { label: '大版本' },
  minor: { label: '功能更新' },
  patch: { label: '问题修复' },
}

defineProps({ active: String })

const aboutContent = ref(createDefaultAboutContent())

const logs = computed(() => {
  return getAboutLogs(aboutContent.value)
})

const supports = computed(() => {
  return getAboutSupports(aboutContent.value).map(item => ({
    ...item,
    action: () => handleAction(item)
  }))
})

const communityCards = computed(() => {
  return getAboutCommunityCards(aboutContent.value)
    .filter(item => item.placeholderText !== 'SPONSOR')
    .map(item => ({
      ...item,
      action: () => handleAction(item)
    }))
})

const sponsorCard = computed(() => {
  return getAboutCommunityCards(aboutContent.value).find(c => c.placeholderText === 'SPONSOR') || null
})

const sponsorHeadline = computed(() => {
  if (!sponsorCard.value) return ''
  const desc = (sponsorCard.value.desc || '').trim()
  if (desc) return desc
  return '如果大梦正在做的事情帮助到了你，并且你也愿意的话，非常希望您能赞助我的工作'
})

const links = computed(() => {
  return getAboutLinks(aboutContent.value).map(item => ({
    ...item,
    action: () => handleAction(item)
  }))
})

onMounted(loadRemoteAboutContent)

onMounted(() => {
  window.addEventListener('xya-header-action', onHeaderAction)
})

onBeforeUnmount(() => {
  window.removeEventListener('xya-header-action', onHeaderAction)
})

async function loadRemoteAboutContent() {
  try {
    const response = await getAboutContent()
    aboutContent.value = mergeAboutContent(response.data || {})
  } catch {
    if (import.meta.env.DEV) console.warn('Failed to load about content, using fallback.')
  }
}

function onHeaderAction(event) {
  if (event.detail === 'settings-reload') loadRemoteAboutContent()
}

function onSupport(item) {
  item.action?.()
}

function toast(message) {
  window.dispatchEvent(new CustomEvent('xya-toast', { detail: { message } }))
}

function openExternal(url, message) {
  if (!url) return
  if (!openExternalUrl(url)) {
    toast('链接无效或使用了不安全的协议')
    return false
  }
  if (message) toast(message)
  return true
}

function openMail(address) {
  if (!address) return
  toast(`正在打开邮件客户端：${address}`)
  location.href = `mailto:${address}`
}

function copyText(text, successMessage = '已复制到剪贴板') {
  const value = String(text || '').trim()
  if (!value) {
    toast('当前未配置可复制的内容')
    return
  }

  const fallbackCopy = () => {
    const textarea = document.createElement('textarea')
    textarea.value = value
    textarea.setAttribute('readonly', 'readonly')
    textarea.style.position = 'fixed'
    textarea.style.left = '-9999px'
    document.body.appendChild(textarea)
    textarea.select()
    textarea.setSelectionRange(0, textarea.value.length)
    const ok = document.execCommand('copy')
    textarea.remove()
    return ok
  }

  if (navigator.clipboard?.writeText) {
    navigator.clipboard
      .writeText(value)
      .then(() => {
        toast(successMessage)
      })
      .catch(() => {
        if (fallbackCopy()) {
          toast(successMessage)
          return
        }
        toast(`复制失败，请手动复制：${value}`)
      })
    return
  }

  if (fallbackCopy()) {
    toast(successMessage)
    return
  }

  toast(`复制失败，请手动复制：${value}`)
}

function navigateInternal(routeKey, message = '') {
  const normalized = String(routeKey || '')
    .trim()
    .replace(/^#\/?/, '')
    .replace(/^\/#\/?/, '')
  if (!normalized) return
  if (message) toast(message)
  location.hash = `#/${normalized}`
}

function handleAction(action = {}) {
  switch (String(action.actionType || '').toLowerCase()) {
    case 'external':
      openExternal(action.actionValue, action.actionMessage || `正在打开${action.label || '链接'}...`)
      return
    case 'route':
      navigateInternal(action.actionValue, action.actionMessage || '')
      return
    case 'mailto':
      openMail(action.actionValue)
      return
    case 'copy':
      copyText(
        action.actionValue || action.value,
        action.actionMessage || `已复制${action.label || '内容'}`
      )
      return
    case 'legal':
      openLegalDoc(action.actionValue, action.label || '相关协议')
      return
    case 'download':
      exportDiagnostics()
      return
    case 'toast':
      toast(action.actionValue || `当前已是最新版本 v${APP_VERSION}`)
      return
    default:
      if (action.actionValue) openExternal(action.actionValue, action.actionMessage || '')
  }
}

function openLegalDoc(type, title) {
  const legalDocs = aboutContent.value.legalDocs || {}
  const externalUrl = type === 'privacy' ? legalDocs.privacyUrl : legalDocs.termsUrl
  if (externalUrl) {
    if (openExternal(externalUrl, `正在打开${title}...`)) return
  }
  showLegalNotice(title)
}

function exportDiagnostics() {
  const payload = {
    version: APP_VERSION,
    buildDate: APP_BUILD_DATE,
    route: location.hash || location.pathname,
    userAgent: navigator.userAgent,
    exportedAt: new Date().toISOString()
  }
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `xya-diagnostics-${Date.now()}.json`
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
  toast('诊断日志已导出')
}
</script>

<style scoped>
.about-page { width: 100%; }
.about-shell {
  padding: 0;
  overflow: hidden;
  background:
    radial-gradient(circle at 0% 0%, rgba(37, 99, 235, 0.08), transparent 36%),
    radial-gradient(circle at 100% 0%, rgba(139, 92, 246, 0.08), transparent 34%),
    rgba(255, 255, 255, 0.98);
}
.about-content { padding: 18px; }
.hero-card {
  position: relative;
  overflow: hidden;
  border-radius: 22px;
  min-height: 164px;
  padding: 24px 28px 22px;
  background: linear-gradient(90deg, rgba(241, 246, 255, 0.98), rgba(246, 239, 255, 0.88));
  border: 1px solid rgba(220, 232, 248, 0.95);
  box-shadow: 0 18px 42px rgba(31, 53, 94, 0.08);
}
.hero-card::after {
  content: '';
  position: absolute;
  inset: 0;
  background: linear-gradient(135deg, rgba(255, 255, 255, 0.08), transparent 45%);
  pointer-events: none;
}
.hero-visual {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  padding: 0 12px 0 0;
  pointer-events: none;
  z-index: 0;
}
.hero-visual img {
  width: min(100%, 1180px);
  height: auto;
  display: block;
  object-fit: contain;
  object-position: center right;
  opacity: 0.95;
}
.hero-brand {
  position: relative;
  display: flex;
  align-items: center;
  gap: 18px;
  z-index: 1;
  max-width: 560px;
}
.brand-mark { width: 76px; height: 76px; position: relative; flex-shrink: 0; }
.brand-mark span {
  position: absolute;
  left: 31px;
  top: 0;
  width: 22px;
  height: 76px;
  border-radius: 14px;
  background: linear-gradient(180deg, #0d7fff, #16b7ff);
  transform: rotate(42deg);
  box-shadow: 0 8px 22px rgba(13, 107, 255, 0.32);
}
.brand-mark span + span { transform: rotate(-42deg); background: linear-gradient(180deg, #25a5ff, #0362f4); }
.hero-text { min-width: 0; }
.hero-title-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.hero-title-row h1 { margin: 0; font-size: 24px; line-height: 1.15; font-weight: 900; color: #13213d; }
.hero-text p { margin: 8px 0 0; font-size: 13px; color: #65748b; }
.hero-meta { display: flex; align-items: center; gap: 10px; margin-top: 12px; flex-wrap: wrap; }
.hero-meta-item { display: inline-flex; align-items: center; gap: 6px; font-size: 11px; color: #7a879e; font-weight: 600; }
.hero-meta-divider { width: 1px; height: 10px; background: #d8e0ec; }
.dot { width: 7px; height: 7px; border-radius: 50%; display: inline-block; }
.dot-green { background: #22c55e; box-shadow: 0 0 0 3px rgba(34, 197, 94, 0.18); }
.metric-row { margin-top: 16px; display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; }
.metric-tile {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 16px 18px;
  border-radius: 18px;
  background: rgba(255, 255, 255, 0.98);
  border: 1px solid rgba(231, 237, 247, 0.95);
  box-shadow: 0 10px 26px rgba(31, 53, 94, 0.06);
}
.metric-icon {
  width: 40px;
  height: 40px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
}
.metric-tile-blue .metric-icon { background: #eef4ff; color: #2563eb; }
.metric-tile-green .metric-icon { background: #ecfdf3; color: #16a34a; }
.metric-tile-purple .metric-icon { background: #f4efff; color: #8b5cf6; }
.metric-icon :deep(.ui-icon), .metric-icon :deep(.ui-icon-img) { width: 20px; height: 20px; }
.metric-info { display: flex; flex-direction: column; gap: 2px; }
.metric-label { font-size: 11px; color: #7a879e; font-weight: 600; }
.metric-value { font-size: 20px; font-weight: 900; color: #13213d; }
.metric-value-green { color: #16a34a; }
.main-grid { margin-top: 16px; display: grid; grid-template-columns: minmax(0, 1fr) 330px; gap: 16px; align-items: start; }
.side-stack { min-width: 0; }
.changelog { display: flex; flex-direction: column; gap: 4px; }
.log-item { display: flex; gap: 14px; padding: 8px 0; }
.log-rail { display: flex; flex-direction: column; align-items: center; flex-shrink: 0; padding-top: 4px; }
.log-dot { width: 12px; height: 12px; border-radius: 50%; background: #2563eb; box-shadow: 0 0 0 2px rgba(37, 99, 235, 0.18); }
.log-major .log-dot { background: #ea580c; box-shadow: 0 0 0 2px rgba(234, 88, 12, 0.18); }
.log-minor .log-dot { background: #2563eb; box-shadow: 0 0 0 2px rgba(37, 99, 235, 0.18); }
.log-patch .log-dot { background: #16a34a; box-shadow: 0 0 0 2px rgba(22, 163, 74, 0.18); }
.log-line { flex: 1; width: 2px; background: #e2e8f3; margin-top: 4px; min-height: 18px; }
.log-body { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 8px; }
.log-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.log-ver { font-size: 12px; font-weight: 800; padding: 3px 10px; border-radius: 999px; background: #eef4ff; color: #2563eb; }
.log-major .log-ver { background: #fff0e6; color: #ea580c; }
.log-minor .log-ver { background: #eef4ff; color: #2563eb; }
.log-patch .log-ver { background: #ecfdf3; color: #16a34a; }
.log-type { font-size: 11px; font-weight: 700; padding: 2px 8px; border-radius: 6px; }
.log-type-major { background: #fff0e6; color: #ea580c; border: 1px solid #ffd9b8; }
.log-type-minor { background: #eef4ff; color: #2563eb; border: 1px solid #cfdefc; }
.log-type-patch { background: #ecfdf3; color: #16a34a; border: 1px solid #c2f0d2; }
.log-date { font-size: 11px; color: #99a4b4; font-weight: 600; }
.log-desc { margin: 0; font-size: 13px; color: #3a4a63; line-height: 1.72; }
.log-change-group { display: flex; flex-direction: column; gap: 6px; margin-top: 2px; }
.log-change-label {
  align-self: flex-start;
  font-size: 11px;
  font-weight: 700;
  color: #2563eb;
  padding: 2px 8px;
  border-radius: 5px;
  background: #eef4ff;
}
.log-change-summary { margin: 0; font-size: 13px; color: #3a4a63; line-height: 1.72; }
.log-change-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.log-change-list li {
  position: relative;
  padding-left: 14px;
  font-size: 13px;
  line-height: 1.72;
  color: #3a4a63;
}
.log-change-list li::before {
  content: '';
  position: absolute;
  left: 2px;
  top: 10px;
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: #94a3b8;
}
.log-item-title { color: #13213d; font-weight: 700; }
.log-item-sep { color: #6b7a90; margin: 0 2px; }
.log-item-desc { color: #4a5a73; }
.log-tags { display: flex; gap: 6px; flex-wrap: wrap; }
.log-tag {
  font-size: 10px;
  padding: 3px 8px;
  border-radius: 999px;
  background: #f3f7ff;
  color: #6b7a90;
  border: 1px solid #e2eaf5;
}
.support-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.support-card {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px;
  border: 1px solid rgba(231, 237, 247, 0.95);
  border-radius: 16px;
  background: #fff;
  cursor: pointer;
  text-align: left;
  transition: transform 0.2s ease, box-shadow 0.2s ease, border-color 0.2s ease;
}
.support-card:hover {
  transform: translateY(-2px);
  border-color: #bcd2ff;
  box-shadow: 0 8px 20px rgba(37, 99, 235, 0.1);
}
.support-icon {
  width: 38px;
  height: 38px;
  border-radius: 12px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
}
.support-icon.blue { background: linear-gradient(135deg, #e8f0ff, #d6e6ff); color: #2563eb; }
.support-icon.green { background: linear-gradient(135deg, #e6f7ee, #d2f1e2); color: #16a34a; }
.support-icon.orange { background: linear-gradient(135deg, #fff1e0, #ffe5c2); color: #ea8a00; }
.support-icon.violet { background: linear-gradient(135deg, #f1e8ff, #e6d6ff); color: #7c3aed; }
.support-icon :deep(.ui-icon), .support-icon :deep(.ui-icon-img) { width: 18px; height: 18px; }
.support-text { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.support-text b { font-size: 13px; color: #13213d; }
.support-text p { margin: 0; font-size: 11px; color: #7a879e; line-height: 1.45; }
.community-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 14px;
}
.community-card {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 18px;
  border-radius: 18px;
  border: 1px solid #e3ebf6;
  background: linear-gradient(180deg, #ffffff, #f9fbff);
  text-align: left;
  cursor: pointer;
  transition: transform 0.2s ease, box-shadow 0.2s ease, border-color 0.2s ease;
}
.community-card.has-media {
  flex-direction: row;
  align-items: center;
}
.community-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 12px 24px rgba(36, 82, 158, 0.1);
}
.community-card.blue:hover { border-color: #bfd5ff; }
.community-card.orange:hover { border-color: #ffd9ac; }
.community-card.green:hover { border-color: #bee8d0; }
.community-card-body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.community-head {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.community-label {
  display: inline-flex;
  width: fit-content;
  min-height: 22px;
  padding: 0 9px;
  border-radius: 999px;
  background: #eef4ff;
  color: #2b66d9;
  font-size: 10px;
  font-weight: 800;
  align-items: center;
}
.community-card.orange .community-label {
  background: #fff3e3;
  color: #cc7600;
}
.community-card.green .community-label {
  background: #ebf8f0;
  color: #14814e;
}
.community-head b {
  color: #13213d;
  font-size: 14px;
}
.community-desc {
  margin: 0;
  color: #667892;
  line-height: 1.65;
  font-size: 12px;
}
.community-media {
  flex-shrink: 0;
  width: 120px;
  height: 120px;
  border-radius: 14px;
  overflow: hidden;
  border: 1px solid #e2eaf5;
  background: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
}
.community-media img {
  width: 100%;
  height: 100%;
  object-fit: contain;
  display: block;
  padding: 6px;
}
.community-media.placeholder {
  background:
    linear-gradient(135deg, rgba(37, 99, 235, 0.06), rgba(37, 99, 235, 0)),
    linear-gradient(90deg, rgba(17, 24, 39, 0.08) 50%, transparent 50%),
    linear-gradient(rgba(17, 24, 39, 0.08) 50%, transparent 50%);
  background-size: auto, 12px 12px, 12px 12px;
  background-position: center;
}
.community-media.placeholder span {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 76px;
  height: 76px;
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.92);
  color: #334155;
  font-size: 12px;
  font-weight: 900;
  letter-spacing: 1px;
}
.community-value {
  color: #14213d;
  font-size: 14px;
  line-height: 1.5;
  word-break: break-all;
}
.community-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  color: #7b889d;
  font-size: 11px;
}
.community-foot em {
  font-style: normal;
  color: #2b66d9;
  font-weight: 700;
  white-space: nowrap;
}
.link-list { display: flex; flex-direction: column; }
.link-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 8px;
  border: 0;
  border-bottom: 1px solid #eef2f8;
  background: transparent;
  cursor: pointer;
  text-align: left;
  transition: background 0.2s ease;
}
.link-row:last-child { border-bottom: 0; }
.link-row:hover { background: #f6faff; }
.link-label {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  font-size: 13px;
  color: #13213d;
  font-weight: 600;
}
.link-label :deep(.ui-icon), .link-label :deep(.ui-icon-img) { width: 18px; height: 18px; }
.link-action { display: inline-flex; align-items: center; gap: 4px; font-size: 12px; color: #6b7a90; white-space: nowrap; }
.link-arrow { font-size: 16px; color: #b3bccd; }
.sponsor-block {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 20px;
}
.sponsor-headline {
  margin: 0;
  padding: 0 16px;
  font-size: 14px;
  line-height: 1.85;
  color: #4a5a73;
  font-weight: 500;
  text-align: center;
  letter-spacing: 0.2px;
}
.sponsor-banner {
  width: 100%;
  max-width: 420px;
  aspect-ratio: 2 / 1;
  border-radius: 16px;
  overflow: hidden;
  border: 1px solid #ffe5c2;
  background: linear-gradient(180deg, #ffffff, #fff8ef);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: transform 0.2s ease, box-shadow 0.2s ease, border-color 0.2s ease;
}
.sponsor-banner:hover {
  transform: translateY(-2px);
  box-shadow: 0 12px 28px rgba(204, 118, 0, 0.14);
  border-color: #ffd9ac;
}
.sponsor-banner img {
  width: 100%;
  height: 100%;
  object-fit: contain;
  display: block;
}
.sponsor-banner.placeholder {
  background:
    linear-gradient(135deg, rgba(204, 118, 0, 0.06), rgba(204, 118, 0, 0)),
    linear-gradient(90deg, rgba(17, 24, 39, 0.06) 50%, transparent 50%),
    linear-gradient(rgba(17, 24, 39, 0.06) 50%, transparent 50%);
  background-size: auto, 14px 14px, 14px 14px;
  background-position: center;
}
.sponsor-banner-text {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 16px 24px;
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.92);
  color: #b3bccd;
  font-size: 13px;
  font-weight: 600;
}
@media (max-width: 1260px) {
  .main-grid { grid-template-columns: minmax(0, 1fr); }
  .support-grid { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); }
}
@media (max-width: 920px) {
  .metric-row,
  .support-grid { grid-template-columns: minmax(0, 1fr); }
  .community-media { width: 104px; height: 104px; }
  .hero-card { min-height: 0; }
  .hero-visual { opacity: 0.32; }
  .hero-brand { max-width: none; align-items: flex-start; }
  .main-grid > *, .metric-row > *, .support-grid > * { min-width: 0; }
}

@media (max-width: 900px) {
  .about-content {
    padding: 12px;
  }

  .hero-card {
    padding: 14px;
  }

  .hero-title-row h1 {
    font-size: 20px;
  }

  .hero-brand {
    gap: 12px;
  }

  .brand-mark {
    width: 56px;
    height: 56px;
  }

  .brand-mark span {
    left: 22px;
    height: 56px;
    width: 16px;
  }

  .metric-tile {
    padding: 12px 14px;
  }

  .metric-value {
    font-size: 18px;
  }

  .community-card {
    padding: 14px;
  }

  .community-card.has-media {
    flex-direction: column;
    align-items: stretch;
  }

  .community-media {
    width: 100%;
    height: 140px;
    align-self: center;
  }

  .sponsor-banner {
    aspect-ratio: 2 / 1;
    max-width: 100%;
  }

  .link-row {
    padding: 10px 6px;
  }
}
</style>
