<template>
  <div class="request-inspector">
    <section class="inspector-section">
      <div class="inspector-section-heading">
        <h3>{{ t('inspector.requestTrace') }}</h3>
        <span class="state-label">
          <i :class="{ working: busy, complete: lastResponse && !busy }"></i>
          {{ busy ? t('inspector.stateWorking') : lastResponse ? t('inspector.stateDone') : t('inspector.stateIdle') }}
        </span>
      </div>
      <p class="inspector-intro">{{ busy ? t('inspector.introWorking') : lastResponse ? t('inspector.introDone') : t('inspector.introIdle') }}</p>
      <ol class="trace-steps" :class="{ complete: lastResponse && !busy }">
        <li>
          <span class="trace-step-icon">
            <AppIcon name="search" />
          </span>
          <div>
            <strong>{{ t('inspector.stepIntent') }}</strong>
            <p>{{ lastResponse?.intent || t('inspector.intentFallback') }}</p>
          </div>
        </li>
        <li>
          <span class="trace-step-icon">
            <AppIcon name="book" />
          </span>
          <div>
            <strong>{{ t('inspector.stepKnowledge') }}</strong>
            <p>{{ lastResponse ? lastResponse.knowledgeUsed ? t('inspector.usedKnowledge') : t('inspector.notUsedKnowledge') : t('inspector.findKnowledge') }}</p>
          </div>
        </li>
        <li>
          <span class="trace-step-icon">
            <AppIcon name="route" />
          </span>
          <div>
            <strong>{{ t('inspector.stepRouting') }}</strong>
            <p>{{ lastResponse ? lastResponse.primaryAgent || lastResponse.agentType || t('inspector.noAgent') : t('inspector.assignAgent') }}</p>
          </div>
        </li>
        <li>
          <span class="trace-step-icon">
            <AppIcon name="chat" />
          </span>
          <div>
            <strong>{{ t('inspector.stepReply') }}</strong>
            <p>{{ busy ? t('inspector.waitingReply') : lastResponse ? lastResponse.escalated ? t('inspector.escalated') : t('inspector.replyReady') : t('inspector.waitNew') }}</p>
          </div>
        </li>
      </ol>
      <div class="request-metrics">
        <div>
          <span>{{ t('inspector.responseTime') }}</span>
          <strong>
            {{ lastResponse ? lastResponse.latencyMs : '—' }}
            <small v-if="lastResponse"> ms</small>
          </strong>
        </div>
        <div>
          <span>{{ t('inspector.routingConfidence') }}</span>
          <strong>{{ lastResponse ? formatPercent(lastResponse.routingConfidence) : '—' }}</strong>
        </div>
      </div>
      <p v-if="lastResponse?.routingReason" class="routing-note">{{ lastResponse.routingReason }}</p>
      <details v-if="lastTrace?.trace" class="inspector-tools">
        <summary>
          <span>{{ t('inspector.toolCalls') }}</span>
          <span class="count-tag">{{ lastTrace.trace.toolCalls?.length || 0 }}</span>
          <AppIcon name="chevron" />
        </summary>
        <div v-for="(call, index) in lastTrace.trace.toolCalls" :key="index" class="trace-call-item">
          <div class="trace-call-meta">
            <strong>{{ call.tool_name }}</strong>
            <span>{{ call.latency_ms ?? '—' }} ms</span>
          </div>
          <pre>{{ formatJson(call.input || {}) }}</pre>
        </div>
        <p v-if="!lastTrace.trace.toolCalls?.length" class="muted">{{ lastTrace.trace.toolsUsed?.length ? lastTrace.trace.toolsUsed.join(' · ') : t('inspector.noToolInput') }}</p>
      </details>
    </section>
    <section class="inspector-section">
      <div class="inspector-section-heading">
        <h3>{{ t('inspector.runtimeStatus') }}</h3>
        <button class="icon-button" @click="$emit('refresh')" :aria-label="t('inspector.refreshStatus')" :title="t('inspector.refreshStatus')">
          <AppIcon name="refresh" />
        </button>
      </div>
      <div class="runtime-metrics">
        <div>
          <span>{{ t('inspector.requests') }}</span>
          <strong>{{ monitorData.loaded ? totalRequests : '—' }}</strong>
        </div>
        <div>
          <span>{{ t('inspector.agents') }}</span>
          <strong>{{ monitorData.loaded ? agentCount : '—' }}</strong>
        </div>
        <div>
          <span>{{ t('inspector.alerts') }}</span>
          <strong :class="{ danger: activeAlerts.length }">{{ monitorData.loaded ? activeAlerts.length : '—' }}</strong>
        </div>
      </div>
      <p v-if="activeAlerts.length" class="alert-note">
        <AppIcon name="alert" />
        {{ activeAlerts[0].message || activeAlerts[0].detail || activeAlerts[0].title }}
      </p>
      <p v-else class="runtime-note">{{ monitorData.loaded ? t('inspector.noActiveAlerts') : t('inspector.connectToView') }}</p>
    </section>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import AppIcon from './AppIcon.vue'
import { t } from '../lib/i18n'

const props = defineProps({
  lastResponse: { type: Object, default: null },
  lastTrace: { type: Object, default: null },
  busy: { type: Boolean, default: false },
  monitorData: { type: Object, required: true }
})
defineEmits(['refresh'])
const activeAlerts = computed(() => props.monitorData.active_alerts || [])
const agentCount = computed(() => Object.keys(props.monitorData.agent_stats || {}).length)
const totalRequests = computed(() => Object.values(props.monitorData.agent_stats || {}).reduce((sum, item) => sum + Number(item.total || 0), 0))
function formatPercent(value) {
  const number = Number(value || 0)
  return `${(number <= 1 ? number * 100 : number).toFixed(1)}%`
}
function formatJson(value) { return JSON.stringify(value ?? {}, null, 2) }
</script>
