<template>
  <CardPanel class="update-checker" title="版本更新检查" desc="检查 GitHub 发布版本，下载本机更新包">
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

      <p class="script-tip">下载匹配的本机部署包，停止程序并备份数据后，按包内说明更新。</p>

      <div class="update-offline">
        <span class="update-label">网络受限？</span>
        <a :href="info.offlineBackup.releaseUrl" target="_blank" rel="noopener noreferrer">查看 GitHub Release 页</a>
        <span class="update-divider">|</span>
        <a :href="info.offlineBackup.tarballUrl" target="_blank" rel="noopener noreferrer">下载源码 tar.gz</a>
      </div>

      <div class="update-actions">
        <button type="button" class="update-action-btn primary" @click="onExecuted">
          更新完成后刷新页面
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
import { getUpdateInfo, reportUpdateFeedback } from '../api/system.js'

const buildDateText = formatBuildDate(APP_BUILD_DATE)

// State: 'idle' | 'loading' | 'latest' | 'available' | 'failed' | 'executed'
const state = ref('idle')
const info = ref({
  latestVersion: '',
  publishedAt: '',
  releaseNotes: '',
  deploymentMode: 'unknown',
  offlineBackup: { tarballUrl: '', releaseUrl: '' },
  githubApiSource: '',
  cachedAt: '',
})
const errorMessage = ref('')
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
      offlineBackup: data.offlineBackup || { tarballUrl: '', releaseUrl: '' },
      githubApiSource: data.githubApiSource || '',
      cachedAt: data.cachedAt || '',
    }
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

async function onExecuted() {
  try {
    await reportUpdateFeedback({
      success: true,
      fromVersion: APP_VERSION,
      toVersion: info.value.latestVersion,
      deploymentMode: info.value.deploymentMode,
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
}
</style>
