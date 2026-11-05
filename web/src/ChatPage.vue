<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { ChevronDown, ChevronRight, Clipboard, File as FileIcon, History, Layers, LoaderCircle, PanelRight, Plus, Send, ShieldCheck, SquarePlus, Trash2, X } from 'lucide-vue-next'
import { activeSession, activeWorkspace, clearSession, newChat, runningSessions, selectSession, sessions, sessionWorkspace, syncThreadMessages, type ChatMode, type Session, type Turn, type WorkOptions } from './chat'
import { readLocal, settings, t, writeLocal } from './preferences'
import MarkdownText from './MarkdownText.vue'
import SessionHistory from './SessionHistory.vue'
import AgentContextPanel from './AgentContextPanel.vue'
import { memoryScope, streamWorkRequest, threadPath, uploadAttachment, workRequest, type MemoryReceipt, type ModelItem, type ToolReview, type WorkReply } from './workApi'
import RunActivityTrace from './RunActivityTrace.vue'
import ModelPicker from './ModelPicker.vue'
import ConversationRail from './ConversationRail.vue'
import ConversationStats from './ConversationStats.vue'
import UserAvatar from './UserAvatar.vue'
import { brandAsset } from './brand'

const emit = defineEmits<{ memory: [id: string]; evidence: [thread: string, run: string]; memoryReview: []; closeContext: [] }>()
const props = defineProps<{ memoryId?: string }>()
const session = computed(() => sessions.value.find(item => item.id === activeSession.value))
const mode = ref<ChatMode>(session.value?.mode ?? readLocal('adaptive.chatMode', 'chat'))
const archived = computed(() => Boolean(session.value?.archived))
const draft = ref('')
const files = ref<File[]>([])
const historyOpen = ref(false)
const historyArea = ref<HTMLElement>()
const historyTrigger = ref<HTMLButtonElement>()
let disposed = false
const confirmClear = ref(false)
const activityOpen = ref(new Set<string>())
const model = ref(readLocal('adaptive.model', 'auto'))
const work = ref<WorkOptions>({ approval: readLocal<WorkOptions>('adaptive.workOptions', { approval: 'ask' }).approval })
const models = ref<ModelItem[]>([])
const defaultModel = ref<string | null>(null)
const reasoningLevels = ref(readLocal<Record<string, string>>('adaptive.reasoningLevels', {}))
const selectedModel = computed(() => models.value.find(item =>
  item.id === (model.value === 'auto' ? defaultModel.value : model.value),
))
const reasoningEffort = computed(() => {
  const selected = selectedModel.value
  if (!selected) return undefined
  const saved = reasoningLevels.value[selected.id]
  return saved && selected.reasoning_levels.includes(saved) ? saved : selected.default_effort ?? undefined
})
function setReasoningEffort(value: string) {
  if (!selectedModel.value) return
  reasoningLevels.value[selectedModel.value.id] = value
  writeLocal('adaptive.reasoningLevels', reasoningLevels.value)
}
const transcript = ref<HTMLElement>()
const composer = ref<HTMLTextAreaElement>()
const fileInput = ref<HTMLInputElement>()
const copied = ref('')
const workBusy = ref(false)
const liveReplies = ref<Record<string, string>>({})
const workError = ref('')
const pendingReview = computed(() => session.value?.pendingReview)
const panelOpen = ref(Boolean(props.memoryId))
watch(() => props.memoryId, id => { panelOpen.value = Boolean(id) })
function closePanel() {
  panelOpen.value = false
  if (props.memoryId) emit('closeContext')
}
watch(model, value => writeLocal('adaptive.model', value))
watch(work, value => writeLocal('adaptive.workOptions', value), { deep: true })
watch(mode, value => writeLocal('adaptive.chatMode', value))
watch(activeSession, () => {
  if (session.value) mode.value = session.value.mode ?? 'chat'
  panelOpen.value = Boolean(props.memoryId)
  resetDraft()
}, { flush: 'sync' })
watch(() => session.value?.mode, value => {
  if (session.value?.draft && value) mode.value = value
})
onMounted(async () => {
  document.addEventListener('pointerdown', closeMenus)
  try {
    const result = await workRequest<{ items: ModelItem[]; default: string | null }>('/models')
    models.value = result.items
    defaultModel.value = result.default
    if (model.value !== 'auto' && !models.value.some(item => item.id === model.value)) model.value = 'auto'
  } catch (error) {
    showWorkError(error)
  }
})
onUnmounted(() => {
  document.removeEventListener('pointerdown', closeMenus)
  disposed = true
  resetDraft()
})
const turns = computed(() => session.value?.turns ?? [])
const empty = computed(() => !turns.value.length)
const canSend = computed(() => Boolean(draft.value.trim() || files.value.length))
watch(() => session.value, async target => {
  if (!target?.apiThreadId || runningSessions.has(target.id)) return
  await refreshReview(target)
  try {
    await syncThreadMessages(target)
    if (target.id === activeSession.value) scrollToEnd()
  } catch (error) {
    if (target.id === activeSession.value) showWorkError(error)
  }
}, { immediate: true })

function toggle(items: Set<string>, id: string) {
  if (items.has(id)) items.delete(id)
  else items.add(id)
}
function scrollToEnd() {
  void nextTick(() => {
    if (transcript.value) transcript.value.scrollTop = transcript.value.scrollHeight
  })
}
function submit() {
  if (archived.value || workBusy.value || runningSessions.has(activeSession.value) || pendingReview.value || !canSend.value) return
  const text = draft.value.trim()
  const uploads = [...files.value]
  const attachments = uploads.map(file => file.name)
  const turn: Turn = {
    id: crypto.randomUUID(), prompt: text, memory: settings.personalizedMemory,
    time: new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date()),
    files: attachments, model: model.value, reasoningEffort: reasoningEffort.value,
    ...(mode.value === 'work'
      ? { work: { ...work.value } }
      : {}),
  }
  if (!session.value) newChat(mode.value)
  if (!session.value) return
  if (!session.value.title) session.value.title = (text || attachments[0] || '').slice(0, 52)
  session.value.draft = false
  session.value.turns.push(turn)
  draft.value = ''
  files.value = []
  scrollToEnd()
  void sendToAgent(session.value, session.value.turns.at(-1)!, uploads)
}
function showWorkError(cause: unknown) {
  workError.value = cause instanceof Error ? cause.message : t('Work request failed.', '工作请求失败。')
}
async function refreshReview(target: Session) {
  if (!target.apiThreadId) return
  try {
    const review = await workRequest<ToolReview | null>(threadPath(target.apiThreadId) + '/approval')
    target.pendingReview = review
    if (!review) return
    const existing = target.turns.find(turn => turn.serverRunId === review.run_id)
    if (existing) {
      existing.activity = review.activity
      existing.metrics = review.metrics
      existing.usedMemoryIds = review.used_memory_ids
    } else if (review.message !== undefined) {
      target.turns.push({
        id: review.run_id, serverRunId: review.run_id, prompt: review.message,
        time: '', files: [], memory: false, activity: review.activity, metrics: review.metrics,
        usedMemoryIds: review.used_memory_ids,
      })
    }
  } catch (cause) {
    if (target.id === activeSession.value) showWorkError(cause)
  }
}
function applyWorkReply(target: Session, turn: Turn, result: ToolReview | WorkReply) {
  turn.serverRunId = result.run_id
  turn.usedMemoryIds = result.used_memory_ids
  if (result.activity) turn.activity = result.activity
  turn.metrics = result.metrics
  if ('approval_id' in result) {
    target.pendingReview = result
  } else {
    target.pendingReview = null
    turn.serverReply = result.response
    turn.profileVersion = result.profile_version
    if (turn.memory && target.apiThreadId) void loadMemoryReceipts(target.apiThreadId, turn, result.run_id)
  }
  scrollToEnd()
}
function runStream(path: string, body: object, turn: Turn) {
  let toolCount = turn.activity?.filter(entry => entry.kind === 'tool').length ?? 0
  return streamWorkRequest(path, body, update => {
    if (update.event === 'activity') {
      turn.serverRunId = update.run_id
      const count = update.items.filter(entry => entry.kind === 'tool').length
      if (count > toolCount) delete liveReplies.value[turn.id]
      toolCount = count
      turn.activity = update.items
      turn.metrics = update.metrics
    } else {
      liveReplies.value[turn.id] = (liveReplies.value[turn.id] ?? '') + update.text
    }
    const area = transcript.value
    if (area && area.scrollHeight - area.scrollTop - area.clientHeight < 100) scrollToEnd()
  })
}
async function loadMemoryReceipts(thread: string, turn: Turn, run: string) {
  const path = threadPath(thread) + '/memory-receipts?run_id=' + encodeURIComponent(run)
  try {
    for (let attempt = 0; attempt < 12; attempt++) {
      const result = await workRequest<{ pending: boolean; items: MemoryReceipt[]; suggestion_ids: string[] }>(path)
      if (result.items.length) turn.savedMemories = result.items
      if (result.suggestion_ids?.length) turn.memorySuggestionIds = result.suggestion_ids
      if (!result.pending) return
      await new Promise(resolve => window.setTimeout(resolve, 700))
    }
  } catch {
    turn.memoryReceiptError = t('Could not load memory receipts.', '无法读取记忆回执。')
  }
}
async function sendToAgent(target: Session, turn: Turn, uploads: File[]) {
  const agentMode = turn.work ? 'work' : 'chat'
  workBusy.value = true
  runningSessions.add(target.id)
  workError.value = ''
  const startedAt = performance.now()
  try {
    if (!target.apiThreadId) {
      const input = { mode: agentMode, title: target.title }
      const thread = await workRequest<{ thread_id: string }>('/threads', 'POST', {
        ...input, workspace_id: sessionWorkspace(target),
      })
      target.apiThreadId = thread.thread_id
    }
    const attachmentIds = []
    for (const file of uploads) {
      const attachment = await uploadAttachment(target.apiThreadId, file)
      attachmentIds.push(attachment.id)
    }
    const result = await runStream('/chat', {
      stream: true,
      attachment_ids: attachmentIds,
      model: turn.model === 'auto' ? undefined : turn.model,
      reasoning_effort: turn.reasoningEffort,
      thread_id: target.apiThreadId, message: turn.prompt, mode: agentMode,
      project_id: sessionWorkspace(target) === 'personal' ? undefined : sessionWorkspace(target),
      personalization_enabled: turn.memory, approval_policy: turn.work?.approval === 'every' ? 'every' : 'ask',
    }, turn)
    turn.activityMs = performance.now() - startedAt
    applyWorkReply(target, turn, result)
  } catch (cause) {
    turn.serverError = cause instanceof Error ? cause.message : t('Could not get a reply.', '无法获取回复。')
    if (target.id === activeSession.value && !draft.value && !files.value.length) {
      draft.value = turn.prompt
      files.value = uploads
    }
    if (target.apiThreadId) await refreshReview(target)
  } finally {
    delete liveReplies.value[turn.id]
    runningSessions.delete(target.id)
    workBusy.value = false
  }
}
async function resolveReview(approved: boolean) {
  const target = session.value
  const review = target?.pendingReview
  const turn = target?.turns.find(item => item.serverRunId === review?.run_id) ?? target?.turns.at(-1)
  if (!target?.apiThreadId || !review || !turn || workBusy.value || runningSessions.has(target.id) || archived.value) return
  activityOpen.value.delete(turn.id)
  workBusy.value = true
  runningSessions.add(target.id)
  workError.value = ''
  turn.serverError = undefined
  const startedAt = performance.now()
  try {
    const result = await runStream(
      threadPath(target.apiThreadId) + '/approval/' + encodeURIComponent(review.approval_id) + '?stream=true',
      { approved }, turn,
    )
    turn.activityMs = (turn.activityMs ?? 0) + performance.now() - startedAt
    applyWorkReply(target, turn, result)
  } catch (cause) {
    turn.serverError = cause instanceof Error ? cause.message : t('Could not resume the Agent.', '无法继续执行 Agent。')
    await refreshReview(target)
  } finally {
    delete liveReplies.value[turn.id]
    runningSessions.delete(target.id)
    workBusy.value = false
  }
}
function createSession() {
  newChat(mode.value)
  historyOpen.value = false
  void nextTick(() => composer.value?.focus())
}
function workRunning(turn: Turn) {
  const runningId = pendingReview.value?.run_id
  return runningSessions.has(activeSession.value) && !turn.serverReply &&
    (runningId ? turn.serverRunId === runningId : turns.value.at(-1)?.id === turn.id)
}
function waitingForReview(turn: Turn) {
  return Boolean(turn.serverRunId && pendingReview.value?.run_id === turn.serverRunId)
}
function showActivity(turn: Turn) {
  return workRunning(turn) || waitingForReview(turn) || activityOpen.value.has(turn.id)
}
function activityLabel(turn: Turn) {
  if (waitingForReview(turn) && !workRunning(turn)) return t('Awaiting approval', '等待审批')
  if (workRunning(turn)) return t('Agent is working…', 'Agent 正在处理…')
  if (turn.serverError) return t('Run interrupted', '运行中断')
  if (turn.activityMs === undefined) return t('Activity', '处理记录')
  const seconds = Math.max(1, Math.round(turn.activityMs / 1000))
  return t(`Processed in ${seconds}s`, `已处理（用时 ${seconds} 秒）`)
}
function switchMode(value: ChatMode) {
  if (mode.value === value) return
  mode.value = value
  createSession()
}
function resetDraft() {
  draft.value = ''
  files.value = []
  workError.value = ''
  confirmClear.value = false
  if (!disposed) scrollToEnd()
}
function openFromHistory(id: string) {
  selectSession(id)
  historyOpen.value = false
}
async function clearCurrent() {
  if (!session.value || archived.value || workBusy.value) return
  const id = session.value.id
  workBusy.value = true
  try {
    await clearSession(id)
    if (id === activeSession.value) {
      resetDraft()
      historyOpen.value = false
      void nextTick(() => composer.value?.focus())
    }
  } catch (cause) {
    showWorkError(cause)
  } finally {
    workBusy.value = false
  }
}
function closeMenus(event: PointerEvent) {
  const target = event.target as Node
  if (!historyArea.value?.contains(target)) {
    historyOpen.value = false
    confirmClear.value = false
  }
}
function dismissHistory() {
  historyOpen.value = false
  confirmClear.value = false
  historyTrigger.value?.focus()
}
function addFiles(event: Event) {
  const input = event.target as HTMLInputElement
  const selected = Array.from(input.files ?? [])
  input.value = ''
  if (files.value.length + selected.length > 8) {
    workError.value = t('Attach at most eight files.', '每次最多添加 8 个文件。')
    return
  }
  if (selected.some(file => file.size > 2 * 1024 * 1024)) {
    workError.value = t('Each file must be no larger than 2 MiB.', '单个文件不能超过 2 MiB。')
    return
  }
  workError.value = ''
  files.value.push(...selected)
}
function onComposerKey(event: KeyboardEvent) {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault()
    submit()
  }
}
async function copy(turn: Turn) {
  const value = turn.serverReply ?? ''
  try {
    await navigator.clipboard.writeText(value)
    copied.value = turn.id
  } catch {
    copied.value = 'error'
  }
}
</script>

<template>
  <main class="chat-page" :class="{ 'chat-welcome': empty, 'work-mode': mode === 'work' }">
    <section v-if="session" class="conversation" :aria-label="mode === 'work' ? t('Work', '工作') : t('Chat', '聊天')">
      <header class="chat-header">
        <div class="chat-modes" role="group" :aria-label="t('Conversation mode', '对话模式')">
          <button type="button" :aria-pressed="mode === 'chat'" @click="switchMode('chat')">{{ t('Chat', '聊天') }}</button>
          <button type="button" :aria-pressed="mode === 'work'" @click="switchMode('work')">{{ t('Work', '工作') }}</button>
        </div>
        <div class="chat-actions">
          <button v-if="!memoryId" class="chat-icon" type="button" :aria-expanded="panelOpen" :aria-label="t('Context monitor', '上下文监控')" :title="t('Context monitor', '上下文监控')" @click="panelOpen = !panelOpen"><PanelRight /></button>
          <div ref="historyArea" class="history-anchor" @keydown.esc.stop="dismissHistory">
            <button ref="historyTrigger" class="chat-icon" type="button" :aria-expanded="historyOpen" :title="t('History', '历史')" :aria-label="t('History', '历史')" @click="historyOpen = !historyOpen; confirmClear = false"><History /></button>
            <div v-if="historyOpen" class="history-popover">
              <SessionHistory :key="activeWorkspace" @open="openFromHistory" />
              <div v-if="!empty && !archived" class="clear-section">
                <button v-if="!confirmClear" type="button" class="quiet-button" :disabled="workBusy" @click="confirmClear = true"><Trash2 />{{ t('Clear conversation', '清空对话') }}</button>
                <template v-else>
                  <p>{{ t('Clear all messages in this conversation?', '清空当前对话中的所有消息？') }}</p>
                  <button type="button" class="quiet-button" @click="confirmClear = false">{{ t('Cancel', '取消') }}</button>
                  <button type="button" class="accent-button" :disabled="workBusy" @click="clearCurrent">{{ t('Clear', '清空') }}</button>
                </template>
              </div>
            </div>
          </div>
          <button type="button" class="chat-icon" :title="t('New session', '新建会话')" :aria-label="t('New session', '新建会话')" @click="createSession"><SquarePlus /></button>
        </div>
      </header>

      <ConversationRail v-if="!empty" :turns="turns" :scroller="transcript" />
      <div v-if="!empty" ref="transcript" class="transcript" :aria-label="t('Conversation', '对话记录')">
        <article v-for="turn in turns" :key="turn.id" class="chat-turn" :data-run-id="turn.serverRunId">
          <div class="user-message">
            <div class="user-content"><div class="user-bubble"><MarkdownText v-if="turn.prompt" :text="turn.prompt" /><div v-if="turn.files.length" class="message-files"><FileIcon />{{ turn.files.join(', ') }}</div></div><time>{{ turn.time }}</time></div>
            <UserAvatar />
          </div>
          <div v-if="workRunning(turn) || waitingForReview(turn) || turn.serverReply || turn.serverError || turn.activity?.length" class="assistant-message">
            <img :src="brandAsset('mark.png')" class="assistant-icon" alt="">
            <div class="assistant-content">
              <header><strong>slothy</strong><time>{{ turn.time }}</time></header>
              <template v-if="turn.activity?.length || turn.toolDecisions?.length || workRunning(turn) || waitingForReview(turn)">
              <button class="activity-trigger" :aria-expanded="showActivity(turn)" :aria-disabled="workRunning(turn) || waitingForReview(turn)" @click="!workRunning(turn) && !waitingForReview(turn) && toggle(activityOpen, turn.id)">
                <component :is="showActivity(turn) ? ChevronDown : ChevronRight" />
                {{ activityLabel(turn) }}
              </button>
              <div class="activity-collapse" :class="{ 'activity-expanded': showActivity(turn) }" :inert="!showActivity(turn)">
                <div class="activity-inner">
                  <div class="activity-trace">
                    <RunActivityTrace :items="turn.activity ?? []" :interrupted="Boolean(turn.serverError && !workRunning(turn))" />
                    <p v-if="workRunning(turn) && !turn.activity?.length" class="tool-line"><LoaderCircle class="loading-icon" />{{ t('Waiting for the Agent', '等待 Agent 返回') }}</p>
                    <p v-for="decision in turn.activity?.length ? [] : turn.toolDecisions" :key="decision.id" class="tool-line">
                      {{ decision.approved ? t('Approved', '已批准') : t('Rejected', '已拒绝') }} · {{ decision.names.join(', ') }}
                    </p>
                  </div>
                </div>
              </div>
              </template>
              <p v-if="turn.serverError" class="muted" role="alert">{{ turn.serverError }}</p>
              <MarkdownText v-else-if="turn.serverReply" class="answer" :text="turn.serverReply" previews />
              <MarkdownText v-else-if="liveReplies[turn.id]" class="answer" :text="liveReplies[turn.id] ?? ''" />
              <footer v-if="turn.serverReply && !turn.serverError" class="answer-footer">
                <span v-if="turn.memoryReceiptError" role="alert">{{ turn.memoryReceiptError }}</span>
                <button v-for="memory in turn.savedMemories" :key="memory.id" class="text-link" @click="emit('memory', memory.id)">
                  {{ t('Memory saved', '已记住') }} · {{ memory.title }} · {{ memoryScope(memory.scope, t) }}
                </button>
                <button v-if="turn.memorySuggestionIds?.length" class="text-link" @click="emit('memoryReview')">
                  {{ t('Review memory suggestions', '查看记忆建议') }}
                </button>
                <button class="quiet-button copy-answer" @click="copy(turn)"><Clipboard />{{ copied === turn.id ? t('Copied', '已复制') : copied === 'error' ? t('Copy failed', '复制失败') : t('Copy', '复制') }}</button>
              </footer>
            </div>
          </div>
        </article>
        <section v-if="pendingReview && !archived" class="tool-review" aria-labelledby="tool-review-title">
          <h2 id="tool-review-title"><ShieldCheck />{{ t('Review tool actions', '审查工具操作') }}</h2>
          <p>{{ t('These actions are paused. Approving runs every action listed below.', '这些操作已暂停。批准后会执行下列所有操作。') }}</p>
          <ul>
            <li v-for="(call, index) in pendingReview.calls" :key="index">
              <strong>{{ call.name }}</strong>
              <pre>{{ JSON.stringify(call.arguments, null, 2) }}</pre>
            </li>
          </ul>
          <div class="tool-review-actions">
            <button type="button" class="outline-button" :disabled="workBusy" @click="resolveReview(false)">{{ t('Reject', '拒绝') }}</button>
            <button type="button" class="accent-button" :disabled="workBusy" @click="resolveReview(true)">{{ t('Approve and run', '批准并执行') }}</button>
          </div>
        </section>
      </div>

      <div v-if="!archived" class="composer-region">
        <div v-if="empty" class="welcome-identity">
          <img class="welcome-mark" :src="brandAsset('mark.png')" alt="">
          <h1>{{ mode === 'work' ? t('What should we work on?', '我们要做什么？') : t('What’s on your mind?', '今天想聊点什么？') }}</h1>
        </div>
        <form class="composer" :class="{ 'composer-with-files': files.length }" @submit.prevent="submit">
          <textarea ref="composer" v-model="draft" rows="1" :placeholder="mode === 'work' ? t('Ask slothy to work…', '使用 slothy 工作…') : t('Ask slothy…', '询问 slothy…')" :aria-label="t('Message slothy', '发消息给 slothy')" @keydown="onComposerKey"></textarea>
          <ul v-if="files.length" class="attachment-list"><li v-for="(file, index) in files" :key="index"><span>{{ file.name }}</span><button type="button" class="quiet-button" :aria-label="t('Remove attachment', '移除附件')" @click="files.splice(index, 1)"><X /></button></li></ul>
          <div class="composer-actions">
            <input ref="fileInput" type="file" multiple hidden @change="addFiles">
            <button class="chat-icon composer-add" type="button" :title="t('Attach files', '添加附件')" :aria-label="t('Attach files', '添加附件')" @click="fileInput?.click()"><Plus /></button>
            <label v-if="mode === 'work'" class="composer-select approval-select">
              <ShieldCheck aria-hidden="true" />
              <select v-model="work.approval" :aria-label="t('Approval mode', '审批模式')">
                <option value="ask">{{ t('Ask me to approve', '询问我的批准') }}</option>
                <option value="every">{{ t('Approve each action', '逐项批准') }}</option>
              </select>
            </label>
            <ModelPicker v-model="model" :items="models" :selected="selectedModel"
              :effort="reasoningEffort" @effort="setReasoningEffort" />
            <button class="voice-button" type="submit" :disabled="workBusy || Boolean(pendingReview) || !canSend" :aria-label="t('Send message', '发送消息')"><Send /></button>
          </div>
        </form>
        <div v-if="mode === 'work'" class="work-dock">
          <button type="button" class="dock-button" @click="fileInput?.click()"><Layers />{{ t('Files', '文件') }}</button>
        </div>
        <ConversationStats :turns="turns" />
        <p v-if="workError" class="voice-status" role="alert">{{ workError }}</p>
        <p v-else-if="workBusy" class="voice-status" role="status">{{ t('Working…', '正在处理…') }}</p>
      </div>
      <p v-else class="archived-notice">{{ t('Archived session', '已归档会话') }}</p>
    </section>
    <p v-else class="conversation-empty">{{ t('Select a session or start a new one.', '选择一个会话或新建') }}</p>
    <AgentContextPanel v-if="panelOpen && (session || memoryId)" :session="session" :memory-id="memoryId"
      @evidence="(thread, run) => emit('evidence', thread, run)" @close="closePanel">
      <template #memory-actions><slot name="memory-actions" /></template>
    </AgentContextPanel>
  </main>
</template>
