import { createHash } from 'node:crypto'
import { expect, test } from '@playwright/test'

const baseUrl = process.env.VFX_FILES_RESTORE_BASE_URL
const productionId = process.env.VFX_FILES_RESTORE_PRODUCTION_ID
const taskId = process.env.VFX_FILES_RESTORE_TASK_ID
const artistToken = process.env.VFX_FILES_RESTORE_ARTIST_TOKEN
const artistId = process.env.VFX_FILES_RESTORE_ARTIST_ID
const publishId = process.env.VFX_FILES_RESTORE_PUBLISH_ID
const cookieName = process.env.VFX_FILES_RESTORE_COOKIE_NAME || 'access_token_cookie'
const expectedW001 = process.env.VFX_FILES_RESTORE_W001_SHA256
const expectedW002 = process.env.VFX_FILES_RESTORE_W002_SHA256
const missingRestoreFixture = !baseUrl || !productionId || !taskId || !artistToken || !artistId || !publishId || !expectedW001 || !expectedW002

test('restored installed Files page reloads Working and Publish pointers and denies unauthorized approval', async ({ browser }) => {
  test.skip(missingRestoreFixture, 'Requires the disposable restored Kitsu/Zou snapshot and synthetic artist identity.')
  test.setTimeout(60_000)
  const origin = new URL(baseUrl).origin
  const context = await browser.newContext()
  await context.addCookies([{ name: cookieName, value: artistToken, url: origin }])
  const page = await context.newPage()
  const overviewResponse = page.waitForResponse((response) => response.url().includes('/api/plugins/vfx_files/overview'))
  await page.goto(`${baseUrl}?production_id=${encodeURIComponent(productionId)}`)
  expect((await overviewResponse).status()).toBe(200)
  await expect(page.getByRole('heading', { name: 'Files', level: 1, exact: true })).toBeVisible()
  await expect(page.locator('.published-card strong')).toHaveText('1')
  await expect(page.locator('.approved-card strong')).toHaveText('1')

  await page.getByLabel('Choose Task').selectOption(taskId)
  const revisions = page.locator('.revision-list > li')
  await expect(revisions).toHaveCount(2)
  await expect(page.locator('.revision-heading')).toContainText('w002')
  await expect(revisions.nth(0).locator('.revision-version')).toHaveText('w002')
  await expect(revisions.nth(1).locator('.revision-version')).toHaveText('w001')

  const revisionDownloads = await page.locator('.revision-list > li').evaluateAll((rows) => rows.map((row) => {
    const link = row.querySelector('.revision-file a')
    return { version: row.querySelector('.revision-version')?.textContent?.trim(), href: link?.getAttribute('href') }
  }))
  for (const [version, expected] of [['w001', expectedW001], ['w002', expectedW002]]) {
    const link = revisionDownloads.find((item) => item.version === version)?.href
    expect(link).toBeTruthy()
    const response = await context.request.get(new URL(link, origin).toString())
    expect(response.status()).toBe(200)
    expect(createHash('sha256').update(await response.body()).digest('hex')).toBe(expected)
  }

  const csrfToken = await page.evaluate(async () => (await (await fetch('/api/plugins/vfx_files/csrf-token', { credentials: 'same-origin' })).json()).csrf_token)
  const deniedApproval = await context.request.post(`${origin}/api/plugins/vfx_files/publishes/${encodeURIComponent(publishId)}/approvals`, {
    headers: { Origin: origin, 'X-VFX-Files-CSRF': csrfToken, 'Idempotency-Key': `restore-denied-approval-${publishId}` },
    data: { policy_id: 'files-page-review-v1', policy_sha256: '9a3d8d0e6a4d690ad97f6f6a7fcfae135c7fc2e0bd23a5e5e5bf30a55a3d93c1' }
  })
  expect(deniedApproval.status()).toBe(403)

  await page.reload()
  await expect(page.getByRole('heading', { name: 'Files', level: 1, exact: true })).toBeVisible()
  await expect(page.locator('.published-card strong')).toHaveText('1')
  await expect(page.locator('.approved-card strong')).toHaveText('1')
  await page.getByLabel('Choose Task').selectOption(taskId)
  await expect(page.locator('.revision-heading')).toContainText('w002')
  await expect(page.locator('.revision-list > li')).toHaveCount(2)
  await context.close()
})
