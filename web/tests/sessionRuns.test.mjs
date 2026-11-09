import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import ts from 'typescript'

let code = ts.transpileModule(readFileSync(new URL('../src/sessionRuns.ts', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText
code = code.replaceAll("'vue'", JSON.stringify(import.meta.resolve('vue')))
const runs = await import('data:text/javascript;base64,' + Buffer.from(code).toString('base64'))

test('concurrent sessions have independent cancellation and completion', () => {
  const a = runs.beginSessionRequest('a', 'a-turn')
  const b = runs.beginSessionRequest('b', 'b-turn')
  a.abort()
  assert.ok(a.signal.aborted)
  assert.equal(b.signal.aborted, false)
  assert.ok(runs.finishSessionRequest('a', a))
  assert.ok(runs.runningSessions.has('b'))
  assert.equal(runs.runningRequests.get('b'), b)
  assert.equal(runs.runningTurnIds.b, 'b-turn')
  runs.finishSessionRequest('b', b)
})

test('an old finally callback cannot clear the new request owner', () => {
  const old = runs.beginSessionRequest('session', 'old')
  runs.finishSessionRequest('session', old)
  const current = runs.beginSessionRequest('session', 'new')
  runs.liveReplies.value.new = 'streaming'
  assert.equal(runs.finishSessionRequest('session', old), false)
  assert.equal(runs.runningRequests.get('session'), current)
  assert.equal(runs.runningTurnIds.session, 'new')
  assert.ok(runs.runningSessions.has('session'))
  assert.equal(runs.liveReplies.value.new, 'streaming')
  runs.finishSessionRequest('session', current)
  assert.equal(runs.liveReplies.value.new, undefined)
})

test('a duplicate request in one session does not replace its stop handle', () => {
  const controller = runs.beginSessionRequest('same', 'first')
  assert.throws(() => runs.beginSessionRequest('same', 'second'), /already/)
  assert.equal(runs.runningRequests.get('same'), controller)
  assert.equal(runs.runningTurnIds.same, 'first')
  runs.finishSessionRequest('same', controller)
})
