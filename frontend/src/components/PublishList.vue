<script setup>
defineProps({
  publishes: { type: Array, default: () => [] }
})
</script>

<template>
  <section class="panel" aria-labelledby="publish-heading">
    <div class="panel-heading">
      <div>
        <p class="eyebrow">Versioned outputs</p>
        <h2 id="publish-heading">Published files</h2>
      </div>
      <span class="count-pill">{{ publishes.length }} versions</span>
    </div>
    <div v-if="publishes.length" class="table-wrap">
      <table>
        <thead><tr><th>Task</th><th>Output family</th><th>Version</th><th>QC</th><th>Published</th><th>Approved</th></tr></thead>
        <tbody>
          <tr v-for="publish in publishes" :key="publish.id">
            <td class="mono">{{ publish.task_id }}</td>
            <td>{{ publish.output_family_name }}</td>
            <td class="version">v{{ String(publish.version).padStart(3, '0') }}</td>
            <td><span class="state" :data-state="publish.qc_state || publish.state">{{ publish.qc_state || publish.state }}</span></td>
            <td><span v-if="publish.latest_published" class="pointer published">Latest Published</span><span v-else class="muted">—</span></td>
            <td><span v-if="publish.latest_approved" class="pointer approved">Latest Approved</span><span v-else class="muted">—</span></td>
          </tr>
        </tbody>
      </table>
    </div>
    <div v-else class="empty-state">
      <div class="empty-icon">↗</div>
      <p>No published versions yet</p>
      <span>Completed publishes and their approval pointers will appear here.</span>
    </div>
  </section>
</template>
