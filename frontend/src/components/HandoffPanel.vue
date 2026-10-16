<script setup>
import { reactive } from 'vue'
defineProps({ handoffs: { type: Array, default: () => [] }, busyId: { type: String, default: '' } })
const emit = defineEmits(['respond'])
const feedback = reactive({})
const publishDownloadUrl = (handoffId) => `/api/plugins/vfx_files/handoffs/${encodeURIComponent(handoffId)}/download`
</script>

<template>
  <section class="panel" aria-labelledby="handoff-heading">
    <div class="panel-heading">
      <div>
        <p class="eyebrow">Department Inbox</p>
        <h2 id="handoff-heading">Publish handoffs</h2>
      </div>
      <span class="count-pill">{{ handoffs.filter((item) => item.state === 'pending').length }} pending</span>
    </div>
    <div v-if="handoffs.length" class="handoff-list">
      <article v-for="handoff in handoffs" :key="handoff.id" class="handoff-row">
        <div class="handoff-main">
          <span class="state" :data-state="handoff.state">{{ handoff.state }}</span>
          <span class="handoff-route">{{ handoff.source_task_type_name || 'Source Task' }} · {{ handoff.source_task_name || handoff.task_id }} → {{ handoff.recipient_department_name || 'Person recipient' }}<template v-if="handoff.recipient_task_type_name"> / {{ handoff.recipient_task_type_name }} · {{ handoff.recipient_task_name }}</template></span>
          <span class="muted">Exact Publish v{{ String(handoff.publish_version || 0).padStart(3, '0') }} · {{ handoff.publish_id }}</span>
          <span v-if="handoff.publish_source_file_name" class="muted">File: {{ handoff.publish_source_file_name }}</span>
          <a v-if="handoff.can_download" class="handoff-download" :href="publishDownloadUrl(handoff.id)">Download verified Publish</a>
          <span v-if="handoff.recipient_person_id" class="muted">Accountable recipient: {{ handoff.recipient_person_name || handoff.recipient_person_id }}</span>
          <span v-else-if="handoff.recipient_department_id" class="muted">Any currently eligible Department Task assignee may respond; first committed response owns the decision.</span>
          <p v-if="handoff.sender_note" class="handoff-note">{{ handoff.sender_note }}</p>
        </div>
        <ol v-if="handoff.events?.length" class="handoff-history" aria-label="Handoff event history">
          <li v-for="event in handoff.events" :key="event.id">
            <time v-if="event.created_at" :datetime="event.created_at">{{ new Date(event.created_at).toLocaleString() }}</time>
            <strong>{{ event.actor_name || event.actor_id }}</strong>
            <span>{{ event.type }}</span>
            <span v-if="event.feedback">— {{ event.feedback }}</span>
          </li>
        </ol>
        <div v-if="handoff.state === 'pending' && handoff.can_respond" class="handoff-actions">
          <textarea v-model="feedback[handoff.id]" class="feedback-input" aria-label="Needs Fix feedback" placeholder="Feedback required for Needs Fix" rows="1"></textarea>
          <button class="button secondary" :disabled="busyId === handoff.id || !feedback[handoff.id]?.trim()" @click="emit('respond', handoff, 'needs_fix', feedback[handoff.id])">Needs Fix</button>
          <button class="button primary" :disabled="busyId === handoff.id" @click="emit('respond', handoff, 'accepted', '')">Accept</button>
        </div>
        <p v-else-if="handoff.state === 'pending'" class="muted">Response requires current Production membership, Department membership, Task assignment and Handoff permission.</p>
      </article>
    </div>
    <div v-else class="empty-state compact">
      <div class="empty-icon">↔</div>
      <p>No handoffs waiting</p>
      <span>Accept and Needs Fix decisions are recorded against the exact immutable Publish version.</span>
    </div>
  </section>
</template>
