<script setup>
import { computed, ref, watch } from 'vue'

const props = defineProps({ productionId: { type: String, required: true }, tasks: { type: Array, default: () => [] }, publishes: { type: Array, default: () => [] }, api: { type: Object, required: true } })
const emit = defineEmits(['changed'])
const taskId = ref('')
const options = ref({ families: [], recipients: [] })
const revisions = ref({ revisions: [], latest_working_id: null })
const selectedFamilyId = ref('')
const selectedFileId = ref('')
const selectedPublishId = ref('')
const recipientId = ref('')
const loading = ref(false)
const busy = ref(false)
const error = ref('')
const status = ref('')
const completedFiles = computed(() => revisions.value.revisions.flatMap((revision) => revision.state === 'completed'
  ? revision.files.filter((file) => file.state === 'completed').map((file) => ({ ...file, revision }))
  : []))
const completedPublishes = computed(() => props.publishes.filter((row) => row.task_id === taskId.value && row.state === 'completed'))

async function loadTask() {
  options.value = { families: [], recipients: [] }
  revisions.value = { revisions: [], latest_working_id: null }
  selectedFamilyId.value = ''
  selectedFileId.value = ''
  selectedPublishId.value = ''
  recipientId.value = ''
  if (!taskId.value) return
  loading.value = true
  error.value = ''
  try {
    const [taskOptions, working] = await Promise.all([props.api.getTaskPublishOptions(taskId.value), props.api.listTaskWorkingRevisions(taskId.value)])
    options.value = taskOptions
    revisions.value = working
    selectedFamilyId.value = taskOptions.families[0]?.id || ''
    selectedPublishId.value = completedPublishes.value[0]?.id || ''
    recipientId.value = taskOptions.recipients[0]?.id || ''
  } catch (cause) {
    error.value = cause.message || 'Publish options could not be loaded.'
  } finally {
    loading.value = false
  }
}

watch(taskId, loadTask)
watch(() => props.publishes, () => {
  if (!selectedPublishId.value || !completedPublishes.value.some((row) => row.id === selectedPublishId.value)) {
    selectedPublishId.value = completedPublishes.value[0]?.id || ''
  }
})

async function publish() {
  busy.value = true
  error.value = ''
  status.value = ''
  try {
    const result = await props.api.createPublish(taskId.value, selectedFamilyId.value, selectedFileId.value)
    status.value = `Publish v${String(result.version).padStart(3, '0')} queued (${result.id}).`
    await emit('changed')
  } catch (cause) {
    error.value = cause.message || 'Publish could not be queued.'
  } finally {
    busy.value = false
  }
}

async function handoff() {
  busy.value = true
  error.value = ''
  status.value = ''
  try {
    const result = await props.api.createHandoff(selectedPublishId.value, recipientId.value)
    status.value = `Handoff ${result.state} (${result.id}).`
    await emit('changed')
  } catch (cause) {
    error.value = cause.message || 'Handoff could not be created.'
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <section class="panel publish-workflow" aria-labelledby="publish-workflow-heading">
    <div class="panel-heading">
      <div><p class="eyebrow">Working → Publish</p><h2 id="publish-workflow-heading">Publish and hand off</h2></div>
      <span class="count-pill">Immutable versions</span>
    </div>
    <div class="workflow-controls">
      <label class="workflow-field">Task
        <select v-model="taskId" class="task-select" aria-label="Publish Task"><option value="">Choose Task</option><option v-for="task in tasks" :key="task.id" :value="task.id">{{ task.id }}</option></select>
      </label>
      <p v-if="loading" class="loading-state" role="status">Loading Working revisions and Publish options…</p>
      <template v-else-if="taskId">
        <p v-if="!completedFiles.length" class="muted">A completed Working revision is required before publishing.</p>
        <div v-else class="workflow-row">
          <label class="workflow-field">Completed Working File
            <select v-model="selectedFileId" class="task-select" aria-label="Completed Working File"><option value="">Select a completed file</option><option v-for="file in completedFiles" :key="file.id" :value="file.id">{{ file.revision.version_label }} · {{ file.name }}</option></select>
          </label>
          <label class="workflow-field">Output family
            <select v-model="selectedFamilyId" class="task-select" aria-label="Output family"><option value="">Select output family</option><option v-for="family in options.families" :key="family.id" :value="family.id">{{ family.name }}</option></select>
          </label>
          <button class="button primary" :disabled="busy || !selectedFamilyId || !selectedFileId" @click="publish">{{ busy ? 'Queueing…' : 'Publish Working File' }}</button>
        </div>
        <p v-if="!options.families.length" class="muted">No enabled Output Family is configured for this Task.</p>
        <template v-if="completedPublishes.length && options.recipients.length">
          <div class="workflow-row handoff-create">
            <label class="workflow-field">Completed Publish
              <select v-model="selectedPublishId" class="task-select" aria-label="Handoff Publish"><option v-for="row in completedPublishes" :key="row.id" :value="row.id">v{{ String(row.version).padStart(3, '0') }} · {{ row.id }}</option></select>
            </label>
            <label class="workflow-field">Recipient
              <select v-model="recipientId" class="task-select" aria-label="Handoff recipient"><option v-for="person in options.recipients" :key="person.id" :value="person.id">{{ person.name }}</option></select>
            </label>
            <button class="button secondary" :disabled="busy || !selectedPublishId || !recipientId" @click="handoff">Create Handoff</button>
          </div>
        </template>
      </template>
      <p v-if="status" class="upload-result" role="status">{{ status }}</p>
      <p v-if="error" class="upload-error" role="alert">{{ error }}</p>
    </div>
  </section>
</template>
