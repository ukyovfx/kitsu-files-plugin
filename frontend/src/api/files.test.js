import { describe, expect, it, vi } from 'vitest'
import { createFilesApi } from './files.js'

function response(body, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

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
    await createFilesApi(client).createDepartmentHandoff('publish-2', 'dept-fx', 'task-fx', 'artist-fx', 'Review frame 120')
    expect(client.request.mock.calls[1][1]).toBe('plugins/vfx_files/publishes/publish-2/handoffs')
    expect(JSON.parse(client.request.mock.calls[1][2].body)).toEqual({ recipient_department_id: 'dept-fx', recipient_task_id: 'task-fx', recipient_person_id: 'artist-fx', note: 'Review frame 120' })
  })

  it('links an Output Package only through the authenticated exact-publication endpoint', async () => {
    const client = { request: vi.fn()
      .mockResolvedValueOnce(response({ csrf_token: 'publication-csrf' }))
      .mockResolvedValueOnce(response({ id: 'link-1', package_id: 'package-1', publish_id: 'publish-1' }, 201)) }
    const api = createFilesApi(client)
    await api.linkOutputPackagePublication('package-1', 'publish-1', 'package-publication:package-1:publish-1')
    expect(client.request.mock.calls).toEqual([
      ['GET', 'plugins/vfx_files/csrf-token'],
      ['POST', 'plugins/vfx_files/output-packages/package-1/publication-binding', expect.objectContaining({
        csrfToken: 'publication-csrf',
        headers: expect.objectContaining({ 'Idempotency-Key': 'package-publication:package-1:publish-1' })
      })]
    ])
    expect(JSON.parse(client.request.mock.calls[1][2].body)).toEqual({ publish_id: 'publish-1' })
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
})
