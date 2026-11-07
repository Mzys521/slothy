/**
 * Frozen-runtime acceptance test: exercises the packaged backend against a local fake model provider.
 *
 * 冻结运行时验收测试：用本地伪模型提供商验证打包后的后端。
 */

// Build-time acceptance only. Never included in application resources.
// 仅用于构建期验收，永远不会进入应用资源。
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
const memoryRequests = []
const documentCode = [
  'from pathlib import Path', "Path('verification.txt').write_text('SLOTHY_DOCUMENT_OK', encoding='utf-8')",
  'from docx import Document', "d=Document(); d.add_paragraph('slothy'); d.save('verification.docx')",
  'from openpyxl import Workbook', "w=Workbook(); w.active['A1']='slothy'; w.save('verification.xlsx')",
  'from reportlab.pdfgen.canvas import Canvas', "c=Canvas('verification.pdf'); c.drawString(20,20,'slothy'); c.save()",
  'from pptx import Presentation', "p=Presentation(); p.save('verification.pptx')",
].join('\n')
// A minimal OpenAI-compatible upstream that also emits the memory-learning plan.
// 一个最小的 OpenAI 兼容上游，同时会产出记忆学习计划。
const upstream = createServer(async (request, response) => {
  assert.equal(request.headers.authorization, 'Bearer runtime-verification-key')
  if (request.method === 'GET') {
    response.writeHead(200, { 'Content-Type': 'application/json' }).end(JSON.stringify({ data: [{ id: 'runtime-model' }] }))
    return
  }
  let raw = ''
  for await (const chunk of request) raw += chunk
  const body = JSON.parse(raw)
  const isMemory = String(body.messages[0]?.content).includes('isolated memory-learning planner')
  ;(isMemory ? memoryRequests : sdkRequests).push(body)
  assert.ok(!raw.includes('cache_control'), 'OpenAI-compatible requests must not receive Anthropic markers')
  calls++
  assert.equal(body.model, 'runtime-model')
  if (isMemory) assert.ok(!body.tools?.length, 'memory learning must not receive executable tools')
  else assert.ok(body.tools.length)
  const prompt = body.messages.filter(message => message.role === 'user').at(-1)?.content || ''
  const toolResults = body.messages.filter(message => message.role === 'tool')
  let content = 'SLOTHY_CONNECTED'
  let toolCall
  if (isMemory) {
    const input = JSON.parse(prompt)
    const turn = input.turns.find(turn => turn.text.includes('MEMORY_SIGNAL:'))
    const skill = input.turns.find(turn => turn.text.includes('SKILL_SIGNAL:') || turn.text.includes('SKILL_ONLY:'))
    const identity = input.turns.find(turn => turn.text.includes('IDENTITY_SIGNAL:'))
    const common = { action: 'add', scope: 'global', basis: 'explicit', durable: true, confidence: .98 }
    const concepts = skill ? [{ key: 'python', kind: 'skill', label: 'Python', aliases: ['python'] }] : []
    const facts = turn ? [{ action: 'add', kind: 'preference', key: 'answer_structure',
      title: '回答结构', content: '先给结论，再补充说明', scope: 'global', basis: 'explicit', durable: true,
      confidence: .97, evidence: [{ turn: turn.turn, quote: turn.text }] }] : []
    if (skill) {
      facts.push({ ...common, kind: 'skill', key: 'skills.python.learning', title: '学习 Python',
        content: '用户正在学习 Python', value: true, predicate: 'learning', target_concept: 'python',
        concept_keys: ['python'], evidence: [{ turn: skill.turn, quote: skill.text }] })
      if (skill.text.includes('SKILL_SIGNAL:')) {
        concepts.push({ key: 'beginner', kind: 'level', label: '新手', aliases: ['beginner'] })
        facts.push({ ...common, kind: 'skill', key: 'skills.python.level', title: 'Python 熟练度',
          content: '用户的 Python 熟练度为新手', value: 'beginner', predicate: 'proficiency',
          source_concept: 'beginner', target_concept: 'python', concept_keys: ['python', 'beginner'],
          evidence: [{ turn: skill.turn, quote: skill.text }] })
      }
    }
    if (identity) facts.push({ ...common, kind: 'identity', key: 'identity.name', title: '姓名',
      content: '用户姓名是林舟', value: '林舟', evidence: [{ turn: identity.turn, quote: identity.text }] })
    content = JSON.stringify({ concepts, facts })
  } else if (String(prompt).includes('CALCULATE') && !toolResults.length) {
    toolCall = { id: 'call-calculator', type: 'function', function: { name: 'calculate', arguments: JSON.stringify({ operation: 'add', a: 2, b: 3 }) } }
  } else if (String(prompt).includes('DOCUMENT') && !toolResults.length) {
    toolCall = { id: 'call-document', type: 'function', function: { name: 'run_python', arguments: JSON.stringify({ code: documentCode }) } }
  } else if (String(prompt).includes('EXPORT') && !toolResults.length) {
    toolCall = { id: 'call-export', type: 'function', function: { name: 'export_document', arguments: JSON.stringify({ format: 'docx', filename: 'verification-export.docx', title: 'Slothy export', sections: [{ paragraphs: ['SLOTHY_EXPORT_OK'] }] }) } }
  } else if (String(prompt).includes('DATETIME') && !toolResults.length) {
    toolCall = { id: 'call-datetime', type: 'function', function: { name: 'get_datetime', arguments: JSON.stringify({ timezone: 'Asia/Shanghai', datetime_iso: '2026-11-06T00:00:00+00:00' }) } }
  } else if (String(prompt).includes('WRITE_REFERENCE') && !toolResults.length) {
    toolCall = { id: 'call-reference-write', type: 'function', function: { name: 'workspace_write', arguments: JSON.stringify({ action: 'write_file', path: 'composer-reference.txt', content: 'SLOTHY_COMPOSER_REFERENCE' }) } }
  } else if (String(prompt).includes('REFERENCE_CONTEXT')) {
    assert.ok(body.messages.some(item => item.name === 'explicit_reference' && String(item.content).includes('SLOTHY_COMPOSER_REFERENCE')))
    content = 'REFERENCE_CONTEXT_OK'
  } else if (String(prompt).includes('REFERENCE_CLEAR')) {
    assert.ok(!body.messages.some(item => item.name === 'explicit_reference'))
    content = 'REFERENCE_CLEAR_OK'
  } else if (toolResults.length) {
    content = 'TOOLS_COMPLETED'
    const envelope = JSON.parse(toolResults.at(-1).content)
    assert.equal(envelope.ok, true, toolResults.at(-1).content)
    if (String(prompt).includes('DOCUMENT')) {
      assert.ok(toolResults.at(-1).content.includes('verification.pptx'))
    }
    if (String(prompt).includes('EXPORT')) assert.equal(envelope.artifacts[0].name, 'verification-export.docx')
    if (String(prompt).includes('DATETIME')) assert.equal(envelope.data.iso, '2026-11-06T08:00:00+08:00')
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
  /**
   * Calls the authorized API and asserts a successful JSON response.
   *
   * 调用需授权的 API，并断言返回成功的 JSON。
   *
   * @param url - Path below `/api/v1`. / `/api/v1` 之下的路径。
   * @param method - HTTP method; defaults to GET. / HTTP 方法，默认 GET。
   * @param body - Optional JSON body to serialize. / 可选的要序列化的 JSON 请求体。
   * @returns Parsed response payload, or `null` for 204. / 解析后的响应体；204 时返回 `null`。
   */
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
  assert.equal(memoryDefaults.runtime.planner_available, true)
  assert.equal(memoryDefaults.runtime.engine, 'llm_learning')
  assert.equal(memoryDefaults.runtime.model_available, false)
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
  assert.equal(calculator.metrics.tools[0].ok, 1)
  const datetime = await request('/chat', 'POST', { message: 'DATETIME' })
  assert.equal(datetime.response, 'TOOLS_COMPLETED')
  const streamed = await fetch(backend.origin + '/api/v1/chat', { method: 'POST', headers, body: JSON.stringify({ message: 'Hello', stream: true }) })
  const events = await streamed.text()
  assert.ok(events.includes('event: activity'))
  assert.ok(events.includes('event: delta'))
  assert.ok(events.includes('event: done'))

  const pending = await request('/chat', 'POST', { message: 'DOCUMENT', mode: 'work' })
  assert.ok(pending.approval_id)
  assert.equal(pending.calls[0].name, 'run_python')
  assert.equal(pending.calls[0].risk, 'execute')
  const completed = await request(`/threads/${pending.thread_id}/approval/${pending.approval_id}`, 'POST', { approved: true })
  assert.equal(completed.response, 'TOOLS_COMPLETED')
  const directories = await readdir(path.join(profile, 'data/artifacts/runs'))
  assert.ok(directories.length)
  const files = await readdir(path.join(profile, 'data/artifacts/runs', directories[0], 'files'))
  assert.deepEqual(files.sort(), ['verification.docx', 'verification.pdf', 'verification.pptx', 'verification.txt', 'verification.xlsx'])

  const exportPending = await request('/chat', 'POST', { message: 'EXPORT' })
  assert.equal(exportPending.calls[0].name, 'export_document')
  assert.equal(exportPending.calls[0].risk, 'write')
  const exported = await request(`/threads/${exportPending.thread_id}/approval/${exportPending.approval_id}`, 'POST', { approved: true })
  assert.equal(exported.response, 'TOOLS_COMPLETED')
  const artifact = exported.activity.find(item => item.name === 'export_document' && item.status === 'done').artifacts[0]
  const download = await fetch(backend.origin + artifact.url, { headers })
  assert.equal(download.status, 200)
  assert.equal((await download.arrayBuffer()).byteLength, artifact.bytes)

  // Composer APIs reuse the actual frozen workspace, archive, journal and download route.
  // 命令接口复用冻结运行时的工作区、归档、清单和下载。
  const referenceWrite = await request('/chat', 'POST', { message: 'WRITE_REFERENCE', mode: 'work' })
  assert.equal(referenceWrite.calls[0].name, 'workspace_write')
  await request(`/threads/${referenceWrite.thread_id}/approval/${referenceWrite.approval_id}`, 'POST', { approved: true })
  const references = await request(`/references/files?thread_id=${referenceWrite.thread_id}&q=composer-reference`)
  assert.equal(references.items[0].id, 'composer-reference.txt')
  const referenced = await request('/chat', 'POST', { thread_id: referenceWrite.thread_id, mode: 'work', message: 'REFERENCE_CONTEXT', references: [{ kind: 'file', id: references.items[0].id, source: 'workspace' }] })
  assert.equal(referenced.response, 'REFERENCE_CONTEXT_OK')
  assert.equal(referenced.message, 'REFERENCE_CONTEXT')
  assert.equal(referenced.references[0].label, 'composer-reference.txt')
  const cleared = await request('/chat', 'POST', { thread_id: referenceWrite.thread_id, mode: 'work', message: 'REFERENCE_CLEAR' })
  assert.equal(cleared.response, 'REFERENCE_CLEAR_OK')
  const regularCalls = sdkRequests.length
  const originalToolCalls = (await request('/account/activity')).tool_calls
  const compacted = await request(`/threads/${referenceWrite.thread_id}/compact`, 'POST', { confirmed: true })
  assert.ok(compacted.compacted)
  const commandExport = await request(`/threads/${referenceWrite.thread_id}/export`, 'POST', { confirmed: true, format: 'md' })
  assert.equal(commandExport.data.exported_turns, 3)
  const commandDownload = await fetch(backend.origin + commandExport.artifacts[0].url, { headers })
  assert.equal(commandDownload.status, 200)
  assert.ok((await commandDownload.text()).includes('REFERENCE_CONTEXT'))
  assert.equal((await request(`/threads/${referenceWrite.thread_id}/messages`)).items.length, 3)
  const commandUsage = await request('/commands/usage')
  assert.ok(commandUsage.references.successful > 0)
  assert.equal((await request('/account/activity')).tool_calls, originalToolCalls)
  assert.equal(sdkRequests.length, regularCalls, 'composer commands never invoke the model')

  // Exercise the cache composer through the actual bundled SDK and persistent stores.
  await request('/profile', 'PATCH', { expected_version: 0,
    attributes: [{ key: 'language', dimension: 'preference', value: 'Python' }] })
  const cacheThread = await request('/threads', 'POST', {})
  const firstCacheTurn = await request('/chat', 'POST', { thread_id: cacheThread.thread_id, message: 'CACHE_FIRST' })
  const firstPrefix = sdkRequests.at(-1).messages.find(message => message.name === 'profile_snapshot')?.content
  assert.ok(firstPrefix?.includes('Python'))
  await request('/profile', 'PATCH', { expected_version: 1,
    attributes: [{ key: 'language', dimension: 'preference', value: 'Go' }] })
  const secondCacheTurn = await request('/chat', 'POST', { thread_id: cacheThread.thread_id, message: 'CACHE_SECOND' })
  const cacheWire = sdkRequests.at(-1).messages
  const prunedPrefix = cacheWire.find(message => message.name === 'profile_snapshot')?.content
  assert.ok(!prunedPrefix?.includes('Python'), 'a corrected value must leave the frozen profile')
  assert.ok(cacheWire.findIndex(message => message.name === 'personalization_reference') >
    cacheWire.findIndex(message => message.content === 'CACHE_FIRST'))
  assert.ok(cacheWire.find(message => message.name === 'personalization_reference')?.content.includes('Go'))
  assert.notEqual(firstCacheTurn.metrics.l1_hash, secondCacheTurn.metrics.l1_hash)
  assert.equal(secondCacheTurn.metrics.prompt_version, '2026-11-07.memory-v3')
  assert.equal(secondCacheTurn.metrics.l2_turns, 1)
  const cacheRuntime = await request('/runtime')
  assert.equal(cacheRuntime.prompt_layout, 'layered')
  assert.equal(cacheRuntime.cache_stats.prefix_hit_ratio, null, 'unreported cache usage stays unknown')
  assert.equal(cacheRuntime.work_tools.length, 9)
  assert.equal(cacheRuntime.chat_tools.length, 7)
  assert.ok(!cacheRuntime.work_tools.includes('run_command'))
  assert.ok(!cacheRuntime.work_tools.includes('write_document_python'))
  assert.ok(!cacheRuntime.work_tools.includes('web_fetch'))

  const learned = await request('/chat', 'POST', { message: 'MEMORY_SIGNAL: Conclusions first are the most useful format for me in my work.' })
  let learnedProfile
  for (let attempt = 0; attempt < 100; attempt++) {
    learnedProfile = await request('/profile')
    if (learnedProfile.attributes?.some(item => item.key === 'answer_structure')) break
    await new Promise(resolve => setTimeout(resolve, 100))
  }
  const attribute = learnedProfile.attributes?.find(item => item.key === 'answer_structure')
  assert.ok(attribute, 'the default background LLM path must persist a real profile attribute')
  assert.ok(memoryRequests.length)
  const evidence = await request('/memories/' + attribute.memory_id)
  assert.ok(evidence.evidence.some(item => item.thread_id === learned.thread_id && item.run_id === learned.run_id))
  const autoReceipt = await request(`/threads/${learned.thread_id}/memory-receipts?run_id=${learned.run_id}`)
  assert.ok(autoReceipt.items.some(item => item.id === attribute.memory_id))
  const unrelated = await request('/chat', 'POST', { message: 'Please explain an unrelated example.' })
  assert.ok(unrelated.used_memory_ids.includes(attribute.memory_id))
  const learningRuntime = (await request('/settings/memory')).runtime
  assert.equal(learningRuntime.engine, 'llm_learning')
  assert.ok(learningRuntime.model_available && learningRuntime.reported_tokens > 0)

  const waitAttribute = async key => {
    for (let attempt = 0; attempt < 150; attempt++) {
      const result = (await request('/profile')).attributes.find(item => item.key === key)
      if (result) return result
      await new Promise(resolve => setTimeout(resolve, 100))
    }
    assert.fail('Background learning did not produce ' + key)
  }
  await request('/chat', 'POST', { message: 'SKILL_SIGNAL: 我最近在学习Python，我还是新手。' })
  const proficiency = await waitAttribute('skills.python.level')
  assert.equal(proficiency.value, 'beginner')
  const skillGraph = await request('/graph')
  const python = skillGraph.nodes.find(node => node.label === 'Python')
  const beginner = skillGraph.nodes.find(node => node.label === '新手')
  const relationship = skillGraph.edges.find(edge => edge.fact_id === proficiency.memory_id)
  assert.equal(relationship.source, beginner.id)
  assert.equal(relationship.target, python.id)
  assert.equal(relationship.effective_frequency, 1)
  await request('/chat', 'POST', { message: 'SKILL_ONLY: 我还在学习Python。' })
  for (let attempt = 0; attempt < 150; attempt++) {
    const graph = await request('/graph')
    if (graph.nodes.find(node => node.id === python.id)?.effective_frequency === 2) break
    await new Promise(resolve => setTimeout(resolve, 100))
  }
  const reinforced = await request('/graph')
  assert.equal(reinforced.nodes.find(node => node.id === python.id).effective_frequency, 2)
  assert.equal(reinforced.edges.find(edge => edge.fact_id === proficiency.memory_id).effective_frequency, 1)
  const skillAnswer = await request('/chat', 'POST', { message: '请给我Python学习建议。' })
  assert.ok(skillAnswer.used_memory_ids.includes(proficiency.memory_id))
  const skillContext = sdkRequests.at(-1).messages.find(message => message.name === 'personalization_reference')?.content
  assert.ok(skillContext?.includes('Semantic:') && skillContext.includes('proficiency') && skillContext.includes('Python'))
  await request('/chat', 'POST', { message: 'IDENTITY_SIGNAL: 我叫林舟。' })
  const name = await waitAttribute('identity.name')
  const modelCount = sdkRequests.length
  const identityAnswer = await request('/chat', 'POST', { message: '我叫什么名字？' })
  assert.equal(identityAnswer.response_source, 'identity_fact')
  assert.ok(identityAnswer.response.includes('林舟'))
  assert.ok(identityAnswer.used_memory_ids.includes(name.memory_id))
  assert.equal(sdkRequests.length, modelCount, 'identity answers must bypass model generation')
  assert.equal(identityAnswer.memory_trace.schema_version, 3)
  await request('/memories/' + name.memory_id, 'DELETE')
  const deletedIdentity = await request('/chat', 'POST', { message: '我叫什么名字？' })
  assert.ok(!deletedIdentity.response.includes('林舟'))
  assert.equal(sdkRequests.length, modelCount)

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
  assert.equal(sdkRequests.at(-1).messages.find(message => message.name === 'profile_snapshot')?.content, prunedPrefix)
  assert.equal(continued.metrics.l1_hash, secondCacheTurn.metrics.l1_hash)
  assert.equal(continued.metrics.l2_turns, 2)
  assert.ok((await request('/profile')).attributes.some(item => item.memory_id === attribute.memory_id))
  assert.equal((await request('/runtime')).data_epoch, cacheRuntime.data_epoch)
  const afterRestartName = await request('/chat', 'POST', { message: '我叫什么名字？' })
  assert.ok(!afterRestartName.response.includes('林舟'))
  console.log('Frozen runtime acceptance passed: SDK/tools/SSE/review/documents, composer, cache revocation, semantic concepts and independent relationship reinforcement, deterministic identity, deletion, evidence, data epoch and restart persistence.')
} finally {
  await stopBackend(backend)
  upstream.closeAllConnections()
  await new Promise(resolve => upstream.close(resolve))
}
