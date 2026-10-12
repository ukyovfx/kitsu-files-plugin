<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'

const props = defineProps({ taskId: { type: String, required: true }, api: { type: Object, required: true } })
const emit = defineEmits(['changed'])
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
let pollTimer
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

async function loadObjects(parentId = currentParent.value, token = null, append = false, requestId = requestGeneration.value) {
  const result = await props.api.listTaskStorageObjects(taskScope.value, parentId, token)
  if (requestId !== requestGeneration.value || result.task_id !== taskScope.value) return
  currentParent.value = result.parent_id
  objects.value = append ? [...objects.value, ...result.items] : result.items
  pageToken.value = result.next_page_token
  moreObjects.value = Boolean(result.next_page_token)
}

async function refresh(requestId = ++requestGeneration.value) {
  if (!taskScope.value) return
  loading.value = true
  error.value = ''
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

watch(taskScope, () => refresh())
onMounted(() => {
  refresh()
  pollTimer = window.setInterval(() => {
    if (packages.value.some((item) => ['verifying', 'reconcile_required'].includes(item.state))) {
      refreshPackageList().catch(() => {})
    }
  }, 3000)
})
onUnmounted(() => {
  requestGeneration.value += 1
  window.clearInterval(pollTimer)
})
</script>

<template>
  <section class="panel output-packages" aria-label="Output Packages">
    <div class="panel-heading">
      <div><p class="eyebrow">Task deliverables</p><h2>Output Packages</h2></div>
      <button class="button secondary" :disabled="loading" @click="refresh()">Refresh</button>
    </div>
    <p v-if="error" class="upload-error" role="alert">{{ error }}</p>
    <p v-if="loading" class="muted" role="status">Loading authorized Drive objects and package history…</p>
    <template v-else>
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
          <span class="muted">{{ item.is_folder ? 'Folder' : `${item.size ?? 'Unknown size'} bytes` }}</span>
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
          <span class="muted">{{ item.file_count }} files · {{ item.manifest_sha256 }}</span>
          <pre v-if="item.qc_report?.missing_frames?.length" class="upload-error">Missing frames: {{ item.qc_report.missing_frames.join(', ') }}</pre>
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
    </template>
  </section>
</template>
