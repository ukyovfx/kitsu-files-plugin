/** Defense in depth: only verified Drive-folder destinations are clickable. */
export function safeTaskFolderLinks(folders) {
  if (!Array.isArray(folders)) return []
  return folders.filter((folder) => {
    if (!folder || typeof folder !== 'object' || folder.state !== 'ready' ||
        typeof folder.url !== 'string' || typeof folder.name !== 'string') return false
    try {
      const url = new URL(folder.url)
      return url.origin === 'https://drive.google.com' &&
        !url.username && !url.password && !url.search && !url.hash &&
        /^\/drive\/folders\/[a-zA-Z0-9_-]{1,200}$/.test(url.pathname)
    } catch {
      return false
    }
  })
}
