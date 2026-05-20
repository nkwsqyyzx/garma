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
    port: 5173,
    proxy: {
      '/api': 'http://192.168.1.11:8000',
      '/ws': {
        target: 'ws://192.168.1.11:8000',
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
