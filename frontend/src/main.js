import { createApp } from 'vue'
import App from './App.vue'
import { createKitsuClient, parseProductionContext } from './kitsuClient.js'
import { createFilesApi } from './api/files.js'
import './style.css'

const context = parseProductionContext(window.location.search)
const client = createKitsuClient({ apiBase: '/api' })
const api = createFilesApi(client)

createApp(App, { context, api }).mount('#app')
