/**
 * Google Picker for the user's OWN Drive (web flow, Google Identity Services token client).
 *
 * Security shape:
 * - The access token is requested in the browser, lives only in this module's closure, is never stored, logged,
 *   sent to the Plugin server, or returned to callers. (It is deliberately NOT revoked afterwards: Google's revoke
 *   removes the whole grant, including the server-held one.)
 * - The server-provided config contains only public identifiers; no secret or Production credential.
 * - Picker results are display hints. The Plugin server re-verifies every file ID with the user's own server-side grant.
 * - A folder is only a selection hint; server-grant preview independently verifies visible children.
 */

export const GSI_SRC = 'https://accounts.google.com/gsi/client'
export const GAPI_SRC = 'https://apis.google.com/js/api.js'
const ALLOWED_SCRIPTS = new Set([GSI_SRC, GAPI_SRC])
const USERINFO_URL = 'https://openidconnect.googleapis.com/v1/userinfo'
const loading = new Map()

export class PickerError extends Error {
  constructor(code, message) {
    super(message || code)
    this.code = code
  }
}

export function loadGoogleScript(src, doc = globalThis.document) {
  if (!ALLOWED_SCRIPTS.has(src)) return Promise.reject(new PickerError('script_not_allowed'))
  if (loading.has(src)) return loading.get(src)
  const pending = new Promise((resolve, reject) => {
    const script = doc.createElement('script')
    script.src = src
    script.async = true
    script.onload = () => resolve()
    script.onerror = () => { loading.delete(src); reject(new PickerError('script_blocked', 'Google scripts could not be loaded.')) }
    doc.head.appendChild(script)
  })
  loading.set(src, pending)
  return pending
}

export async function sha256Hex(text, cryptoApi = globalThis.crypto) {
  const bytes = new TextEncoder().encode(text)
  const digest = await cryptoApi.subtle.digest('SHA-256', bytes)
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, '0')).join('')
}

function requestToken(google, config) {
  return new Promise((resolve, reject) => {
    const client = google.accounts.oauth2.initTokenClient({
      client_id: config.client_id,
      scope: config.scope,
      callback: (response) => {
        if (response?.error || typeof response?.access_token !== 'string' || !response.access_token) {
          reject(new PickerError('auth_failed', 'Google sign-in did not return access.'))
        } else resolve(response.access_token)
      },
      error_callback: (error) => reject(new PickerError(error?.type === 'popup_closed' ? 'cancelled' : 'auth_failed',
        error?.type === 'popup_failed_to_open' ? 'The Google sign-in popup was blocked.' : undefined))
    })
    client.requestAccessToken({ prompt: '' })
  })
}

async function assertLinkedAccount(token, config, fetchImpl, cryptoApi) {
  let subject
  try {
    const response = await fetchImpl(USERINFO_URL, { headers: { Authorization: `Bearer ${token}` } })
    if (!response.ok) throw new Error('userinfo')
    subject = (await response.json()).sub
  } catch {
    throw new PickerError('auth_failed', 'The signed-in Google account could not be identified.')
  }
  if (typeof subject !== 'string' || (await sha256Hex(subject, cryptoApi)) !== config.subject_sha256) {
    throw new PickerError('account_mismatch', 'This is not the Google account linked to your Kitsu user.')
  }
}

function topOrigin() {
  // Inside Kitsu's iframe the Picker must be given the embedding page's origin.
  try { return window.top.location.origin } catch { /* cross-origin parent */ }
  try { return window.location.origin } catch { return '' }
}

/** Returns [{ id, name, sizeBytes, mimeType }] (display hints only) or throws PickerError. */
export async function pickDriveFiles(config, { fetchImpl = globalThis.fetch?.bind(globalThis), cryptoApi = globalThis.crypto, doc = globalThis.document } = {}) {
  if (!config?.client_id || !config?.api_key || !/^\d+$/.test(String(config.app_id || ''))) throw new PickerError('picker_not_configured')
  await Promise.all([loadGoogleScript(GSI_SRC, doc), loadGoogleScript(GAPI_SRC, doc)])
  const { google, gapi } = window
  if (!google?.accounts?.oauth2 || !gapi?.load) throw new PickerError('script_blocked', 'Google scripts are unavailable.')
  await new Promise((resolve, reject) => {
    try { gapi.load('picker', { callback: resolve, onerror: () => reject(new PickerError('script_blocked')) }) } catch (cause) { reject(cause) }
  })
  const token = await requestToken(google, config)
  await assertLinkedAccount(token, config, fetchImpl, cryptoApi)
  return new Promise((resolve, reject) => {
    const view = new google.picker.DocsView(google.picker.ViewId.DOCS)
      .setIncludeFolders(true)
      .setSelectFolderEnabled(true)
    new google.picker.PickerBuilder()
      .addView(view)
      .enableFeature(google.picker.Feature.MULTISELECT_ENABLED)
      .setOAuthToken(token)
      .setDeveloperKey(config.api_key)
      .setAppId(String(config.app_id))
      .setOrigin(topOrigin())
      .setCallback((data) => {
        if (data.action === google.picker.Action.PICKED) {
          resolve((data.docs || []).map((doc) => ({
            id: String(doc.id), name: String(doc.name || ''), mimeType: String(doc.mimeType || ''),
            sizeBytes: Number.isFinite(Number(doc.sizeBytes)) ? Number(doc.sizeBytes) : null
          })))
        } else if (data.action === google.picker.Action.CANCEL) {
          reject(new PickerError('cancelled'))
        }
      })
      .build()
      .setVisible(true)
  })
}
