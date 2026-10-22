import { describe,it,expect } from 'vitest'
import { importText } from './personalImportLocale.js'
describe('personal import language',()=>{
 it('uses the Task locale and preserves parameter values',()=>{
  expect(importText('ja-JP','Register {count} Google Drive file(s) as one Output version',{count:300})).toBe('Google Driveの300ファイルを1つのOutputバージョンとして登録')
  expect(importText('en','Retry copy')).toBe('Retry copy')
  expect(importText('ja','Retry copy')).toBe('コピーを再試行')
 })
})
