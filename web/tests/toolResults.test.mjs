import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import ts from 'typescript'

const code = ts.transpileModule(readFileSync(new URL('../src/toolResults.ts', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText
const { parseToolResult, safeArtifactUrl, fetchArtifact, riskLabel, metricSummary } = await import('data:text/javascript;base64,' + Buffer.from(code).toString('base64'))

test('new results expose status, error hints and artifacts while historical text remains readable', () => {
  assert.equal(parseToolResult('historical tool text'), null)
  assert.equal(parseToolResult('{"ok":false}'), null)
  const value = { ok: false, summary: 'Outside workspace', artifacts: [], error: { code: 'OUT_OF_WORKSPACE', message: 'Outside workspace', retryable: false, hint: 'Use a relative path.' } }
  assert.deepEqual(parseToolResult(JSON.stringify(value)), value)
  assert.equal(parseToolResult(JSON.stringify({ ok: true, summary: 'Read text', data: { text: 'reference' }, artifacts: [], next_offset: 8000 })).next_offset, 8000)
})

test('artifact links only navigate to the authenticated local artifact endpoint', () => {
  const path = '/api/v1/threads/abc/artifacts/' + 'a'.repeat(64)
  assert.equal(safeArtifactUrl(path), path)
  for (const value of ['javascript:alert(1)', 'https://example.com/file', '//example.com/file', path + '?token=secret', path + '#fragment']) assert.equal(safeArtifactUrl(value), undefined)
})

test('artifact bytes use the authenticated fetch transport and failures never become downloaded files', async () => {
  const artifact = { url: '/api/v1/threads/abc/artifacts/' + 'a'.repeat(64), name: 'report.txt' }
  const blob = await fetchArtifact(artifact, async path => {
    assert.equal(path, artifact.url)
    return new Response('report')
  })
  assert.equal(await blob.text(), 'report')
  await assert.rejects(fetchArtifact(artifact, async () => new Response('missing', { status: 404 })), /download failed/)
  await assert.rejects(fetchArtifact({ ...artifact, url: 'https://example.com/private' }, () => assert.fail('must not request external URL')), /Invalid artifact URL/)
})

test('risk and tool measurements use the selected language and real values', () => {
  assert.equal(riskLabel('network', (en, zh) => zh), '网络')
  const metric = { calls: 3, ok: 1, errors: 1, rejected: 1, duration_ms: 42, approval_wait_ms: 230, output_bytes: 81, artifacts: 1, timed_out: 0, reused: 1 }
  assert.match(metricSummary(metric, (en) => en), /3 calls.*42 ms.*230 ms.*81 B.*1 reused/)
})
