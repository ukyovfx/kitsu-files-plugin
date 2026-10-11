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

  it('shows the disabled service as a non-success result', async () => {
    const client = { request: vi.fn(async () => response({}, 503)) }
    await expect(createFilesApi(client).listProduction('p-1')).rejects.toMatchObject({ status: 503 })
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
})
