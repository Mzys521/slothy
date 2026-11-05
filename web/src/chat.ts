import { computed, reactive, ref, watch } from 'vue'
import { readLocal, t, writeLocal } from './preferences'
import { ApiError, threadPath, workRequest, type MemoryReceipt, type RunActivity, type RunMetrics, type ToolReview } from './workApi'

export type ChatMode = 'chat' | 'work'

export interface WorkOptions {
  approval: string
}

export interface Turn {
  id: string
  prompt: string
  time: string
  files: string[]
  memory?: boolean
  model?: string
  reasoningEffort?: string
  work?: WorkOptions
  serverReply?: string
  serverRunId?: string
  usedMemoryIds?: string[]
  savedMemories?: MemoryReceipt[]
  memorySuggestionIds?: string[]
  memoryReceiptError?: string
  serverError?: string
  profileVersion?: number | null
  activityMs?: number
  activity?: RunActivity[]
  metrics?: RunMetrics | null
  toolDecisions?: { id: string; names: string[]; approved: boolean }[]
}

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

export function workspaceName(id: string) {
  return workspaces.value.find(workspace => workspace.id === id)?.name || t('Personal workspace', '个人工作区')
}

export function workspaceDirectory(id: string) {
  return workspaces.value.find(workspace => workspace.id === id)?.directory ?? ''
}

export function sessionWorkspace(session: Session) {
  return session.workspaceId ?? personalWorkspaceId
}

const cachedSessions = readLocal<Session[]>('adaptive.sessions', [])
export const sessions = ref(cachedSessions
  .filter(item => item.id !== 'atlas' || item.apiThreadId || item.turns.some(turn => !('sample' in turn)))
  .map(item => ({ ...item, turns: item.turns.filter(turn => !('sample' in turn)) })))
const localOnly = sessions.value.filter(item => !item.apiThreadId && item.turns.length)
if (localOnly.length) {
  const legacy = new Map(readLocal<Session[]>('adaptive.legacyLocalSessions', []).map(item => [item.id, item]))
  for (const item of localOnly) legacy.set(item.id, item)
  writeLocal('adaptive.legacyLocalSessions', [...legacy.values()])
  sessions.value = sessions.value.filter(item => item.apiThreadId || !item.turns.length)
}
writeLocal('adaptive.sessions', sessions.value)
const savedSession = readLocal('adaptive.activeSession', '')
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
let deletionVersion = 0
watch(activeWorkspace, value => writeLocal('adaptive.activeWorkspace', value))
watch(sessions, value => writeLocal('adaptive.sessions', value), { deep: true })
watch(activeSession, value => writeLocal('adaptive.activeSession', value))

export function selectWorkspace(id: string) {
  if (id === activeWorkspace.value || !workspaces.value.some(workspace => workspace.id === id)) return
  activeWorkspace.value = id
  activeSession.value = ''
}

export async function createWorkspace(name: string, directory: string) {
  const workspace = await workRequest<Workspace>('/workspaces', 'POST', { name, directory })
  workspacesState.value.push(workspace)
  selectWorkspace(workspace.id)
}

export async function renameWorkspace(id: string, name: string) {
  const updated = await workRequest<Workspace>('/workspaces/' + encodeURIComponent(id), 'PATCH', { name })
  const workspace = workspacesState.value.find(item => item.id === id)
  if (workspace) workspace.name = updated.name
}

export async function removeWorkspace(id: string) {
  try {
    await workRequest('/workspaces/' + encodeURIComponent(id), 'DELETE')
  } catch (error) {
    await syncThreads().catch(() => {})
    throw error
  }
  deletionVersion++
  sessions.value = sessions.value.filter(session => sessionWorkspace(session) !== id)
  workspacesState.value = workspacesState.value.filter(item => item.id !== id)
  if (activeWorkspace.value === id) selectWorkspace(personalWorkspaceId)
}

export function selectSession(id: string) {
  const session = sessions.value.find(item => item.id === id)
  if (!session || session.archived) return false
  activeWorkspace.value = sessionWorkspace(session)
  activeSession.value = id
  return true
}

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

export async function forkSession(session: Session, title: string) {
  if (!session.apiThreadId) throw new Error(t('Send a message before forking.', '发送消息后才能分叉会话。'))
  const fork = await workRequest<RemoteThread>(threadPath(session.apiThreadId) + '/fork', 'POST', { title })
  const entry = sessionFromThread(fork)
  sessions.value.unshift(entry)
  const id = entry.id
  selectSession(id)
}

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

export async function syncThreads() {
  const version = deletionVersion
  const pages = await Promise.all([fetchThreads(false), fetchThreads(true)])
  if (version !== deletionVersion) return
  const existing = new Map(sessions.value.filter(item => item.apiThreadId).map(item => [item.apiThreadId, item]))
  sessions.value = [
    ...sessions.value.filter(item => isDraft(item) || runningSessions.has(item.id) && !item.apiThreadId),
    ...pages.flat().map(thread => sessionFromThread(thread, existing.get(thread.thread_id))),
  ]
  if (!sessions.value.some(item => item.id === activeSession.value && !item.archived)) activeSession.value = ''
}

export async function syncWorkspaceData() {
  // Persist real workspaces created in earlier versions before replacing the browser index.
  if (!readLocal('adaptive.workspacesMigrated', false)) {
    const legacy = readLocal<Workspace[]>('adaptive.workspaces', [])
    const personalName = readLocal('adaptive.personalWorkspaceName', '')
    if (personalName) await workRequest('/workspaces/personal', 'PATCH', { name: personalName })
    for (const entry of legacy) await workRequest('/workspaces', 'POST', entry)
    for (const item of sessions.value) {
      if (item.apiThreadId && legacy.some(entry => entry.id === item.workspaceId)) {
        try {
          const thread = await workRequest<RemoteThread>(threadPath(item.apiThreadId))
          if (!thread.workspace_id) {
            await workRequest(threadPath(item.apiThreadId), 'PATCH', { workspace_id: item.workspaceId })
          }
        } catch (error) {
          if (!(error instanceof ApiError && error.status === 404)) throw error
        }
      }
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

export async function syncArchivedSessions() {
  await syncThreads()
}

export type ServerMessage = {
  run_id: string
  message: string
  response: string
  attachments: { filename: string }[]
  activity?: RunActivity[]
  metrics?: RunMetrics | null
  used_memory_ids?: string[]
  profile_version?: number | null
}

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
  if (runningSessions.has(session.id)) return
  const known = new Map(session.turns.map(turn => [turn.serverRunId, turn]))
  const runIds = new Set(messages.map(message => message.run_id))
  session.turns = [
    ...messages.map(message => {
      const turn = known.get(message.run_id)
      return { id: message.run_id, time: '', ...turn, prompt: message.message,
        memory: turn?.memory ?? (message.profile_version != null || message.used_memory_ids?.length ? true : undefined),
        serverRunId: message.run_id, serverReply: message.response, activity: message.activity, metrics: message.metrics,
        usedMemoryIds: message.used_memory_ids ?? turn?.usedMemoryIds,
        profileVersion: message.profile_version !== undefined ? message.profile_version : turn?.profileVersion,
        files: message.attachments.map(file => file.filename) }
    }),
    ...session.turns.filter(turn => !runIds.has(turn.serverRunId ?? '') && (
      turn.serverError || turn.serverRunId && turn.serverRunId === session.pendingReview?.run_id
    )),
  ]
}

export async function deleteSession(id: string) {
  const session = sessions.value.find(item => item.id === id)
  if (session?.apiThreadId) {
    try {
      await workRequest(threadPath(session.apiThreadId), 'DELETE')
    } catch (error) {
      if (!(error instanceof ApiError && error.status === 404 && error.code === 'NOT_FOUND')) throw error
    }
  }
  deletionVersion++
  sessions.value = sessions.value.filter(item => item.id !== id)
  if (activeSession.value === id) activeSession.value = ''
}

export async function clearSession(id: string) {
  const session = sessions.value.find(item => item.id === id)
  if (!session) return
  if (session.apiThreadId) await workRequest(threadPath(session.apiThreadId) + '/messages', 'DELETE')
  session.pendingReview = null
  session.draft = false
  session.turns = []
  session.title = ''
}
