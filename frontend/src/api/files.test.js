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

  it('shows the disabled service as a non-success result', async () => {
    const client = { request: vi.fn(async () => response({}, 503)) }
    await expect(createFilesApi(client).listProduction('p-1')).rejects.toMatchObject({ status: 503 })
  })

  it('discovers, saves, activates and queues Production-scoped shared folders', async () => {
    const client = { request: vi.fn()
      .mockResolvedValueOnce(response({ configured: false }))
      .mockResolvedValueOnce(response({ allowed_root_id: 'root/a', templates: [] }))
      .mockResolvedValueOnce(response({ csrf_token: 'save-csrf' }))
      .mockResolvedValueOnce(response({ version: 1, enabled: false }))
      .mockResolvedValueOnce(response({ csrf_token: 'activate-csrf' }))
      .mockResolvedValueOnce(response({ enabled: true }))
      .mockResolvedValueOnce(response({ csrf_token: 'queue-csrf' }))
      .mockResolvedValueOnce(response({ job_id: 'job-1', state: 'queued' }))
      .mockResolvedValueOnce(response({ configured: true, folders: [] })) }
    const api = createFilesApi(client)
    await api.getSharedTemplate('prod/a')
    await api.discoverSharedTemplates('prod/a', 'https://drive.google.com/drive/folders/root/a')
    await api.saveSharedTemplate('prod/a', { template_folder_id: 'template-1' })
    await api.setSharedTemplateEnabled('prod/a', 1, true)
    await api.provisionTaskFolders('prod/a', 'task/1')
    await api.getTaskSharedFolders('prod/a', 'task/1')
    expect(client.request.mock.calls.map(([method, path]) => [method, path])).toEqual([
      ['GET', 'plugins/vfx_files/productions/prod%2Fa/shared-template'],
      ['GET', 'plugins/vfx_files/productions/prod%2Fa/shared-template/candidates?root_url=https%3A%2F%2Fdrive.google.com%2Fdrive%2Ffolders%2Froot%2Fa'],
      ['GET', 'plugins/vfx_files/csrf-token'],
      ['POST', 'plugins/vfx_files/productions/prod%2Fa/shared-template'],
      ['GET', 'plugins/vfx_files/csrf-token'],
      ['POST', 'plugins/vfx_files/productions/prod%2Fa/shared-template/activate'],
      ['GET', 'plugins/vfx_files/csrf-token'],
      ['POST', 'plugins/vfx_files/productions/prod%2Fa/tasks/task%2F1/shared-folders'],
      ['GET', 'plugins/vfx_files/productions/prod%2Fa/tasks/task%2F1/shared-folders']
    ])
    expect(JSON.parse(client.request.mock.calls[3][2].body)).toMatchObject({ template_folder_id: 'template-1' })
    expect(JSON.parse(client.request.mock.calls[5][2].body)).toEqual({ expected_version: 1, enabled: true })
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
    expect(client.request.mock.calls.filter(([method]) => method === 'PUT')).toHaveLength(1)
    expect(client.request.mock.calls.at(-1)[1]).toBe('plugins/vfx_files/uploads/session-1/finalize')
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
})
