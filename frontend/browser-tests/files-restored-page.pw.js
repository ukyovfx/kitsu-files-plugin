import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { expect, test } from '@playwright/test'

const base = process.env.VFX_FILES_RESTORE_BASE_URL
const productionId = process.env.VFX_FILES_RESTORE_PRODUCTION_ID
const taskId = process.env.VFX_FILES_RESTORE_TASK_ID
const host = process.env.VFX_FILES_RESTORE_HOST_CONTAINER
const missingFixture = !base || !productionId || !taskId || !host

function mintSyntheticArtistToken() {
  const python = [
    'import json, os',
    'from uuid import UUID',
    'from flask_jwt_extended import create_access_token, get_jti',
    'from zou.app import create_app',
    'from zou.app.models.person import Person',
    'from zou.app.models.task import Task, TaskPersonLink',
    'from seeder_safety import create_short_lived_person_token, token_has_short_expiry',
    'app = create_app()',
    'with app.app_context():',
    ' task = Task.query.filter_by(id=UUID(os.environ["VFX_TARGET_TASK"])).one()',
    ' person = Person.query.join(TaskPersonLink, TaskPersonLink.person_id == Person.id).filter(TaskPersonLink.task_id == task.id, Person.email.like("artist-b-%@example.invalid")).one()',
    ' token = create_short_lived_person_token(person, create_access_token, get_jti)',
    ' if not token_has_short_expiry(token): raise RuntimeError("short expiry check failed")',
    ' print(json.dumps({"token": token, "cookie_name": app.config.get("JWT_ACCESS_COOKIE_NAME", "access_token_cookie")}))'
  ].join('\n')

  try {
    const output = execFileSync('docker', [
      'exec',
      '-e', 'SECRET_KEY=vfx-files-isolated-test-secret-key-32-bytes-minimum',
      '-e', 'PLUGIN_FOLDER=/opt/zou/plugins',
      '-e', 'PYTHONPATH=/opt/zou/plugins:/tmp',
      '-e', `VFX_TARGET_TASK=${taskId}`,
      host,
      '/opt/zou/env/bin/python', '-c', python
    ], { encoding: 'utf8', timeout: 30_000, stdio: ['ignore', 'pipe', 'ignore'] })
    const identity = JSON.parse(output)
    if (!identity.token || !identity.cookie_name) throw new Error('incomplete identity')
    return identity
  } catch {
    throw new Error('Unable to mint a short-lived synthetic restore identity')
  }
}

test('restored Files page keeps Working, Publish, Approval and bytes readable', async ({ browser }) => {
  test.skip(missingFixture, 'Requires a verified isolated restore fixture and synthetic artist token.')

  const identity = mintSyntheticArtistToken()
  const origin = new URL(base).origin
  const context = await browser.newContext()
  await context.addCookies([{ name: identity.cookie_name, value: identity.token, url: origin }])
  const page = await context.newPage()
  const apiBase = `${origin}/api/plugins/vfx_files`

  await page.goto(`${apiBase}/frontend/index.html?production_id=${encodeURIComponent(productionId)}`)
  await expect(page.getByRole('heading', { name: 'Files', level: 1, exact: true })).toBeVisible()
  await page.getByLabel('Choose Task').selectOption(taskId)
  await expect(page.locator('.revision-list').getByText('w001', { exact: true })).toBeVisible()
  await expect(page.locator('.revision-list').getByText('w002', { exact: true })).toBeVisible()
  await expect(page.locator('.revision-heading')).toContainText('Latest Working')
  await expect(page.locator('.revision-heading')).toContainText('w002')

  const historicalFiles = [
    ['artist-a-scene-v001.exr', 'verified synthetic Working File revision w001'],
    ['artist-b-scene-v002.exr', 'verified synthetic Working File revision w002 from Artist B']
  ]
  for (const [name, expectedBytes] of historicalFiles) {
    const link = page.getByRole('link', { name, exact: true })
    await expect(link).toBeVisible()
    const response = await page.request.get(new URL(await link.getAttribute('href'), page.url()).toString())
    expect(response.status()).toBe(200)
    assert.equal((await response.body()).toString('utf8'), expectedBytes)
  }

  const response = await page.request.get(`${apiBase}/overview?production_id=${encodeURIComponent(productionId)}`)
  expect(response.status()).toBe(200)
  const overview = await response.json()
  const publish = overview.publishes.find((row) => row.task_id === taskId)
  expect(publish).toMatchObject({ state: 'completed', qc_state: 'passed', latest_published: true, latest_approved: true })
  expect(publish.native_output_file_id).toBeTruthy()

  await context.close()
})
