import { execFileSync } from 'node:child_process'
import { expect, test } from '@playwright/test'

const host = process.env.VFX_FILES_LIVE_HOST_CONTAINER
const productionId = process.env.VFX_FILES_LIVE_PRODUCTION_ID
const taskId = process.env.VFX_FILES_LIVE_TASK_ID
const accessToken = process.env.VFX_FILES_LIVE_ACCESS_TOKEN

test.skip(!host || !productionId || !taskId || !accessToken, 'Set the synthetic pinned-host integration environment to enable live workflow proof.')

test('real browser upload reaches the pinned Zou API, worker and FakeStorage, then recovers on refresh', async ({ page, context }) => {
  const pageUrl = new URL(process.env.VFX_FILES_LIVE_BASE_URL || 'http://127.0.0.1:62089/api/plugins/vfx_files/frontend/')
  if (!pageUrl.pathname.endsWith('/')) pageUrl.pathname += '/'
  const origin = pageUrl.origin
  const bundleResponse = page.waitForResponse((response) => response.url().includes('/api/plugins/vfx_files/frontend/assets/index-'))
  await context.addCookies([{
    name: process.env.VFX_FILES_LIVE_COOKIE_NAME || 'access_token_cookie',
    value: accessToken,
    url: origin
  }])

  const productionResponse = page.waitForResponse((response) => response.url().includes('/api/plugins/vfx_files/overview'))
  pageUrl.searchParams.set('production_id', productionId)
  await page.goto(pageUrl.toString())
  expect((await bundleResponse).status()).toBe(200)
  await expect(page.getByRole('heading', { name: 'Files', level: 1, exact: true })).toBeVisible()
  expect((await productionResponse).status()).toBe(200)
  await page.getByLabel('Choose Task').selectOption(taskId)
  const fileName = `browser-e2e-${Date.now()}.mov`
  const fileBytes = Buffer.from('browser to Zou to worker to FakeStorage')
  const fileInput = page.locator('input[type="file"]')
  await fileInput.setInputFiles({ name: fileName, mimeType: 'video/quicktime', buffer: fileBytes })
  await expect(page.getByText(/Source staged\. Job .* is queued/)).toBeVisible({ timeout: 15_000 })

  const workerOutput = execFileSync('docker', [
    'exec',
    '-e', 'SECRET_KEY=vfx-files-isolated-test-secret-key-32-bytes-minimum',
    '-e', 'VFX_FILES_ENABLED=true',
    '-e', 'VFX_FILES_WORKER_ENABLED=true',
    '-e', 'VFX_FILES_STORAGE_MODE=fake',
    '-e', `VFX_FILES_STAGING_ROOT=${process.env.VFX_FILES_LIVE_STAGING_ROOT}`,
    '-e', `VFX_FILES_FAKE_STORAGE_ROOT=${process.env.VFX_FILES_LIVE_FAKE_STORAGE_PATH}`,
    '-e', 'VFX_FILES_FAKE_ROOT_ID=synthetic-root-id',
    '-e', 'PYTHONPATH=/opt/zou/plugins',
    host,
    '/opt/zou/env/bin/python', '-m', 'vfx_files.worker_runtime', '--once'
  ], { encoding: 'utf8', timeout: 30_000 })
  expect(workerOutput).toContain('"result": "completed"')

  await page.reload()
  const completedFile = page.locator('.file-row[data-state="completed"]').filter({ hasText: fileName })
  await expect(completedFile).toContainText(fileName)
  await expect(completedFile).toContainText(String(fileBytes.length))
  const overview = await page.evaluate(async (id) => {
    const response = await fetch(`/api/plugins/vfx_files/overview?production_id=${encodeURIComponent(id)}`, { credentials: 'same-origin' })
    return response.json()
  }, productionId)
  const record = overview.files.find((item) => item.name === fileName)
  expect(record).toMatchObject({ state: 'completed', size: fileBytes.length })
  expect(record.provider_object_id).toBeTruthy()
  expect(record.sha256).toBeTruthy()
})
