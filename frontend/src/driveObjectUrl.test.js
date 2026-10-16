import { describe, expect, it } from 'vitest'

import { driveObjectUrl } from './driveObjectUrl.js'

describe('driveObjectUrl', () => {
  it('opens a listed authorized folder without changing its Drive permissions', () => {
    expect(driveObjectUrl({ id: 'folder-1', is_folder: true })).toBe(
      'https://drive.google.com/drive/folders/folder-1'
    )
  })

  it('opens a listed authorized file without sharing or copying it', () => {
    expect(driveObjectUrl({ id: 'file/id', is_folder: false })).toBe(
      'https://drive.google.com/open?id=file%2Fid'
    )
  })

  it('does not create a link when no provider ID is present', () => {
    expect(driveObjectUrl({ name: 'missing-id', is_folder: true })).toBe('')
  })
})
