import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import ts from 'typescript'

const code = ts.transpileModule(readFileSync(new URL('../src/readGuard.ts', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText
const { ReadGuard } = await import('data:text/javascript;base64,' + Buffer.from(code).toString('base64'))

/**
 * Mirrors the guarded read in WorkDetails.vue without a DOM: one panel, one selected
 * thread, and the rule that a response may only write while its token still owns it.
 * 不依赖 DOM 复现 WorkDetails.vue 的受控读取：单一面板、单一选中会话，响应仅在其令牌
 * 仍拥有目标时写入。
 */
function detailsPanel() {
  const guard = new ReadGuard()
  const applied = []
  const state = { thread: '', events: [], error: '', loading: false }
  async function load(request) {
    const thread = state.thread
    const token = guard.begin(thread)
    state.events = []
    state.error = ''
    state.loading = false
    if (!thread) return
    const active = () => guard.owns(token, state.thread)
    state.loading = true
    try {
      const items = await request(thread)
      if (!active()) return
      state.events = items
      applied.push(thread)
    } catch (reason) {
      if (!active()) return
      state.error = reason instanceof Error ? reason.message : String(reason)
    } finally {
      if (active()) state.loading = false
    }
  }
  return { guard, applied, state, load }
}

test('WorkDetails-style reads: a late A response cannot replace the selected B logs', async () => {
  const panel = detailsPanel()
  const pending = new Map()
  const request = thread => new Promise(resolve => pending.set(thread, resolve))
  panel.state.thread = 'a'
  const first = panel.load(request)
  panel.state.thread = 'b'
  const second = panel.load(request)
  pending.get('b')([{ model_call_id: 'b-call' }])
  await second
  pending.get('a')([{ model_call_id: 'a-call' }])
  await first
  assert.deepEqual(panel.state.events, [{ model_call_id: 'b-call' }])
  assert.deepEqual(panel.applied, ['b'])
  assert.equal(panel.state.loading, false)
})

test('WorkDetails-style reads: two reads of one thread apply only the newer response', async () => {
  const panel = detailsPanel()
  const pending = []
  panel.state.thread = 'a'
  const first = panel.load(() => new Promise(resolve => pending.push(resolve)))
  const second = panel.load(() => new Promise(resolve => pending.push(resolve)))
  pending[1]([{ model_call_id: 'newer' }])
  await second
  pending[0]([{ model_call_id: 'older' }])
  await first
  assert.deepEqual(panel.state.events, [{ model_call_id: 'newer' }])
  assert.deepEqual(panel.applied, ['a'])
})

test('WorkDetails-style reads: a stale failure never publishes an error', async () => {
  const panel = detailsPanel()
  const pending = new Map()
  panel.state.thread = 'a'
  const first = panel.load(thread => new Promise((resolve, reject) => pending.set(thread, { resolve, reject })))
  panel.state.thread = 'b'
  const second = panel.load(thread => new Promise((resolve, reject) => pending.set(thread, { resolve, reject })))
  pending.get('b').resolve([{ model_call_id: 'b-call' }])
  await second
  pending.get('a').reject(new Error('stale failure'))
  await first
  assert.equal(panel.state.error, '')
  assert.deepEqual(panel.state.events, [{ model_call_id: 'b-call' }])
})

test('WorkDetails-style reads: an unmounted panel ignores its in-flight response', async () => {
  const panel = detailsPanel()
  let resolve
  panel.state.thread = 'a'
  const reading = panel.load(() => new Promise(done => { resolve = done }))
  // Unmounting invalidates the read and clears the panel state.
  // 卸载会作废该读取并清空面板状态。
  panel.guard.invalidate(panel.state.thread)
  panel.state.events = []
  panel.state.loading = false
  resolve([{ model_call_id: 'late' }])
  await reading
  assert.deepEqual(panel.state.events, [])
  assert.deepEqual(panel.applied, [])
})

test('approval reads of different sessions never invalidate each other', () => {
  const reviews = new ReadGuard()
  const a = reviews.begin('session-a')
  const b = reviews.begin('session-b')
  assert.equal(reviews.owns(a, 'session-a'), true)
  assert.equal(reviews.owns(b, 'session-b'), true)
  const newer = reviews.begin('session-a')
  assert.equal(reviews.owns(a, 'session-a'), false)
  assert.equal(reviews.owns(newer, 'session-a'), true)
  assert.equal(reviews.owns(b, 'session-b'), true)
})
