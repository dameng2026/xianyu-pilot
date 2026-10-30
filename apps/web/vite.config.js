import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import pkg from './package.json'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { curateBuiltPublicAssets } from './scripts/public-assets.mjs'

const buildDate = new Date().toISOString()
const webRoot = path.dirname(fileURLToPath(import.meta.url))
const apiProxyTarget = process.env.VITE_API_PROXY_TARGET || 'http://127.0.0.1:15177'
const uploadsProxyTarget = process.env.VITE_UPLOAD_PROXY_TARGET || apiProxyTarget
const webDevPort = Number.parseInt(process.env.XYA_WEB_PORT || '15176', 10)
const webDevHost = process.env.XYA_WEB_HOST || '127.0.0.1'

if (!Number.isInteger(webDevPort) || webDevPort < 1 || webDevPort > 65535) {
  throw new Error('XYA_WEB_PORT must be an integer between 1 and 65535')
}

export default defineConfig({
  plugins: [
    vue(),
    {
      name: 'curate-runtime-public-assets',
      apply: 'build',
      closeBundle() {
        const result = curateBuiltPublicAssets(webRoot)
        console.log(`curated public assets: kept ${result.keptFiles}, removed ${result.removedFiles}`)
      },
    },
  ],
  define: {
    __APP_VERSION__: JSON.stringify(pkg.version),
    __APP_BUILD_DATE__: JSON.stringify(buildDate)
  },
  build: {
    rollupOptions: {
      input: { main: path.resolve(webRoot, 'index.html'), safety: path.resolve(webRoot, 'desktop-safety.html') },
    },
  },
  server: {
    port: webDevPort,
    strictPort: true,
    host: webDevHost,
    proxy: {
      '/api': {
        target: apiProxyTarget,
        changeOrigin: true,
      },
      // /ai/* 前端 aiChat.js 直接 fetch('/ai/...')，代理到本地 Python API
      '/ai': {
        target: apiProxyTarget,
        changeOrigin: true,
      },
      // 上传的图片/文件由本项目 Python API 直接提供静态服务（/uploads/images/xxx.png）
      // 代理到本地 Python API，避免 Vite SPA fallback 返回 index.html 导致图片无法显示
      '/uploads': {
        target: uploadsProxyTarget,
        changeOrigin: true,
      },
    },
  },
})
