import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  timeout: 30000,
  retries: 0,
  use: {
    baseURL: 'http://localhost:8999',
    headless: true,
    actionTimeout: 10000,
    screenshot: 'only-on-failure',
  },
})
