/** Create a stateless client that reads the current host token for every call. */
export function createKitsuClient({ apiBase, getBearerToken, fetch: fetchImpl = globalThis.fetch }) {
  if (typeof apiBase !== 'string' || !apiBase.trim()) {
    throw new TypeError('A Kitsu API base URL is required')
  }
  if (typeof getBearerToken !== 'function') {
    throw new TypeError('A bearer token provider is required')
  }
  if (typeof fetchImpl !== 'function') {
    throw new TypeError('A fetch implementation is required')
  }

  const base = apiBase.replace(/\/+$/, '')

  return {
    async request(method, path, options = {}) {
      const token = getBearerToken()
      if (typeof token !== 'string' || token.length === 0) {
        throw new Error('A Kitsu bearer token is required')
      }

      if (typeof path !== 'string' || path.length === 0 || /^[a-z][a-z\d+.-]*:/i.test(path)) {
        throw new TypeError('An API-relative path is required')
      }

      const headers = new Headers(options.headers)
      headers.set('Authorization', `Bearer ${token}`)
      return fetchImpl(`${base}/${path.replace(/^\/+/, '')}`, {
        ...options,
        method,
        headers,
        credentials: 'omit'
      })
    },
    close() {}
  }
}

/** Route query values are display/filter hints only; the server authorizes them. */
export function parseProductionContext(search) {
  const params = new URLSearchParams(search.startsWith('?') ? search.slice(1) : search)
  return {
    productionId: params.get('production_id'),
    episodeId: params.get('episode_id')
  }
}
