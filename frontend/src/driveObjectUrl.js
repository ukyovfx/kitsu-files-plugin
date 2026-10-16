export function driveObjectUrl(item) {
  if (!item?.id) return ''
  const id = encodeURIComponent(item.id)
  return item.is_folder
    ? `https://drive.google.com/drive/folders/${id}`
    : `https://drive.google.com/open?id=${id}`
}
