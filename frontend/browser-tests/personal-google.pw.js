import { expect, test } from '@playwright/test'

const productionId = '11111111-1111-4111-8111-111111111111'
const taskId = '22222222-2222-4222-8222-222222222222'
const googleAuthUrl = 'https://accounts.google.com/o/oauth2/v2/auth?client_id=synthetic&state=synthetic-state'

function json(route, body, status = 200) {
  return route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
}

async function mockPlugin(page, { admin = false, state = 'not_linked', allowed = true } = {}) {
  const seen = { authorizations: [], policies: [], disconnects: 0, leaked: [] }
  await page.route('https://accounts.google.com/**', (route) => route.fulfill({ status: 200, contentType: 'text/html', body: '<h1>synthetic google</h1>' }))
  await page.route('**/api/plugins/vfx_files/**', async (route) => {
    const request = route.request()
    const { pathname } = new URL(request.url())
    if (pathname.endsWith('/csrf-token')) return json(route, { csrf_token: 'synthetic-csrf', expires_in: 900 })
    if (pathname.endsWith(`/productions/${productionId}/personal-google`) && request.method() === 'GET') {
      return json(route, { policy: allowed ? 'allowed' : 'blocked', allowed, can_change_policy: admin, linked: state === 'active', state,
        account_hint: state === 'not_linked' ? undefined : '…1234' })
    }
    if (pathname.endsWith('/personal-google/authorizations')) {
      seen.authorizations.push({ csrf: request.headers()['x-vfx-files-csrf'], key: request.headers()['idempotency-key'] })
      return json(route, { authorization_id: 'a-1', authorization_url: googleAuthUrl }, 201)
    }
    if (pathname.endsWith('/personal-google-policy')) {
      seen.policies.push(JSON.parse(request.postData() || '{}'))
      return json(route, { policy: seen.policies.at(-1).policy })
    }
    if (pathname.endsWith('/me/google-account/disconnect')) { seen.disconnects += 1; return json(route, { state: 'revoked' }) }
    if (pathname.endsWith('/overview')) return json(route, { production_id: productionId, task_id: taskId, actor_id: 'a', tasks: [{ id: taskId, task_type_id: 't', entity_id: 'e' }], upload_ready: false, publishes: [], handoffs: [] })
    if (pathname.endsWith('/output-package-options')) return json(route, { task_id: taskId, output_types: [], storage_root_id: '' })
    if (pathname.endsWith('/output-packages')) return json(route, [])
    if (pathname.endsWith('/shared-folders')) return json(route, { task_id: taskId, folders: [] })
    if (pathname.endsWith('/storage-objects')) return json(route, { task_id: taskId, parent_id: null, items: [], next_page_token: null })
    if (pathname.endsWith('/link-shares') || pathname.endsWith('/received-link-shares') || pathname.endsWith('/share-candidates')) return json(route, { items: [], shares: [], recipients: [], candidates: [] })
    // Settings-page admin/Production APIs a normal member is not allowed to read.
    if (/drive-access-policy|output-delete-policy/.test(pathname)) return json(route, { checks: [], policy: 'registrant_and_admin', policies: [], can_change: false })
    return json(route, { error: true, message: 'forbidden' }, 403)
  })
  page.on('request', (request) => { if (/refresh_token|access_token/i.test(request.url() + (request.postData() || ''))) seen.leaked.push(request.url()) })
  return seen
}

test('a normal Production member sees only their own Google link controls, and connect goes to Google', async ({ page }) => {
  const seen = await mockPlugin(page, { admin: false })
  const stored = []
  await page.exposeFunction('recordStorage', (key, value) => stored.push([key, value]))
  await page.addInitScript(() => {
    const original = Storage.prototype.setItem
    Storage.prototype.setItem = function (key, value) { window.recordStorage(key, value); return original.call(this, key, value) }
  })
  await page.goto(`/?production_id=${productionId}&drive_settings=1`)
  const panel = page.locator('.personal-google')
  await expect(panel).toBeVisible()
  await expect(panel.getByText('Not connected')).toBeVisible()
  await expect(panel.locator('.personal-google-policy')).toHaveCount(0) // no admin controls for members
  await panel.getByRole('button', { name: 'Connect Google account' }).click()
  await page.waitForURL(/accounts\.google\.com\/o\/oauth2\/v2\/auth/)
  expect(seen.authorizations).toHaveLength(1)
  expect(seen.authorizations[0].csrf).toBe('synthetic-csrf')
  expect(seen.authorizations[0].key).toMatch(/^personal-google:/)
  expect(stored).toContainEqual(['vfx-files-plugin-drive-production-id', productionId]) // restored on return
  expect(seen.leaked).toEqual([])
})

test('returning from Google restores the Production and shows the result', async ({ page }) => {
  await mockPlugin(page, { state: 'active' })
  await page.addInitScript((id) => window.sessionStorage.setItem('vfx-files-plugin-drive-production-id', id), productionId)
  await page.goto('/?authorization_id=a-1&personal_google=connected')
  const panel = page.locator('.personal-google')
  await expect(panel.getByText('Google account connected.')).toBeVisible()
  await expect(panel.getByText('Connected', { exact: true })).toBeVisible()
  await expect(panel.getByText('…1234')).toBeVisible()
})

test('a failed return shows a readable error and keeps the Connect action', async ({ page }) => {
  await mockPlugin(page)
  await page.addInitScript((id) => window.sessionStorage.setItem('vfx-files-plugin-drive-production-id', id), productionId)
  await page.goto('/?authorization_id=a-1&personal_google=failed&error=account_mismatch')
  const panel = page.locator('.personal-google')
  await expect(panel.getByRole('alert')).toContainText('different Kitsu user')
  await expect(panel.getByRole('button', { name: 'Connect Google account' })).toBeVisible()
})

test('blocked Production: members cannot start a link but can disconnect; admins see and save the setting', async ({ page }) => {
  const seen = await mockPlugin(page, { admin: false, state: 'active', allowed: false })
  await page.goto(`/?production_id=${productionId}&drive_settings=1`)
  const panel = page.locator('.personal-google')
  await expect(panel.getByText('turned off personal Google accounts')).toBeVisible()
  await expect(panel.getByRole('button', { name: /Connect Google account/ })).toHaveCount(0)
  await panel.getByRole('button', { name: 'Disconnect' }).click()
  await expect.poll(() => seen.disconnects).toBe(1)

  const adminPage = await page.context().newPage()
  const adminSeen = await mockPlugin(adminPage, { admin: true })
  await adminPage.goto(`/?production_id=${productionId}&drive_settings=1`)
  const adminPanel = adminPage.locator('.personal-google')
  await adminPanel.getByLabel(/Block/).check()
  await adminPanel.getByRole('button', { name: 'Save' }).click()
  await expect.poll(() => adminSeen.policies).toEqual([{ policy: 'blocked' }])
})

test('the personal Google control is also reachable from the Task screen', async ({ page }) => {
  await mockPlugin(page)
  await page.goto(`/?production_id=${productionId}&task_id=${taskId}`)
  await expect(page.locator('.files-page[data-mode="task"]')).toBeVisible()
  await page.locator('details.task-personal-google > summary').click()
  await expect(page.locator('.task-personal-google .personal-google').getByText('Not connected')).toBeVisible()
})
