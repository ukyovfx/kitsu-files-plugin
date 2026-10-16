import { execFileSync } from 'node:child_process'
import { expect, test } from '@playwright/test'

const composeProject = process.env.VFX_FILES_LIVE_COMPOSE_PROJECT
const composeFile = process.env.VFX_FILES_LIVE_COMPOSE_FILE
const composeEnvFile = process.env.VFX_FILES_LIVE_COMPOSE_ENV_FILE
const productionId = process.env.VFX_FILES_LIVE_PRODUCTION_ID
const productionName = process.env.VFX_FILES_LIVE_PRODUCTION_NAME
const taskId = process.env.VFX_FILES_LIVE_TASK_ID
const recipientTaskId = process.env.VFX_FILES_LIVE_RECIPIENT_TASK_ID
const recipientDepartmentId = process.env.VFX_FILES_LIVE_RECIPIENT_DEPARTMENT_ID
const recipientPersonId = process.env.VFX_FILES_LIVE_ARTIST_A_ID
const recipientPersonCId = process.env.VFX_FILES_LIVE_ARTIST_C_ID
const loginEmails = JSON.parse(process.env.VFX_FILES_LIVE_LOGIN_EMAILS || '{}')
const loginPassword = process.env.VFX_FILES_LIVE_TEST_PASSWORD
const artistAId = process.env.VFX_FILES_LIVE_ARTIST_A_ID

const missingHostFixture = !composeProject || !composeFile || !composeEnvFile || !productionId || !productionName || !taskId || !recipientTaskId || !recipientDepartmentId || !recipientPersonId || !recipientPersonCId || !loginPassword || !loginEmails.manager || !loginEmails.artist_a || !loginEmails.artist_b || !loginEmails.artist_c || !loginEmails.supervisor || !artistAId

const baseUrl = process.env.VFX_FILES_LIVE_BASE_URL || 'http://127.0.0.1:55253'
const origin = new URL(baseUrl).origin
const pluginApiBase = `${origin}/api/plugins/vfx_files`

async function openFilesFromProductionMenu(page, productionId, { alreadySelected = false, taskId: selectedTaskId = null } = {}) {
  const selectedPlugin = page.getByRole('button', { name: 'VFX Files', exact: true })
  const assetsMenu = page.getByRole('button', { name: 'Assets', exact: true })
  if (alreadySelected) {
    await expect(selectedPlugin).toBeVisible()
  } else {
    await expect(assetsMenu).toBeVisible()
    await assetsMenu.click()
    const projectMenuItem = page.getByText('VFX Files', { exact: true })
    await expect(projectMenuItem).toBeVisible()
    await projectMenuItem.click()
  }
  await expect.poll(() => page.frames().some((frame) => frame.url().includes('/api/plugins/vfx_files/frontend/') && frame.url().includes(`production_id=${encodeURIComponent(productionId)}`))).toBe(true)
  let frame = page.frames().find((item) => item.url().includes('/api/plugins/vfx_files/frontend/'))
  if (selectedTaskId && !frame.url().includes(`task_id=${encodeURIComponent(selectedTaskId)}`)) {
    await frame.goto(`${origin}/api/plugins/vfx_files/frontend/?production_id=${encodeURIComponent(productionId)}&task_id=${encodeURIComponent(selectedTaskId)}`)
  }
  const overview = await readOverview(page, productionId)
  expect(overview.production_id).toBe(productionId)
  frame = page.frames().find((item) => item.url().includes('/api/plugins/vfx_files/frontend/'))
  await expect(frame.getByRole('heading', { name: 'Files', level: 1, exact: true })).toBeVisible()
}

async function openFiles(browser, role) {
  const context = await browser.newContext({
    // Kitsu/Zou distinguishes browser logins from API clients using the UA parser.
    userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
  })
  const page = await context.newPage()
  await page.goto(`${origin}/login`)
  const loginResponse = page.waitForResponse((response) => response.url().endsWith('/api/auth/login') && response.request().method() === 'POST')
  await page.getByRole('textbox', { name: 'Email' }).fill(loginEmails[role])
  await page.getByRole('textbox', { name: 'Password' }).fill(loginPassword)
  await page.getByRole('button', { name: 'Log in', exact: true }).click()
  const authResponse = await loginResponse
  expect(authResponse.status()).toBe(200)
  const cookieNames = (await context.cookies()).map((cookie) => cookie.name)
  expect(cookieNames).toContain('access_token_cookie')
  await page.goto(`${origin}/open-productions`)
  const production = page.getByText(productionName, { exact: true })
  await expect(production).toBeVisible()
  await production.click()
  await openFilesFromProductionMenu(page, productionId)
  return { context, page, get ui() { return page.frames().find((frame) => frame.url().includes('/api/plugins/vfx_files/frontend/')) } }
}

async function readOverview(page, productionId) {
  const response = await page.request.get(`${pluginApiBase}/overview?production_id=${encodeURIComponent(productionId)}`)
  if (response.status() !== 200) throw new Error(`Overview API returned ${response.status()}: ${await response.text()}`)
  return response.json()
}

function runWorker() {
  if (!composeProject || !composeFile || !composeEnvFile) {
    throw new Error('Disposable Worker Compose identity is required for this integration test.')
  }
  const output = execFileSync('docker', [
    'compose', '--project-name', composeProject, '--env-file', composeEnvFile, '--file', composeFile,
    '--profile', 'worker', 'run', '--rm', '--no-deps', 'worker',
    '/opt/zou/env/bin/python', '-m', 'vfx_files.worker_runtime', '--once'
  ], { encoding: 'utf8', timeout: 90_000 })
  expect(output).toContain('"result": "completed"')
}

test('installed Files page uses KitsuSync-managed folders and carries w001 through Artist B to immutable w002', async ({ browser }) => {
  test.skip(missingHostFixture, 'Requires synthetic identities on the pinned disposable Zou host.')
  // A pinned host is slow to start disposable one-shot Workers between UI
  // actions; budget the full installed-host flow rather than timing out after
  // a successful first upload.
  test.setTimeout(420_000)
  const manager = await openFiles(browser, 'manager')
  await openFilesFromProductionMenu(manager.page, productionId, { alreadySelected: true, taskId })
  await expect(manager.ui.getByRole('link', { name: 'Open folder' })).toHaveCount(0)
  await expect(manager.ui.getByText(/managed through KitsuSync/)).toBeVisible()

  const artistA = await openFiles(browser, 'artist_a')
  await artistA.ui.getByLabel('Choose Task').selectOption(taskId)
  const bytes001 = Buffer.from('verified synthetic Working File revision w001')
  const name001 = 'artist-a-scene-v001.exr'
  await artistA.ui.locator('input[type="file"]').setInputFiles({ name: name001, mimeType: 'image/x-exr', buffer: bytes001 })
  await expect(artistA.ui.getByText(/Working w001 reserved\. 1 transfer job\(s\) queued\./)).toBeVisible({ timeout: 15_000 })
  runWorker()
  await artistA.page.reload()
  await openFilesFromProductionMenu(artistA.page, productionId, { alreadySelected: true, taskId })
  await expect(artistA.ui.getByRole('link', { name: name001, exact: true })).toBeVisible()
  await expect(artistA.ui.locator('.revision-heading')).toContainText('w001')

  const artistB = await openFiles(browser, 'artist_b')
  await artistB.ui.getByLabel('Choose Task').selectOption(taskId)
  const file001 = artistB.ui.getByRole('link', { name: name001, exact: true })
  await expect(file001).toBeVisible()
  const download001 = await artistB.page.request.get(new URL(await file001.getAttribute('href'), artistB.ui.url()).toString())
  expect(download001.status()).toBe(200)
  expect(await download001.body()).toEqual(bytes001)

  const bytes002 = Buffer.from('verified synthetic Working File revision w002 from Artist B')
  const name002 = 'artist-b-scene-v002.exr'
  await artistB.ui.locator('input[type="file"]').setInputFiles({ name: name002, mimeType: 'image/x-exr', buffer: bytes002 })
  await expect(artistB.ui.getByText(/Working w002 reserved\. 1 transfer job\(s\) queued\./)).toBeVisible({ timeout: 15_000 })
  runWorker()
  await artistB.page.reload()
  await openFilesFromProductionMenu(artistB.page, productionId, { alreadySelected: true, taskId })
  await expect(artistB.ui.locator('.revision-list').getByText('w001', { exact: true })).toBeVisible()
  await expect(artistB.ui.locator('.revision-list').getByText('w002', { exact: true })).toBeVisible()
  const pointer = artistB.ui.locator('.revision-heading')
  await expect(pointer).toContainText('Latest Working')
  await expect(pointer).toContainText('w002')
  for (const [name, bytes] of [[name001, bytes001], [name002, bytes002]]) {
    const file = artistB.ui.getByRole('link', { name, exact: true })
    const response = await artistB.page.request.get(new URL(await file.getAttribute('href'), artistB.ui.url()).toString())
    expect(response.status()).toBe(200)
    expect(await response.body()).toEqual(bytes)
  }

  await artistB.ui.getByLabel('Completed Working File').selectOption({ label: `w002 · ${name002}` })
  await artistB.ui.getByLabel('Output family').selectOption({ label: 'Synthetic Main' })
  await artistB.ui.getByRole('button', { name: 'Publish Working File' }).click()
  await expect(artistB.ui.getByRole('status').filter({ hasText: /Publish v001 queued/ })).toBeVisible()
  runWorker()
  await artistB.page.reload()
  await openFilesFromProductionMenu(artistB.page, productionId, { alreadySelected: true, taskId })
  let overview = await readOverview(artistB.page, productionId)
  const publish = overview.publishes.find((row) => row.task_id === taskId)
  expect(publish).toMatchObject({ state: 'completed', qc_state: 'passed', latest_published: true })
  expect(publish.manifest).toMatchObject({ publish_id: publish.id, task_id: taskId, source_revision_file_id: expect.any(String) })
  expect(publish.manifest.artifact).toMatchObject({ provider_object_id: publish.provider_artifact_id, byte_count: bytes002.length })
  expect(publish.native_output_file_id).toBeTruthy()
  await expect(artistB.ui.getByRole('link', { name: 'Open artifact' })).toBeVisible()

  const unauthorizedCsrf = await artistA.ui.evaluate(async () => (await (await fetch('/api/plugins/vfx_files/csrf-token', { credentials: 'same-origin' })).json()).csrf_token)
  const unauthorizedApproval = await artistA.page.request.post(`${pluginApiBase}/publishes/${publish.id}/approvals`, {
    headers: { Origin: new URL(baseUrl).origin, 'X-VFX-Files-CSRF': unauthorizedCsrf, 'Idempotency-Key': `denied-approval-${publish.id}` },
    data: { policy_id: 'files-page-review-v1', policy_sha256: '9a3d8d0e6a4d690ad97f6f6a7fcfae135c7fc2e0bd23a5e5e5bf30a55a3d93c1' }
  })
  expect(unauthorizedApproval.status()).toBe(403)

  const supervisor = await openFiles(browser, 'supervisor')
  await supervisor.ui.getByLabel('Publish Task').selectOption(taskId)
  await expect(supervisor.ui.getByRole('button', { name: 'Approve exact Publish' })).toBeVisible()
  await supervisor.ui.getByRole('button', { name: 'Approve exact Publish' }).click()
  await expect(supervisor.ui.getByRole('button', { name: 'Approved' })).toBeVisible()
  await supervisor.page.reload()
  await openFilesFromProductionMenu(supervisor.page, productionId, { alreadySelected: true, taskId })
  overview = await readOverview(supervisor.page, productionId)
  expect(overview.publishes.find((row) => row.id === publish.id)).toMatchObject({ latest_published: true, latest_approved: true })

  await expect(artistB.ui.getByLabel('Handoff Publish')).toBeVisible()
  await artistB.ui.getByLabel('Handoff Publish').selectOption(publish.id)
  await artistB.ui.getByLabel('Handoff recipient').selectOption(artistAId)
  await artistB.ui.getByRole('button', { name: 'Create Handoff' }).click()
  await expect(artistB.ui.getByRole('status').filter({ hasText: /Handoff pending/ })).toBeVisible()
  await artistA.page.reload()
  await openFilesFromProductionMenu(artistA.page, productionId, { alreadySelected: true, taskId })
  const incomingHandoff = artistA.ui.getByRole('region', { name: 'Awaiting your response' }).getByRole('article').filter({ hasText: publish.id })
  await expect(incomingHandoff).toBeVisible()
  await artistA.ui.getByRole('button', { name: 'Accept' }).click()
  await expect(artistA.ui.getByText('accepted', { exact: true })).toBeVisible()
  await artistA.page.reload()
  await openFilesFromProductionMenu(artistA.page, productionId, { alreadySelected: true, taskId })
  overview = await readOverview(artistA.page, productionId)
  expect(overview.handoffs.find((item) => item.publish_id === publish.id)).toMatchObject({ state: 'accepted', recipient_id: artistAId })

  // Exercise the Department-targeted flow through the installed Kitsu page,
  // backed by Zou/PostgreSQL, the real plugin API and FakeStorage.
  await artistB.page.reload()
  await openFilesFromProductionMenu(artistB.page, productionId, { alreadySelected: true, taskId })
  await artistB.ui.getByLabel('Publish Task').selectOption(taskId)
  await artistB.ui.getByLabel('Department Handoff Publish').selectOption(publish.id)
  await artistB.ui.getByLabel('Receiving Department').selectOption(recipientDepartmentId)
  await artistB.ui.getByLabel('Receiving Task Type').selectOption(recipientTaskId)
  await expect(artistB.ui.getByLabel('Accountable person').locator(`option[value="${recipientPersonId}"]`)).toHaveCount(1)
  await artistB.ui.getByLabel('Accountable person').selectOption(recipientPersonId)
  await artistB.ui.getByLabel('Handoff note').fill('Department E2E: check edge treatment.')
  await expect(artistB.ui.getByRole('group', { name: 'Handoff confirmation summary' })).toContainText(publish.id)
  await expect(artistB.ui.getByRole('group', { name: 'Handoff confirmation summary' })).toContainText('Publish v001')
  await artistB.ui.getByRole('button', { name: 'Confirm and Send Publish' }).click()
  await expect(artistB.ui.getByRole('status').filter({ hasText: /Created handoff for VFXCompositing/ })).toBeVisible()
  overview = await readOverview(artistB.page, productionId)
  const needsFixHandoff = overview.handoffs.find((item) => item.publish_id === publish.id && item.recipient_task_id === recipientTaskId && item.state === 'pending')
  expect(needsFixHandoff).toMatchObject({
    publish_id: publish.id,
    publish_version: publish.version,
    recipient_department_id: recipientDepartmentId,
    recipient_person_id: recipientPersonId,
    sender_note: 'Department E2E: check edge treatment.'
  })

  await openFilesFromProductionMenu(artistA.page, productionId, { alreadySelected: true, taskId: recipientTaskId })
  const departmentInbox = artistA.ui.locator('.handoff-row').filter({ hasText: publish.id })
  await expect(artistA.ui.getByText('Department Inbox')).toBeVisible()
  await expect(departmentInbox).toContainText('Department E2E: check edge treatment.')
  await expect(departmentInbox).toContainText('Exact Publish v001')
  await expect(departmentInbox).toContainText('VFXCompositing')
  const verifiedDownload = await artistA.page.request.get(new URL(
    await departmentInbox.getByRole('link', { name: 'Download verified Publish' }).getAttribute('href'),
    artistA.ui.url()
  ).toString())
  expect(verifiedDownload.status()).toBe(200)
  expect(await verifiedDownload.body()).toEqual(bytes002)
  await departmentInbox.getByRole('textbox', { name: 'Needs Fix feedback' }).fill('Please adjust the edge and resend.')
  await departmentInbox.getByRole('button', { name: 'Needs Fix' }).click()
  await expect(departmentInbox).toContainText('needs_fix')
  await expect(departmentInbox).toContainText('Please adjust the edge and resend.')
  await artistA.page.reload()
  await openFilesFromProductionMenu(artistA.page, productionId, { alreadySelected: true, taskId: recipientTaskId })
  const persistedNeedsFix = artistA.ui.locator('.handoff-row').filter({ hasText: publish.id })
  await expect(persistedNeedsFix).toContainText('needs_fix')
  await expect(persistedNeedsFix).toContainText('Please adjust the edge and resend.')

  // A second Handoff with no named responder exposes response ownership to
  // the whole currently eligible team. Concurrent HTTP responses must still
  // serialize to one persisted decision.
  await artistB.page.reload()
  await openFilesFromProductionMenu(artistB.page, productionId, { alreadySelected: true, taskId })
  await artistB.ui.getByLabel('Publish Task').selectOption(taskId)
  await artistB.ui.getByLabel('Department Handoff Publish').selectOption(publish.id)
  await artistB.ui.getByLabel('Receiving Department').selectOption(recipientDepartmentId)
  await artistB.ui.getByLabel('Receiving Task Type').selectOption(recipientTaskId)
  await expect(artistB.ui.getByLabel('Accountable person')).toHaveValue('')
  await artistB.ui.getByLabel('Handoff note').fill('Concurrent response serialization check.')
  await artistB.ui.getByRole('button', { name: 'Confirm and Send Publish' }).click()
  await expect(artistB.ui.getByRole('status').filter({ hasText: /Created handoff for VFXCompositing/ })).toBeVisible()
  overview = await readOverview(artistB.page, productionId)
  const concurrentHandoff = overview.handoffs.find((item) => item.publish_id === publish.id && item.recipient_task_id === recipientTaskId && item.sender_note === 'Concurrent response serialization check.')
  expect(concurrentHandoff).toMatchObject({ state: 'pending', response_ownership: 'first_authorized_responder' })

  const artistC = await openFiles(browser, 'artist_c')
  await openFilesFromProductionMenu(artistC.page, productionId, { alreadySelected: true, taskId: recipientTaskId })
  const responseRequest = async (recipientContext, target, feedback, suffix) => {
    const csrf = await recipientContext.page.evaluate(async () => {
      const response = await fetch('/api/plugins/vfx_files/csrf-token', { credentials: 'same-origin' })
      if (!response.ok) throw new Error(`CSRF endpoint returned ${response.status}`)
      return (await response.json()).csrf_token
    })
    return recipientContext.page.request.post(`${pluginApiBase}/handoffs/${concurrentHandoff.id}/respond`, {
      headers: { Origin: origin, 'X-VFX-Files-CSRF': csrf, 'Idempotency-Key': `department-race-${concurrentHandoff.id}-${suffix}` },
      data: { target, feedback }
    })
  }
  const race = await Promise.all([
    responseRequest(artistA, 'accepted', '', 'artist-a'),
    responseRequest(artistC, 'needs_fix', 'Concurrent review feedback.', 'artist-c')
  ])
  expect(race.map((response) => response.status()).sort()).toEqual([201, 409])
  const winningResponse = race.find((response) => response.status() === 201)
  const winningPayload = await winningResponse.json()
  expect(winningPayload.state).toMatch(/^(accepted|needs_fix)$/)
  const persistedOverview = await readOverview(artistA.page, productionId)
  const persistedRace = persistedOverview.handoffs.find((item) => item.id === concurrentHandoff.id)
  expect(persistedRace.state).toBe(winningPayload.state)
  expect(persistedRace.events.filter((event) => ['accepted', 'needs_fix'].includes(event.type))).toHaveLength(1)
  if (winningPayload.state === 'needs_fix') expect(persistedRace.events.at(-1).feedback).toBe('Concurrent review feedback.')

  await manager.context.close()
  await artistA.context.close()
  await artistB.context.close()
  await supervisor.context.close()
  await artistC.context.close()
})
