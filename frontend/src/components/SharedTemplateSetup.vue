<script setup>
import { computed, onMounted, ref, watch } from 'vue'

const props = defineProps({ productionId: { type: String, required: true }, tasks: { type: Array, default: () => [] }, api: { type: Object, required: true } })
const hidden = ref(false)
const expanded = ref(false)
const loading = ref(false)
const busy = ref(false)
const error = ref('')
const rootUrl = ref('')
const candidates = ref([])
const selectedTemplateId = ref('')
const mappings = ref({})
const configuration = ref(null)
const taskId = ref('')
const folderResult = ref(null)
const statusText = ref('')
const selectedTaskStorageKey = computed(() => `vfx-files:shared-template-task:${props.productionId}`)

function flatten(node, depth = 0, rows = []) {
  rows.push({ id: node.folder_id, name: node.name, depth })
  for (const child of node.children || []) flatten(child, depth + 1, rows)
  return rows
}

const selectedTemplate = computed(() => candidates.value.find((item) => item.folder_id === selectedTemplateId.value) || null)
const templateFolders = computed(() => selectedTemplate.value ? flatten(selectedTemplate.value) : [])
const roleOptions = [
  { id: 'sequence', label: 'Sequence (optional)' }, { id: 'shot', label: 'Shot' }, { id: 'task', label: 'Task' }
]
const configured = computed(() => Boolean(configuration.value?.configured))

async function loadConfiguration() {
  loading.value = true
  error.value = ''
  try {
    const current = await props.api.getSharedTemplate(props.productionId)
    configuration.value = current
    if (current.configured) {
      rootUrl.value = `https://drive.google.com/drive/folders/${current.allowed_root_id}`
      selectedTemplateId.value = current.template_folder_id
      mappings.value = { ...current.role_mappings }
      candidates.value = [current.template_tree]
      const savedTaskId = window.sessionStorage.getItem(selectedTaskStorageKey.value)
      if (savedTaskId && props.tasks.some((task) => task.id === savedTaskId)) {
        expanded.value = true
        taskId.value = savedTaskId
        folderResult.value = await props.api.getTaskSharedFolders(props.productionId, savedTaskId)
      }
    }
  } catch (cause) {
    if (cause.status === 403) hidden.value = true
    else error.value = cause.message || 'Shared folder settings could not be loaded.'
  } finally {
    loading.value = false
  }
}

watch(taskId, (value) => {
  if (value) window.sessionStorage.setItem(selectedTaskStorageKey.value, value)
  else window.sessionStorage.removeItem(selectedTaskStorageKey.value)
})

async function discover() {
  busy.value = true
  error.value = ''
  statusText.value = ''
  try {
    const result = await props.api.discoverSharedTemplates(props.productionId, rootUrl.value.trim())
    candidates.value = result.templates
    selectedTemplateId.value = ''
    mappings.value = {}
    statusText.value = `${result.templates.length} folder templates found under the authorized root.`
  } catch (cause) {
    error.value = cause.message || 'Template discovery failed.'
  } finally {
    busy.value = false
  }
}

async function save() {
  if (!selectedTemplate.value) return
  busy.value = true
  error.value = ''
  try {
    configuration.value = await props.api.saveSharedTemplate(props.productionId, {
      root_url: rootUrl.value.trim(),
      template_folder_id: selectedTemplateId.value,
      role_mappings: Object.fromEntries(Object.entries(mappings.value).filter(([, folderId]) => folderId)),
      expected_version: configuration.value?.version ?? null
    })
    await loadConfiguration()
    statusText.value = 'Configuration saved disabled. Review the hierarchy before activation.'
  } catch (cause) {
    error.value = cause.message || 'Template configuration could not be saved.'
  } finally {
    busy.value = false
  }
}

async function setEnabled(enabled) {
  if (!configuration.value?.version) return
  busy.value = true
  error.value = ''
  try {
    await props.api.setSharedTemplateEnabled(props.productionId, configuration.value.version, enabled)
    await loadConfiguration()
    statusText.value = enabled ? 'Shared folder provisioning is active.' : 'Shared folder provisioning is disabled.'
  } catch (cause) {
    error.value = cause.message || 'Activation state could not be updated.'
  } finally {
    busy.value = false
  }
}

async function provision() {
  busy.value = true
  error.value = ''
  folderResult.value = null
  try {
    const queued = await props.api.provisionTaskFolders(props.productionId, taskId.value)
    statusText.value = `Folder job ${queued.state}: ${queued.job_id}`
    folderResult.value = await props.api.getTaskSharedFolders(props.productionId, taskId.value)
  } catch (cause) {
    error.value = cause.message || 'Task folders could not be queued or loaded.'
  } finally {
    busy.value = false
  }
}

async function refreshFolders() {
  if (!taskId.value) return
  busy.value = true
  error.value = ''
  try {
    folderResult.value = await props.api.getTaskSharedFolders(props.productionId, taskId.value)
  } catch (cause) {
    error.value = cause.message || 'Task folder bindings could not be refreshed.'
  } finally {
    busy.value = false
  }
}

onMounted(loadConfiguration)
</script>

<template>
  <section v-if="!hidden" class="panel template-panel" aria-label="Shared folder templates">
    <button class="template-toggle" type="button" :aria-expanded="expanded" @click="expanded = !expanded">
      <span><span class="eyebrow">Shared Drive</span><strong>Folder template setup</strong></span>
      <span class="count-pill">{{ configuration?.enabled ? 'Active' : configured ? 'Disabled' : 'Not configured' }}</span>
    </button>
    <div v-if="expanded" class="template-body">
      <p class="muted template-help">Import the root URL from the Production Storage setting. Access is checked against this Production's configured StorageConnection. Only folders are discovered and copied.</p>
      <label class="template-label" for="drive-root-url">Google Drive root URL</label>
      <div class="template-row"><input id="drive-root-url" v-model="rootUrl" class="template-input" type="url" placeholder="https://drive.google.com/drive/folders/…"><button class="button secondary" :disabled="busy || loading || !rootUrl" @click="discover">Discover</button></div>
      <div v-if="candidates.length" class="template-grid">
        <label class="template-label" for="template-choice">Folder template</label>
        <select id="template-choice" v-model="selectedTemplateId" class="task-select">
          <option value="">Select a discovered template</option>
          <option v-for="item in candidates" :key="item.folder_id" :value="item.folder_id">{{ item.name }}</option>
        </select>
        <template v-if="selectedTemplate">
          <div class="template-tree" aria-label="Selected folder hierarchy"><div v-for="folder in templateFolders" :key="folder.id" class="template-tree-row" :style="{ paddingLeft: `${12 + folder.depth * 16}px` }">{{ folder.name }}</div></div>
          <template v-for="role in roleOptions" :key="role.id">
            <label class="template-label" :for="`template-role-${role.id}`">{{ role.label }} mapping</label>
            <select :id="`template-role-${role.id}`" v-model="mappings[role.id]" class="task-select">
              <option value="">{{ role.id === 'sequence' ? 'No Sequence folder' : 'Select folder' }}</option>
              <option v-for="folder in templateFolders" :key="folder.id" :value="folder.id">{{ '　'.repeat(folder.depth) }}{{ folder.name }} · {{ folder.id }}</option>
            </select>
          </template>
          <button class="button primary" :disabled="busy || !mappings.shot || !mappings.task" @click="save">Save disabled configuration</button>
        </template>
      </div>
      <div v-if="configured" class="template-actions">
        <p>Configuration v{{ configuration.version }} · {{ configuration.enabled ? 'active' : 'disabled' }}</p>
        <button v-if="!configuration.enabled" class="button primary" :disabled="busy" @click="setEnabled(true)">Activate provisioning</button>
        <button v-else class="button secondary" :disabled="busy" @click="setEnabled(false)">Disable provisioning</button>
        <label class="template-label" for="provision-task">Provision Task folders</label>
        <div class="template-row"><select id="provision-task" v-model="taskId" class="task-select"><option value="">Select Kitsu Task</option><option v-for="task in tasks" :key="task.id" :value="task.id">{{ task.id }}</option></select><button class="button secondary" :disabled="busy || !configuration.enabled || !taskId" @click="provision">Create or reuse</button><button class="text-button" :disabled="busy || !taskId" @click="refreshFolders">Refresh bindings</button></div>
        <ul v-if="folderResult?.folders?.length" class="template-bindings"><li v-for="folder in folderResult.folders" :key="folder.scope_key"><span>{{ folder.name }}</span><span class="state" :data-state="folder.state">{{ folder.state }}</span><a v-if="folder.url" :href="folder.url" target="_blank" rel="noreferrer">Open folder</a><span v-else class="muted">Waiting for worker</span></li></ul>
      </div>
      <p v-if="statusText" class="upload-result" role="status">{{ statusText }}</p>
      <p v-if="error" class="upload-error" role="alert">{{ error }}</p>
    </div>
  </section>
</template>
