import { createApp } from 'vue'
import App from './App.vue'
import { createKitsuClient, parseProductionContext } from './kitsuClient.js'
import { createFilesApi } from './api/files.js'
import './style.css'

const context = parseProductionContext(window.location.search)
const query = new URLSearchParams(window.location.search)
context.driveSettings = query.get('drive_settings') === '1' || query.has('status') || query.has('authorization_id')
if (!context.productionId && context.driveSettings) {
  context.productionId = window.sessionStorage.getItem('vfx-files-plugin-drive-production-id')
}
const client = createKitsuClient({ apiBase: '/api' })
const api = createFilesApi(client)

createApp(App, { context, api }).mount('#app')
