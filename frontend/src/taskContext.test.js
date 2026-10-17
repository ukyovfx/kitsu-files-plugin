import { describe, expect, it } from 'vitest'
import { createRequestGeneration, filesSurfaceMode } from './taskContext.js'

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

describe('Files surface mode', () => {
  it('prioritizes Production administration over Task work when explicitly requested', () => {
    expect(filesSurfaceMode({ driveSettings: true, taskId: 'task-1' })).toBe('admin')
  })

  it('shows the embedded Task workflow only when a Task is selected', () => {
    expect(filesSurfaceMode({ productionId: 'production-1', taskId: 'task-1' })).toBe('task')
  })

  it('opens Production configuration when Kitsu launches the Production Plugin page', () => {
    expect(filesSurfaceMode({ productionId: 'production-1' })).toBe('admin')
  })
})
