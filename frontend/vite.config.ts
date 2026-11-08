import { fileURLToPath, URL } from 'node:url'
import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

// VITE_API_BASE_URL 是唯一入口（默认 /api）。禁止在组件里硬编码路径 —— 见 SKILL.md §6。
export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  build: {
    // 骨架期不做精细分包，但要把"改业务代码"和"升级依赖"的缓存分开
    rollupOptions: {
      output: {
        manualChunks: {
          vue: ['vue', 'vue-router'],
          element: ['element-plus'],
        },
      },
    },
    chunkSizeWarningLimit: 1000,
  },
  server: {
    port: 5173,
    proxy: {
      // 开发期直连 Go（8080），生产由 Nginx 反代，前端代码不用改
      '/api': {
        target: process.env.VITE_DEV_PROXY_TARGET ?? 'http://localhost:8080',
        changeOrigin: true,
      },
    },
  },
})
