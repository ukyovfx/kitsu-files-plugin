<script setup>
import { computed, ref, watch } from 'vue'

const props = defineProps({
  enabled: { type: Boolean, default: false },
  tasks: { type: Array, default: () => [] },
  api: { type: Object, required: true }
})
const emit = defineEmits(['uploaded'])
const taskId = ref('')
const busy = ref(false)
const progress = ref(0)
const message = ref('')
const problem = ref('')
const working = ref({ latest_working_id: null, revisions: [] })
const resumeRevision = ref(null)
const fileInput = ref(null)
const percent = computed(() => progress.value ? Math.round(progress.value * 100) : 0)

async function loadWorking() {
  if (!taskId.value) {
    working.value = { latest_working_id: null, revisions: [] }
    return
  }
  try {
    working.value = await props.api.listTaskWorkingRevisions(taskId.value)
  } catch (error) {
    problem.value = error.message || 'Working revisions could not be loaded.'
  }
}

watch(taskId, loadWorking)

async function chooseFile(event) {
  const files = [...(event.target.files || [])]
  event.target.value = ''
  if (!files.length || !taskId.value || busy.value) return
  busy.value = true
  message.value = ''
  problem.value = ''
  progress.value = 0
  try {
    const pendingRevision = resumeRevision.value
    resumeRevision.value = null
    const result = pendingRevision
      ? await props.api.resumeWorkingSnapshot(taskId.value, pendingRevision, files, ({ completed, total }) => {
        progress.value = total ? completed / total : 0
      })
      : await props.api.stageWorkingSnapshot(taskId.value, files, working.value.latest_working_id, ({ completed, total }) => {
      progress.value = total ? completed / total : 0
    })
    progress.value = 1
    message.value = `Working ${result.revision.version_label} ${pendingRevision ? 'resumed' : 'reserved'}. ${result.jobs.length} transfer job(s) queued.`
    await loadWorking()
    emit('uploaded')
  } catch (error) {
    problem.value = error.message || 'The source file could not be staged.'
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <section class="upload-panel" aria-labelledby="upload-heading">
    <div class="upload-symbol">＋</div>
    <div class="upload-copy">
      <p class="eyebrow">Immutable Task snapshots</p>
      <h2 id="upload-heading">Working Files</h2>
      <p>Each upload creates a new w### snapshot. Earlier revisions remain available.</p>
    </div>
    <select v-model="taskId" class="task-select" aria-label="Choose Task" :disabled="!enabled || busy">
      <option value="">Choose Task</option>
      <option v-for="task in tasks" :key="task.id" :value="task.id">{{ task.task_type_id.slice(0, 8) }} · {{ task.entity_id.slice(0, 8) }}</option>
    </select>
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
      <div class="revision-heading"><strong>Latest Working</strong><span class="mono">{{ working.revisions.find((item) => item.id === working.latest_working_id)?.version_label || '—' }}</span><button class="text-button" :disabled="busy" @click="loadWorking">Refresh revisions</button></div>
      <p v-if="!working.revisions.length" class="empty-state compact">No Working snapshots for this Task yet.</p>
      <ol v-else class="revision-list">
        <li v-for="revision in working.revisions" :key="revision.id" :data-state="revision.state">
          <span class="revision-version">{{ revision.version_label }}</span>
          <span class="file-state">{{ revision.state.replaceAll('_', ' ') }}</span>
          <span v-if="revision.id === working.latest_working_id" class="pointer published">Latest Working</span>
          <button v-if="revision.state === 'receiving'" class="text-button" :disabled="busy" @click="resumeRevision = revision; fileInput?.click()">Resume upload</button>
          <span v-for="file in revision.files" :key="file.id" class="revision-file">
            <a v-if="file.state === 'completed'" :href="api.workingFileDownloadUrl(file.id)">{{ file.name }}</a>
            <span v-else>{{ file.name }}</span>
            <small>{{ file.state === 'completed' ? `${file.size} bytes · ${file.sha256?.slice(0, 12)}` : file.state.replaceAll('_', ' ') }}</small>
          </span>
        </li>
      </ol>
    </section>
  </section>
</template>
