import { describe, expect, it } from 'vitest'
import { outputPayloadTransferAvailable } from './outputTransferAvailability.js'

describe('Output payload transfer availability', () => {
  it('allows only the explicitly synthetic FakeStorage mode', () => {
    expect(outputPayloadTransferAvailable('fake')).toBe(true)
    expect(outputPayloadTransferAvailable('provider')).toBe(false)
    expect(outputPayloadTransferAvailable(undefined)).toBe(false)
  })
})
