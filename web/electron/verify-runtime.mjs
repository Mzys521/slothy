// Build-time acceptance only. Never included in application resources.
import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import { mkdtemp, readFile, readdir } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { startBackend, stopBackend } from './backend.cjs'

const web = path.resolve(fileURLToPath(new URL('..', import.meta.url)))
const profile = await mkdtemp(path.join(web, 'build/runtime-acceptance-'))
const resources = path.resolve(process.argv[2] || path.join(web, 'release/slothy/win-unpacked/resources'))
let calls = 0
let fileRead = false
const sdkRequests = []
const documentCode = [
  'from pathlib import Path', "Path('verification.txt').write_text('SLOTHY_DOCUMENT_OK', encoding='utf-8')",
  'from docx import Document', "d=Document(); d.add_paragraph('slothy'); d.save('verification.docx')",
  'from openpyxl import Workbook', "w=Workbook(); w.active['A1']='slothy'; w.save('verification.xlsx')",
  'from reportlab.pdfgen.canvas import Canvas', "c=Canvas('verification.pdf'); c.drawString(20,20,'slothy'); c.save()",
  'from pptx import Presentation', "p=Presentation(); p.save('verification.pptx')",
].join('\n')
const upstream = createServer(async (request, response) => {
  assert.equal(request.headers.authorization, 'Bearer runtime-verification-key')
  if (request.method === 'GET') {
    response.writeHead(200, { 'Content-Type': 'application/json' }).end(JSON.stringify({ data: [{ id: 'runtime-model' }] }))
    return
  }
  let raw = ''
  for await (const chunk of request) raw += chunk
  const body = JSON.parse(raw)
  sdkRequests.push(body)
  assert.ok(!raw.includes('cache_control'), 'OpenAI-compatible requests must not receive Anthropic markers')
  calls++
  assert.equal(body.model, 'runtime-model')
  assert.ok(body.tools.length)
  const prompt = body.messages.filter(message => message.role === 'user').at(-1)?.content || ''
  const toolResults = body.messages.filter(message => message.role === 'tool')
  let content = 'SLOTHY_CONNECTED'
  let toolCall
  if (String(prompt).includes('CALCULATE') && !toolResults.length) {
    toolCall = { id: 'call-calculator', type: 'function', function: { name: 'calculate', arguments: JSON.stringify({ operation: 'add', a: 2, b: 3 }) } }
  } else if (String(prompt).includes('DOCUMENT') && !toolResults.length) {
    toolCall = { id: 'call-document', type: 'function', function: { name: 'write_document_python', arguments: JSON.stringify({ code: documentCode }) } }
  } else if (toolResults.length) {
    content = 'TOOLS_COMPLETED'
    if (String(prompt).includes('DOCUMENT')) {
      assert.ok(toolResults.at(-1).content.includes('"ok": true'), toolResults.at(-1).content)
      assert.ok(toolResults.at(-1).content.includes('verification.pptx'))
    }
  }
  if (raw.includes('SLOTHY_UPLOAD_MARKER')) { fileRead = true; content = 'FILE_RECEIVED' }
  const usage = { prompt_tokens: 30, completion_tokens: 10, total_tokens: 40 }
  const base = { id: 'chatcmpl-' + calls, created: 1, model: 'runtime-model' }
  if (body.stream) {
    response.writeHead(200, { 'Content-Type': 'text/event-stream' })
    const delta = toolCall ? { tool_calls: [{ index: 0, ...toolCall }] } : { content }
    response.write('data: ' + JSON.stringify({ ...base, object: 'chat.completion.chunk', choices: [{ index: 0, delta: { role: 'assistant', ...delta }, finish_reason: null }] }) + '\n\n')
    response.write('data: ' + JSON.stringify({ ...base, object: 'chat.completion.chunk', choices: [{ index: 0, delta: {}, finish_reason: toolCall ? 'tool_calls' : 'stop' }] }) + '\n\n')
    response.end('data: ' + JSON.stringify({ ...base, object: 'chat.completion.chunk', choices: [], usage }) + '\n\ndata: [DONE]\n\n')
  } else {
    response.writeHead(200, { 'Content-Type': 'application/json' }).end(JSON.stringify({ ...base, object: 'chat.completion', choices: [{ index: 0, message: { role: 'assistant', content: toolCall ? null : content, ...(toolCall && { tool_calls: [toolCall] }) }, finish_reason: toolCall ? 'tool_calls' : 'stop' }], usage }))
  }
})
await new Promise(resolve => upstream.listen(0, '127.0.0.1', resolve))
let backend
try {
  backend = await startBackend({ resourcesPath: resources, userData: profile, development: false })
  const headers = { 'X-Slothy-Token': backend.token, 'Content-Type': 'application/json' }
  const request = async (url, method = 'GET', body) => {
    const response = await fetch(backend.origin + '/api/v1' + url, { method, headers, ...(body && { body: JSON.stringify(body) }) })
    if (response.status === 204) return null
    const result = await response.json()
    assert.ok(response.ok, JSON.stringify(result))
    return result
  }
  assert.equal((await fetch(backend.origin + '/api/v1/health')).status, 401)
  assert.equal((await fetch(backend.origin + '/docs', { headers })).status, 404)
  assert.equal((await request('/settings/providers')).setup_required, true)
  assert.deepEqual((await request('/threads')).items, [])
  assert.deepEqual((await request('/memories')).items, [])
  assert.deepEqual((await request('/models')).items, [])
  const memoryDefaults = await request('/settings/memory')
  assert.equal(memoryDefaults.revision, 0)
  assert.equal(memoryDefaults.runtime.planner_available, false)
  const memoryConfig = { ...memoryDefaults.configuration, batch_size: 12, retention_days: 120 }
  const memorySaved = await request('/settings/memory', 'PATCH', { expected_revision: 0, configuration: memoryConfig })
  assert.equal(memorySaved.revision, 1)
  assert.deepEqual(memorySaved.configuration, memoryConfig)
  const config = { id: 'runtime', name: 'Runtime acceptance', template: null, protocol: 'openai', base_url: `http://127.0.0.1:${upstream.address().port}/v1`, api_key: 'runtime-verification-key', models: [{ id: 'runtime-model' }] }
  assert.equal((await request('/settings/providers/discover', 'POST', config)).items[0].id, 'runtime-model')
  assert.equal((await request('/settings/providers', 'POST', config)).has_api_key, true)
  assert.equal((await request('/settings/providers')).setup_required, false)
  assert.ok(!(await readFile(path.join(profile, 'data/providers.json'), 'utf8')).includes(config.api_key))

  const thread = await request('/threads', 'POST', {})
  const upload = await fetch(backend.origin + `/api/v1/threads/${thread.thread_id}/attachments?filename=acceptance.md`, { method: 'POST', headers: { 'X-Slothy-Token': backend.token, 'Content-Type': 'application/octet-stream' }, body: 'SLOTHY_UPLOAD_MARKER\nThis is an isolated acceptance document.' })
  assert.equal(upload.status, 201)
  const attachment = await upload.json()
  const read = await request('/chat', 'POST', { thread_id: thread.thread_id, message: 'Read the attached file.', attachment_ids: [attachment.id] })
  assert.equal(read.response, 'FILE_RECEIVED')
  assert.ok(fileRead)

  const calculator = await request('/chat', 'POST', { message: 'CALCULATE 2 + 3' })
  assert.equal(calculator.response, 'TOOLS_COMPLETED')
  assert.ok(calculator.activity.some(item => item.name === 'calculate' && item.status === 'done'))
  const streamed = await fetch(backend.origin + '/api/v1/chat', { method: 'POST', headers, body: JSON.stringify({ message: 'Hello', stream: true }) })
  const events = await streamed.text()
  assert.ok(events.includes('event: activity'))
  assert.ok(events.includes('event: delta'))
  assert.ok(events.includes('event: done'))

  const pending = await request('/chat', 'POST', { message: 'DOCUMENT', mode: 'work' })
  assert.ok(pending.approval_id)
  assert.equal(pending.calls[0].name, 'write_document_python')
  const completed = await request(`/threads/${pending.thread_id}/approval/${pending.approval_id}`, 'POST', { approved: true })
  assert.equal(completed.response, 'TOOLS_COMPLETED')
  const directories = await readdir(path.join(profile, 'data/artifacts/runs'))
  assert.ok(directories.length)
  const files = await readdir(path.join(profile, 'data/artifacts/runs', directories[0], 'files'))
  assert.deepEqual(files.sort(), ['verification.docx', 'verification.pdf', 'verification.pptx', 'verification.txt', 'verification.xlsx'])

  // Exercise the cache composer through the actual bundled SDK and persistent stores.
  await request('/profile', 'PATCH', { expected_version: 0, preferences: { language: 'Python' } })
  const cacheThread = await request('/threads', 'POST', {})
  const firstCacheTurn = await request('/chat', 'POST', { thread_id: cacheThread.thread_id, message: 'CACHE_FIRST' })
  const firstPrefix = sdkRequests.at(-1).messages.find(message => message.name === 'profile_snapshot')?.content
  assert.ok(firstPrefix?.includes('Python'))
  await request('/profile', 'PATCH', { expected_version: 1, preferences: { language: 'Go' } })
  const secondCacheTurn = await request('/chat', 'POST', { thread_id: cacheThread.thread_id, message: 'CACHE_SECOND' })
  const cacheWire = sdkRequests.at(-1).messages
  assert.equal(cacheWire.find(message => message.name === 'profile_snapshot')?.content, firstPrefix)
  assert.ok(cacheWire.findIndex(message => message.name === 'personalization_reference') >
    cacheWire.findIndex(message => message.content === 'CACHE_FIRST'))
  assert.ok(cacheWire.find(message => message.name === 'personalization_reference')?.content.includes('Go'))
  assert.equal(firstCacheTurn.metrics.l1_hash, secondCacheTurn.metrics.l1_hash)
  assert.equal(secondCacheTurn.metrics.prompt_version, '2026-11-05.1')
  assert.equal(secondCacheTurn.metrics.l2_turns, 1)
  const cacheRuntime = await request('/runtime')
  assert.equal(cacheRuntime.prompt_layout, 'layered')
  assert.equal(cacheRuntime.cache_stats.prefix_hit_ratio, null, 'unreported cache usage stays unknown')

  await stopBackend(backend)
  backend = await startBackend({ resourcesPath: resources, userData: profile, development: false })
  const persisted = await fetch(backend.origin + '/api/v1/settings/providers', { headers: { 'X-Slothy-Token': backend.token } }).then(response => response.json())
  assert.equal(persisted.setup_required, false)
  assert.equal(persisted.items.length, 1)
  assert.ok(!JSON.stringify(persisted).includes(config.api_key))
  const memoryPersisted = await fetch(backend.origin + '/api/v1/settings/memory', { headers: { 'X-Slothy-Token': backend.token } }).then(response => response.json())
  assert.equal(memoryPersisted.revision, 1)
  assert.deepEqual(memoryPersisted.configuration, memoryConfig)
  headers['X-Slothy-Token'] = backend.token
  const continued = await request('/chat', 'POST', { thread_id: cacheThread.thread_id, message: 'CACHE_AFTER_RESTART' })
  assert.equal(sdkRequests.at(-1).messages.find(message => message.name === 'profile_snapshot')?.content, firstPrefix)
  assert.equal(continued.metrics.l1_hash, firstCacheTurn.metrics.l1_hash)
  assert.equal(continued.metrics.l2_turns, 2)
  console.log('Frozen runtime acceptance passed: clean first run, encrypted API configuration, memory settings, SDK routing, file upload, tools, SSE, approval, 5 document formats, layered prompt ordering, profile delta and frozen prefix persistence after restart.')
} finally {
  await stopBackend(backend)
  upstream.closeAllConnections()
  await new Promise(resolve => upstream.close(resolve))
}
