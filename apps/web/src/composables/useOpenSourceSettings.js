import { reactive, ref } from 'vue'
import {
  getOpenSourceConfig,
  getRuntimeStatus,
  runtimeConfig,
  saveOpenSourceConfig,
} from '../api/system.js'
import { unwrap } from '../utils/apiData.js'

export function createDefaultOpenSourceConfig() {
  return {
    siteName: '',
    icp: '',
    logoUrl: '',
    crawlerBaseUrl: '',
    generalModel: {
      provider: '',
      modelName: '',
      baseUrl: '',
      apiKey: '',
      apiKeyConfigured: false,
      requestTimeout: 15,
      polishKeywords: '',
      polishForbiddenKeywords: '',
    },
    embeddingModel: {
      provider: '',
      modelName: '',
      baseUrl: '',
      apiKey: '',
      apiKeyConfigured: false,
    },
  }
}

export function createDefaultRuntimeMeta() {
  return {
    appEnv: '',
    docsEnabled: false,
    openSource: true,
    authMode: '',
  }
}

export function createDefaultRuntimeStatus() {
  return {
    dbConnected: false,
    dbVersion: '',
    redisConnected: false,
    redisMemory: '',
    redisMode: '',
    crawlerBaseUrl: '',
    commercialBridgeConfigured: false,
    commercialBridgeConnected: false,
    commercialBridgeMode: 'local-fallback',
    commercialBridgeHealthOk: false,
    commercialAdminHealthOk: false,
    commercialUserHealthOk: false,
    commercialBridgeSiteCode: '',
    commercialBridgeMessage: '',
    commercialMutationIdempotencyEnabled: false,
    commercialPaymentIdempotencyEnabled: false,
    commercialPaidAdPlacementEnforced: false,
    commercialFrontendUrl: '',
    generalModelConfigured: false,
    embeddingModelConfigured: false,
  }
}

export function normalizeOpenSourceConfig(config = {}) {
  const defaults = createDefaultOpenSourceConfig()
  const generalModel = config?.generalModel || {}
  const embeddingModel = config?.embeddingModel || {}
  return {
    siteName: String(config?.siteName || defaults.siteName),
    icp: String(config?.icp || defaults.icp),
    logoUrl: String(config?.logoUrl || defaults.logoUrl),
    crawlerBaseUrl: String(config?.crawlerBaseUrl || defaults.crawlerBaseUrl),
    generalModel: {
      provider: String(generalModel.provider || defaults.generalModel.provider),
      // realModel is accepted here only to migrate older saved settings. New
      // configuration has one canonical model name.
      modelName: String(generalModel.modelName || generalModel.realModel || defaults.generalModel.modelName),
      baseUrl: String(generalModel.baseUrl || defaults.generalModel.baseUrl),
      apiKey: String(generalModel.apiKey || defaults.generalModel.apiKey),
      apiKeyConfigured: Boolean(generalModel.apiKeyConfigured || generalModel.apiKey),
      requestTimeout: Number(generalModel.requestTimeout || defaults.generalModel.requestTimeout) || 15,
      polishKeywords: String(generalModel.polishKeywords || defaults.generalModel.polishKeywords),
      polishForbiddenKeywords: String(
        generalModel.polishForbiddenKeywords || defaults.generalModel.polishForbiddenKeywords
      ),
    },
    embeddingModel: {
      provider: String(embeddingModel.provider || defaults.embeddingModel.provider),
      modelName: String(embeddingModel.modelName || defaults.embeddingModel.modelName),
      baseUrl: String(embeddingModel.baseUrl || defaults.embeddingModel.baseUrl),
      apiKey: String(embeddingModel.apiKey || defaults.embeddingModel.apiKey),
      apiKeyConfigured: Boolean(embeddingModel.apiKeyConfigured || embeddingModel.apiKey),
    },
  }
}

export function cloneOpenSourceConfig(config = {}) {
  return JSON.parse(JSON.stringify(normalizeOpenSourceConfig(config)))
}

export function syncOpenSourceConfig(target, source = {}) {
  const normalized = normalizeOpenSourceConfig(source)
  target.siteName = normalized.siteName
  target.icp = normalized.icp
  target.logoUrl = normalized.logoUrl
  target.crawlerBaseUrl = normalized.crawlerBaseUrl
  Object.assign(target.generalModel, normalized.generalModel)
  Object.assign(target.embeddingModel, normalized.embeddingModel)
}

export function useOpenSourceSettings() {
  const loading = ref(false)
  const saving = ref(false)
  const error = ref('')
  const success = ref('')
  const configAvailable = ref(false)
  const runtimeMetaAvailable = ref(false)
  const runtimeStatusAvailable = ref(false)
  const config = reactive(createDefaultOpenSourceConfig())
  const runtimeMeta = reactive(createDefaultRuntimeMeta())
  const runtimeStatus = reactive(createDefaultRuntimeStatus())

  function clearSuccess() {
    success.value = ''
  }

  async function refreshRuntimeStatus() {
    try {
      const status = unwrap(await getRuntimeStatus())
      Object.assign(runtimeStatus, createDefaultRuntimeStatus(), status)
      runtimeStatusAvailable.value = true
      return runtimeStatus
    } catch (e) {
      runtimeStatusAvailable.value = false
      error.value = e?.message || '运行状态暂不可用'
      return null
    }
  }

  async function loadBundle({ includeRuntimeStatus = false } = {}) {
    loading.value = true
    error.value = ''
    const tasks = [runtimeConfig(), getOpenSourceConfig()]
    if (includeRuntimeStatus) tasks.push(getRuntimeStatus())
    const results = await Promise.allSettled(tasks)
    const failures = []
    if (results[0].status === 'fulfilled') {
      Object.assign(runtimeMeta, createDefaultRuntimeMeta(), unwrap(results[0].value))
      runtimeMetaAvailable.value = true
    } else {
      runtimeMetaAvailable.value = false
      failures.push('运行元数据')
    }
    if (results[1].status === 'fulfilled') {
      syncOpenSourceConfig(config, unwrap(results[1].value))
      configAvailable.value = true
    } else {
      configAvailable.value = false
      failures.push('系统配置')
    }
    if (includeRuntimeStatus) {
      if (results[2].status === 'fulfilled') {
        Object.assign(runtimeStatus, createDefaultRuntimeStatus(), unwrap(results[2].value))
        runtimeStatusAvailable.value = true
      } else {
        runtimeStatusAvailable.value = false
        failures.push('运行状态')
      }
    }
    if (failures.length) error.value = `${failures.join('、')}暂不可用；未知状态不会显示为未配置。`
    loading.value = false
    return {
      runtimeMeta,
      config,
      runtimeStatus,
      configAvailable: configAvailable.value,
      runtimeMetaAvailable: runtimeMetaAvailable.value,
      runtimeStatusAvailable: runtimeStatusAvailable.value,
    }
  }

  async function saveConfig(payload, { successMessage = 'Settings saved' } = {}) {
    if (!configAvailable.value) {
      error.value = '尚未成功读取现有配置，为避免覆盖未知值，保存操作已禁用。'
      return null
    }
    saving.value = true
    error.value = ''
    try {
      const saved = normalizeOpenSourceConfig(unwrap(await saveOpenSourceConfig(payload)))
      syncOpenSourceConfig(config, saved)
      configAvailable.value = true
      success.value = successMessage
      setTimeout(() => {
        if (success.value === successMessage) success.value = ''
      }, 3000)
      return saved
    } catch (e) {
      error.value = e?.message || 'Failed to save open-source settings'
      return null
    } finally {
      saving.value = false
    }
  }

  return {
    loading,
    saving,
    error,
    success,
    configAvailable,
    runtimeMetaAvailable,
    runtimeStatusAvailable,
    config,
    runtimeMeta,
    runtimeStatus,
    clearSuccess,
    loadBundle,
    refreshRuntimeStatus,
    saveConfig,
  }
}
