/** Closed composer actions and their existing UI/API adapters. / 封闭命令与现有界面、接口适配。 */
export type CommandName = 'new' | 'rename' | 'stop' | 'retry' | 'status' | 'compact' | 'clear'
  | 'model' | 'mode' | 'workspace' | 'attach' | 'export' | 'tools' | 'plugins' | 'memory'
  | 'graph' | 'settings' | 'language' | 'theme' | 'help'
export type CommandGroup = 'run' | 'context' | 'model' | 'artifact' | 'capability' | 'ui' | 'help'
export interface CommandDefinition {
  id: string
  name: CommandName
  group: CommandGroup
  title: [string, string]
  description: [string, string]
  availability: 'always' | 'idle' | 'running' | 'has-session'
  argument?: string
  choices?: string[]
  requiresConfirmation?: boolean
}
export interface CommandState { running: boolean; hasSession: boolean; archived: boolean }
export interface CommandContext extends CommandState {
  actions: Record<CommandName, (argument: string) => void | Promise<void>>
}
