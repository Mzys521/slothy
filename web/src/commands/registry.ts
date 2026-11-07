import { builtinCommands } from './builtin'
import type { CommandContext, CommandDefinition, CommandState } from './types'

export const commands = builtinCommands
export function findCommand(name: string) { return commands.find(item => item.name === name.toLowerCase()) }
export function commandAvailable(command: CommandDefinition, state: CommandState) {
  if (state.archived && !['help', 'settings', 'status', 'tools', 'plugins', 'graph', 'memory', 'theme', 'language'].includes(command.name)) return false
  if (command.availability === 'running') return state.running
  if (command.availability === 'idle') return !state.running
  if (command.availability === 'has-session') return state.hasSession && !state.running
  return true
}
export function filterCommands(query: string) {
  const text = query.toLocaleLowerCase()
  return commands.filter(item => [item.name, ...item.title, ...item.description].some(value => value.toLocaleLowerCase().includes(text)))
}
export function parseCommand(value: string): { name: string; argument: string } | null {
  const result = /^\s*\/([a-z][a-z0-9-]*)(?:[ \t]+([\s\S]*))?$/i.exec(value)
  return result ? { name: result[1]!.toLowerCase(), argument: (result[2] ?? '').trim() } : null
}
export async function executeCommand(name: string, argument: string, context: CommandContext) {
  const definition = findCommand(name)
  if (!definition) throw new Error('Unknown command / 未知命令。')
  if (!commandAvailable(definition, context)) throw new Error('Command unavailable / 当前无法执行此命令。')
  validateArgument(definition, argument)
  await context.actions[definition.name](argument)
}
export function validateArgument(definition: CommandDefinition, argument: string) {
  if (argument && (definition.choices ? !definition.choices.includes(argument) : !definition.argument)) {
    throw new Error('Invalid command argument / 命令参数无效。')
  }
}
