<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'

const props = defineProps({ taskId: { type: String, default: null }, tasks: { type: Array, default: () => [] }, publishes: { type: Array, default: () => [] }, fixedTaskId: { type: String, default: null }, api: { type: Object, required: true } })
const emit = defineEmits(['changed'])
const selectedTaskId = ref(props.fixedTaskId || props.taskId || '')
const requestGeneration = ref(0)
const loading = ref(false)
const submitting = ref(false)
const error = ref('')
const options = ref({ output_types: [], storage_root_id: '' })
const packages = ref([])
const currentParent = ref('')
const folders = ref([])
const objects = ref([])
const pageToken = ref(null)
const moreObjects = ref(false)
const selectedIds = ref([])
const frameNumbers = ref({})
const packageType = ref('tracking-data')
const outputTypeId = ref('')
const frameStart = ref('')
const frameEnd = ref('')
const colorSpace = ref('')
const expandedPackage = ref('')
const packageFiles = ref({})
const taskDeliveries = ref([])
const deliveryTargets = ref({})
const selectedDeliveryTargets = ref({})
const selectedPublications = ref({})
const deliveryFeedback = ref({})
const deliveryFiles = ref({})
const deliveryBusy = ref('')
const workingRevisions = ref({ revisions: [], latest_working_id: null })
const sourceWorkingRevisionId = ref('')
const driveCheckedAt = ref('')
const driveSyncStatus = ref('not checked')
let pollTimer
let driveSyncTimer
let pendingKey = ''

const packageTypes = [
  ['tracking-data', 'Tracking / Camera'], ['roto-matte', 'Roto / Matte'],
  ['layout-animation', 'Layout / Animation'], ['fx-cache', 'FX Cache / Simulation'],
  ['low-res-video', 'Low-Res Video'], ['low-res-sequence', 'Low-Res Image Sequence'],
  ['lighting-aov', 'Lighting AOV / Deep EXR'], ['precomp', 'Precomp'],
  ['final-exr', 'Final EXR Sequence'], ['final-dpx', 'Final DPX Sequence'],
  ['preview-proxy', 'Review Proxy'], ['delivery-metadata', 'Delivery Metadata']
]
const sequencePackage = computed(() => ['low-res-sequence', 'lighting-aov', 'final-exr', 'final-dpx'].includes(packageType.value))
const selectedFiles = computed(() => objects.value.filter((item) => selectedIds.value.includes(item.id)))
const taskScope = computed(() => props.fixedTaskId || selectedTaskId.value)

function taskLabel(task) {
  const entity = task.entity_name || task.entity_id || 'Entity'
  const type = task.task_type_name || task.task_type_id || 'Task'
  return `${entity} · ${type} · ${task.name || task.id}`
}

async function loadObjects(parentId = currentParent.value, token = null, append = false, requestId = requestGeneration.value) {
  const result = await props.api.listTaskStorageObjects(taskScope.value, parentId, token)
  if (requestId !== requestGeneration.value || result.task_id !== taskScope.value) return
  driveCheckedAt.value = result.checked_at || ''
  driveSyncStatus.value = result.sync_status || 'unknown'
  currentParent.value = result.parent_id
  objects.value = append ? [...objects.value, ...result.items] : result.items
  pageToken.value = result.next_page_token
  moreObjects.value = Boolean(result.next_page_token)
}

async function refresh(requestId = ++requestGeneration.value) {
  if (!taskScope.value) {
    loading.value = false
    error.value = ''
    packages.value = []
    objects.value = []
    taskDeliveries.value = []
    deliveryTargets.value = {}
    deliveryFiles.value = {}
    workingRevisions.value = { revisions: [], latest_working_id: null }
    sourceWorkingRevisionId.value = ''
    return
  }
  loading.value = true
  error.value = ''
  driveCheckedAt.value = ''
  driveSyncStatus.value = 'not checked'
  selectedIds.value = []
  folders.value = []
  packageFiles.value = {}
  taskDeliveries.value = []
  deliveryTargets.value = {}
  deliveryFiles.value = {}
  expandedPackage.value = ''
  try {
    const [packageOptions, currentPackages, currentWorking, currentDeliveries] = await Promise.all([
      props.api.getTaskOutputPackageOptions(taskScope.value),
      props.api.listTaskOutputPackages(taskScope.value),
      props.api.listTaskWorkingRevisions(taskScope.value),
      props.api.listTaskPackageDeliveries(taskScope.value)
    ])
    if (requestId !== requestGeneration.value || packageOptions.task_id !== taskScope.value) return
    options.value = packageOptions
    packages.value = currentPackages
    selectedPublications.value = Object.fromEntries(currentPackages.map((item) => [
      item.id,
      approvedPublicationCandidates(item)[0]?.id || '',
    ]))
    workingRevisions.value = currentWorking
    taskDeliveries.value = currentDeliveries.deliveries || []
    sourceWorkingRevisionId.value = currentWorking.latest_working_id || ''
    currentParent.value = packageOptions.storage_root_id
    if (!outputTypeId.value || !packageOptions.output_types.some((item) => item.id === outputTypeId.value)) {
      outputTypeId.value = packageOptions.output_types[0]?.id || ''
    }
    await loadObjects(packageOptions.storage_root_id, null, false, requestId)
  } catch (cause) {
    if (requestId === requestGeneration.value) error.value = cause.message || 'Output Packages could not be loaded.'
  } finally {
    if (requestId === requestGeneration.value) loading.value = false
  }
}

async function refreshPackageList() {
  const requestId = requestGeneration.value
  const result = await props.api.listTaskOutputPackages(taskScope.value)
  if (requestId === requestGeneration.value && result.every((item) => item.task_id === taskScope.value)) packages.value = result
}

function openFolder(item) {
  folders.value.push({ id: item.id, name: item.name })
  selectedIds.value = []
  loadObjects(item.id).catch((cause) => { error.value = cause.message || 'Folder could not be opened.' })
}

function goToFolder(index) {
  folders.value = folders.value.slice(0, index)
  const parent = folders.value.at(-1)?.id || options.value.storage_root_id
  selectedIds.value = []
  loadObjects(parent).catch((cause) => { error.value = cause.message || 'Folder could not be opened.' })
}

function toggleSelected(item) {
  selectedIds.value = selectedIds.value.includes(item.id)
    ? selectedIds.value.filter((id) => id !== item.id)
    : [...selectedIds.value, item.id]
}

async function registerPackage() {
  if (!selectedFiles.value.length || !outputTypeId.value || !packageType.value) return
  submitting.value = true
  error.value = ''
  pendingKey ||= `output-package:${crypto.randomUUID()}`
  try {
    const descriptor = {
      output_type_id: outputTypeId.value,
      package_type: packageType.value,
      source_working_revision_id: sourceWorkingRevisionId.value || null,
      color_space: colorSpace.value || null,
      files: selectedFiles.value.map((item) => ({
        provider_object_id: item.id,
        frame_number: sequencePackage.value ? Number(frameNumbers.value[item.id]) : null,
        file_format: item.name.includes('.') ? item.name.split('.').at(-1).toLowerCase() : null,
        color_space: colorSpace.value || null,
        is_proxy: packageType.value === 'preview-proxy'
      }))
    }
    if (sequencePackage.value) {
      descriptor.frame_start = Number(frameStart.value)
      descriptor.frame_end = Number(frameEnd.value)
    }
    await props.api.createTaskOutputPackage(taskScope.value, descriptor, pendingKey)
    pendingKey = ''
    emit('changed')
    await refresh()
  } catch (cause) {
    error.value = cause.message || 'Output Package registration failed.'
  } finally {
    submitting.value = false
  }
}

async function togglePackage(item) {
  if (expandedPackage.value === item.id) {
    expandedPackage.value = ''
    return
  }
  expandedPackage.value = item.id
  try {
    const result = await props.api.listOutputPackageFiles(item.id)
    packageFiles.value = { ...packageFiles.value, [item.id]: result }
  } catch (cause) {
    error.value = cause.message || 'Package files could not be loaded.'
  }
}

async function morePackageFiles(item) {
  const prior = packageFiles.value[item.id]
  if (!prior?.next_offset) return
  try {
    const next = await props.api.listOutputPackageFiles(item.id, prior.next_offset)
    packageFiles.value = { ...packageFiles.value, [item.id]: { ...next, files: [...prior.files, ...next.files] } }
  } catch (cause) {
    error.value = cause.message || 'More package files could not be loaded.'
  }
}

async function loadDeliveryTargets(item) {
  try {
    const result = await props.api.listOutputPackageDeliveryTargets(item.id)
    deliveryTargets.value = { ...deliveryTargets.value, [item.id]: result.targets || [] }
    if (!selectedDeliveryTargets.value[item.id] && result.targets?.length) {
      selectedDeliveryTargets.value = {
        ...selectedDeliveryTargets.value,
        [item.id]: `${result.targets[0].task_id}:${result.targets[0].task_type_id}`
      }
    }
  } catch (cause) {
    error.value = cause.message || 'Delivery targets could not be loaded.'
  }
}

function recipientLabel(target) {
  const team = target.department_name || 'Task Type team'
  return `${team} · ${target.task_type_name} · ${target.task_name}`
}

function approvedPublicationCandidates(item) {
  const candidateIds = new Set((item.approved_publish_candidates || []).map((row) => row.id))
  return props.publishes.filter((row) => candidateIds.has(row.id) && row.task_id === item.task_id && row.state === 'completed' && row.qc_state === 'passed')
}

async function linkFormalPublish(item) {
  const publishId = selectedPublications.value[item.id]
  if (!publishId) return
  deliveryBusy.value = item.id
  error.value = ''
  try {
    await props.api.linkOutputPackagePublication(item.id, publishId, `package-publication:${item.id}:${publishId}`)
    await refresh()
    emit('changed')
  } catch (cause) {
    error.value = cause.message || 'The exact approved Publish could not be linked to this package.'
  } finally {
    deliveryBusy.value = ''
  }
}

async function sendPackage(item) {
  const selected = selectedDeliveryTargets.value[item.id]
  const target = (deliveryTargets.value[item.id] || []).find((row) => `${row.task_id}:${row.task_type_id}` === selected)
  if (!target) return
  deliveryBusy.value = item.id
  error.value = ''
  try {
    const idempotencyKey = `package-delivery:${item.id}:${target.task_id}:${target.task_type_id}`
    await props.api.createOutputPackageDelivery(
      item.id, target.task_id, target.task_type_id, idempotencyKey
    )
    await refresh()
  } catch (cause) {
    error.value = cause.message || 'Output Package delivery failed.'
  } finally {
    deliveryBusy.value = ''
  }
}

async function respondToDelivery(delivery, status) {
  const feedback = deliveryFeedback.value[delivery.id] || ''
  if (status === 'needs_fix' && !feedback.trim()) {
    error.value = 'Add feedback before requesting fixes.'
    return
  }
  deliveryBusy.value = delivery.id
  error.value = ''
  try {
    const idempotencyKey = `package-delivery-response:${delivery.id}:${delivery.revision}:${status}`
    await props.api.respondToOutputPackageDelivery(
      delivery.id, status, status === 'needs_fix' ? feedback : null,
      idempotencyKey
    )
    await refresh()
  } catch (cause) {
    error.value = cause.message || 'Delivery response could not be saved.'
  } finally {
    deliveryBusy.value = ''
  }
}

async function loadDeliveryFiles(delivery) {
  try {
    const result = await props.api.listOutputPackageFiles(delivery.package_id)
    deliveryFiles.value = { ...deliveryFiles.value, [delivery.id]: result.files || [] }
  } catch (cause) {
    error.value = cause.message || 'Delivered files could not be loaded.'
  }
}

watch(taskScope, () => refresh())
watch(() => props.fixedTaskId, (value) => { selectedTaskId.value = value || props.taskId || '' })
watch(() => props.taskId, (value) => { if (!props.fixedTaskId) selectedTaskId.value = value || '' })
onMounted(() => {
  refresh()
  pollTimer = window.setInterval(() => {
    if (packages.value.some((item) => ['verifying', 'reconcile_required'].includes(item.state))) {
      refreshPackageList().catch(() => {})
    }
  }, 3000)
  driveSyncTimer = window.setInterval(() => {
    if (taskScope.value && !loading.value) {
      loadObjects(currentParent.value || options.value.storage_root_id, null, false, requestGeneration.value)
        .catch((cause) => { error.value = cause.message || 'Drive revalidation failed.' })
    }
  }, 5 * 60 * 1000)
})
onUnmounted(() => {
  requestGeneration.value += 1
  window.clearInterval(pollTimer)
  window.clearInterval(driveSyncTimer)
})
</script>

<template>
  <section class="panel output-packages" aria-label="Output Packages">
    <div class="panel-heading">
      <div><p class="eyebrow">Task deliverables</p><h2>Output Packages</h2></div>
      <button class="button secondary" :disabled="loading" @click="refresh()">Refresh</button>
    </div>
    <label v-if="!fixedTaskId" class="workflow-field">Task
      <select v-model="selectedTaskId" class="task-select" aria-label="Output Package Task">
        <option value="">Choose Task</option>
        <option v-for="task in tasks" :key="task.id" :value="task.id">{{ taskLabel(task) }}</option>
      </select>
    </label>
    <p v-if="!taskScope" class="muted">Choose a Kitsu Task to browse authorized files and register Output Packages.</p>
    <p v-if="error" class="upload-error" role="alert">{{ error }}</p>
    <p v-if="loading" class="muted" role="status">Loading authorized Drive objects and package history…</p>
    <p v-if="taskScope && driveCheckedAt" class="muted" role="status">Drive sync: {{ driveSyncStatus }} · last checked {{ new Date(driveCheckedAt).toLocaleString() }}. This check covers the currently opened folder listing.</p>
    <template v-if="taskScope && !loading">
      <div class="package-register">
        <label>Output Type
          <select v-model="outputTypeId" aria-label="Output Type">
            <option v-for="item in options.output_types" :key="item.id" :value="item.id">{{ item.name }}</option>
          </select>
        </label>
        <label>Package kind
          <select v-model="packageType" aria-label="Package kind">
            <option v-for="[value, label] in packageTypes" :key="value" :value="value">{{ label }}</option>
          </select>
        </label>
        <label>Source Working Revision
          <select v-model="sourceWorkingRevisionId" aria-label="Source Working Revision">
            <option value="">No Working Revision</option>
            <option v-for="revision in workingRevisions.revisions.filter((item) => item.state === 'completed')" :key="revision.id" :value="revision.id">{{ revision.version_label }} · {{ revision.created_by }}</option>
          </select>
        </label>
        <label v-if="sequencePackage">Frame start<input v-model="frameStart" type="number" step="1" aria-label="Frame start"></label>
        <label v-if="sequencePackage">Frame end<input v-model="frameEnd" type="number" step="1" aria-label="Frame end"></label>
        <label>Color space<input v-model="colorSpace" maxlength="128" placeholder="e.g. ACEScg"></label>
      </div>
      <nav class="folder-breadcrumb" aria-label="Drive folder path">
        <button class="text-button" @click="goToFolder(0)">Allowed root</button>
        <template v-for="(folder, index) in folders" :key="folder.id"><span> / </span><button class="text-button" @click="goToFolder(index + 1)">{{ folder.name }}</button></template>
      </nav>
      <div class="storage-object-list">
        <div v-for="item in objects" :key="item.id" class="storage-object-row">
          <button v-if="item.is_folder" class="text-button object-name" @click="openFolder(item)">📁 {{ item.name }}</button>
          <label v-else class="object-name"><input type="checkbox" :checked="selectedIds.includes(item.id)" :disabled="item.size === null" @change="toggleSelected(item)"> {{ item.name }}</label>
          <span class="muted">{{ item.is_folder ? 'Folder' : `${item.size ?? 'Unknown size'} bytes` }}<template v-if="item.modified_time"> · Drive modified {{ new Date(item.modified_time).toLocaleString() }}</template></span>
          <label v-if="sequencePackage && selectedIds.includes(item.id)" class="frame-number">Frame<input v-model="frameNumbers[item.id]" type="number" step="1" :aria-label="`Frame number ${item.name}`"></label>
        </div>
        <p v-if="!objects.length" class="muted">This folder has no visible items.</p>
      </div>
      <button v-if="moreObjects" class="button secondary" @click="loadObjects(currentParent, pageToken, true)">Load next Drive page</button>
      <button class="button primary" :disabled="submitting || !selectedFiles.length || !outputTypeId || (sequencePackage && (frameStart === '' || frameEnd === '' || Number(frameEnd) < Number(frameStart) || selectedFiles.some((item) => frameNumbers[item.id] === undefined || frameNumbers[item.id] === '')))" @click="registerPackage">{{ submitting ? 'Registering…' : `Register ${selectedFiles.length} selected file(s) as one immutable Version` }}</button>
      <p class="muted">Files remain in Google Drive. The worker verifies their IDs, root ancestry, byte counts and SHA-256 before recording QC. Sequence files are one package version with explicit frame numbers.</p>
      <div class="package-history">
        <article v-for="item in packages" :key="item.id" class="package-row">
          <button class="text-button" @click="togglePackage(item)"><strong>{{ item.package_type }} v{{ String(item.version).padStart(3, '0') }}</strong></button>
          <span>{{ item.state }} · QC {{ item.qc_state }}</span>
          <span class="package-pointers" aria-label="Package publication status">{{ item.latest_working ? 'Latest Package' : '' }}<template v-if="item.latest_published"> · Latest Published</template><template v-if="item.latest_approved"> · Latest Approved</template></span>
          <span class="muted">{{ item.file_count }} files · {{ item.manifest_sha256 }}</span>
          <div v-if="item.formal_publish" class="package-publication" role="status">
            Formally Published v{{ String(item.formal_publish.version).padStart(3, '0') }} · {{ item.formal_publish.state }} · QC {{ item.formal_publish.qc_state }}<template v-if="item.formal_publish.approval_active"> · exact approval active</template><template v-else> · approval revoked or unavailable</template>
          </div>
          <div v-else-if="approvedPublicationCandidates(item).length" class="package-publication">
            <label>Exact approved Publish
              <select v-model="selectedPublications[item.id]" :aria-label="`Approved Publish for ${item.id}`">
                <option v-for="publish in approvedPublicationCandidates(item)" :key="publish.id" :value="publish.id">v{{ String(publish.version).padStart(3, '0') }} · {{ publish.id }}</option>
              </select>
            </label>
            <button class="button secondary" :disabled="deliveryBusy === item.id || !selectedPublications[item.id]" @click="linkFormalPublish(item)">{{ deliveryBusy === item.id ? 'Linking…' : 'Confirm Package / Publish match' }}</button>
            <p class="muted">Only exact single-file Working provenance and SHA-256 matches are linkable. The server revalidates both immutable manifests and the active approval.</p>
          </div>
          <p v-else-if="item.state === 'completed' && item.qc_state === 'passed'" class="muted">Package delivery uses this immutable QC-passed manifest. Formal Publish approval is shown only when an exact one-file provenance match is linked; it is never inferred from the package or Drive timestamps.</p>
          <pre v-if="item.qc_report?.missing_frames?.length" class="upload-error">Missing frames: {{ item.qc_report.missing_frames.join(', ') }}</pre>
          <div v-if="item.state === 'completed' && item.qc_state === 'passed'" class="delivery-compose">
            <button class="button secondary" :disabled="deliveryBusy === item.id" @click="loadDeliveryTargets(item)">Choose delivery recipient</button>
            <template v-if="deliveryTargets[item.id]">
              <label>Team / Department / Task
                <select v-model="selectedDeliveryTargets[item.id]" :aria-label="`Delivery recipient for ${item.id}`">
                  <option v-for="target in deliveryTargets[item.id]" :key="`${target.task_id}:${target.task_type_id}`" :value="`${target.task_id}:${target.task_type_id}`">{{ recipientLabel(target) }}</option>
                </select>
              </label>
              <p v-if="!deliveryTargets[item.id].length" class="muted">No eligible receiving Task Type exists for this Production entity.</p>
              <button class="button primary" :disabled="deliveryBusy === item.id || !deliveryTargets[item.id].length" @click="sendPackage(item)">{{ deliveryBusy === item.id ? 'Sending…' : 'Send immutable package version' }}</button>
            </template>
          </div>
          <div v-if="expandedPackage === item.id" class="package-files">
            <div v-for="file in packageFiles[item.id]?.files || []" :key="file.id" class="storage-object-row">
              <span>{{ file.frame_number ?? file.file_name }} · {{ file.byte_count }} bytes · {{ file.sha256 }}</span>
              <a v-if="file.download_url" :href="api.outputPackageDownloadUrl(file.id)">Download verified file</a>
            </div>
            <button v-if="packageFiles[item.id]?.next_offset !== null && packageFiles[item.id]?.next_offset !== undefined" class="button secondary" @click="morePackageFiles(item)">Load next file page</button>
          </div>
        </article>
        <p v-if="!packages.length" class="muted">No Output Packages are registered for this Task.</p>
      </div>
      <section class="delivery-history" aria-label="Recipient deliveries">
        <h3>Recipient deliveries</h3>
        <article v-for="delivery in taskDeliveries" :key="delivery.id" class="delivery-row">
          <strong>{{ delivery.direction === 'incoming' ? 'Incoming' : 'Sent' }} · {{ delivery.package_type }} v{{ String(delivery.package_version).padStart(3, '0') }}</strong>
          <span>{{ delivery.recipient_task_type_name }} · {{ delivery.state }} · revision {{ delivery.revision }}</span>
          <p v-if="delivery.feedback" role="status">Needs Fix feedback: {{ delivery.feedback }}</p>
          <button class="button secondary" @click="loadDeliveryFiles(delivery)">View / download delivered files</button>
            <div v-for="file in deliveryFiles[delivery.id] || []" :key="file.id" class="storage-object-row">
            <span>{{ file.file_name }}<template v-if="file.frame_number !== null && file.frame_number !== undefined"> · frame {{ file.frame_number }}</template> · {{ file.byte_count }} bytes · {{ file.sha256 }}</span>
            <a v-if="file.download_url" :href="api.outputPackageDownloadUrl(file.id)">Download verified file</a>
          </div>
          <label v-if="delivery.direction === 'incoming' && delivery.can_respond">Needs Fix feedback
            <textarea v-model="deliveryFeedback[delivery.id]" maxlength="2000" :aria-label="`Needs Fix feedback ${delivery.id}`"></textarea>
          </label>
          <div v-if="delivery.direction === 'incoming' && delivery.can_respond" class="delivery-actions">
            <button class="button secondary" :disabled="deliveryBusy === delivery.id" @click="respondToDelivery(delivery, 'needs_fix')">Needs Fix</button>
            <button class="button primary" :disabled="deliveryBusy === delivery.id" @click="respondToDelivery(delivery, 'accepted')">Accept</button>
          </div>
          <ol class="delivery-events"><li v-for="(event, index) in delivery.history" :key="`${delivery.id}:${index}`">{{ event.event_type }}<template v-if="event.feedback"> · {{ event.feedback }}</template></li></ol>
        </article>
        <p v-if="!taskDeliveries.length" class="muted">No incoming or outgoing Output Package deliveries for this Task.</p>
      </section>
    </template>
  </section>
</template>
