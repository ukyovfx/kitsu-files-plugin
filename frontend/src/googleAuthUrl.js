/** Defense in depth: only a Google account-chooser URL produced by the server may be navigated to. */
export function safeGoogleAuthUrl(value) {
  if (typeof value !== 'string') return ''
  try {
    const url = new URL(value)
    const ok = url.origin === 'https://accounts.google.com' && url.pathname === '/o/oauth2/v2/auth' &&
      !url.username && !url.password && !url.hash
    return ok ? url.href : ''
  } catch {
    return ''
  }
}
