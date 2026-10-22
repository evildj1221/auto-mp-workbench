import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// 开发期：vite dev server 代理 /api → 本地 FastAPI（python -m uvicorn backend.app.main:app --port 8765）
// 生产期：npm run build 产出 dist/，由 FastAPI 直接托管（exe 单进程）
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: { '/api': { target: 'http://127.0.0.1:8765', changeOrigin: true } },
  },
  build: { outDir: 'dist', emptyOutDir: true },
})
