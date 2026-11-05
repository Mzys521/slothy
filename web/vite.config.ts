import tailwindcss from '@tailwindcss/vite'
import vue from '@vitejs/plugin-vue'
import { defineConfig, type ProxyOptions } from 'vite'
import desktop from './desktop.config.cjs'
import { readFileSync } from 'node:fs'

const metadata = JSON.parse(readFileSync(new URL('./package.json', import.meta.url), 'utf8'))

const apiProxy: ProxyOptions = {
  target: process.env.DESKTOP_API_ORIGIN || desktop.apiOrigin,
  changeOrigin: true,
  configure(proxy) {
    proxy.on('proxyReq', request => request.setHeader('Origin', desktop.apiRequestOrigin))
  },
}

export default defineConfig({
  base: './',
  define: { __APP_VERSION__: JSON.stringify(metadata.version) },
  plugins: [
    vue(),
    tailwindcss(),
    {
      name: 'desktop-runtime',
      transformIndexHtml: {
        order: 'pre',
        handler: () => [{
          tag: 'script',
          attrs: { type: 'module', src: '/electron/renderer.js' },
          injectTo: 'head-prepend',
        }],
      },
    },
  ],
  server: { proxy: { '/api': apiProxy }, watch: { ignored: ['**/build/**', '**/release/**', '**/qa/**'] } },
  preview: { proxy: { '/api': apiProxy } },
})
