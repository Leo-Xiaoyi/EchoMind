<template>
  <div class="app-shell" :class="`view-${activeView}`">
    <aside class="navigation-rail" :aria-label="t('nav.main')">
      <a class="brand" href="#" @click.prevent="activeView = 'chat'" :aria-label="t('nav.home')">
        <span class="brand-mark">E</span>
        <span class="brand-name">EchoMind</span>
      </a>
      <button class="new-chat-button" @click="activeView = 'chat'; clearConversation()">
        <AppIcon name="plus" />
        <span>{{ t('nav.newChat') }}</span>
      </button>
      <span class="nav-caption">{{ t('nav.workspace') }}</span>
      <nav class="view-nav">
        <button
          v-for="view in views"
          :key="view.id"
          :aria-label="view.label"
          :class="{ active: activeView === view.id }"
          :aria-current="activeView === view.id ? 'page' : undefined"
          @click="activeView = view.id"
        >
          <AppIcon :name="view.icon" />
          <span>{{ view.label }}</span>
          <AppIcon class="nav-chevron" name="chevron" />
        </button>
      </nav>
      <div class="rail-bottom">
        <a class="docs-link" :aria-label="t('nav.apiDocs')" :href="docsUrl" target="_blank" rel="noreferrer">
          <AppIcon name="document" />
          <span>{{ t('nav.apiDocs') }}</span>
          <AppIcon name="northeast" />
        </a>
        <button class="user-button" :aria-label="t('nav.userConfig')" @click="openConnection">
          <span class="avatar">{{ userInitial }}</span>
          <span>
            <strong>{{ settings.userId || 'anonymous' }}</strong>
            <small>{{ t('nav.personalWorkspace') }}</small>
          </span>
          <AppIcon name="sliders" />
        </button>
      </div>
    </aside>
    <main class="workspace">
      <header class="topbar">
        <div class="page-breadcrumb">
          <span>{{ t('nav.workspace') }}</span>
          <AppIcon name="chevron" />
          <strong>{{ currentView.label }}</strong>
        </div>
        <a class="mobile-brand" href="#" @click.prevent="activeView = 'chat'" :aria-label="t('nav.home')">
          <span class="brand-mark">E</span>
          <span>EchoMind</span>
        </a>
        <div class="topbar-tools">
          <div class="language-switch" role="group" :aria-label="t('nav.language')">
            <button
              v-for="item in languages"
              :key="item.id"
              type="button"
              :class="{ active: settings.locale === item.id }"
              :aria-pressed="settings.locale === item.id"
              @click="switchLocale(item.id)"
            >{{ item.label }}</button>
          </div>
          <span class="connection-status" :title="statusText || healthStatusText">
            <i :class="healthState === 'ok' ? 'online' : 'offline'"></i>
            {{ healthStatusText }}
          </span>
          <button class="icon-button settings-button" :aria-label="t('topbar.connectionSettings')" :title="t('topbar.connectionSettings')" @click="openConnection">
            <AppIcon name="sliders" />
          </button>
        </div>
      </header>
      <div v-if="toast" class="toast" role="status">
        <AppIcon name="chat" />
        {{ toast }}
      </div>
      <section v-if="activeView === 'chat'" class="page-chat">
        <div class="chat-toolbar">
          <span class="conversation-label">
            {{ messages.length ? t('chat.current') : t('chat.new') }}
            <span v-if="messages.length" class="conversation-count">{{ messageCountText }}</span>
          </span>
          <div class="chat-toolbar-actions">
            <button class="icon-button" @click="clearConversation" :aria-label="t('chat.new')" :title="t('chat.new')">
              <AppIcon name="compose" />
            </button>
            <button class="trace-toggle" @click="inspectorDialog.showModal()">
              <AppIcon name="panel" />
              <span>{{ t('chat.runDetails') }}</span>
            </button>
          </div>
        </div>
        <div class="conversation-layout" :class="{ 'has-conversation': messages.length }">
          <section class="conversation" :class="{ 'is-empty': !messages.length }" :aria-label="t('chat.ariaConversation')">
            <div class="messages" ref="messageList" role="log" :aria-label="t('chat.ariaMessages')" aria-live="polite" :aria-busy="busy && pendingAction === 'chat'">
              <div v-if="!messages.length" class="welcome">
                <div class="welcome-mark" aria-hidden="true">E</div>
                <h1>{{ t('chat.welcomeTitle') }}</h1>
                <p>{{ t('chat.welcomeSubtitle') }}</p>
              </div>
              <template v-else>
                <article v-for="item in messages" :key="item.id" :class="['message', item.role, { 'message-error': item.error }]">
                  <span v-if="item.role === 'assistant'" class="message-avatar">E</span>
                  <div class="message-content">
                    <div class="message-meta">
                      <strong>{{ item.role === 'user' ? t('chat.you') : t('chat.brand') }}</strong>
                      <span v-if="item.error" class="error-label">{{ t('chat.requestFailed') }}</span>
                      <small v-else-if="item.role === 'assistant'">{{ currentBackend.label }} Agent</small>
                    </div>
                    <p>{{ item.content }}</p>
                    <span v-if="item.meta && !item.error" class="agent-tag">
                      <AppIcon name="route" />
                      {{ item.meta }}
                    </span>
                    <details v-if="item.trace?.toolCalls?.length || item.trace?.toolsUsed?.length" class="message-trace">
                      <summary>
                        <AppIcon name="chevron" />
                        <span>{{ t('chat.toolCalls') }}</span>
                        <span class="count-tag">{{ item.trace.toolCalls?.length || 0 }}</span>
                      </summary>
                      <div v-for="(call, index) in item.trace.toolCalls" :key="`${item.id}-${index}`" class="trace-call-item">
                        <div class="trace-call-meta">
                          <strong>{{ call.tool_name || 'unknown_tool' }}</strong>
                          <span :class="call.success ? 'success' : 'danger'">{{ call.success ? t('chat.success') : t('chat.failed') }}</span>
                        </div>
                        <pre>{{ formatJson(call.input || {}) }}</pre>
                      </div>
                      <p v-if="!item.trace.toolCalls?.length" class="muted">{{ t('chat.usedTools') }}{{ item.trace.toolsUsed.join(' · ') }}</p>
                    </details>
                    <button
                      v-if="item.role === 'assistant' && !item.error"
                      class="icon-button copy-button"
                      @click="copyMessage(item.content)"
                      :aria-label="t('chat.copy')"
                      :title="t('chat.copy')"
                    >
                      <AppIcon name="copy" />
                    </button>
                  </div>
                </article>
                <div v-if="busy && pendingAction === 'chat'" class="thinking" role="status">
                  <span class="message-avatar">E</span>
                  <span class="thinking-dots">
                    <i></i>
                    <i></i>
                    <i></i>
                  </span>
                  <span>{{ t('chat.thinking') }}</span>
                </div>
              </template>
            </div>
            <div class="composer-area">
              <form class="composer" @submit.prevent="performAction('chat', sendMessage)">
                <textarea
                  ref="composerInput"
                  v-model="draft"
                  rows="2"
                  :aria-label="t('chat.composerAria')"
                  :placeholder="t('chat.placeholder')"
                  @keydown.meta.enter.prevent="performAction('chat', sendMessage)"
                  @keydown.ctrl.enter.prevent="performAction('chat', sendMessage)"
                >
                </textarea>
                <div class="composer-bottom">
                  <span class="routing-label">
                    <AppIcon name="route" />
                    {{ t('chat.autoRoute') }}
                  </span>
                  <div class="send-controls">
                    <span class="keyboard-hint">⌘ / Ctrl + Enter</span>
                    <button
                      class="send-button"
                      type="submit"
                      :disabled="busy || !draft.trim()"
                      :aria-label="busy && pendingAction === 'chat' ? t('chat.processing') : t('chat.send')"
                      :title="busy && pendingAction === 'chat' ? t('chat.processing') : t('chat.send')"
                    >
                      <AppIcon :name="busy && pendingAction === 'chat' ? 'refresh' : 'arrow'" :class="{ spinning: busy && pendingAction === 'chat' }" />
                    </button>
                  </div>
                </div>
              </form>
              <div v-if="!messages.length" class="prompt-grid">
                <button v-for="prompt in prompts" :key="prompt.label" @click="usePrompt(prompt.text)">
                  <AppIcon :name="prompt.icon" />
                  <span>
                    <strong>{{ prompt.label }}</strong>
                    <small>{{ prompt.desc }}</small>
                  </span>
                  <AppIcon class="prompt-arrow" name="northeast" />
                </button>
              </div>
              <p class="composer-note">{{ t('chat.disclaimer') }}</p>
            </div>
          </section>
          <aside v-if="messages.length" class="inspector-desktop" :aria-label="t('chat.runDetails')">
            <div class="inspector-heading">
              <AppIcon name="panel" />
              <h2>{{ t('chat.runDetails') }}</h2>
            </div>
            <RequestInspector :last-response="lastResponse" :last-trace="lastTrace" :busy="busy && pendingAction === 'chat'" :monitor-data="monitorData" @refresh="loadMonitor" />
          </aside>
        </div>
      </section>
      <section v-else-if="activeView === 'knowledge'" class="page page-knowledge">
        <div class="page-heading">
          <div class="heading-copy">
            <span class="kicker">{{ t('knowledge.kicker') }}</span>
            <h1>{{ t('knowledge.title') }}</h1>
            <p>{{ t('knowledge.subtitle') }}</p>
          </div>
          <div class="count-display">
            <strong>{{ knowledgeCount }}</strong>
            <span>{{ t('knowledge.chunks') }}</span>
          </div>
        </div>
        <div class="knowledge-layout">
          <section class="workspace-card search-workspace">
            <div class="card-heading">
              <div>
                <span class="kicker">{{ t('knowledge.retrieval') }}</span>
                <h2>{{ t('knowledge.searchTitle') }}</h2>
              </div>
              <AppIcon name="search" />
            </div>
            <form class="search-line" @submit.prevent="performAction('search', searchKnowledge)">
              <input v-model="searchQuery" :aria-label="t('knowledge.searchAria')" :placeholder="t('knowledge.searchPlaceholder')" />
              <button :disabled="busy || !searchQuery.trim()">
                <AppIcon name="search" />
                {{ busy && pendingAction === 'search' ? t('knowledge.searching') : t('knowledge.search') }}
              </button>
            </form>
            <div v-if="searchResults.length" class="result-list">
              <article v-for="(item, index) in searchResults" :key="item.id || index" class="result-item">
                <span class="result-number">{{ String(index + 1).padStart(2, '0') }}</span>
                <div>
                  <div class="result-title">
                    <strong>{{ item.title || t('knowledge.unnamedDoc') }}</strong>
                    <small>{{ t('knowledge.relevance', { score: item.score ?? '—' }) }}</small>
                  </div>
                  <p>{{ item.content }}</p>
                </div>
              </article>
            </div>
            <div v-else class="workspace-empty">
              <AppIcon name="book" />
              <h3>{{ searched ? t('knowledge.noResultsTitle') : t('knowledge.emptyTitle') }}</h3>
              <p>{{ searched ? t('knowledge.noResultsDesc') : t('knowledge.emptyDesc') }}</p>
            </div>
          </section>
          <section class="workspace-card import-workspace">
            <div class="card-heading">
              <div>
                <span class="kicker">{{ t('knowledge.ingestion') }}</span>
                <h2>{{ t('knowledge.addTitle') }}</h2>
              </div>
              <AppIcon name="document" />
            </div>
            <form @submit.prevent="performAction('import', submitKnowledge)">
              <label>
                <span>{{ t('knowledge.docTitle') }}</span>
                <input v-model="docTitle" required :placeholder="t('knowledge.docTitlePlaceholder')" />
              </label>
              <label>
                <span>{{ t('knowledge.content') }}</span>
                <textarea v-model="docContent" required rows="7" :placeholder="t('knowledge.contentPlaceholder')"></textarea>
              </label>
              <div class="side-actions">
                <button :disabled="busy || !docTitle.trim() || !docContent.trim()">
                  <AppIcon name="arrow" />
                  {{ busy && pendingAction === 'import' ? t('knowledge.adding') : t('knowledge.addDoc') }}
                </button>
                <label class="upload-button" :class="{ disabled: busy }">
                  <AppIcon name="upload" />
                  {{ t('knowledge.upload') }}
                  <input :disabled="busy" type="file" accept=".txt,.md,.json" @change="performAction('upload', handleUpload, $event)" />
                </label>
              </div>
              <p class="form-hint">{{ t('knowledge.uploadHint') }}</p>
            </form>
          </section>
        </div>
        <section class="workspace-card skills-workspace">
          <div class="card-heading">
            <div>
              <span class="kicker">{{ t('knowledge.skillsKicker') }}</span>
              <h2>{{ t('knowledge.skillsTitle') }}</h2>
            </div>
            <button class="text-button" :disabled="busy" @click="performAction('skills', reloadSkillSet)">
              <AppIcon name="refresh" />
              {{ t('knowledge.reload') }}
            </button>
          </div>
          <div class="skill-table">
            <div v-for="skill in skillsData.skills" :key="skill.name" class="skill-item">
              <span class="skill-dot"></span>
              <strong>{{ skill.name }}</strong>
              <span>{{ skill.description || t('knowledge.skillFallback') }}</span>
              <small>{{ t('knowledge.chars', { count: skill.content_chars || 0 }) }}</small>
            </div>
            <p v-if="!skillsData.skills?.length" class="skills-empty">{{ t('knowledge.skillsEmpty') }}</p>
          </div>
          <p class="form-hint">{{ t('knowledge.rawNote') }}</p>
        </section>
      </section>
      <section v-else class="page page-evaluation">
        <div class="page-heading">
          <div class="heading-copy">
            <span class="kicker">{{ t('evaluation.kicker') }}</span>
            <h1>{{ t('evaluation.title') }}</h1>
            <p>{{ t('evaluation.subtitle') }}</p>
          </div>
          <button @click="performAction('evaluation', runEvaluation)" :disabled="busy">
            <AppIcon :name="busy && pendingAction === 'evaluation' ? 'refresh' : 'play'" :class="{ spinning: busy && pendingAction === 'evaluation' }" />
            {{ busy && pendingAction === 'evaluation' ? t('evaluation.running') : t('evaluation.run') }}
          </button>
        </div>
        <div v-if="evalData" class="evaluation-content">
          <div class="evaluation-summary">
            <div class="score-hero">
              <span>{{ t('evaluation.passRate') }}</span>
              <strong :class="{ 'no-score': evalData.valid_total === 0 }">{{ evalData.valid_total === 0 ? t('evaluation.noScore') : formatPercent(evalData.pass_rate) }}</strong>
              <small>{{ t('evaluation.validPassed', { passed: evalData.passed, total: evalData.valid_total ?? evalData.total }) }}</small>
            </div>
            <div>
              <span>{{ t('evaluation.passedCases') }}</span>
              <strong>{{ evalData.passed }}</strong>
            </div>
            <div>
              <span>{{ t('evaluation.totalFailed') }}</span>
              <strong>
                {{ evalData.total }}
                <small> / {{ evalData.evaluation_errors ?? 0 }}</small>
              </strong>
            </div>
            <div>
              <span>{{ t('evaluation.regression') }}</span>
              <strong :class="evalData.regressions?.length ? 'danger' : 'success'">{{ evalData.regressions?.length || 0 }}</strong>
            </div>
          </div>
          <div class="evaluation-layout">
            <section class="workspace-card">
              <div class="card-heading">
                <div>
                  <span class="kicker">{{ t('evaluation.qualityKicker') }}</span>
                  <h2>{{ t('evaluation.avgScores') }}</h2>
                </div>
              </div>
              <p v-if="!Object.keys(evalData.avg_scores || {}).length" class="muted">{{ t('evaluation.noScores') }}</p>
              <div class="score-list">
                <div v-for="(value, key) in evalData.avg_scores" :key="key">
                  <span>{{ key }}</span>
                  <i>
                    <b :style="{ width: `${Math.max(0, Math.min(Number(value) * 100, 100))}%` }"></b>
                  </i>
                  <strong>{{ Number(value).toFixed(2) }}</strong>
                </div>
              </div>
            </section>
            <section class="workspace-card">
              <div class="card-heading">
                <div>
                  <span class="kicker">{{ t('evaluation.recommendationsKicker') }}</span>
                  <h2>{{ t('evaluation.recommendationsTitle') }}</h2>
                </div>
              </div>
              <p v-if="evalData.regression_note" class="muted">{{ evalData.regression_note }}</p>
              <div v-if="evalData.recommendations?.length" class="recommendations">
                <p v-for="(item, index) in evalData.recommendations" :key="index">{{ item }}</p>
              </div>
              <p v-else class="muted">{{ t('evaluation.noRecommendations') }}</p>
              <div v-for="(result, index) in evalData.regressions || []" :key="index" class="evaluation-warning">
                <strong class="danger">{{ t('evaluation.regressionIssue') }}</strong>
                <p>{{ typeof result === 'string' ? result : formatJson(result) }}</p>
              </div>
              <div v-for="result in (evalData.results || []).filter(item => item.metadata?.judge_failed)" :key="result.test_id" class="evaluation-warning">
                <strong class="danger">{{ t('evaluation.judgeFailed', { id: result.test_id }) }}</strong>
                <p>{{ result.metadata.judge_error || t('evaluation.noScore') }}</p>
                <small>{{ result.metadata.judge_attempts == null ? '—' : attemptsLabel(result.metadata.judge_attempts) }}</small>
              </div>
            </section>
          </div>
          <p class="form-hint">{{ t('evaluation.rawNote') }}</p>
        </div>
        <div v-else class="evaluation-empty">
          <span class="empty-emblem">
            <AppIcon name="chart" />
          </span>
          <h2>{{ busy && pendingAction === 'evaluation' ? t('evaluation.emptyRunningTitle') : t('evaluation.emptyTitle') }}</h2>
          <p>{{ busy && pendingAction === 'evaluation' ? t('evaluation.emptyRunningDesc') : t('evaluation.emptyDesc') }}</p>
          <div class="evaluation-dimensions">
            <span>{{ t('evaluation.dimensionIntent') }}</span>
            <span>{{ t('evaluation.dimensionQuality') }}</span>
            <span>{{ t('evaluation.dimensionRegression') }}</span>
          </div>
        </div>
      </section>
    </main>
    <dialog ref="inspectorDialog" class="inspector-dialog" @click="closeOnBackdrop" aria-labelledby="inspector-title">
      <div class="drawer-heading">
        <div>
          <AppIcon name="panel" />
          <h2 id="inspector-title">{{ t('chat.runDetails') }}</h2>
        </div>
        <button class="icon-button" :aria-label="t('dialog.closeRunDetails')" @click="inspectorDialog.close()">
          <AppIcon name="close" />
        </button>
      </div>
      <RequestInspector :last-response="lastResponse" :last-trace="lastTrace" :busy="busy && pendingAction === 'chat'" :monitor-data="monitorData" @refresh="loadMonitor" />
    </dialog>
    <dialog ref="connectionDialog" class="connection-dialog" @click="closeOnBackdrop" aria-labelledby="connection-title">
      <div class="dialog-heading">
        <div>
          <span class="kicker">{{ t('connection.kicker') }}</span>
          <h2 id="connection-title">{{ t('connection.title') }}</h2>
        </div>
        <button class="icon-button" :aria-label="t('dialog.closeConnection')" @click="connectionDialog.close()">
          <AppIcon name="close" />
        </button>
      </div>
      <p class="dialog-description">{{ t('connection.description', { backend: currentBackend.label }) }}</p>
      <form @submit.prevent="saveConnection">
        <label>
          <span>{{ t('connection.endpoint') }}</span>
          <input v-model="connectionDraft.endpoint" required :placeholder="t('connection.endpointPlaceholder')" />
          <small>{{ t('connection.endpointHint') }}</small>
        </label>
        <label>
          <span>{{ t('connection.userId') }}</span>
          <input v-model="connectionDraft.userId" placeholder="u1001" />
        </label>
        <label>
          <span>{{ t('connection.conversationId') }}</span>
          <input v-model="connectionDraft.conversationId" :placeholder="t('connection.conversationPlaceholder')" />
        </label>
        <label>
          <span>{{ t('connection.token') }}</span>
          <input v-model="connectionDraft.pythonToken" type="password" autocomplete="off" :placeholder="t('connection.tokenPlaceholder')" />
        </label>
        <div class="dialog-health">
          <i :class="healthState === 'ok' ? 'online' : 'offline'"></i>
          <span>{{ healthState === 'ok' ? t('connection.serviceConnected') : healthState === 'error' ? t('topbar.disconnected') : t('topbar.notChecked') }}</span>
          <button type="button" class="text-button" @click="checkHealth">
            {{ t('connection.check') }}
            <AppIcon name="refresh" />
          </button>
        </div>
        <details v-if="statusText" class="connection-detail">
          <summary>{{ t('connection.details') }}</summary>
          <pre>{{ statusText }}</pre>
        </details>
        <div class="dialog-actions">
          <button type="button" class="quiet-button" @click="connectionDialog.close()">{{ t('connection.cancel') }}</button>
          <button type="submit">
            {{ t('connection.save') }}
            <AppIcon name="check" />
          </button>
        </div>
      </form>
    </dialog>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import {
  addKnowledge,
  backendMeta,
  createInitialSettings,
  reloadSkills,
  requestChat,
  requestHealth,
  requestKnowledgeStats,
  requestMonitor,
  requestSearch,
  requestToolTrace,
  requestSkills,
  runEvaluation as requestEvaluation,
  saveSettings,
  uploadKnowledge
} from './lib/backends'

import AppIcon from './components/AppIcon.vue'
import RequestInspector from './components/RequestInspector.vue'

import { createRequestState } from './lib/requestState'
import {
  attemptsLabel,
  messageCountLabel,
  setLocale,
  t
} from './lib/i18n'

const settings = reactive(createInitialSettings())
setLocale(settings.locale)
const requestState = createRequestState(settings)
const activeView = ref('chat')
const messages = ref([])
const draft = ref('')
const busy = ref(false)
const healthState = ref('unknown')
const statusText = ref('')
const knowledgeCount = ref('-')
const searchQuery = ref(t('knowledge.searchExample'))
const searchResults = ref([])
const docTitle = ref('')
const docContent = ref('')
const messageList = ref(null)
const composerInput = ref(null)
const connectionDialog = ref(null)
const inspectorDialog = ref(null)
const connectionDraft = reactive({ endpoint: '', userId: '', conversationId: '', pythonToken: '' })
const pendingAction = ref('')
const searched = ref(false)
const languages = [
  { id: 'en', label: 'English' },
  { id: 'zh-CN', label: '中文' }
]
const viewDefinitions = [
  { id: 'chat', labelKey: 'views.chat', icon: 'chat' },
  { id: 'knowledge', labelKey: 'views.knowledge', icon: 'book' },
  { id: 'evaluation', labelKey: 'views.evaluation', icon: 'chart' }
]
const views = computed(() => viewDefinitions.map(view => ({ ...view, label: t(view.labelKey) })))
const prompts = computed(() => [
  { label: t('prompts.refundLabel'), desc: t('prompts.refundDesc'), text: t('prompts.refundText'), icon: 'route' },
  { label: t('prompts.technicalLabel'), desc: t('prompts.technicalDesc'), text: t('prompts.technicalText'), icon: 'sliders' },
  { label: t('prompts.invoiceLabel'), desc: t('prompts.invoiceDesc'), text: t('prompts.invoiceText'), icon: 'document' }
])
const currentView = computed(() => views.value.find(view => view.id === activeView.value))
const healthStatusText = computed(() => healthState.value === 'ok'
  ? t('topbar.connected')
  : healthState.value === 'error' ? t('topbar.disconnected') : t('topbar.connecting'))
const messageCountText = computed(() => messageCountLabel(messages.value.length))
const monitorData = ref({ agent_stats: {}, tool_stats: {}, active_alerts: [], suggestions: [] })
const skillsData = ref({ count: 0, skills: [], errors: [] })
const lastResponse = ref(null)
const lastTrace = ref(null)
const evalData = ref(null)
const toast = ref('')
let toastTimer
let messageSequence = 0

const currentBackend = computed(() => backendMeta(settings.backend, settings))
const docsUrl = computed(() => `${currentBackend.value.baseUrl}/docs`)
const userInitial = computed(() => (settings.userId || 'U').slice(0, 1).toUpperCase())

watch(() => settings.conversationId, persist)
onMounted(refreshConsole)
onBeforeUnmount(() => {
  requestState.invalidate()
  clearTimeout(toastTimer)
})

function persist() { saveSettings(settings) }

function performAction(name, action, ...args) {
  if (busy.value) return
  pendingAction.value = name
  return action(...args)
}

function switchLocale(next) {
  if (next === settings.locale) return
  if (!setLocale(next)) return
  settings.locale = next
  persist()
  // 旧语言的回答、检索、评测、监控与 Skills 展示不能回写到新语言页面。
  requestState.invalidate()
  busy.value = false
  pendingAction.value = ''
  messages.value = []
  lastResponse.value = null
  lastTrace.value = null
  searchResults.value = []
  searched.value = false
  evalData.value = null
  monitorData.value = { agent_stats: {}, tool_stats: {}, active_alerts: [], suggestions: [] }
  skillsData.value = { count: 0, skills: [], errors: [] }
  statusText.value = ''
  searchQuery.value = t('knowledge.searchExample')
  // 开始新会话，避免中文历史驱动英文回复；服务端旧会话与知识数据保留。
  settings.conversationId = ''
  persist()
  showToast(t('toast.languageChanged'))
  refreshConsole()
}

function openConnection() {
  connectionDraft.pythonToken = settings.pythonToken
  connectionDraft.endpoint = currentBackend.value.baseUrl
  connectionDraft.userId = settings.userId
  connectionDraft.conversationId = settings.conversationId
  connectionDialog.value.showModal()
}

function closeOnBackdrop(event) {
  const dialog = event.currentTarget
  if (event.target !== dialog) return
  const rect = dialog.getBoundingClientRect()
  if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close()
}

function saveConnection() {
  const endpoint = connectionDraft.endpoint.trim().replace(/\/+$/, '')
  if (!endpoint || !(endpoint.startsWith('/') || /^https?:\/\//i.test(endpoint))) {
    showToast(t('connection.invalidEndpoint'))
    return
  }
  const changed = connectionDraft.pythonToken !== settings.pythonToken || endpoint !== currentBackend.value.baseUrl || connectionDraft.userId.trim() !== settings.userId || connectionDraft.conversationId.trim() !== settings.conversationId
  if (changed) {
    requestState.invalidate()
    busy.value = false
    messages.value = []
    lastResponse.value = null
    lastTrace.value = null
    searchResults.value = []
    searched.value = false
    evalData.value = null
    monitorData.value = { agent_stats: {}, tool_stats: {}, active_alerts: [], suggestions: [] }
    skillsData.value = { count: 0, skills: [], errors: [] }
    knowledgeCount.value = '—'
    healthState.value = 'unknown'
    statusText.value = ''
  }
  const identityChanged = connectionDraft.userId.trim() !== settings.userId || endpoint !== currentBackend.value.baseUrl
  settings.endpoints[settings.backend] = endpoint
  settings.pythonToken = connectionDraft.pythonToken.trim()
  settings.userId = connectionDraft.userId.trim()
  settings.conversationId = identityChanged && connectionDraft.conversationId.trim() === settings.conversationId ? '' : connectionDraft.conversationId.trim()
  persist()
  connectionDialog.value.close()
  refreshConsole()
  showToast(t('connection.saved'))
}

async function copyMessage(content) {
  try {
    await navigator.clipboard.writeText(content)
    showToast(t('toast.replyCopied'))
  } catch {
    showToast(t('toast.copyFailed'))
  }
}

async function refreshConsole() {
  await Promise.allSettled([checkHealth(), loadStats(), loadMonitor(), loadSkills()])
}

async function checkHealth() {
  const request = requestState.begin('health')

  try {
    const data = await requestHealth(request.backend, request.settings)
    if (!request.isCurrent()) return
    healthState.value = data.status === 'ok' ? 'ok' : 'error'
    statusText.value = JSON.stringify(data, null, 2)
  } catch (error) {
    if (!request.isCurrent()) return
    // 健康检查只更新状态，保留用户选择的后端。
    healthState.value = 'error'
    statusText.value = error.message
  }
}

async function loadStats() {
  const request = requestState.begin('stats')
  try {
    const data = await requestKnowledgeStats(request.backend, request.settings)
    if (!request.isCurrent()) return
    knowledgeCount.value = data.total_chunks ?? data.totalChunks ?? '-'
  } catch {
    if (request.isCurrent()) knowledgeCount.value = '-'
  }
}

async function loadMonitor() {
  const request = requestState.begin('monitor')
  try {
    const data = await requestMonitor(request.backend, request.settings)
    if (request.isCurrent()) monitorData.value = { ...data, loaded: true }
  } catch {
    if (request.isCurrent()) monitorData.value = { agent_stats: {}, tool_stats: {}, active_alerts: [], suggestions: [] }
  }
}

async function loadSkills() {
  const request = requestState.begin('skills')
  try {
    const data = await requestSkills(request.backend, request.settings)
    if (request.isCurrent()) skillsData.value = data
  } catch {
    if (request.isCurrent()) skillsData.value = { count: 0, skills: [], errors: [] }
  }
}

async function reloadSkillSet() {
  const request = requestState.begin('action')
  busy.value = true
  try {
    const data = await reloadSkills(request.backend, request.settings)
    if (!request.isCurrent()) return
    skillsData.value = data
    showToast(t('toast.skillsReloaded'))
  } catch (error) {
    if (!request.isCurrent()) return
    statusText.value = error.message
    showToast(t('toast.skillsFailed'))
  } finally { if (request.isCurrent()) busy.value = false }
}

async function sendMessage() {
  const content = draft.value.trim()
  if (!content || busy.value) return
  messages.value.push({ id: createMessageId(), role: 'user', content })
  draft.value = ''
  lastResponse.value = null
  lastTrace.value = null
  nextTick(() => messageList.value?.scrollTo({ top: messageList.value.scrollHeight, behavior: 'smooth' }))
  const request = requestState.begin('action')
  busy.value = true
  try {
    const response = await requestChat(request.backend, request.settings, content)
    if (!request.isCurrent()) return
    if (response.conversationId && !settings.conversationId) {
      settings.conversationId = response.conversationId
      persist()
    }
    const trace = await loadToolTrace(response.requestId, request)
    if (!request.isCurrent()) return
    lastResponse.value = response
    lastTrace.value = trace
    const meta = [response.intent, response.primaryAgent || response.agentType, response.knowledgeUsed ? t('chat.rag') : '', response.escalated ? t('chat.escalated') : ''].filter(Boolean).join(' · ')
    messages.value.push({ id: createMessageId(), role: 'assistant', content: response.response, meta, trace: lastTrace.value?.trace || null })
    await loadMonitor()
  } catch (error) {
    if (!request.isCurrent()) return
    messages.value.push({ id: createMessageId(), role: 'assistant', content: `${t('errors.requestFailed')}: ${error.message}`, meta: t('errors.requestFailed'), error: true })
  } finally {
    if (!request.isCurrent()) return
    busy.value = false
    await nextTick()
    messageList.value?.scrollTo({ top: messageList.value.scrollHeight, behavior: 'smooth' })
  }
}

function usePrompt(prompt) { draft.value = prompt; nextTick(() => composerInput.value?.focus()) }

function clearConversation() {
  requestState.invalidate()
  busy.value = false
  messages.value = []
  lastResponse.value = null
  lastTrace.value = null
  settings.conversationId = ''
  persist()
}

async function searchKnowledge() {
  if (!searchQuery.value.trim() || busy.value) return
  const request = requestState.begin('action')
  busy.value = true
  try {
    const data = await requestSearch(request.backend, request.settings, searchQuery.value, 5)
    if (!request.isCurrent()) return
    searchResults.value = data.results || []
    searched.value = true
    showToast(t('toast.searchDone', { count: searchResults.value.length }))
  } catch (error) {
    if (!request.isCurrent()) return
    statusText.value = error.message
    showToast(t('toast.searchFailed'))
  } finally { if (request.isCurrent()) busy.value = false }
}

async function submitKnowledge() {
  if (!docTitle.value.trim() || !docContent.value.trim() || busy.value) return
  const request = requestState.begin('action')
  busy.value = true
  try {
    const data = await addKnowledge(request.backend, request.settings, [{ title: docTitle.value.trim(), content: docContent.value.trim() }])
    if (!request.isCurrent()) return
    statusText.value = JSON.stringify(data, null, 2)
    await loadStats()
    if (!request.isCurrent()) return
    showToast(t('toast.docAdded'))
  } catch (error) {
    if (!request.isCurrent()) return
    statusText.value = error.message
    showToast(t('toast.docFailed'))
  } finally { if (request.isCurrent()) busy.value = false }
}

async function handleUpload(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file || busy.value) return
  if (file.size > 10 * 1024 * 1024) { showToast(t('toast.fileTooLarge')); return }
  const request = requestState.begin('action')
  busy.value = true
  try {
    const data = await uploadKnowledge(request.backend, request.settings, file)
    if (!request.isCurrent()) return
    statusText.value = JSON.stringify(data, null, 2)
    await loadStats()
    if (!request.isCurrent()) return
    showToast(t('toast.fileImported', { name: file.name }))
  } catch (error) {
    if (!request.isCurrent()) return
    statusText.value = error.message
    showToast(t('toast.fileFailed'))
  } finally { if (request.isCurrent()) busy.value = false }
}

async function runEvaluation() {
  const request = requestState.begin('action')
  busy.value = true
  try {
    const data = await requestEvaluation(request.backend, request.settings, null, {
      isCurrent: request.isCurrent,
      onProgress: job => {
        if (!request.isCurrent()) return
        const status = job.status === 'queued' ? t('evaluation.jobQueued') : t('evaluation.jobRunning')
        statusText.value = t('evaluation.jobProgress', { id: job.job_id, status, done: job.completed, total: job.total })
      }
    })
    if (!request.isCurrent()) return
    evalData.value = data
    showToast(data.status === 'failed' ? t('errors.evalFailed') : data.status === 'partial' ? t('errors.evalPartial') : t('errors.evalComplete'))
  } catch (error) {
    if (!request.isCurrent()) return
    statusText.value = error.message
    showToast(t('errors.evalFailed'))
  } finally { if (request.isCurrent()) busy.value = false }
}

async function loadToolTrace(requestId, request = requestState.begin('trace')) {
  try {
    return await requestToolTrace(request.backend, request.settings, requestId)
  } catch {
    return null
  }
}

function formatPercent(value) {
  const number = Number(value || 0)
  return `${(number <= 1 ? number * 100 : number).toFixed(1)}%`
}

function formatJson(value) {
  try {
    return JSON.stringify(value ?? {}, null, 2)
  } catch {
    return String(value ?? '')
  }
}

function createMessageId() {
  messageSequence += 1
  return `message-${Date.now()}-${messageSequence}`
}

function showToast(message) {
  toast.value = message
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => { toast.value = '' }, 2600)
}
</script>
