import { expect, test } from '@playwright/test'

const productionId = '11111111-1111-4111-8111-111111111111'
const taskId = '22222222-2222-4222-8222-222222222222'
const handoffId = '33333333-3333-4333-8333-333333333333'
const uploadId = '44444444-4444-4444-8444-444444444444'
const sha256 = 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'

function json(route, body, status = 200) {
  return route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
}

async function openFiles(page) {
  await page.goto(`/?production_id=${productionId}`)
  await expect(page.getByRole('heading', { name: 'Files', level: 1, exact: true })).toBeVisible()
}

test('upload resumes after a deterministic part-transfer failure and finalizes once', async ({ page }) => {
  const storedParts = new Map()
  let failedFirstPart = false
  let finalizeCount = 0
  const session = { id: uploadId, state: 'receiving', expected_size: 3, expected_sha256: sha256, received_size: 0, max_chunk_bytes: 1024, parts: [] }

  await page.route('**/api/plugins/vfx_files/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (url.pathname.endsWith('/csrf-token')) return json(route, { csrf_token: 'synthetic-csrf', expires_in: 900 })
    if (url.pathname.endsWith('/overview')) return json(route, { production_id: productionId, tasks: [{ id: taskId, task_type_id: 'Animation', entity_id: 'SHOT010' }], upload_ready: true, publishes: [], handoffs: [] })
    if (url.pathname.endsWith(`/tasks/${taskId}/uploads`) && request.method() === 'GET') return json(route, [session])
    if (url.pathname.endsWith(`/uploads/${uploadId}`) && request.method() === 'GET') return json(route, { ...session, parts: [...storedParts.values()] })
    if (url.pathname.endsWith(`/tasks/${taskId}/uploads`) && request.method() === 'POST') return json(route, session, 201)
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
      return json(route, { job_id: '55555555-5555-4555-8555-555555555555', state: 'queued' }, 202)
    }
    return json(route, { error: 'unexpected test request' }, 500)
  })

  await openFiles(page)
  await page.getByLabel('Choose Task').selectOption(taskId)
  const fileInput = page.locator('input[type="file"]')
  await fileInput.setInputFiles({ name: 'plate.mov', mimeType: 'video/quicktime', buffer: Buffer.from('abc') })
  await expect(page.getByRole('alert')).toContainText('Files is temporarily unavailable')
  expect(storedParts.size).toBe(0)
  expect(finalizeCount).toBe(0)

  await fileInput.setInputFiles({ name: 'plate.mov', mimeType: 'video/quicktime', buffer: Buffer.from('abc') })
  await expect(page.getByText(/Source staged\. Job .* is queued/)).toBeVisible()
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
