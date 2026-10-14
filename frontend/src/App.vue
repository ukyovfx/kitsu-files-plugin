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
const scopeKey = computed(() => `${productionId.value || ''}:${taskId.value || ''}`)
const folderState = ref(null)
const verifiedFolderLinks = computed(() => safeTaskFolderLinks(folderState.value?.folders))
const requests = createRequestGeneration()
const receivedHandoffs = computed(() => (overview.value?.handoffs || []).filter((item) => item.recipient_id === overview.value?.actor_id || (taskId.value && item.recipient_task_id === taskId.value)))
let pollTimer
let loadedScope = ''

async function load() {
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
    error.value = 'Open Files from a Production menu to load its authorized file records.'
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
  load()
  pollTimer = window.setInterval(() => {
    if (overview.value?.publishes.some((publish) => !['completed', 'failed', 'reconcile_required'].includes(publish.state))) load()
  }, 3000)
})
onUnmounted(() => window.clearInterval(pollTimer))
onUnmounted(() => requests.invalidate())
</script>

<template>
  <main class="files-page" :data-theme="context.darkTheme ? 'dark' : 'light'">
    <header class="page-header">
      <div class="brand-mark">VF</div>
      <div class="heading-copy">
        <p class="eyebrow">Production workspace</p>
        <h1>Files</h1>
        <p class="subtitle">Versioned work, published artifacts and task handoffs.</p>
      </div>
      <button class="button secondary refresh" :disabled="loading" @click="load">Refresh</button>
    </header>

    <div v-if="error" class="notice" role="alert"><span class="notice-dot"></span>{{ error }}</div>
    <div v-if="loading" class="loading-state" role="status">Loading Production files…</div>
    <template v-else-if="overview">
      <div class="scope-line"><span class="scope-dot"></span>Production <span class="mono">{{ overview.production_id }}</span><span v-if="overview.task_id" class="scope-separator">/ Task <span class="mono">{{ overview.task_id }}</span></span></div>
      <section class="summary-grid" aria-label="Publish pointers">
        <article class="summary-card published-card"><span class="card-label">Latest Published</span><strong>{{ overview.publishes.filter((item) => item.latest_published).length }}</strong><span>current output pointers</span></article>
        <article class="summary-card approved-card"><span class="card-label">Latest Approved</span><strong>{{ overview.publishes.filter((item) => item.latest_approved).length }}</strong><span>reviewed version pointers</span></article>
        <article class="summary-card handoff-card"><span class="card-label">Open Handoffs</span><strong>{{ overview.handoffs.filter((item) => item.state === 'pending').length }}</strong><span>awaiting a response</span></article>
      </section>
      <section v-if="taskId" class="panel task-folders" aria-label="Task shared folders">
        <div class="panel-heading"><div><p class="eyebrow">Kitsu Task context</p><h2>Shared folders</h2></div></div>
        <p v-if="folderState?.error" class="upload-error" role="alert">{{ folderState.error }}</p>
        <p v-else-if="verifiedFolderLinks.length" class="folder-links"><a v-for="folder in verifiedFolderLinks" :key="folder.scope_key" :href="folder.url" target="_blank" rel="noopener noreferrer">Open {{ folder.name }}</a></p>
        <p v-else class="muted">No verified Task folder bindings are ready yet. Drive connection, root, templates and synchronization are managed through KitsuSync.</p>
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
