import { describe, expect, it } from 'vitest'
import { taskSectionEnabled, taskSections } from './taskNavigation.js'

describe('Task Files navigation', () => {
  it('keeps daily file workflows inside the selected Task context', () => {
    expect(taskSections.map(({ id }) => id)).toEqual([
      'files', 'working', 'publish', 'share', 'received', 'handoffs'
    ])
    expect(taskSections.every((section) => section.label)).toBe(true)
  })

  it('exposes reference sharing and received files inside the selected Task context', () => {
    expect(taskSectionEnabled('files')).toBe(true)
    expect(taskSectionEnabled('working')).toBe(true)
    expect(taskSectionEnabled('publish')).toBe(true)
    expect(taskSectionEnabled('handoffs')).toBe(true)
    expect(taskSectionEnabled('share')).toBe(true)
    expect(taskSectionEnabled('received')).toBe(true)
  })
})
