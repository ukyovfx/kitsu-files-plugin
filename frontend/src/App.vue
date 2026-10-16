<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import HandoffPanel from './components/HandoffPanel.vue'
import PublishList from './components/PublishList.vue'
import PublishWorkflow from './components/PublishWorkflow.vue'
import OutputPackagePanel from './components/OutputPackagePanel.vue'
import UploadPanel from './components/UploadPanel.vue'
import { createRequestGeneration } from './taskContext.js'
import { safeTaskFolderLinks } from './taskFolderLinks.js'

const props = defineProps({ context: { type: Object, required: true }, api: { type: Object, required: true } })
const overview = ref(null)
const loading = ref(true)
const error = ref('')
const busyId = ref('')
const productionId = computed(() => props.context.productionId)
const taskId = computed(() => props.context.taskId || null)
const taskMode = computed(() => Boolean(taskId.value))
const scopeKey = computed(() => `${productionId.value || ''}:${taskId.value || ''}`)
const folderState = ref(null)
const verifiedFolderLinks = computed(() => safeTaskFolderLinks(folderState.value?.folders))
const currentTask = computed(() => overview.value?.tasks?.find((task) => task.id === taskId.value) || null)
const driveSettingsHref = computed(() => {
  const query = new URLSearchParams({ production_id: productionId.value || '', drive_settings: '1' })
  return `/api/plugins/vfx_files/frontend/?${query.toString()}`
})
const requests = createRequestGeneration()
const receivedHandoffs = computed(() => (overview.value?.handoffs || []).filter((item) => item.recipient_id === overview.value?.actor_id || (taskId.value && item.recipient_task_id === taskId.value)))
let pollTimer
let loadedScope = ''

async function load() {
  if (!taskMode.value) {
    overview.value = null
    folderState.value = null
    loading.value = false
    error.value = ''
    return
  }
  const requestId = requests.begin()
  const currentScope = scopeKey.value
  if (currentScope !== loadedScope) {
    overview.value = null
    folderState.value = null
    loadedScope = currentScope
  }
  loading.value = !overview.value
  error.value = ''
  if (!productionId.value) {
    error.value = 'Open Files from a Kitsu Task to load its authorized file records.'
    loading.value = false
    return
  }
  try {
    const result = await props.api.listProduction(productionId.value, props.context.episodeId, taskId.value)
    if (!requests.isCurrent(requestId) || currentScope !== scopeKey.value) return
    overview.value = result
    if (taskId.value) {
      try {
        folderState.value = await props.api.getTaskSharedFolders(productionId.value, taskId.value)
      } catch (cause) {
        if (requests.isCurrent(requestId)) folderState.value = { error: cause.message || 'Task folders could not be loaded.' }
      }
    }
  } catch (cause) {
    if (requests.isCurrent(requestId)) error.value = cause.message || 'Files could not be loaded.'
  } finally {
    if (requests.isCurrent(requestId)) loading.value = false
  }
}

watch(scopeKey, () => load())

async function respond(handoff, target, feedback = '') {
  busyId.value = handoff.id
  error.value = ''
  try {
    await props.api.respondHandoff(handoff.id, target, feedback)
    await load()
  } catch (cause) {
    error.value = cause.message || 'The handoff response could not be saved.'
  } finally {
    busyId.value = ''
  }
}

async function approve(publish) {
  busyId.value = publish.id
  error.value = ''
  try {
    await props.api.approvePublish(publish.id)
    await load()
  } catch (cause) {
    error.value = cause.message || 'The Publish approval could not be saved.'
  } finally {
    busyId.value = ''
  }
}

onMounted(() => {
  if (!taskMode.value) return
  load()
  pollTimer = window.setInterval(() => {
    if (taskMode.value && overview.value?.publishes.some((publish) => !['completed', 'failed', 'reconcile_required'].includes(publish.state))) load()
  }, 3000)
})
onUnmounted(() => window.clearInterval(pollTimer))
onUnmounted(() => requests.invalidate())
</script>

<template>
  <main class="files-page" :data-theme="context.darkTheme ? 'dark' : 'light'" :data-mode="taskMode ? 'task' : 'admin-entry'">
    <header v-if="taskMode" class="task-context-header">
      <div class="task-context-copy">
        <p class="eyebrow">{{ context.entityType || 'Task' }} · {{ currentTask?.task_type_name || 'Task files' }}</p>
        <h1>{{ currentTask?.entity_name || 'Files' }}</h1>
      </div>
      <button class="button secondary refresh" :disabled="loading" @click="load">Refresh</button>
    </header>

    <section v-if="!taskMode" class="admin-entry" aria-labelledby="admin-entry-heading">
      <p class="eyebrow">Production administration</p>
      <h1 id="admin-entry-heading">Files configuration</h1>
      <p>Daily file work belongs in the Files tab inside a Kitsu Task. Production Drive connection, verified root, templates, activation, and diagnostics are managed by the VFX Files Plugin.</p>
      <a class="button secondary" :href="driveSettingsHref">Open VFX Files Plugin settings</a>
    </section>

    <div v-if="taskMode && error" class="notice" role="alert"><span class="notice-dot"></span>{{ error }}</div>
    <div v-if="taskMode && loading" class="loading-state" role="status">Loading Task files…</div>
    <template v-else-if="taskMode && overview">
      <section v-if="taskId" class="panel task-folders" aria-label="Task shared folders">
        <div class="panel-heading"><div><p class="eyebrow">Kitsu Task context</p><h2>Shared folders</h2></div></div>
        <p v-if="folderState?.error" class="upload-error" role="alert">{{ folderState.error }}</p>
        <p v-else-if="verifiedFolderLinks.length" class="folder-links"><a v-for="folder in verifiedFolderLinks" :key="folder.scope_key" :href="folder.url" target="_blank" rel="noopener noreferrer">Open {{ folder.name }}</a></p>
        <p v-else class="muted">No verified Task folder bindings are ready yet. Production Drive configuration is managed in VFX Files Plugin settings.</p>
      </section>
      <UploadPanel :enabled="overview.upload_ready" :tasks="overview.tasks" :fixed-task-id="taskId" :api="api" @uploaded="load" />
      <OutputPackagePanel :tasks="overview.tasks" :publishes="overview.publishes" :fixed-task-id="taskId" :api="api" @changed="load" />
      <PublishWorkflow :production-id="productionId" :tasks="overview.tasks" :publishes="overview.publishes" :fixed-task-id="taskId" :api="api" @changed="load" />
      <div class="content-grid">
        <PublishList :publishes="overview.publishes" :actor-id="overview.actor_id" :role="overview.role" :busy-id="busyId" @approve="approve" />
        <HandoffPanel :handoffs="receivedHandoffs" :busy-id="busyId" @respond="respond" />
      </div>
      <footer class="page-footer"><span>VFX Files plugin</span><span>Published and approved versions are tracked separately.</span></footer>
    </template>
  </main>
</template>
