<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { Archive, BookOpen, Copy, Ellipsis, Folder, FolderOpen, FolderPlus, GitFork, MessageCirclePlus, MessageSquare, Network, PanelLeft, Pin, PinOff, Plug, Plus, Settings, SquarePen, Trash2, UserRound } from 'lucide-vue-next'
import ChatPage from './ChatPage.vue'
import MemoryPage from './MemoryPage.vue'
import ServerCollection from './ServerCollection.vue'
import GraphPage from './GraphPage.vue'
import SettingsPage from './SettingsPage.vue'
import SessionHistory from './SessionHistory.vue'
import AccountPage from './AccountPage.vue'
import UserAvatar from './UserAvatar.vue'
import TitleBar from './TitleBar.vue'
import ApiSettingsPage from './ApiSettingsPage.vue'
import OnboardingOverlay from './OnboardingOverlay.vue'
import AboutPage from './AboutPage.vue'
import BrandWordmark from './BrandWordmark.vue'
import SettingsLayout from './SettingsLayout.vue'
import { configurationError, configurationLoaded, configurationLoading, loadProviderSettings, setupRequired } from './providerSettings'
import { developmentOnboarding } from './providerForm'
import { accountProfile, loadAccount } from './account'
import { activeSession, activeWorkspace, archiveSession, createWorkspace, forkSession, newChat, personalWorkspaceId, removeWorkspace, renameWorkspace, runningSessions, selectSession, sessions, sessionWorkspace, syncWorkspaceData, workspaceName, workspaceDirectory, workspaces, type Session } from './chat'
import { t, zh } from './preferences'
import { threadPath, workRequest } from './workApi'
import { copyText } from './clipboard'

const account = [
  { id: 'account', en: 'My profile', zh: '用户主页', icon: UserRound },
  { id: 'workspace', en: 'Conversation', zh: '会话流', icon: MessageSquare },
  { id: 'memories', en: 'Memories', zh: '记忆', icon: Archive },
  { id: 'graph', en: 'Graph', zh: '图谱', icon: Network },
  { id: 'library', en: 'Library', zh: '资料库', icon: BookOpen },
  { id: 'plugins', en: 'Connectors', zh: '连接器', icon: Plug },
] as const
type Page = 'chat' | 'settings' | 'settings/language' | 'settings/memory' | 'settings/archives' | 'settings/api' | 'settings/about' | 'memory-detail' | typeof account[number]['id']
const validPages = new Set<string>(['chat', 'settings', 'settings/language', 'settings/memory', 'settings/archives', 'settings/api', 'settings/about', 'memory-detail', ...account.map(item => item.id)])
const isDesktop = !!window.desktop
/** Resolve the active page from the URL hash, falling back to the workspace view. / 根据 URL hash 解析当前页面，无法识别时回退到会话页。 */
function currentPage(): Page {
  const route = location.hash.slice(1)
  return validPages.has(route) ? route as Page : 'workspace'
}
const page = ref(currentPage())
const onboardingPreview = ref(import.meta.env.DEV && developmentOnboarding(true, location.search))
const onboardingOpen = computed(() => configurationLoaded.value && (setupRequired.value || onboardingPreview.value))
const onboardingVisible = ref(false)
const onboardingBlocking = computed(() => onboardingOpen.value || onboardingVisible.value)
const conversationGeneration = ref(0)
const settingsPage = computed(() => page.value === 'settings' || page.value.startsWith('settings/'))
const conversationPage = computed(() => page.value === 'workspace' || page.value === 'chat' || page.value === 'memory-detail')
const selectedMemory = ref('')
const memoryOrigin = ref<Page>('workspace')
const graphNode = ref('')
const profileMenu = ref(false)
const profileArea = ref<HTMLElement>()
const profileButton = ref<HTMLButtonElement>()
const sidebarOpen = ref(false)
const sidebar = ref<HTMLElement>()
const expandedWorkspaces = ref(new Set<string>())
const workspaceDialog = ref<HTMLDialogElement>()
const workspaceDraft = ref('')
const workspaceDirectoryDraft = ref('')
const choosingDirectory = ref(false)
const canPickDirectory = typeof window.desktop?.selectDirectory === 'function'
type RowTarget = { kind: 'workspace'; id: string } | { kind: 'session'; item: Session }
const rowMenu = ref<{ target: RowTarget; anchor: HTMLButtonElement; left: number; top: number; above: boolean }>()
const rowMenuPanel = ref<HTMLElement>()
const editTarget = ref<RowTarget>()
const deletingWorkspace = ref(false)
const workspaceBusy = ref(false)
const workspaceError = ref('')
const workspaceLoadError = ref('')
const affectedSessions = computed(() => {
  const target = editTarget.value
  return target?.kind === 'workspace' ? sessions.value.filter(item => sessionWorkspace(item) === target.id) : []
})
const actionNotice = ref('')
const archiving = ref(new Set<string>())
const forking = ref(new Set<string>())
const now = ref(Date.now())
let ageTimer: number | undefined
const nameTitle = computed(() => deletingWorkspace.value ? t('Delete workspace', '删除工作区')
  : editTarget.value ? t('Rename', '重命名') : t('New workspace', '新建工作区'))
const duplicateWorkspace = computed(() => {
  if (editTarget.value?.kind === 'session') return false
  const editingId = editTarget.value?.id
  const name = workspaceDraft.value.trim().toLowerCase()
  return Boolean(name) && workspaces.value.some(workspace => workspace.id !== editingId && workspaceName(workspace.id).toLowerCase() === name)
})
// Group sessions under their workspace, hiding archived rows and putting pinned rows first.
// 按工作区归组会话，隐藏归档行并优先显示置顶行。
const workspaceTree = computed(() => workspaces.value.map(workspace => {
  const entries = sessions.value.filter(item => sessionWorkspace(item) === workspace.id)
  return {
    id: workspace.id,
    hasCurrentSession: conversationPage.value && entries.some(item => item.id === activeSession.value),
    items: entries.filter(item => !item.archived).sort((a, b) => (b.pinnedAt ?? 0) - (a.pinnedAt ?? 0)),
  }
}))
// Expand the newly active workspace and create a draft session when it is empty.
// 展开新激活的工作区，为空时创建草稿会话。
watch(activeWorkspace, id => { openWorkspace(id) })
watch(activeSession, id => {
  if (id) expandedWorkspaces.value.add(activeWorkspace.value)
})
// Recreate a draft session when the active workspace loses all of its rows.
// 活动工作区没有任何会话行时重建草稿会话。
watch(() => sessions.value.some(item => sessionWorkspace(item) === activeWorkspace.value), hasSessions => {
  if (!hasSessions && !workspaceBusy.value && expandedWorkspaces.value.has(activeWorkspace.value)) openWorkspace(activeWorkspace.value)
})
/**
 * Short status label shown on a session row.
 * 会话行上显示的简短状态标签。
 *
 * @param item - Session to describe. / 待描述的会话。
 * @returns Localized running, approval, finished or ready label. / 本地化的运行、审批、已结束或就绪文案。
 */
function sessionStatus(item: Session) {
  if (runningSessions.has(item.id)) return t('Running', '运行中')
  if (item.pendingReview) return t('Awaiting approval', '等待确认')
  return item.turns.length ? t('Finished', '已结束') : t('Ready', '就绪')
}
/**
 * Display title for a session row, including draft and untitled fallbacks.
 * 会话行的显示标题，包含草稿与未命名回退。
 *
 * @param item - Session to title. / 待生成标题的会话。
 * @returns Session title or a localized fallback. / 会话标题或本地化回退文案。
 */
function sessionTitle(item: Session) {
  return item.title || (item.draft === false || item.archived
    ? t('Untitled session', '未命名会话') : t('New session', '新建会话'))
}
watch(page, () => { profileMenu.value = false })

/**
 * Switch the active page and keep the URL hash in sync.
 * 切换当前页面并同步 URL hash。
 *
 * @param target - Page to open. / 要打开的页面。
 */
function navigate(target: Page) {
  page.value = target
  if (location.hash !== '#' + target) location.hash = target
  profileMenu.value = false
  sidebarOpen.value = false
}
/** Sync the page state from the current URL hash. / 根据当前 URL hash 同步页面状态。 */
function updateRoute() {
  if (location.hash === '#sessions') location.replace('#workspace')
  page.value = currentPage()
}
/** Retry the startup provider configuration after a failed attempt. / 启动配置失败后重试读取服务商配置。 */
async function initializeConfiguration() {
  try {
    await loadProviderSettings()
  } catch { /* The startup error stays visible with an explicit retry. */ }
}
/** Close the onboarding preview, reset the chat view and route to the workspace. / 关闭引导预览、重置聊天视图并跳转到会话页。 */
function finishOnboarding() {
  onboardingPreview.value = false
  conversationGeneration.value++
  const url = new URL(location.href)
  url.hash = 'workspace'
  history.replaceState(history.state, '', url)
  page.value = 'workspace'
  profileMenu.value = false; sidebarOpen.value = false
}
// Close other overlays when onboarding becomes visible.
// 引导层显示时关闭其他浮层。
watch(onboardingOpen, open => {
  if (!open) return
  profileMenu.value = false; sidebarOpen.value = false; closeRowMenu()
  if (workspaceDialog.value?.open) workspaceDialog.value.close()
})
/**
 * Open a memory detail view, remembering where the user came from.
 * 打开记忆详情视图，并记录用户来源页面。
 *
 * @param id - Memory id to open. / 要打开的记忆 id。
 */
function openMemory(id: string) {
  if (page.value !== 'memory-detail') memoryOrigin.value = page.value
  selectedMemory.value = id
  navigate('memory-detail')
}
/**
 * Jump to the transcript that produced a memory evidence quote.
 * 跳转到产生记忆证据引用的对话记录。
 *
 * @param thread - API thread id of the source conversation. / 来源会话的接口 id。
 * @param run - Run id whose messages should be revealed. / 需要定位的运行 id。
 */
function openEvidence(thread: string, run: string) {
  const session = sessions.value.find(item => item.apiThreadId === thread)
  if (!session || session.archived) {
    actionNotice.value = t('The source transcript is not open in this workspace.', '此工作区无法直接打开来源会话。')
    return
  }
  openSession(session.id)
  void nextTick(() => requestAnimationFrame(() => {
    const article = Array.from(document.querySelectorAll<HTMLElement>('[data-run-id]'))
      .find(item => item.dataset.runId === run)
    article?.scrollIntoView({ block: 'center' })
  }))
}
/**
 * Open the graph page focused on one memory.
 * 打开图谱页面并聚焦指定记忆。
 *
 * @param id - Memory id to focus. / 要聚焦的记忆 id。
 */
function openGraph(id: string) {
  graphNode.value = id
  navigate('graph')
}
/**
 * Activate a session and show the chat page.
 * 激活会话并显示聊天页面。
 *
 * @param id - Session id to open. / 要打开的会话 id。
 */
function openSession(id: string) {
  if (selectSession(id)) navigate('chat')
}
/**
 * Start a new session in a workspace and route to it.
 * 在工作区中新建会话并跳转到该会话。
 *
 * @param workspaceId - Workspace that owns the new session. / 新会话所属的工作区。
 */
function startChat(workspaceId = activeWorkspace.value) {
  if (!workspaces.value.some(item => item.id === workspaceId)) return
  newChat('chat', workspaceId)
  expandedWorkspaces.value.add(activeWorkspace.value)
  navigate('chat')
}
/**
 * Expand a workspace, creating an empty session when it has none.
 * 展开工作区；若其中没有会话则新建一个空会话。
 *
 * @param id - Workspace id to open. / 要打开的工作区 id。
 * @returns True when a new session had to be created. / 需要新建会话时返回 true。
 */
function openWorkspace(id: string) {
  expandedWorkspaces.value.add(id)
  if (sessions.value.some(item => sessionWorkspace(item) === id)) return false
  newChat('chat', id)
  return true
}
/**
 * Expand or collapse a workspace folder in the sidebar.
 * 在侧边栏中展开或折叠工作区目录。
 *
 * @param id - Workspace id to toggle. / 要切换的工作区 id。
 */
function toggleWorkspace(id: string) {
  if (expandedWorkspaces.value.delete(id)) return
  if (openWorkspace(id)) navigate('chat')
}
/**
 * Open the workspace/session dialog in create, rename or delete mode.
 * 以新建、重命名或删除模式打开工作区/会话弹窗。
 *
 * @param target - Optional workspace or session being edited. / 可选：正在编辑的工作区或会话。
 * @param remove - Whether the dialog should confirm deletion. / 弹窗是否进入删除确认模式。
 */
function showWorkspaceDialog(target?: RowTarget, remove = false) {
  workspaceError.value = ''
  editTarget.value = target
  deletingWorkspace.value = remove
  workspaceDirectoryDraft.value = ''
  workspaceDraft.value = target?.kind === 'workspace' ? workspaceName(target.id) : target?.item.title ?? ''
  workspaceDialog.value?.showModal()
}
/** Pick a working directory through the desktop bridge. / 通过桌面端桥接选择工作目录。 */
async function chooseDirectory() {
  if (!window.desktop?.selectDirectory || choosingDirectory.value) return
  choosingDirectory.value = true
  workspaceError.value = ''
  try {
    const directory = await window.desktop.selectDirectory()
    if (directory) {
      workspaceDirectoryDraft.value = directory
      if (!workspaceDraft.value.trim()) workspaceDraft.value = (directory.split(/[\\/]/).filter(Boolean).pop() ?? '').slice(0, 80)
    }
  } catch {
    workspaceError.value = t('Could not open the directory picker.', '无法打开工作目录选择器。')
  } finally { choosingDirectory.value = false }
}
/** Validate and apply the dialog's create, rename or delete action. / 校验并执行弹窗中的新建、重命名或删除操作。 */
async function saveWorkspace() {
  if (workspaceBusy.value) return
  const target = editTarget.value
  const name = workspaceDraft.value.trim()
  const deleting = deletingWorkspace.value && target?.kind === 'workspace'
  if (!deleting && (!name || duplicateWorkspace.value || (!target && !workspaceDirectoryDraft.value.trim()))) return
  workspaceError.value = ''
  if (deleting && affectedSessions.value.some(item =>
    runningSessions.has(item.id) || archiving.value.has(item.id) || forking.value.has(item.id))) {
    workspaceError.value = t('Wait for active session operations to finish, then retry.', '请等待会话操作结束后重试。')
    return
  }
  workspaceBusy.value = true
  try {
    if (deleting) {
      await removeWorkspace(target.id)
      expandedWorkspaces.value.delete(target.id)
    } else if (target?.kind === 'workspace') await renameWorkspace(target.id, name)
    else if (target?.kind === 'session') {
      await updateSessionMetadata(target.item, { title: name })
      target.item.title = name
    } else {
      await createWorkspace(name, workspaceDirectoryDraft.value.trim())
      navigate('workspace')
    }
    workspaceDialog.value?.close()
  } catch (error) {
    workspaceError.value = error instanceof Error ? error.message : t('Could not save changes.', '无法保存更改。')
  } finally {
    workspaceBusy.value = false
  }
}
const relativeTime = computed(() => new Intl.RelativeTimeFormat(zh.value ? 'zh-CN' : 'en', { style: 'short' }))
// Unit sizes in seconds, largest first, used by sessionAge().
// sessionAge 使用的单位秒数，按从大到小排列。
const ageUnits = [['year', 31536000], ['month', 2592000], ['week', 604800], ['day', 86400], ['hour', 3600], ['minute', 60]] as const
/**
 * Relative age label for a session row.
 * 会话行的相对年龄标签。
 *
 * @param createdAt - Creation timestamp in milliseconds. / 创建时间戳（毫秒）。
 * @returns Localized relative time, or an em dash when unknown. / 本地化相对时间；未知时返回破折号。
 */
function sessionAge(createdAt?: number) {
  if (!createdAt) return '—'
  const seconds = Math.max(0, (now.value - createdAt) / 1000)
  const unit = ageUnits.find(([, size]) => seconds >= size)
  return unit ? relativeTime.value.format(-Math.floor(seconds / unit[1]), unit[0]) : t('Now', '刚刚')
}
/**
 * Open the per-row action menu anchored to its trigger button.
 * 在触发按钮旁打开行内操作菜单。
 *
 * @param event - Click event on the menu trigger. / 菜单触发按钮的点击事件。
 * @param target - Workspace or session the menu acts on. / 菜单作用的工作区或会话。
 */
function showRowMenu(event: MouseEvent, target: RowTarget) {
  const anchor = event.currentTarget as HTMLButtonElement
  if (rowMenu.value?.anchor === anchor) {
    closeRowMenu()
    return
  }
  const rect = anchor.getBoundingClientRect()
  const above = rect.bottom + 230 > window.innerHeight && rect.top > window.innerHeight - rect.bottom
  rowMenu.value = { target, anchor, above, left: Math.max(8, Math.min(rect.right - 190, window.innerWidth - 198)),
    top: above ? rect.top - 4 : rect.bottom + 4 }
  void nextTick(() => rowMenuPanel.value?.querySelector<HTMLButtonElement>('button:not(:disabled)')?.focus())
}
/**
 * Close the row action menu.
 * 关闭行内操作菜单。
 *
 * @param restoreFocus - Whether to return focus to the trigger. / 是否将焦点还给触发按钮。
 */
function closeRowMenu(restoreFocus = false) {
  const anchor = rowMenu.value?.anchor
  rowMenu.value = undefined
  if (restoreFocus) anchor?.focus()
}
/**
 * Patch title or pin state on the server for a session that has a thread id.
 * 对已有接口会话 id 的会话，在服务端更新标题或置顶状态。
 *
 * @param item - Session to update. / 要更新的会话。
 * @param patch - Fields to send to the API. / 发送给接口的字段。
 */
async function updateSessionMetadata(item: Session, patch: { title?: string; pinned?: boolean }) {
  if (!item.apiThreadId) return
  await workRequest(threadPath(item.apiThreadId), 'PATCH', patch)
}
/**
 * Toggle the pinned state of a session.
 * 切换会话的置顶状态。
 *
 * @param item - Session to pin or unpin. / 要置顶或取消置顶的会话。
 */
async function togglePin(item: Session) {
  closeRowMenu(true)
  const pinned = !item.pinnedAt
  try {
    await updateSessionMetadata(item, { pinned })
    item.pinnedAt = pinned ? Date.now() : undefined
  } catch (error) {
    actionNotice.value = error instanceof Error ? error.message : t('Could not update session.', '无法更新会话。')
  }
}
/**
 * Toggle the archived state of a session.
 * 切换会话的归档状态。
 *
 * @param item - Session to archive or restore. / 要归档或恢复的会话。
 */
async function toggleArchive(item: Session) {
  closeRowMenu(true)
  if (archiving.value.has(item.id)) return
  actionNotice.value = ''
  archiving.value.add(item.id)
  try {
    await archiveSession(item.id, !item.archived)
  } catch (error) {
    actionNotice.value = error instanceof Error ? error.message : t('Could not update session.', '无法更新会话。')
  } finally {
    archiving.value.delete(item.id)
  }
}
/**
 * Fork a session into a new branch and open it.
 * 将会话分叉为新分支并打开。
 *
 * @param item - Session to fork. / 要分叉的会话。
 */
async function branchSession(item: Session) {
  closeRowMenu()
  if (forking.value.has(item.id)) return
  actionNotice.value = ''
  forking.value.add(item.id)
  try {
    const title = (item.title || t('Untitled session', '未命名会话')).slice(0, 100)
    await forkSession(item, title + t(' (fork)', '（分叉）'))
    navigate('chat')
  } catch (error) {
    actionNotice.value = error instanceof Error ? error.message : t('Could not fork session.', '无法分叉会话。')
  } finally {
    forking.value.delete(item.id)
  }
}
/**
 * Copy a session id to the clipboard and report the result.
 * 将会话 id 复制到剪贴板并提示结果。
 *
 * @param item - Session whose id should be copied. / 要复制 id 的会话。
 */
async function copySessionId(item: Session) {
  closeRowMenu(true)
  try {
    await copyText(item.id)
    actionNotice.value = t('Session ID copied.', '已复制会话 ID。')
  } catch {
    actionNotice.value = t('Could not copy session ID.', '无法复制会话 ID。')
  }
}
/**
 * Handle global Escape: close menus or reset transient navigation state.
 * 处理全局 Escape：关闭菜单或重置临时导航状态。
 *
 * @param event - Keydown event from the window. / 窗口上的键盘事件。
 */
function onGlobalKey(event: KeyboardEvent) {
  if (onboardingBlocking.value) return
  if (event.key === 'Escape') {
    if (rowMenu.value) {
      closeRowMenu(true)
      return
    }
    if (profileMenu.value) profileButton.value?.focus()
    profileMenu.value = false
    sidebarOpen.value = false
  }
}
/**
 * Close menus and popovers when a pointerdown lands outside them.
 * 当指针按在菜单或弹层之外时关闭它们。
 *
 * @param event - Document-level pointerdown event. / 文档级 pointerdown 事件。
 */
function closeOutside(event: PointerEvent) {
  const target = event.target as Node
  if (rowMenuPanel.value?.contains(target)) return
  if (!rowMenu.value?.anchor.contains(target)) closeRowMenu()
  if (!workspaceDialog.value?.open && !sidebar.value?.contains(target)) sidebarOpen.value = false
  if (!profileArea.value?.contains(target)) profileMenu.value = false
}
/** Load workspaces and sessions, surfacing failures beside the sidebar. / 加载工作区与会话，失败时在侧边栏提示。 */
async function loadWorkspaces() {
  workspaceLoadError.value = ''
  try {
    await syncWorkspaceData()
    openWorkspace(activeWorkspace.value)
  } catch (error) {
    workspaceLoadError.value = error instanceof Error ? error.message : t('Could not load workspaces.', '无法读取工作区。')
  }
}
// Bootstrap routing, configuration, workspaces and global listeners once on mount.
// 挂载时初始化路由、配置、工作区与全局监听。
onMounted(() => {
  updateRoute()
  void initializeConfiguration()
  void loadAccount().catch(() => {})
  void loadWorkspaces()
  window.addEventListener('hashchange', updateRoute)
  window.addEventListener('keydown', onGlobalKey)
  document.addEventListener('pointerdown', closeOutside)
  ageTimer = window.setInterval(() => { now.value = Date.now() }, 15_000)
})
// Remove every global listener and interval registered on mount.
// 卸载时移除挂载阶段注册的全局监听与定时器。
onUnmounted(() => {
  window.removeEventListener('hashchange', updateRoute)
  window.removeEventListener('keydown', onGlobalKey)
  document.removeEventListener('pointerdown', closeOutside)
  window.clearInterval(ageTimer)
})
</script>

<template>
  <TitleBar />
  <OnboardingOverlay :open="onboardingOpen" :desktop="isDesktop" :preview="onboardingPreview" @visibility="onboardingVisible = $event" @complete="finishOnboarding" />
  <div class="app-shell" :class="{ 'desktop-shell': isDesktop }" :inert="onboardingBlocking">
    <aside ref="sidebar" class="sidebar" :class="{ 'sidebar-expanded': sidebarOpen }" :aria-label="t('Navigation', '导航')">
      <a class="brand" href="#workspace" aria-label="slothy" @click.prevent="navigate('workspace')">
        <BrandWordmark />
      </a>
      <button type="button" class="sidebar-new" :disabled="!workspaces.length" :aria-label="t('New session', '新建会话')" :title="t('New session', '新建会话')" @click="startChat()">
        <Plus aria-hidden="true" /><span>{{ t('New session', '新建会话') }}</span>
      </button>
      <button type="button" class="sidebar-mobile-toggle sidebar-icon" :aria-expanded="sidebarOpen" aria-controls="sidebar-tree" :aria-label="t('Workspace sessions', '工作区会话')" @click="sidebarOpen = !sidebarOpen">
        <PanelLeft aria-hidden="true" />
      </button>
      <nav id="sidebar-tree" class="sidebar-tree" :aria-label="t('Workspace sessions', '工作区会话')" @scroll="closeRowMenu()">
        <div class="sidebar-tree-heading">
          <p class="sidebar-label">{{ t('Workspaces', '工作区') }}</p>
          <button type="button" class="sidebar-icon" :aria-label="t('New workspace', '新建工作区')" :title="t('New workspace', '新建工作区')" @click="showWorkspaceDialog()"><FolderPlus aria-hidden="true" /></button>
        </div>
        <div v-if="workspaceLoadError" class="sidebar-action-notice">
          <p role="alert">{{ workspaceLoadError }}</p>
          <button type="button" class="text-link" @click="loadWorkspaces">{{ t('Retry', '重试') }}</button>
        </div>
        <div v-for="workspace in workspaceTree" :key="workspace.id" class="sidebar-folder">
        <details :open="expandedWorkspaces.has(workspace.id)">
          <summary class="sidebar-workspace" @click.prevent="toggleWorkspace(workspace.id)">
            <component :is="expandedWorkspaces.has(workspace.id) ? FolderOpen : Folder" class="sidebar-workspace-icon"
              :class="{ 'has-current-session': expandedWorkspaces.has(workspace.id) && workspace.hasCurrentSession }" aria-hidden="true" />
            <span :title="workspaceName(workspace.id) + '\n' + workspaceDirectory(workspace.id)">{{ workspaceName(workspace.id) }}</span>
          </summary>
          <div class="sidebar-branches">
              <ul class="sidebar-branch">
                <li v-if="!workspace.items.length" class="sidebar-empty">{{ t('No sessions yet', '暂无会话') }}</li>
                <li v-for="item in workspace.items" :key="item.id" class="sidebar-session-row">
                  <button type="button" class="sidebar-session" :aria-current="conversationPage && activeSession === item.id ? 'true' : undefined"
                    :title="sessionTitle(item) + ' · ' + sessionStatus(item)" @click="openSession(item.id)">
                    <Pin v-if="item.pinnedAt" class="sidebar-pin" :aria-label="t('Pinned', '已置顶')" />
                    <span v-else class="session-dot" :class="{ 'session-dot-running': runningSessions.has(item.id), 'session-dot-pending': item.pendingReview && !runningSessions.has(item.id) }" aria-hidden="true"></span>
                    <span class="sidebar-session-title">{{ sessionTitle(item) }}</span>
                    <span class="sr-only"> · {{ sessionStatus(item) }}</span>
                  </button>
                  <time class="sidebar-session-age" :datetime="item.createdAt ? new Date(item.createdAt).toISOString() : undefined"
                    :title="item.createdAt ? new Date(item.createdAt).toLocaleString() : t('Creation time unavailable', '无创建时间记录')">{{ sessionAge(item.createdAt) }}</time>
                  <div class="sidebar-row-actions" :class="{ 'actions-open': rowMenu?.target.kind === 'session' && rowMenu.target.item.id === item.id }">
                    <button type="button" :aria-label="t('Session actions', '会话操作')" :aria-expanded="rowMenu?.target.kind === 'session' && rowMenu.target.item.id === item.id" @click="showRowMenu($event, { kind: 'session', item })"><Ellipsis /></button>
                    <button type="button" :disabled="archiving.has(item.id) || runningSessions.has(item.id) || Boolean(item.pendingReview)" :aria-label="t('Archive session', '归档会话')" @click="toggleArchive(item)"><Archive /></button>
                    <button type="button" :aria-label="item.pinnedAt ? t('Unpin session', '取消置顶') : t('Pin session', '置顶会话')" @click="togglePin(item)"><component :is="item.pinnedAt ? PinOff : Pin" /></button>
                  </div>
                </li>
              </ul>
          </div>
        </details>
        <div class="sidebar-row-actions" :class="{ 'actions-open': rowMenu?.target.kind === 'workspace' && rowMenu.target.id === workspace.id }">
          <button type="button" :aria-label="t('Workspace actions', '工作区操作')" :aria-expanded="rowMenu?.target.kind === 'workspace' && rowMenu.target.id === workspace.id" @click="showRowMenu($event, { kind: 'workspace', id: workspace.id })"><Ellipsis /></button>
          <button type="button" :aria-label="t('New session in workspace', '在此工作区新建会话')" @click="startChat(workspace.id)"><MessageCirclePlus /></button>
        </div>
        </div>
      </nav>
      <p v-if="actionNotice" class="sidebar-action-notice" role="status">{{ actionNotice }}</p>
      <div ref="profileArea" class="profile-area">
        <nav v-if="profileMenu" id="account-menu" class="account-menu" :aria-label="t('User menu', '用户菜单')">
          <a v-for="item in account" :key="item.en" :href="'#' + item.id" @click.prevent="navigate(item.id)"><component :is="item.icon" />{{ t(item.en, item.zh) }}</a>
        </nav>
        <button ref="profileButton" class="profile-button" :aria-label="t('User menu', '用户菜单')" :aria-expanded="profileMenu" :aria-controls="profileMenu ? 'account-menu' : undefined" @click="profileMenu = !profileMenu">
          <UserAvatar />
          <span><strong>{{ accountProfile?.display_name || t('Local account', '本地账户') }}</strong><small class="sidebar-workspace-name">{{ workspaceName(activeWorkspace) }}</small></span>
        </button>
        <a class="sidebar-icon sidebar-settings" href="#settings" :aria-label="t('Settings', '设置')" :title="t('Settings', '设置')" :aria-current="settingsPage ? 'page' : undefined" @click.prevent="navigate('settings')"><Settings aria-hidden="true" /></a>
      </div>
    </aside>

    <div class="app-content">
      <div v-if="!configurationLoaded" class="configuration-startup" role="status"><p :role="configurationError ? 'alert' : undefined">{{ configurationError || t('Starting slothy…', '正在启动 slothy…') }}</p><button v-if="configurationError" type="button" class="text-link" :disabled="configurationLoading" @click="initializeConfiguration">{{ t('Retry', '重试') }}</button></div>
      <template v-else>
      <AccountPage v-if="page === 'account'" />
      <GraphPage v-else-if="page === 'graph'" v-model="graphNode" @detail="openMemory" />
      <SettingsLayout v-else-if="settingsPage" :page="page" :setup-required="setupRequired">
        <SettingsPage v-if="page === 'settings' || page === 'settings/language' || page === 'settings/memory'"
          :section="page === 'settings/language' ? 'language' : page === 'settings/memory' ? 'memory' : 'appearance'" />
        <ApiSettingsPage v-else-if="page === 'settings/api'" />
        <AboutPage v-else-if="page === 'settings/about'" />
        <main v-else class="simple-page archives-page">
          <header><h1>{{ t('Archived sessions', '已归档会话') }}</h1></header>
          <SessionHistory archived />
        </main>
      </SettingsLayout>
      <ChatPage v-if="conversationPage" :key="conversationGeneration" :memory-id="page === 'memory-detail' ? selectedMemory : undefined"
        @memory="openMemory" @evidence="openEvidence" @close-context="navigate(memoryOrigin)"
        @memory-review="navigate('memories')">
        <template #memory-actions>
          <button v-if="selectedMemory" type="button" class="text-link" @click="openGraph(selectedMemory)">{{ t('Open in Graph', '在图谱中打开') }}</button>
        </template>
      </ChatPage>

      <ServerCollection v-if="page === 'plugins' || page === 'library'" :key="page" :kind="page" />
      <MemoryPage v-else-if="page === 'memories'" @memory="openMemory" @evidence="openEvidence" />
      </template>
    </div>

    <Teleport to="body">
      <div v-if="rowMenu" ref="rowMenuPanel" class="sidebar-action-menu" role="group" :aria-label="t('Actions', '操作')"
        :class="{ 'menu-above': rowMenu.above }" :style="{ left: rowMenu.left + 'px', top: rowMenu.top + 'px' }">
        <template v-if="rowMenu.target.kind === 'workspace'">
          <button type="button" @click="showWorkspaceDialog(rowMenu.target); closeRowMenu()"><SquarePen />{{ t('Rename', '重命名') }}</button>
          <button type="button" class="danger-action" :disabled="rowMenu.target.id === personalWorkspaceId" @click="showWorkspaceDialog(rowMenu.target, true); closeRowMenu()"><Trash2 />{{ t('Delete workspace', '删除工作区') }}</button>
        </template>
        <template v-else>
          <button v-if="!rowMenu.target.item.archived" type="button" @click="togglePin(rowMenu.target.item)">
            <component :is="rowMenu.target.item.pinnedAt ? PinOff : Pin" />{{ rowMenu.target.item.pinnedAt ? t('Unpin session', '取消置顶') : t('Pin session', '置顶会话') }}
          </button>
          <button type="button" @click="showWorkspaceDialog(rowMenu.target); closeRowMenu()"><SquarePen />{{ t('Rename', '重命名') }}</button>
          <button type="button" :disabled="!rowMenu.target.item.turns.length || forking.has(rowMenu.target.item.id) || runningSessions.has(rowMenu.target.item.id) || Boolean(rowMenu.target.item.pendingReview)"
            @click="branchSession(rowMenu.target.item)"><GitFork />{{ t('Fork session', '分叉会话') }}</button>
          <button type="button" :disabled="archiving.has(rowMenu.target.item.id) || runningSessions.has(rowMenu.target.item.id) || Boolean(rowMenu.target.item.pendingReview)" @click="toggleArchive(rowMenu.target.item)">
            <Archive />{{ t('Archive session', '归档会话') }}
          </button>
          <button type="button" @click="copySessionId(rowMenu.target.item)"><Copy />{{ t('Copy Session ID', '复制 Session ID') }}</button>
        </template>
      </div>
    </Teleport>

    <dialog ref="workspaceDialog" class="modal" aria-labelledby="new-workspace-title"
      @cancel="workspaceBusy && $event.preventDefault()" @click.self="!workspaceBusy && workspaceDialog?.close()">
      <form class="workspace-form" @submit.prevent="saveWorkspace">
        <h2 id="new-workspace-title">{{ nameTitle }}</h2>
        <p v-if="deletingWorkspace && editTarget?.kind === 'workspace'">
          {{ t(`Permanently delete “${workspaceName(editTarget.id)}” and its ${affectedSessions.length} sessions, including archived sessions and drafts?`,
            `永久删除「${workspaceName(editTarget.id)}」及其中的 ${affectedSessions.length} 个会话（包含归档会话和草稿）？`) }}
          {{ t('Files in the working directory are preserved.', '工作目录中的文件会保留。') }}
        </p>
        <template v-else>
        <label for="workspace-name">{{ editTarget?.kind === 'session' ? t('Session name', '会话名称') : t('Workspace name', '工作区名称') }}</label>
        <input id="workspace-name" v-model="workspaceDraft" maxlength="80" required autofocus
          :aria-invalid="duplicateWorkspace" :aria-describedby="duplicateWorkspace ? 'workspace-name-error' : undefined">
        <p v-if="duplicateWorkspace" id="workspace-name-error" class="muted" role="status">{{ t('This workspace name already exists.', '已有同名工作区。') }}</p>
        <template v-if="!editTarget">
          <label for="workspace-directory">{{ t('Working directory', '工作目录') }}</label>
          <div class="directory-picker">
            <input id="workspace-directory" v-model="workspaceDirectoryDraft" required :readonly="canPickDirectory"
              :placeholder="canPickDirectory ? t('Choose a directory', '选择一个工作目录') : t('Absolute path on the server device', '后端设备上的绝对目录路径')">
            <button v-if="canPickDirectory" type="button" class="outline-button" :disabled="choosingDirectory || workspaceBusy" @click="chooseDirectory"><FolderOpen />{{ t('Choose', '选择') }}</button>
          </div>
          <p class="muted">{{ t('Agent files and document output stay within this directory. Choose a separate directory for each workspace.', 'Agent 的文件访问与产物限定在此目录内。不同工作区请选择独立且不重叠的目录。') }}</p>
        </template>
        </template>
        <p v-if="workspaceError" class="muted" role="alert">{{ workspaceError }}</p>
        <footer>
          <button type="button" class="quiet-button" :disabled="workspaceBusy" @click="workspaceDialog?.close()">{{ t('Cancel', '取消') }}</button>
          <button type="submit" class="accent-button" :disabled="workspaceBusy || choosingDirectory || (!deletingWorkspace && (!workspaceDraft.trim() || duplicateWorkspace || (!editTarget && !workspaceDirectoryDraft.trim())))">{{ workspaceBusy ? t('Saving…', '处理中…') : deletingWorkspace ? t('Delete', '删除') : editTarget ? t('Save', '保存') : t('Create', '创建') }}</button>
        </footer>
      </form>
    </dialog>

  </div>
</template>
