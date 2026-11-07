import type { CommandDefinition } from './types'

/** Stable names; translated labels do not become executable strings. / 固定名称，翻译文本不参与执行。 */
export const builtinCommands: readonly CommandDefinition[] = [
  { id: 'run.new', name: 'new', group: 'run', title: ['New conversation', '新建会话'], description: ['Start in the current mode', '使用当前模式开始新会话'], availability: 'idle' },
  { id: 'run.rename', name: 'rename', group: 'run', title: ['Rename conversation', '重命名会话'], description: ['Change the current title', '修改当前会话标题'], availability: 'has-session', argument: 'title' },
  { id: 'run.stop', name: 'stop', group: 'run', title: ['Stop', '停止运行'], description: ['Cancel the active request', '取消当前请求，可重试恢复'], availability: 'running' },
  { id: 'run.retry', name: 'retry', group: 'run', title: ['Retry', '重试上一条'], description: ['Resume an interrupted run or resend', '恢复中断运行或重新发送'], availability: 'idle', argument: 'message' },
  { id: 'run.status', name: 'status', group: 'run', title: ['Run status', '运行状态'], description: ['View model, context and usage', '查看模型、上下文与用量'], availability: 'always' },
  { id: 'context.compact', name: 'compact', group: 'context', title: ['Compact context', '压缩上下文'], description: ['Keep the archive and summarize earlier turns', '保留归档，摘要较早的轮次'], availability: 'has-session', requiresConfirmation: true },
  { id: 'context.clear', name: 'clear', group: 'context', title: ['Clear draft', '清空草稿'], description: ['Clear input, files and references', '清空输入、附件和引用，不删除历史'], availability: 'always' },
  { id: 'model.select', name: 'model', group: 'model', title: ['Select model', '选择模型'], description: ['Choose a configured model', '选择已配置的模型'], availability: 'idle', argument: 'model' },
  { id: 'model.mode', name: 'mode', group: 'model', title: ['Conversation mode', '会话模式'], description: ['Start a Chat or Work conversation', '新建聊天或工作模式会话'], availability: 'idle', argument: 'mode', choices: ['chat', 'work'], requiresConfirmation: true },
  { id: 'context.workspace', name: 'workspace', group: 'context', title: ['Select workspace', '选择工作区'], description: ['Use an existing workspace', '切换到已有工作区'], availability: 'idle', argument: 'workspace' },
  { id: 'context.attach', name: 'attach', group: 'context', title: ['Add files', '添加文件'], description: ['Choose files from your computer', '从电脑选择要上传的文件'], availability: 'idle' },
  { id: 'artifact.export', name: 'export', group: 'artifact', title: ['Export conversation', '导出会话'], description: ['Save Markdown or Word after confirmation', '确认后导出 Markdown 或 Word'], availability: 'has-session', choices: ['md', 'docx'], requiresConfirmation: true },
  { id: 'capability.tools', name: 'tools', group: 'capability', title: ['Available tools', '可用工具'], description: ['Inspect current capabilities and approval policy', '查看当前能力与审批策略'], availability: 'always' },
  { id: 'capability.plugins', name: 'plugins', group: 'capability', title: ['Manage plugins', '管理插件'], description: ['Enable, disable and manage connectors', '启用、停用和管理连接器'], availability: 'always' },
  { id: 'capability.memory', name: 'memory', group: 'capability', title: ['Memories', '记忆'], description: ['Open the memory page', '打开记忆页面'], availability: 'always' },
  { id: 'capability.graph', name: 'graph', group: 'capability', title: ['Memory graph', '记忆图谱'], description: ['Open the graph page', '打开图谱页面'], availability: 'always' },
  { id: 'ui.settings', name: 'settings', group: 'ui', title: ['Settings', '设置'], description: ['Open application settings', '打开应用设置'], availability: 'always' },
  { id: 'ui.language', name: 'language', group: 'ui', title: ['Language', '界面语言'], description: ['Switch English or Chinese', '切换中文或英文'], availability: 'always', argument: 'language', choices: ['zh', 'en'] },
  { id: 'ui.theme', name: 'theme', group: 'ui', title: ['Appearance', '外观主题'], description: ['Light, dark or follow system', '浅色、深色或跟随系统'], availability: 'always', argument: 'theme', choices: ['light', 'dark', 'system'] },
  { id: 'help.commands', name: 'help', group: 'help', title: ['Commands and shortcuts', '命令与快捷键'], description: ['Browse commands and reference types', '查看命令、引用与操作提示'], availability: 'always' },
]
