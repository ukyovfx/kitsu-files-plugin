<script setup>
import { reactive } from 'vue'
defineProps({ handoffs: { type: Array, default: () => [] }, busyId: { type: String, default: '' } })
const emit = defineEmits(['respond'])
const feedback = reactive({})
</script>

<template>
  <section class="panel" aria-labelledby="handoff-heading">
    <div class="panel-heading">
      <div>
        <p class="eyebrow">Task Handoff</p>
        <h2 id="handoff-heading">Awaiting your response</h2>
      </div>
      <span class="count-pill">{{ handoffs.filter((item) => item.state === 'pending').length }} pending</span>
    </div>
    <div v-if="handoffs.length" class="handoff-list">
      <article v-for="handoff in handoffs" :key="handoff.id" class="handoff-row">
        <div class="handoff-main">
          <span class="state" :data-state="handoff.state">{{ handoff.state }}</span>
          <span class="mono">Task {{ handoff.task_id }}</span>
          <span class="muted">Publish {{ handoff.publish_id }}</span>
        </div>
        <div v-if="handoff.state === 'pending'" class="handoff-actions">
          <textarea v-model="feedback[handoff.id]" class="feedback-input" aria-label="Needs Fix feedback" placeholder="Feedback required for Needs Fix" rows="1"></textarea>
          <button class="button secondary" :disabled="busyId === handoff.id || !feedback[handoff.id]?.trim()" @click="emit('respond', handoff, 'needs_fix', feedback[handoff.id])">Needs Fix</button>
          <button class="button primary" :disabled="busyId === handoff.id" @click="emit('respond', handoff, 'accepted', '')">Accept</button>
        </div>
      </article>
    </div>
    <div v-else class="empty-state compact">
      <div class="empty-icon">↔</div>
      <p>No handoffs waiting</p>
      <span>Accepted / Needs Fix decisions are recorded against an exact Publish version.</span>
    </div>
  </section>
</template>
