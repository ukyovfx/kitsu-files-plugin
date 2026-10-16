<script setup>
import { computed, onMounted, ref } from 'vue'

const props = defineProps({ productionId: { type: String, default: '' }, api: { type: Object, required: true } })
const status = ref(null)
const connections = ref([])
const roots = ref([])
const configuration = ref(null)
const legacyPreview = ref(null)
const confirmLegacyImport = ref(false)
const selectedScopes = ref({ assets: false, sequences: false, shots: true, tasks: true })
const notifyNewDeliveries = ref(false)
const syncMode = ref('new_production')
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
    configuration.value = await props.api.getDriveConfiguration(props.productionId)
    legacyPreview.value = status.value.owner === 'legacy_kitsusync'
      ? await props.api.getLegacyDriveImportPreview(props.productionId)
      : null
    confirmLegacyImport.value = false
    if (configuration.value.configured) {
      const selections = configuration.value.template_selection?.selections || []
      selectedScopes.value = {
        assets: selections.some((item) => item.scope === 'asset'),
        sequences: selections.some((item) => item.scope === 'sequence'),
        shots: selections.some((item) => item.scope === 'shot'),
        tasks: selections.some((item) => item.scope === 'task'),
      }
      notifyNewDeliveries.value = configuration.value.sync_policy?.notify_new_deliveries === true
      syncMode.value = configuration.value.sync_policy?.mode || 'new_production'
    }
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
  if (!selectedConnectionId.value || !selectedRootId.value || busy.value || status.value?.activation_enabled || status.value?.activation_transition_pending) return
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

const canSaveConfiguration = computed(() =>
  status.value?.owner === 'plugin' && status.value?.state === 'configured'
  && !status.value?.activation_enabled
  && !status.value?.activation_transition_pending
  && selectedScopes.value.tasks
  && (selectedScopes.value.assets || selectedScopes.value.shots)
  && (!selectedScopes.value.sequences || selectedScopes.value.shots)
)

function configPayload() {
  const scopes = ['production']
  if (selectedScopes.value.assets) scopes.push('asset')
  if (selectedScopes.value.shots) scopes.push('shot')
  if (selectedScopes.value.sequences) scopes.push('sequence')
  if (selectedScopes.value.tasks && (selectedScopes.value.assets || selectedScopes.value.shots)) scopes.push('task')
  const selections = scopes.map((scope) => ({
    scope,
    template_id: `vfx-files.${scope}.default`,
    revision: 1,
    overrides: {},
  }))
  return {
    contract_version: '1.0',
    template_selection: { selections },
    sync_policy: {
      mode: syncMode.value,
      scope: {
        episodes: false,
        sequences: selectedScopes.value.sequences,
        shots: selectedScopes.value.shots,
        assets: selectedScopes.value.assets,
        tasks: selectedScopes.value.tasks,
      },
      notify_new_deliveries: notifyNewDeliveries.value,
    },
  }
}

async function saveConfiguration() {
  if (!canSaveConfiguration.value || busy.value) return
  busy.value = true
  error.value = ''
  try {
    configuration.value = await props.api.saveDriveConfiguration(
      props.productionId, configPayload(), status.value.config_revision,
      `native-config:${props.productionId}:${crypto.randomUUID()}`
    )
    status.value = await props.api.getDriveAuthorityStatus(props.productionId)
  } catch (cause) {
    error.value = cause.message || 'Drive settings could not be saved.'
    if (cause.status === 409) await load()
  } finally {
    busy.value = false
  }
}

async function setActivation(enabled) {
  if (busy.value || !configuration.value?.configured) return
  busy.value = true
  error.value = ''
  try {
    configuration.value = await props.api.setDriveActivation(
      props.productionId, enabled, status.value.config_revision,
      `native-activation:${props.productionId}:${crypto.randomUUID()}`
    )
    status.value = await props.api.getDriveAuthorityStatus(props.productionId)
  } catch (cause) {
    error.value = cause.message || 'Drive activation could not be changed.'
    if (cause.status === 409) await load()
  } finally {
    busy.value = false
  }
}

async function importLegacyConfiguration() {
  if (!confirmLegacyImport.value || legacyPreview.value?.status !== 'ready_for_explicit_review' || busy.value) return
  busy.value = true
  error.value = ''
  try {
    await props.api.confirmLegacyDriveImport(
      props.productionId, legacyPreview.value.source_revision,
      `legacy-import:${props.productionId}:${crypto.randomUUID()}`
    )
    await load()
  } catch (cause) {
    error.value = cause.message || 'The legacy configuration could not be imported.'
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
          <div><dt>Storage activation</dt><dd>{{ status.activation_transition_pending ? 'Waiting for KitsuSync barrier' : status.activation_enabled ? 'Active' : 'Inactive' }}</dd></div>
        </dl>

        <p v-if="callbackStatus === 'connected'" class="notice" role="status">Google authorization completed. Choose an app-authorized root below.</p>
        <p v-else-if="callbackStatus && callbackStatus !== 'connected'" class="upload-error" role="alert">
          Google authorization did not complete. Refresh the status before retrying.
        </p>
        <p v-if="legacyOwned" class="notice" role="status">
          This Production has a KitsuSync-managed configuration. Ownership will not change automatically; an approved migration is required.
        </p>
        <template v-if="legacyOwned && legacyPreview">
          <dl v-if="legacyPreview.status === 'ready_for_explicit_review'" class="drive-status">
            <div><dt>Legacy configuration revision</dt><dd>{{ legacyPreview.source_revision }}</dd></div>
            <div><dt>Current root</dt><dd>{{ legacyPreview.root_verification === 'stored_identity_match_only' ? 'Stored identity match; awaiting live root check' : legacyPreview.root_id }}</dd></div>
            <div><dt>Current activation</dt><dd>{{ legacyPreview.source_activation_enabled ? 'Active' : 'Inactive' }}</dd></div>
            <div><dt>After import</dt><dd>Plugin-owned, inactive, with fresh activation epoch</dd></div>
          </dl>
          <p v-else class="muted" role="status">Legacy import preview: {{ legacyPreview.reason || legacyPreview.status }}. The existing configuration remains under KitsuSync ownership.</p>
          <label v-if="legacyPreview.status === 'ready_for_explicit_review'" class="legacy-confirm">
            <input v-model="confirmLegacyImport" type="checkbox" :disabled="busy">
            I approve transferring this Production to Files Plugin ownership. Import keeps Drive sync inactive until it is reviewed and explicitly activated.
          </label>
          <button v-if="legacyPreview.status === 'ready_for_explicit_review'" class="button primary" :disabled="busy || !confirmLegacyImport" @click="importLegacyConfiguration">
            {{ busy ? 'Verifying and importing…' : 'Import configuration and keep inactive' }}
          </button>
        </template>
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
            <select v-model="selectedRootId" :disabled="busy || status.activation_enabled || status.activation_transition_pending || !roots.length">
              <option value="" disabled>Select a folder</option>
              <option v-for="root in roots" :key="root.id" :value="root.id">{{ root.name }}</option>
            </select>
          </label>
          <button class="button secondary" :disabled="busy || status.activation_enabled || status.activation_transition_pending || !selectedRootId || selectedRootId === status.root_id" @click="verifyRoot">Verify root change</button>
          <p class="muted">The verified root is the Production folder. Files Plugin stores credentials on the server and uses the existing Drive scope.</p>
          <fieldset class="drive-options" :disabled="busy || status.activation_enabled || status.activation_transition_pending">
            <legend>Folder templates and synchronization scope</legend>
            <label><input v-model="selectedScopes.shots" type="checkbox"> Shots</label>
            <label><input v-model="selectedScopes.assets" type="checkbox"> Assets</label>
            <label><input v-model="selectedScopes.sequences" type="checkbox" :disabled="!selectedScopes.shots"> Sequences</label>
            <label><input v-model="selectedScopes.tasks" type="checkbox" :disabled="!selectedScopes.shots && !selectedScopes.assets"> Tasks</label>
            <label><input v-model="notifyNewDeliveries" type="checkbox"> Notify KitsuSync about new deliveries</label>
            <label class="drive-field">Synchronization mode
              <select v-model="syncMode">
                <option value="new_production">New Production</option>
                <option value="future_only">Future changes only</option>
                <option value="adopt_existing">Adopt existing hierarchy</option>
              </select>
            </label>
          </fieldset>
          <div class="drive-actions">
            <button class="button primary" :disabled="busy || !canSaveConfiguration" @click="saveConfiguration">
              {{ busy ? 'Saving…' : 'Save Drive settings' }}
            </button>
            <button v-if="status.activation_enabled && !status.activation_transition_pending" class="button secondary" :disabled="busy" @click="setActivation(false)">Deactivate Drive sync</button>
            <button v-else-if="!status.activation_transition_pending" class="button primary" :disabled="busy || !configuration?.configured || !canSaveConfiguration" @click="setActivation(true)">Activate Drive sync</button>
          </div>
          <p v-if="status.activation_transition_pending" class="notice" role="status">Waiting for KitsuSync to durably fence the previous activation epoch and resolve in-flight Discord delivery. Drive sync stays paused until KitsuSync confirms the barrier. Use Refresh to check progress.</p>
          <p v-else class="muted">Activation verifies the saved Production root again. Changing settings requires deactivation and creates a fresh activation epoch.</p>
        </template>
        <p v-else class="muted">Drive setup is unavailable in the current state. No Drive files or permissions have been changed.</p>
      </template>

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
.drive-field { display: grid; gap: 6px; color: var(--muted); font-size: 11px; }
.drive-field select { min-width: 0; padding: 9px 10px; color: var(--text); background: var(--surface); border: 1px solid var(--border); border-radius: 7px; }
.drive-options { display: grid; gap: 10px; padding: 14px; border: 1px solid var(--border); border-radius: 8px; }
.drive-options label { display: flex; align-items: center; gap: 8px; color: var(--text); font-size: 12px; }
.drive-options .drive-field { display: grid; align-items: start; }
.drive-actions { display: flex; flex-wrap: wrap; gap: 10px; }
.legacy-confirm { display: flex; align-items: flex-start; gap: 9px; color: var(--text); font-size: 12px; line-height: 1.5; }
</style>
