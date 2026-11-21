/**
 * Client-side conversation store: sessions, turns, workspaces and their server synchronization.
 *
 * 客户端会话存储：会话、轮次、工作区及其与服务端的同步。
 */

import { computed, ref, watch } from 'vue'
import { readLocal, t, writeLocal } from './preferences'
import { ApiError, threadPath, workRequest, type MemoryTrace, type MemoryReceipt, type PendingRun, type RunActivity, type RunMetrics, type ToolReview } from './workApi'
import type { ReferenceItem } from './references/types'
import { bumpSessionVersion, pendingSessionActions, pendingSessionCommands, runningRequests, runningSessions, runningTurnIds, liveReplies, liveReasoning, sessionVersion } from './sessionRuns'
import { ReadGuard } from './readGuard'
export { runningRequests, runningSessions, runningTurnIds } from './sessionRuns'

/** Chat surface mode; `work` enables tools and approvals. / 对话界面模式；`work` 会启用工具与审批。 */
export type ChatMode = 'chat' | 'work'

/** Per-turn work options. / 单轮工作选项。 */
export interface WorkOptions {
  approval: string
}

/** One user turn with its server result and UI state. / 一轮用户输入及其服务端结果与界面状态。 */
export interface Turn {
  id: string
  /**
   * Stable local request identity sent as `client_request_id`.
   * 作为 `client_request_id` 发送的稳定本地请求标识。
   */
  clientRequestId?: string
  prompt: string
  time: string
  files: string[]
  attachmentIds?: string[]
  memory?: boolean
  model?: string
  reasoningEffort?: string
  work?: WorkOptions
  serverReply?: string
  reasoning?: string
  serverRunId?: string
  checkpointId?: string
  usedMemoryIds?: string[]
  memoryTrace?: MemoryTrace
  responseSource?: string
  savedMemories?: MemoryReceipt[]
  memorySuggestionIds?: string[]
  memoryReceiptError?: string
  serverError?: string
  profileVersion?: number | null
  activityMs?: number
  activity?: RunActivity[]
  metrics?: RunMetrics | null
  toolDecisions?: { id: string; names: string[]; approved: boolean }[]
  references?: ReferenceItem[]
  usedReferenceIds?: string[]
}

/** A conversation draft or server-backed thread. / 会话草稿或由服务端支持的线程。 */
export interface Session {
  id: string
  workspaceId?: string
  createdAt?: number
  pinnedAt?: number
  draft?: boolean
  title: string
  turns: Turn[]
  mode?: ChatMode
  archived?: boolean
  apiThreadId?: string
  pendingReview?: ToolReview | null
  pendingRun?: PendingRun | null
}

export const personalWorkspaceId = 'personal'
type Workspace = { id: string; name: string; directory?: string | null }
const workspacesState = ref<Workspace[]>([])
export const workspaces = computed(() => workspacesState.value)
const savedWorkspace = readLocal('adaptive.activeWorkspace', personalWorkspaceId)
export const activeWorkspace = ref(savedWorkspace)

/**
 * Resolves a workspace display name, falling back to the localized "personal workspace".
 *
 * 解析工作区显示名称，缺失时回退到本地化的「个人工作区」。
 *
 * @param id - Workspace id. / 工作区 ID。
 * @returns Display name for the workspace. / 工作区的显示名称。
 */
export function workspaceName(id: string) {
  return workspaces.value.find(workspace => workspace.id === id)?.name || t('Personal workspace', '个人工作区')
}

/**
 * Returns the filesystem directory bound to a workspace.
 *
 * 返回工作区绑定的文件系统目录。
 *
 * @param id - Workspace id. / 工作区 ID。
 * @returns Directory path, or an empty string when unbound. / 目录路径；未绑定时返回空串。
 */
export function workspaceDirectory(id: string) {
  return workspaces.value.find(workspace => workspace.id === id)?.directory ?? ''
}

/**
 * Returns the workspace a session belongs to.
 *
 * 返回会话所属的工作区。
 *
 * @param session - Session to inspect. / 待检查的会话。
 * @returns Workspace id, defaulting to the personal workspace. / 工作区 ID，默认个人工作区。
 */
export function sessionWorkspace(session: Session) {
  return session.workspaceId ?? personalWorkspaceId
}

const cachedSessions = readLocal<Session[]>('adaptive.sessions', [])
export const sessions = ref(cachedSessions
  .filter(item => item.id !== 'atlas' || item.apiThreadId || item.turns.some(turn => !('sample' in turn)))
  .map(item => ({ ...item, turns: item.turns.filter(turn => !('sample' in turn)) })))
// Move turns that only exist locally (created before server threads) into a legacy bucket.
// 将仅存在于本地的轮次（早于服务端线程）迁移到旧数据桶。
const localOnly = sessions.value.filter(item => !item.apiThreadId && item.turns.length)
if (localOnly.length) {
  const legacy = new Map(readLocal<Session[]>('adaptive.legacyLocalSessions', []).map(item => [item.id, item]))
  for (const item of localOnly) legacy.set(item.id, item)
  writeLocal('adaptive.legacyLocalSessions', [...legacy.values()])
  sessions.value = sessions.value.filter(item => item.apiThreadId || !item.turns.length)
}
writeLocal('adaptive.sessions', sessions.value)
const savedSession = readLocal('adaptive.activeSession', '')
/**
 * Reports whether a session is an unsent draft.
 *
 * 判断会话是否为未发送的草稿。
 *
 * @param session - Session to inspect. / 待检查的会话。
 * @returns `true` for a draft without turns or server id. / 无轮次且无服务端 ID 的草稿返回 `true`。
 */
function isDraft(session: Session) {
  return !session.archived && !session.turns.length && !session.apiThreadId && (session.draft ?? !session.title)
}
const existingDraft = sessions.value.find(session => session.id === savedSession && isDraft(session))
  ?? sessions.value.find(isDraft)
if (existingDraft) {
  sessions.value = sessions.value.filter(session => session.id === existingDraft.id || !isDraft(session))
  writeLocal('adaptive.sessions', sessions.value)
}
export const activeSession = ref(sessions.value.some(session => !session.archived &&
  session.id === savedSession && sessionWorkspace(session) === activeWorkspace.value) ? savedSession : '')
// Bumped by every local index mutation so a thread list read that started earlier cannot
// re-apply its snapshot. / 每次本地索引变更都会递增，使更早开始的线程列表读取无法再套用快照。
let indexGeneration = 0
// Only the newest history read of a session may publish its turns.
// 只有某个会话最新的历史读取可以提交其轮次。
const messageReads = new ReadGuard<string>()
/**
 * Invalidates thread index snapshots that started before a local mutation.
 *
 * 作废本地变更之前开始的线程索引快照。
 */
function invalidateThreadIndex() {
  indexGeneration++
}

/** Durable cleanup accepted by a delete call that must be retried. / 删除调用返回的、需要重试的可持久化清理任务。 */
export interface CleanupOperation {
  operation_id?: string
  id?: string
  kind?: string
  target_id?: string
  threads?: string[]
  steps?: string[]
  state: 'pending' | 'running' | 'failed' | 'done'
  error?: string | null
}

// Bounded polling of a durable cleanup; one interval per read of the operation.
// 可持久化清理的有界轮询；每次读取任务间隔一个周期。
const cleanupPollMs = 400
const cleanupPollAttempts = 15

/**
 * Reports whether a request only failed because a cleanup is still running.
 *
 * 判断请求是否仅因清理尚未完成而失败。
 *
 * @param error - Value thrown by a request. / 请求抛出的值。
 * @returns True for `SESSION_CLEANING` and `WORKSPACE_CLEANING` conflicts. /
 *     会话或工作区清理冲突时返回 true。
 */
export function cleanupConflict(error: unknown) {
  return error instanceof ApiError && (error.code === 'SESSION_CLEANING' || error.code === 'WORKSPACE_CLEANING')
}

/**
 * Bilingual message for a request deferred by an unfinished cleanup.
 *
 * 清理尚未完成时请求被推迟的双语提示。
 *
 * @returns Localized temporary-conflict message. / 本地化的临时冲突提示。
 */
export function cleanupConflictMessage() {
  return t('A cleanup is still running for this conversation or workspace. Try again in a moment.',
    '该会话或工作区正在清理中，请稍后再试。')
}

/**
 * Waits for a durable cleanup operation to finish and never reports an unfinished task as done.
 *
 * 等待可持久化清理任务完成，绝不把未完成任务当作完成。
 *
 * @param operation - Accepted operation (202) or a status payload. / 已接受的任务（202）或状态载荷。
 * @param options - Poll interval and attempt bound, overridable in tests. / 轮询间隔与尝试上限，测试可覆盖。
 * @returns The operation once its state is `done`. / 状态为 `done` 的任务。
 * @throws Error with a bilingual message on failure or timeout. / 失败或超时时抛出双语错误。
 */
export async function waitForCleanup(
  operation: CleanupOperation,
  options: { intervalMs?: number; attempts?: number } = {},
) {
  const id = operation.id ?? operation.operation_id
  if (!id) return operation
  const intervalMs = options.intervalMs ?? cleanupPollMs
  const attempts = options.attempts ?? cleanupPollAttempts
  let current = operation
  for (let attempt = 0; attempt <= attempts; attempt++) {
    if (current.state === 'done') return current
    if (current.state === 'failed') {
      const detail = current.error || t('unknown reason', '未知原因')
      throw new Error(t('Cleanup did not finish: ' + detail, '清理未完成：' + detail))
    }
    if (attempt === attempts) break
    await new Promise(resolve => setTimeout(resolve, intervalMs))
    current = await workRequest<CleanupOperation>('/operations/' + encodeURIComponent(id))
  }
  throw new Error(t('Cleanup is still running. Refresh and try again shortly.',
    '清理仍在进行，请刷新后稍后重试。'))
}

/**
 * Sends one delete and waits for its durable cleanup when it was accepted with a 202.
 *
 * 发送一次删除调用，若以 202 接受则等待其可持久化清理完成。
 *
 * @param path - Delete path below `/api/v1`. / `/api/v1` 之下的删除路径。
 */
async function deleteDurably(path: string) {
  try {
    const accepted = await workRequest<CleanupOperation | undefined>(path, 'DELETE')
    if (accepted && (accepted.operation_id || accepted.id)) await waitForCleanup(accepted)
  } catch (error) {
    // An already-missing resource is cleaned up by definition.
    // 已经不存在的资源按清理完成处理。
    if (error instanceof ApiError && error.status === 404) return
    throw error
  }
}

/**
 * Refreshes the authoritative state after a cleanup could not finish.
 *
 * 清理未能完成时刷新权威状态。
 *
 * @param ids - Local sessions covered by the cleanup. / 该清理覆盖的本地会话。
 */
async function refreshCleanupState(ids: string[]) {
  await syncThreads().catch(() => {})
  for (const id of ids) {
    const current = sessions.value.find(item => item.id === id)
    if (current) await syncThreadMessages(current).catch(() => {})
  }
}

/**
 * Maps a failed cleanup onto a bilingual, temporary-conflict-aware error.
 *
 * 把失败的清理映射为双语、区分临时冲突的错误。
 *
 * @param error - Value thrown by the delete or the poll. / 删除或轮询抛出的值。
 * @returns The error to surface to the user. / 呈现给用户的错误。
 */
function cleanupError(error: unknown) {
  if (cleanupConflict(error)) return new Error(cleanupConflictMessage())
  if (error instanceof Error) return error
  return new Error(t('Cleanup could not be completed. Try again shortly.', '清理无法完成，请稍后重试。'))
}

watch(activeWorkspace, value => writeLocal('adaptive.activeWorkspace', value))
watch(sessions, value => writeLocal('adaptive.sessions', value), { deep: true })
watch(activeSession, value => writeLocal('adaptive.activeSession', value))

/**
 * Switches the active workspace and clears the selected session.
 *
 * 切换活动工作区并清空当前选中的会话。
 *
 * @param id - Target workspace id; ignored when unknown or already active. / 目标工作区 ID；未知或已激活时忽略。
 */
export function selectWorkspace(id: string) {
  if (id === activeWorkspace.value || !workspaces.value.some(workspace => workspace.id === id)) return
  activeWorkspace.value = id
  activeSession.value = ''
}

/**
 * Creates a workspace on the server and selects it.
 *
 * 在服务端创建工作区并选中它。
 *
 * @param name - Display name of the new workspace. / 新工作区的显示名称。
 * @param directory - Filesystem directory the workspace is bound to. / 工作区绑定的文件系统目录。
 */
export async function createWorkspace(name: string, directory: string) {
  const workspace = await workRequest<Workspace>('/workspaces', 'POST', { name, directory })
  workspacesState.value.push(workspace)
  selectWorkspace(workspace.id)
}

/**
 * Renames an existing workspace.
 *
 * 重命名已有工作区。
 *
 * @param id - Workspace id. / 工作区 ID。
 * @param name - New display name. / 新的显示名称。
 */
export async function renameWorkspace(id: string, name: string) {
  const updated = await workRequest<Workspace>('/workspaces/' + encodeURIComponent(id), 'PATCH', { name })
  const workspace = workspacesState.value.find(item => item.id === id)
  if (workspace) workspace.name = updated.name
}

/**
 * Deletes a workspace and drops its local sessions.
 *
 * 删除工作区并移除其本地会话。
 *
 * @param id - Workspace id. / 工作区 ID。
 */
export async function removeWorkspace(id: string) {
  const covered = sessions.value.filter(session => sessionWorkspace(session) === id).map(session => session.id)
  try {
    await deleteDurably('/workspaces/' + encodeURIComponent(id))
  } catch (error) {
    // Refresh first so the UI reflects whatever the server kept.
    // 先刷新，让界面反映服务端实际保留的数据。
    await refreshCleanupState(covered)
    throw cleanupError(error)
  }
  const removed = sessions.value.filter(session => sessionWorkspace(session) === id)
  invalidateThreadIndex()
  for (const session of removed) bumpSessionVersion(session.id)
  sessions.value = sessions.value.filter(session => sessionWorkspace(session) !== id)
  workspacesState.value = workspacesState.value.filter(item => item.id !== id)
  if (activeWorkspace.value === id) selectWorkspace(personalWorkspaceId)
}

/**
 * Selects a session, switching to its workspace.
 *
 * 选中会话，并切换到它所属的工作区。
 *
 * @param id - Session id. / 会话 ID。
 * @returns `true` when selection changed. / 选中状态改变时返回 `true`。
 */
export function selectSession(id: string) {
  const session = sessions.value.find(item => item.id === id)
  if (!session || session.archived) return false
  activeWorkspace.value = sessionWorkspace(session)
  activeSession.value = id
  return true
}

/**
 * Creates or reuses a draft session in the given workspace and selects it.
 *
 * 在指定工作区新建或复用草稿会话并选中。
 *
 * @param mode - Chat or work mode for the new session. / 新会话的模式（chat 或 work）。
 * @param workspaceId - Workspace to place the draft in. / 草稿所属的工作区。
 */
export function newChat(mode: ChatMode = 'chat', workspaceId = activeWorkspace.value) {
  const draft = sessions.value.find(isDraft)
  if (draft) {
    draft.workspaceId = workspaceId
    draft.mode = mode
    draft.createdAt ??= Date.now()
    selectSession(draft.id)
    return
  }
  const id = crypto.randomUUID()
  sessions.value.unshift({ id, workspaceId, createdAt: Date.now(), draft: true, title: '', turns: [], mode })
  selectSession(id)
}

/**
 * Forks a server-backed session into a new local session.
 *
 * 将服务端会话分叉为一个新的本地会话。
 *
 * @param session - Source session; must already have a server thread. / 源会话，必须已有服务端线程。
 * @param title - Title for the fork. / 分叉会话的标题。
 */
export async function forkSession(session: Session, title: string) {
  if (!session.apiThreadId) throw new Error(t('Send a message before forking.', '发送消息后才能分叉会话。'))
  const fork = await workRequest<RemoteThread>(threadPath(session.apiThreadId) + '/fork', 'POST', { title })
  const entry = sessionFromThread(fork)
  invalidateThreadIndex()
  sessions.value.unshift(entry)
  const id = entry.id
  selectSession(id)
}

/**
 * Archives or restores a session locally and on the server.
 *
 * 在本地与服务端归档或恢复会话。
 *
 * @param id - Session id. / 会话 ID。
 * @param archived - Target archived state. / 目标归档状态。
 */
export async function archiveSession(id: string, archived: boolean) {
  const session = sessions.value.find(item => item.id === id)
  if (!session) return
  if (session.apiThreadId) await workRequest(threadPath(session.apiThreadId), 'PATCH', { archived })
  invalidateThreadIndex()
  session.archived = archived
  session.draft = false
  if (archived && activeSession.value === id) activeSession.value = ''
}

type RemoteThread = {
  thread_id: string
  title: string
  mode: ChatMode
  archived: boolean
  created_at: string
  workspace_id?: string
  pinned?: boolean
}

/**
 * Merges a server thread into an existing session or builds a new one.
 *
 * 将服务端线程合并进已有会话，或新建会话。
 *
 * @param thread - Thread payload from the API. / API 返回的线程载荷。
 * @param existing - Session to update in place when present. / 存在时原地更新的会话。
 * @returns Updated or newly created session. / 更新后或新建的会话。
 */
function sessionFromThread(thread: RemoteThread, existing?: Session): Session {
  const fields = {
    workspaceId: thread.workspace_id ?? personalWorkspaceId,
    title: thread.title, mode: thread.mode, archived: thread.archived,
    createdAt: Date.parse(thread.created_at) || undefined,
    pinnedAt: thread.pinned ? existing?.pinnedAt ?? Date.now() : undefined,
  }
  if (existing) {
    Object.assign(existing, fields)
    return existing
  }
  return { ...fields, id: thread.thread_id, apiThreadId: thread.thread_id, draft: false, turns: [] }
}

/**
 * Pages through all threads of one archived state.
 *
 * 分页拉取某一归档状态下的全部线程。
 *
 * @param archived - Whether to fetch archived or active threads. / 拉取归档线程还是活动线程。
 * @returns All threads in that state. / 该状态下的全部线程。
 */
async function fetchThreads(archived: boolean) {
  const threads: RemoteThread[] = []
  let offset: number | null = 0
  while (offset !== null) {
    const page: { items: RemoteThread[]; next_offset: number | null } = await workRequest(
      '/threads?archived=' + archived + '&limit=100&offset=' + offset,
    )
    threads.push(...page.items)
    offset = page.next_offset
  }
  return threads
}

/**
 * Replaces the local session index with the server thread list, keeping drafts and running sessions.
 *
 * 用服务端线程列表替换本地会话索引，同时保留草稿与运行中的会话。
 */
export async function syncThreads() {
  const generation = indexGeneration
  const knownRemoteIds = new Set(sessions.value.flatMap(item => item.apiThreadId ? [item.apiThreadId] : []))
  const pages = await Promise.all([fetchThreads(false), fetchThreads(true)])
  // A local mutation during the request invalidates this snapshot.
  // 请求期间发生的本地变更会让本次快照失效。
  if (generation !== indexGeneration) return
  const existing = new Map(sessions.value.filter(item => item.apiThreadId).map(item => [item.apiThreadId, item]))
  const remoteIds = new Set(pages.flat().map(thread => thread.thread_id))
  sessions.value = [
    ...sessions.value.filter(item => isDraft(item) ||
      (runningSessions.has(item.id) || pendingSessionActions.has(item.id) || pendingSessionCommands.has(item.id) ||
        Boolean(item.apiThreadId && !knownRemoteIds.has(item.apiThreadId))) &&
      (!item.apiThreadId || !remoteIds.has(item.apiThreadId))),
    ...pages.flat().map(thread => sessionFromThread(thread, existing.get(thread.thread_id))),
  ]
  if (!sessions.value.some(item => item.id === activeSession.value && !item.archived)) activeSession.value = ''
}

/**
 * Migrates legacy local workspace data, then loads workspaces and threads from the server.
 *
 * 先迁移旧的本地工作区数据，再从服务端加载工作区与线程。
 */
export async function syncWorkspaceData() {
  // The runtime call also proves the server is readable, so a failed request never
  // invalidates the local cache. / 该请求同时证明服务端可读，失败时不会让本地缓存失效。
  const runtime = await workRequest<{ storage_version?: number; data_epoch?: string }>('/runtime')
  if (runtime.storage_version === 3 && runtime.data_epoch) {
    const epoch = readLocal('adaptive.dataEpoch', '')
    if (epoch !== runtime.data_epoch) {
      // Confirm the authoritative data is readable before acting on the new epoch.
      // 在按新纪元行动前先确认权威数据确实可读。
      await fetchThreads(false)
      for (const controller of runningRequests.values()) controller.abort()
      runningRequests.clear(); runningSessions.clear()
      pendingSessionActions.clear()
      pendingSessionCommands.clear()
      for (const key of Object.keys(runningTurnIds)) delete runningTurnIds[key]
      liveReplies.value = {}; liveReasoning.value = {}
      invalidateThreadIndex()
      for (const session of sessions.value) if (session.apiThreadId) bumpSessionVersion(session.id)
      // Keep drafts and every request that was never committed to the server; only cached
      // server sessions are reloaded from the new epoch below.
      // 保留草稿与所有未提交到服务端的请求；仅重新加载新纪元下的服务端会话缓存。
      sessions.value = sessions.value.filter(item => !item.apiThreadId)
      if (!sessions.value.some(item => item.id === activeSession.value)) activeSession.value = ''
      writeLocal('adaptive.dataEpoch', runtime.data_epoch)
    }
    writeLocal('adaptive.workspacesMigrated', true)
  }
  const list: Workspace[] = []
  let offset: number | null = 0
  while (offset !== null) {
    const page: { items: Workspace[]; next_offset: number | null } = await workRequest('/workspaces?offset=' + offset)
    list.push(...page.items)
    offset = page.next_offset
  }
  workspacesState.value = list
  await syncThreads()
  if (!list.some(item => item.id === activeWorkspace.value)) selectWorkspace(personalWorkspaceId)
}

/**
 * Refreshes the archived session list.
 *
 * 刷新归档会话列表。
 */
export async function syncArchivedSessions() {
  await syncThreads()
}

/** One assistant turn as returned by `GET /threads/{id}/messages`. / `GET /threads/{id}/messages` 返回的单轮助手消息。 */
export type ServerMessage = {
  run_id: string
  message: string
  response: string
  reasoning?: string
  attachments: { id?: string; filename: string }[]
  activity?: RunActivity[]
  metrics?: RunMetrics | null
  used_memory_ids?: string[]
  memory_trace?: MemoryTrace
  response_source?: string
  profile_version?: number | null
  references?: ReferenceItem[]
  used_reference_ids?: string[]
  /** Stable request identity when the server projects it. / 服务端投影时提供的稳定请求标识。 */
  client_request_id?: string | null
}

/**
 * Finds the turn a server record belongs to, matching only stable identities.
 *
 * 仅按稳定标识查找服务端记录所属的轮次。
 *
 * @param turns - Local turns to search. / 待搜索的本地轮次。
 * @param runId - Run identity of the server record. / 服务端记录中的运行标识。
 * @param clientRequestId - Request identity when the server projects it. / 服务端投影时提供的请求标识。
 * @returns The owning turn, or undefined when no identity matches. / 所属轮次；没有标识匹配时为 undefined。
 */
function turnIdentity(turns: Turn[], runId: string | null | undefined, clientRequestId?: string | null) {
  const byRun = runId ? turns.find(turn => turn.serverRunId === runId) : undefined
  if (byRun) return byRun
  return clientRequestId ? turns.find(turn => turn.clientRequestId === clientRequestId) : undefined
}

/**
 * Reports whether a local turn must survive a history merge without a server message.
 *
 * 判断本地轮次在没有对应服务端消息时是否仍需保留。
 *
 * @param turn - Local turn under review. / 待检查的本地轮次。
 * @param session - Session that owns the turn. / 轮次所属会话。
 * @param pending - Latest pending run projection, when known. / 已知的最新待处理运行投影。
 * @returns True for failed, unsent or still-pending turns. / 失败、未发送或仍待处理的轮次返回 true。
 */
function keepsLocalTurn(turn: Turn, session: Session, pending?: PendingRun | null) {
  if (turn.serverError) return true
  // A request that never reached the archive keeps its identity for a resend.
  // 未进入归档的请求保留其标识，以便重新发送。
  if (turn.clientRequestId && !turn.serverReply) return true
  return Boolean(turn.serverRunId &&
    (turn.serverRunId === session.pendingReview?.run_id || turn.serverRunId === pending?.run_id))
}

/**
 * Re-hydrates a session's turns from the authoritative server history.
 *
 * 用服务端权威历史重新填充会话轮次。
 *
 * @param session - Session to refresh; skipped while it is running. / 待刷新的会话；运行中时跳过。
 */
export async function syncThreadMessages(session: Session) {
  if (!session.apiThreadId || runningSessions.has(session.id)) return
  const thread = session.apiThreadId
  // The read owns this session only while it stays the newest read and its data version holds.
  // 该读取只在其仍是该会话最新读取且数据版本未变时拥有该会话。
  const version = sessionVersion(session.id)
  const token = messageReads.begin(session.id)
  const messages: ServerMessage[] = []
  let pending: PendingRun | null | undefined
  let cursor: number | null = 0
  while (cursor !== null) {
    const page: { items: ServerMessage[]; next_cursor: number | null; pending_run?: PendingRun | null } = await workRequest(
      threadPath(thread) + '/messages?after_id=' + cursor,
    )
    messages.push(...page.items)
    if (page.pending_run !== undefined) pending = page.pending_run
    cursor = page.next_cursor
  }
  // A run started or completed, a clear happened, or a newer read arrived while pages were loading.
  // 分页加载期间可能有运行开始或完成、会话被清空，或出现了更新的读取。
  if (runningSessions.has(session.id) || version !== sessionVersion(session.id) ||
    !messageReads.owns(token, session.id)) return
  if (pending !== undefined) session.pendingRun = pending
  const runIds = new Set(messages.map(message => message.run_id))
  // A local turn consumed by an archived message is replaced by its merged copy, even when
  // only the request identity (not the run id) was known before this read.
  // 已被归档消息认领的本地轮次由合并副本取代；即使此前只知请求标识而不知运行标识。
  const matched = new Set<Turn>()
  session.turns = [
    ...messages.map(message => {
      const turn = turnIdentity(session.turns, message.run_id, message.client_request_id)
      if (turn) matched.add(turn)
      return { id: message.run_id, time: '', ...turn, prompt: message.message,
        clientRequestId: turn?.clientRequestId ?? message.client_request_id ?? undefined,
        // Server memory metadata restores the memory flag after a reload.
        // 服务端记忆元数据用于在重载后恢复「使用记忆」标记。
        memory: turn?.memory ?? (message.profile_version != null || message.used_memory_ids?.length ? true : undefined),
        serverRunId: message.run_id, serverReply: message.response, serverError: undefined, checkpointId: undefined,
        reasoning: message.reasoning ?? turn?.reasoning, activity: message.activity, metrics: message.metrics,
        usedMemoryIds: message.used_memory_ids ?? turn?.usedMemoryIds,
        memoryTrace: message.memory_trace, responseSource: message.response_source,
        references: message.references ?? turn?.references,
        usedReferenceIds: message.used_reference_ids ?? turn?.usedReferenceIds,
        profileVersion: message.profile_version !== undefined ? message.profile_version : turn?.profileVersion,
        attachmentIds: message.attachments.flatMap(file => file.id ? [file.id] : []),
        files: message.attachments.map(file => file.filename) }
    }),
    ...session.turns.filter(turn => !matched.has(turn) && !runIds.has(turn.serverRunId ?? '') &&
      keepsLocalTurn(turn, session, pending)),
  ]
  if (pending) {
    let turn = turnIdentity(session.turns, pending.run_id, pending.client_request_id)
    if (!turn) { turn = { id: pending.run_id, prompt: pending.message, time: '', files: [] }; session.turns.push(turn) }
    Object.assign(turn, { prompt: pending.message, serverRunId: pending.run_id, checkpointId: pending.checkpoint_id,
      clientRequestId: turn.clientRequestId ?? pending.client_request_id ?? undefined,
      serverReply: undefined, model: pending.model_selection.id, reasoningEffort: pending.model_selection.effort,
      memory: pending.personalization_enabled, files: pending.attachments.map(file => file.filename),
      attachmentIds: pending.attachments.map(file => file.id), references: pending.references.map(item => ({ ...item, label: item.label ?? item.path ?? item.id })),
      activity: pending.activity, metrics: pending.metrics,
      serverError: pending.status === 'awaiting_approval' ? undefined : turn.serverError
        || t('This run is unfinished. Continue from the saved checkpoint.', '该任务尚未完成，可从已保存的检查点继续。') })
  } else if (pending === null) {
    for (const turn of session.turns) turn.checkpointId = undefined
  }
}

/** How a retry must continue one turn. / 重试某个轮次时应采取的方式。 */
export type RetryPlan =
  | { action: 'resume'; runId: string; checkpointId?: string }
  | { action: 'resend'; clientRequestId: string }
  | { action: 'new' }
  | { action: 'blocked'; reason: 'no_request' | 'awaiting_approval' }

/**
 * Decides how one turn may be retried from the saved run identity.
 *
 * 依据已保存的运行标识决定某个轮次如何重试。
 *
 * Identity is never inferred from matching text: a resume is allowed only when the
 * session's pending run is this turn's own run id. A completed turn with a healthy
 * reply is a new logical request, and anything else is resent with the same identity.
 * 绝不按文本匹配推断身份：只有本会话的待处理运行就是该轮次自己的运行标识时才恢复。
 * 已完成且回复正常的轮次属于新的逻辑请求，其余情况都复用同一标识重新发送。
 *
 * @param session - Session that owns the turn. / 轮次所属会话。
 * @param turn - Turn to retry. / 待重试的轮次。
 * @returns The retry action. / 重试动作。
 */
export function retryPlan(session: Session, turn: Turn | undefined): RetryPlan {
  if (!turn) return { action: 'blocked', reason: 'no_request' }
  const pending = session.pendingRun
  if (pending?.status === 'awaiting_approval') return { action: 'blocked', reason: 'awaiting_approval' }
  if (pending && turn.serverRunId && pending.run_id === turn.serverRunId) {
    return { action: 'resume', runId: pending.run_id, ...(pending.checkpoint_id ? { checkpointId: pending.checkpoint_id } : {}) }
  }
  if (turn.serverReply && !turn.serverError) return { action: 'new' }
  return { action: 'resend', clientRequestId: turn.clientRequestId ?? turn.id }
}

/**
 * Builds the stable upload identity of one file of a turn.
 *
 * 构造某一轮次中某个文件的稳定上传标识。
 *
 * @param turn - Turn that owns the upload. / 拥有该上传的轮次。
 * @param file - Browser file selected for the turn. / 该轮次选中的浏览器文件。
 * @param index - Position of the file inside the turn. / 文件在该轮次中的位置。
 * @returns Stable id within the 1–128 character request limit. / 符合 1–128 字符接口限制的稳定标识。
 */
export function fileRequestId(turn: Turn, file: File, index: number) {
  const owner = (turn.clientRequestId ?? turn.id).slice(0, 64)
  const descriptor = [owner, index, file.name, file.size, file.lastModified].join('|')
  let hash = 0x811c9dc5
  for (let position = 0; position < descriptor.length; position++) {
    hash ^= descriptor.charCodeAt(position)
    hash = Math.imul(hash, 0x01000193) >>> 0
  }
  return owner + '-' + index + '-' + hash.toString(36)
}

/**
 * Deletes a session locally and, when backed by a thread, on the server.
 *
 * 删除会话：本地删除；若绑定服务端线程则同时删除服务端。
 *
 * The local session survives until the durable cleanup reports `done`; a failed or
 * interrupted cleanup refreshes the authoritative state and surfaces its error.
 * 本地会话会保留到可持久化清理报告 `done`；清理失败或中断时刷新权威状态并显示错误。
 *
 * @param id - Session id. / 会话 ID。
 */
export async function deleteSession(id: string) {
  const session = sessions.value.find(item => item.id === id)
  // Invalidate reads of this session before the server call covers the whole window.
  // 在服务端调用前作废该会话的读取，覆盖整个删除窗口。
  bumpSessionVersion(id)
  try {
    if (session?.apiThreadId) await deleteDurably(threadPath(session.apiThreadId))
  } catch (error) {
    await refreshCleanupState([id])
    throw cleanupError(error)
  }
  bumpSessionVersion(id)
  invalidateThreadIndex()
  sessions.value = sessions.value.filter(item => item.id !== id)
  if (activeSession.value === id) activeSession.value = ''
}

/**
 * Clears every message of a session while keeping the session itself.
 *
 * 清空会话的全部消息，但保留会话本身。
 *
 * @param id - Session id. / 会话 ID。
 */
export async function clearSession(id: string) {
  const session = sessions.value.find(item => item.id === id)
  if (!session) return
  // A history read that started before the clear must never refill the turns afterwards.
  // 清空之前开始的历史读取绝不能在之后重新填回轮次。
  bumpSessionVersion(id)
  try {
    if (session.apiThreadId) await deleteDurably(threadPath(session.apiThreadId) + '/messages')
  } catch (error) {
    // Keep the turns and pending run visible when the durable cleanup is unfinished.
    // 可持久化清理未完成时保留轮次与待处理运行。
    await refreshCleanupState([id])
    throw cleanupError(error)
  }
  bumpSessionVersion(id)
  invalidateThreadIndex()
  session.pendingReview = null
  session.pendingRun = null
  session.draft = false
  session.turns = []
  session.title = ''
}
