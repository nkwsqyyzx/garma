import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src'),
    },
  },
  server: {
    port: 8999,
    proxy: {
      '/api': 'http://127.0.0.1:8998',
      '/ws': {
        target: 'ws://127.0.0.1:8998',
        ws: true,
      },
    },
  },
  build: {
    outDir: '../backend/static',
    emptyOutDir: true,
    rollupOptions: {
      external: ['echarts'],
      output: {
        globals: {
          echarts: 'echarts',
        },
        manualChunks: {
          'element-plus': ['element-plus'],
          'vue-vendor': ['vue', 'vue-router', 'pinia'],
        },
      },
    },
  },
})
