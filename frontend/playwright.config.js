import { defineConfig } from '@playwright/test'

const port = process.env.VFX_FILES_PLAYWRIGHT_PORT || '5179'

export default defineConfig({
  testDir: './browser-tests',
  testMatch: '**/*.pw.js',
  fullyParallel: true,
  reporter: 'list',
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    browserName: 'chromium',
    headless: true
  },
  webServer: {
    command: `npm run dev -- --host 127.0.0.1 --port ${port} --strictPort`,
    url: `http://127.0.0.1:${port}`,
    reuseExistingServer: !process.env.CI,
    timeout: 30_000
  }
})
