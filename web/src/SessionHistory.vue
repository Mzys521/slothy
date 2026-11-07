<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Archive, ArchiveRestore, BriefcaseBusiness, MessageSquare, Trash2 } from 'lucide-vue-next'
import { activeWorkspace, archiveSession, deleteSession, runningSessions, sessions, sessionWorkspace, syncArchivedSessions, workspaceName, workspaces, type Session, type Turn } from './chat'
import { t } from './preferences'
import { threadPath, workRequest } from './workApi'
import MarkdownText from './MarkdownText.vue'

const props = defineProps<{ archived?: boolean }>()
const emit = defineEmits<{ open: [id: string] }>()
const pending = ref<Session>()
const busy = ref(false)
const error = ref('')
const archiveLoading = ref(false)
const archiveLoadError = ref('')
type HistoryTurn = { message: string; response?: string; run_id?: string; attachments?: { filename: string }[] }
const preview = ref<{ session: Session; items: HistoryTurn[]; cursor?: number; loading: boolean; error?: string }>()
/** Sessions shown for this view: archived groups by workspace, or the active workspace's live sessions. / 当前视图的会话：归档视图按工作区分组，否则显示当前工作区的会话。 */
const groups = computed(() => {
  const entries = sessions.value.filter(session => props.archived
    ? session.archived
    : !session.archived && sessionWorkspace(session) === activeWorkspace.value && Boolean(session.turns.length || session.title))
  return props.archived ? workspaces.value.map(workspace => ({
    id: workspace.id,
    items: entries.filter(session => sessionWorkspace(session) === workspace.id),
  })).filter(group => group.items.length) : entries.length ? [{ id: activeWorkspace.value, items: entries }] : []
})

/** Fetches archived sessions from the server. / 从服务端获取归档会话。 */
async function loadArchive() {
  archiveLoading.value = true
  archiveLoadError.value = ''
  try {
    await syncArchivedSessions()
  } catch (cause) {
    archiveLoadError.value = cause instanceof Error ? cause.message : t('Could not load archived sessions.', '无法读取归档会话。')
  } finally {
    archiveLoading.value = false
  }
}

// Archived views must first sync archived sessions from the server. / 归档视图需先从服务端同步归档会话。
onMounted(() => { if (props.archived) void loadArchive() })

/**
 * Requests one page of messages for a thread.
 *
 * 请求某个会话的一页消息。
 *
 * @param threadId - API thread id. / API 会话 ID。
 * @param cursor - Message id to continue after; defaults to 0. / 从该消息 ID 之后继续，默认为 0。
 * @returns Message page with items and the next cursor. / 包含消息项与下一页游标的消息页。
 */
function messagePage(threadId: string, cursor = 0) {
  return workRequest<{ items: HistoryTurn[]; next_cursor: number | null }>(
    threadPath(threadId) + '/messages?after_id=' + cursor,
  )
}

/**
 * Restores an archived session, rebuilding its turn history when only the server copy remains.
 *
 * 恢复归档会话；若只剩服务端副本则重建其对话历史。
 *
 * @param session - Archived session to restore. / 要恢复的归档会话。
 * @returns Resolves when the session is restored and its turns are available. / 会话恢复且轮次可用后结束。
 */
async function restore(session: Session) {
  if (!session.apiThreadId || session.turns.length) {
    await archiveSession(session.id, false)
    return
  }
  const history: Turn[] = []
  let cursor: number | null = 0
  while (cursor !== null) {
    const page: Awaited<ReturnType<typeof messagePage>> = await messagePage(session.apiThreadId, cursor)
    history.push(...page.items.map(turn => ({
      id: turn.run_id ?? crypto.randomUUID(), prompt: turn.message, serverReply: turn.response,
      serverRunId: turn.run_id, time: '', files: (turn.attachments ?? []).map(file => file.filename), memory: false,
    })))
    cursor = page.next_cursor
  }
  await archiveSession(session.id, false)
  session.turns = history
}

/**
 * Opens a live session or toggles an archived-session preview.
 *
 * 打开活动会话，或切换归档会话的预览。
 *
 * @param session - Session clicked in the history list. / 历史列表中点击的会话。
 * @returns Nothing. / 无返回值。
 */
function open(session: Session) {
  if (!props.archived) {
    emit('open', session.id)
    return
  }
  if (preview.value?.session.id === session.id) {
    preview.value = undefined
    return
  }
  preview.value = {
    session,
    items: session.apiThreadId ? [] : session.turns.map(turn => ({
      message: turn.prompt,
      response: turn.serverReply,
      attachments: turn.files.map(filename => ({ filename })),
    })),
    loading: false,
  }
  if (session.apiThreadId) void loadPreview()
}

/** Loads the next page of the archived-session preview. / 加载归档会话预览的下一页。 */
async function loadPreview() {
  const current = preview.value
  if (!current?.session.apiThreadId || current.loading) return
  current.loading = true
  current.error = undefined
  try {
    const page = await messagePage(current.session.apiThreadId, current.cursor)
    current.items.push(...page.items)
    current.cursor = page.next_cursor ?? undefined
  } catch (cause) {
    current.error = cause instanceof Error ? cause.message : t('Could not load conversation.', '无法读取对话。')
  } finally {
    current.loading = false
  }
}

/**
 * Runs a session mutation with busy/error handling and clears a stale preview.
 *
 * 以忙碌/错误处理执行会话修改，并清除已失效的预览。
 *
 * @param action - Async mutation to execute. / 要执行的异步修改操作。
 * @returns Resolves when the mutation and cleanup complete. / 修改与清理完成后结束。
 */
async function change(action: () => Promise<void>) {
  if (busy.value) return
  error.value = ''
  busy.value = true
  try {
    await action()
    pending.value = undefined
    if (preview.value && !groups.value.some(group => group.items.some(item => item.id === preview.value?.session.id))) {
      preview.value = undefined
    }
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : t('Session update failed.', '会话更新失败。')
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="session-history">
    <p v-if="archiveLoading" class="history-empty" role="status">{{ t('Loading archives…', '正在读取归档…') }}</p>
    <div v-if="archiveLoadError" class="history-empty">
      <p role="alert">{{ archiveLoadError }}</p>
      <button type="button" class="text-link" @click="loadArchive">{{ t('Retry', '重试') }}</button>
    </div>
    <p v-if="!groups.length && !archiveLoading && !archiveLoadError" class="history-empty">{{ archived ? t('No archived sessions.', '暂无归档会话。') : t('No sessions yet.', '暂无历史会话。') }}</p>
    <section v-for="group in groups" :key="group.id" class="history-group">
      <h2 v-if="archived">{{ workspaceName(group.id) }}</h2>
      <ul class="history-list">
      <li v-for="session in group.items" :key="session.id">
        <button class="history-entry" type="button" :aria-expanded="archived ? preview?.session.id === session.id : undefined" @click="open(session)">
          <component :is="session.mode === 'work' ? BriefcaseBusiness : MessageSquare" />
          <span>{{ session.title || t('Untitled session', '未命名会话') }}</span>
        </button>
        <button type="button" class="quiet-button" :disabled="busy || archiveLoading || runningSessions.has(session.id)" :aria-label="archived ? t('Restore session', '恢复会话') : t('Archive session', '归档会话')" :title="archived ? t('Restore session', '恢复会话') : t('Archive session', '归档会话')" @click="change(() => archived ? restore(session) : archiveSession(session.id, true))">
          <component :is="archived ? ArchiveRestore : Archive" />
        </button>
        <button type="button" class="quiet-button" :disabled="busy || archiveLoading || runningSessions.has(session.id)" :aria-label="t('Delete session', '删除会话')" :title="t('Delete session', '删除会话')" @click="pending = session"><Trash2 /></button>
      </li>
      </ul>
    </section>
    <section v-if="pending" class="history-confirm" role="group" :aria-label="t('Confirm deletion', '确认删除')">
      <p>{{ t('Permanently delete this session?', '永久删除此会话？') }} <strong>{{ pending.title }}</strong></p>
      <div><button type="button" class="quiet-button" @click="pending = undefined">{{ t('Cancel', '取消') }}</button><button type="button" class="accent-button" :disabled="busy" @click="change(() => deleteSession(pending!.id))">{{ t('Delete', '删除') }}</button></div>
    </section>
    <p v-if="error" class="history-empty" role="alert">{{ error }}</p>
    <section v-if="preview" class="archive-preview" :aria-label="t('Archived conversation', '归档对话')">
      <h2>{{ preview.session.title || t('Untitled session', '未命名会话') }}</h2>
      <article v-for="(turn, index) in preview.items" :key="index">
        <strong>{{ t('You', '你') }}</strong><MarkdownText :text="turn.message" />
        <p v-if="turn.attachments?.length" class="muted">{{ turn.attachments.map(file => file.filename).join(', ') }}</p>
        <template v-if="turn.response"><strong>Agent</strong><MarkdownText :text="turn.response" previews /></template>
      </article>
      <p v-if="preview.loading" role="status">{{ t('Loading…', '加载中…') }}</p>
      <p v-else-if="!preview.items.length && !preview.error">{{ t('No messages.', '暂无消息。') }}</p>
      <p v-if="preview.error" role="alert">{{ preview.error }}</p>
      <button v-if="preview.cursor || preview.error" type="button" class="outline-button" :disabled="preview.loading" @click="loadPreview">{{ preview.error ? t('Retry', '重试') : t('Load more', '加载更多') }}</button>
    </section>
  </div>
</template>
