import { afterEach, describe, expect, it, vi } from 'vitest'
import { GAPI_SRC, GSI_SRC, PickerError, loadGoogleScript, pickDriveFiles, sha256Hex } from './googlePicker.js'

const config = { client_id: 'cid', api_key: 'key', app_id: '123456789012', scope: 'openid https://www.googleapis.com/auth/drive.file', subject_sha256: '' }

function installGoogle({ token = 'synthetic-picker-token', tokenError = null, docs = [], action = 'picked' } = {}) {
  const seen = { tokenScopes: [], built: [], revoked: 0 }
  window.gapi = { load: (_name, options) => options.callback() }
  window.google = {
    accounts: { oauth2: {
      initTokenClient: (options) => ({ requestAccessToken: () => {
        seen.tokenScopes.push(options.scope)
        if (tokenError) options.error_callback?.(tokenError)
        else options.callback({ access_token: token })
      } }),
      revoke: () => { seen.revoked += 1 }
    } },
    picker: {
      ViewId: { DOCS: 'docs' }, Feature: { MULTISELECT_ENABLED: 'multi' }, Action: { PICKED: 'picked', CANCEL: 'cancel' },
      DocsView: class { setIncludeFolders(value) { seen.folders = value; return this } setSelectFolderEnabled(value) { seen.selectFolder = value; return this } },
      PickerBuilder: class {
        addView() { return this } enableFeature() { return this } setOAuthToken(value) { seen.builtToken = value; return this }
        setDeveloperKey(value) { seen.key = value; return this } setAppId(value) { seen.app = value; return this }
        setOrigin() { return this } setCallback(callback) { this.callback = callback; return this }
        build() { return { setVisible: () => this.callback({ action, docs }) } }
      }
    }
  }
  return seen
}

const scripts = () => ({ createElement: () => { const el = {}; setTimeout(() => el.onload?.(), 0); return el }, head: { appendChild() {} } })
const okUserinfo = (sub) => async () => ({ ok: true, json: async () => ({ sub }) })

globalThis.window = globalThis // the module reads window.google / window.gapi
afterEach(() => { delete globalThis.google; delete globalThis.gapi })

describe('Google Picker flow', () => {
  it('only loads the two Google scripts', async () => {
    await expect(loadGoogleScript('https://evil.example/x.js')).rejects.toMatchObject({ code: 'script_not_allowed' })
    await expect(loadGoogleScript('http://accounts.google.com/gsi/client')).rejects.toMatchObject({ code: 'script_not_allowed' })
    await expect(loadGoogleScript(GSI_SRC, scripts())).resolves.toBeUndefined()
    expect(GAPI_SRC).toBe('https://apis.google.com/js/api.js')
  })

  it('returns picked file hints, permits folder hints, and never exposes or revokes the token', async () => {
    const sub = 'google-sub-1'
    const seen = installGoogle({ docs: [{ id: 'f1', name: 'a.exr', mimeType: 'image/x-exr', sizeBytes: '12' }] })
    const result = await pickDriveFiles({ ...config, subject_sha256: await sha256Hex(sub) }, { fetchImpl: okUserinfo(sub), doc: scripts() })
    expect(result).toEqual([{ id: 'f1', name: 'a.exr', mimeType: 'image/x-exr', sizeBytes: 12 }])
    expect(JSON.stringify(result)).not.toContain('synthetic-picker-token')
    expect(seen.folders).toBe(true)
    expect(seen.selectFolder).toBe(true)
    expect(seen.builtToken).toBe('synthetic-picker-token')
    expect([seen.key, seen.app]).toEqual(['key', '123456789012'])
    expect(seen.tokenScopes).toEqual([config.scope])
    expect(seen.revoked).toBe(0) // revoking would remove the server-held grant too
  })

  it('refuses a different Google account before opening the Picker', async () => {
    const seen = installGoogle({ docs: [{ id: 'f1' }] })
    await expect(pickDriveFiles({ ...config, subject_sha256: await sha256Hex('linked-sub') }, { fetchImpl: okUserinfo('someone-else'), doc: scripts() }))
      .rejects.toMatchObject({ code: 'account_mismatch' })
    expect(seen.builtToken).toBeUndefined()
  })

  it('reports cancel, closed popup, failed sign-in and missing config distinctly', async () => {
    const sub = 's'
    const base = { ...config, subject_sha256: await sha256Hex(sub) }
    installGoogle({ action: 'cancel' })
    await expect(pickDriveFiles(base, { fetchImpl: okUserinfo(sub), doc: scripts() })).rejects.toMatchObject({ code: 'cancelled' })
    installGoogle({ tokenError: { type: 'popup_closed' } })
    await expect(pickDriveFiles(base, { fetchImpl: okUserinfo(sub), doc: scripts() })).rejects.toMatchObject({ code: 'cancelled' })
    installGoogle({ tokenError: { type: 'popup_failed_to_open' } })
    await expect(pickDriveFiles(base, { fetchImpl: okUserinfo(sub), doc: scripts() })).rejects.toMatchObject({ code: 'auth_failed' })
    installGoogle()
    await expect(pickDriveFiles(base, { fetchImpl: async () => ({ ok: false }), doc: scripts() })).rejects.toMatchObject({ code: 'auth_failed' })
    await expect(pickDriveFiles({ ...base, app_id: 'abc' }, { doc: scripts() })).rejects.toMatchObject({ code: 'picker_not_configured' })
    expect(new PickerError('x')).toBeInstanceOf(Error)
  })
})
