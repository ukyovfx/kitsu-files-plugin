/** Defense in depth: the server builds Drive URLs from verified Plugin-managed IDs; only plain Drive URLs are clickable. */
export function safeDriveUrl(value) {
  if (typeof value !== 'string') return ''
  try {
    const url = new URL(value)
    const ok = url.origin === 'https://drive.google.com' && !url.username && !url.password && !url.search && !url.hash &&
      /^\/(file\/d\/[A-Za-z0-9_-]{10,200}\/view|drive\/folders\/[A-Za-z0-9_-]{10,200})$/.test(url.pathname)
    return ok ? url.href : ''
  } catch {
    return ''
  }
}
