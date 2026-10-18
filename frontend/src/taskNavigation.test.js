import { describe, expect, it } from 'vitest'
import { taskSectionEnabled, taskSections } from './taskNavigation.js'

describe('Task Files navigation', () => {
  it('keeps daily file workflows inside the selected Task context', () => {
    expect(taskSections.map(({ id }) => id)).toEqual([
      'send', 'receive'
    ])
    expect(taskSections.every((section) => section.label)).toBe(true)
  })

  it('exposes reference sharing and received files inside the selected Task context', () => {
    expect(taskSectionEnabled('send')).toBe(true)
    expect(taskSectionEnabled('receive')).toBe(true)
    expect(taskSectionEnabled('working')).toBe(false)
    expect(taskSectionEnabled('publish')).toBe(false)
    expect(taskSectionEnabled('handoffs')).toBe(false)
  })
})
