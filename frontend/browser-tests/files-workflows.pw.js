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
      actor_id: 'synthetic-recipient',
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

test('Production Files page keeps configuration in KitsuSync and creates a Task-scoped Output Package from a selected Working revision', async ({ page }) => {
  let submitted = null
  let deliveryRequest = null
  let deliveries = []
  let packages = []
  let incomingState = 'available'
  const sourceRevision = '88888888-8888-4888-8888-888888888888'
  await page.route('**/api/plugins/vfx_files/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (url.pathname.endsWith('/csrf-token')) return json(route, { csrf_token: 'synthetic-csrf', expires_in: 900 })
    if (url.pathname.endsWith('/overview')) return json(route, { production_id: productionId, tasks: [{ id: taskId, name: 'main', task_type_id: 'comp', task_type_name: 'Compositing', entity_id: 'shot-1', entity_name: 'SH010' }], upload_ready: false, publishes: [{ id: 'publish-1', task_id: taskId, version: 3, state: 'completed', qc_state: 'passed', latest_approved: true }], handoffs: [] })
    if (url.pathname.endsWith(`/tasks/${taskId}/output-package-options`)) return json(route, { task_id: taskId, production_id: productionId, storage_root_id: 'authorized-root', output_types: [{ id: 'output-type-1', name: 'EXR Sequence' }] })
    if (url.pathname.endsWith(`/tasks/${taskId}/output-packages`)) {
      if (request.method() === 'POST') {
        submitted = JSON.parse(request.postData())
        packages = [{
          id: 'package-1', task_id: taskId, version: 1, package_type: 'final-exr',
          state: 'completed', qc_state: 'passed', file_count: 1, manifest_sha256: 'a'.repeat(64),
          qc_report: null, approved_publish_candidates: [{ id: 'publish-1', version: 3 }],
          formal_publish: null, latest_working: true, latest_published: false, latest_approved: false
        }]
        return json(route, { id: 'package-1', version: 1, state: 'queued' }, 202)
      }
      return json(route, packages)
    }
    if (url.pathname.endsWith('/publication-binding') && request.method() === 'POST') {
      expect(JSON.parse(request.postData())).toEqual({ publish_id: 'publish-1' })
      packages = packages.map((item) => ({ ...item, formal_publish: { id: 'publish-1', version: 3, state: 'completed', qc_state: 'passed', approval_active: true }, latest_published: true, latest_approved: true }))
      return json(route, { id: 'binding-1', package_id: 'package-1', publish_id: 'publish-1', replayed: false }, 201)
    }
    if (url.pathname.endsWith(`/tasks/${taskId}/output-package-deliveries`)) return json(route, { task_id: taskId, deliveries })
    if (url.pathname.endsWith('/output-packages/package-1/files')) return json(route, { package_id: 'package-1', files: [{ id: 'package-file-1', file_name: 'plate.1001.exr', frame_number: 1001, byte_count: 3, sha256: 'a'.repeat(64), download_url: '/download' }], next_offset: null })
    if (url.pathname.endsWith('/delivery-targets')) return json(route, { package_id: 'package-1', targets: [
      { task_id: 'fx-task', task_name: 'fx', task_type_id: 'fx-type', task_type_name: 'FX', department_id: 'fx-dept', department_name: 'Effects' },
      { task_id: 'layout-task', task_name: 'layout', task_type_id: 'layout-type', task_type_name: 'Layout', department_id: 'layout-dept', department_name: 'Layout' }
    ] })
    if (url.pathname.endsWith('/deliveries') && request.method() === 'POST') {
      deliveryRequest = JSON.parse(request.postData())
      deliveries = [{
        id: 'delivery-1', direction: 'outgoing', package_id: 'package-1', package_version: 1,
        package_type: 'final-exr', recipient_task_id: deliveryRequest.recipient_task_id,
        recipient_task_type_id: deliveryRequest.recipient_task_type_id,
        recipient_task_type_name: 'FX', revision: 1, state: 'available', can_respond: false,
        history: [{ event_type: 'delivered' }]
      }, {
        id: 'incoming-delivery-1', direction: 'incoming', package_id: 'package-1', package_version: 1,
        package_type: 'final-exr', recipient_task_id: taskId, recipient_task_type_id: 'comp',
        recipient_task_type_name: 'Compositing', revision: 1, state: incomingState, can_respond: incomingState === 'available',
        history: [{ event_type: incomingState === 'accepted' ? 'accepted' : 'delivered' }]
      }]
      return json(route, { id: 'delivery-1', state: 'available', revision: 1 }, 201)
    }
    if (url.pathname.endsWith('/output-package-deliveries/incoming-delivery-1/respond')) {
      incomingState = JSON.parse(request.postData()).status
      deliveries = deliveries.map((item) => item.id === 'incoming-delivery-1' ? {
        ...item, state: incomingState, can_respond: false, history: [{ event_type: 'delivered' }, { event_type: incomingState }]
      } : item)
      return json(route, { id: 'incoming-delivery-1', state: incomingState, revision: 1 })
    }
    if (url.pathname.endsWith(`/tasks/${taskId}/working-revisions`)) return json(route, { task_id: taskId, latest_working_id: 'working-2', revisions: [
      { id: sourceRevision, version_label: 'w001', state: 'completed', created_by: 'artist-a' },
      { id: 'working-2', version_label: 'w002', state: 'completed', created_by: 'artist-b' }
    ] })
    if (url.pathname.endsWith(`/tasks/${taskId}/storage-objects`)) return json(route, { task_id: taskId, parent_id: 'authorized-root', checked_at: '2026-10-14T00:00:00Z', sync_status: 'verified', items: [{ id: 'drive-object-1', name: 'plate.1001.exr', is_folder: false, size: 3, modified_time: null }], next_page_token: null })
    return json(route, { error: 'unexpected test request' }, 500)
  })

  await openFiles(page)
  await expect(page.getByRole('button', { name: /Folder template setup/ })).toHaveCount(0)
  await expect(page.getByLabel('Output Package Task').locator(`option[value="${taskId}"]`)).toHaveText('SH010 · Compositing · main')
  await page.getByLabel('Output Package Task').selectOption(taskId)
  await expect(page.getByLabel('Source Working Revision')).toHaveValue('working-2')
  await page.getByLabel('Source Working Revision').selectOption(sourceRevision)
  await page.getByLabel('Package kind').selectOption('final-exr')
  await page.getByLabel('plate.1001.exr').check()
  await page.getByLabel('Frame start').fill('1001')
  await page.getByLabel('Frame end').fill('1001')
  await page.getByLabel('Frame number plate.1001.exr').fill('1001')
  await page.getByRole('button', { name: /Register 1 selected file/ }).click()
  await expect.poll(() => submitted).toMatchObject({
    output_type_id: 'output-type-1',
    package_type: 'final-exr',
    source_working_revision_id: sourceRevision,
    frame_start: 1001,
    frame_end: 1001,
    files: [{ provider_object_id: 'drive-object-1', frame_number: 1001 }]
  })
  await expect(page.getByRole('article').filter({ hasText: 'final-exr v001' })).toBeVisible()
  await expect(page.getByRole('combobox', { name: /Approved Publish for/ })).toHaveValue('publish-1')
  await page.getByRole('button', { name: 'Confirm Package / Publish match' }).click()
  await expect(page.getByText(/Formally Published v003/)).toBeVisible()
  await page.getByRole('button', { name: 'Choose delivery recipient' }).click()
  const recipientSelect = page.getByRole('combobox', { name: /Delivery recipient for/ })
  await expect(recipientSelect).toBeVisible()
  await expect(recipientSelect.locator('option')).toHaveCount(2)
  await expect(recipientSelect.locator('option').filter({ hasText: 'Effects · FX · fx' })).toHaveCount(1)
  await recipientSelect.selectOption('fx-task:fx-type')
  await page.getByRole('button', { name: 'Send immutable package version' }).click()
  await expect.poll(() => deliveryRequest).toEqual({ recipient_task_id: 'fx-task', recipient_task_type_id: 'fx-type' })
  await expect(page.getByText('Sent · final-exr v001')).toBeVisible()
  await expect(page.getByText('FX · available · revision 1')).toBeVisible()
  const incoming = page.locator('.delivery-row').filter({ hasText: 'Incoming · final-exr v001' })
  await expect(incoming).toBeVisible()
  await incoming.getByRole('button', { name: 'View / download delivered files' }).click()
  await expect(incoming.getByText(/plate\.1001\.exr/)).toBeVisible()
  await expect(incoming.getByRole('link', { name: 'Download verified file' })).toHaveAttribute(
    'href', '/api/plugins/vfx_files/output-package-files/package-file-1/download'
  )
  await incoming.getByRole('button', { name: 'Accept' }).click()
  await expect(incoming).toContainText('accepted')
})
