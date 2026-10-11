<script setup>
import { computed, ref } from 'vue'

const props = defineProps({
  enabled: { type: Boolean, default: false },
  tasks: { type: Array, default: () => [] },
  api: { type: Object, required: true }
})
const taskId = ref('')
const busy = ref(false)
const progress = ref(0)
const message = ref('')
const problem = ref('')
const percent = computed(() => progress.value ? Math.round(progress.value * 100) : 0)

async function chooseFile(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file || !taskId.value || busy.value) return
  busy.value = true
  message.value = ''
  problem.value = ''
  progress.value = 0
  try {
    const result = await props.api.stageFile(taskId.value, file, ({ completed, total }) => {
      progress.value = total ? completed / total : 0
    })
    progress.value = 1
    message.value = `Source staged. Job ${result.job_id} is queued; publishing remains a separate step.`
  } catch (error) {
    problem.value = error.message || 'The source file could not be staged.'
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <section class="upload-panel" aria-labelledby="upload-heading">
    <div class="upload-symbol">＋</div>
    <div class="upload-copy">
      <p class="eyebrow">Source ingestion</p>
      <h2 id="upload-heading">Upload to a Task</h2>
      <p>Files are checksum verified in durable staging before a worker job is queued.</p>
    </div>
    <select v-model="taskId" class="task-select" aria-label="Choose Task" :disabled="!enabled || busy">
      <option value="">Choose Task</option>
      <option v-for="task in tasks" :key="task.id" :value="task.id">{{ task.task_type_id.slice(0, 8) }} · {{ task.entity_id.slice(0, 8) }}</option>
    </select>
    <label class="button primary file-button" :class="{ disabled: !enabled || !taskId || busy }">
      {{ busy ? 'Uploading…' : 'Choose file' }}
      <input type="file" :disabled="!enabled || !taskId || busy" @change="chooseFile">
    </label>
    <div v-if="busy || progress" class="upload-progress" role="status">
      <div class="progress-track"><span :style="{ width: `${percent}%` }"></span></div>
      <span>{{ percent }}%</span>
    </div>
    <p v-if="message" class="upload-result">{{ message }}</p>
    <p v-if="problem" class="upload-error" role="alert">{{ problem }}</p>
    <p v-if="!enabled" class="upload-disabled">Storage, durable staging, and the worker must be configured before uploads are enabled.</p>
  </section>
</template>
