<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import HandoffPanel from './components/HandoffPanel.vue'
import PublishList from './components/PublishList.vue'
import PublishWorkflow from './components/PublishWorkflow.vue'
import OutputPackagePanel from './components/OutputPackagePanel.vue'
import DriveSettingsPanel from './components/DriveSettingsPanel.vue'
import UploadPanel from './components/UploadPanel.vue'
import TaskLinkShares from './components/TaskLinkShares.vue'
import { taskSections } from './taskNavigation.js'
import { createRequestGeneration, filesSurfaceMode } from './taskContext.js'
import { safeTaskFolderLinks } from './taskFolderLinks.js'

const props = defineProps({ context: { type: Object, required: true }, api: { type: Object, required: true } })
const overview = ref(null)
const loading = ref(true)
const error = ref('')
const busyId = ref('')
const productionId = computed(() => props.context.productionId)
const taskId = computed(() => props.context.taskId || null)
const taskMode = computed(() => filesSurfaceMode(props.context) === 'task')
const scopeKey = computed(() => `${productionId.value || ''}:${taskId.value || ''}`)
const folderState = ref(null)
const activeTaskSection = ref('files')
const verifiedFolderLinks = computed(() => safeTaskFolderLinks(folderState.value?.folders))
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

watch(scopeKey, () => {
  activeTaskSection.value = 'files'
  load()
})

function onTaskSectionKeydown(event, index) {
  if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return
  event.preventDefault()
  const direction = event.key === 'ArrowRight' ? 1 : -1
  const nextIndex = (index + direction + taskSections.length) % taskSections.length
  const tablist = event.currentTarget?.closest('[role="tablist"]')
  activeTaskSection.value = taskSections[nextIndex].id
  nextTick(() => {
    tablist?.querySelectorAll('[role="tab"]')[nextIndex]?.focus()
  })
}

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
  if (!taskMode.value) {
    loading.value = false
    return
  }
  load()
  pollTimer = window.setInterval(() => {
    if (taskMode.value && overview.value?.publishes.some((publish) => !['completed', 'failed', 'reconcile_required'].includes(publish.state))) load()
  }, 3000)
})
onUnmounted(() => window.clearInterval(pollTimer))
onUnmounted(() => requests.invalidate())
</script>

<template>
  <main class="files-page" :data-theme="context.darkTheme ? 'dark' : 'light'" :data-mode="taskMode ? 'task' : 'admin'">
    <DriveSettingsPanel v-if="!taskMode" :production-id="productionId" :api="api" />
    <template v-else>
      <div v-if="error" class="notice" role="alert"><span class="notice-dot"></span>{{ error }}</div>
      <div v-if="loading" class="loading-state" role="status">Loading Task files…</div>
      <template v-else-if="overview">
      <nav class="task-navigation" role="tablist" aria-label="Task Files sections">
        <button
          v-for="(section, index) in taskSections"
          :key="section.id"
          class="task-navigation-item"
          type="button"
          role="tab"
          :id="`task-tab-${section.id}`"
          :aria-selected="activeTaskSection === section.id"
          :tabindex="activeTaskSection === section.id ? 0 : -1"
          aria-controls="task-section-panel"
          :data-availability="section.enabled ? 'ready' : 'gated'"
          @click="activeTaskSection = section.id"
          @keydown="onTaskSectionKeydown($event, index)"
        >{{ section.label }}</button>
      </nav>

      <div id="task-section-panel" role="tabpanel" :aria-labelledby="`task-tab-${activeTaskSection}`" tabindex="0">
      <section v-if="activeTaskSection === 'files'" class="task-section" aria-label="Files for this Task">
        <section v-if="taskId" class="panel task-folders" aria-label="Task shared folders">
          <div class="panel-heading"><div><h2>Task folders</h2></div></div>
          <p v-if="folderState?.error" class="upload-error" role="alert">{{ folderState.error }}</p>
          <p v-else-if="verifiedFolderLinks.length" class="folder-links"><a v-for="folder in verifiedFolderLinks" :key="folder.scope_key" :href="folder.url" target="_blank" rel="noopener noreferrer">Open {{ folder.name }}</a></p>
          <p v-else class="muted">No verified Task folder bindings are ready yet. Folder setup is managed in VFX Files Plugin settings.</p>
        </section>
        <OutputPackagePanel :task-id="taskId" :tasks="overview.tasks" :publishes="overview.publishes" :fixed-task-id="taskId" :api="api" @changed="load" />
      </section>

      <section v-else-if="activeTaskSection === 'working'" class="task-section" aria-label="Working files for this Task">
        <UploadPanel :enabled="overview.upload_ready" :tasks="overview.tasks" :fixed-task-id="taskId" :api="api" @uploaded="load" />
      </section>

      <section v-else-if="activeTaskSection === 'publish'" class="task-section" aria-label="Publish for this Task">
        <PublishWorkflow :production-id="productionId" :tasks="overview.tasks" :publishes="overview.publishes" :fixed-task-id="taskId" :api="api" @changed="load" />
        <PublishList :publishes="overview.publishes" :actor-id="overview.actor_id" :role="overview.role" :busy-id="busyId" @approve="approve" />
      </section>

      <section v-else-if="activeTaskSection === 'handoffs'" class="task-section" aria-label="Department handoffs for this Task">
        <HandoffPanel :handoffs="receivedHandoffs" :busy-id="busyId" @respond="respond" />
      </section>

      <section v-else-if="activeTaskSection === 'share'" class="task-section" aria-label="Share a Drive reference">
        <TaskLinkShares :task-id="taskId" mode="share" :api="api" />
      </section>

      <section v-else-if="activeTaskSection === 'received'" class="task-section" aria-label="Received Files">
        <TaskLinkShares :task-id="taskId" mode="received" :api="api" />
      </section>
      </div>
      </template>
    </template>
  </main>
</template>
