import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './browser-tests',
  testMatch: '**/*.pw.js',
  fullyParallel: true,
  reporter: 'list',
  use: {
    baseURL: 'http://127.0.0.1:5179',
    browserName: 'chromium',
    headless: true
  },
  webServer: {
    command: 'npm run dev -- --host 127.0.0.1 --port 5179 --strictPort',
    url: 'http://127.0.0.1:5179',
    reuseExistingServer: !process.env.CI,
    timeout: 30_000
  }
})
