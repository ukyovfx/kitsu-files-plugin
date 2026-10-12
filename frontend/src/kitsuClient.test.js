import { afterEach, describe, expect, it, vi } from 'vitest'
import { createKitsuClient, parseProductionContext } from './kitsuClient.js'

describe('createKitsuClient', () => {
  afterEach(() => vi.restoreAllMocks())

  it('can use same-origin Kitsu cookies without exposing or persisting a token', async () => {
    const fetch = vi.fn(async () =>
      new Response('{"ok":true}', {
        status: 200,
        headers: { 'Content-Type': 'application/json' }
      })
    )
    const client = createKitsuClient({
      apiBase: '/api',
      fetch
    })

    await client.request('GET', 'plugins/vfx_files/health')

    expect(fetch).toHaveBeenCalledOnce()
    const [, options] = fetch.mock.calls[0]
    expect(options.headers.get('Authorization')).toBeNull()
    expect(options.credentials).toBe('same-origin')
    client.close()
  })

  it('uses a bearer token only when an explicit current-token provider is supplied', async () => {
    const fetch = vi.fn()
    const client = createKitsuClient({
      apiBase: '/api',
      getBearerToken: () => 'synthetic-token',
      fetch
    })

    await client.request('GET', 'plugins/vfx_files/health')
    expect(fetch.mock.calls[0][1].headers.get('Authorization')).toBe('Bearer synthetic-token')
    client.close()
  })

  it('requires CSRF proof for mutations', async () => {
    const fetch = vi.fn()
    const client = createKitsuClient({ apiBase: '/api', fetch })
    await expect(client.request('POST', 'plugins/vfx_files/mutation-probe', { body: '{}' })).rejects.toThrow('CSRF token')
    await client.request('POST', 'plugins/vfx_files/mutation-probe', { body: '{}', csrfToken: 'signed-csrf-token' })
    expect(fetch.mock.calls[0][1].headers.get('X-VFX-Files-CSRF')).toBe('signed-csrf-token')
  })
})

describe('parseProductionContext', () => {
  it('returns iframe IDs only as untrusted context strings', () => {
    const context = parseProductionContext(
      '?production_id=not-a-uuid&episode_id=spoofed'
    )

    expect(context).toEqual({
      productionId: 'not-a-uuid',
      episodeId: 'spoofed',
      taskId: null,
      entityId: null,
      entityType: null,
      taskTypeId: null,
      darkTheme: false,
      locale: null
    })
  })

  it('passes selected Kitsu Task and Entity IDs through as untrusted panel context', () => {
    expect(parseProductionContext('?production_id=p-1&task_id=t-1&entity_id=e-1&entity_type=Shot&task_type_id=tt-1')).toEqual({
      productionId: 'p-1',
      episodeId: null,
      taskId: 't-1',
      entityId: 'e-1',
      entityType: 'Shot',
      taskTypeId: 'tt-1',
      darkTheme: false,
      locale: null
    })
  })
})
