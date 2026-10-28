import { expect, test } from '@playwright/test'

const productionId = '11111111-1111-4111-8111-111111111111'
const sourceTaskId = '22222222-2222-4222-8222-222222222222'
const recipientTaskId = '33333333-3333-4333-8333-333333333333'
const objectId = 'synthetic-original-drive-file'
const shareTargetId = '44444444-4444-4444-8444-444444444444'
const objectUrl = `https://drive.google.com/file/d/${objectId}/view`

test('Task Files UI shares an original Drive reference and displays it in Received Files', async ({ page }) => {
  let shareRequest = null
  let shareSaved = false
  const shareAttempts = []
  const providerMutationPaths = []

  await page.route('**/api/plugins/vfx_files/**', async route => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname

    if (/copy|move|permission/i.test(path)) providerMutationPaths.push(path)
    if (path.endsWith('/csrf-token')) {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ csrf_token: 'synthetic-csrf', expires_in: 900 }) })
    }
    if (path.endsWith('/overview')) {
      const currentTaskId = url.searchParams.get('task_id')
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
        production_id: productionId,
        task_id: currentTaskId,
        actor_id: 'synthetic-artist',
        tasks: [{ id: currentTaskId, task_type_id: 'compositing', entity_id: currentTaskId === sourceTaskId ? 'SH010' : 'SH020' }],
        upload_ready: false,
        publishes: [],
        handoffs: []
      }) })
    }
    if (path.endsWith(`/productions/${productionId}/tasks/${sourceTaskId}/shared-folders`) || path.endsWith(`/productions/${productionId}/tasks/${recipientTaskId}/shared-folders`)) {
      const currentTaskId = path.includes(recipientTaskId) ? recipientTaskId : sourceTaskId
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ task_id: currentTaskId, folders: [] }) })
    }
    const taskId = [sourceTaskId, recipientTaskId].find(id => path.includes(`/tasks/${id}/`))
    if (taskId && path.endsWith('/output-package-options')) {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ task_id: taskId, output_types: [], storage_root_id: '' }) })
    }
    if (taskId && path.endsWith('/output-packages')) {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) })
    }
    if (taskId && path.endsWith('/storage-objects')) {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
        task_id: taskId,
        parent_id: url.searchParams.get('parent_id'),
        items: taskId === sourceTaskId ? [{ id: objectId, name: 'plate.exr', is_folder: false, mime_type: 'image/x-exr' }] : [],
        next_page_token: null
      }) })
    }
    if (path.endsWith(`/tasks/${sourceTaskId}/share-candidates`)) {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
        task_id: sourceTaskId,
        recipients: [{ task_id: recipientTaskId, entity_name: 'SH020', task_type_name: 'Compositing', department_name: 'Comp' }]
      }) })
    }
    if (path.endsWith(`/tasks/${sourceTaskId}/link-shares`) && request.method() === 'POST') {
      shareRequest = {
        body: request.postDataJSON(),
        csrf: request.headers()['x-vfx-files-csrf'],
        idempotencyKey: request.headers()['idempotency-key']
      }
      shareAttempts.push(shareRequest)
      if (shareAttempts.length === 1) {
        return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: 'synthetic temporary failure' }) })
      }
      shareSaved = true
      return route.fulfill({ status: 202, contentType: 'application/json', body: JSON.stringify({
        targets: [{ share_target_id: shareTargetId, recipient_task_id: recipientTaskId, notification_status: 'pending' }]
      }) })
    }
    if (path.endsWith(`/tasks/${sourceTaskId}/link-shares`)) {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
        task_id: sourceTaskId,
        shares: shareSaved ? [{
          share_target_id: shareTargetId,
          recipient_task_id: recipientTaskId,
          object: { object_id: objectId, url: objectUrl },
          metadata: { name: 'plate.exr' },
          recipient: { task_type_name: 'Compositing' },
          notification_status: 'pending'
        }] : []
      }) })
    }
    if (path.endsWith(`/tasks/${recipientTaskId}/received-link-shares`)) {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
        task_id: recipientTaskId,
        received_files: [{
          share_target_id: shareTargetId,
          object: { object_id: objectId, object_type: 'file', url: objectUrl },
          metadata: { name: 'plate.exr' },
          source: { entity_name: 'SH010', task_type_name: 'Compositing', task_id: sourceTaskId },
          notification_status: 'pending'
        }]
      }) })
    }
    return route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ error: `Unexpected synthetic request: ${request.method()} ${path}` }) })
  })

  await page.goto(`/?production_id=${productionId}&task_id=${sourceTaskId}`)
  await expect(page.locator('.files-page[data-mode="task"]')).toBeVisible()
  await expect(page.getByRole('tab', { name: 'Send', exact: true })).toBeVisible()
  await page.getByRole('tab', { name: 'Send', exact: true }).click()

  await page.getByLabel('Select plate.exr as original reference').check()
  await page.getByLabel('SH020 · Compositing · Comp').check()
  await page.getByRole('button', { name: 'Share reference', exact: true }).click()
  await expect(page.locator('.task-link-share').getByRole('alert')).toBeVisible()
  await expect(page.getByLabel('Select plate.exr as original reference')).toBeChecked()
  await expect(page.getByLabel('SH020 · Compositing · Comp')).toBeChecked()
  await page.getByRole('button', { name: 'Share reference', exact: true }).click()
  await expect(page.locator('.task-link-share .upload-result')).toContainText('Reference saved')
  await expect(page.getByRole('link', { name: 'plate.exr', exact: true })).toHaveAttribute('href', objectUrl)

  expect(shareRequest).toMatchObject({
    body: { object_id: objectId, recipient_task_ids: [recipientTaskId] },
    csrf: 'synthetic-csrf'
  })
  expect(shareRequest.idempotencyKey).toBeTruthy()
  expect(shareAttempts).toHaveLength(2)
  expect(shareAttempts[1].idempotencyKey).toBe(shareAttempts[0].idempotencyKey)
  expect(providerMutationPaths).toEqual([])

  await page.goto(`/?production_id=${productionId}&task_id=${recipientTaskId}`)
  await expect(page.locator('.files-page[data-mode="task"]')).toBeVisible()
  await page.getByRole('tab', { name: 'Receive', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Received Files', exact: true })).toBeVisible()
  await expect(page.getByRole('link', { name: 'plate.exr', exact: true })).toHaveAttribute('href', objectUrl)
  await expect(page.getByText('From SH010 · Compositing')).toBeVisible()
})
