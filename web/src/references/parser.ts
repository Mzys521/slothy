import type { ReferenceKind } from './types'

export interface ComposerTrigger { kind: 'command' | 'reference' | 'add' | 'argument'; start: number; end: number; query: string; referenceKind?: ReferenceKind; command?: string }
/** Match only the current token, outside code, paths, URLs and email. / 仅匹配当前 token，避开代码、路径、网址与邮箱。 */
export function detectTrigger(value: string, position: number, composing = false): ComposerTrigger | null {
  if (composing || position < 0 || position > value.length) return null
  const before = value.slice(0, position)
  const fences = before.match(/^\s*(?:```|~~~)/gm) ?? []
  if (fences.length % 2 || (before.split('\n').at(-1)?.match(/(?<!\\)`/g)?.length ?? 0) % 2) return null
  if (position < value.length && !/\s/.test(value[position]!)) return null
  const argument = /^\s*\/(model|mode|workspace|language|theme|export)[ \t]+([^\n]*)$/i.exec(before)
  if (argument) return { kind: 'argument', command: argument[1]!.toLowerCase(), query: argument[2]!, start: 0, end: position }
  const match = /(?:^|\s)(\/[^\s]*|@[^\s]*)$/.exec(before)
  if (!match) return null
  const token = match[1]!
  const start = position - token.length
  if (token[0] === '/') {
    if (!/^\/[a-z0-9-]*$/i.test(token)) return null
    return { kind: 'command', query: token.slice(1), start, end: position }
  }
  const typed = /^@(folder|file|session|plugin):(.*)$/i.exec(token)
  if (/@|:\/\//.test(token.slice(1)) && !typed) return null
  return { kind: 'reference', query: typed ? typed[2]! : token.slice(1), start, end: position,
    referenceKind: typed?.[1]?.toLowerCase() as ReferenceKind | undefined }
}
export function replaceToken(value: string, trigger: ComposerTrigger, replacement = '') {
  return value.slice(0, trigger.start) + replacement + value.slice(trigger.end)
}
export function unresolvedMentions(value: string) {
  const matches: { start: number; end: number }[] = []
  for (const match of value.matchAll(/(?:^|\s)(@[^\s]+)/g)) {
    const end = match.index + match[0].length
    const trigger = detectTrigger(value, end)
    if (trigger?.kind === 'reference') matches.push({ start: trigger.start, end })
  }
  return matches
}
