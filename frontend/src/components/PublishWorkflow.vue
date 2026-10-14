<script setup>
import { computed, ref, watch } from 'vue'

const props = defineProps({ productionId: { type: String, required: true }, tasks: { type: Array, default: () => [] }, publishes: { type: Array, default: () => [] }, fixedTaskId: { type: String, default: null }, api: { type: Object, required: true } })
const emit = defineEmits(['changed'])
const taskId = ref(props.fixedTaskId || '')
const options = ref({ families: [], recipients: [], department_targets: [] })
const revisions = ref({ revisions: [], latest_working_id: null })
const selectedFamilyId = ref('')
const selectedFileId = ref('')
const selectedPublishId = ref('')
const recipientId = ref('')
const departmentTargets = ref([])
const loading = ref(false)
const busy = ref(false)
const error = ref('')
const status = ref('')
let taskRequest = 0
const completedFiles = computed(() => revisions.value.revisions.flatMap((revision) => revision.state === 'completed'
  ? revision.files.filter((file) => file.state === 'completed').map((file) => ({ ...file, revision }))
  : []))
const completedPublishes = computed(() => props.publishes.filter((row) => row.task_id === taskId.value && row.state === 'completed'))
const workingVersionLabel = (revision) => `v${String(revision.version).padStart(3, '0')}`

async function loadTask() {
  const requestId = ++taskRequest
  options.value = { families: [], recipients: [], department_targets: [] }
  revisions.value = { revisions: [], latest_working_id: null }
  selectedFamilyId.value = ''
  selectedFileId.value = ''
  selectedPublishId.value = ''
  recipientId.value = ''
  departmentTargets.value = []
  if (!taskId.value) return
  loading.value = true
  error.value = ''
  try {
    const [taskOptions, working] = await Promise.all([props.api.getTaskPublishOptions(taskId.value), props.api.listTaskWorkingRevisions(taskId.value)])
    if (requestId !== taskRequest) return
    options.value = taskOptions
    revisions.value = working
    selectedFamilyId.value = taskOptions.families[0]?.id || ''
    selectedPublishId.value = completedPublishes.value[0]?.id || ''
    recipientId.value = taskOptions.recipients[0]?.id || ''
    departmentTargets.value = []
  } catch (cause) {
    if (requestId === taskRequest) error.value = cause.message || 'Publish options could not be loaded.'
  } finally {
    if (requestId === taskRequest) loading.value = false
  }
}

watch(taskId, loadTask, { immediate: true })
watch(() => props.fixedTaskId, (next) => {
  taskRequest += 1
  taskId.value = next || ''
  options.value = { families: [], recipients: [], department_targets: [] }
  revisions.value = { revisions: [], latest_working_id: null }
  error.value = ''
  status.value = ''
})
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

async function departmentHandoff() {
  const targets = options.value.department_targets.filter((item) => departmentTargets.value.includes(item.task_id))
  if (!targets.length) return
  busy.value = true
  error.value = ''
  status.value = ''
  try {
    let created = 0
    let failed = 0
    for (const target of targets) {
      try {
        await props.api.createDepartmentHandoff(selectedPublishId.value, target.department_id, target.task_id)
        created += 1
      } catch {
        failed += 1
      }
    }
    status.value = `Created or reused ${created} independent Department handoff(s).${failed ? ` ${failed} failed; refresh before retrying.` : ''}`
    if (failed) error.value = 'Some recipients could not be reached. Successful handoffs remain persisted independently.'
    await emit('changed')
  } catch (cause) {
    error.value = cause.message || 'Department handoff could not be queued.'
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
    <p class="muted workflow-note">Publish Handoffs reference the exact native Zou OutputFile. Output Package deliveries use a verified immutable file manifest; the Files page links the records only when a single-file source and SHA-256 match, so approval is never inferred from a similar name or timestamp.</p>
    <div class="workflow-controls">
      <p v-if="fixedTaskId" class="task-context">Current Kitsu Task <span class="mono">{{ fixedTaskId }}</span></p>
      <label v-else class="workflow-field">Task
        <select v-model="taskId" class="task-select" aria-label="Publish Task"><option value="">Choose Task</option><option v-for="task in tasks" :key="task.id" :value="task.id">{{ task.id }}</option></select>
      </label>
      <p v-if="loading" class="loading-state" role="status">Loading Working revisions and Publish options…</p>
      <template v-else-if="taskId">
        <p v-if="!completedFiles.length" class="muted">A completed Working revision is required before publishing.</p>
        <div v-else class="workflow-row">
          <label class="workflow-field">Completed Working File
            <select v-model="selectedFileId" class="task-select" aria-label="Completed Working File"><option value="">Select a completed file</option><option v-for="file in completedFiles" :key="file.id" :value="file.id">{{ workingVersionLabel(file.revision) }} · {{ file.category }} · {{ file.name }}</option></select>
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
        <div v-if="completedPublishes.length && options.department_targets?.length" class="workflow-row handoff-create">
          <label class="workflow-field">Receiving Task Types / Departments
            <select v-model="departmentTargets" multiple class="task-select" aria-label="Receiving Task Types"><option v-for="target in options.department_targets" :key="target.task_id" :value="target.task_id">{{ target.task_type_name }} · {{ target.task_id.slice(0, 8) }}</option></select>
          </label>
          <button class="button secondary" :disabled="busy || !selectedPublishId || !departmentTargets.length" @click="departmentHandoff">Distribute Publish</button>
        </div>
      </template>
      <p v-if="status" class="upload-result" role="status">{{ status }}</p>
      <p v-if="error" class="upload-error" role="alert">{{ error }}</p>
    </div>
  </section>
</template>
