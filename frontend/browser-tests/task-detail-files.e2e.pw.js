import { expect, test } from '@playwright/test'
import { execFileSync } from 'node:child_process'

const origin = process.env.VFX_FILES_LIVE_BASE_URL || 'http://127.0.0.1:55253'
const composeProject = process.env.VFX_FILES_LIVE_COMPOSE_PROJECT
const composeFile = process.env.VFX_FILES_LIVE_COMPOSE_FILE
const composeEnvFile = process.env.VFX_FILES_LIVE_COMPOSE_ENV_FILE
const loginEmails = JSON.parse(process.env.VFX_FILES_LIVE_LOGIN_EMAILS || '{}')
const loginPassword = process.env.VFX_FILES_LIVE_TEST_PASSWORD
const productionId = process.env.VFX_FILES_LIVE_PRODUCTION_ID
const taskId = process.env.VFX_FILES_LIVE_TASK_ID
const secondTaskId = process.env.VFX_FILES_LIVE_SECOND_TASK_ID
const otherProductionId = process.env.VFX_FILES_LIVE_OTHER_PRODUCTION_ID
const otherTaskId = process.env.VFX_FILES_LIVE_OTHER_TASK_ID
const entityIds = JSON.parse(process.env.VFX_FILES_LIVE_ENTITY_IDS || '{}')

test('native Task detail Files tab stays scoped to the Kitsu Task and Production', async ({ browser }) => {
  test.skip(
    !loginEmails.manager || !loginEmails.artist_a || !loginEmails.artist_b || !loginEmails.supervisor || !loginPassword || !productionId || !taskId || !secondTaskId || !otherProductionId || !otherTaskId || !entityIds.primary || !entityIds.second || !entityIds.other || !composeProject || !composeFile || !composeEnvFile,
    'Requires the isolated synthetic pinned Kitsu host fixture.'
  )
  test.setTimeout(150_000)

  const login = async role => {
    const context = await browser.newContext({
      userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
    })
    const page = await context.newPage()
    await page.goto(`${origin}/login`)
    const loginResponse = page.waitForResponse(response => response.url().endsWith('/api/auth/login') && response.request().method() === 'POST')
    await page.getByRole('textbox', { name: 'Email' }).fill(loginEmails[role])
    await page.getByRole('textbox', { name: 'Password' }).fill(loginPassword)
    await page.getByRole('button', { name: 'Log in', exact: true }).click()
    expect((await loginResponse).status()).toBe(200)
    return { context, page }
  }
  const manager = await login('manager')
  const contextResponse = await manager.page.request.get(`${origin}/api/data/user/context`)
  const contextData = await contextResponse.json()
  expect(contextData.plugins).toContainEqual(expect.objectContaining({ plugin_id: 'vfx_files', frontend_project_enabled: true }))
  const overviewResponse = await manager.page.request.get(`${origin}/api/plugins/vfx_files/overview?production_id=${productionId}&task_id=${taskId}`)
  const overviewData = await overviewResponse.json()
  expect(overviewData.upload_ready).toBe(true)

  const openFilesTab = async (production, task) => {
    const entity = task === taskId ? entityIds.primary : task === secondTaskId ? entityIds.second : entityIds.other
    await manager.page.goto(`${origin}/productions/${production}/assets/${entity}`)
    await manager.page.getByRole('button', { name: /VFXComp/ }).click()
    const filesTab = manager.page.getByRole('tab', { name: 'VFX Files', exact: true })
    await expect(filesTab).toBeVisible()
    await filesTab.click()
    const frame = manager.page.frameLocator('iframe[title="VFX Files"]')
    await expect(frame.getByRole('heading', { name: 'Files', level: 1, exact: true })).toBeVisible()
    await expect(frame.getByLabel('Choose Task')).toHaveCount(0)
    return frame
  }
  const frameUrl = () => manager.page.locator('iframe[title="VFX Files"]').getAttribute('src')
  const hasContext = async (production, task) => {
    const url = new URL(await frameUrl(), origin)
    return url.searchParams.get('production_id') === production && url.searchParams.get('task_id') === task
  }

  let frame = await openFilesTab(productionId, taskId)
  expect(await hasContext(productionId, taskId)).toBe(true)
  await expect(frame.getByText(/Working files/i)).toBeVisible()
  await manager.page.screenshot({ path: 'test-results/issue5-task-detail-files.png', fullPage: true })

  frame = await openFilesTab(productionId, secondTaskId)
  expect(await hasContext(productionId, secondTaskId)).toBe(true)

  frame = await openFilesTab(otherProductionId, otherTaskId)
  expect(await hasContext(otherProductionId, otherTaskId)).toBe(true)

  const artistA = await login('artist_a')
  const artistADrawer = async () => {
    await artistA.page.goto(`${origin}/productions/${productionId}/assets/${entityIds.second}`)
    await artistA.page.getByRole('button', { name: /VFXComp/ }).click()
    await artistA.page.getByRole('tab', { name: 'VFX Files', exact: true }).click()
    const panel = artistA.page.frameLocator('iframe[title="VFX Files"]')
    await expect(panel.getByRole('heading', { name: 'Files', level: 1, exact: true })).toBeVisible()
    return panel
  }
  let artistAFrame = await artistADrawer()
  const bytes = Buffer.from('native Task drawer Working revision from Artist A')
  const filename = 'artist-a-native-working.exr'
  await artistAFrame.locator('input[type="file"]').setInputFiles({ name: filename, mimeType: 'image/x-exr', buffer: bytes })
  const reservationStatus = artistAFrame.getByText(/Working w\d{3} reserved\. 1 transfer job\(s\) queued\./)
  await expect(reservationStatus).toBeVisible({ timeout: 15_000 })
  const workingVersion = (await reservationStatus.innerText()).match(/Working (w\d{3})/)[1]
  const workerOutput = execFileSync('docker', [
    'compose', '--project-name', composeProject, '--env-file', composeEnvFile, '--file', composeFile,
    '--profile', 'worker', 'run', '--rm', '--no-deps', 'worker',
    '/opt/zou/env/bin/python', '-m', 'vfx_files.worker_runtime', '--once'
  ], { encoding: 'utf8', timeout: 90_000 })
  expect(workerOutput).toContain('"result": "completed"')
  console.log('Native Task drawer upload completed')
  artistAFrame = await artistADrawer()
  const artistAFile = artistAFrame.getByRole('listitem').filter({ hasText: workingVersion }).getByRole('link', { name: filename, exact: true })
  await expect(artistAFile).toBeVisible()
  const artistAResponse = await artistA.page.request.get(new URL(await artistAFile.getAttribute('href'), origin).toString())
  expect(artistAResponse.status()).toBe(200)
  expect(await artistAResponse.body()).toEqual(bytes)
  console.log('Artist A native drawer download verified')

  const artistB = await login('artist_b')
  await artistB.page.goto(`${origin}/productions/${productionId}/assets/${entityIds.second}`)
  await artistB.page.getByRole('button', { name: /VFXComp/ }).click()
  await artistB.page.getByRole('tab', { name: 'VFX Files', exact: true }).click()
  const artistBFrame = artistB.page.frameLocator('iframe[title="VFX Files"]')
  const artistBFile = artistBFrame.getByRole('listitem').filter({ hasText: workingVersion }).getByRole('link', { name: filename, exact: true })
  await expect(artistBFile).toBeVisible()
  const artistBResponse = await artistB.page.request.get(new URL(await artistBFile.getAttribute('href'), origin).toString())
  expect(artistBResponse.status()).toBe(200)
  expect(await artistBResponse.body()).toEqual(bytes)
  console.log('Artist B native drawer download verified')

  const priorPublishResponse = await artistB.page.request.get(`${origin}/api/plugins/vfx_files/overview?production_id=${productionId}&task_id=${secondTaskId}`)
  const priorPublishOverview = await priorPublishResponse.json()
  const latestPublishVersion = Math.max(0, ...priorPublishOverview.publishes.filter(row => row.task_id === secondTaskId).map(row => row.version))
  const nextPublishVersion = `v${String(latestPublishVersion + 1).padStart(3, '0')}`
  console.log('Selecting completed native drawer file for Publish')
  await artistBFrame.getByLabel('Completed Working File').selectOption({ label: `${workingVersion.replace('w', 'v')} · working · ${filename}` })
  await artistBFrame.getByLabel('Output family').selectOption({ label: 'Synthetic Main' })
  await artistBFrame.getByRole('button', { name: 'Publish Working File' }).click()
  await expect(artistBFrame.getByRole('status').filter({ hasText: new RegExp(`Publish ${nextPublishVersion} queued`) })).toBeVisible()
  console.log('Native drawer Publish queued')
  const publishWorkerOutput = execFileSync('docker', [
    'compose', '--project-name', composeProject, '--env-file', composeEnvFile, '--file', composeFile,
    '--profile', 'worker', 'run', '--rm', '--no-deps', 'worker',
    '/opt/zou/env/bin/python', '-m', 'vfx_files.worker_runtime', '--once'
  ], { encoding: 'utf8', timeout: 90_000 })
  expect(publishWorkerOutput).toContain('"result": "completed"')
  console.log('Native drawer Publish Worker completed')
  const publishOverviewResponse = await artistB.page.request.get(`${origin}/api/plugins/vfx_files/overview?production_id=${productionId}&task_id=${secondTaskId}`)
  const publishOverview = await publishOverviewResponse.json()
  const completedPublish = publishOverview.publishes.filter(row => row.task_id === secondTaskId).sort((left, right) => right.version - left.version)[0]
  expect(completedPublish).toMatchObject({ state: 'completed', qc_state: 'passed', latest_published: true })

  const denied = await artistA.page.request.get(`${origin}/api/plugins/vfx_files/overview?production_id=${otherProductionId}&task_id=${otherTaskId}`)
  expect([403, 404]).toContain(denied.status())
  const supervisor = await login('supervisor')
  await supervisor.page.goto(`${origin}/productions/${productionId}/assets/${entityIds.second}`)
  await supervisor.page.getByRole('button', { name: /VFXComp/ }).click()
  await supervisor.page.getByRole('tab', { name: 'VFX Files', exact: true }).click()
  const supervisorFrame = supervisor.page.frameLocator('iframe[title="VFX Files"]')
  await expect(supervisorFrame.getByRole('heading', { name: 'Files', level: 1, exact: true })).toBeVisible()
  const latestPublishRow = supervisorFrame.getByRole('row', { name: new RegExp(`${secondTaskId} Synthetic Main ${nextPublishVersion} passed`) })
  const approvalButton = latestPublishRow.getByRole('button', { name: 'Approve exact Publish' })
  await expect(approvalButton).toBeVisible()
  await approvalButton.click()
  await expect(latestPublishRow.getByRole('button', { name: 'Approved' })).toBeVisible()
  console.log('Supervisor native drawer approval completed')
  const approvedOverviewResponse = await supervisor.page.request.get(`${origin}/api/plugins/vfx_files/overview?production_id=${productionId}&task_id=${secondTaskId}`)
  const approvedOverview = await approvedOverviewResponse.json()
  expect(approvedOverview.publishes.find(row => row.id === completedPublish.id)).toMatchObject({ latest_published: true, latest_approved: true })

  await Promise.all([manager.context.close(), artistA.context.close(), artistB.context.close(), supervisor.context.close()])
})
