import { expect, test } from '@playwright/test'

const productionId = '11111111-1111-4111-8111-111111111111'
const taskId = '22222222-2222-4222-8222-222222222222'
const handoffId = '33333333-3333-4333-8333-333333333333'
const uploadId = '44444444-4444-4444-8444-444444444444'
const revisionId = '66666666-6666-4666-8666-666666666666'
const revisionFileId = '77777777-7777-4777-8777-777777777777'
const sha256 = 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'

function json(route, body, status = 200) {
  return route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
}

async function openFiles(page) {
  await page.goto(`/?production_id=${productionId}&task_id=${taskId}`)
  await expect(page.locator('.files-page[data-mode="task"]')).toBeVisible()
}

test('Working snapshot resumes after a deterministic part-transfer failure and finalizes once', async ({ page }) => {
  const storedParts = new Map()
  let failedFirstPart = false
  let finalizeCount = 0
  const file = { id: revisionFileId, name: 'plate.mov', category: 'working', size: 3, sha256, state: 'receiving', upload_session_id: uploadId }
  const revision = { id: revisionId, production_id: productionId, task_id: taskId, version: 1, version_label: 'w001', state: 'receiving', base_revision_id: null, created_by: 'synthetic-artist', files: [file] }
  const session = { id: uploadId, state: 'receiving', expected_size: 3, expected_sha256: sha256, received_size: 0, max_chunk_bytes: 1024, parts: [] }
  let latestWorkingId = null
  let reserved = false

  await page.route('**/api/plugins/vfx_files/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (url.pathname.endsWith('/csrf-token')) return json(route, { csrf_token: 'synthetic-csrf', expires_in: 900 })
    if (url.pathname.endsWith('/overview')) return json(route, { production_id: productionId, tasks: [{ id: taskId, task_type_id: 'Animation', entity_id: 'SHOT010' }], upload_ready: true, publishes: [], handoffs: [] })
    if (url.pathname.endsWith('/output-package-options')) return json(route, { task_id: taskId, output_types: [], storage_root_id: '' })
    if (url.pathname.endsWith('/output-packages')) return json(route, [])
    if (url.pathname.endsWith('/storage-objects')) return json(route, { task_id: taskId, parent_id: null, items: [], next_page_token: null })
    if (url.pathname.endsWith(`/productions/${productionId}/tasks/${taskId}/shared-folders`)) return json(route, { task_id: taskId, folders: [] })
    if (/link-shares|share-candidates/.test(url.pathname)) return json(route, { items: [], shares: [], recipients: [], candidates: [] })
    if (url.pathname.endsWith(`/tasks/${taskId}/working-revisions`) && request.method() === 'GET') return json(route, { task_id: taskId, latest_working_id: latestWorkingId, revisions: reserved ? [revision] : [] })
    if (url.pathname.endsWith(`/tasks/${taskId}/working-revisions`) && request.method() === 'POST') { reserved = true; return json(route, revision, 201) }
    if (url.pathname.endsWith(`/uploads/${uploadId}`) && request.method() === 'GET') return json(route, { ...session, parts: [...storedParts.values()] })
    if (url.pathname.endsWith(`/uploads/${uploadId}/parts/0`) && request.method() === 'PUT') {
      if (!failedFirstPart) {
        failedFirstPart = true
        return json(route, { error: 'synthetic storage interruption' }, 503)
      }
      const body = request.postDataBuffer()
      storedParts.set(0, { index: 0, byte_start: 0, byte_count: body.length, sha256, state: 'staged' })
      session.received_size = body.length
      return json(route, storedParts.get(0))
    }
    if (url.pathname.endsWith(`/uploads/${uploadId}/finalize`) && request.method() === 'POST') {
      finalizeCount += 1
      revision.state = 'transferring'
      return json(route, { job_id: '55555555-5555-4555-8555-555555555555', state: 'queued' }, 202)
    }
    return json(route, { error: 'unexpected test request' }, 500)
  })

  await openFiles(page)
  await page.getByRole('tab', { name: 'Send', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Working Files', exact: true })).toBeVisible()
  const fileInput = page.locator('input[type="file"]')
  await fileInput.setInputFiles({ name: 'plate.mov', mimeType: 'video/quicktime', buffer: Buffer.from('abc') })
  await expect(page.locator('.upload-panel').getByRole('alert')).toContainText('Files is temporarily unavailable')
  await expect(page.getByRole('button', { name: 'Resume upload' })).toBeVisible()
  expect(storedParts.size).toBe(0)
  expect(finalizeCount).toBe(0)

  await page.getByRole('button', { name: 'Resume upload' }).click()
  await fileInput.setInputFiles({ name: 'plate.mov', mimeType: 'video/quicktime', buffer: Buffer.from('abc') })
  await expect(page.getByText(/Working w001 resumed\. 1 transfer job\(s\) queued\./)).toBeVisible()
  expect(storedParts.get(0)).toMatchObject({ byte_count: 3, sha256, state: 'staged' })
  expect(finalizeCount).toBe(1)
})

test('Needs Fix requires feedback and Accepted persists after the page reloads', async ({ page }) => {
  let state = 'pending'
  let responseCount = 0
  await page.route('**/api/plugins/vfx_files/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (url.pathname.endsWith('/csrf-token')) return json(route, { csrf_token: 'synthetic-csrf', expires_in: 900 })
    if (url.pathname.endsWith('/overview')) return json(route, {
      production_id: productionId,
      actor_id: 'synthetic-recipient',
      tasks: [],
      upload_ready: false,
      publishes: [{ id: 'p1', task_id: taskId, output_family_id: 'family1', output_family_name: 'Comp', version: 4, state: 'completed', qc_state: 'passed', latest_published: true, latest_approved: false }, { id: 'p2', task_id: taskId, output_family_id: 'family1', output_family_name: 'Comp', version: 3, state: 'completed', qc_state: 'passed', latest_published: false, latest_approved: true }],
      handoffs: [{ id: handoffId, task_id: taskId, publish_id: 'p1', recipient_id: 'synthetic-recipient', state }]
    })
    if (url.pathname.endsWith('/output-package-options')) return json(route, { task_id: taskId, output_types: [], storage_root_id: '' })
    if (url.pathname.endsWith('/output-packages')) return json(route, [])
    if (url.pathname.endsWith('/storage-objects')) return json(route, { task_id: taskId, parent_id: null, items: [], next_page_token: null })
    if (url.pathname.endsWith(`/productions/${productionId}/tasks/${taskId}/shared-folders`)) return json(route, { task_id: taskId, folders: [] })
    if (/link-shares|share-candidates/.test(url.pathname)) return json(route, { items: [], shares: [], recipients: [], candidates: [] })
    if (url.pathname.endsWith(`/handoffs/${handoffId}/respond`)) {
      const payload = JSON.parse(request.postData())
      expect(payload).toMatchObject({ target: 'accepted', feedback: '' })
      state = payload.target
      responseCount += 1
      return json(route, { id: handoffId, state, publish_id: 'p1' })
    }
    return json(route, { error: 'unexpected test request' }, 500)
  })

  await openFiles(page)
  await page.getByRole('tab', { name: 'Receive', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Awaiting your response', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Needs Fix' })).toBeDisabled()
  await page.getByRole('button', { name: 'Accept' }).click()
  await expect(page.locator('[data-state="accepted"]')).toBeVisible()
  expect(responseCount).toBe(1)

  await page.reload()
  await page.getByRole('tab', { name: 'Receive', exact: true }).click()
  await expect(page.locator('[data-state="accepted"]')).toBeVisible()
})

test('Working revision downloads the exact completed bytes and displays the recorded SHA-256', async ({ page }) => {
  const fileName = 'synthetic-w001.exr'
  await page.route('**/api/plugins/vfx_files/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (url.pathname.endsWith('/csrf-token')) return json(route, { csrf_token: 'synthetic-csrf', expires_in: 900 })
    if (url.pathname.endsWith('/overview')) return json(route, { production_id: productionId, tasks: [{ id: taskId, task_type_id: 'Animation', entity_id: 'SHOT010' }], upload_ready: true, publishes: [], handoffs: [] })
    if (url.pathname.endsWith('/output-package-options')) return json(route, { task_id: taskId, output_types: [], storage_root_id: '' })
    if (url.pathname.endsWith('/output-packages')) return json(route, [])
    if (url.pathname.endsWith('/storage-objects')) return json(route, { task_id: taskId, parent_id: null, items: [], next_page_token: null })
    if (url.pathname.endsWith(`/productions/${productionId}/tasks/${taskId}/shared-folders`)) return json(route, { task_id: taskId, folders: [] })
    if (/link-shares|share-candidates/.test(url.pathname)) return json(route, { items: [], shares: [], recipients: [], candidates: [] })
    if (url.pathname.endsWith(`/tasks/${taskId}/working-revisions`) && request.method() === 'GET') {
      const revision = { id: revisionId, production_id: productionId, task_id: taskId, version: 1, version_label: 'w001', state: 'completed', files: [{ id: revisionFileId, name: fileName, category: 'working', size: 3, sha256, state: 'completed' }] }
      return json(route, { task_id: taskId, latest_working_id: revisionId, revisions: [revision] })
    }
    if (url.pathname.endsWith(`/working-files/${revisionFileId}/download`)) return route.fulfill({ status: 200, contentType: 'application/octet-stream', body: Buffer.from('abc') })
    return json(route, { error: 'unexpected test request' }, 500)
  })

  await openFiles(page)
  const panel = page.locator('.upload-panel')
  const link = panel.getByRole('link', { name: fileName, exact: true })
  await expect(link).toBeVisible()
  await expect(panel.locator('.revision-file small')).toContainText(sha256.slice(0, 12))
  const result = await link.evaluate(async (anchor) => {
    const response = await fetch(anchor.href)
    return { status: response.status, bytes: Array.from(new Uint8Array(await response.arrayBuffer())) }
  })
  expect(result).toEqual({ status: 200, bytes: [97, 98, 99] })
})

test('Production settings expose the Plugin Drive authority without a Task selector', async ({ page }) => {
  await page.route('**/api/plugins/vfx_files/**', async (route) => {
    const url = new URL(route.request().url())
    if (url.pathname.endsWith('/drive/status')) return json(route, {
      production_id: productionId,
      owner: 'plugin',
      state: 'unconfigured',
      credential_state: 'none',
      root_state: 'unconfigured',
      activation_enabled: false,
      activation_transition_pending: false,
      config_revision: 0
    })
    if (url.pathname.endsWith('/drive/configuration')) return json(route, { production_id: productionId, configured: false })
    if (url.pathname.endsWith('/drive/connections')) return json(route, { production_id: productionId, connections: [] })
    return json(route, { error: 'unexpected request' }, 500)
  })

  await page.goto(`/?production_id=${productionId}`)
  await expect(page.locator('.files-page[data-mode="admin"]')).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Google Drive', exact: true })).toBeVisible()
  await expect(page.getByText('VFX Files Plugin', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Connect Google Drive', exact: true })).toBeVisible()
  await expect(page.getByLabel('Choose Task')).toHaveCount(0)
  await expect(page.getByText(/Folder template setup/)).toHaveCount(0)
})
