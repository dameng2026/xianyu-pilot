<template>
  <CardPanel class="update-checker" title="版本更新检查" desc="一键检查 GitHub 最新版本，复制脚本在宿主机执行">
    <div class="update-header">
      <div class="update-current">
        <span class="update-label">当前版本</span>
        <b class="update-current-version">v{{ APP_VERSION }}</b>
        <span v-if="buildDateText" class="update-build-date">构建于 {{ buildDateText }}</span>
      </div>
      <button
        type="button"
        class="update-check-btn"
        :disabled="state === 'loading'"
        @click="checkUpdate"
      >
        <Icon name="refresh" />
        <span>{{ state === 'loading' ? '正在检查...' : '检查更新' }}</span>
      </button>
    </div>

    <!-- 检查结果：已是最新 -->
    <div v-if="state === 'latest'" class="update-status update-status-latest">
      <Icon name="success" />
      <span>当前已是最新版本 v{{ APP_VERSION }}</span>
    </div>

    <!-- 检查结果：有更新 -->
    <div v-if="state === 'available'" class="update-available">
      <div class="update-version-row">
        <span class="update-label">最新版本</span>
        <b class="update-latest-version">v{{ info.latestVersion }}</b>
        <span v-if="info.publishedAt" class="update-published">发布于 {{ formatDate(info.publishedAt) }}</span>
        <Badge type="blue">有新版本</Badge>
      </div>

      <details v-if="info.releaseNotes" class="update-notes">
        <summary>更新说明</summary>
        <pre>{{ info.releaseNotes }}</pre>
      </details>

      <div class="update-deployment">
        <span class="update-label">部署方式</span>
        <div class="deployment-tabs">
          <button
            v-for="opt in deploymentOptions"
            :key="opt.value"
            type="button"
            :class="['deployment-tab', { active: selectedMode === opt.value }]"
            @click="selectedMode = opt.value"
          >
            {{ opt.label }}
          </button>
        </div>
        <span class="deployment-hint">{{ deploymentHint }}</span>
      </div>

      <div v-if="selectedMode === 'docker' && info.mirrors.length > 1" class="update-mirrors">
        <span class="update-label">镜像源</span>
        <div class="mirror-tabs">
          <button
            v-for="mirror in info.mirrors"
            :key="mirror.registry"
            type="button"
            :class="['mirror-tab', { active: selectedMirror === mirror.registry }]"
            @click="selectMirror(mirror)"
          >
            {{ mirror.name }}
          </button>
        </div>
      </div>

      <div class="update-script-block">
        <div class="script-header">
          <span class="script-title">执行脚本（在项目根目录运行）</span>
          <button type="button" class="script-copy-btn" @click="onCopyScript">
            <Icon name="copy" /><span>{{ copied ? '已复制' : '复制' }}</span>
          </button>
        </div>
        <pre class="script-content">{{ currentScript }}</pre>
        <p class="script-tip">提示：{{ scriptTip }}</p>
      </div>

      <div class="update-offline">
        <span class="update-label">网络受限？</span>
        <a :href="info.offlineBackup.releaseUrl" target="_blank" rel="noopener noreferrer">查看 GitHub Release 页</a>
        <span class="update-divider">|</span>
        <a :href="info.offlineBackup.tarballUrl" target="_blank" rel="noopener noreferrer">下载源码 tar.gz</a>
      </div>

      <div class="update-actions">
        <button type="button" class="update-action-btn primary" @click="onExecuted">
          我已执行完成，刷新页面
        </button>
      </div>
    </div>

    <!-- 检查结果：失败 -->
    <div v-if="state === 'failed'" class="update-status update-status-failed">
      <Icon name="warning" />
      <div class="failed-body">
        <b>无法检查更新</b>
        <p>{{ errorMessage }}</p>
        <a v-if="githubReleaseFallbackUrl" :href="githubReleaseFallbackUrl" target="_blank" rel="noopener noreferrer">
          直接访问 GitHub Releases 页 →
        </a>
      </div>
    </div>

    <!-- 执行完成提示 -->
    <div v-if="state === 'executed'" class="update-status update-status-latest">
      <Icon name="success" />
      <span>正在刷新页面以加载新版本...</span>
    </div>
  </CardPanel>
</template>

<script setup>
import { computed, ref } from 'vue'
import CardPanel from './CardPanel.vue'
import Badge from './Badge.vue'
import Icon from './Icon.vue'
import { APP_BUILD_DATE, APP_VERSION, formatBuildDate } from '../utils/appMeta.js'
import { copyText } from '../utils/clipboard.js'
import { getUpdateInfo, reportUpdateFeedback } from '../api/system.js'

const buildDateText = formatBuildDate(APP_BUILD_DATE)

// State: 'idle' | 'loading' | 'latest' | 'available' | 'failed' | 'executed'
const state = ref('idle')
const info = ref({
  latestVersion: '',
  publishedAt: '',
  releaseNotes: '',
  deploymentMode: 'unknown',
  updateScript: '',
  updateScriptWindows: '',
  mirrors: [],
  offlineBackup: { tarballUrl: '', releaseUrl: '' },
  githubApiSource: '',
  cachedAt: '',
})
const errorMessage = ref('')
const selectedMode = ref('docker')
const selectedMirror = ref('')
const copied = ref(false)

const deploymentOptions = [
  { label: 'Docker 镜像', value: 'docker' },
  { label: '源码构建', value: 'source' },
]

const deploymentHint = computed(() => {
  if (selectedMode.value === 'docker') return '通过 docker compose 拉取镜像并重启容器，数据通过命名卷保留不丢失。'
  if (selectedMode.value === 'source') return '通过 git pull 拉取源码并重新构建启动，需保证本地无未提交改动。'
  return ''
})

const isWindows = computed(() => /windows/i.test(navigator.userAgent))

const currentScript = computed(() => {
  if (selectedMode.value === 'source') {
    return isWindows.value ? info.value.updateScriptWindows : info.value.updateScript
  }
  // Docker mode: re-generate based on selected mirror
  return buildDockerScript(selectedMirror.value)
})

function buildDockerScript(mirrorRegistry) {
  const ghcrDefault = 'ghcr.io/xianyu-assistant-opensource'
  if (!mirrorRegistry || mirrorRegistry === ghcrDefault) {
    return 'docker compose pull && docker compose up -d'
  }
  const apiImage = `${mirrorRegistry}/xianyu-assistant-api:latest`
  const webImage = `${mirrorRegistry}/xianyu-assistant-web:latest`
  const crawlerImage = `${mirrorRegistry}/xianyu-assistant-crawler:latest`
  if (isWindows.value) {
    return `$env:API_IMAGE='${apiImage}'\n$env:WEB_IMAGE='${webImage}'\n$env:CRAWLER_IMAGE='${crawlerImage}'\ndocker compose pull && docker compose up -d`
  }
  return `export API_IMAGE=${apiImage}\nexport WEB_IMAGE=${webImage}\nexport CRAWLER_IMAGE=${crawlerImage}\ndocker compose pull && docker compose up -d`
}

const scriptTip = computed(() => {
  if (selectedMode.value === 'source') return '执行前请确认本地没有未提交的改动，否则 git pull 会失败。'
  if (selectedMirror.value && selectedMirror.value !== 'ghcr.io/xianyu-assistant-opensource') {
    return '使用阿里云 ACR 时，首次需先 docker login <acr_url> 完成登录。'
  }
  return '执行前请确认已在项目根目录（含 docker-compose.yml 的目录）。'
})

const githubReleaseFallbackUrl = computed(() => {
  if (info.value.offlineBackup.releaseUrl) return info.value.offlineBackup.releaseUrl
  return 'https://github.com/dameng2026/XianYuPilo/releases'
})

function formatDate(value) {
  if (!value) return ''
  try {
    const date = new Date(value)
    if (Number.isNaN(date.getTime())) return value
    return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
  } catch {
    return value
  }
}

function toast(message) {
  window.dispatchEvent(new CustomEvent('xya-toast', { detail: { message } }))
}

async function checkUpdate() {
  state.value = 'loading'
  errorMessage.value = ''
  try {
    const res = await getUpdateInfo()
    const data = res?.data || {}
    info.value = {
      latestVersion: data.latestVersion || '',
      publishedAt: data.publishedAt || '',
      releaseNotes: data.releaseNotes || '',
      deploymentMode: data.deploymentMode || 'unknown',
      updateScript: data.updateScript || '',
      updateScriptWindows: data.updateScriptWindows || '',
      mirrors: Array.isArray(data.mirrors) ? data.mirrors : [],
      offlineBackup: data.offlineBackup || { tarballUrl: '', releaseUrl: '' },
      githubApiSource: data.githubApiSource || '',
      cachedAt: data.cachedAt || '',
    }
    // Initialize selection from deployment mode
    if (info.value.deploymentMode === 'docker' || info.value.deploymentMode === 'source') {
      selectedMode.value = info.value.deploymentMode
    } else {
      selectedMode.value = 'docker'
    }
    // Initialize mirror to GHCR default
    selectedMirror.value = info.value.mirrors[0]?.registry || 'ghcr.io/xianyu-assistant-opensource'

    if (data.githubApiSource === 'unavailable') {
      state.value = 'failed'
      errorMessage.value = 'GitHub API 暂时不可用，可能是网络限制或 GitHub 限流。请稍后重试，或直接访问 GitHub Release 页手动下载。'
      return
    }
    if (data.hasUpdate) {
      state.value = 'available'
    } else {
      state.value = 'latest'
    }
  } catch (err) {
    state.value = 'failed'
    errorMessage.value = err?.message || '检查更新失败，请稍后重试。'
  }
}

function selectMirror(mirror) {
  selectedMirror.value = mirror.registry
  copied.value = false
}

async function onCopyScript() {
  const ok = await copyText(currentScript.value)
  copied.value = ok
  toast(ok ? '已复制到剪贴板，请在项目根目录执行' : '复制失败，请手动选中脚本复制')
}

async function onExecuted() {
  try {
    await reportUpdateFeedback({
      success: true,
      fromVersion: APP_VERSION,
      toVersion: info.value.latestVersion,
      deploymentMode: selectedMode.value,
    })
  } catch {
    // feedback failure should not block reload
  }
  state.value = 'executed'
  toast('更新已记录，正在刷新页面...')
  setTimeout(() => window.location.reload(), 800)
}
</script>

<style scoped>
.update-checker { margin-top: 16px; }
.update-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}
.update-current { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.update-label { font-size: 12px; color: #7a879e; font-weight: 600; }
.update-current-version { font-size: 20px; font-weight: 900; color: #13213d; }
.update-build-date { font-size: 11px; color: #99a4b4; }
.update-check-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 8px 16px;
  border-radius: 10px;
  border: 1px solid #bcd2ff;
  background: linear-gradient(180deg, #f6faff, #eaf2ff);
  color: #2563eb;
  font-weight: 700;
  cursor: pointer;
  transition: transform 0.2s, box-shadow 0.2s, border-color 0.2s;
}
.update-check-btn:hover:not(:disabled) {
  transform: translateY(-1px);
  box-shadow: 0 8px 18px rgba(37, 99, 235, 0.15);
  border-color: #2563eb;
}
.update-check-btn:disabled { opacity: 0.6; cursor: not-allowed; }
.update-check-btn .ui-icon, .update-check-btn .ui-icon-img { width: 16px; height: 16px; }
.update-status {
  margin-top: 14px;
  padding: 14px 16px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 13px;
}
.update-status-latest {
  background: #ecfdf3;
  color: #16a34a;
  border: 1px solid #abefc6;
}
.update-status-failed {
  background: #fff7ed;
  color: #c2410c;
  border: 1px solid #fed7aa;
  align-items: flex-start;
}
.update-status-failed .failed-body { display: flex; flex-direction: column; gap: 4px; }
.update-status-failed .failed-body p { margin: 0; font-size: 12px; }
.update-status-failed .failed-body a { color: #c2410c; text-decoration: underline; font-size: 12px; }
.update-status .ui-icon, .update-status .ui-icon-img { width: 18px; height: 18px; flex-shrink: 0; }

.update-available { margin-top: 14px; display: flex; flex-direction: column; gap: 14px; }
.update-version-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  padding: 12px 14px;
  background: #f6faff;
  border: 1px solid #bcd2ff;
  border-radius: 10px;
}
.update-latest-version { font-size: 16px; font-weight: 900; color: #2563eb; }
.update-published { font-size: 11px; color: #99a4b4; }

.update-notes {
  padding: 12px 14px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
}
.update-notes summary {
  cursor: pointer;
  font-size: 13px;
  color: #475569;
  font-weight: 600;
}
.update-notes pre {
  margin: 8px 0 0;
  padding: 0;
  font-family: inherit;
  font-size: 12px;
  line-height: 1.7;
  color: #334155;
  white-space: pre-wrap;
  word-break: break-word;
}

.update-deployment {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.deployment-tabs, .mirror-tabs {
  display: inline-flex;
  gap: 4px;
  padding: 4px;
  background: #eef2f7;
  border-radius: 10px;
}
.deployment-tab, .mirror-tab {
  padding: 6px 14px;
  border: 0;
  background: transparent;
  border-radius: 7px;
  font-size: 12px;
  font-weight: 700;
  color: #64748b;
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
}
.deployment-tab.active, .mirror-tab.active {
  background: #fff;
  color: #2563eb;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08);
}
.deployment-hint {
  font-size: 11px;
  color: #94a3b8;
  flex-basis: 100%;
}

.update-mirrors {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.update-script-block {
  background: #0f172a;
  border-radius: 12px;
  padding: 14px 16px;
  color: #e2e8f0;
}
.script-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}
.script-title { font-size: 11px; color: #94a3b8; font-weight: 600; }
.script-copy-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 10px;
  border: 1px solid #334155;
  background: #1e293b;
  color: #93c5fd;
  border-radius: 6px;
  font-size: 11px;
  font-weight: 700;
  cursor: pointer;
  transition: background 0.15s;
}
.script-copy-btn:hover { background: #334155; }
.script-copy-btn .ui-icon, .script-copy-btn .ui-icon-img { width: 12px; height: 12px; }
.script-content {
  margin: 0;
  font-family: 'SFMono-Regular', Consolas, monospace;
  font-size: 12px;
  line-height: 1.7;
  color: #e2e8f0;
  white-space: pre-wrap;
  word-break: break-all;
}
.script-tip {
  margin: 8px 0 0;
  font-size: 11px;
  color: #94a3b8;
}

.update-offline {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  padding: 10px 12px;
  background: #fffbeb;
  border: 1px solid #fde68a;
  border-radius: 8px;
  font-size: 12px;
  color: #92400e;
}
.update-offline a { color: #b45309; text-decoration: underline; }
.update-divider { color: #fbbf24; }

.update-actions {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}
.update-action-btn {
  padding: 10px 20px;
  border-radius: 10px;
  border: 0;
  cursor: pointer;
  font-size: 13px;
  font-weight: 700;
  transition: transform 0.15s, box-shadow 0.15s;
}
.update-action-btn.primary {
  background: linear-gradient(180deg, #2563eb, #1d4ed8);
  color: #fff;
  box-shadow: 0 6px 18px rgba(37, 99, 235, 0.25);
}
.update-action-btn.primary:hover {
  transform: translateY(-1px);
  box-shadow: 0 10px 24px rgba(37, 99, 235, 0.35);
}

@media (max-width: 720px) {
  .update-header { flex-direction: column; align-items: flex-start; }
  .script-content { font-size: 11px; }
}
</style>
