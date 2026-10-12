import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

const apiProxy = {
  target: process.env.VFX_FILES_DEV_API_TARGET || 'http://127.0.0.1:5000',
  changeOrigin: true,
  configure(proxy) {
    const origin = process.env.VFX_FILES_DEV_API_ORIGIN
    if (origin) proxy.on('proxyReq', (proxyRequest) => proxyRequest.setHeader('Origin', origin))
  }
}
if (process.env.VFX_FILES_DEV_API_REWRITE === 'strip-api') {
  apiProxy.rewrite = (path) => path.replace(/^\/api/, '')
}

export default defineConfig({
  base: './',
  plugins: [vue()],
  build: {
    outDir: '../plugin/frontend/dist',
    emptyOutDir: true
  },
  server: {
    proxy: {
      '/api': apiProxy
    }
  },
  test: {
    environment: 'node'
  }
})
