<script setup>
import { onMounted, ref, watch } from 'vue'

const props = defineProps({ productionId: { type: String, default: '' }, api: { type: Object, required: true } })
const policy = ref('registrant_and_admin')
const canChange = ref(false)
const busy = ref(false)
const error = ref('')
const saved = ref(false)

async function load() {
  if (!props.productionId) return
  error.value = ''
  try {
    const result = await props.api.getOutputDeletePolicy(props.productionId)
    policy.value = result.policy
    canChange.value = Boolean(result.can_change)
  } catch (cause) {
    error.value = cause.message || 'Output deletion settings could not be loaded.'
  }
}

async function save() {
  busy.value = true
  saved.value = false
  error.value = ''
  try {
    const result = await props.api.setOutputDeletePolicy(props.productionId, policy.value)
    policy.value = result.policy
    saved.value = true
  } catch (cause) {
    error.value = cause.message || 'Output deletion settings could not be saved.'
  } finally {
    busy.value = false
  }
}

onMounted(load)
watch(() => props.productionId, load)
</script>

<template>
  <section class="panel output-deletion-settings" aria-labelledby="output-deletion-heading">
    <h2 id="output-deletion-heading">Output file deletion</h2>
    <p class="muted">Choose who may permanently delete the files of an Output version. Enforced by the server.</p>
    <div v-if="error" class="notice" role="alert"><span class="notice-dot"></span>{{ error }}</div>
    <fieldset :disabled="!canChange || busy">
      <label><input v-model="policy" type="radio" value="registrant_and_admin"> 登録者＋Production管理者 (Registrant + Production admins, default)</label>
      <label><input v-model="policy" type="radio" value="admin_only"> Production管理者のみ (Production admins only)</label>
    </fieldset>
    <button v-if="canChange" class="button primary" :disabled="busy" @click="save">{{ busy ? 'Saving…' : 'Save' }}</button>
    <p v-else class="muted">Only a Production admin can change this setting.</p>
    <p v-if="saved" class="muted" role="status">Saved.</p>
  </section>
</template>
