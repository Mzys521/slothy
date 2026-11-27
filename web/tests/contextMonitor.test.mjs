import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import ts from 'typescript'

async function moduleFor(name) {
  const source = readFileSync(new URL('../src/monitor/' + name + '.ts', import.meta.url), 'utf8')
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
  return import('data:text/javascript;base64,' + Buffer.from(outputText).toString('base64'))
}
const { recallCards } = await moduleFor('memoryCards')
const { estimateSegments, donutArcs, providerUsage } = await moduleFor('workMetrics')
const t = (_en, zh) => zh

test('joins trace and detail by full ID, preserving excluded, missing and legacy data', () => {
  const trace = { facts: [{ id: 'hit', included: true, reason: 'profile_attribute' }, { id: 'excluded', included: false }] }
  const details = [{ id: 'hit', content: 'Remembered text' }, { id: 'legacy' }]
  const cards = recallCards(['hit', 'legacy', 'unavailable', 'hit'], trace, details)
  assert.deepEqual(cards.map(c => c.id), ['hit', 'excluded', 'legacy', 'unavailable'])
  assert.equal(cards[0].detail.content, 'Remembered text')
  assert.equal(cards[1].detail, undefined)
  assert.equal(cards[2].fact, undefined)
  assert.equal(cards[3].detail, undefined)
  assert.deepEqual(recallCards([], trace, details, 'legacy').map(c => c.id), ['legacy'])
  assert.deepEqual(recallCards([], undefined, []), [])
})

test('sums categories across calls without counting total or estimated a second time', () => {
  const calls = [{ context: { system: 750, tool_schema: 140, total: 890, estimated: true } },
    { context: { system: 733, tool_schema: 140, history: 131, user: 5, memory: 124, runtime: 0, other: 0 } }]
  const segments = estimateSegments(calls, t)
  const total = segments.reduce((n, item) => n + item.value, 0)
  const arcs = donutArcs(segments, total)
  assert.equal(total, 2023)
  assert.deepEqual(arcs.map(x => x.key), ['system', 'tool_schema', 'history', 'memory', 'user'])
  assert.deepEqual(arcs.map(x => x.percent), ['73%', '14%', '6%', '6%', '<1%'])
  assert.ok(arcs.at(-1).painted >= 2)
  assert.ok(arcs[0].sweep < 1483 / total * 360)
  assert.ok(Math.abs(arcs.reduce((n, a) => n + a.sweep, 0) - 360) < 1e-9)
  assert.equal(estimateSegments(calls.slice(0, 1), t)[0].value, 750)
  assert.equal(donutArcs([{ key: 'x', value: 3 }], 3)[0].painted, 360)
  assert.deepEqual(donutArcs([{ key: 'x', value: 0 }], 0), [])
})

test('provider usage keeps partial counts, missing cache and a measured zero distinct', () => {
  const a = { usage: { source: 'provider', input_tokens: 20, output_tokens: 5, cache_hit_tokens: 0 }, duration_ms: 12 }
  const b = { usage: { source: 'provider', input_tokens: 80, output_tokens: 2, cache_hit_tokens: 75 }, duration_ms: 18 }
  assert.equal(providerUsage([a, b]).hitRate, .75)
  assert.equal(providerUsage([a, b]).duration.display, '30')
  assert.equal(providerUsage([a]).hitRate, 0)
  assert.equal(providerUsage([a, { usage: { source: 'unknown', input_tokens: 8 } }]).input.display, '≥ 20')
  assert.equal(providerUsage([a, {}]).hitRate, null)
  assert.equal(providerUsage([{}]).cache.display, '—')
})
