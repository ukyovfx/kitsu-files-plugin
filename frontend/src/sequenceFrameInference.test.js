import { describe, it, expect } from 'vitest'
import { inferSequenceSelection } from './sequenceFrameInference.js'

describe('sequence frame suggestions', () => {
  it('detects missing interior frames without assigning missing files', () => {
    expect(inferSequenceSelection([
      { id: 'a', name: 'shot.1001.exr' },
      { id: 'b', name: 'shot.1003.exr' }
    ])).toEqual({ frames: { a: 1001, b: 1003 }, start: 1001, end: 1003 })
  })

  it('supports nested paths and case-insensitive extensions', () => {
    expect(inferSequenceSelection([
      { id: 'a', name: 'plates/beauty/SHOT_0001.DPX' },
      { id: 'b', name: 'plates/beauty/SHOT_0002.dpx' }
    ])).toEqual({ frames: { a: 1, b: 2 }, start: 1, end: 2 })
  })

  it('does not silently combine different shots, padding, or AOVs', () => {
    const a = { id: 'a', name: 'shot.1001.exr' }
    expect(inferSequenceSelection([a, { id: 'b', name: 'other.1002.exr' }])).toBeNull()
    expect(inferSequenceSelection([a, { id: 'b', name: 'shot.01002.exr' }])).toBeNull()
    expect(inferSequenceSelection([a, { id: 'b', name: 'shot.1002.png' }])).toBeNull()
    expect(inferSequenceSelection([{ id: 'a', name: 'shot.1001.left.exr' }])).toBeNull()
  })

  it('rejects unknown names, duplicates, and empty selections', () => {
    expect(inferSequenceSelection([])).toBeNull()
    expect(inferSequenceSelection([{ id: 'a', name: 'readme.txt' }])).toBeNull()
    expect(inferSequenceSelection([
      { id: 'a', name: 'shot.1001.exr' },
      { id: 'a', name: 'shot.1002.exr' }
    ])).toBeNull()
  })

  it('only infers visible endpoints, never claims shot-range completeness', () => {
    const observed = inferSequenceSelection([{ id: 'x', name: 'shot.1002.exr' }])
    expect(observed).toEqual({ frames: { x: 1002 }, start: 1002, end: 1002 })
    // The expected range (possibly 1001-1003) must remain user-editable.
  })
})
