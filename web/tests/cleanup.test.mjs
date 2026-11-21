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
const guardUrl = compiled('readGuard.ts')
const preferences = moduleUrl('export const readLocal=(key,fallback)=>fallback;export const writeLocal=()=>{};export const t=(en)=>en;')
const api = moduleUrl('export class ApiError extends Error { constructor(message, status, code) { super(message); this.status = status; this.code = code } };export const threadPath=id=>"/threads/"+id;export const workRequest=(...args)=>globalThis.cleanupRequest(...args);')
const { ApiError } = await import(api)
const chat = await import(compiled('chat.ts', {
  "'./sessionRuns'": JSON.stringify(runUrl), "'./preferences'": JSON.stringify(preferences),
  "'./workApi'": JSON.stringify(api), "'./readGuard'": JSON.stringify(guardUrl),
}))

const tick = (ms = 0) => new Promise(resolve => setTimeout(resolve, ms))

test('a durable cleanup is polled until it reports done', async () => {
  const seen = []
  const replies = [
    { id: 'op-1', state: 'running' },
    { id: 'op-1', state: 'pending' },
    { id: 'op-1', state: 'done' },
  ]
  globalThis.cleanupRequest = path => {
    seen.push(path)
    return Promise.resolve(replies.shift())
  }
  const done = await chat.waitForCleanup({ operation_id: 'op-1', state: 'pending' }, { intervalMs: 1, attempts: 5 })
  assert.equal(done.state, 'done')
  assert.deepEqual(seen, ['/operations/op-1', '/operations/op-1', '/operations/op-1'])
})

test('a failed cleanup surfaces its reason instead of reporting success', async () => {
  let polls = 0
  globalThis.cleanupRequest = () => { polls += 1; return Promise.resolve({ id: 'op-2', state: 'done' }) }
  await assert.rejects(
    chat.waitForCleanup({ operation_id: 'op-2', state: 'failed', error: 'CLEANUP_FAILED' }, { intervalMs: 1, attempts: 3 }),
    /Cleanup did not finish: CLEANUP_FAILED/,
  )
  assert.equal(polls, 0)
})

test('a cleanup that never finishes times out instead of reporting success', async () => {
  globalThis.cleanupRequest = () => Promise.resolve({ id: 'op-3', state: 'running' })
  await assert.rejects(
    chat.waitForCleanup({ operation_id: 'op-3', state: 'running' }, { intervalMs: 1, attempts: 2 }),
    /Cleanup is still running/,
  )
})

test('clear keeps the turns until its durable operation reports done', async () => {
  const turn = { id: 'turn-clear', prompt: 'hello', files: [], time: '' }
  const target = { id: 'session-clear', apiThreadId: 'server-clear', title: 'Clear', mode: 'chat', turns: [turn],
    pendingRun: { run_id: 'run-clear' } }
  chat.sessions.value = [target]
  const requests = []
  globalThis.cleanupRequest = (path, method) => {
    requests.push([path, method])
    if (method === 'DELETE') return Promise.resolve({ operation_id: 'op-clear', state: 'pending', error: null })
    if (path === '/operations/op-clear') return Promise.resolve({ id: 'op-clear', state: 'done' })
    throw new Error('unexpected request: ' + path)
  }
  const version = runs.sessionVersion(target.id)
  const clearing = chat.clearSession(target.id)
  await tick(80)
  // Still pending: the visible turns and pending run must not be dropped early.
  // 仍在进行中：可见轮次与待处理运行不得提前清除。
  assert.equal(target.turns.length, 1)
  assert.ok(target.pendingRun)
  await clearing
  assert.equal(target.turns.length, 0)
  assert.equal(target.pendingRun, null)
  assert.ok(runs.sessionVersion(target.id) > version)
  assert.deepEqual(requests[0], ['/threads/server-clear/messages', 'DELETE'])
  assert.deepEqual(requests.at(-1), ['/operations/op-clear', undefined])
})

test('delete keeps the session until its durable operation reports done', async () => {
  const target = { id: 'session-delete', apiThreadId: 'server-delete', title: 'Delete', mode: 'chat', turns: [] }
  chat.sessions.value = [target]
  globalThis.cleanupRequest = (path, method) => {
    if (method === 'DELETE') return Promise.resolve({ operation_id: 'op-delete', state: 'running', error: null })
    if (path === '/operations/op-delete') return Promise.resolve({ id: 'op-delete', state: 'done' })
    throw new Error('unexpected request: ' + path)
  }
  const deleting = chat.deleteSession(target.id)
  await tick(80)
  assert.ok(chat.sessions.value.includes(target))
  await deleting
  assert.equal(chat.sessions.value.includes(target), false)
  assert.equal(chat.activeSession.value, '')
})

test('a 404 cleanup target is already gone and still clears locally', async () => {
  const turn = { id: 'turn-404', prompt: 'gone', files: [], time: '' }
  const target = { id: 'session-404', apiThreadId: 'server-404', title: 'Gone', mode: 'chat', turns: [turn] }
  chat.sessions.value = [target]
  globalThis.cleanupRequest = () => Promise.reject(new ApiError('Session does not exist.', 404, 'NOT_FOUND'))
  await chat.clearSession(target.id)
  assert.equal(target.turns.length, 0)
})

test('a failed cleanup refreshes state and keeps the turns', async () => {
  const turn = { id: 'turn-failed', clientRequestId: 'turn-failed', prompt: 'keep', files: [], time: '' }
  const target = { id: 'session-failed', apiThreadId: 'server-failed', title: 'Failed', mode: 'chat', turns: [turn] }
  chat.sessions.value = [target]
  const remote = { thread_id: 'server-failed', title: 'Failed', mode: 'chat', archived: false, created_at: '2026-01-01T00:00:00Z' }
  const paths = []
  globalThis.cleanupRequest = (path, method) => {
    paths.push(path)
    if (method === 'DELETE') return Promise.resolve({ operation_id: 'op-failed', state: 'failed', error: 'CLEANUP_FAILED' })
    if (path === '/threads?archived=false&limit=100&offset=0') return Promise.resolve({ items: [remote], next_offset: null })
    if (path === '/threads?archived=true&limit=100&offset=0') return Promise.resolve({ items: [], next_offset: null })
    if (path === '/threads/server-failed/messages?after_id=0') return Promise.resolve({ items: [], next_cursor: null })
    throw new Error('unexpected request: ' + path)
  }
  await assert.rejects(chat.clearSession(target.id), /Cleanup did not finish: CLEANUP_FAILED/)
  assert.ok(paths.includes('/threads?archived=false&limit=100&offset=0'))
  assert.ok(paths.includes('/threads/server-failed/messages?after_id=0'))
  const current = chat.sessions.value.find(item => item.id === 'session-failed')
  assert.ok(current)
  assert.equal(current.turns.length, 1)
  assert.equal(current.turns[0].prompt, 'keep')
})

test('a cleanup conflict during a delete reads as a temporary conflict', async () => {
  const target = { id: 'session-busy', apiThreadId: 'server-busy', title: 'Busy', mode: 'chat', turns: [] }
  chat.sessions.value = [target]
  const paths = []
  globalThis.cleanupRequest = path => {
    paths.push(path)
    if (path === '/threads/server-busy') return Promise.reject(new ApiError('This session is being cleared; wait for it to finish.', 409, 'SESSION_CLEANING'))
    if (path === '/threads?archived=false&limit=100&offset=0') return Promise.resolve({ items: [], next_offset: null })
    if (path === '/threads?archived=true&limit=100&offset=0') return Promise.resolve({ items: [], next_offset: null })
    throw new Error('unexpected request: ' + path)
  }
  await assert.rejects(chat.deleteSession(target.id), /cleanup is still running/i)
  assert.ok(paths.includes('/threads?archived=false&limit=100&offset=0'))
})
