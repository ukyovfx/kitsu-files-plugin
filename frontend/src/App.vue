<script setup>
import { computed, onMounted, ref } from 'vue'
import HandoffPanel from './components/HandoffPanel.vue'
import PublishList from './components/PublishList.vue'
import UploadPanel from './components/UploadPanel.vue'

const props = defineProps({ context: { type: Object, required: true }, api: { type: Object, required: true } })
const overview = ref(null)
const loading = ref(true)
const error = ref('')
const busyId = ref('')
const productionId = computed(() => props.context.productionId)

async function load() {
  loading.value = !overview.value
  error.value = ''
  if (!productionId.value) {
    error.value = 'Open Files from a Production menu to load its authorized file records.'
    loading.value = false
    return
  }
  try {
    overview.value = await props.api.listProduction(productionId.value, props.context.episodeId)
  } catch (cause) {
    error.value = cause.message || 'Files could not be loaded.'
  } finally {
    loading.value = false
  }
}

async function respond(handoff, target, feedback = '') {
  busyId.value = handoff.id
  error.value = ''
  try {
    await props.api.respondHandoff(handoff.id, target, feedback)
    await load()
  } catch (cause) {
    error.value = cause.message || 'The handoff response could not be saved.'
  } finally {
    busyId.value = ''
  }
}

onMounted(load)
</script>

<template>
  <main class="files-page" :data-theme="context.darkTheme ? 'dark' : 'light'">
    <header class="page-header">
      <div class="brand-mark">VF</div>
      <div class="heading-copy">
        <p class="eyebrow">Production workspace</p>
        <h1>Files</h1>
        <p class="subtitle">Versioned work, published artifacts and task handoffs.</p>
      </div>
      <button class="button secondary refresh" :disabled="loading" @click="load">Refresh</button>
    </header>

    <div v-if="error" class="notice" role="alert"><span class="notice-dot"></span>{{ error }}</div>
    <div v-if="loading" class="loading-state" role="status">Loading Production files…</div>
    <template v-else-if="overview">
      <div class="scope-line"><span class="scope-dot"></span>Production <span class="mono">{{ overview.production_id }}</span><span v-if="overview.task_id" class="scope-separator">/ Task <span class="mono">{{ overview.task_id }}</span></span></div>
      <section class="summary-grid" aria-label="Publish pointers">
        <article class="summary-card published-card"><span class="card-label">Latest Published</span><strong>{{ overview.publishes.filter((item) => item.latest_published).length }}</strong><span>current output pointers</span></article>
        <article class="summary-card approved-card"><span class="card-label">Latest Approved</span><strong>{{ overview.publishes.filter((item) => item.latest_approved).length }}</strong><span>reviewed version pointers</span></article>
        <article class="summary-card handoff-card"><span class="card-label">Open Handoffs</span><strong>{{ overview.handoffs.filter((item) => item.state === 'pending').length }}</strong><span>awaiting a response</span></article>
      </section>
      <UploadPanel :enabled="overview.upload_ready" :tasks="overview.tasks" :api="api" @uploaded="load" />
      <section class="files-list" aria-labelledby="files-heading">
        <div class="section-heading"><div><p class="eyebrow">Task working files</p><h2 id="files-heading">Files</h2></div><span class="item-count">{{ overview.files?.length || 0 }} records</span></div>
        <p v-if="!overview.files?.length" class="empty-state">Uploaded files will appear here after the worker verifies the storage transfer.</p>
        <ul v-else class="file-rows">
          <li v-for="file in overview.files" :key="file.id" class="file-row" :data-state="file.state">
            <span class="file-name">{{ file.name }}</span>
            <span class="file-state">{{ file.state === 'completed' ? 'Completed' : file.state.replaceAll('_', ' ') }}</span>
            <span v-if="file.state === 'completed'" class="file-evidence">{{ file.size }} bytes · {{ file.sha256?.slice(0, 12) }}</span>
            <span v-else-if="file.failure_code" class="file-evidence">{{ file.failure_code }}</span>
          </li>
        </ul>
      </section>
      <div class="content-grid">
        <PublishList :publishes="overview.publishes" />
        <HandoffPanel :handoffs="overview.handoffs" :busy-id="busyId" @respond="respond" />
      </div>
      <footer class="page-footer"><span>VFX Files plugin</span><span>Published and approved versions are tracked separately.</span></footer>
    </template>
  </main>
</template>
