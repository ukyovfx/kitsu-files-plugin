<script setup>
import { computed, onMounted, ref, watch } from 'vue'

const props = defineProps({ taskId: { type: String, required: true }, mode: { type: String, required: true }, api: { type: Object, required: true } })
const emit = defineEmits(['changed'])
const loading = ref(false)
const submitting = ref(false)
const error = ref('')
const notice = ref('')
const objectState = ref(null)
const candidates = ref([])
const shares = ref([])
const received = ref([])
const selectedObjectId = ref('')
const selectedRecipients = ref([])
const folderStack = ref([])
const pendingKey = ref('')
const pendingRequest = ref('')
let requestGeneration = 0

const rows = computed(() => objectState.value?.items || [])
const currentParent = computed(() => objectState.value?.parent_id || '')
const modeIsShare = computed(() => props.mode === 'share')

async function loadObjects(parentId = '', pageToken = null, generation = requestGeneration, taskId = props.taskId) {
  const result = await props.api.listTaskStorageObjects(taskId, parentId || undefined, pageToken)
  if (generation !== requestGeneration || taskId !== props.taskId || result.task_id !== taskId) return
  if (pageToken && objectState.value) {
    objectState.value = { ...result, items: [...objectState.value.items, ...result.items] }
  } else {
    objectState.value = result
  }
}

async function load() {
  if (!props.taskId) return
  const generation = ++requestGeneration
  const taskId = props.taskId
  loading.value = true
  error.value = ''
  notice.value = ''
  selectedObjectId.value = ''
  selectedRecipients.value = []
  folderStack.value = []
  objectState.value = null
  candidates.value = []
  shares.value = []
  received.value = []
  try {
    if (modeIsShare.value) {
      const [objects, recipientOptions, existingShares] = await Promise.all([
        props.api.listTaskStorageObjects(taskId),
        props.api.listTaskShareCandidates(taskId),
        props.api.listTaskLinkShares(taskId)
      ])
      if (generation !== requestGeneration || taskId !== props.taskId || objects.task_id !== taskId || recipientOptions.task_id !== taskId || existingShares.task_id !== taskId) return
      objectState.value = objects
      candidates.value = recipientOptions.recipients
      shares.value = existingShares.shares
    } else {
      const result = await props.api.listTaskReceivedLinkShares(taskId)
      if (generation !== requestGeneration || taskId !== props.taskId || result.task_id !== taskId) return
      received.value = result.received_files
    }
  } catch (cause) {
    if (generation === requestGeneration) error.value = cause.message || 'Task share references could not be loaded.'
  } finally {
    if (generation === requestGeneration) loading.value = false
  }
}

function openFolder(item) {
  folderStack.value.push({ id: item.id, name: item.name })
  selectedObjectId.value = ''
  loadObjects(item.id).catch((cause) => { if (props.taskId) error.value = cause.message || 'Drive folder could not be opened.' })
}

function goToFolder(index) {
  folderStack.value = folderStack.value.slice(0, index)
  const parent = folderStack.value.at(-1)?.id || ''
  selectedObjectId.value = ''
  loadObjects(parent).catch((cause) => { if (props.taskId) error.value = cause.message || 'Drive folder could not be opened.' })
}

async function loadMore() {
  if (!objectState.value?.next_page_token) return
  try {
    await loadObjects(currentParent.value, objectState.value.next_page_token)
  } catch (cause) {
    error.value = cause.message || 'More Drive objects could not be loaded.'
  }
}

function recipientLabel(item) {
  return `${item.entity_name} · ${item.task_type_name}${item.department_name ? ` · ${item.department_name}` : ''}`
}

function notificationText(status) {
  switch (status) {
    case 'pending': return 'Queued for KitsuSync intake; Discord delivery is not confirmed.'
    case 'inbox_accepted': return 'KitsuSync accepted this notification; Discord delivery is not confirmed.'
    case 'quarantined': return 'KitsuSync quarantined this notification.'
    case 'blocked_disclosure': return 'KitsuSync disclosure consent is unavailable or revoked; notification is held.'
    case 'delivered': return 'KitsuSync delivery status is unavailable here.'
    default: return 'Notification held until a verified KitsuSync receiver contract 1.1 is pinned.'
  }
}

async function share() {
  if (!selectedObjectId.value || !selectedRecipients.value.length) return
  submitting.value = true
  error.value = ''
  notice.value = ''
  const bodyKey = JSON.stringify({ object_id: selectedObjectId.value, recipient_task_ids: [...selectedRecipients.value].sort() })
  if (pendingRequest.value !== bodyKey) {
    pendingRequest.value = bodyKey
    pendingKey.value = `share-request:${crypto.randomUUID()}`
  }
  try {
    const taskId = props.taskId
    await props.api.createTaskLinkShare(taskId, selectedObjectId.value, selectedRecipients.value, pendingKey.value)
    pendingRequest.value = ''
    pendingKey.value = ''
    selectedObjectId.value = ''
    selectedRecipients.value = []
    emit('changed')
    notice.value = 'Reference saved. Notification status will reflect KitsuSync intake only.'
    try {
      const refreshed = await props.api.listTaskLinkShares(taskId)
      if (taskId === props.taskId && refreshed.task_id === taskId) shares.value = refreshed.shares
    } catch {
      if (taskId === props.taskId) error.value = 'Reference saved, but the shared history did not refresh.'
    }
  } catch (cause) {
    error.value = cause.message || 'The original Drive reference could not be shared.'
  } finally {
    submitting.value = false
  }
}

watch(() => [props.taskId, props.mode], () => load())
onMounted(load)
</script>

<template>
  <section v-if="modeIsShare" class="panel task-link-share" aria-labelledby="share-drive-heading">
    <div class="panel-heading"><div><p class="eyebrow">Task Files</p><h2 id="share-drive-heading">Share an original Drive reference</h2></div></div>
    <p class="share-explanation">Choose one existing file or folder and the receiving Tasks. The Plugin records a reference to the original Drive object. It does not copy, move, or change Drive permissions. Recipients need existing Google Drive access.</p>
    <p v-if="error" class="upload-error" role="alert">{{ error }}</p>
    <p v-if="notice" class="upload-result" role="status">{{ notice }}</p>
    <p v-if="loading" class="loading-state" role="status">Loading authorized Drive objects and recipient Tasks…</p>
    <template v-else>
      <div class="task-share-browser">
        <div class="folder-breadcrumb" aria-label="Drive folder path">
          <button class="text-button" type="button" @click="goToFolder(0)">Production root</button>
          <template v-for="(folder, index) in folderStack" :key="folder.id">
            <span aria-hidden="true">/</span><button class="text-button" type="button" @click="goToFolder(index + 1)">{{ folder.name }}</button>
          </template>
        </div>
        <ul v-if="rows.length" class="storage-object-list">
          <li v-for="item in rows" :key="item.id" class="storage-object-row">
            <input :id="`share-object-${item.id}`" v-model="selectedObjectId" type="radio" name="share-object" :value="item.id" :aria-label="`Select ${item.name} as original reference`">
            <label class="object-name" :for="`share-object-${item.id}`"><span aria-hidden="true">{{ item.is_folder ? '▰' : '▧' }}</span>{{ item.name }}</label>
            <small class="muted">{{ item.is_folder ? 'Folder' : item.mime_type || 'File' }}</small>
            <button v-if="item.is_folder" class="text-button" type="button" @click="openFolder(item)">Browse</button>
          </li>
        </ul>
        <div v-else class="empty-state compact"><p>No authorized Drive objects in this folder.</p></div>
        <button v-if="objectState?.next_page_token" class="text-button" type="button" @click="loadMore">Load more</button>
      </div>
      <fieldset class="share-recipients">
        <legend>Receiving Tasks</legend>
        <p v-if="!candidates.length" class="muted">No other Tasks are currently available to this actor.</p>
        <p v-else class="muted">Select up to 100 receiving Tasks for this original reference.</p>
        <label v-for="candidate in candidates" :key="candidate.task_id" class="share-recipient">
          <input v-model="selectedRecipients" type="checkbox" :value="candidate.task_id" :disabled="selectedRecipients.length >= 100 && !selectedRecipients.includes(candidate.task_id)">
          <span>{{ recipientLabel(candidate) }}</span>
        </label>
      </fieldset>
      <div class="share-actions"><span class="muted">{{ selectedObjectId ? '1 original selected' : 'Choose a file or folder' }} · {{ selectedRecipients.length }} recipient{{ selectedRecipients.length === 1 ? '' : 's' }}</span><button class="button primary" type="button" :disabled="submitting || !selectedObjectId || !selectedRecipients.length" @click="share">{{ submitting ? 'Saving…' : 'Share reference' }}</button></div>
      <section class="share-history" aria-label="References shared from this Task">
        <h3>Shared from this Task</h3>
        <ul v-if="shares.length" class="revision-list">
          <li v-for="item in shares" :key="item.share_target_id">
            <a :href="item.object.url" target="_blank" rel="noopener noreferrer">{{ item.metadata?.name || item.object.object_id }}</a>
            <span class="muted">to {{ item.recipient?.task_type_name || `Task ${item.recipient_task_id}` }}</span>
            <small class="share-notification">{{ notificationText(item.notification_status) }}</small>
          </li>
        </ul>
        <p v-else class="muted">No references shared from this Task yet.</p>
      </section>
    </template>
  </section>

  <section v-else class="panel task-received-files" aria-labelledby="received-files-heading">
    <div class="panel-heading"><div><p class="eyebrow">Task Files</p><h2 id="received-files-heading">Received Files</h2></div><span class="count-pill">{{ received.length }} references</span></div>
    <p class="share-explanation">These are references to original Google Drive files and folders. Opening a link uses your existing Drive permissions; receiving a reference does not grant access or change Drive permissions.</p>
    <p v-if="error" class="upload-error" role="alert">{{ error }}</p>
    <p v-if="loading" class="loading-state" role="status">Loading received references…</p>
    <ul v-else-if="received.length" class="revision-list received-share-list">
      <li v-for="item in received" :key="item.share_target_id">
        <a :href="item.object.url" target="_blank" rel="noopener noreferrer">{{ item.metadata?.name || item.object.object_id }}</a>
        <span>{{ item.object.object_type }}</span>
        <small>From {{ item.source.entity_name || item.source.entity_type }} · {{ item.source.task_type_name || `Task ${item.source.task_id}` }}</small>
        <small class="share-notification">{{ notificationText(item.notification_status) }}</small>
      </li>
    </ul>
    <div v-else class="empty-state compact"><div class="empty-icon">↗</div><p>No references received</p><span>References shared to this Task will appear here.</span></div>
  </section>
</template>
