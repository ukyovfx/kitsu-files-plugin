<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { outputPayloadTransferAvailable } from '../outputTransferAvailability.js'
import { safeDriveUrl } from '../driveLinks.js'
import { importText } from '../personalImportLocale.js'
import { PickerError, pickDriveFiles } from '../googlePicker.js'
import { inferSequenceSelection } from '../sequenceFrameInference.js'

const props = defineProps({ taskId: { type: String, required: true }, api: { type: Object, required: true }, locale: { type: String, default: 'en' } })
const emit = defineEmits(['changed'])
const t = (text, values) => importText(props.locale, text, values)
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
const autoRangeHint = ref(false)
const colorSpace = ref('')
const expandedPackage = ref('')
const packageFiles = ref({})
const driveCheckedAt = ref('')
const driveSyncStatus = ref('not checked')
const pendingDelete = ref(null)
const deleting = ref(false)
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
const taskScope = computed(() => props.taskId)
const fakeTransferAvailable = computed(() => outputPayloadTransferAvailable(options.value.storage_mode))

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

async function refresh(requestId) {
  if (requestId === undefined && (pickerBusy.value || personalSubmitting.value)) return refreshPackageList()
  requestId ??= ++requestGeneration.value
  if (!taskScope.value) return
  loading.value = true
  error.value = ''
  driveCheckedAt.value = ''
  driveSyncStatus.value = 'not checked'
  selectedIds.value = []
  folders.value = []
  packageFiles.value = {}
  expandedPackage.value = ''
  try {
    const [packageOptions, currentPackages] = await Promise.all([
      props.api.getTaskOutputPackageOptions(taskScope.value),
      props.api.listTaskOutputPackages(taskScope.value)
    ])
    if (requestId !== requestGeneration.value || packageOptions.task_id !== taskScope.value) return
    options.value = packageOptions
    packages.value = currentPackages
    loadPersonalInfo(requestId)
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

async function startDelete(item) {
  error.value = ''
  try {
    pendingDelete.value = { item, review: await props.api.getOutputPackageDeletionImpact(item.id) }
  } catch (cause) {
    error.value = cause.message || 'The deletion impact could not be loaded.'
  }
}

async function retryDelete(item) {
  error.value = ''
  try {
    const review = await props.api.getOutputPackageDeletionImpact(item.id)
    await props.api.deleteOutputPackage(item.id, review.impact_sha256)
    await refresh()
  } catch (cause) {
    error.value = cause.message || 'The deletion could not be retried.'
  }
}

async function confirmDelete() {
  if (!pendingDelete.value || deleting.value) return
  deleting.value = true
  error.value = ''
  try {
    await props.api.deleteOutputPackage(pendingDelete.value.item.id, pendingDelete.value.review.impact_sha256)
    pendingDelete.value = null
    await refresh()
  } catch (cause) {
    error.value = cause.message || 'The Output version could not be deleted.'
    pendingDelete.value = null
  } finally {
    deleting.value = false
  }
}

// --- Personal Google Drive import (Picker selections are display hints; the server re-verifies every ID) ---
const personalInfo = ref(null)
const pickedFiles = ref([])
const folderScopeLimited = ref(false)
const folderAcknowledged = ref(false)
const pickerBusy = ref(false)
const pickerError = ref('')
const personalSubmitting = ref(false)
let personalKey = ''
const pickerMessages = computed(() => ({
  folder_access_unverified: t('The folder contents could not be verified. Select its files individually or reconnect the same account.'),
  folder_ancestry_unverified: t('The folder contents could not be verified. Select its files individually or reconnect the same account.'),
  folder_no_readable_files: t('No readable files were found in this folder.'),
  selection_too_large: t('The selection exceeds the configured file or depth limit.'),
  selection_too_deep: t('The selection exceeds the configured file or depth limit.'),
  cancelled: t("Selection cancelled."),
  account_mismatch: t("You are signed in to a different Google account than the one linked to your Kitsu user. Sign in with the linked account and try again."),
  auth_failed: t("Google sign-in did not complete. Allow the popup and try again."),
  script_blocked: t("Google scripts could not be loaded (blocked by the browser or network)."),
  picker_not_configured: t("Google Picker is not configured by the administrator."),
  personal_google_not_linked: t("Connect your Google account first (My Google account below)."),
  personal_google_blocked: t("Personal Google accounts are blocked for this Production."),
  drive_object_unavailable: t("A selected file is not accessible to your linked Google account. Pick it again with the linked account."),
  unsupported_drive_object: t("Google-native documents and shortcuts cannot be imported. Pick regular files or readable folders."),
  unsupported_file_name: t("A selected file has a name that cannot be imported."),
  duplicate_file_name: t("Two selected files have the same name."),
  file_too_large: t("A selected file is larger than the allowed size.")
}))
const copyStatus = computed(() => ({
  queued: t("Waiting to copy"), leased: t("Copying…"), retry: t("Retrying shortly"), failed: t("Failed"), reconcile_required: t("Needs attention"),
  blocked_authorization: t("Blocked (reconnect your Google account or ask an admin)"), completed: t("Copied")
}))
const personalReady = computed(() => Boolean(personalInfo.value?.allowed && personalInfo.value?.linked))
const sequenceCheck = computed(() => {
  if (!sequencePackage.value || !pickedFiles.value.length) return null
  const frames = pickedFiles.value.map((file) => frameNumbers.value[file.id]).map((value) => (value === '' || value === undefined ? NaN : Number(value)))
  const start = Number(frameStart.value)
  const end = Number(frameEnd.value)
  const valid = Number.isSafeInteger(start) && Number.isSafeInteger(end) && end - start <= 10000 && frameStart.value !== '' && frameEnd.value !== '' && end >= start && frames.every((value) => Number.isSafeInteger(value) && value >= start && value <= end)
  const duplicates = valid && new Set(frames).size !== frames.length
  const frameSet = new Set(frames)
  const missing = valid && !duplicates ? Array.from({ length: end - start + 1 }, (_unused, index) => start + index).filter((frame) => !frameSet.has(frame)) : []
  return { valid, duplicates, missing }
})
const guessedSelection = computed(() => sequencePackage.value && pickedFiles.value.length ? inferSequenceSelection(pickedFiles.value) : null)
// Autofill only when every selected name belongs to one sequence. Never override manual frame edits.
watch([pickedFiles, sequencePackage], () => {
  const guessed = guessedSelection.value
  if (!guessed) return
  const next = { ...frameNumbers.value }
  for (const [id, frame] of Object.entries(guessed.frames)) {
    if (next[id] === undefined || next[id] === '') next[id] = String(frame)
  }
  frameNumbers.value = next
  // Inferred endpoints cannot detect missing head/tail frames. Keep them editable.
  if (autoRangeHint.value || (frameStart.value === '' && frameEnd.value === '')) {
    frameStart.value = String(guessed.start)
    frameEnd.value = String(guessed.end)
    autoRangeHint.value = true
  }
})

const personalCanRegister = computed(() => personalReady.value && pickedFiles.value.length && outputTypeId.value &&
  (!folderScopeLimited.value || folderAcknowledged.value) &&
  (!sequencePackage.value || (sequenceCheck.value?.valid && !sequenceCheck.value?.duplicates)))

async function loadPersonalInfo(requestId = requestGeneration.value) {
  const productionId = options.value.production_id
  if (!productionId) return
  try {
    const result = await props.api.getPersonalGoogle(productionId)
    if (requestId === requestGeneration.value) personalInfo.value = result
  } catch { if (requestId === requestGeneration.value) personalInfo.value = null }
}

function describePicker(cause, fallback) {
  const code = cause?.code || cause?.message
  return pickerMessages.value[code] || t(fallback)
}

async function chooseFromGoogleDrive() {
  const requestId = requestGeneration.value
  const selectedTask = taskScope.value
  pickerError.value = ''
  pickerBusy.value = true
  try {
    const config = await props.api.getPersonalPickerConfig(options.value.production_id)
    if (requestId !== requestGeneration.value) return
    let picked = await pickDriveFiles(config)
    if (requestId !== requestGeneration.value) return
    if (picked.some(file => file.mimeType === 'application/vnd.google-apps.folder')) {
      const preview = await props.api.previewPersonalSelection(selectedTask, picked.map(file => ({ drive_file_id: file.id })))
      if (requestId !== requestGeneration.value) return
      picked = preview.files
      folderScopeLimited.value = true
      folderAcknowledged.value = false
    }
    const known = new Set(pickedFiles.value.map((file) => file.id))
    pickedFiles.value = [...pickedFiles.value, ...picked.filter((file) => !known.has(file.id))]
    personalKey = ''
  } catch (cause) {
    if (requestId === requestGeneration.value && !(cause instanceof PickerError && cause.code === 'cancelled')) pickerError.value = describePicker(cause, 'Google Drive selection failed.')
  } finally {
    if (requestId === requestGeneration.value) pickerBusy.value = false
  }
}

function removePicked(file) {
  pickedFiles.value = pickedFiles.value.filter((item) => item.id !== file.id)
  personalKey = ''
}

async function registerPersonalFiles() {
  if (!personalCanRegister.value) return
  const requestId = requestGeneration.value
  const selectedTask = taskScope.value
  personalSubmitting.value = true
  pickerError.value = ''
  personalKey ||= `personal-import:${crypto.randomUUID()}`
  try {
    const descriptor = {
      output_type_id: outputTypeId.value,
      package_type: packageType.value,
      color_space: colorSpace.value || null,
      files: pickedFiles.value.map((file) => ({
        drive_file_id: file.id,
        root_folder_id: file.root_folder_id || null,
        frame_number: sequencePackage.value ? Number(frameNumbers.value[file.id]) : null,
        file_format: file.name.includes('.') ? file.name.split('.').at(-1).toLowerCase() : null,
        color_space: colorSpace.value || null,
        is_proxy: packageType.value === 'preview-proxy'
      }))
    }
    if (sequencePackage.value) {
      descriptor.frame_start = Number(frameStart.value)
      descriptor.frame_end = Number(frameEnd.value)
    }
    await props.api.createPersonalOutputPackage(selectedTask, descriptor, personalKey)
    if (requestId !== requestGeneration.value) return
    personalKey = ''
    pickedFiles.value = []
    frameNumbers.value = {}
    frameStart.value = ''
    frameEnd.value = ''
    autoRangeHint.value = false
    emit('changed')
    await refreshPackageList()
  } catch (cause) {
    if (requestId === requestGeneration.value) pickerError.value = describePicker(cause, 'The Google Drive files could not be registered.')
  } finally {
    if (requestId === requestGeneration.value) personalSubmitting.value = false
  }
}

async function retryCopy(item) {
  const requestId = requestGeneration.value
  error.value = ''
  try {
    await props.api.retryOutputPackage(item.id)
    if (requestId !== requestGeneration.value) return
    await refreshPackageList()
  } catch (cause) {
    if (requestId === requestGeneration.value) error.value = describePicker(cause, 'The copy could not be retried.')
  }
}

function copyError(code) {
  const messages = {
    personal_source_permission_denied: 'The source is unavailable. Reconnect the linked Google account, check its access and retry.',
    personal_source_blocked: 'Personal Google access is blocked. Ask a Production admin before retrying.',
    personal_source_integrity_mismatch: 'The source changed during copying. Review the source before retrying.',
    personal_source_directory_unconfirmed: 'The destination directory could not be verified. Ask an admin to check storage.',
    personal_source_retry: 'Google Drive requested a retry. The worker will try again.'
  }
  return t(messages[code] || 'The copy needs attention. Check the linked account and storage before retrying.')
}

function deletionLabel(item) {
  if (item.deletion_state === 'deleted') return t('Deleted')
  if (item.deletion_state === 'deleting') return t('Deleting…')
  return ''
}

watch(taskScope, () => {
  personalInfo.value = null
  folderScopeLimited.value = false
  folderAcknowledged.value = false
  pickedFiles.value = []
  frameNumbers.value = {}
  frameStart.value = ''
  frameEnd.value = ''
  autoRangeHint.value = false
  personalKey = ''
  pickerBusy.value = false
  personalSubmitting.value = false
  pickerError.value = ''
  refresh()
}, { flush: 'sync' })
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
  <section class="panel output-packages" :aria-label="t('Output Packages')">
    <div class="panel-heading">
      <div><p class="eyebrow">{{ t('Task deliverables') }}</p><h2>{{ t('Output registration') }}</h2></div>
      <button class="button secondary" :disabled="loading || pickerBusy || personalSubmitting" @click="refresh()">{{ t('Refresh') }}</button>
    </div>
    <p v-if="error" class="upload-error" role="alert">{{ error }}</p>
    <p v-if="loading" class="muted" role="status">{{ t('Loading authorized Drive objects and package history…') }}</p>
    <p v-if="driveCheckedAt" class="muted" role="status">Drive sync: {{ driveSyncStatus }} · last checked {{ new Date(driveCheckedAt).toLocaleString() }}. This check covers the currently opened folder listing.</p>
    <p v-if="!fakeTransferAvailable" class="upload-error" role="status">Output payload transfer is disabled for external storage until a secure direct-to-Drive path is verified. Existing authorized references can still be shared below.</p>
    <template v-if="!loading">
      <div class="package-register">
        <label>{{ t('Output Type') }}
          <select v-model="outputTypeId" :aria-label="t('Output Type')">
            <option v-for="item in options.output_types" :key="item.id" :value="item.id">{{ item.name }}</option>
          </select>
        </label>
        <label>{{ t('Package kind') }}
          <select v-model="packageType" :aria-label="t('Package kind')">
            <option v-for="[value, label] in packageTypes" :key="value" :value="value">{{ t(label) }}</option>
          </select>
        </label>
        <label v-if="sequencePackage">{{ t('Frame start') }}<input v-model="frameStart" type="number" step="1" @input="autoRangeHint = false" :aria-label="t('Frame start')"></label>
        <label v-if="sequencePackage">{{ t('Frame end') }}<input v-model="frameEnd" type="number" step="1" @input="autoRangeHint = false" :aria-label="t('Frame end')"></label>
        <label>{{ t('Color space') }}<input v-model="colorSpace" maxlength="128" placeholder="e.g. ACEScg"></label>
      </div>
      <section class="personal-import" :aria-label="t('Import from my Google Drive')">
        <h3>{{ t('My Google Drive') }}</h3>
        <p v-if="!personalInfo" class="muted">{{ t('Checking your Google account link…') }}</p>
        <p v-else-if="!personalInfo.allowed" class="muted">{{ t('Personal Google accounts are blocked for this Production by an admin.') }}</p>
        <p v-else-if="!personalInfo.linked" class="muted">{{ t('Connect your Google account first (see "My Google account" below), then choose files from your own Drive. Your originals are only read, never changed.') }}</p>
        <template v-else>
          <p class="muted">{{ t('Selected files are copied to this Production; originals are never changed.') }}</p>
          <button class="button secondary" :disabled="pickerBusy || personalSubmitting" @click="chooseFromGoogleDrive">{{ pickerBusy ? t('Opening Google…') : t('Choose files from Google Drive…') }}</button>
          <p v-if="folderScopeLimited" role="status">{{ t('Only files readable by your linked grant are listed. A folder selection does not prove that all descendants are accessible.') }}</p>
          <label v-if="folderScopeLimited"><input type="checkbox" v-model="folderAcknowledged">{{ t('Import exactly the reviewed files; this may be an incomplete folder selection.') }}</label>
          <div v-if="pickedFiles.length" class="storage-object-list" :aria-label="t('Selected Google Drive files')">
            <div v-for="file in pickedFiles" :key="file.id" class="storage-object-row">
              <span class="object-name">{{ file.name }}</span>
              <span class="muted">{{ file.sizeBytes === null ? t('size checked by server') : `${file.sizeBytes} ${t('bytes')}` }}</span>
              <label v-if="sequencePackage" class="frame-number">{{ t('Frame') }}<input v-model="frameNumbers[file.id]" type="number" step="1" :aria-label="`${t('Frame number')} ${file.name}`"></label>
              <button class="text-button" :aria-label="`${t('Remove')} ${file.name}`" @click="removePicked(file)">{{ t('Remove') }}</button>
            </div>
            <p v-if="sequencePackage && pickedFiles.length && !guessedSelection" class="muted" role="status">{{ t('Frame numbers could not be safely inferred from one filename pattern. Enter them manually or select one sequence.') }}</p>
            <p v-if="sequencePackage && autoRangeHint" class="muted" role="status">{{ t('Frame range inferred from visible filenames. Check the expected shot start/end: missing first or last frames cannot be inferred.') }}</p>
            <p v-if="sequencePackage && folderScopeLimited" class="muted" role="status">{{ t('Frame QC covers selected readable files only. Hidden or inaccessible Drive items are not verified.') }}</p>
            <p v-if="sequenceCheck && !sequenceCheck.valid" class="upload-error" role="status">{{ t('Enter the frame range and a frame number inside it for every file.') }}</p>
            <p v-else-if="sequenceCheck?.duplicates" class="upload-error" role="status">{{ t('Two files use the same frame number.') }}</p>
            <p v-else-if="sequenceCheck?.missing.length" class="upload-error" role="status">{{ t('Missing frames:') }} {{ sequenceCheck.missing.slice(0, 20).join(', ') }}<template v-if="sequenceCheck.missing.length > 20"> … ({{ sequenceCheck.missing.length }} total)</template>. {{ t('You can still register; QC will flag the gap.') }}</p>
            <p v-else-if="sequenceCheck" class="muted" role="status">{{ t('No gaps in the selected files within the entered frame range.') }}</p>
          </div>
          <button class="button primary" :disabled="!personalCanRegister || personalSubmitting || pickerBusy" @click="registerPersonalFiles">{{ personalSubmitting ? t('Registering…') : t('Register {count} Google Drive file(s) as one Output version', { count: pickedFiles.length }) }}</button>
        </template>
        <p v-if="pickerError" class="upload-error" role="alert">{{ pickerError }}</p>
      </section>
      <nav class="folder-breadcrumb" aria-label="Drive folder path">
        <button class="text-button" @click="goToFolder(0)">{{ t('Allowed root') }}</button>
        <template v-for="(folder, index) in folders" :key="folder.id"><span> / </span><button class="text-button" @click="goToFolder(index + 1)">{{ folder.name }}</button></template>
      </nav>
      <div class="storage-object-list">
        <div v-for="item in objects" :key="item.id" class="storage-object-row">
          <button v-if="item.is_folder" class="text-button object-name" @click="openFolder(item)">📁 {{ item.name }}</button>
          <label v-else class="object-name"><input type="checkbox" :checked="selectedIds.includes(item.id)" :disabled="item.size === null" @change="toggleSelected(item)"> {{ item.name }}</label>
          <span class="muted">{{ item.is_folder ? t('Folder') : `${item.size ?? 'Unknown size'} bytes` }}<template v-if="item.modified_time"> · Drive modified {{ new Date(item.modified_time).toLocaleString() }}</template></span>
          <label v-if="sequencePackage && selectedIds.includes(item.id)" class="frame-number">{{ t('Frame') }}<input v-model="frameNumbers[item.id]" type="number" step="1" :aria-label="`Frame number ${item.name}`"></label>
        </div>
        <p v-if="!objects.length" class="muted">{{ t('This folder has no visible items.') }}</p>
      </div>
      <button v-if="moreObjects" class="button secondary" @click="loadObjects(currentParent, pageToken, true)">{{ t('Load next Drive page') }}</button>
      <button class="button primary" :disabled="!fakeTransferAvailable || submitting || !selectedFiles.length || !outputTypeId || (sequencePackage && (frameStart === '' || frameEnd === '' || Number(frameEnd) < Number(frameStart) || selectedFiles.some((item) => frameNumbers[item.id] === undefined || frameNumbers[item.id] === '')))" @click="registerPackage">{{ submitting ? 'Registering…' : `Register ${selectedFiles.length} selected file(s) as one immutable Output version` }}</button>
      <p class="muted">Synthetic FakeStorage tests can verify immutable Output metadata and QC. External storage payloads are not transferred through the Plugin server.</p>
      <div class="package-history">
        <article v-for="item in packages" :key="item.id" class="package-row">
          <button class="text-button" @click="togglePackage(item)"><strong>{{ item.package_type }} v{{ String(item.version).padStart(3, '0') }}</strong></button>
          <span>{{ deletionLabel(item) || item.state }} · QC {{ item.qc_state }}<template v-if="item.source_kind === 'personal'"> · {{ t('from Google Drive') }}</template></span>
          <span v-if="item.state !== 'completed' && !item.deleted" class="muted copy-progress" role="status">{{ copyStatus[item.job_state] || t('Waiting to copy') }}<template v-if="item.job_error_code"> {{ copyError(item.job_error_code) }} ({{ item.job_error_code }})</template>.</span>
          <button v-if="item.retry_available" class="button secondary" @click="retryCopy(item)">{{ t('Retry copy') }}</button>
          <span class="muted">{{ item.file_count }} files · {{ item.manifest_sha256 }}</span>
          <a v-if="safeDriveUrl(item.folder_drive_url)" class="drive-link" :href="safeDriveUrl(item.folder_drive_url)" target="_blank" rel="noopener noreferrer">Google Driveで開く (open version folder in Drive)</a>
          <span v-if="item.deleted" class="muted deleted-note">削除済み · The files were permanently deleted<template v-if="item.deleted_at"> on {{ new Date(item.deleted_at).toLocaleString() }}</template>. Name, version, sender and delivery history are kept.</span>
          <span v-if="item.deletion_state === 'deleting'" class="muted deleted-note">Downloads are blocked while the files are being deleted.<template v-if="item.deletion_stuck"> The deletion did not finish<template v-if="item.deletion_error_code"> ({{ item.deletion_error_code }})</template>; check the Drive connection and retry.</template></span>
          <button v-if="item.deletion_stuck && item.can_retry_delete" class="button secondary" @click="retryDelete(item)">Retry deletion</button>
          <button v-if="item.can_delete" class="button danger" :aria-label="`Delete ${item.package_type} v${String(item.version).padStart(3, '0')}`" @click="startDelete(item)">Delete files…</button>
          <div v-if="pendingDelete?.item.id === item.id" class="delete-confirm" role="alertdialog" aria-label="Confirm permanent deletion">
            <p><strong>Permanently delete {{ item.package_type }} v{{ String(item.version).padStart(3, '0') }}?</strong> This removes {{ pendingDelete.review.impact.file_count }} file(s) from the shared Drive. Copies in personal Drives are not touched. This cannot be undone.</p>
            <ul class="impact-list">
              <li v-if="!pendingDelete.review.impact.deliveries.length">No sent Tasks are recorded for this version.</li>
              <li v-for="delivery in pendingDelete.review.impact.deliveries" :key="delivery.id">Sent to Task {{ delivery.recipient_task_id }} (revision {{ delivery.revision }}, {{ delivery.state }}). The history stays; the files can no longer be downloaded.</li>
              <li v-if="pendingDelete.review.impact.latest_pointers.length">This version is currently marked as: {{ pendingDelete.review.impact.latest_pointers.join(', ') }}.</li>
              <li class="muted">Review or approval references outside this plugin are not tracked here and are not changed.</li>
            </ul>
            <button class="button danger" :disabled="deleting || !pendingDelete.review.can_delete" @click="confirmDelete">{{ deleting ? 'Deleting…' : 'Yes, permanently delete these files' }}</button>
            <button class="button secondary" :disabled="deleting" @click="pendingDelete = null">{{ t('Cancel') }}</button>
          </div>
          <pre v-if="item.qc_report?.missing_frames?.length" class="upload-error">{{ t('Missing frames:') }} {{ item.qc_report.missing_frames.join(', ') }}</pre>
          <div v-if="expandedPackage === item.id" class="package-files">
            <div v-for="file in packageFiles[item.id]?.files || []" :key="file.id" class="storage-object-row">
              <span>{{ file.frame_number ?? file.file_name }} · {{ file.byte_count }} bytes · {{ file.sha256 }}</span>
            <a v-if="safeDriveUrl(file.drive_url)" class="drive-link" :href="safeDriveUrl(file.drive_url)" target="_blank" rel="noopener noreferrer">Google Driveで開く</a>
            <a v-else-if="file.download_url && fakeTransferAvailable" :href="api.outputPackageDownloadUrl(file.id)">{{ t('Download FakeStorage test file') }}</a>
            </div>
            <button v-if="packageFiles[item.id]?.next_offset !== null && packageFiles[item.id]?.next_offset !== undefined" class="button secondary" @click="morePackageFiles(item)">{{ t('Load next file page') }}</button>
          </div>
        </article>
        <p v-if="!packages.length" class="muted">{{ t('No Output Packages are registered for this Task.') }}</p>
      </div>
    </template>
  </section>
</template>
