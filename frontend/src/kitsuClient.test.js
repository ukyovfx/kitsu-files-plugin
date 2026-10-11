import { afterEach, describe, expect, it, vi } from 'vitest'
import { createKitsuClient, parseProductionContext } from './kitsuClient.js'

describe('createKitsuClient', () => {
  afterEach(() => vi.restoreAllMocks())

  it('sends the current bearer token without cookie credentials', async () => {
    const fetch = vi.fn(async () =>
      new Response('{"ok":true}', {
        status: 200,
        headers: { 'Content-Type': 'application/json' }
      })
    )
    const getBearerToken = vi.fn(() => 'synthetic-token')
    const client = createKitsuClient({
      apiBase: 'http://127.0.0.1:18082/api',
      getBearerToken,
      fetch
    })

    await client.request('GET', 'plugins/vfx-files/health')

    expect(getBearerToken).toHaveBeenCalledOnce()
    expect(fetch).toHaveBeenCalledOnce()
    const [, options] = fetch.mock.calls[0]
    expect(options.headers.get('Authorization')).toBe('Bearer synthetic-token')
    expect(options.credentials).toBe('omit')
    client.close()
  })

  it('rejects calls when no current bearer token is available', async () => {
    const fetch = vi.fn()
    const client = createKitsuClient({
      apiBase: 'http://127.0.0.1:18082/api',
      getBearerToken: () => null,
      fetch
    })

    await expect(client.request('GET', 'plugins/vfx-files/health')).rejects.toThrow(
      'A Kitsu bearer token is required'
    )
    expect(fetch).not.toHaveBeenCalled()
    client.close()
  })
})

describe('parseProductionContext', () => {
  it('returns iframe IDs only as untrusted context strings', () => {
    const context = parseProductionContext(
      '?production_id=not-a-uuid&episode_id=spoofed'
    )

    expect(context).toEqual({
      productionId: 'not-a-uuid',
      episodeId: 'spoofed'
    })
  })
})
