import { describe, expect, it } from 'vitest'
import { safeGoogleAuthUrl } from './googleAuthUrl.js'

describe('safeGoogleAuthUrl', () => {
  it('accepts only the Google OAuth endpoint', () => {
    const good = 'https://accounts.google.com/o/oauth2/v2/auth?client_id=x&state=y'
    expect(safeGoogleAuthUrl(good)).toBe(good)
    for (const bad of [null, '', 'javascript:alert(1)', 'http://accounts.google.com/o/oauth2/v2/auth',
      'https://accounts.google.com.evil.example/o/oauth2/v2/auth', 'https://evil.example/o/oauth2/v2/auth',
      'https://accounts.google.com/o/oauth2/v2/auth#frag', 'https://u:p@accounts.google.com/o/oauth2/v2/auth',
      'https://accounts.google.com/other']) expect(safeGoogleAuthUrl(bad)).toBe('')
  })
})
