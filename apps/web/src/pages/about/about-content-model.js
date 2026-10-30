import {
  APP_BUILD_DATE,
  APP_VERSION,
  formatBuildDate,
} from '../../utils/appMeta.js'


function buildDateText() {
  return formatBuildDate(APP_BUILD_DATE)
}

function resolveSourceList(source, fallback) {
  return Array.isArray(source) && source.length ? source : fallback
}


function isFakeLocalEmail(value) {
  const text = String(value || '').trim().toLowerCase()
  return text.includes('@') && text.split('@').at(-1)?.endsWith('.local')
}

function sanitizeCommunityCards(cards) {
  return (Array.isArray(cards) ? cards : []).filter(item => item)
}

function sanitizeLegalDocs(legalDocs) {
  const result = { ...(legalDocs || {}) }
  for (const key of ['supportEmail', 'feedbackEmail', 'businessEmail']) {
    if (isFakeLocalEmail(result[key])) result[key] = ''
  }
  return result
}

export function createDefaultAboutContent() {
  return {
    heroTitle: '闲鱼助手 · 个人版',
    heroBadgeText: '个人使用',
    heroDescription: '集中管理账号、商品、消息、订单、自动发货与卡密。AI 与 QQ/MCP 按需配置。',
    serviceStatusText: '个人工作区',
    logs: [],
    supports: [{ label: '系统配置', desc: '管理模型和知识库配置。', icon: 'aboutSupportDoc',
      tone: 'green', actionType: 'route', actionValue: 'settings-system' }],
    communityCards: [],
    links: [{ label: '导出诊断日志', icon: 'download', actionText: '导出',
      actionType: 'download', actionValue: 'diagnostics' }],
    legalDocs: { termsUrl: '', privacyUrl: '', supportEmail: '', feedbackEmail: '', businessEmail: '' },
  }
}

export function mergeAboutContent(payload = {}) {
  const defaults = createDefaultAboutContent()
  const communityCards = sanitizeCommunityCards(
    Array.isArray(payload.communityCards) ? payload.communityCards : defaults.communityCards
  )
  return {
    ...defaults,
    ...payload,
    heroTitle: payload.heroTitle || defaults.heroTitle,
    heroBadgeText: payload.heroBadgeText || defaults.heroBadgeText,
    heroDescription: payload.heroDescription || defaults.heroDescription,
    serviceStatusText: payload.serviceStatusText || defaults.serviceStatusText,
    logs: Array.isArray(payload.logs) ? payload.logs : defaults.logs,
    supports: Array.isArray(payload.supports) ? payload.supports : defaults.supports,
    communityCards,
    links: Array.isArray(payload.links) ? payload.links : defaults.links,
    legalDocs: sanitizeLegalDocs({
      ...defaults.legalDocs,
      ...(payload.legalDocs || {}),
    }),
  }
}

export function getAboutLogs(content = {}) {
  const fallbackLogs = createDefaultAboutContent().logs
  const source = resolveSourceList(content.logs, fallbackLogs)
  return source.map((item, index) => ({
    v: item?.v || `v${APP_VERSION}`,
    t: item?.t || buildDateText(),
    tone: item?.tone || (index === 0 ? 'major' : 'minor'),
    d: item?.d || '',
    sections: Array.isArray(item?.sections) ? item.sections.map(section => ({
      t: section?.t || '',
      d: section?.d || '',
      items: Array.isArray(section?.items) ? section.items.map(entry => ({
        t: entry?.t || '',
        d: entry?.d || '',
      })) : [],
    })) : [],
    tags: Array.isArray(item?.tags) ? item.tags : [],
  }))
}

export function getAboutSupports(content = {}) {
  const fallbackSupports = createDefaultAboutContent().supports
  const source = resolveSourceList(content.supports, fallbackSupports)
  return source.map(item => {
    const fakeMailto = item?.actionType === 'mailto' && isFakeLocalEmail(item?.actionValue)
    return {
      label: item?.label || '支持入口',
      desc: item?.desc || '',
      icon: item?.icon || 'aboutSupportDoc',
      tone: item?.tone || 'blue',
      actionType: fakeMailto ? 'toast' : (item?.actionType || ''),
      actionValue: fakeMailto ? '部署方尚未配置真实联系方式。' : (item?.actionValue || ''),
      actionMessage: item?.actionMessage || '',
    }
  })
}

export function getAboutCommunityCards(content = {}) {
  const fallbackCards = createDefaultAboutContent().communityCards
  const source = sanitizeCommunityCards(resolveSourceList(content.communityCards, fallbackCards))
  return source.map(item => ({
    label: item?.label || '支持',
    title: item?.title || '社区卡片',
    desc: item?.desc || '',
    imageUrl: item?.imageUrl || '',
    imageAlt: item?.imageAlt || item?.title || 'community',
    placeholderText: item?.placeholderText || '',
    value: item?.value || '',
    hint: item?.hint || '',
    tone: item?.tone || 'blue',
    actionType: item?.actionType || '',
    actionText: item?.actionText || '',
    actionValue: item?.actionValue || '',
    actionMessage: item?.actionMessage || '',
  }))
}

export function getAboutLinks(content = {}) {
  const fallbackLinks = createDefaultAboutContent().links
  const source = resolveSourceList(content.links, fallbackLinks)
  return source.map(item => ({
    label: item?.label || '相关链接',
    icon: item?.icon || 'aboutEye',
    actionText: item?.actionText || '查看',
    actionType: item?.actionType || '',
    actionValue: item?.actionValue || '',
    actionMessage: item?.actionMessage || '',
  }))
}
