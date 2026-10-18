export const taskSections = Object.freeze([
  { id: 'send', label: 'Send', enabled: true },
  { id: 'receive', label: 'Receive', enabled: true }
])

export function taskSectionEnabled(sectionId) {
  return taskSections.find((section) => section.id === sectionId)?.enabled === true
}
