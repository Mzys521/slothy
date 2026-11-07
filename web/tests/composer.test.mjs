import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import ts from 'typescript'

function compiled(file, replacements = {}) {
  let code = ts.transpileModule(readFileSync(new URL('../src/' + file, import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  }).outputText
  for (const [from, to] of Object.entries(replacements)) code = code.replaceAll(from, to)
  return 'data:text/javascript;base64,' + Buffer.from(code).toString('base64')
}
const builtin = compiled('commands/builtin.ts')
const registry = await import(compiled('commands/registry.ts', { "'./builtin'": JSON.stringify(builtin) }))
const parser = await import(compiled('references/parser.ts'))
const references = await import(compiled('references/types.ts'))

test('composer triggers cover empty tokens, partial names, paths and arguments', () => {
  assert.equal(parser.detectTrigger('/', 1).kind, 'command')
  assert.equal(parser.detectTrigger('Please /com', 11).query, 'com')
  assert.equal(parser.detectTrigger('@', 1).kind, 'reference')
  assert.equal(parser.detectTrigger('Read @file:reports/q3', 21).referenceKind, 'file')
  assert.equal(parser.detectTrigger('/mode wo', 8).kind, 'argument')
  assert.equal(parser.detectTrigger('/model ', 7).query, '')
  assert.equal(parser.detectTrigger('@reports/q3', 11).kind, 'reference')
})

test('URLs, emails, code, composing and mid-token cursors never open a palette', () => {
  for (const value of ['https://example.com/a', 'a/b', '/usr/local', 'a@b.com', 'foo@bar', 'https://a/@b', '`/help', '```ts\n/help', 'text /help ']) {
    assert.equal(parser.detectTrigger(value, value.length), null, value)
  }
  assert.equal(parser.detectTrigger('/help', 3), null)
  assert.equal(parser.detectTrigger('/help', 5, true), null)
  const afterFence = '```\n/help\n```\n/help'
  assert.equal(parser.detectTrigger(afterFence, afterFence.length).kind, 'command')
})

test('selection replacement preserves surrounding draft and unresolved references stay explicit', () => {
  const value = 'Read @report\nthen summarize'
  const trigger = parser.detectTrigger(value, 12)
  assert.equal(parser.replaceToken(value, trigger), 'Read \nthen summarize')
  assert.equal(parser.unresolvedMentions('mail a@b.com then @file:report').length, 1)
  assert.equal(parser.unresolvedMentions('```\n@file:report\n```').length, 0)
})

test('the registry has unique stable commands, localized search and state gates', () => {
  assert.equal(registry.commands.length, 20)
  assert.equal(new Set(registry.commands.map(item => item.id)).size, 20)
  assert.equal(registry.filterCommands('导出')[0].name, 'export')
  const idle = { running: false, hasSession: false, archived: false }
  assert.equal(registry.commandAvailable(registry.findCommand('stop'), idle), false)
  assert.equal(registry.commandAvailable(registry.findCommand('compact'), idle), false)
  assert.equal(registry.commandAvailable(registry.findCommand('new'), idle), true)
  assert.equal(registry.commandAvailable(registry.findCommand('mode'), { ...idle, running: true }), false)
})

test('unknown or invalid commands never reach an action or an API', async () => {
  const called = []
  const context = { running: false, hasSession: true, archived: false,
    actions: Object.fromEntries(registry.commands.map(item => [item.name, value => called.push([item.name, value])])) }
  assert.deepEqual(registry.parseCommand('/rename New title'), { name: 'rename', argument: 'New title' })
  assert.equal(registry.parseCommand('/usr/local'), null)
  await assert.rejects(registry.executeCommand('run_command', 'rm', context), /Unknown command/)
  await assert.rejects(registry.executeCommand('mode', 'work; execute', context), /Invalid command/)
  await assert.rejects(registry.executeCommand('stop', '', context), /unavailable/)
  await assert.rejects(registry.executeCommand('new', 'ignored text', context), /Invalid command/)
  assert.deepEqual(called, [])
  await registry.executeCommand('mode', 'work', context)
  assert.deepEqual(called, [['mode', 'work']])
})

test('reference identity and wire payload never forward caller supplied content or labels', () => {
  const item = { kind: 'file', id: 'report.md', source: 'workspace', label: 'report', content: 'untrusted', enabled: true }
  assert.equal(references.referenceKey(item), 'file:workspace:report.md')
  assert.deepEqual(references.referencePayload(item), { kind: 'file', id: 'report.md', source: 'workspace' })
  assert.deepEqual(references.referencePayload({ kind: 'folder', id: 'reports', label: 'Reports' }), { kind: 'folder', path: 'reports' })
})
