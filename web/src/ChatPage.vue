<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { ChevronDown, ChevronRight, CircleAlert, File as FileIcon, History, LoaderCircle, PanelRight, Plus, Send, ShieldCheck, Square, SquarePlus, Trash2, X } from 'lucide-vue-next'
import { activeSession, activeWorkspace, clearSession, newChat, runningRequests as requestControllers, runningSessions, runningTurnIds, selectSession, sessions, sessionWorkspace, syncThreadMessages, workspaces, type ChatMode, type Session, type Turn, type WorkOptions } from './chat'
import { readLocal, settings, t, writeLocal } from './preferences'
import MarkdownText from './MarkdownText.vue'
import ReasoningTrace from './ReasoningTrace.vue'
import CopyMessage from './CopyMessage.vue'
import SessionHistory from './SessionHistory.vue'
import AgentContextPanel from './AgentContextPanel.vue'
import { ApiError, memoryScope, streamWorkRequest, threadPath, uploadAttachment, workRequest, type MemoryReceipt, type ModelItem, type ToolReview, type WorkReply, type ToolArtifact } from './workApi'
import RunActivityTrace from './RunActivityTrace.vue'
import ModelPicker from './ModelPicker.vue'
import ConversationRail from './ConversationRail.vue'
import ConversationStats from './ConversationStats.vue'
import UserAvatar from './UserAvatar.vue'
import { brandAsset } from './brand'
import { callRisk, downloadArtifact, irreversibleCall, riskLabel } from './toolResults'
import ComposerPalette from './composer/ComposerPalette.vue'
import ReferenceChip from './composer/ReferenceChip.vue'
import { useComposerPalette } from './composer/useComposerPalette'
import { useComposerCommands } from './composer/useComposerCommands'
import type { PaletteItem } from './composer/types'
import { beginSessionRequest, finishSessionRequest, liveReplies, liveReasoning, pendingSessionActions } from './sessionRuns'
import { commandAvailable, filterCommands, findCommand, parseCommand } from './commands/registry'
import type { CommandName } from './commands/types'
import { searchReferences } from './references/providers'
import { referenceKey, referencePayload, type ReferenceItem, type ReferenceKind } from './references/types'
import { replaceToken, unresolvedMentions, type ComposerTrigger } from './references/parser'

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
// Prefer the saved effort when the model still supports it, otherwise fall back to its default.
// 优先使用已保存且模型仍支持的强度，否则回退到该模型的默认值。
const reasoningEffort = computed(() => {
  const selected = selectedModel.value
  if (!selected) return undefined
  const saved = reasoningLevels.value[selected.id]
  return saved && selected.reasoning_levels.includes(saved) ? saved : selected.default_effort ?? undefined
})
/**
 * Persist the reasoning effort chosen for the active model.
 * 保存当前模型所选的思考强度。
 *
 * @param value - Effort id reported by the model picker. / 模型选择器返回的强度标识。
 */
function setReasoningEffort(value: string) {
  if (!selectedModel.value) return
  reasoningLevels.value[selectedModel.value.id] = value
  writeLocal('adaptive.reasoningLevels', reasoningLevels.value)
}
const transcript = ref<HTMLElement>()
const composer = ref<HTMLTextAreaElement>()
const fileInput = ref<HTMLInputElement>()
const workBusy = computed(() => runningSessions.has(activeSession.value) || pendingSessionActions.has(activeSession.value))
const workError = ref('')
const selectedReferences = ref<ReferenceItem[]>([])
const referenceTextConfirm = ref(false)
const composerArea = ref<HTMLElement>()
const modelPicker = ref<InstanceType<typeof ModelPicker>>()
const confirmationArea = ref<HTMLElement>()
const command = useComposerCommands({ session, mode, model, models, draft, busy: workBusy,
  create: createSession, switchMode, clear: resetDraft, attach: () => fileInput.value?.click(),
  openModel: () => modelPicker.value?.openModels(), arguments: askCommandArguments,
  stop: stopCurrent, retry: retryCurrent, error: showWorkError })
const palette = useComposerPalette(draft, composer, paletteItems, commitPalette)
const paletteTitle = computed(() => palette.trigger.value?.kind === 'add' ? t('Add and commands', '添加与命令')
  : palette.trigger.value?.kind === 'reference' ? t('References', '引用')
    : palette.trigger.value?.kind === 'argument' ? '/' + palette.trigger.value.command : t('Commands', '命令'))
watch(command.confirmation, value => { if (value) void nextTick(() => confirmationArea.value?.querySelector<HTMLButtonElement>('button')?.focus()) })
const pendingReview = computed(() => session.value?.pendingReview)
const irreversibleReview = computed(() => pendingReview.value?.calls.some(irreversibleCall) ?? false)
const reviewPanel = ref<HTMLElement>()
// A high-risk review is non-modal; start keyboard focus on Reject, never on approval.
// 高危审查沿用非模态面板，键盘焦点从拒绝开始，不默认选中批准。
watch(() => pendingReview.value?.approval_id, id => {
  if (id && irreversibleReview.value) void nextTick(() => reviewPanel.value?.querySelector<HTMLButtonElement>('button')?.focus())
}, { immediate: true })
const panelOpen = ref(Boolean(props.memoryId))
watch(() => props.memoryId, id => { panelOpen.value = Boolean(id) })
/** Close the context panel and notify the parent when it was opened from memory detail. / 关闭上下文面板，并在从记忆详情打开时通知父组件。 */
function closePanel() {
  panelOpen.value = false
  if (props.memoryId) emit('closeContext')
}
watch(model, value => writeLocal('adaptive.model', value))
watch(work, value => writeLocal('adaptive.workOptions', value), { deep: true })
watch(mode, value => writeLocal('adaptive.chatMode', value))
// Switching sessions resets the local draft and mirrors the session mode.
// 切换会话时重置本地草稿，并同步该会话的模式。
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
watch([
  () => { const id = runningTurnIds[activeSession.value]; return id ? liveReplies.value[id] : undefined },
  () => { const id = runningTurnIds[activeSession.value]; return id ? liveReasoning.value[id] : undefined },
], () => {
  const area = transcript.value
  if (area && area.scrollHeight - area.scrollTop - area.clientHeight < 100) scrollToEnd()
})
const empty = computed(() => !turns.value.length)
const canSend = computed(() => Boolean(draft.value.trim() || files.value.length || selectedReferences.value.length))
// Reload pending review and transcript whenever the selected session changes.
// 选中会话变化时重新加载待审批状态与消息记录。
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

/**
 * Toggle a string id inside one of the collapsible state sets.
 * 在可折叠状态集合中切换某个 id。
 *
 * @param items - Set to mutate. / 待修改的集合。
 * @param id - Id to add or remove. / 要添加或移除的 id。
 */
function toggle(items: Set<string>, id: string) {
  if (items.has(id)) items.delete(id)
  else items.add(id)
}
/** Pin the transcript to its latest message on the next tick. / 在下一个 tick 将对话记录滚动到底部。 */
function scrollToEnd() {
  void nextTick(() => {
    if (transcript.value) transcript.value.scrollTop = transcript.value.scrollHeight
  })
}
/** Append the draft as a new turn and hand it to the agent. / 将草稿作为新一轮追加并交给 Agent 处理。 */
function submit(allowUnresolved?: unknown) {
  const parsed = parseCommand(draft.value)
  if (parsed) {
    const input = draft.value
    palette.close()
    void command.run(parsed.name, parsed.argument).then(accepted => {
      if (accepted && draft.value === input) draft.value = ''
    }).catch(showWorkError)
    return
  }
  if (draft.value.trim() === '/') { palette.update(); return }
  if (command.busy.value) return
  if (allowUnresolved !== true && unresolvedMentions(draft.value).length) { referenceTextConfirm.value = true; return }
  if (archived.value || workBusy.value || runningSessions.has(activeSession.value) || pendingReview.value || !canSend.value) return
  if (session.value?.pendingRun) {
    showWorkError(new Error(t('Continue the saved run before sending a new message.', '请先从检查点继续当前任务，再发送新消息。')))
    return
  }
  const text = draft.value.trim()
  const uploads = [...files.value]
  const attachments = uploads.map(file => file.name)
  const turn: Turn = {
    id: crypto.randomUUID(), prompt: text, memory: settings.personalizedMemory,
    time: new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date()),
    files: attachments, model: model.value, reasoningEffort: reasoningEffort.value,
    references: [...selectedReferences.value],
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
  selectedReferences.value = []
  palette.close()
  scrollToEnd()
  void sendToAgent(session.value, session.value.turns.at(-1)!, uploads)
}
/**
 * Surface a work request failure as the composer-level error message.
 * 将工作请求失败显示为输入区级别的错误信息。
 *
 * @param cause - Value thrown by the failed request. / 请求失败时抛出的值。
 */
function showWorkError(cause: unknown) {
  workError.value = cause instanceof Error ? cause.message : t('Work request failed.', '工作请求失败。')
}
/**
 * Reload any pending tool review for a session and merge it into its turn.
 * 重新读取会话中待处理的工具审批，并合并到对应轮次。
 *
 * @param target - Session to refresh. / 需要刷新的会话。
 */
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
      existing.memoryTrace = review.memory_trace
      existing.responseSource = review.response_source
      existing.usedMemoryIds = review.used_memory_ids
      existing.references = review.references ?? existing.references
      existing.usedReferenceIds = review.used_reference_ids ?? existing.usedReferenceIds
    } else if (review.message !== undefined) {
      target.turns.push({
        id: review.run_id, serverRunId: review.run_id, prompt: review.message,
        time: '', files: [], memory: false, activity: review.activity, metrics: review.metrics,
        usedMemoryIds: review.used_memory_ids, memoryTrace: review.memory_trace, responseSource: review.response_source,
        references: review.references, usedReferenceIds: review.used_reference_ids,
      })
    }
  } catch (cause) {
    if (target.id === activeSession.value) showWorkError(cause)
  }
}
/**
 * Copy a completed or paused run's metadata into its turn and update pending state.
 * 将完成或暂停运行的元数据写入对应轮次，并更新待审批状态。
 *
 * @param target - Session that owns the turn. / 轮次所属会话。
 * @param turn - Turn receiving the reply metadata. / 接收回复元数据的轮次。
 * @param result - Completed reply or approval request returned by the server. / 服务端返回的完成回复或审批请求。
 */
function applyWorkReply(target: Session, turn: Turn, result: ToolReview | WorkReply) {
  turn.serverRunId = result.run_id
  turn.memoryTrace = result.memory_trace
  turn.responseSource = result.response_source
  turn.usedMemoryIds = result.used_memory_ids
  if (result.activity) turn.activity = result.activity
  turn.metrics = result.metrics
  if ('references' in result && result.references) turn.references = result.references
  if ('used_reference_ids' in result) turn.usedReferenceIds = result.used_reference_ids
  if ('approval_id' in result) {
    target.pendingReview = result
  } else {
    target.pendingReview = null
    target.pendingRun = null
    turn.checkpointId = undefined
    turn.serverReply = result.response
    turn.reasoning = result.reasoning ?? liveReasoning.value[turn.id] ?? turn.reasoning
    delete liveReasoning.value[turn.id]
    turn.profileVersion = result.profile_version
    if (turn.memory && target.apiThreadId) void loadMemoryReceipts(target.apiThreadId, turn, result.run_id)
  }
  if (!disposed && target.id === activeSession.value) scrollToEnd()
}
/**
 * Stream one agent request and fold activity and delta events into the turn.
 * 流式执行一次 Agent 请求，并将活动与增量事件写入该轮次。
 *
 * @param path - API path to stream from. / 流式请求的接口路径。
 * @param body - JSON body sent to the endpoint. / 发送给接口的 JSON 请求体。
 * @param turn - Turn whose live state is updated while streaming. / 流式过程中持续更新的轮次。
 * @returns The final work reply or approval request. / 最终的回复或审批请求。
 */
function runStream(owner: string, path: string, body: object, turn: Turn, signal?: AbortSignal, resolveTurn?: (run: string) => Turn) {
  let toolCount = turn.activity?.filter(entry => entry.kind === 'tool').length ?? 0
  return streamWorkRequest(path, body, update => {
    if (update.event === 'activity') {
      if (resolveTurn) turn = resolveTurn(update.run_id)
      turn.serverRunId = update.run_id
      const count = update.items.filter(entry => entry.kind === 'tool').length
      if (count > toolCount) delete liveReplies.value[turn.id]
      toolCount = count
      turn.activity = update.items
      turn.metrics = update.metrics
    } else if (update.event === 'reasoning') {
      liveReasoning.value[turn.id] = (liveReasoning.value[turn.id] ?? '') + update.text
    } else {
      liveReplies.value[turn.id] = (liveReplies.value[turn.id] ?? '') + update.text
    }
    const area = transcript.value
    if (!disposed && owner === activeSession.value && area && area.scrollHeight - area.scrollTop - area.clientHeight < 100) scrollToEnd()
  }, signal)
}
/**
 * Poll memory receipts until the background learning pass settles.
 * 轮询记忆回执，直到后台学习任务完成。
 *
 * @param thread - API thread id that produced the turn. / 产生该轮次的接口会话 id。
 * @param turn - Turn receiving saved memories and suggestions. / 接收已保存记忆与建议的轮次。
 * @param run - Run id whose receipts are requested. / 请求回执的运行 id。
 */
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
/**
 * Create the thread if needed, upload attachments, stream the run and record its outcome.
 * 按需创建会话、上传附件、流式执行并记录结果。
 *
 * @param target - Session the turn belongs to. / 该轮次所属会话。
 * @param turn - Turn to send and complete. / 要发送并补全的轮次。
 * @param uploads - Files to upload before sending. / 发送前要上传的文件。
 */
async function sendToAgent(target: Session, turn: Turn, uploads: File[]) {
  const agentMode = turn.work ? 'work' : 'chat'
  const controller = beginSessionRequest(target.id, turn.id)
  workError.value = ''
  const startedAt = performance.now()
  let failed = false
  try {
    if (!target.apiThreadId) {
      const input = { mode: agentMode, title: target.title }
      const thread = await workRequest<{ thread_id: string }>('/threads', 'POST', {
        ...input, workspace_id: sessionWorkspace(target),
      }, { signal: controller.signal })
      target.apiThreadId = thread.thread_id
    }
    const attachmentIds = uploads.length ? [] : [...(turn.attachmentIds ?? [])]
    for (const file of uploads) {
      const attachment = await uploadAttachment(target.apiThreadId, file, controller.signal)
      attachmentIds.push(attachment.id)
    }
    turn.attachmentIds = attachmentIds
    const result = await runStream(target.id, '/chat', {
      stream: true,
      attachment_ids: attachmentIds,
      references: (turn.references ?? []).map(referencePayload),
      model: turn.model === 'auto' ? undefined : turn.model,
      reasoning_effort: turn.reasoningEffort,
      thread_id: target.apiThreadId, message: turn.prompt, mode: agentMode,
      project_id: sessionWorkspace(target) === 'personal' ? undefined : sessionWorkspace(target),
      personalization_enabled: turn.memory, approval_policy: turn.work?.approval === 'every' ? 'every' : 'ask',
    }, turn, controller.signal)
    turn.activityMs = performance.now() - startedAt
    applyWorkReply(target, turn, result)
  } catch (cause) {
    failed = true
    turn.serverError = controller.signal.aborted ? t('Run stopped. Use /retry to resume.', '已停止运行，可使用 /retry 恢复。') : cause instanceof Error ? cause.message : t('Could not get a reply.', '无法获取回复。')
    if (!disposed && target.id === activeSession.value && !draft.value && !files.value.length) {
      draft.value = turn.prompt
      files.value = uploads
      selectedReferences.value = [...(turn.references ?? [])]
    }
    if (target.apiThreadId) await refreshReview(target)
  } finally {
    finishSessionRequest(target.id, controller)
    if (failed && target.apiThreadId) await syncThreadMessages(target).catch(() => {})
  }
}
/**
 * Approve or reject the pending tool review and continue the same run.
 * 批准或拒绝待处理的工具审批，并继续同一运行。
 *
 * @param approved - Whether the user approved the paused actions. / 用户是否批准了暂停的操作。
 */
async function resolveReview(approved: boolean) {
  const target = session.value
  const review = target?.pendingReview
  const turn = target?.turns.find(item => item.serverRunId === review?.run_id) ?? target?.turns.at(-1)
  if (!target?.apiThreadId || !review || !turn || workBusy.value || runningSessions.has(target.id) || archived.value) return
  activityOpen.value.delete(turn.id)
  const controller = beginSessionRequest(target.id, turn.id)
  workError.value = ''
  turn.serverError = undefined
  const startedAt = performance.now()
  try {
    const result = await runStream(target.id,
      threadPath(target.apiThreadId) + '/approval/' + encodeURIComponent(review.approval_id) + '?stream=true',
      { approved }, turn, controller.signal,
    )
    turn.activityMs = (turn.activityMs ?? 0) + performance.now() - startedAt
    applyWorkReply(target, turn, result)
  } catch (cause) {
    turn.serverError = cause instanceof Error ? cause.message : t('Could not resume the Agent.', '无法继续执行 Agent。')
    await refreshReview(target)
  } finally {
    finishSessionRequest(target.id, controller)
  }
}
/** Start a fresh session in the current mode and focus the composer. / 以当前模式新建会话并聚焦输入框。 */
function createSession() {
  newChat(mode.value)
  historyOpen.value = false
  void nextTick(() => composer.value?.focus())
}
/**
 * Whether a turn currently represents the in-flight agent run.
 * 判断某个轮次是否对应当前正在执行的 Agent 运行。
 *
 * @param turn - Turn to test. / 待判断的轮次。
 * @returns True while the run is streaming or waiting on approval. / 运行流式执行或等待审批时为 true。
 */
function workRunning(turn: Turn) {
  const owner = runningTurnIds[activeSession.value]
  if (owner) return runningSessions.has(activeSession.value) && owner === turn.id && !turn.serverReply
  const runningId = pendingReview.value?.run_id
  return runningSessions.has(activeSession.value) && !turn.serverReply &&
    (runningId ? turn.serverRunId === runningId : turns.value.at(-1)?.id === turn.id)
}
/**
 * Whether a turn is paused on the current tool review.
 * 判断某个轮次是否正等待当前工具审批。
 *
 * @param turn - Turn to test. / 待判断的轮次。
 * @returns True when the pending review belongs to this turn. / 待审批属于该轮次时为 true。
 */
function waitingForReview(turn: Turn) {
  return Boolean(turn.serverRunId && pendingReview.value?.run_id === turn.serverRunId)
}
/**
 * Whether the activity trace should be expanded for a turn.
 * 判断某轮次的活动记录是否应展开。
 *
 * @param turn - Turn to test. / 待判断的轮次。
 * @returns True when running, awaiting review or manually opened. / 运行中、等待审批或手动展开时为 true。
 */
function showActivity(turn: Turn) {
  return workRunning(turn) || waitingForReview(turn) || activityOpen.value.has(turn.id)
}
/**
 * Label for the activity disclosure, including run timing when known.
 * 生成活动记录标题，并在可用时显示运行耗时。
 *
 * @param turn - Turn whose activity label is rendered. / 要渲染活动标题的轮次。
 * @returns Localized activity label. / 本地化活动标题。
 */
function activityLabel(turn: Turn) {
  if (waitingForReview(turn) && !workRunning(turn)) return t('Awaiting approval', '等待审批')
  if (workRunning(turn)) return t('Agent is working…', 'Agent 正在处理…')
  if (turn.serverError) return t('Run interrupted', '运行中断')
  if (turn.activityMs === undefined) return t('Activity', '处理记录')
  const seconds = Math.max(1, Math.round(turn.activityMs / 1000))
  return t(`Processed in ${seconds}s`, `已处理（用时 ${seconds} 秒）`)
}
/**
 * Switch between chat and work mode, starting a new session to apply it.
 * 在聊天与工作模式间切换，并新建会话来应用该模式。
 *
 * @param value - Requested conversation mode. / 请求的对话模式。
 */
function switchMode(value: ChatMode) {
  if (mode.value === value) return
  mode.value = value
  createSession()
}
/** Clear the draft, attachments and transient composer errors. / 清空草稿、附件与临时输入区错误。 */
function resetDraft() {
  draft.value = ''
  files.value = []
  selectedReferences.value = []
  referenceTextConfirm.value = false
  palette.close()
  workError.value = ''
  confirmClear.value = false
  if (!disposed) scrollToEnd()
}
/**
 * Open a session chosen from the history popover.
 * 打开从历史弹层中选择的会话。
 *
 * @param id - Session id to activate. / 要激活的会话 id。
 */
function openFromHistory(id: string) {
  selectSession(id)
  historyOpen.value = false
}
/** Clear all messages of the active session after confirmation. / 确认后清空当前会话的全部消息。 */
async function clearCurrent() {
  if (!session.value || archived.value || workBusy.value) return
  const id = session.value.id
  pendingSessionActions.add(id)
  try {
    await clearSession(id)
    if (id === activeSession.value) {
      resetDraft()
      historyOpen.value = false
      void nextTick(() => composer.value?.focus())
    }
  } catch (cause) {
    if (id === activeSession.value) showWorkError(cause)
  } finally {
    pendingSessionActions.delete(id)
  }
}
/**
 * Close the history popover when a pointerdown lands outside it.
 * 当指针按在历史弹层之外时关闭该弹层。
 *
 * @param event - Document-level pointerdown event. / 文档级 pointerdown 事件。
 */
function closeMenus(event: PointerEvent) {
  const target = event.target as Node
  if (!composerArea.value?.contains(target)) palette.close()
  if (!historyArea.value?.contains(target)) {
    historyOpen.value = false
    confirmClear.value = false
  }
}
/** Close the history popover and return focus to its trigger. / 关闭历史弹层并将焦点还给触发按钮。 */
function dismissHistory() {
  historyOpen.value = false
  confirmClear.value = false
  historyTrigger.value?.focus()
}
/**
 * Validate and append files chosen by the attachment picker or a drop gesture.
 * 校验并追加附件选择器或拖拽手势选中的文件。
 *
 * @param event - Change event from the hidden file input. / 隐藏文件输入框的 change 事件。
 */
function addFiles(event: Event) {
  const input = event.target as HTMLInputElement
  const selected = Array.from(input.files ?? [])
  input.value = ''
  addFileList(selected)
}

const dragActive = ref(false)
let dragDepth = 0
const SUPPORTED_UPLOAD_EXTENSIONS = new Set([
  '.txt', '.md', '.markdown', '.csv', '.tsv', '.json', '.jsonl', '.yaml', '.yml',
  '.xml', '.log', '.py', '.js', '.ts', '.html', '.css', '.sql', '.toml', '.ini',
  '.docx', '.pdf',
])

/**
 * Validates count, size and extension, then appends supported files.
 *
 * 校验数量、大小与扩展名，然后追加受支持的文件；不支持的项不会静默进入附件列表。
 *
 * @param selected - Files from the picker or a drag-and-drop gesture. / 选择器或拖拽得到的文件。
 */
function addFileList(selected: File[]) {
  if (!selected.length) return
  if (files.value.length + selected.length > 8) {
    workError.value = t('Attach at most eight files.', '每次最多添加 8 个文件。')
    return
  }
  if (selected.some(file => file.size > 32 * 1024 * 1024)) {
    workError.value = t('Each file must be no larger than 32 MiB.', '单个文件不能超过 32 MiB。')
    return
  }
  const unsupported = selected.find(file => {
    const dot = file.name.lastIndexOf('.')
    return dot < 0 || !SUPPORTED_UPLOAD_EXTENSIONS.has(file.name.slice(dot).toLowerCase())
  })
  if (unsupported) {
    workError.value = t(
      `Unsupported file type: ${unsupported.name}. Attach TXT, Markdown, PDF, DOCX, JSON, CSV or similar documents.`,
      `不支持的文件类型：${unsupported.name}。请添加 TXT、Markdown、PDF、DOCX、JSON、CSV 等文档。`,
    )
    return
  }
  workError.value = ''
  files.value.push(...selected)
}

/** Shows the drop overlay while a file drag is inside the conversation. / 拖拽进入对话区域时显示覆盖提示。 */
function onDragEnter(event: DragEvent) {
  if (!event.dataTransfer?.types.includes('Files')) return
  event.preventDefault()
  dragDepth += 1
  dragActive.value = true
}

/** Keeps the browser drop target active and shows a copy cursor. / 保持浏览器放置目标并显示复制光标。 */
function onDragOver(event: DragEvent) {
  if (!event.dataTransfer?.types.includes('Files')) return
  event.preventDefault()
  event.dataTransfer.dropEffect = 'copy'
}

/** Hides the overlay when the pointer leaves the conversation region. / 指针离开对话区域时隐藏覆盖层。 */
function onDragLeave() {
  dragDepth = Math.max(0, dragDepth - 1)
  if (dragDepth === 0) dragActive.value = false
}

/** Accepts dropped files and appends them to the pending attachment list. / 接收拖入文件并加入待发送附件。 */
function onDrop(event: DragEvent) {
  event.preventDefault()
  dragDepth = 0
  dragActive.value = false
  addFileList(Array.from(event.dataTransfer?.files ?? []))
}
/**
 * Send on Enter while keeping Shift+Enter and IME composition for new lines.
 * 回车发送，保留 Shift+Enter 与输入法组合状态用于换行。
 *
 * @param event - Keydown event from the composer textarea. / 输入框的键盘事件。
 */
function groupLabel(group: string) {
  const labels: Record<string, [string, string]> = { run: ['Run', '运行'], context: ['Context', '上下文'], model: ['Model', '模型'], artifact: ['Artifacts', '产物'], capability: ['Capabilities', '能力'], ui: ['Interface', '界面'], help: ['Help', '帮助'], folder: ['Folders', '文件夹'], file: ['Files', '文件'], session: ['Conversations', '会话'], plugin: ['Plugins', '插件'] }
  return labels[group] ? t(...labels[group]) : group
}

async function paletteItems(trigger: ComposerTrigger, signal: AbortSignal): Promise<PaletteItem[]> {
  const scope = { workspace: activeWorkspace.value, thread: session.value?.apiThreadId }
  if (trigger.kind === 'argument') {
    const definition = findCommand(trigger.command ?? '')
    const argumentLabels: Record<string, string> = { chat: t('Chat', '聊天'), work: t('Work', '工作'),
      light: t('Light', '浅色'), dark: t('Dark', '深色'), system: t('System', '跟随系统'),
      en: 'English', zh: '简体中文', md: 'Markdown (.md)', docx: 'Word (.docx)' }
    let choices = (definition?.choices ?? []).map(value => ({ value, title: argumentLabels[value] ?? value, description: value }))
    if (trigger.command === 'model') choices = [{ value: 'auto', title: t('Default model', '默认模型'), description: '' }, ...models.value.map(item => ({ value: item.id, title: item.display_name || item.id, description: item.provider }))]
    if (trigger.command === 'workspace') choices = workspaces.value.map(item => ({ value: item.id, title: item.name, description: item.directory ?? '' }))
    return choices.filter(item => (item.title + ' ' + item.value).toLowerCase().includes(trigger.query.toLowerCase()))
      .map(item => ({ id: 'argument:' + item.value, title: item.title, description: item.description,
        group: '/' + trigger.command, icon: 'command', command: definition!.name, argument: item.value }))
  }
  const categories: ReferenceKind[] = ['folder', 'file', 'session', 'plugin']
  const categoryRows = categories.map(kind => ({ id: 'category:' + kind, title: groupLabel(kind),
    description: t('Add a reference', '添加引用'), group: t('References', '引用'), icon: kind, category: kind }))
  const referenceRow = (item: ReferenceItem): PaletteItem => ({ id: referenceKey(item), title: item.label,
    description: item.sublabel ?? '', group: groupLabel(item.kind), icon: item.kind, reference: item })
  if (trigger.kind === 'reference') {
    if (trigger.referenceKind) return (await searchReferences(trigger.referenceKind, trigger.query, scope, signal)).map(referenceRow)
    const results = await Promise.allSettled(categories.map(kind => searchReferences(kind, trigger.query, scope, signal)))
    if (results.every(item => item.status === 'rejected')) throw new Error('Reference suggestions unavailable')
    const found = results.flatMap(item => item.status === 'fulfilled' ? item.value.slice(0, 2).map(referenceRow) : [])
    return trigger.query ? found.slice(0, 8) : [...categoryRows, ...found.slice(0, 4)]
  }
  const candidates = filterCommands(trigger.query).map(item => ({ id: item.id, title: t(...item.title),
    description: commandAvailable(item, command.state.value) ? t(...item.description) : t('Unavailable in the current state', '当前状态不可用'),
    group: groupLabel(item.group), icon: 'command', command: item.name,
    disabled: !commandAvailable(item, command.state.value) }))
  if (trigger.kind !== 'add') return candidates
  const attach = candidates.find(item => item.command === 'attach')!
  let plugins: PaletteItem[] = []
  try { plugins = (await searchReferences('plugin', '', scope, signal)).map(referenceRow) } catch { /* Commands and file upload remain local. / 命令和文件入口仍可使用。 */ }
  const primary = ['new', 'model', 'mode', 'compact', 'export', 'tools', 'plugins', 'help']
  return [{ ...attach, group: t('Add', '添加'), icon: 'file' },
    ...primary.flatMap(name => candidates.filter(item => item.command === name).map(item => ({ ...item, group: t('Commands', '命令') }))),
    ...categoryRows, ...candidates.filter(item => item.command !== 'attach' && !primary.includes(item.command)), ...plugins]
}

async function commitPalette(item: PaletteItem, trigger: ComposerTrigger) {
  workError.value = ''
  if (item.category) {
    const text = '@' + item.category + ':'
    draft.value = trigger.kind === 'add' ? draft.value + (draft.value && !/\s$/.test(draft.value) ? ' ' : '') + text : replaceToken(draft.value, trigger, text)
    await focusComposer(draft.value.length)
    palette.update()
    return
  }
  if (item.reference) {
    if (selectedReferences.value.length >= 8) { showWorkError(new Error(t('Select at most eight references.', '每条消息最多引用 8 项。'))); return }
    if (!selectedReferences.value.some(entry => referenceKey(entry) === referenceKey(item.reference!))) selectedReferences.value.push(item.reference)
    if (trigger.kind !== 'add') draft.value = replaceToken(draft.value, trigger)
    await focusComposer(Math.min(trigger.start, draft.value.length))
    return
  }
  if (item.command) {
    const previous = draft.value
    if (trigger.kind !== 'add') draft.value = replaceToken(draft.value, trigger)
    if (!await command.run(item.command, item.argument ?? '')) draft.value = previous
    if (!['model', 'attach'].includes(item.command) && !command.confirmation.value) await focusComposer(draft.value.length)
  }
}

async function focusComposer(position = draft.value.length) {
  await nextTick()
  composer.value?.focus()
  composer.value?.setSelectionRange(position, position)
}

function askCommandArguments(name: CommandName) {
  draft.value = '/' + name + ' '
  void focusComposer().then(() => {
    if (name === 'rename') command.notice.value = { title: t('Rename conversation', '重命名会话'), lines: [t('Enter the title, then press Enter.', '输入标题后按 Enter。')] }
    else palette.show({ kind: 'argument', command: name, query: '', start: 0, end: draft.value.length })
  })
}

function stopCurrent() {
  const controller = requestControllers.get(activeSession.value)
  if (!controller) throw new Error(t('No active request in this conversation.', '当前会话没有正在运行的请求。'))
  controller.abort()
}

async function retryCurrent(message: string) {
  const target = session.value
  const original = target?.turns.at(-1)
  if (!target || target.pendingReview || runningSessions.has(target.id)) throw new Error(t('No message to retry, or approval is pending.', '没有可重试消息，或当前仍有待审批操作。'))
  const draftAtStart = draft.value
  if (target.apiThreadId) await syncThreadMessages(target)
  const last = target.turns.find(turn => turn.serverRunId === target.pendingRun?.run_id) ?? target.turns.at(-1) ?? original
  if (!last) throw new Error(t('No saved request to resume.', '没有可恢复的已保存请求。'))
  const clearRestoredDraft = draftAtStart === last.prompt || parseCommand(draftAtStart)?.name === 'retry'
  if (target.pendingRun?.status === 'awaiting_approval') {
    await refreshReview(target)
    throw new Error(t('Review the pending tool actions before continuing.', '请先审查待处理的工具操作。'))
  }
  const unfinished = Boolean(target.pendingRun || last.checkpointId || last.serverError || original?.serverError)
  if (unfinished && message) throw new Error(t('Resume the original request first. Edited text cannot replace a checkpoint.', '请先从检查点继续原请求，编辑后的内容不能替换检查点。'))
  const retryOptions = { memory: settings.personalizedMemory, model: model.value, reasoningEffort: reasoningEffort.value,
    ...(target.mode === 'work' ? { work: { ...work.value } } : {}) }
  if (unfinished && target.apiThreadId && !message) {
    const controller = beginSessionRequest(target.id, last.id)
    const previousError = last.serverError
    const previousRun = last.serverRunId
    let restored = last
    last.serverError = undefined
    try {
      const checkpoint = target.pendingRun?.checkpoint_id ?? last.checkpointId
      const path = threadPath(target.apiThreadId) + '/resume?stream=true' + (checkpoint ? '&checkpoint_id=' + encodeURIComponent(checkpoint) : '')
      const result = await runStream(target.id, path, {}, last, controller.signal, run => {
        restored = target.turns.find(item => item.serverRunId === run) ?? last
        runningTurnIds[target.id] = restored.id
        restored.serverError = undefined
        return restored
      })
      restored = target.turns.find(item => item.serverRunId === result.run_id && item.prompt === result.message)
        ?? target.turns.find(item => item.prompt === result.message && item !== last && item.serverError)
        ?? restored
      if (restored !== last) { last.serverError = previousError; last.serverRunId = previousRun }
      if (result.message != null && restored.prompt !== result.message) {
        restored.prompt = result.message
        restored.files = []
        restored.attachmentIds = []
      }
      restored.serverError = undefined
      applyWorkReply(target, restored, result)
      if (clearRestoredDraft && !disposed && target.id === activeSession.value && draft.value === draftAtStart) resetDraft()
      // The archive supplies the authoritative prompt, attachment names and references.
      // 归档提供权威原文、附件名称与引用，不能用修改后的草稿冒充原始请求。
      if (finishSessionRequest(target.id, controller)) {
        delete liveReplies.value[last.id]; delete liveReasoning.value[last.id]
      }
      await syncThreadMessages(target).catch(error => { if (!disposed && target.id === activeSession.value) showWorkError(error) })
      return
    } catch (error) {
      if (restored !== last) { last.serverError = previousError; last.serverRunId = previousRun }
      restored.serverError = error instanceof Error ? error.message : t('Retry failed.', '重试失败。')
      finishSessionRequest(target.id, controller)
      await refreshReview(target)
      await syncThreadMessages(target).catch(() => {})
      if (target.turns.some(turn => turn.serverRunId === previousRun && turn.serverReply)) return
      if (error instanceof ApiError && error.code === 'NO_PENDING_RUN') {
        throw new Error(t('No recoverable checkpoint exists. Edit and send the request explicitly.', '没有可恢复的检查点，请编辑后明确重新发送请求。'))
      }
      throw error
    } finally {
      if (finishSessionRequest(target.id, controller)) {
        delete liveReplies.value[last.id]; delete liveReasoning.value[last.id]
      }
    }
  }
  const turn: Turn = { id: crypto.randomUUID(), prompt: message || last.prompt, time: '',
    files: [...last.files], attachmentIds: last.attachmentIds, references: last.references,
    ...retryOptions }
  target.turns.push(turn)
  if (!disposed && target.id === activeSession.value) resetDraft()
  await sendToAgent(target, turn, [])
}

async function downloadDocument(artifact: ToolArtifact) {
  try { await downloadArtifact(artifact) } catch (error) { showWorkError(error) }
}

async function confirmCommand() { await command.confirm(); await focusComposer() }
function cancelCommand() { command.confirmation.value = null; void focusComposer() }
function focusReference(index: number) { composerArea.value?.querySelectorAll<HTMLButtonElement>('.composer-references button')[index]?.focus() }

function removeUnresolved() {
  for (const item of unresolvedMentions(draft.value).reverse()) draft.value = draft.value.slice(0, item.start) + draft.value.slice(item.end)
  referenceTextConfirm.value = false
  void focusComposer()
}

function onComposerKey(event: KeyboardEvent) {
  if (palette.keydown(event) || event.isComposing || event.keyCode === 229) return
  if (composer.value?.selectionStart === 0 && composer.value.selectionEnd === 0 && selectedReferences.value.length) {
    if (event.key === 'Backspace') { event.preventDefault(); selectedReferences.value.pop(); return }
    if (event.key === 'ArrowLeft') { event.preventDefault(); focusReference(selectedReferences.value.length - 1); return }
  }
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault()
    submit()
  }
}
</script>

<template>
  <main class="chat-page" :class="{ 'chat-welcome': empty, 'work-mode': mode === 'work' }"
  @dragenter.prevent="onDragEnter" @dragover.prevent="onDragOver" @dragleave.prevent="onDragLeave" @drop.prevent="onDrop">
  <div v-if="dragActive" class="drop-overlay" aria-hidden="true">
    <div>{{ t('Drop files to attach', '松开以添加附件') }}</div>
  </div>
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
            <div class="user-content"><div class="user-bubble"><MarkdownText v-if="turn.prompt" :text="turn.prompt" /><div v-if="turn.references?.length" class="reference-list"><ReferenceChip v-for="item in turn.references" :key="referenceKey(item)" :item="item" /></div><div v-if="turn.files.length" class="message-files"><FileIcon />{{ turn.files.join(', ') }}</div></div><div class="user-message-footer"><CopyMessage v-if="turn.prompt" :text="turn.prompt" /><time>{{ turn.time }}</time></div></div>
            <UserAvatar />
          </div>
          <div v-if="workRunning(turn) || waitingForReview(turn) || turn.serverReply || turn.serverError || turn.activity?.length" class="assistant-message">
            <img :src="brandAsset('mark.png')" class="assistant-icon" alt="">
            <div class="assistant-content">
              <header><strong>slothy</strong><time>{{ turn.time }}</time></header>
              <ReasoningTrace
                :text="turn.reasoning || liveReasoning[turn.id] || ''"
                :streaming="workRunning(turn) && !turn.serverReply && !liveReplies[turn.id]"
              />
              <template v-if="turn.activity?.length || turn.toolDecisions?.length || workRunning(turn) || waitingForReview(turn)">
              <button class="activity-trigger" :aria-expanded="showActivity(turn)" :aria-disabled="workRunning(turn) || waitingForReview(turn)" @click="!workRunning(turn) && !waitingForReview(turn) && toggle(activityOpen, turn.id)">
                <component :is="showActivity(turn) ? ChevronDown : ChevronRight" />
                {{ activityLabel(turn) }}
              </button>
              <div class="activity-collapse" :class="{ 'activity-expanded': showActivity(turn) }" :inert="!showActivity(turn)">
                <div class="activity-inner">
                  <div class="activity-trace">
                    <RunActivityTrace :items="turn.activity ?? []" :metrics="turn.metrics" :interrupted="Boolean(turn.serverError && !workRunning(turn))" />
                    <p v-if="workRunning(turn) && !turn.activity?.length" class="tool-line"><LoaderCircle class="loading-icon" />{{ t('Waiting for the Agent', '等待 Agent 返回') }}</p>
                    <p v-for="decision in turn.activity?.length ? [] : turn.toolDecisions" :key="decision.id" class="tool-line">
                      {{ decision.approved ? t('Approved', '已批准') : t('Rejected', '已拒绝') }} · {{ decision.names.join(', ') }}
                    </p>
                  </div>
                </div>
              </div>
              </template>
              <p v-if="turn.serverError" class="muted" role="alert">{{ turn.serverError }}</p>
              <button v-if="turn.checkpointId && !workRunning(turn) && !pendingReview && turn.serverRunId === session?.pendingRun?.run_id" type="button" class="outline-button checkpoint-resume" :disabled="archived || workBusy || command.busy.value" @click="command.run('retry')">{{ t('Continue from checkpoint', '从检查点继续') }}</button>
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
                <CopyMessage :key="turn.id" :text="turn.serverReply" />
              </footer>
            </div>
          </div>
        </article>
        <section v-if="pendingReview && !archived" ref="reviewPanel" class="tool-review" :class="{ 'tool-review-danger': irreversibleReview }"
          :role="irreversibleReview ? 'alertdialog' : 'region'" :aria-modal="irreversibleReview ? 'false' : undefined" aria-labelledby="tool-review-title" :aria-describedby="irreversibleReview ? 'tool-review-description tool-review-warning' : 'tool-review-description'">
          <h2 id="tool-review-title"><component :is="irreversibleReview ? CircleAlert : ShieldCheck" aria-hidden="true" />{{ irreversibleReview ? t('High-risk action review', '高危操作审查') : t('Review tool actions', '审查工具操作') }}</h2>
          <p id="tool-review-description">{{ t('These actions are paused. Approving runs every action listed below.', '这些操作已暂停。批准后会执行下列所有操作。') }}</p>
          <p v-if="irreversibleReview" id="tool-review-warning" class="tool-review-warning">{{ t('This batch includes irreversible actions. Check the targets and arguments: deleted or replaced content may not be recoverable.', '此批操作包含不可逆操作。请核对目标与参数，删除或覆盖的内容可能无法恢复。') }}</p>
          <ul>
            <li v-for="(call, index) in pendingReview.calls" :key="index">
              <strong>{{ call.name }}</strong>
              <span v-if="callRisk(call)" class="tool-review-risk" :class="{ 'tool-risk-danger': irreversibleCall(call) }"> · {{ riskLabel(callRisk(call), t) }}<strong v-if="irreversibleCall(call)"> · {{ t('High risk · Irreversible', '高危 · 不可逆') }}</strong></span>
              <p v-if="call.review_hint">{{ call.review_hint }}</p>
              <details><summary>{{ t('View arguments', '查看参数') }}</summary><pre>{{ JSON.stringify(call.arguments, null, 2) }}</pre></details>
            </li>
          </ul>
          <div class="tool-review-actions">
            <button type="button" class="outline-button" :disabled="workBusy" @click="resolveReview(false)">{{ t('Reject', '拒绝') }}</button>
            <button type="button" class="accent-button tool-review-approve" :disabled="workBusy" @click="resolveReview(true)">{{ irreversibleReview ? t('Approve high-risk actions', '批准高危操作') : t('Approve and run', '批准并执行') }}</button>
          </div>
        </section>
      </div>

      <div v-if="!archived" ref="composerArea" class="composer-region">
        <div v-if="empty" class="welcome-identity">
          <img class="welcome-mark" :src="brandAsset('mark.png')" alt="">
          <h1>{{ mode === 'work' ? t('What should we work on?', '我们要做什么？') : t('What’s on your mind?', '今天想聊点什么？') }}</h1>
        </div>
        <section v-if="command.confirmation.value" ref="confirmationArea" class="composer-command-card" role="region" :aria-label="command.confirmation.value.title">
          <strong>{{ command.confirmation.value.title }}</strong><p>{{ command.confirmation.value.description }}</p>
          <div><button type="button" class="quiet-button" @click="cancelCommand">{{ t('Cancel', '取消') }}</button><button type="button" class="quiet-button" @click="confirmCommand">{{ t('Confirm', '确认') }}</button></div>
        </section>
        <section v-if="referenceTextConfirm" class="composer-command-card" role="region" :aria-label="t('Unresolved references', '未解析的引用')">
          <p>{{ t('Some @ references have not been selected. Send them as ordinary text?', '有尚未选择的 @ 引用，是否作为普通文本发送？') }}</p>
          <div><button type="button" class="quiet-button" @click="referenceTextConfirm = false">{{ t('Cancel', '取消') }}</button><button type="button" class="quiet-button" @click="removeUnresolved">{{ t('Remove', '移除') }}</button><button type="button" class="quiet-button" @click="referenceTextConfirm = false; submit(true)">{{ t('Send as text', '作为文本发送') }}</button></div>
        </section>
        <section v-if="command.notice.value" class="composer-command-card composer-command-notice" role="status">
          <header><strong>{{ command.notice.value.title }}</strong><button type="button" class="chat-icon" :aria-label="t('Dismiss', '关闭')" @click="command.notice.value = null"><X /></button></header>
          <ul><li v-for="(line, index) in command.notice.value.lines" :key="index">{{ line }}</li></ul>
          <button v-for="artifact in command.notice.value.artifacts" :key="artifact.id" type="button" class="quiet-button" @click="downloadDocument(artifact)"><FileIcon />{{ artifact.name }} · {{ artifact.bytes }} B</button>
        </section>
        <form class="composer" :class="{ 'composer-with-files': files.length || selectedReferences.length }" @submit.prevent="submit">
          <ComposerPalette v-if="palette.trigger.value" :items="palette.items.value" :selected="palette.selected.value" :loading="palette.loading.value" :failed="palette.failed.value" :title="paletteTitle" @choose="palette.choose" @hover="palette.selected.value = $event" />
          <div v-if="selectedReferences.length" class="reference-list composer-references"><ReferenceChip v-for="(item, index) in selectedReferences" :key="referenceKey(item)" :item="item" removable @remove="selectedReferences.splice(index, 1); focusComposer()" @keydown.right.prevent="focusComposer(0)" @keydown.left.prevent="focusReference(Math.max(0, index - 1))" /></div>
          <textarea ref="composer" v-model="draft" rows="1" role="combobox" aria-autocomplete="list" aria-haspopup="listbox"
            :aria-expanded="Boolean(palette.trigger.value)" :aria-controls="palette.trigger.value ? 'composer-palette-list' : undefined"
            :aria-activedescendant="palette.trigger.value && palette.items.value.length && !palette.loading.value ? 'composer-option-' + palette.selected.value : undefined"
            :placeholder="mode === 'work' ? t('Ask slothy to work…', '使用 slothy 工作…') : t('Ask slothy…', '询问 slothy…')" :aria-label="t('Message slothy', '发消息给 slothy')"
            @input="palette.update" @click="palette.update" @blur="palette.close()" @keyup.left="palette.update()" @keyup.right="palette.update()"
            @compositionstart="palette.compositionStart" @compositionend="palette.compositionEnd" @keydown="onComposerKey"></textarea>
          <ul v-if="files.length" class="attachment-list"><li v-for="(file, index) in files" :key="index"><span>{{ file.name }}</span><button type="button" class="quiet-button" :aria-label="t('Remove attachment', '移除附件')" @click="files.splice(index, 1)"><X /></button></li></ul>
          <div class="composer-actions">
            <input ref="fileInput" type="file" multiple hidden @change="addFiles">
            <button class="chat-icon composer-add" type="button" :title="t('Commands, files and references', '命令、文件与引用')" :aria-label="t('Commands, files and references', '命令、文件与引用')" :aria-expanded="palette.trigger.value?.kind === 'add'" aria-controls="composer-palette-list" @pointerdown.prevent @click="palette.toggleAdd"><Plus /></button>
            <label v-if="mode === 'work'" class="composer-select approval-select">
              <ShieldCheck aria-hidden="true" />
              <select v-model="work.approval" :aria-label="t('Approval mode', '审批模式')">
                <option value="ask">{{ t('Ask me to approve', '询问我的批准') }}</option>
                <option value="every">{{ t('Approve each action', '逐项批准') }}</option>
              </select>
            </label>
            <ModelPicker ref="modelPicker" v-model="model" :items="models" :selected="selectedModel"
              :effort="reasoningEffort" @effort="setReasoningEffort" />
            <button v-if="requestControllers.has(activeSession) && runningSessions.has(activeSession)" class="voice-button" type="button" :aria-label="t('Stop run', '停止运行')" @click="stopCurrent"><Square /></button>
            <button v-else class="voice-button" type="submit" :disabled="workBusy || command.busy.value || Boolean(pendingReview) || !canSend" :aria-label="t('Send message', '发送消息')"><Send /></button>
          </div>
        </form>
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

<style scoped>
.reference-list { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.composer-references { width: 100%; margin: 6px 6px 0; }
.composer-command-card { padding: 16px 18px; margin-bottom: 10px; border-radius: 16px; background: var(--neutral); font-size: 13px; }
.composer-command-card p { margin: 7px 0 12px; color: var(--muted); line-height: 1.6; }
.composer-command-card > div, .composer-command-card header { display: flex; align-items: center; justify-content: flex-end; gap: 10px; }
.composer-command-card header { justify-content: space-between; }
.composer-command-notice { max-height: 260px; overflow: auto; }
.composer-command-notice ul { padding-left: 18px; line-height: 1.8; }
.composer-command-card strong { font-weight: 500; }
.drop-overlay { position: absolute; inset: 12px; z-index: 30; display: grid; place-items: center; border: 2px dashed var(--accent); border-radius: var(--radius-surface); background: color-mix(in srgb, var(--surface) 82%, transparent); color: var(--ink); font-size: var(--type-section); pointer-events: none; }
.drop-overlay > div { padding: 10px 18px; border-radius: 999px; background: var(--accent-ink); color: var(--surface); }
</style>
