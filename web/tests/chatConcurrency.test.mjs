import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import ts from 'typescript'

const moduleUrl = code => 'data:text/javascript;base64,' + Buffer.from(code).toString('base64')
const compiled = (file, replacements = {}) => {
  let code = ts.transpileModule(readFileSync(new URL('../src/' + file, import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  }).outputText
  for (const [from, to] of Object.entries({ "'vue'": JSON.stringify(import.meta.resolve('vue')), ...replacements })) code = code.replaceAll(from, to)
  return moduleUrl(code)
}
const runUrl = compiled('sessionRuns.ts')
const runs = await import(runUrl)
const preferences = moduleUrl('export const readLocal=(key,fallback)=>fallback;export const writeLocal=()=>{};export const t=(en)=>en;')
const api = moduleUrl('export class ApiError extends Error{};export const threadPath=id=>"/threads/"+id;export const workRequest=(...args)=>globalThis.chatConcurrencyRequest(...args);')
const chat = await import(compiled('chat.ts', { "'./sessionRuns'": JSON.stringify(runUrl), "'./preferences'": JSON.stringify(preferences), "'./workApi'": JSON.stringify(api) }))

test('a stale thread-list snapshot cannot remove a now-server-backed running session', async () => {
  const complete = []
  globalThis.chatConcurrencyRequest = () => new Promise(resolve => complete.push(resolve))
  chat.sessions.value = [{ id: 'local-a', title: 'A', mode: 'chat', turns: [], draft: false }]
  const target = chat.sessions.value[0]
  const controller = runs.beginSessionRequest(target.id, 'turn-a')
  const refreshing = chat.syncThreads()
  target.apiThreadId = 'server-a'
  for (const resolve of complete) resolve({ items: [], next_offset: null })
  await refreshing
  assert.equal(chat.sessions.value.length, 1)
  assert.equal(chat.sessions.value[0], target)
  assert.equal(runs.runningRequests.get(target.id), controller)
  runs.finishSessionRequest(target.id, controller)
})

test('a current remote index keeps the same live session and turn objects', async () => {
  globalThis.chatConcurrencyRequest = path => Promise.resolve({ items: path.includes('archived=false')
    ? [{ thread_id: 'server-a', title: 'Renamed A', mode: 'chat', archived: false }] : [], next_offset: null })
  const target = chat.sessions.value[0]
  target.turns.push({ id: 'turn-a', prompt: 'A', files: [], time: '' })
  const turn = target.turns[0]
  const controller = runs.beginSessionRequest(target.id, turn.id)
  await chat.syncThreads()
  assert.equal(chat.sessions.value[0], target)
  assert.equal(chat.sessions.value[0].turns[0], turn)
  assert.equal(target.title, 'Renamed A')
  runs.finishSessionRequest(target.id, controller)
})

test('a new session finishing before a stale index returns is still retained', async () => {
  const complete = []
  globalThis.chatConcurrencyRequest = () => new Promise(resolve => complete.push(resolve))
  const refreshing = chat.syncThreads()
  chat.sessions.value.push({ id: 'local-b', title: 'B', mode: 'chat', turns: [] })
  const target = chat.sessions.value.at(-1)
  const controller = runs.beginSessionRequest(target.id, 'turn-b')
  target.apiThreadId = 'server-b'
  target.turns.push({ id: 'turn-b', prompt: 'B', files: [], time: '', serverReply: 'complete' })
  runs.finishSessionRequest(target.id, controller)
  for (const resolve of complete) resolve({ items: [], next_offset: null })
  await refreshing
  assert.ok(chat.sessions.value.includes(target))
})

test('a history read that finishes after a new run starts cannot replace its live turns', async () => {
  let complete
  globalThis.chatConcurrencyRequest = () => new Promise(resolve => { complete = resolve })
  const target = chat.sessions.value.find(item => item.apiThreadId === 'server-b'), turn = target.turns[0]
  const refreshing = chat.syncThreadMessages(target)
  const controller = runs.beginSessionRequest(target.id, turn.id)
  complete({ items: [], next_cursor: null })
  await refreshing
  assert.equal(target.turns[0], turn)
  runs.finishSessionRequest(target.id, controller)
})
