import { execFileSync } from 'node:child_process'
import { expect, test } from '@playwright/test'

const host = process.env.VFX_FILES_LIVE_HOST_CONTAINER
const productionId = process.env.VFX_FILES_LIVE_PRODUCTION_ID
const taskId = process.env.VFX_FILES_LIVE_TASK_ID
const managerToken = process.env.VFX_FILES_LIVE_MANAGER_TOKEN
const artistAToken = process.env.VFX_FILES_LIVE_ARTIST_A_TOKEN
const artistBToken = process.env.VFX_FILES_LIVE_ARTIST_B_TOKEN
const supervisorToken = process.env.VFX_FILES_LIVE_SUPERVISOR_TOKEN
const artistAId = process.env.VFX_FILES_LIVE_ARTIST_A_ID

const missingHostFixture = !host || !productionId || !taskId || !managerToken || !artistAToken || !artistBToken || !supervisorToken || !artistAId

const baseUrl = process.env.VFX_FILES_LIVE_BASE_URL || 'http://127.0.0.1:55253/api/plugins/vfx_files/frontend/index.html'
const cookieName = process.env.VFX_FILES_LIVE_COOKIE_NAME || 'access_token_cookie'
const pluginApiBase = `${new URL(baseUrl).origin}/api/plugins/vfx_files`

async function openFiles(browser, token) {
  const context = await browser.newContext()
  const origin = new URL(baseUrl).origin
  await context.addCookies([{ name: cookieName, value: token, url: origin }])
  const page = await context.newPage()
  const overview = page.waitForResponse((response) => response.url().includes(`${pluginApiBase}/overview`))
  await page.goto(`${baseUrl}?production_id=${encodeURIComponent(productionId)}`)
  const overviewResponse = await overview
  expect(overviewResponse.status(), await overviewResponse.text()).toBe(200)
  await expect(page.getByRole('heading', { name: 'Files', level: 1, exact: true })).toBeVisible()
  return { context, page }
}

async function readOverview(page, productionId) {
  const response = await page.request.get(`${pluginApiBase}/overview?production_id=${encodeURIComponent(productionId)}`)
  if (response.status() !== 200) throw new Error(`Overview API returned ${response.status()}: ${await response.text()}`)
  return response.json()
}

function runWorker() {
  const output = execFileSync('docker', [
    'exec',
    '-e', 'SECRET_KEY=vfx-files-isolated-test-secret-key-32-bytes-minimum',
    '-e', 'PLUGIN_FOLDER=/opt/zou/plugins',
    '-e', 'PYTHONPATH=/opt/zou/plugins',
    '-e', 'VFX_FILES_ENABLED=true',
    '-e', 'VFX_FILES_WORKER_ENABLED=true',
    '-e', 'VFX_FILES_STORAGE_MODE=fake',
    '-e', `VFX_FILES_STAGING_ROOT=${process.env.VFX_FILES_LIVE_STAGING_ROOT}`,
    '-e', `VFX_FILES_FAKE_STORAGE_ROOT=${process.env.VFX_FILES_LIVE_FAKE_STORAGE_PATH}`,
    '-e', 'VFX_FILES_FAKE_ROOT_ID=synthetic-root-id',
    '-e', 'VFX_FILES_FAKE_DRIVE_ID=synthetic-drive-id',
    host,
    '/opt/zou/env/bin/python', '-m', 'vfx_files.worker_runtime', '--once'
  ], { encoding: 'utf8', timeout: 30_000 })
  expect(output).toContain('"result": "completed"')
}

test('installed Files page provisions folders and carries w001 through Artist B to immutable w002', async ({ browser }) => {
  test.skip(missingHostFixture, 'Requires synthetic identities on the pinned disposable Zou host.')
  test.setTimeout(90_000)
  const manager = await openFiles(browser, managerToken)
  const managerPage = manager.page
  await managerPage.getByRole('button', { name: /Folder template setup/ }).click()
  await managerPage.getByLabel('Google Drive root URL').fill(`https://drive.google.com/drive/folders/synthetic-root-id`)
  await managerPage.getByRole('button', { name: 'Discover' }).click()
  await managerPage.getByLabel('Folder template', { exact: true }).selectOption('template-root')
  await managerPage.getByLabel('Sequence (optional) mapping').selectOption('template-sequence')
  await managerPage.getByLabel('Shot mapping').selectOption('template-shot')
  await managerPage.getByLabel('Task mapping').selectOption('template-task')
  await managerPage.getByRole('button', { name: 'Save disabled configuration' }).click()
  await expect(managerPage.getByText(/Configuration v\d+ · disabled/)).toBeVisible()
  await managerPage.getByRole('button', { name: 'Activate provisioning' }).click()
  await expect(managerPage.getByText(/Configuration v\d+ · active/)).toBeVisible()
  await managerPage.getByLabel('Provision Task folders').selectOption(taskId)
  await managerPage.getByRole('button', { name: 'Create or reuse' }).click()
  await expect(managerPage.getByText(/Folder job queued:/)).toBeVisible()
  runWorker()

  await managerPage.reload()
  await expect(managerPage.getByRole('link', { name: 'Open folder' })).toHaveCount(5)
  const folderLinks = await managerPage.getByRole('link', { name: 'Open folder' }).evaluateAll((links) => links.map((link) => link.href))
  expect(folderLinks.every((url) => /^https:\/\/drive\.google\.com\/drive\/folders\/[A-Za-z0-9_-]+$/.test(url))).toBe(true)

  const artistA = await openFiles(browser, artistAToken)
  await artistA.page.getByLabel('Choose Task').selectOption(taskId)
  const bytes001 = Buffer.from('verified synthetic Working File revision w001')
  const name001 = 'artist-a-scene-v001.exr'
  await artistA.page.locator('input[type="file"]').setInputFiles({ name: name001, mimeType: 'image/x-exr', buffer: bytes001 })
  await expect(artistA.page.getByText(/Working w001 reserved\. 1 transfer job\(s\) queued\./)).toBeVisible({ timeout: 15_000 })
  runWorker()
  await artistA.page.reload()
  await artistA.page.getByLabel('Choose Task').selectOption(taskId)
  await expect(artistA.page.getByRole('link', { name: name001, exact: true })).toBeVisible()
  await expect(artistA.page.locator('.revision-heading')).toContainText('w001')

  const artistB = await openFiles(browser, artistBToken)
  await artistB.page.getByLabel('Choose Task').selectOption(taskId)
  const file001 = artistB.page.getByRole('link', { name: name001, exact: true })
  await expect(file001).toBeVisible()
  const download001 = await artistB.page.request.get(new URL(await file001.getAttribute('href'), artistB.page.url()).toString())
  expect(download001.status()).toBe(200)
  expect(await download001.body()).toEqual(bytes001)

  const bytes002 = Buffer.from('verified synthetic Working File revision w002 from Artist B')
  const name002 = 'artist-b-scene-v002.exr'
  await artistB.page.locator('input[type="file"]').setInputFiles({ name: name002, mimeType: 'image/x-exr', buffer: bytes002 })
  await expect(artistB.page.getByText(/Working w002 reserved\. 1 transfer job\(s\) queued\./)).toBeVisible({ timeout: 15_000 })
  runWorker()
  await artistB.page.reload()
  await artistB.page.getByLabel('Choose Task').selectOption(taskId)
  await expect(artistB.page.locator('.revision-list').getByText('w001', { exact: true })).toBeVisible()
  await expect(artistB.page.locator('.revision-list').getByText('w002', { exact: true })).toBeVisible()
  const pointer = artistB.page.locator('.revision-heading')
  await expect(pointer).toContainText('Latest Working')
  await expect(pointer).toContainText('w002')
  for (const [name, bytes] of [[name001, bytes001], [name002, bytes002]]) {
    const file = artistB.page.getByRole('link', { name, exact: true })
    const response = await artistB.page.request.get(new URL(await file.getAttribute('href'), artistB.page.url()).toString())
    expect(response.status()).toBe(200)
    expect(await response.body()).toEqual(bytes)
  }

  await artistB.page.getByLabel('Publish Task').selectOption(taskId)
  await artistB.page.getByLabel('Completed Working File').selectOption({ label: `w002 · ${name002}` })
  await artistB.page.getByLabel('Output family').selectOption({ label: 'Synthetic Main' })
  await artistB.page.getByRole('button', { name: 'Publish Working File' }).click()
  await expect(artistB.page.getByRole('status').filter({ hasText: /Publish v001 queued/ })).toBeVisible()
  runWorker()
  await artistB.page.reload()
  let overview = await readOverview(artistB.page, productionId)
  const publish = overview.publishes.find((row) => row.task_id === taskId)
  expect(publish).toMatchObject({ state: 'completed', qc_state: 'passed', latest_published: true })
  expect(publish.manifest).toMatchObject({ publish_id: publish.id, task_id: taskId, source_revision_file_id: expect.any(String) })
  expect(publish.manifest.artifact).toMatchObject({ provider_object_id: publish.provider_artifact_id, byte_count: bytes002.length })
  expect(publish.native_output_file_id).toBeTruthy()
  await expect(artistB.page.getByRole('link', { name: 'Open artifact' })).toBeVisible()

  const unauthorizedCsrf = await artistA.page.evaluate(async () => (await (await fetch('/api/plugins/vfx_files/csrf-token', { credentials: 'same-origin' })).json()).csrf_token)
  const unauthorizedApproval = await artistA.page.request.post(`${pluginApiBase}/publishes/${publish.id}/approvals`, {
    headers: { Origin: new URL(baseUrl).origin, 'X-VFX-Files-CSRF': unauthorizedCsrf, 'Idempotency-Key': `denied-approval-${publish.id}` },
    data: { policy_id: 'files-page-review-v1', policy_sha256: '9a3d8d0e6a4d690ad97f6f6a7fcfae135c7fc2e0bd23a5e5e5bf30a55a3d93c1' }
  })
  expect(unauthorizedApproval.status()).toBe(403)

  const supervisor = await openFiles(browser, supervisorToken)
  await supervisor.page.getByLabel('Publish Task').selectOption(taskId)
  await expect(supervisor.page.getByRole('button', { name: 'Approve exact Publish' })).toBeVisible()
  await supervisor.page.getByRole('button', { name: 'Approve exact Publish' }).click()
  await expect(supervisor.page.getByRole('button', { name: 'Approved' })).toBeVisible()
  await supervisor.page.reload()
  overview = await readOverview(supervisor.page, productionId)
  expect(overview.publishes.find((row) => row.id === publish.id)).toMatchObject({ latest_published: true, latest_approved: true })

  await artistB.page.getByLabel('Publish Task').selectOption(taskId)
  await expect(artistB.page.getByLabel('Handoff Publish')).toBeVisible()
  await artistB.page.getByLabel('Handoff Publish').selectOption(publish.id)
  await artistB.page.getByLabel('Handoff recipient').selectOption(artistAId)
  await artistB.page.getByRole('button', { name: 'Create Handoff' }).click()
  await expect(artistB.page.getByRole('status').filter({ hasText: /Handoff pending/ })).toBeVisible()
  await artistA.page.reload()
  const incomingHandoff = artistA.page.getByRole('region', { name: 'Awaiting your response' }).getByRole('article').filter({ hasText: publish.id })
  await expect(incomingHandoff).toBeVisible()
  await artistA.page.getByRole('button', { name: 'Accept' }).click()
  await expect(artistA.page.getByText('accepted', { exact: true })).toBeVisible()
  await artistA.page.reload()
  overview = await readOverview(artistA.page, productionId)
  expect(overview.handoffs.find((item) => item.publish_id === publish.id)).toMatchObject({ state: 'accepted', recipient_id: artistAId })

  await manager.context.close()
  await artistA.context.close()
  await artistB.context.close()
  await supervisor.context.close()
})
