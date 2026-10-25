/** Conservative suggestions from a single consistently named image sequence.
 * Frames remain editable, and the caller must verify the intended start/end.
 */
const FRAME_NAME = /^(.*[._-])(\d{3,8})\.(exr|dpx|png|tif|tiff|jpg|jpeg)$/i

export function inferSequenceSelection(files) {
  if (!Array.isArray(files) || !files.length) return null
  let signature = null
  const frames = {}
  let start = Infinity
  let end = -Infinity
  for (const file of files) {
    if (typeof file?.id !== 'string' || !file.id || typeof file.name !== 'string') return null
    const match = FRAME_NAME.exec(file.name)
    if (!match) return null
    // Do not mix shots/AOVs, frame padding, folders, or image formats.
    const current = `${match[1].toLowerCase()}\0${match[2].length}\0${match[3].toLowerCase()}`
    if (signature !== null && signature !== current) return null
    signature = current
    const frame = Number(match[2])
    if (!Number.isSafeInteger(frame)) return null
    frames[file.id] = frame
    start = Math.min(start, frame)
    end = Math.max(end, frame)
  }
  if (Object.keys(frames).length !== files.length) return null
  return { frames, start, end }
}
