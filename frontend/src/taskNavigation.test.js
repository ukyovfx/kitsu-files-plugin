import { describe, expect, it } from 'vitest'
import { taskSectionEnabled, taskSections } from './taskNavigation.js'

describe('Task Files navigation', () => {
  it('keeps daily file workflows inside the selected Task context', () => {
    expect(taskSections.map(({ id }) => id)).toEqual([
      'files', 'working', 'publish', 'share', 'received', 'handoffs'
    ])
    expect(taskSections.every((section) => section.label)).toBe(true)
  })

  it('keeps Share and Received Files gated until their API and receiver are verified', () => {
    expect(taskSectionEnabled('files')).toBe(true)
    expect(taskSectionEnabled('working')).toBe(true)
    expect(taskSectionEnabled('publish')).toBe(true)
    expect(taskSectionEnabled('handoffs')).toBe(true)
    expect(taskSectionEnabled('share')).toBe(false)
    expect(taskSectionEnabled('received')).toBe(false)
  })
})
