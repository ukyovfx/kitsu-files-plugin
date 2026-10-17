/** Create a stateless same-origin client; host cookies remain browser-managed. */
export function createKitsuClient({ apiBase, getBearerToken, fetch: fetchImpl = globalThis.fetch }) {
  if (typeof apiBase !== 'string' || !apiBase.trim()) {
    throw new TypeError('A Kitsu API base URL is required')
  }
  if (getBearerToken !== undefined && typeof getBearerToken !== 'function') {
    throw new TypeError('A bearer token provider must be a function')
  }
  if (typeof fetchImpl !== 'function') {
    throw new TypeError('A fetch implementation is required')
  }

  const base = apiBase.replace(/\/+$/, '')

  return {
    async request(method, path, options = {}) {
      if (typeof path !== 'string' || path.length === 0 || /^[a-z][a-z\d+.-]*:/i.test(path)) {
        throw new TypeError('An API-relative path is required')
      }

      const { csrfToken, ...requestOptions } = options
      const headers = new Headers(requestOptions.headers)
      if (getBearerToken) {
        const token = getBearerToken()
        if (token !== null && token !== undefined && token !== '') {
          if (typeof token !== 'string') throw new TypeError('A Kitsu bearer token must be a string')
          headers.set('Authorization', `Bearer ${token}`)
        }
      }
      const methodUpper = method.toUpperCase()
      if (!['GET', 'HEAD', 'OPTIONS'].includes(methodUpper)) {
        if (!csrfToken) throw new Error('A Kitsu Files CSRF token is required for mutations')
        headers.set('X-VFX-Files-CSRF', csrfToken)
      }
      return fetchImpl(`${base}/${path.replace(/^\/+/, '')}`, {
        ...requestOptions,
        method: methodUpper,
        headers,
        credentials: 'same-origin'
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
    episodeId: params.get('episode_id'),
    // These are iframe context hints only; the plugin API resolves and authorizes them.
    taskId: params.get('task_id'),
    entityId: params.get('entity_id'),
    entityType: params.get('entity_type'),
    taskTypeId: params.get('task_type_id'),
    darkTheme: params.get('dark_theme') === 'true',
    locale: params.get('locale')
  }
}

/** Accept only same-origin visual preferences from the containing Kitsu Task panel. */
export function applyTaskPluginPreferences(event, context, hostWindow = window) {
  if (
    !context?.taskId ||
    !hostWindow ||
    hostWindow.parent === hostWindow ||
    event?.source !== hostWindow.parent ||
    event?.origin !== hostWindow.location?.origin
  ) {
    return false
  }

  const message = event.data
  if (
    !message ||
    message.type !== 'kitsu.task-plugin.preferences' ||
    message.version !== 1 ||
    typeof message.darkTheme !== 'boolean' ||
    typeof message.locale !== 'string' ||
    !/^[a-z]{2,8}$/i.test(message.locale)
  ) {
    return false
  }

  context.darkTheme = message.darkTheme
  context.locale = message.locale.slice(0, 2).toLowerCase()
  return true
}
