import { computed, ref, watch, type Ref } from 'vue'
import { activeSession, activeWorkspace, runningSessions, selectWorkspace, workspaces, type ChatMode, type Session, type Turn } from '../chat'
import { pendingSessionCommands } from '../sessionRuns'
import { settings, t } from '../preferences'
import { threadPath, workRequest, type ModelItem, type ToolArtifact, type ToolResult } from '../workApi'
import { commandAvailable, commands, executeCommand, findCommand, validateArgument } from '../commands/registry'
import type { CommandContext, CommandName } from '../commands/types'

interface Host {
  session: Readonly<Ref<Session | undefined>>
  mode: Ref<ChatMode>
  model: Ref<string>
  models: Ref<ModelItem[]>
  draft: Ref<string>
  busy: Ref<boolean>
  create: () => void
  switchMode: (value: ChatMode) => void
  clear: () => void
  attach: () => void
  openModel: () => void
  arguments: (name: CommandName) => void
  stop: () => void
  retry: (message: string) => Promise<void>
  error: (value: unknown) => void
}
export interface CommandNotice { title: string; lines: string[]; artifacts?: ToolArtifact[] }

/** Command actions delegate to the existing stores, widgets and transport. / 命令委托给既有状态、组件与请求通道。 */
export function useComposerCommands(host: Host) {
  const notice = ref<CommandNotice | null>(null)
  const confirmation = ref<{ title: string; description: string; action: () => Promise<boolean> } | null>(null)
  const busy = computed(() => pendingSessionCommands.has(activeSession.value))
  const state = computed(() => ({ running: host.busy.value || runningSessions.has(activeSession.value),
    hasSession: Boolean(host.session.value?.apiThreadId), archived: Boolean(host.session.value?.archived) }))
  const tell = (title: string, ...lines: string[]) => { notice.value = { title, lines } }
  function currentThread() {
    const id = host.session.value?.apiThreadId
    if (!id) throw new Error(t('Send a message first.', '请先发送一条消息。'))
    return threadPath(id)
  }
  const actions: CommandContext['actions'] = {
    new: () => host.create(),
    rename: async title => {
      if (!title || title.length > 80) throw new Error(t('Enter a title of 1–80 characters.', '请输入 1–80 字的标题。'))
      const target = host.session.value!
      await workRequest(currentThread(), 'PATCH', { title })
      target.title = title
      tell(t('Conversation renamed', '会话已重命名'), title)
    },
    stop: () => host.stop(),
    retry: async text => host.retry(text),
    status: async () => {
      const turn: Turn | undefined = host.session.value?.turns.at(-1)
      tell(t('Run status', '运行状态'), state.value.running ? t('Running', '正在运行') : t('Idle', '空闲'),
        t('Mode', '模式') + ': ' + host.mode.value, t('Model', '模型') + ': ' + host.model.value,
        t('Context tokens', '上下文 Token') + ': ' + (turn?.metrics?.context_tokens ?? '—'),
        t('Reported tokens', '已报告 Token') + ': ' + (turn?.metrics?.usage_reported === false ? '—' : turn?.metrics?.total_tokens ?? '—'))
      const usage = await workRequest<{ references?: { attempts: number; successful: number } }>('/commands/usage')
      if (usage.references && notice.value) notice.value.lines.push(t('Reference validation', '引用校验') + ': ' + usage.references.successful + ' / ' + usage.references.attempts)
    },
    compact: async () => {
      const result = await workRequest<{ summary: string; compacted: boolean; version: number }>(currentThread() + '/compact', 'POST', { confirmed: true })
      tell(t('Context', '上下文'), result.summary)
    },
    clear: () => host.clear(),
    model: id => {
      if (!id) { host.openModel(); return }
      if (id !== 'auto' && !host.models.value.some(item => item.id === id)) throw new Error(t('Choose a configured model.', '请选择已配置的模型。'))
      host.model.value = id
      tell(t('Model selected', '已切换模型'), id)
    },
    mode: value => host.switchMode(value as ChatMode),
    workspace: value => {
      const item = workspaces.value.find(entry => entry.id === value || entry.name === value)
      if (!item) throw new Error(t('Choose an existing workspace.', '请选择已有工作区。'))
      if (item.id !== activeWorkspace.value) { selectWorkspace(item.id); host.create() }
      tell(t('Workspace selected', '已切换工作区'), item.name)
    },
    attach: () => host.attach(),
    export: async format => {
      const result = await workRequest<ToolResult>(currentThread() + '/export', 'POST', {
        confirmed: true, format: format || 'md', request_id: crypto.randomUUID(),
      })
      notice.value = { title: t('Conversation exported', '会话已导出'), lines: [result.summary,
        result.data && !Array.isArray(result.data) && result.data.truncated ? t('The export was truncated; full history is preserved.', '导出已截断，完整会话仍保留。') : ''].filter(Boolean), artifacts: result.artifacts }
    },
    tools: async () => {
      const runtime = await workRequest<{ chat_tools: string[]; work_tools: string[]; tool_groups: Record<string, string[]> }>('/runtime?workspace_id=' + encodeURIComponent(activeWorkspace.value))
      const visible = host.mode.value === 'work' ? runtime.work_tools : runtime.chat_tools
      tell(t('Available tools', '可用工具'), ...visible, t('Read-only operations run directly; write, execution and network operations require approval.', '只读操作直接运行；写入、执行与网络操作需要审批。'))
    },
    plugins: () => { location.hash = 'plugins' },
    memory: () => { location.hash = 'memories' },
    graph: () => { location.hash = 'graph' },
    settings: () => { location.hash = 'settings' },
    language: value => { settings.language = value === 'zh' ? 'zh-CN' : 'en'; tell(t('Language updated', '已切换语言'), value) },
    theme: value => { settings.mode = value; tell(t('Appearance updated', '已切换主题'), value) },
    help: () => tell(t('Commands and references', '命令与引用'), ...commands.map(item => '/' + item.name + ' · ' + t(...item.description)),
      t('@folder / @file / @session / @plugin add explicit reference context. Referencing a plugin does not enable it or execute tools.', '@folder / @file / @session / @plugin 添加显式引用。引用插件不会自动启用或执行。'),
      t('↑ ↓ / Home / End browse · Enter / Tab select · Esc close · Shift+Enter newline', '↑ ↓ / Home / End 浏览 · Enter / Tab 选择 · Esc 关闭 · Shift+Enter 换行')),
  }
  async function perform(name: CommandName, argument: string) {
    const owner = activeSession.value
    if (name !== 'stop') pendingSessionCommands.add(owner)
    const thread = host.session.value?.apiThreadId
    try {
      await executeCommand(name, argument, { ...state.value, actions })
      if (!['compact', 'export'].includes(name)) {
        await workRequest('/commands/events', 'POST', { name, thread_id: thread })
          .catch(() => {}) // Audit transport must not repeat a completed action. / 审计失败不得重做已完成操作。
      }
      return true
    } catch (error) { if (owner === activeSession.value) host.error(error); return false }
    finally { if (name !== 'stop') pendingSessionCommands.delete(owner) }
  }
  async function run(name: string, argument = '') {
    if (busy.value && name !== 'stop') { host.error(new Error(t('Wait for the current command.', '请等待当前命令完成。'))); return false }
    const definition = findCommand(name)
    if (!definition) { host.error(new Error(t('Unknown command. Use /help.', '未知命令，请使用 /help。'))); return false }
    if (!commandAvailable(definition, state.value)) { host.error(new Error(t('Command unavailable in the current state.', '当前状态无法执行此命令。'))); return false }
    if (!argument && ['rename', 'mode', 'workspace', 'language', 'theme'].includes(name)) { host.arguments(definition.name); return true }
    try { validateArgument(definition, argument) } catch (error) { host.error(error); return false }
    if (definition.requiresConfirmation) {
      confirmation.value = { title: t(...definition.title), description: name === 'compact'
        ? t('Summarize earlier context and retain the last two turns. The full archive is preserved.', '将较早上下文压缩为摘要，保留最近两轮；完整会话记录不变。')
        : name === 'export' ? t('Write a document to managed workspace artifacts. You can download it afterward.', '将文档写入当前工作区的受管产物目录，完成后可下载。')
          : t('Switching mode starts a new conversation.', '切换模式将新建会话。'),
        action: () => perform(definition.name, argument || (name === 'export' ? 'md' : argument)) }
      return true
    }
    return perform(definition.name, argument)
  }
  async function confirm() {
    const pending = confirmation.value
    confirmation.value = null
    if (pending) await pending.action()
  }
  watch([activeSession, activeWorkspace], () => { notice.value = null; confirmation.value = null })
  return { state, notice, confirmation, busy, run, confirm }
}
