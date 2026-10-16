export const taskSections = Object.freeze([
  { id: 'files', label: 'Files', enabled: true },
  { id: 'working', label: 'Working', enabled: true },
  { id: 'publish', label: 'Publish', enabled: true },
  { id: 'share', label: 'Share', enabled: false },
  { id: 'received', label: 'Received Files', enabled: false },
  { id: 'handoffs', label: 'Department Handoff', enabled: true }
])

export function taskSectionEnabled(sectionId) {
  return taskSections.find((section) => section.id === sectionId)?.enabled === true
}
