import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import ts from 'typescript'

const code = ts.transpileModule(readFileSync(new URL('../src/graphMotion.ts', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText
const { GraphMotion } = await import('data:text/javascript;base64,' + Buffer.from(code).toString('base64'))
const node = (id, importance = .5) => ({ id, label: id, type: 'semantic', importance })
const link = (relevance = .9) => ({ source: 'left', target: 'right', relevance })
function pair(edges = []) {
  const motion = new GraphMotion()
  motion.update({ nodes: [node('left'), node('right')], edges })
  motion.moveNode('left', 250, 374)
  motion.moveNode('right', 1050, 374)
  return motion
}
const distance = motion => {
  const [a, b] = motion.snapshot()
  return Math.hypot(a.x - b.x, a.y - b.y)
}
const advance = (motion, count = 180, held) => { for (let i = 0; i < count; i++) motion.step(1000 / 60, held) }

test('semantic provenance and concept strength survive simulation without exposing mutable internals', () => {
  const motion = new GraphMotion()
  motion.update({ nodes: [{ ...node('python'), type: 'skill', node_kind: 'concept', fact_ids: ['level-fact'], effective_frequency: 3, strength: 3 / 7 }], edges: [] })
  advance(motion, 3)
  const point = motion.snapshot()[0]
  assert.equal(point.node_kind, 'concept')
  assert.equal(point.effective_frequency, 3)
  assert.equal(point.strength, 3 / 7)
  assert.deepEqual(point.fact_ids, ['level-fact'])
  assert.equal('vx' in point, false)
  point.fact_ids.push('untrusted')
  assert.deepEqual(motion.snapshot()[0].fact_ids, ['level-fact'])
})

test('real links attract substantially more than the global field', () => {
  const linked = pair([link()]), unrelated = pair()
  advance(linked)
  advance(unrelated)
  assert.ok(distance(linked) < distance(unrelated) * .6)
  assert.ok(distance(unrelated) < 800)
  assert.ok(distance(linked) > 50, 'springs must not collapse endpoints into one dot')
})

test('stronger relationships settle closer', () => {
  const weak = pair([link(.1)]), strong = pair([link(1)])
  advance(weak, 360)
  advance(strong, 360)
  assert.ok(distance(strong) < distance(weak) - 30)
})

test('hovered, focused or selected nodes stay still while neighbors respond', () => {
  const motion = pair([link()])
  const before = motion.snapshot().find(point => point.id === 'left')
  advance(motion, 240, new Set(['left']))
  const after = motion.snapshot().find(point => point.id === 'left')
  assert.equal(after.x, before.x)
  assert.equal(after.y, before.y)
  assert.ok(distance(motion) < 500)
})

test('refresh keeps positions, manual moves remain bounded and overlaps separate', () => {
  const motion = pair([link()])
  advance(motion, 20)
  const before = motion.snapshot()
  motion.update({ nodes: [node('left'), node('right')], edges: [link()] })
  assert.deepEqual(motion.snapshot(), before)
  motion.moveNode('left', 635, 374)
  motion.moveNode('right', 635, 374)
  advance(motion, 240)
  assert.ok(distance(motion) > 27)
  motion.moveNode('left', -100000, 100000)
  const left = motion.snapshot().find(point => point.id === 'left')
  assert.ok(left.x >= 70 && left.y <= 700)
})

test('integration stays consistent across render rates and bounds resumed intervals', () => {
  const sixty = pair([link()]), thirty = pair([link()])
  for (let i = 0; i < 120; i++) sixty.step(16)
  for (let i = 0; i < 60; i++) thirty.step(32)
  for (let i = 0; i < 2; i++) {
    assert.ok(Math.abs(sixty.snapshot()[i].x - thirty.snapshot()[i].x) < .000001)
    assert.ok(Math.abs(sixty.snapshot()[i].y - thirty.snapshot()[i].y) < .000001)
  }
  const before = thirty.snapshot()
  thirty.step(0)
  assert.deepEqual(thirty.snapshot(), before)
  thirty.step(60000)
  assert.ok(Math.hypot(thirty.snapshot()[0].x - before[0].x, thirty.snapshot()[0].y - before[0].y) <= 5.5)
})

test('all 250 nodes remain finite inside the canvas', () => {
  const motion = new GraphMotion()
  const nodes = Array.from({ length: 250 }, (_, index) => node('memory-' + index, index % 10 / 10))
  motion.update({ nodes, edges: [] })
  const before = motion.snapshot()
  advance(motion, 30)
  const after = motion.snapshot()
  assert.ok(after.every((point, index) => point.x !== before[index].x || point.y !== before[index].y))
  assert.ok(after.every(point => Number.isFinite(point.x) && Number.isFinite(point.y) &&
    point.x >= 70 && point.x <= 1200 && point.y >= 45 && point.y <= 700))
})

test('category neighborhoods remain cohesive without inventing links', () => {
  const motion = new GraphMotion()
  const nodes = ['episodic', 'semantic', 'preference'].flatMap(type =>
    Array.from({ length: 12 }, (_, index) => ({ ...node(type + index), type })))
  motion.update({ nodes, edges: [] })
  function separation(points) {
    const same = [], different = []
    for (let i = 0; i < points.length; i++) for (let j = i + 1; j < points.length; j++) {
      const a = points[i], b = points[j]
      ;(a.type === b.type ? same : different).push(Math.hypot(a.x - b.x, a.y - b.y))
    }
    const mean = values => values.reduce((total, value) => total + value, 0) / values.length
    return mean(same) / mean(different)
  }
  assert.ok(separation(motion.snapshot()) < .65, 'static reduced-motion layout also groups categories')
  advance(motion, 360)
  assert.ok(separation(motion.snapshot()) < .65)
})

test('cross-category real links still bridge neighborhoods more strongly than category forces', () => {
  const motions = [new GraphMotion(), new GraphMotion()]
  motions.forEach((motion, index) => {
    motion.update({ nodes: [{ ...node('left'), type: 'semantic' }, { ...node('right'), type: 'episodic' }],
      edges: index === 0 ? [link()] : [] })
    motion.moveNode('left', 250, 374)
    motion.moveNode('right', 1050, 374)
    advance(motion, 300)
  })
  assert.ok(distance(motions[0]) < distance(motions[1]) * .6)
})
