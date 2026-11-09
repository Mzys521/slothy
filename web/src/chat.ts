/**
 * Client-side conversation store: sessions, turns, workspaces and their server synchronization.
 *
 * 客户端会话存储：会话、轮次、工作区及其与服务端的同步。
 */

import { computed, reactive, ref, watch } from 'vue'
import { readLocal, t, writeLocal } from './preferences'
import { ApiError, threadPath, workRequest, type MemoryTrace, type MemoryReceipt, type RunActivity, type RunMetrics, type ToolReview } from './workApi'
import type { ReferenceItem } from './references/types'

/** Chat surface mode; `work` enables tools and approvals. / 对话界面模式；`work` 会启用工具与审批。 */
export type ChatMode = 'chat' | 'work'

/** Per-turn work options. / 单轮工作选项。 */
export interface WorkOptions {
  approval: string
}

/** One user turn with its server result and UI state. / 一轮用户输入及其服务端结果与界面状态。 */
export interface Turn {
  id: string
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
export const runningSessions = reactive(new Set<string>())
// Live request handles are memory-only and survive page navigation. / 运行句柄只留内存，跨页面保留。
export const runningRequests = new Map<string, AbortController>()
export const runningTurnIds = reactive<Record<string, string>>({})
let deletionVersion = 0
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
  try {
    await workRequest('/workspaces/' + encodeURIComponent(id), 'DELETE')
  } catch (error) {
    // Refresh first so the UI reflects whatever the server kept.
    // 先刷新，让界面反映服务端实际保留的数据。
    await syncThreads().catch(() => {})
    throw error
  }
  deletionVersion++
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
  const version = deletionVersion
  const pages = await Promise.all([fetchThreads(false), fetchThreads(true)])
  // A deletion during the request invalidates this snapshot.
  // 请求期间发生的删除会让本次快照失效。
  if (version !== deletionVersion) return
  const existing = new Map(sessions.value.filter(item => item.apiThreadId).map(item => [item.apiThreadId, item]))
  sessions.value = [
    ...sessions.value.filter(item => isDraft(item) || runningSessions.has(item.id) && !item.apiThreadId),
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
  const runtime = await workRequest<{ storage_version?: number; data_epoch?: string }>('/runtime')
  if (runtime.storage_version === 3 && runtime.data_epoch) {
    const epoch = readLocal('adaptive.dataEpoch', '')
    if (epoch !== runtime.data_epoch) {
      for (const controller of runningRequests.values()) controller.abort()
      runningRequests.clear(); runningSessions.clear()
      sessions.value = []; activeSession.value = ''; activeWorkspace.value = personalWorkspaceId
      writeLocal('adaptive.legacyLocalSessions', [])
      writeLocal('adaptive.workspaces', [])
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
  const messages: ServerMessage[] = []
  let cursor: number | null = 0
  while (cursor !== null) {
    const page: { items: ServerMessage[]; next_cursor: number | null } = await workRequest(
      threadPath(session.apiThreadId) + '/messages?after_id=' + cursor,
    )
    messages.push(...page.items)
    cursor = page.next_cursor
  }
  // The session may have started running while pages were loading.
  // 分页加载期间该会话可能已经进入运行状态。
  if (runningSessions.has(session.id)) return
  const known = new Map(session.turns.map(turn => [turn.serverRunId, turn]))
  const runIds = new Set(messages.map(message => message.run_id))
  session.turns = [
    ...messages.map(message => {
      const turn = known.get(message.run_id)
      return { id: message.run_id, time: '', ...turn, prompt: message.message,
        // Server memory metadata restores the memory flag after a reload.
        // 服务端记忆元数据用于在重载后恢复「使用记忆」标记。
        memory: turn?.memory ?? (message.profile_version != null || message.used_memory_ids?.length ? true : undefined),
        serverRunId: message.run_id, serverReply: message.response, reasoning: message.reasoning ?? turn?.reasoning, activity: message.activity, metrics: message.metrics,
        usedMemoryIds: message.used_memory_ids ?? turn?.usedMemoryIds,
        memoryTrace: message.memory_trace, responseSource: message.response_source,
        references: message.references ?? turn?.references,
        usedReferenceIds: message.used_reference_ids ?? turn?.usedReferenceIds,
        profileVersion: message.profile_version !== undefined ? message.profile_version : turn?.profileVersion,
        attachmentIds: message.attachments.flatMap(file => file.id ? [file.id] : []),
        files: message.attachments.map(file => file.filename) }
    }),
    ...session.turns.filter(turn => !runIds.has(turn.serverRunId ?? '') && (
      turn.serverError || turn.serverRunId && turn.serverRunId === session.pendingReview?.run_id
    )),
  ]
}

/**
 * Deletes a session locally and, when backed by a thread, on the server.
 *
 * 删除会话：本地删除；若绑定服务端线程则同时删除服务端。
 *
 * @param id - Session id. / 会话 ID。
 */
export async function deleteSession(id: string) {
  const session = sessions.value.find(item => item.id === id)
  if (session?.apiThreadId) {
    try {
      await workRequest(threadPath(session.apiThreadId), 'DELETE')
    } catch (error) {
      // An already-missing thread is still a successful local deletion.
      // 线程本就不存在时，本地删除仍然算成功。
      if (!(error instanceof ApiError && error.status === 404 && error.code === 'NOT_FOUND')) throw error
    }
  }
  deletionVersion++
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
  if (session.apiThreadId) await workRequest(threadPath(session.apiThreadId) + '/messages', 'DELETE')
  session.pendingReview = null
  session.draft = false
  session.turns = []
  session.title = ''
}
