<script setup>
defineProps({
  publishes: { type: Array, default: () => [] },
  actorId: { type: String, default: '' },
  role: { type: String, default: '' },
  busyId: { type: String, default: '' }
})
const emit = defineEmits(['approve'])
</script>

<template>
  <section class="panel" aria-labelledby="publish-heading">
    <div class="panel-heading">
      <div><p class="eyebrow">Versioned outputs</p><h2 id="publish-heading">Published files</h2></div>
      <span class="count-pill">{{ publishes.length }} versions</span>
    </div>
    <div v-if="publishes.length" class="table-wrap">
      <table>
        <thead><tr><th>Task</th><th>Output family</th><th>Version</th><th>QC / Job</th><th>Artifact / Manifest</th><th>Latest Published</th><th>Latest Approved</th><th>Review</th></tr></thead>
        <tbody>
          <tr v-for="publish in publishes" :key="publish.id">
            <td class="mono">{{ publish.task_id }}</td>
            <td>{{ publish.output_family_name }}</td>
            <td class="version">v{{ String(publish.version).padStart(3, '0') }}</td>
            <td><span class="state" :data-state="publish.qc_state || publish.state">{{ publish.qc_state || publish.state }}</span><small v-if="publish.state !== 'completed'" class="file-state">{{ publish.job_state || publish.state }}<template v-if="publish.job_error"> · {{ publish.job_error }}</template></small><small v-else class="file-state">{{ publish.byte_count }} bytes · {{ publish.sha256?.slice(0, 12) }}</small></td>
            <td><a v-if="publish.provider_artifact_id" :href="`https://drive.google.com/file/d/${publish.provider_artifact_id}/view`" target="_blank" rel="noopener noreferrer">Open artifact</a><span v-else class="muted">Pending</span><small v-if="publish.native_output_file_id" class="file-state">Zou OutputFile {{ publish.native_output_file_id }}</small><details v-if="publish.manifest"><summary class="file-state">Manifest {{ publish.manifest_sha256?.slice(0, 12) }}</summary><pre class="publish-manifest">{{ JSON.stringify(publish.manifest, null, 2) }}</pre></details></td>
            <td><span v-if="publish.latest_published" class="pointer published">v{{ String(publish.version).padStart(3, '0') }}</span><span v-else class="muted">—</span></td>
            <td><span v-if="publish.latest_approved" class="pointer approved">v{{ String(publish.version).padStart(3, '0') }}</span><span v-else class="muted">—</span></td>
            <td><button v-if="['admin', 'manager', 'supervisor'].includes(role) && actorId !== publish.created_by && publish.state === 'completed' && publish.qc_state === 'passed'" class="text-button" :disabled="busyId === publish.id || publish.latest_approved" @click="emit('approve', publish)">{{ busyId === publish.id ? 'Saving…' : publish.latest_approved ? 'Approved' : 'Approve exact Publish' }}</button><span v-else class="muted">{{ publish.created_by === actorId ? 'Self-approval blocked' : '—' }}</span></td>
          </tr>
        </tbody>
      </table>
    </div>
    <div v-else class="empty-state"><div class="empty-icon">↗</div><p>No published versions yet</p><span>Completed publishes and their approval pointers will appear here.</span></div>
  </section>
</template>
