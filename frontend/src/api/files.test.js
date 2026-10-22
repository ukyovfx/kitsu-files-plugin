import { describe, expect, it, vi } from 'vitest'
import { createFilesApi } from './files.js'

function response(body, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

describe('Personal Google API', () => {
  it('starts personal OAuth with CSRF + idempotency and never sends credentials from the browser', async () => {
    const client = { request: vi.fn()
      .mockResolvedValueOnce(response({ csrf_token: 'pg-csrf' }))
      .mockResolvedValueOnce(response({ authorization_url: 'https://accounts.google.com/o/oauth2/v2/auth?x=1' }, 201)) }
    const api = createFilesApi(client)
    await api.startPersonalGoogleAuthorization('p/1', 'personal-google:key-0000000001')
    const [method, path, options] = client.request.mock.calls[1]
    expect(method).toBe('POST')
    expect(path).toBe('plugins/vfx_files/productions/p%2F1/personal-google/authorizations')
    expect(options.csrfToken).toBe('pg-csrf')
    expect(options.headers['Idempotency-Key']).toBe('personal-google:key-0000000001')
    expect(options.body).toBe('{}')
    await expect(api.startPersonalGoogleAuthorization('', 'k')).rejects.toThrow('Production')
  })

  it('disconnect is user-level and policy changes go to the Production policy route', async () => {
    const client = { request: vi.fn()
      .mockResolvedValueOnce(response({ csrf_token: 'a' })).mockResolvedValueOnce(response({ state: 'revoked' }))
      .mockResolvedValueOnce(response({ csrf_token: 'b' })).mockResolvedValueOnce(response({ policy: 'blocked' })) }
    const api = createFilesApi(client)
    await api.disconnectPersonalGoogle()
    expect(client.request.mock.calls[1][1]).toBe('plugins/vfx_files/me/google-account/disconnect')
    await api.setPersonalGooglePolicy('prod-1', 'blocked')
    expect(client.request.mock.calls[3][1]).toBe('plugins/vfx_files/productions/prod-1/personal-google-policy')
    expect(JSON.parse(client.request.mock.calls[3][2].body)).toEqual({ policy: 'blocked' })
  })
})

describe('Files API', () => {
  it('requests only the supplied Production scope and treats IDs as query values', async () => {
    const client = { request: vi.fn(async () => response({ production_id: 'p-1', publishes: [], handoffs: [] })) }
    const api = createFilesApi(client)
    await api.listProduction('p-1', 'episode-1')
    expect(client.request).toHaveBeenCalledWith('GET', 'plugins/vfx_files/overview?production_id=p-1&episode_id=episode-1')
    await expect(api.listProduction('')).rejects.toThrow('Production ID')
  })

  it('passes the selected Task as a scope hint for server-side authorization', async () => {
    const client = { request: vi.fn(async () => response({ production_id: 'p-1', task_id: 't-1' })) }
    const api = createFilesApi(client)
    await api.listProduction('p-1', null, 't-1')
    expect(client.request).toHaveBeenCalledWith('GET', 'plugins/vfx_files/overview?production_id=p-1&task_id=t-1')
  })

  it('reads sanitized Plugin authority status and starts native OAuth with CSRF and idempotency', async () => {
    const client = { request: vi.fn()
      .mockResolvedValueOnce(response({ owner: 'plugin', state: 'unconfigured' }))
      .mockResolvedValueOnce(response({ csrf_token: 'drive-csrf' }))
      .mockResolvedValueOnce(response({ authorization_url: 'https://accounts.google.test/oauth', authorization_id: 'auth-1' })) }
    const api = createFilesApi(client)
    await expect(api.getDriveAuthorityStatus('prod/a')).resolves.toMatchObject({ owner: 'plugin' })
    await api.createDriveAuthorization('prod/a', 'my_drive', 'native-drive:prod/a:one')
    expect(client.request.mock.calls[0]).toEqual(['GET', 'plugins/vfx_files/productions/prod%2Fa/drive/status'])
    expect(client.request.mock.calls[1]).toEqual(['GET', 'plugins/vfx_files/csrf-token'])
    const [method, path, options] = client.request.mock.calls[2]
    expect(method).toBe('POST')
    expect(path).toBe('plugins/vfx_files/productions/prod%2Fa/drive/authorizations')
    expect(options.csrfToken).toBe('drive-csrf')
    expect(options.headers['Idempotency-Key']).toBe('native-drive:prod/a:one')
    expect(JSON.parse(options.body)).toEqual({ contract_version: '1.0', account_mode: 'my_drive' })
    await expect(api.createDriveAuthorization('prod/a', 'drive', 'key')).rejects.toThrow('account mode')
    await expect(api.createDriveAuthorization('prod/a', 'shared_drive', 'key')).rejects.toThrow('account mode')
  })

  it('lists Plugin-owned Drive candidates and verifies root selection with revision fencing', async () => {
    const client = { request: vi.fn()
      .mockResolvedValueOnce(response({ connections: [{ id: 'conn-1', state: 'connected' }] }))
      .mockResolvedValueOnce(response({ roots: [{ id: 'root-1', name: 'App authorized root' }] }))
      .mockResolvedValueOnce(response({ csrf_token: 'drive-csrf' }))
      .mockResolvedValueOnce(response({ owner: 'plugin', config_revision: 1, root_id: 'root-1' })) }
    const api = createFilesApi(client)
    await expect(api.listDriveConnections('prod-1')).resolves.toMatchObject({ connections: [{ id: 'conn-1' }] })
    await expect(api.listDriveRootCandidates('prod-1', 'conn-1')).resolves.toMatchObject({ roots: [{ id: 'root-1' }] })
    await api.selectDriveRoot('prod-1', 'conn-1', 'root-1', 0, 'native-root-selection-0001')
    const [method, path, options] = client.request.mock.calls[3]
    expect(method).toBe('POST')
    expect(path).toBe('plugins/vfx_files/productions/prod-1/drive/connections/conn-1/roots')
    expect(options.csrfToken).toBe('drive-csrf')
    expect(options.headers['If-Match']).toBe('"0"')
    expect(options.headers['Idempotency-Key']).toBe('native-root-selection-0001')
    expect(JSON.parse(options.body)).toEqual({ contract_version: '1.0', root_id: 'root-1' })
    await expect(api.selectDriveRoot('prod-1', 'conn-1', 'root-1', -1, 'key')).rejects.toThrow('revision')
  })

  it('saves native Drive configuration, activates explicitly, and confirms legacy import with exact revisions', async () => {
    const client = { request: vi.fn()
      .mockResolvedValueOnce(response({ configured: true, config_revision: 2 }))
      .mockResolvedValueOnce(response({ csrf_token: 'config-csrf' }))
      .mockResolvedValueOnce(response({ config_revision: 3 }))
      .mockResolvedValueOnce(response({ csrf_token: 'activate-csrf' }))
      .mockResolvedValueOnce(response({ activation: { enabled: true } }))
      .mockResolvedValueOnce(response({ status: 'ready_for_explicit_review', source_revision: 7 }))
      .mockResolvedValueOnce(response({ csrf_token: 'import-csrf' }))
      .mockResolvedValueOnce(response({ import_status: 'imported_inactive' })) }
    const api = createFilesApi(client)
    const config = {
      contract_version: '1.0',
      template_selection: { selections: [] },
      sync_policy: { mode: 'future_only', scope: {}, notify_new_deliveries: false },
    }
    await api.saveDriveConfiguration('prod-1', config, 2, 'native-config-save-0001')
    await api.setDriveActivation('prod-1', true, 3, 'native-activate-save-0001')
    await api.getLegacyDriveImportPreview('prod-1')
    await api.confirmLegacyDriveImport('prod-1', 7, 'legacy-import-confirm-0001')
    const save = client.request.mock.calls[1]
    expect(save[1]).toBe('plugins/vfx_files/productions/prod-1/drive/configuration')
    expect(save[2].headers['If-Match']).toBe('"2"')
    expect(JSON.parse(save[2].body)).toEqual(config)
    const activation = client.request.mock.calls[3]
    expect(activation[1]).toBe('plugins/vfx_files/productions/prod-1/drive/activation')
    expect(activation[2].headers['If-Match']).toBe('"3"')
    expect(JSON.parse(activation[2].body)).toEqual({ contract_version: '1.0', enabled: true })
    const legacyImport = client.request.mock.calls[6]
    expect(legacyImport[1]).toBe('plugins/vfx_files/productions/prod-1/drive/legacy-import')
    expect(legacyImport[2].headers['If-Match']).toBe('"legacy-7"')
    expect(JSON.parse(legacyImport[2].body)).toEqual({ contract_version: '1.0', source_revision: 7, confirm: true })
  })

  it('fetches a CSRF token and submits an idempotent handoff response', async () => {
    const client = { request: vi.fn()
      .mockResolvedValueOnce(response({ csrf_token: 'signed-token' }))
      .mockResolvedValueOnce(response({ id: 'h-1', state: 'accepted' })) }
    const api = createFilesApi(client)
    await api.respondHandoff('h-1', 'accepted')
    expect(client.request.mock.calls[0]).toEqual(['GET', 'plugins/vfx_files/csrf-token'])
    const [method, path, options] = client.request.mock.calls[1]
    expect(method).toBe('POST')
    expect(path).toBe('plugins/vfx_files/handoffs/h-1/respond')
    expect(options.csrfToken).toBe('signed-token')
    expect(options.headers['Idempotency-Key']).toMatch(/^[0-9a-f-]{36}$/i)
    expect(JSON.parse(options.body)).toMatchObject({ target: 'accepted', feedback: '' })
  })

  it('loads output families and submits Publish, exact-Publish approval and handoff requests', async () => {
    const client = { request: vi.fn()
      .mockResolvedValueOnce(response([{ id: 'family-1', name: 'Main' }]))
      .mockResolvedValueOnce(response({ csrf_token: 'publish-csrf' }))
      .mockResolvedValueOnce(response({ id: 'publish-1', state: 'reserved' }, 202))
      .mockResolvedValueOnce(response({ csrf_token: 'approval-csrf' }))
      .mockResolvedValueOnce(response({ approval_id: 'approval-1' }, 201))
      .mockResolvedValueOnce(response({ csrf_token: 'handoff-csrf' }))
      .mockResolvedValueOnce(response({ id: 'handoff-1', state: 'pending' }, 201)) }
    const api = createFilesApi(client)
    await expect(api.listOutputFamilies('prod-1')).resolves.toHaveLength(1)
    await api.createPublish('task-1', 'family-1', 'working-file-1')
    await api.approvePublish('publish-1')
    await api.createHandoff('publish-1', 'person-2')
    expect(client.request.mock.calls.map(([method, path]) => [method, path])).toEqual([
      ['GET', 'plugins/vfx_files/productions/prod-1/output-families'],
      ['GET', 'plugins/vfx_files/csrf-token'],
      ['POST', 'plugins/vfx_files/tasks/task-1/publishes'],
      ['GET', 'plugins/vfx_files/csrf-token'],
      ['POST', 'plugins/vfx_files/publishes/publish-1/approvals'],
      ['GET', 'plugins/vfx_files/csrf-token'],
      ['POST', 'plugins/vfx_files/publishes/publish-1/handoffs']
    ])
    expect(JSON.parse(client.request.mock.calls[2][2].body)).toEqual({ output_family_id: 'family-1', source_revision_file_id: 'working-file-1' })
    expect(JSON.parse(client.request.mock.calls[4][2].body)).toMatchObject({ policy_id: 'files-page-review-v1' })
    expect(JSON.parse(client.request.mock.calls[6][2].body)).toEqual({ recipient_id: 'person-2' })
  })

  it('distributes one Publish to an exact Task Type Department recipient with an optional accountable person', async () => {
    const client = { request: vi.fn()
      .mockResolvedValueOnce(response({ csrf_token: 'team-csrf' }))
      .mockResolvedValueOnce(response({ id: 'handoff-2', recipient_task_id: 'task-fx', state: 'pending' }, 201)) }
    await createFilesApi(client).createDepartmentHandoff('publish-2', 'dept-fx', 'task-fx', 'artist-fx')
    expect(client.request.mock.calls[1][1]).toBe('plugins/vfx_files/publishes/publish-2/handoffs')
    expect(JSON.parse(client.request.mock.calls[1][2].body)).toEqual({ recipient_department_id: 'dept-fx', recipient_task_id: 'task-fx', recipient_person_id: 'artist-fx' })
  })

  it('shows the disabled service as a non-success result', async () => {
    const client = { request: vi.fn(async () => response({}, 503)) }
    await expect(createFilesApi(client).listProduction('p-1')).rejects.toMatchObject({ status: 503 })
  })

  it('reads Task folder bindings without exposing Drive configuration mutations', async () => {
    const client = { request: vi.fn(async () => response({
      configured: true,
      managed_by_kitsusync: true,
      folders: [{ scope_key: 'production:p:task:t', name: 'main', state: 'ready', url: 'https://drive.google.com/drive/folders/folder-1' }]
    })) }
    const api = createFilesApi(client)
    await expect(api.getTaskSharedFolders('prod/a', 'task/1')).resolves.toMatchObject({
      managed_by_kitsusync: true,
      folders: [{ state: 'ready' }]
    })
    expect(client.request.mock.calls).toEqual([[
      'GET', 'plugins/vfx_files/productions/prod%2Fa/tasks/task%2F1/shared-folders'
    ]])
    expect(api.getSharedTemplate).toBeUndefined()
    expect(api.saveSharedTemplate).toBeUndefined()
    expect(api.setSharedTemplateEnabled).toBeUndefined()
    expect(api.provisionTaskFolders).toBeUndefined()
  })

  it('uploads fixed chunks with checksums and finalizes only after server readback status', async () => {
    const client = { request: vi.fn()
      .mockResolvedValueOnce(response([]))
      .mockResolvedValueOnce(response({ csrf_token: 'csrf-1' }))
      .mockResolvedValueOnce(response({ id: 'upload-1', max_chunk_bytes: 3 }))
      .mockResolvedValueOnce(response({ id: 'upload-1', state: 'receiving', expected_size: 5, received_size: 0, max_chunk_bytes: 3, parts: [] }))
      .mockResolvedValueOnce(response({ csrf_token: 'csrf-2' }))
      .mockResolvedValueOnce(response({ index: 0, state: 'staged' }))
      .mockResolvedValueOnce(response({ csrf_token: 'csrf-3' }))
      .mockResolvedValueOnce(response({ index: 1, state: 'staged' }))
      .mockResolvedValueOnce(response({ id: 'upload-1', state: 'receiving', expected_size: 5, received_size: 5, max_chunk_bytes: 3, parts: [{ index: 0, byte_count: 3, state: 'staged' }, { index: 1, byte_count: 2, state: 'staged' }] }))
      .mockResolvedValueOnce(response({ csrf_token: 'csrf-4' }))
      .mockResolvedValueOnce(response({ job_id: 'job-1', state: 'queued' })) }
    const progress = []
    const result = await createFilesApi(client).stageFile('task-1', new Blob([new Uint8Array([1, 2, 3, 4, 5])]), (value) => progress.push(value.completed))
    expect(result).toMatchObject({ job_id: 'job-1', sessionId: 'upload-1' })
    const partCalls = client.request.mock.calls.filter(([method]) => method === 'PUT')
    expect(partCalls).toHaveLength(2)
    expect(partCalls.map(([, path]) => path)).toEqual([
      'plugins/vfx_files/uploads/upload-1/parts/0',
      'plugins/vfx_files/uploads/upload-1/parts/1'
    ])
    expect(partCalls.map(([, , options]) => options.headers['Content-Range'])).toEqual(['bytes 0-2/5', 'bytes 3-4/5'])
    expect(progress).toEqual([0, 3, 5])
  })

  it('reserves a multi-file Working snapshot before uploading its resumable sessions', async () => {
    const client = { request: vi.fn()
      .mockResolvedValueOnce(response({ csrf_token: 'working-csrf' }))
      .mockResolvedValueOnce(response({ id: 'revision-1', version_label: 'w001', files: [{ upload_session_id: 'session-1' }] }))
      .mockResolvedValueOnce(response({ id: 'session-1', max_chunk_bytes: 8, parts: [] }))
      .mockResolvedValueOnce(response({ csrf_token: 'part-csrf' }))
      .mockResolvedValueOnce(response({ index: 0, state: 'staged' }))
      .mockResolvedValueOnce(response({ csrf_token: 'finalize-csrf' }))
      .mockResolvedValueOnce(response({ job_id: 'job-1', state: 'queued' })) }
    const file = new File([new Uint8Array([4, 5, 6])], 'comp.nk', { type: 'application/octet-stream' })
    const result = await createFilesApi(client).stageWorkingSnapshot('task-1', [file], null)
    expect(result).toMatchObject({ revision: { id: 'revision-1', version_label: 'w001' }, jobs: [{ job_id: 'job-1' }] })
    const reserve = client.request.mock.calls[1]
    expect(reserve[0]).toBe('POST')
    expect(reserve[1]).toBe('plugins/vfx_files/tasks/task-1/working-revisions')
    expect(reserve[2].headers['Idempotency-Key']).toMatch(/^working:/)
    expect(JSON.parse(reserve[2].body)).toMatchObject({ base_revision_id: null, files: [{ name: 'comp.nk', size: 3, category: 'working' }] })
    expect(JSON.parse(reserve[2].body).include_intermediate).toBe(false)
    expect(client.request.mock.calls.filter(([method]) => method === 'PUT')).toHaveLength(1)
    expect(client.request.mock.calls.at(-1)[1]).toBe('plugins/vfx_files/uploads/session-1/finalize')
  })

  it('sends a bounded custom Working file type without changing the revision API label', async () => {
    const client = { request: vi.fn()
      .mockResolvedValueOnce(response({ csrf_token: 'working-csrf' }))
      .mockResolvedValueOnce(response({ id: 'revision-2', version_label: 'w002', files: [{ upload_session_id: 'session-2' }] }))
      .mockResolvedValueOnce(response({ id: 'session-2', max_chunk_bytes: 8, parts: [] }))
      .mockResolvedValueOnce(response({ csrf_token: 'part-csrf' }))
      .mockResolvedValueOnce(response({ index: 0, state: 'staged' }))
      .mockResolvedValueOnce(response({ csrf_token: 'finalize-csrf' }))
      .mockResolvedValueOnce(response({ job_id: 'job-2', state: 'queued' })) }
    const file = new File(['abc'], 'track.csv')
    const result = await createFilesApi(client).stageWorkingSnapshot('task-1', [file], 'revision-1', () => {}, 'Tracking Data', true)
    const reserve = client.request.mock.calls.find(([, path]) => path.endsWith('/working-revisions'))
    expect(JSON.parse(reserve[2].body).files[0]).toMatchObject({ category: 'Tracking Data' })
    expect(JSON.parse(reserve[2].body).include_intermediate).toBe(true)
    expect(result.revision.version_label).toBe('w002')
  })

  it('rejects unsafe custom Working file types before making an API request', async () => {
    const client = { request: vi.fn() }
    const api = createFilesApi(client)
    const file = new File(['abc'], 'track.csv')
    await expect(api.stageWorkingSnapshot('task-1', [file], null, () => {}, '../outside')).rejects.toThrow('File type')
    expect(client.request).not.toHaveBeenCalled()
  })

  it('resumes only when every selected file matches the reserved snapshot', async () => {
    const client = { request: vi.fn()
      .mockResolvedValueOnce(response({ id: 'session-1', max_chunk_bytes: 8, parts: [{ index: 0, state: 'staged', byte_count: 3 }] }))
      .mockResolvedValueOnce(response({ csrf_token: 'finalize-csrf' }))
      .mockResolvedValueOnce(response({ job_id: 'job-1', state: 'queued' })) }
    const file = new File([new Uint8Array([4, 5, 6])], 'comp.nk', { type: 'application/octet-stream' })
    const revision = { id: 'revision-1', version_label: 'w001', files: [{ name: 'comp.nk', size: 3, sha256: '787c798e39a5bc1910355bae6d0cd87a36b2e10fd0202a83e3bb6b005da83472', upload_session_id: 'session-1' }] }
    const api = createFilesApi(client)
    await expect(api.resumeWorkingSnapshot('task-1', revision, [new File(['different'], 'comp.nk')])).rejects.toThrow('do not match')
    expect(client.request).not.toHaveBeenCalled()
    await expect(api.resumeWorkingSnapshot('task-1', revision, [file])).resolves.toMatchObject({ jobs: [{ job_id: 'job-1' }] })
    expect(client.request.mock.calls.filter(([method]) => method === 'PUT')).toHaveLength(0)
    expect(client.request.mock.calls.at(-1)[1]).toBe('plugins/vfx_files/uploads/session-1/finalize')
  })

  it('lists scoped Drive objects and reserves one Output Package with a stable retry key', async () => {
    const client = { request: vi.fn()
      .mockResolvedValueOnce(response({ task_id: 'task-1', parent_id: 'root', items: [], complete: true }))
      .mockResolvedValueOnce(response({ csrf_token: 'package-csrf' }))
      .mockResolvedValueOnce(response({ id: 'package-1', job_id: 'job-1', version: 1, state: 'verifying' })) }
    const api = createFilesApi(client)
    await expect(api.listTaskStorageObjects('task-1', 'root')).resolves.toMatchObject({ parent_id: 'root', complete: true })
    await expect(api.createTaskOutputPackage('task-1', {
      output_type_id: 'output-1', package_type: 'final-exr', frame_start: 1001, frame_end: 1002,
      files: [{ provider_object_id: 'drive-1', frame_number: 1001 }]
    }, 'output-package:retry-1')).resolves.toMatchObject({ id: 'package-1', version: 1 })
    expect(client.request.mock.calls[0][1]).toBe('plugins/vfx_files/tasks/task-1/storage-objects?parent_id=root')
    const create = client.request.mock.calls[2]
    expect(create[1]).toBe('plugins/vfx_files/tasks/task-1/output-packages')
    expect(create[2].headers['Idempotency-Key']).toBe('output-package:retry-1')
  })

  it('deletes an Output version only with an explicit confirmation of the reviewed impact', async () => {
    const client = { request: vi.fn()
      .mockResolvedValueOnce(response({ impact_sha256: 'a'.repeat(64), can_delete: true }))
      .mockResolvedValueOnce(response({ csrf_token: 'del-csrf' }))
      .mockResolvedValueOnce(response({ state: 'deleting' }, 202)) }
    const api = createFilesApi(client)
    await api.getOutputPackageDeletionImpact('pkg/1')
    await api.deleteOutputPackage('pkg/1', 'a'.repeat(64))
    expect(client.request.mock.calls[0]).toEqual(['GET', 'plugins/vfx_files/output-packages/pkg%2F1/deletion'])
    const [method, path, options] = client.request.mock.calls[2]
    expect(method).toBe('POST')
    expect(path).toBe('plugins/vfx_files/output-packages/pkg%2F1/deletion')
    expect(options.csrfToken).toBe('del-csrf')
    expect(JSON.parse(options.body)).toEqual({ confirm: true, impact_sha256: 'a'.repeat(64) })
    await expect(api.deleteOutputPackage('pkg/1', '')).rejects.toThrow('reviewed impact')
  })

  it('reads and changes the Production deletion policy through CSRF-protected requests', async () => {
    const client = { request: vi.fn()
      .mockResolvedValueOnce(response({ policy: 'registrant_and_admin', can_change: true }))
      .mockResolvedValueOnce(response({ csrf_token: 'pol-csrf' }))
      .mockResolvedValueOnce(response({ policy: 'admin_only' })) }
    const api = createFilesApi(client)
    await expect(api.getOutputDeletePolicy('prod-1')).resolves.toMatchObject({ can_change: true })
    await expect(api.setOutputDeletePolicy('prod-1', 'admin_only')).resolves.toEqual({ policy: 'admin_only' })
    const [, path, options] = client.request.mock.calls[2]
    expect(path).toBe('plugins/vfx_files/productions/prod-1/output-delete-policy')
    expect(options.csrfToken).toBe('pol-csrf')
    expect(JSON.parse(options.body)).toEqual({ policy: 'admin_only' })
    await expect(api.setOutputDeletePolicy('', 'admin_only')).rejects.toThrow('Production')
  })
})
