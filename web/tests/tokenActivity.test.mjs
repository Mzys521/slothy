import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import ts from 'typescript'

const code = ts.transpileModule(readFileSync(new URL('../src/tokenActivity.ts', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText
const { buildTokenActivity, formatTokenCount, heatLevel } = await import('data:text/javascript;base64,' + Buffer.from(code).toString('base64'))

test('token formatting keeps one decimal and K/M across boundaries', () => {
  for (const [input, expected] of [[0, '0.0'], [975, '975.0'], [1250, '1.3K'], [999949, '999.9K'],
    [999950, '1.0M'], [1280000, '1.3M'], [1500000000, '1500.0M'], [null, '—'], [undefined, '—'], [NaN, '—']]) {
    assert.equal(formatTokenCount(input), expected)
  }
})

test('UTC calendar aligns Monday columns, truncates future days and includes prior usage', () => {
  const result = buildTokenActivity({ as_of: '2026-11-05', prior_tokens: 700, daily: [
    { date: '2026-11-02', turns: 1, tokens: 1000 },
    { date: '2026-11-05', turns: 1, tokens: 2000000 },
    { date: '2026-11-06', turns: 1, tokens: 1000000000 },
  ] })
  assert.equal(result.days.length, 371)
  assert.equal(result.weeks.length, 53)
  assert.equal(result.days[0].date, '2025-11-03')
  assert.equal(result.days[result.lastDay].date, '2026-11-05')
  assert.equal(result.days.filter(day => day.future).length, 3)
  assert.equal(result.weeks.at(-1).end, '2026-11-05')
  assert.equal(result.weeks.at(-1).tokens, 2001000)
  assert.equal(result.cumulative[0].tokens, 700)
  assert.equal(result.cumulative.at(-1).tokens, 2001700)
  assert.equal(result.peak, 2000000)
})

test('unreported usage stays unknown and partial totals remain explicit', () => {
  const result = buildTokenActivity({ as_of: '2026-11-05', prior_tokens: 0, daily: [
    { date: '2026-10-26', turns: 1, tokens: null },
    { date: '2026-11-02', turns: 1, tokens: 5000 },
  ] })
  assert.equal(result.days.find(day => day.date === '2026-10-26').tokens, null)
  assert.equal(result.weeks.at(-2).tokens, null)
  assert.equal(result.cumulative.at(-2).tokens, null)
  assert.equal(result.cumulative.at(-1).tokens, 5000)
  assert.equal(result.cumulative.at(-1).partial, true)
  assert.equal(result.weeks.at(-1).partial, false)
  const empty = buildTokenActivity({ as_of: '2026-11-05', prior_tokens: 0, daily: [] })
  assert.equal(empty.cumulative.at(-1).tokens, 0)
  assert.equal(empty.cumulative.at(-1).partial, false)
})

test('heat levels grow monotonically with real consumption', () => {
  const levels = [0, 100, 40000, 200000, 600000, 1000000].map(value => heatLevel(value, 1000000))
  assert.equal(heatLevel(null, 1000000), 0)
  assert.equal(levels[0], 0)
  assert.equal(levels.at(-1), 5)
  assert.ok(levels.every((value, index) => index === 0 || value >= levels[index - 1]))
})

test('background usage contributes tokens without inventing chat turns and preserves partial totals', () => {
  const result = buildTokenActivity({ as_of: '2026-11-05', prior_tokens: 0, daily: [
    { date: '2026-11-05', turns: 0, tokens: 160, unreported: 1 },
  ] })
  assert.equal(result.days.find(day => day.date === '2026-11-05').turns, 0)
  assert.equal(result.weeks.at(-1).tokens, 160)
  assert.equal(result.weeks.at(-1).reported, true)
  assert.equal(result.weeks.at(-1).partial, true)
  assert.equal(result.cumulative.at(-1).tokens, 160)
})
