import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// base='./'：构建产物用相对路径引用，任何子路径/反代下都能直接服务
// server.proxy：npm run dev 时把 /api 转给后端 8000，前后端各自热更新
// manualChunks：echarts 单独分包——业务迭代时不打爆图表库的浏览器缓存，首屏两请求并行不阻塞
export default defineConfig({
  plugins: [vue()],
  base: './',
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          echarts: ['echarts'],
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
