import { createApp, reactive } from 'vue'
import App from './App.vue'
import { applyTaskPluginPreferences, createKitsuClient, parseProductionContext } from './kitsuClient.js'
import { createFilesApi } from './api/files.js'
import './style.css'

const context = reactive(parseProductionContext(window.location.search))
window.addEventListener('message', (event) => applyTaskPluginPreferences(event, context))
const client = createKitsuClient({ apiBase: '/api' })
const api = createFilesApi(client)

createApp(App, { context, api }).mount('#app')
