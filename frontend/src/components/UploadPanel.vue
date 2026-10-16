<script setup>
import { computed, ref, watch } from 'vue'

const props = defineProps({
  enabled: { type: Boolean, default: false },
  fixedTaskId: { type: String, default: null },
  tasks: { type: Array, default: () => [] },
  api: { type: Object, required: true }
})
const emit = defineEmits(['uploaded'])
const taskId = ref(props.fixedTaskId || '')
const busy = ref(false)
const progress = ref(0)
const message = ref('')
const problem = ref('')
const working = ref({ latest_working_id: null, revisions: [] })
const resumeRevision = ref(null)
const fileInput = ref(null)
const fileType = ref('working')
const customFileType = ref('')
const includeIntermediate = ref(false)
let workingRequest = 0
const percent = computed(() => progress.value ? Math.round(progress.value * 100) : 0)

async function loadWorking() {
  const requestId = ++workingRequest
  if (!taskId.value) {
    working.value = { latest_working_id: null, revisions: [] }
    return
  }
  try {
    const result = await props.api.listTaskWorkingRevisions(taskId.value)
    if (requestId === workingRequest) working.value = result
  } catch (error) {
    if (requestId === workingRequest) problem.value = error.message || 'Working revisions could not be loaded.'
  }
}

watch(taskId, (next, previous) => {
  if (previous && next !== previous) includeIntermediate.value = false
  loadWorking()
}, { immediate: true })
watch(() => props.fixedTaskId, (next) => {
  taskId.value = next || ''
  workingRequest += 1
  working.value = { latest_working_id: null, revisions: [] }
  resumeRevision.value = null
  includeIntermediate.value = false
  problem.value = ''
  message.value = ''
})

async function chooseFile(event) {
  const files = [...(event.target.files || [])]
  event.target.value = ''
  if (!files.length || !taskId.value || busy.value) return
  busy.value = true
  message.value = ''
  problem.value = ''
  progress.value = 0
  try {
    const selectedType = fileType.value === '__custom__' ? customFileType.value.trim() : fileType.value
    if (!selectedType || selectedType.length > 32 || !/^[A-Za-z0-9][A-Za-z0-9 _-]*$/.test(selectedType)) {
      throw new Error('Enter a file type using 1 to 32 letters, digits, spaces, underscores or hyphens.')
    }
    const pendingRevision = resumeRevision.value
    resumeRevision.value = null
    const result = pendingRevision
      ? await props.api.resumeWorkingSnapshot(taskId.value, pendingRevision, files, ({ completed, total }) => {
        progress.value = total ? completed / total : 0
      })
      : await props.api.stageWorkingSnapshot(taskId.value, files, working.value.latest_working_id, ({ completed, total }) => {
      progress.value = total ? completed / total : 0
    }, selectedType, includeIntermediate.value)
    progress.value = 1
    message.value = `Working ${displayVersion(result.revision)} ${pendingRevision ? 'resumed' : 'reserved'}. ${result.jobs.length} transfer job(s) queued.`
    await loadWorking()
    emit('uploaded')
  } catch (error) {
    problem.value = error.message || 'The source file could not be staged.'
    await loadWorking()
  } finally {
    busy.value = false
  }
}

function displayVersion(revision) {
  if (!revision) return ''
  return /^w[0-9]{3,}$/.test(revision.version_label || '')
    ? revision.version_label
    : `w${String(revision.version).padStart(3, '0')}`
}
</script>

<template>
  <section class="upload-panel" aria-labelledby="upload-heading">
    <div class="upload-symbol">＋</div>
    <div class="upload-copy">
      <p class="eyebrow">Immutable Task snapshots</p>
      <h2 id="upload-heading">Working Files</h2>
      <p>Each upload creates a new w### Working snapshot. Earlier revisions remain available.</p>
    </div>
    <select v-if="!fixedTaskId" v-model="taskId" class="task-select" aria-label="Choose Task" :disabled="!enabled || busy">
      <option value="">Choose Task</option>
      <option v-for="task in tasks" :key="task.id" :value="task.id">{{ task.task_type_id.slice(0, 8) }} · {{ task.entity_id.slice(0, 8) }}</option>
    </select>
    <label class="workflow-field">File type
      <select v-model="fileType" class="task-select" aria-label="Working file type" :disabled="!enabled || busy || Boolean(resumeRevision)">
        <option value="working">Working</option>
        <option value="__custom__">Custom type…</option>
      </select>
    </label>
    <input v-if="fileType === '__custom__'" v-model="customFileType" class="task-select" aria-label="Custom file type" maxlength="32" placeholder="e.g. Tracking data" :disabled="!enabled || busy || Boolean(resumeRevision)">
    <label class="workflow-field"><input v-model="includeIntermediate" type="checkbox" :disabled="!enabled || busy || Boolean(resumeRevision)"> Include cache, simulation, or intermediate renders</label>
    <label class="button primary file-button" :class="{ disabled: !enabled || !taskId || busy }">
      {{ busy ? 'Uploading…' : 'Choose files' }}
      <input ref="fileInput" type="file" multiple :disabled="!enabled || !taskId || busy" @change="chooseFile">
    </label>
    <div v-if="busy || progress" class="upload-progress" role="status">
      <div class="progress-track"><span :style="{ width: `${percent}%` }"></span></div>
      <span>{{ percent }}%</span>
    </div>
    <p v-if="message" class="upload-result">{{ message }}</p>
    <p v-if="problem" class="upload-error" role="alert">{{ problem }}</p>
    <p v-if="!enabled" class="upload-disabled">Storage, durable staging, and the worker must be configured before uploads are enabled.</p>
    <section v-if="taskId" class="working-revisions" aria-label="Working revisions">
      <div class="revision-heading"><strong>Latest Working</strong><span class="mono">{{ displayVersion(working.revisions.find((item) => item.id === working.latest_working_id)) || '—' }}</span><button class="text-button" :disabled="busy" @click="loadWorking">Refresh revisions</button></div>
      <p v-if="!working.revisions.length" class="empty-state compact">No Working snapshots for this Task yet.</p>
      <ol v-else class="revision-list">
        <li v-for="revision in working.revisions" :key="revision.id" :data-state="revision.state">
          <span class="revision-version">{{ displayVersion(revision) }}</span>
          <span class="file-state">{{ revision.state.replaceAll('_', ' ') }}</span>
          <span v-if="revision.id === working.latest_working_id" class="pointer published">Latest Working</span>
          <button v-if="revision.state === 'receiving'" class="text-button" :disabled="busy" @click="resumeRevision = revision; fileInput?.click()">Resume upload</button>
          <span v-for="file in revision.files" :key="file.id" class="revision-file">
            <a v-if="file.state === 'completed'" :href="api.workingFileDownloadUrl(file.id)">{{ file.name }}</a>
            <span v-else>{{ file.name }}</span>
            <small>{{ file.category }} · {{ file.state === 'completed' ? `${file.size} bytes · ${file.sha256?.slice(0, 12)}` : file.state.replaceAll('_', ' ') }}</small>
          </span>
        </li>
      </ol>
    </section>
  </section>
</template>
