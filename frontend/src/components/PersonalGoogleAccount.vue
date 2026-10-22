<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { safeGoogleAuthUrl } from '../googleAuthUrl.js'

const props = defineProps({ productionId: { type: String, default: '' }, api: { type: Object, required: true } })
const info = ref(null)
const busy = ref(false)
const error = ref('')
const notice = ref('')
const policyChoice = ref('allowed')
const returned = new URLSearchParams(window.location.search)
const errorText = {
  personal_google_blocked: 'Personal Google accounts are blocked for this Production.',
  authorization_denied: 'Google sign-in was cancelled.',
  authorization_expired: 'The sign-in took too long. Try again.',
  authorization_replayed: 'That sign-in link was already used. Start again.',
  authorization_failed: 'Google sign-in could not be completed.',
  identity_unverified: 'Your Google identity could not be verified.',
  account_switch_requires_disconnect: 'Disconnect the current Google account before linking another.',
  google_account_already_linked: 'That Google account is already linked to another Kitsu user.',
  reauthorization_required: 'Google did not return a long-lived grant. Connect again.'
}
const stateText = computed(() => ({
  not_linked: 'Not connected', active: 'Connected', reauth_required: 'Reconnect required (Google no longer accepts the grant)',
  revoking: 'Disconnecting…', revoke_pending: 'Disconnect pending (Google has not confirmed yet)'
}[info.value?.state] || info.value?.state || ''))
const canConnect = computed(() => info.value?.allowed && ['not_linked', 'reauth_required'].includes(info.value?.state))

function describe(cause, fallback) {
  const code = cause?.message || ''
  return errorText[code] || fallback
}

async function load() {
  if (!props.productionId) return
  error.value = ''
  try {
    info.value = await props.api.getPersonalGoogle(props.productionId)
    policyChoice.value = info.value.policy
  } catch (cause) {
    error.value = describe(cause, 'Personal Google status could not be loaded.')
  }
}

async function connect() {
  busy.value = true
  error.value = ''
  try {
    const started = await props.api.startPersonalGoogleAuthorization(props.productionId, `personal-google:${crypto.randomUUID()}`)
    const target = safeGoogleAuthUrl(started.authorization_url)
    if (!target) throw new Error('authorization_failed')
    window.location.assign(target)
  } catch (cause) {
    error.value = describe(cause, 'Google sign-in could not be started.')
    busy.value = false
  }
}

async function check() {
  busy.value = true
  error.value = ''
  try {
    await props.api.checkPersonalGoogle(props.productionId)
    await load()
    notice.value = 'Connection checked.'
  } catch (cause) {
    error.value = describe(cause, 'The connection could not be checked.')
  } finally {
    busy.value = false
  }
}

async function disconnect() {
  busy.value = true
  error.value = ''
  try {
    await props.api.disconnectPersonalGoogle()
    await load()
    notice.value = 'Disconnect requested.'
  } catch (cause) {
    error.value = describe(cause, 'The account could not be disconnected.')
  } finally {
    busy.value = false
  }
}

async function savePolicy() {
  busy.value = true
  error.value = ''
  try {
    await props.api.setPersonalGooglePolicy(props.productionId, policyChoice.value)
    await load()
    notice.value = 'Saved. Existing links are kept.'
  } catch (cause) {
    error.value = describe(cause, 'The setting could not be saved.')
  } finally {
    busy.value = false
  }
}

onMounted(() => {
  if (returned.get('personal_google') === 'connected') notice.value = 'Google account connected.'
  if (returned.get('personal_google') === 'failed') error.value = describe({ message: returned.get('error') || '' }, 'Google sign-in failed.')
  load()
})
watch(() => props.productionId, load)
</script>

<template>
  <section class="panel personal-google" aria-labelledby="personal-google-heading">
    <h2 id="personal-google-heading">My Google account</h2>
    <p class="muted">Link your own Google account so you can choose your own files later. Plugin servers keep an encrypted grant; nothing is shown to the browser. Your files are never changed or deleted by this link.</p>
    <div v-if="error" class="notice" role="alert"><span class="notice-dot"></span>{{ error }}</div>
    <p v-if="notice" class="muted" role="status">{{ notice }}</p>
    <template v-if="info">
      <p><strong>{{ stateText }}</strong><span v-if="info.account_hint" class="muted"> · Google account {{ info.account_hint }}</span></p>
      <p v-if="!info.allowed" class="muted">Your Production admin has turned off personal Google accounts here. You can still disconnect an existing link.</p>
      <div class="button-row">
        <button v-if="canConnect" class="button primary" :disabled="busy" @click="connect">{{ info.state === 'reauth_required' ? 'Reconnect Google account' : 'Connect Google account' }}</button>
        <button v-if="info.state === 'active' || info.state === 'reauth_required'" class="button secondary" :disabled="busy" @click="check">Check connection</button>
        <button v-if="info.state !== 'not_linked'" class="button secondary" :disabled="busy || info.state === 'revoking'" @click="disconnect">Disconnect</button>
      </div>
      <fieldset v-if="info.can_change_policy" class="personal-google-policy" :disabled="busy">
        <legend>Production admin: personal Google accounts</legend>
        <label><input v-model="policyChoice" type="radio" value="allowed"> Allow members to link their own Google account</label>
        <label><input v-model="policyChoice" type="radio" value="blocked"> Block (existing links are kept, but not used here)</label>
        <button class="button secondary" @click="savePolicy">Save</button>
      </fieldset>
    </template>
  </section>
</template>
