import { expect, test } from '@playwright/test'

const productionId = '11111111-1111-4111-8111-111111111111'
const taskId = '22222222-2222-4222-8222-222222222222'
const linkedSub = 'google-sub-linked'

function json(route, body, status = 200) {
  return route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
}

async function sha256Hex(text) {
  const { createHash } = await import('node:crypto')
  return createHash('sha256').update(text).digest('hex')
}

// Fake Google scripts: the real ones are third-party and need real accounts. The fakes follow the same API shape.
const fakeGsi = `window.google = Object.assign(window.google || {}, { accounts: { oauth2: { initTokenClient(cfg) { return { requestAccessToken() {
  const mode = window.__tokenMode || 'ok'
  if (mode === 'closed') return cfg.error_callback({ type: 'popup_closed' })
  cfg.callback({ access_token: 'synthetic-picker-token' }) } } } } } })`
const fakeGapi = `window.gapi = { load(_name, options) { (options.callback || options)() } }
window.google = Object.assign(window.google || {}, { picker: {
  ViewId: { DOCS: 'docs' }, Feature: { MULTISELECT_ENABLED: 'multi' }, Action: { PICKED: 'picked', CANCEL: 'cancel' },
  DocsView: class { setIncludeFolders(v) { window.__pickerFolders = v; return this } setSelectFolderEnabled() { return this } },
  PickerBuilder: class { addView() { return this } enableFeature() { return this } setOAuthToken(t) { window.__pickerToken = t; return this }
    setDeveloperKey() { return this } setAppId() { return this } setOrigin() { return this } setCallback(cb) { this.cb = cb; return this }
    build() { return { setVisible: () => this.cb({ action: window.__pickerAction || 'picked', docs: window.__pickerDocs || [] }) } } } } })`

async function setup(page, { linked = true, allowed = true, userSub = linkedSub, packages = [] } = {}) {
  const seen = { posts: [], retries: 0, userinfoAuth: [], mutatingGoogle: [] }
  const subjectHash = await sha256Hex(linkedSub)
  await page.route('https://accounts.google.com/gsi/client', (route) => route.fulfill({ status: 200, contentType: 'text/javascript', body: fakeGsi }))
  await page.route('https://apis.google.com/js/api.js', (route) => route.fulfill({ status: 200, contentType: 'text/javascript', body: fakeGapi }))
  await page.route('https://openidconnect.googleapis.com/v1/userinfo', (route) => {
    seen.userinfoAuth.push(route.request().headers().authorization)
    return route.fulfill({ status: 200, contentType: 'application/json', headers: { 'access-control-allow-origin': '*' }, body: JSON.stringify({ sub: userSub }) })
  })
  await page.route('**/api/plugins/vfx_files/**', async (route) => {
    const request = route.request()
    const { pathname } = new URL(request.url())
    if (pathname.endsWith('/csrf-token')) return json(route, { csrf_token: 'synthetic-csrf', expires_in: 900 })
    if (pathname.endsWith('/overview')) return json(route, { production_id: productionId, task_id: taskId, actor_id: 'a', tasks: [{ id: taskId, task_type_id: 't', entity_id: 'e' }], upload_ready: false, publishes: [], handoffs: [] })
    if (pathname.endsWith('/output-package-options')) return json(route, { task_id: taskId, production_id: productionId, storage_root_id: 'root', output_types: [{ id: 'ot-1', name: 'Final' }] })
    if (pathname.endsWith('/output-packages') && request.method() === 'GET') return json(route, packages)
    if (pathname.endsWith('/storage-objects')) return json(route, { task_id: taskId, parent_id: 'root', items: [], next_page_token: null })
    if (pathname.endsWith('/shared-folders')) return json(route, { task_id: taskId, folders: [] })
    if (pathname.endsWith(`/productions/${productionId}/personal-google`)) {
      return json(route, { policy: allowed ? 'allowed' : 'blocked', allowed, can_change_policy: false, linked, state: linked ? 'active' : 'not_linked', account_hint: '…ked' })
    }
    if (pathname.endsWith('/personal-google/picker-config')) {
      return json(route, { client_id: 'synthetic-client', api_key: 'synthetic-key', app_id: '123456789012', scope: 'openid https://www.googleapis.com/auth/drive.file', subject_sha256: subjectHash })
    }
    if (pathname.endsWith(`/tasks/${taskId}/personal-drive/output-packages`) && request.method() === 'POST') {
      seen.posts.push({ body: JSON.parse(request.postData()), key: request.headers()['idempotency-key'], csrf: request.headers()['x-vfx-files-csrf'] })
      return json(route, { id: 'pkg-new', job_id: 'job-new', version: 1, state: 'verifying' }, 202)
    }
    if (pathname.endsWith('/retry')) { seen.retries += 1; return json(route, { requeued: true }, 202) }
    if (/link-shares|share-candidates/.test(pathname)) return json(route, { items: [], shares: [], recipients: [], candidates: [] })
    return json(route, { error: true, message: 'not mocked' }, 404)
  })
  page.on('request', (request) => {
    const url = request.url()
    if (url.includes('googleapis.com/drive') && request.method() !== 'GET') seen.mutatingGoogle.push(`${request.method()} ${url}`)
  })
  return seen
}

async function openTask(page) {
  await page.goto(`/?production_id=${productionId}&task_id=${taskId}`)
  await expect(page.locator('.files-page[data-mode="task"]')).toBeVisible()
  await expect(page.locator('.output-packages')).toBeVisible()
}

test('EXR sequence from Google Drive: pick, see the missing frame, register once with IDs only', async ({ page }) => {
  const seen = await setup(page)
  await page.addInitScript(() => {
    window.__pickerDocs = [1001, 1002, 1004, 1005].map((frame) => ({ id: `1drivefile${frame}aaaa`, name: `shot.${frame}.exr`, mimeType: 'image/x-exr', sizeBytes: '1048576' }))
  })
  await openTask(page)
  await page.getByLabel('Package kind').selectOption('final-exr')
  await page.getByLabel('Frame start').fill('1001')
  await page.getByLabel('Frame end').fill('1005')
  await page.getByRole('button', { name: /Choose files from Google Drive/ }).click()
  await expect(page.getByLabel('Selected Google Drive files')).toBeVisible()
  await expect(page.getByText(/Enter the frame range/)).toBeVisible() // frames are required before registering
  await expect(page.getByRole('button', { name: /Register 4 Google Drive file/ })).toBeDisabled()
  for (const frame of [1001, 1002, 1004, 1005]) await page.getByLabel(`Frame number shot.${frame}.exr`).fill(String(frame))
  await expect(page.getByText(/Missing frames: 1003/)).toBeVisible() // EXR sequence check before registering
  expect(await page.evaluate(() => window.__pickerFolders)).toBe(true) // folder hints require a server-side grant-scoped preview
  await page.getByRole('button', { name: /Register 4 Google Drive file/ }).click()
  await expect.poll(() => seen.posts.length).toBe(1)
  const post = seen.posts[0]
  expect(post.csrf).toBe('synthetic-csrf')
  expect(post.key).toMatch(/^personal-import:/)
  expect(post.body.files.map((file) => file.drive_file_id)).toEqual([1001, 1002, 1004, 1005].map((frame) => `1drivefile${frame}aaaa`))
  expect(post.body.frame_start).toBe(1001)
  // The browser sends IDs and frame numbers only: no names, sizes or tokens.
  const text = JSON.stringify(post.body)
  for (const forbidden of ['synthetic-picker-token', 'shot.1001.exr', '1048576', 'access_token', 'refresh_token']) expect(text).not.toContain(forbidden)
  expect(seen.userinfoAuth).toEqual(['Bearer synthetic-picker-token']) // the token only ever goes to Google
  expect(seen.mutatingGoogle).toEqual([])
})

test('a different Google account, a cancelled Picker and a blocked popup are handled without side effects', async ({ page }) => {
  const seen = await setup(page, { userSub: 'someone-else' })
  await page.addInitScript(() => { window.__pickerDocs = [{ id: '1driveshould-not-appear', name: 'x.exr', mimeType: 'image/x-exr' }] })
  await openTask(page)
  await page.getByRole('button', { name: /Choose files from Google Drive/ }).click()
  await expect(page.getByRole('alert').filter({ hasText: 'different Google account' })).toBeVisible()
  await expect(page.getByLabel('Selected Google Drive files')).toHaveCount(0)
  expect(await page.evaluate(() => window.__pickerToken)).toBeUndefined() // the Picker was never opened

  await page.evaluate(() => { window.__tokenMode = 'closed' })
  await page.getByRole('button', { name: /Choose files from Google Drive/ }).click()
  await expect(page.getByRole('alert')).toHaveCount(0) // closing the popup is a quiet cancel
  expect(seen.posts).toEqual([])
})

test('not linked or blocked: the Drive picker is not offered and explains why', async ({ page }) => {
  await setup(page, { linked: false })
  await openTask(page)
  await expect(page.getByText(/Connect your Google account first/).first()).toBeVisible()
  await expect(page.getByRole('button', { name: /Choose files from Google Drive/ })).toHaveCount(0)
  const blocked = await page.context().newPage()
  await setup(blocked, { allowed: false })
  await openTask(blocked)
  await expect(blocked.getByText(/blocked for this Production/).first()).toBeVisible()
  await expect(blocked.getByRole('button', { name: /Choose files from Google Drive/ })).toHaveCount(0)
})

test('copy progress, error and retry are shown on the version row', async ({ page }) => {
  const seen = await setup(page, { packages: [
    { id: 'p1', production_id: productionId, task_id: taskId, package_type: 'final-exr', version: 1, state: 'verifying', qc_state: 'pending', job_state: 'leased', job_error_code: null, file_count: 4, manifest_sha256: 'x', manifest_summary: {}, source_kind: 'personal', retry_available: false, deleted: false },
    { id: 'p2', production_id: productionId, task_id: taskId, package_type: 'final-exr', version: 2, state: 'verifying', qc_state: 'pending', job_state: 'failed', job_error_code: 'personal_source_permission_denied', file_count: 1, manifest_sha256: 'y', manifest_summary: {}, source_kind: 'personal', retry_available: true, deleted: false }
  ] })
  await openTask(page)
  await expect(page.getByText('Copying…')).toBeVisible()
  await expect(page.getByText(/Failed.*personal_source_permission_denied/)).toBeVisible()
  await expect(page.getByText(/from Google Drive/).first()).toBeVisible()
  await page.getByRole('button', { name: 'Retry copy' }).click()
  await expect.poll(() => seen.retries).toBe(1)
})


test('Task change discards a late Picker result and stale selected files', async ({ page }) => {
 await setup(page)
 await page.addInitScript(hash => { window.linkedHash = hash }, await sha256Hex(linkedSub))
 await page.route('**/m4-test-harness', route => route.fulfill({ contentType:'text/html',body:'<div id="fixture"></div><script type="module" src="/browser-tests/m4TaskSwitchHarness.js"></script>' }))
 await page.route('https://apis.google.com/js/api.js', route => route.fulfill({ contentType:'text/javascript',body:fakeGapi.replace("this.cb({ action: window.__pickerAction || 'picked', docs: window.__pickerDocs || [] })", "(window.finishPicker = () => this.cb({ action:'picked',docs:[{id:'old-file-id',name:'old.exr'}] }))") }))
 await page.goto('/m4-test-harness')
 await page.getByRole('button',{name:/Choose files from Google Drive/}).click()
 await expect.poll(()=>page.evaluate(()=>typeof window.finishPicker)).toBe('function')
 await page.evaluate(()=>window.switchTask('task-b'))
 await page.evaluate(()=>window.finishPicker())
 await expect(page.getByLabel('Selected Google Drive files')).toHaveCount(0)
})


test('Japanese Task locale localizes import selection, progress and retry', async ({ page }) => {
 const seen=await setup(page,{packages:[{id:'failed-ja',task_id:taskId,package_type:'tracking-data',version:1,state:'verifying',qc_state:'pending',job_state:'failed',job_error_code:'personal_source_permission_denied',source_kind:'personal',retry_available:true,deleted:false}]})
 await page.goto(`/?production_id=${productionId}&task_id=${taskId}&locale=ja`)
 await expect(page.getByRole('heading',{name:'自分のGoogle Drive'})).toBeVisible()
 await expect(page.getByRole('button',{name:'Google Driveのファイル・フォルダを選択…'})).toBeVisible()
 await expect(page.getByText('Output登録',{exact:true})).toBeVisible()
 await expect(page.getByText(/元ファイルを読めません/)).toBeVisible()
 await page.getByRole('button',{name:'コピーを再試行',exact:true}).click()
 await expect.poll(()=>seen.retries).toBe(1)
})

test('folder preview preserves relative paths and requires explicit file review', async ({ page }) => {
 const seen=await setup(page)
 await page.addInitScript(()=>{window.__pickerDocs=[{id:'1folderselectedaaaa',name:'plates',mimeType:'application/vnd.google-apps.folder'}]})
 await page.route('**/personal-drive/selection-preview',route=>json(route,{scope_limited:true,files:[{id:'1nestedfileaaaaaaaa',name:'plates/beauty/a.exr',sizeBytes:3,root_folder_id:'1folderselectedaaaa'}]}))
 await openTask(page)
 await page.getByRole('button',{name:/Choose files from Google Drive/}).click()
 await expect(page.getByText('plates/beauty/a.exr')).toBeVisible()
 await expect(page.getByRole('button',{name:/Register 1 Google Drive file/})).toBeDisabled()
 await page.getByRole('checkbox',{name:/Import exactly the reviewed files/}).check()
 await page.getByRole('button',{name:/Register 1 Google Drive file/}).click()
 await expect.poll(()=>seen.posts.length).toBe(1)
 expect(seen.posts[0].body.files[0].root_folder_id).toBe('1folderselectedaaaa')
 expect(JSON.stringify(seen.posts[0].body)).not.toContain('plates/beauty')
})


for (const pending of ['Picker', 'registration']) {
 test(`Refresh is disabled during deferred ${pending} and controls recover`, async ({page})=>{
  await setup(page)
  await page.addInitScript(()=>{window.__pickerDocs=[{id:'1singlefileaaaaaaaa',name:'clip.mov',mimeType:'video/quicktime'}]})
  let release
  if(pending==='Picker') {
   await page.route('https://apis.google.com/js/api.js',route=>route.fulfill({contentType:'text/javascript',body:fakeGapi.replace("this.cb({ action: window.__pickerAction || 'picked', docs: window.__pickerDocs || [] })", "(window.finishPicker = () => this.cb({ action:'picked',docs:window.__pickerDocs }))")}))
  } else {
   await page.route('**/personal-drive/output-packages',async route=>{
    await new Promise(resolve=>{release=resolve})
    await json(route,{id:'pkg-new',job_id:'job-new',version:1,state:'verifying'},202)
   })
  }
  await openTask(page)
  await page.getByLabel('Package kind').selectOption('low-res-video')
  await page.getByRole('button',{name:/Choose files from Google Drive/}).click()
  if(pending==='Picker') await expect.poll(()=>page.evaluate(()=>typeof window.finishPicker)).toBe('function')
  else {
   await page.getByRole('button',{name:/Register 1 Google Drive file/}).click()
   await expect.poll(()=>typeof release).toBe('function')
  }
  await expect(page.getByRole('button',{name:'Refresh',exact:true})).toBeDisabled()
  if(pending==='Picker') await page.evaluate(()=>window.finishPicker())
  else release()
  await expect(page.getByRole('button',{name:'Refresh',exact:true})).toBeEnabled()
  await expect(page.getByRole('button',{name:/Choose files from Google Drive/})).toBeEnabled()
 })
}


test('deletion completion during a deferred Picker keeps its cleanup valid',async({page})=>{
 await setup(page,{packages:[{id:'old-package',task_id:taskId,package_type:'tracking-data',version:1,state:'completed',qc_state:'passed',file_count:1,can_delete:true,deleted:false}]})
 await page.route('**/output-packages/old-package/deletion',route=>route.request().method()==='GET' ? json(route,{can_delete:true,impact_sha256:'fixture',impact:{file_count:1,deliveries:[],latest_pointers:[]}}) : json(route,{state:'deleting'},202))
 await page.route('https://apis.google.com/js/api.js',route=>route.fulfill({contentType:'text/javascript',body:fakeGapi.replace("this.cb({ action: window.__pickerAction || 'picked', docs: window.__pickerDocs || [] })", "(window.finishPicker = () => this.cb({ action:'picked',docs:[{id:'1singlefileaaaaaaaa',name:'clip.mov'}] }))")}))
 await openTask(page)
 await page.getByRole('button',{name:/Choose files from Google Drive/}).click()
 await expect.poll(()=>page.evaluate(()=>typeof window.finishPicker)).toBe('function')
 await page.getByRole('button',{name:'Delete tracking-data v001'}).click()
 await page.getByRole('button',{name:'Yes, permanently delete these files'}).click()
 await expect(page.getByRole('alertdialog')).toHaveCount(0)
 await page.evaluate(()=>window.finishPicker())
 await expect(page.getByText('clip.mov',{exact:true})).toBeVisible()
 await expect(page.getByRole('button',{name:/Choose files from Google Drive/})).toBeEnabled()
})
