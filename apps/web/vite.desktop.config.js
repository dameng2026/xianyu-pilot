import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath } from 'node:url'

export default defineConfig({
  root: fileURLToPath(new URL('../desktop/ui', import.meta.url)),
  plugins: [vue()],
  resolve: {
    alias: { vue: fileURLToPath(new URL('./node_modules/vue/dist/vue.runtime.esm-bundler.js', import.meta.url)) },
  },
  build: {
    outDir: fileURLToPath(new URL('../desktop/web-dist', import.meta.url)),
    emptyOutDir: true,
  },
})
