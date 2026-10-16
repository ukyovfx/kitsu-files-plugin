<script setup>
import { computed, onMounted, ref } from 'vue'

const props = defineProps({ productionId: { type: String, default: '' }, api: { type: Object, required: true } })
const status = ref(null)
const connections = ref([])
const roots = ref([])
const selectedConnectionId = ref('')
const selectedRootId = ref('')
const loading = ref(true)
const busy = ref(false)
const error = ref('')
const callbackStatus = new URLSearchParams(window.location.search).get('status') || ''
const legacyOwned = computed(() => status.value?.owner === 'legacy_kitsusync' || status.value?.state === 'legacy_import_required')
const canConnect = computed(() => status.value?.owner === 'plugin' && status.value?.state === 'unconfigured' && callbackStatus !== 'connected')

async function load() {
  if (!props.productionId) {
    error.value = 'Open Drive settings from a Kitsu Production.'
    loading.value = false
    return
  }
  loading.value = true
  error.value = ''
  try {
    status.value = await props.api.getDriveAuthorityStatus(props.productionId)
    connections.value = []
    roots.value = []
    selectedConnectionId.value = ''
    selectedRootId.value = ''
    if (status.value.owner === 'plugin' && status.value.state !== 'legacy_import_required') {
      const result = await props.api.listDriveConnections(props.productionId)
      connections.value = result.connections.filter((connection) => connection.state === 'connected' && connection.account_mode === 'my_drive')
      selectedConnectionId.value = status.value.connection_id || connections.value[0]?.id || ''
      if (selectedConnectionId.value) await loadRoots()
    }
  } catch (cause) {
    error.value = cause.message || 'Drive status could not be loaded.'
  } finally {
    loading.value = false
  }
}

async function loadRoots() {
  roots.value = []
  selectedRootId.value = ''
  if (!selectedConnectionId.value) return
  try {
    const result = await props.api.listDriveRootCandidates(props.productionId, selectedConnectionId.value)
    roots.value = result.roots
    selectedRootId.value = status.value?.root_id || ''
  } catch (cause) {
    error.value = cause.message || 'App-authorized Drive folders could not be listed.'
  }
}

async function connect() {
  if (!canConnect.value || busy.value) return
  busy.value = true
  error.value = ''
  try {
    window.sessionStorage.setItem('vfx-files-plugin-drive-production-id', props.productionId)
    const result = await props.api.createDriveAuthorization(
      props.productionId,
      'my_drive',
      `native-drive:${props.productionId}:${crypto.randomUUID()}`
    )
    window.location.assign(result.authorization_url)
  } catch (cause) {
    error.value = cause.message || 'Google authorization could not be started.'
    busy.value = false
  }
}

async function verifyRoot() {
  if (!selectedConnectionId.value || !selectedRootId.value || busy.value || status.value?.activation_enabled) return
  busy.value = true
  error.value = ''
  try {
    status.value = await props.api.selectDriveRoot(
      props.productionId,
      selectedConnectionId.value,
      selectedRootId.value,
      status.value.config_revision,
      `native-root:${props.productionId}:${crypto.randomUUID()}`
    )
  } catch (cause) {
    error.value = cause.message || 'The Drive root could not be verified.'
    if (cause.status === 409) await load()
  } finally {
    busy.value = false
  }
}

onMounted(load)
</script>

<template>
  <section class="drive-settings panel" aria-labelledby="drive-settings-title">
    <header class="panel-heading">
      <div>
        <p class="eyebrow">Kitsu Production settings</p>
        <h2 id="drive-settings-title">Google Drive</h2>
      </div>
      <button class="button secondary" :disabled="loading || busy" @click="load">Refresh</button>
    </header>

    <div class="drive-settings-body">
      <p class="muted">Drive connection and verified root for this Production. This setup currently lists only My Drive folders the Plugin app can already access.</p>
      <p v-if="error" class="upload-error" role="alert">{{ error }}</p>
      <p v-else-if="loading" role="status">Loading Drive status…</p>

      <template v-else-if="status">
        <dl class="drive-status">
          <div><dt>Configuration owner</dt><dd>{{ status.owner === 'plugin' ? 'VFX Files Plugin' : 'KitsuSync (legacy)' }}</dd></div>
          <div><dt>Connection</dt><dd>{{ status.credential_state === 'active' ? 'Connected' : status.state.replaceAll('_', ' ') }}</dd></div>
          <div><dt>Verified root</dt><dd>{{ status.root_state === 'verified' ? 'Verified' : 'Not configured' }}</dd></div>
          <div><dt>Storage activation</dt><dd>{{ status.activation_enabled ? 'Active' : 'Inactive' }}</dd></div>
        </dl>

        <p v-if="callbackStatus === 'connected'" class="notice" role="status">Google authorization completed. Choose an app-authorized root below.</p>
        <p v-else-if="callbackStatus && callbackStatus !== 'connected'" class="upload-error" role="alert">
          Google authorization did not complete. Refresh the status before retrying.
        </p>
        <p v-if="legacyOwned" class="notice" role="status">
          This Production has a KitsuSync-managed configuration. Ownership will not change automatically; an approved migration is required.
        </p>
        <template v-else-if="status.state === 'unconfigured'">
          <button class="button primary" :disabled="!canConnect || busy" @click="connect">
            {{ busy ? 'Opening Google…' : 'Connect Google Drive' }}
          </button>
          <p class="muted">The Plugin stores credentials server-side and requests only OpenID and Drive file access.</p>
          <template v-if="connections.length">
            <label class="drive-field">Authorized connection
              <select v-model="selectedConnectionId" :disabled="busy" @change="loadRoots">
                <option v-for="connection in connections" :key="connection.id" :value="connection.id">My Drive · {{ connection.id.slice(0, 8) }}</option>
              </select>
            </label>
            <label class="drive-field">App-authorized Production root
              <select v-model="selectedRootId" :disabled="busy || !roots.length">
                <option value="" disabled>Select a folder</option>
                <option v-for="root in roots" :key="root.id" :value="root.id">{{ root.name }}</option>
              </select>
            </label>
            <button class="button primary" :disabled="busy || !selectedRootId" @click="verifyRoot">
              {{ busy ? 'Verifying…' : 'Verify and select root' }}
            </button>
          </template>
        </template>
        <template v-else-if="status.state === 'configured'">
          <label class="drive-field">App-authorized Production root
            <select v-model="selectedRootId" :disabled="busy || status.activation_enabled || !roots.length">
              <option value="" disabled>Select a folder</option>
              <option v-for="root in roots" :key="root.id" :value="root.id">{{ root.name }}</option>
            </select>
          </label>
          <button class="button secondary" :disabled="busy || status.activation_enabled || !selectedRootId || selectedRootId === status.root_id" @click="verifyRoot">Verify root change</button>
          <p class="muted">The root is selected and verified. Template configuration and explicit storage activation are still required before Drive synchronization can run.</p>
        </template>
        <p v-else class="muted">Drive setup is unavailable in the current state. No Drive files or permissions have been changed.</p>
      </template>

      <a class="text-button drive-back" :href="`/api/plugins/vfx_files/frontend/?production_id=${encodeURIComponent(productionId)}`">Back to Files</a>
    </div>
  </section>
</template>

<style scoped>
.drive-settings { max-width: 760px; margin: 0 auto; }
.drive-settings-body { display: grid; gap: 14px; padding: 0 18px 20px; }
.drive-status { display: grid; gap: 0; margin: 4px 0; border: 1px solid var(--border); border-radius: 9px; }
.drive-status > div { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 5px 18px; padding: 12px 14px; border-bottom: 1px solid var(--border); }
.drive-status > div:last-child { border-bottom: 0; }
.drive-status dt { color: var(--muted); font-size: 11px; }
.drive-status dd { margin: 0; font-size: 11px; font-weight: 600; text-transform: capitalize; }
.drive-back { justify-self: start; text-decoration: none; }
.drive-field { display: grid; gap: 6px; color: var(--muted); font-size: 11px; }
.drive-field select { min-width: 0; padding: 9px 10px; color: var(--text); background: var(--surface); border: 1px solid var(--border); border-radius: 7px; }
</style>
