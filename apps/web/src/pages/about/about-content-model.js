import {
  APP_BUILD_DATE,
  APP_VERSION,
  formatBuildDate,
} from '../../utils/appMeta.js'

const DEFAULT_SUPPORT_EMAIL = ''
const DEFAULT_FEEDBACK_EMAIL = ''
const DEFAULT_BD_EMAIL = ''

function buildDateText() {
  return formatBuildDate(APP_BUILD_DATE)
}

function resolveSourceList(source, fallback) {
  return Array.isArray(source) && source.length ? source : fallback
}

const NON_AD_PAYMENT_MARKERS = /赞助|捐赠|打赏|sponsor|donat(?:e|ion)|tip\s*jar/i

function isFakeLocalEmail(value) {
  const text = String(value || '').trim().toLowerCase()
  return text.includes('@') && text.split('@').at(-1)?.endsWith('.local')
}

function isNonAdPaymentCard(item) {
  return NON_AD_PAYMENT_MARKERS.test([
    item?.label,
    item?.title,
    item?.desc,
    item?.placeholderText,
    item?.actionText,
    item?.actionValue,
  ].map(value => String(value || '')).join(' '))
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
  const releaseDate = buildDateText()
  return {
    heroTitle: 'XianYuAssistant 开源版',
    heroBadgeText: '付费广告商业桥',
    heroDescription: '开源单管理员版本提供本地核心能力；只有付费广告申请、支付与展示会连接商业版后端。',
    serviceStatusText: '本地核心能力；付费广告桥按需配置',
    logs: [
      {
        v: `v${APP_VERSION}`,
        t: releaseDate,
        tone: 'major',
        d: '当前版本已完成固定账号密码登录、系统配置整合与付费广告商业桥边界；未配置的广告能力会明确显示为不可用。',
        sections: [
          { t: '登录与账号', d: '保留固定管理员账号密码登录模式，扫码登录仅用于闲鱼店铺授权，避免误连到外部旧环境。' },
          { t: '系统配置', d: '通用模型、向量模型、RAG 知识库配置已统一收敛到系统配置页。' },
          { t: '广告商业桥', d: '开源版只通过服务端桥接接口处理付费广告申请、支付与展示，不暴露商业版数据库，也不让前端持有 bridge token。' },
        ],
        tags: ['固定账号登录', '系统配置整合', '付费广告桥', '广告合作'],
      },
    ],
    supports: [
      {
        label: '反馈建议',
        desc: '提交内容仅保存在当前部署；是否有维护人员处理取决于部署方。',
        icon: 'aboutSupportFeedback',
        tone: 'violet',
        actionType: 'route',
        actionValue: 'feedback',
        actionMessage: '正在前往反馈建议...',
      },
      {
        label: '广告合作',
        desc: '仅在真实付费广告桥接通后查看套餐、提交申请并支付；未配置时入口会明确禁用操作。',
        icon: 'aboutSupportWeb',
        tone: 'blue',
        actionType: 'route',
        actionValue: 'ad-application',
        actionMessage: '正在前往广告合作...',
      },
      {
        label: '系统配置',
        desc: '统一管理通用模型、向量模型、RAG 知识库配置。',
        icon: 'aboutSupportDoc',
        tone: 'green',
        actionType: 'route',
        actionValue: 'settings-system',
        actionMessage: '正在前往系统配置...',
      },
      {
        label: '商务联系',
        desc: '当前默认部署未配置商务联系人，请由部署方补充真实入口。',
        icon: 'aboutSupportChat',
        tone: 'orange',
        actionType: 'toast',
        actionValue: '部署方尚未配置商务联系方式。',
      },
    ],
    communityCards: [
      {
        label: '交流群',
        title: '微信群二维码',
        desc: '用于当前部署自行维护的版本通知、使用答疑、投放交流与功能建议收集。',
        placeholderText: 'GROUP',
        hint: '配置后可扫码',
        tone: 'blue',
        actionType: 'toast',
        actionText: '配置后可扫码',
        actionValue: '当前部署尚未配置交流二维码，请联系部署管理员。',
      },
      {
        label: 'QQ群',
        title: 'QQ群聊二维码',
        desc: '用于版本通知、使用答疑与功能建议收集，方便习惯使用 QQ 的用户加入。',
        placeholderText: 'QQ',
        hint: '配置后可扫码',
        tone: 'violet',
        actionType: 'toast',
        actionText: '配置后可扫码',
        actionValue: '当前部署尚未配置QQ群聊二维码，请联系部署管理员。',
      },
      {
        label: '微信客服',
        title: '微信客服二维码',
        desc: '用于一对一咨询、技术支持与商务合作，扫码后可添加客服微信。',
        placeholderText: 'KEFU',
        hint: '配置后可扫码',
        tone: 'green',
        actionType: 'toast',
        actionText: '配置后可扫码',
        actionValue: '当前部署尚未配置微信客服二维码，请联系部署管理员。',
      },
      {
        label: '联系方式',
        title: '商务合作联系方式',
        desc: '部署方配置真实且有人值守的联系方式后才会在这里展示。',
        value: '',
        hint: '当前未配置',
        tone: 'green',
        actionType: 'toast',
        actionText: '不可用',
        actionValue: '部署方尚未配置商务联系方式。',
      },
    ],
    links: [
      { label: '用户协议', icon: 'aboutShield', actionText: '查看', actionType: 'legal', actionValue: 'terms' },
      { label: '隐私政策', icon: 'aboutEye', actionText: '查看', actionType: 'legal', actionValue: 'privacy' },
      { label: '版本说明', icon: 'refresh', actionText: '查看', actionType: 'toast', actionValue: `当前版本 v${APP_VERSION} 已包含固定账号登录、系统配置整合与付费广告商业桥。` },
      { label: '导出诊断日志', icon: 'download', actionText: '导出', actionType: 'download', actionValue: 'diagnostics' },
    ],
    legalDocs: {
      termsUrl: '',
      privacyUrl: '',
      supportEmail: DEFAULT_SUPPORT_EMAIL,
      feedbackEmail: DEFAULT_FEEDBACK_EMAIL,
      businessEmail: DEFAULT_BD_EMAIL,
    },
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
