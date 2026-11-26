import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// base='./'：构建产物用相对路径引用，任何子路径/反代下都能直接服务
// server.proxy：npm run dev 时把 /api 转给后端 8000，前后端各自热更新
export default defineConfig({
  plugins: [vue()],
  base: './',
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
