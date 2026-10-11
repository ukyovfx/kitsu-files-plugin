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
  await page.goto(`/?production_id=${productionId}`)
  await expect(page.getByRole('heading', { name: 'Files', level: 1, exact: true })).toBeVisible()
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
  await page.getByLabel('Choose Task').selectOption(taskId)
  const fileInput = page.locator('input[type="file"]')
  await fileInput.setInputFiles({ name: 'plate.mov', mimeType: 'video/quicktime', buffer: Buffer.from('abc') })
  await expect(page.getByRole('alert')).toContainText('Files is temporarily unavailable')
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
      tasks: [],
      upload_ready: false,
      publishes: [{ id: 'p1', task_id: taskId, output_family_id: 'family1', output_family_name: 'Comp', version: 4, state: 'completed', qc_state: 'passed', latest_published: true, latest_approved: false }, { id: 'p2', task_id: taskId, output_family_id: 'family1', output_family_name: 'Comp', version: 3, state: 'completed', qc_state: 'passed', latest_published: false, latest_approved: true }],
      handoffs: [{ id: handoffId, task_id: taskId, publish_id: 'p1', recipient_id: 'synthetic-recipient', state }]
    })
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
  await expect(page.getByText('Latest Published')).toHaveCount(2)
  await expect(page.getByRole('button', { name: 'Needs Fix' })).toBeDisabled()
  await page.getByRole('button', { name: 'Accept' }).click()
  await expect(page.locator('[data-state="accepted"]')).toBeVisible()
  expect(responseCount).toBe(1)

  await page.reload()
  await expect(page.locator('[data-state="accepted"]')).toBeVisible()
  await expect(page.getByText('Latest Published')).toHaveCount(2)
})

test('template setup stays disabled until activation and refreshes folder bindings', async ({ page }) => {
  let config = { configured: false, enabled: false }
  let bindingsReady = false
  const tree = { folder_id: 'template-root', name: 'Studio Shared', children: [
    { folder_id: 'shot-role', name: '{shot}', children: [{ folder_id: 'task-role', name: '{task}', children: [] }] }
  ] }
  await page.route('**/api/plugins/vfx_files/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (url.pathname.endsWith('/csrf-token')) return json(route, { csrf_token: 'synthetic-csrf', expires_in: 900 })
    if (url.pathname.endsWith('/overview')) return json(route, { production_id: productionId, tasks: [{ id: taskId, task_type_id: 'comp', entity_id: 'shot-1' }], upload_ready: false, publishes: [], handoffs: [] })
    if (url.pathname.endsWith('/shared-template/candidates')) return json(route, { templates: [tree] })
    if (url.pathname.endsWith('/shared-template') && request.method() === 'GET') return json(route, config)
    if (url.pathname.endsWith('/shared-template') && request.method() === 'POST') {
      const payload = JSON.parse(request.postData())
      expect(payload).toMatchObject({ template_folder_id: 'template-root', role_mappings: { shot: 'shot-role', task: 'task-role' } })
      config = { configured: true, enabled: false, version: 1, allowed_root_id: 'authorized-root', template_folder_id: 'template-root', template_tree: tree, role_mappings: payload.role_mappings }
      return json(route, { id: 'config-1', version: 1, enabled: false, template_folder_id: 'template-root' }, 201)
    }
    if (url.pathname.endsWith('/shared-template/activate')) {
      config.enabled = JSON.parse(request.postData()).enabled
      return json(route, { id: 'config-1', version: 1, enabled: config.enabled })
    }
    if (url.pathname.endsWith('/shared-folders') && request.method() === 'POST') return json(route, { job_id: 'job-1', state: 'queued' }, 202)
    if (url.pathname.endsWith('/shared-folders') && request.method() === 'GET') {
      const state = bindingsReady ? 'ready' : 'pending'
      return json(route, { configured: true, enabled: true, folders: [
        { scope_key: 'shot-1', name: 'sh010', state, url: bindingsReady ? 'https://drive.google.com/drive/folders/folder-1' : null },
        { scope_key: 'task-1', name: 'comp', state, url: bindingsReady ? 'https://drive.google.com/drive/folders/folder-2' : null }
      ] })
    }
    return json(route, { error: 'unexpected test request' }, 500)
  })

  await openFiles(page)
  await page.getByRole('button', { name: /Folder template setup/ }).click()
  await page.getByLabel('Google Drive root URL').fill('https://drive.google.com/drive/folders/authorized-root')
  await page.getByRole('button', { name: 'Discover' }).click()
  await page.getByLabel('Folder template', { exact: true }).selectOption('template-root')
  await page.getByLabel('Shot mapping').selectOption('shot-role')
  await page.getByLabel('Task mapping').selectOption('task-role')
  await page.getByRole('button', { name: 'Save disabled configuration' }).click()
  await expect(page.getByText('Configuration v1 · disabled')).toBeVisible()
  await page.getByRole('button', { name: 'Activate provisioning' }).click()
  await page.getByLabel('Provision Task folders').selectOption(taskId)
  await page.getByRole('button', { name: 'Create or reuse' }).click()
  await expect(page.getByText('Folder job queued: job-1')).toBeVisible()
  await expect(page.getByText('Waiting for worker')).toHaveCount(2)
  bindingsReady = true
  await page.getByRole('button', { name: 'Refresh bindings' }).click()
  await expect(page.getByRole('link', { name: 'Open folder' })).toHaveCount(2)
})
