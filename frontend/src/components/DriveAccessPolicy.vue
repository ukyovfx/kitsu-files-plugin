<script setup>
import { onMounted, ref, watch } from 'vue'

const props = defineProps({ productionId: { type: String, default: '' }, api: { type: Object, required: true } })
const policy = ref(null)
const error = ref('')
const labels = {
  verified: 'Plugin verified',
  not_verified: 'Not verified (set in Google)',
  attention: 'Check',
  not_applicable: 'Not applicable'
}

async function load() {
  if (!props.productionId) return
  error.value = ''
  try {
    policy.value = await props.api.getDriveAccessPolicy(props.productionId)
  } catch (cause) {
    error.value = cause.message || 'Drive access status could not be loaded.'
  }
}

onMounted(load)
watch(() => props.productionId, load)
</script>

<template>
  <section class="panel drive-access-policy" aria-labelledby="drive-access-heading">
    <h2 id="drive-access-heading">Drive viewing and access</h2>
    <p class="muted">Files are opened in Google Drive; Google decides who can view them. Items marked "Not verified" are configured in Google and are not read by this Plugin.</p>
    <div v-if="error" class="notice" role="alert"><span class="notice-dot"></span>{{ error }}</div>
    <ul v-if="policy" class="access-checks">
      <li v-for="check in policy.checks" :key="check.id" :data-state="check.state">
        <strong>{{ labels[check.state] || check.state }}</strong> — {{ check.detail }}
      </li>
    </ul>
  </section>
</template>
