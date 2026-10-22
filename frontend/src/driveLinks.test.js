import { describe, expect, it } from 'vitest'
import { safeDriveUrl } from './driveLinks.js'

describe('safeDriveUrl', () => {
  it('accepts only plain Drive file and folder navigation URLs', () => {
    expect(safeDriveUrl('https://drive.google.com/file/d/1AbCdEfGhIjKlMnOp/view')).toBe('https://drive.google.com/file/d/1AbCdEfGhIjKlMnOp/view')
    expect(safeDriveUrl('https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOp')).toBe('https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOp')
  })

  it('rejects other hosts, schemes, credentials, queries and malformed IDs', () => {
    for (const bad of [
      null, undefined, 12, '', 'javascript:alert(1)', 'http://drive.google.com/file/d/1AbCdEfGhIjKlMnOp/view',
      'https://evil.example/file/d/1AbCdEfGhIjKlMnOp/view', 'https://drive.google.com.evil.example/file/d/1AbCdEfGhIjKlMnOp/view',
      'https://user:pw@drive.google.com/file/d/1AbCdEfGhIjKlMnOp/view', 'https://drive.google.com/file/d/1AbCdEfGhIjKlMnOp/view?usp=sharing',
      'https://drive.google.com/file/d/1AbCdEfGhIjKlMnOp/view#x', 'https://drive.google.com/file/d/short/view',
      'https://drive.google.com/file/d/1AbCdEfGhIjKlMnOp/edit', 'https://drive.google.com/open?id=1AbCdEfGhIjKlMnOp'
    ]) expect(safeDriveUrl(bad)).toBe('')
  })
})
