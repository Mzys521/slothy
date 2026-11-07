// V3 UI acceptance reuses the isolated Electron harness. Never shipped.
// V3 界面验收复用隔离 Electron 验证器，不随应用分发。
import { mkdir } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { createSmokeBackend, runSmoke } from './smoke.mjs'

const web = fileURLToPath(new URL('..', import.meta.url))
const output = path.resolve(web, '../_internal/core-qa/memory-v3')
await mkdir(output, { recursive: true })

for (const theme of ['dark', 'light']) {
  const calls = []
  let version = 1
  let attributes = [
    { key: 'identity.name', value: '林舟', dimension: 'identity', scope: 'global', scope_ref: null, memory_id: 'name-fact' },
    { key: 'skills.python.level', value: 'beginner', dimension: 'skill', target_label: 'Python', predicate: 'proficiency', scope: 'global', scope_ref: null, memory_id: 'level-fact' },
  ]
  const facts = [
    { id: 'name-fact', title: '姓名', content: '林舟', type: 'identity', scope: 'global', effective_frequency: 1, strength: .2, protected: true },
    { id: 'level-fact', title: 'Python 熟练度', content: '用户的 Python 熟练度为新手', type: 'skill', scope: 'global', effective_frequency: 2, strength: 1 / 3, protected: false },
  ]
  const fixture = await createSmokeBackend({ handle: async (request, response) => {
    const url = new URL(request.url, 'http://127.0.0.1')
    const route = url.pathname
    const json = value => { response.writeHead(200, { 'Content-Type': 'application/json' }).end(JSON.stringify(value)); return true }
    if (route.startsWith('/api/v1/desktop-smoke')) {
      if (route.endsWith('/memory-log')) return json(calls)
      if (route.endsWith('/memory-theme')) return json({ theme })
      return false
    }
    if (route === '/api/v1/runtime') return json({ ready: true, model: 'test', work_tools: [], chat_tools: [], storage_version: 3, data_epoch: 'memory-v3-ui' })
    if (route === '/api/v1/memory-suggestions') return json({ items: [] })
    if (route === '/api/v1/memory-learning') return json({ enabled: true, total_turns: 1, pending: 0, processed: 1, failed: 0, suggestions: 0, observed: 0, profile_version: version, engine: 'llm_learning', mode: 'auto', dead: 0, last_error: null })
    if (route === '/api/v1/profile/versions') return json({ items: [], next_cursor: null, retention_limit: 200 })
    if (route === '/api/v1/memories') return json({ items: facts, next_cursor: null })
    if (route.startsWith('/api/v1/memories/')) return json({ ...facts.find(f => f.id === route.split('/').at(-1)), evidence: [] })
    if (route === '/api/v1/graph') return json({ nodes: [
      { id: 'python', label: 'Python', type: 'skill', node_kind: 'concept', concept_kind: 'skill', fact_ids: ['level-fact'], importance: .8, effective_frequency: 3, strength: 3 / 7 },
      { id: 'beginner', label: '新手', type: 'skill', node_kind: 'concept', concept_kind: 'level', fact_ids: ['level-fact'], importance: .7, effective_frequency: 2, strength: 1 / 3 },
    ], edges: [{ id: 'level-fact', fact_id: 'level-fact', source: 'beginner', target: 'python', relation: 'proficiency', relevance: .8, directed: true, origin: 'semantic', effective_frequency: 2, strength: 1 / 3 }], total: 2, truncated: false, limit: 100 })
    if (route === '/api/v1/profile' && request.method === 'GET') return json({ version, attributes })
    if (route.endsWith('/approval')) return json(null)
    if (request.method !== 'POST' && request.method !== 'PATCH') return false
    let raw = ''
    for await (const chunk of request) raw += chunk
    const body = raw ? JSON.parse(raw) : {}
    calls.push({ route, body })
    if (route === '/api/v1/profile') {
      if ('preferences' in body) throw Error('The UI used the retired preferences contract')
      attributes = body.attributes.map(a => ({ ...a, memory_id: a.dimension === 'identity' ? 'name-fact' : 'level-fact' }))
      facts[0].content = String(attributes.find(a => a.key === 'identity.name')?.value)
      version++
      return json({ version, attributes })
    }
    if (route === '/api/v1/threads') return json({ thread_id: 'identity-ui', title: '', mode: 'chat' })
    if (route === '/api/v1/chat') {
      response.writeHead(200, { 'Content-Type': 'text/event-stream' })
      response.end('event: done\ndata: ' + JSON.stringify({ run_id: 'identity-run', thread_id: 'identity-ui', response: '我记录的姓名是：林海', message: body.message, used_memory_ids: ['name-fact'], response_source: 'identity_fact', memory_trace: { schema_version: 3, response_source: 'identity_fact', status: 'identity_fact', context_token_estimate: 0, warnings: [], facts: [{ id: 'name-fact', included: true, reason: 'identity_intent', strength: .2, concept_ids: [] }] } }) + '\n\n')
      return true
    }
    return json({ ok: true })
  } })
  await runSmoke({ backend: fixture, screenshot: path.join(output, 'profile-' + theme + '.png'), verify: async function () {
    const check = (value, message) => { if (!value) throw Error(message) }
    const until = async (condition, message) => {
      for (let i = 0; i < 150; i++) { if (condition()) return; await new Promise(resolve => setTimeout(resolve, 40)) }
      throw Error(message)
    }
    // The theme is also available as a serialized literal via the URL-free harness payload below.
    const { theme } = await (await fetch('/api/v1/desktop-smoke/memory-theme')).json()
    location.hash = '#settings'
    const themeLabel = theme === 'light' ? '浅色' : '深色'
    await until(() => [...document.querySelectorAll('button')].some(b => b.textContent.trim() === themeLabel), 'Theme controls missing')
    ;[...document.querySelectorAll('button')].find(b => b.textContent.trim() === themeLabel).click()
    await until(() => document.documentElement.dataset.mode === theme, 'Application theme did not change')
    location.hash = '#memories'
    await until(() => document.querySelector('.memory-management') && document.body.textContent.includes('林舟'), 'V3 profile did not load')
    const edit = [...document.querySelectorAll('button')].find(b => b.textContent.includes('编辑画像'))
    check(edit, 'Unified profile editor missing'); edit.click()
    await until(() => document.querySelector('.profile-editor[open]'), 'Profile editor did not open')
    const row = [...document.querySelectorAll('.v3-profile-row')].find(r => [...r.querySelectorAll('input')].some(i => i.value === 'identity.name'))
    check(row, 'Identity row missing')
    const value = row.querySelectorAll('input')[1]
    value.value = '林海'; value.dispatchEvent(new Event('input', { bubbles: true }))
    row.closest('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    await until(() => !document.querySelector('.profile-editor[open]') && document.body.textContent.includes('林海'), 'Unified profile edit did not persist')
    const log = await (await fetch('/api/v1/desktop-smoke/memory-log')).json()
    const patch = log.find(c => c.route.endsWith('/profile'))
    check(patch?.body.attributes.some(a => a.key === 'identity.name' && a.value === '林海') && !('preferences' in patch.body), 'Editor sent a second profile truth')
    location.hash = '#graph'
    await until(() => document.querySelectorAll('.graph-node').length === 2, 'Semantic graph nodes missing')
    const node = document.querySelector('.graph-node[aria-label="Python"]')
    check(node, 'Accessible concept label missing')
    node.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await until(() => document.querySelector('.graph-selection')?.textContent.includes('概念确认会话'), 'Concept frequency not shown')
    check(document.querySelector('.graph-selection').textContent.includes('有效确认会话'), 'Relation frequency not shown separately')
    check(document.querySelector('line[marker-end]'), 'Semantic edge direction missing')
    location.hash = '#'
    await until(() => document.querySelector('.sidebar-new') && !document.querySelector('.sidebar-new').disabled, 'Conversation creation unavailable')
    document.querySelector('.sidebar-new').click()
    await until(() => document.querySelector('.composer textarea'), 'Composer missing')
    const field = document.querySelector('.composer textarea')
    field.value = '我叫什么名字？'; field.dispatchEvent(new Event('input', { bubbles: true }))
    field.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
    await until(() => document.body.textContent.includes('我记录的姓名是：林海'), 'Fact answer missing')
    const monitor = [...document.querySelectorAll('button')].find(b => (b.getAttribute('aria-label') || '').includes('上下文'))
    if (monitor) monitor.click()
    await until(() => document.querySelector('[data-testid="memory-trace"]'), 'Context trace missing')
    check(document.querySelector('[data-testid="memory-trace"]').textContent.includes('根据身份事实直接回答'), 'Response provenance mislabeled')
    location.hash = '#memories'
    await until(() => document.querySelector('.memory-management'), 'Memory screen did not return')
  } })
}
