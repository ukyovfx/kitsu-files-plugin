import { describe, expect, it } from 'vitest'
import { createRequestGeneration } from './taskContext.js'

describe('Task context request generation', () => {
  it('marks responses from a prior Task request stale after a new context begins', () => {
    const requests = createRequestGeneration()
    const oldRequest = requests.begin()
    const newRequest = requests.begin()

    expect(requests.isCurrent(oldRequest)).toBe(false)
    expect(requests.isCurrent(newRequest)).toBe(true)
  })

  it('invalidates pending responses when the panel is unmounted or cleared', () => {
    const requests = createRequestGeneration()
    const pending = requests.begin()
    requests.invalidate()

    expect(requests.isCurrent(pending)).toBe(false)
  })
})
