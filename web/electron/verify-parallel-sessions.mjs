/** Concurrent session lifecycle regression. / 多会话生命周期回归。 */
import { mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { createSmokeBackend, runSmoke } from './smoke.mjs'

const output = path.resolve(fileURLToPath(new URL('..', import.meta.url)), '../_internal/core-qa/parallel-preview')
await mkdir(output, { recursive: true })
const threads = [], streams = new Map()
const backend = await createSmokeBackend({ handle: async (request, response) => {
  const url = new URL(request.url, 'http://localhost')
  const json = body => { response.writeHead(200, { 'Content-Type': 'application/json' }).end(JSON.stringify(body)); return true }
  if (url.pathname === '/api/v1/desktop-smoke/parallel') return json([...streams].map(([name, value]) => ({ name, closed: value.closed, done: value.done })))
  if (url.pathname === '/api/v1/desktop-smoke/finish') {
    const name = url.searchParams.get('name'), value = streams.get(name)
    if (value && !value.closed) {
      value.done = true
      value.response.end('event: done\ndata: ' + JSON.stringify({ thread_id: value.thread, run_id: name,
        response: name + '_COMPLETE', message: value.message, used_memory_ids: [], activity: [] }) + '\n\n')
    }
    return json({ ok: true })
  }
  if (url.pathname === '/api/v1/desktop-smoke/result') {
    let body = ''; for await (const chunk of request) body += chunk
    await writeFile(path.join(output, 'parallel-ui.json'), body + '\n')
    return json({ ok: true })
  }
  if (url.pathname === '/api/v1/threads' && request.method === 'POST') {
    let body = ''; for await (const chunk of request) body += chunk
    const input = JSON.parse(body), id = 'parallel-' + (threads.length + 1)
    const thread = { thread_id: id, title: input.title, mode: input.mode, workspace_id: 'personal', archived: false, created_at: new Date().toISOString() }
    threads.push(thread); return json(thread)
  }
  if (url.pathname === '/api/v1/threads') return json({ items: url.searchParams.get('archived') === 'true' ? [] : threads, next_offset: null })
  if (url.pathname.endsWith('/approval')) return json(null)
  if (url.pathname.endsWith('/messages')) {
    const thread = url.pathname.split('/')[4]
    return json({ items: [...streams].filter(([, value]) => value.thread === thread && value.done).map(([name, value], index) => ({
      id: index + 1, run_id: name, message: value.message, response: name + '_COMPLETE',
      attachments: [], activity: [], used_memory_ids: [],
    })), next_cursor: null })
  }
  if (url.pathname.endsWith('/memory-receipts')) return json({ pending: false, items: [], suggestion_ids: [] })
  if (url.pathname === '/api/v1/chat') {
    let body = ''; for await (const chunk of request) body += chunk
    const input = JSON.parse(body), value = { response, thread: input.thread_id, message: input.message, closed: false, done: false }
    streams.set(input.message, value)
    response.on('close', () => { value.closed = true })
    response.writeHead(200, { 'Content-Type': 'text/event-stream' })
    response.write('event: delta\ndata: ' + JSON.stringify({ text: input.message + '_LIVE' }) + '\n\n')
    return true
  }
  if (url.pathname.endsWith('/resume')) {
    const thread = url.pathname.split('/')[4], previous = [...streams].find(([, value]) => value.thread === thread)
    const name = previous[0] + '_RETRY', value = { response, thread, message: previous[1].message, closed: false, done: false }
    streams.set(name, value)
    response.on('close', () => { value.closed = true })
    response.writeHead(200, { 'Content-Type': 'text/event-stream' })
    response.write('event: delta\ndata: ' + JSON.stringify({ text: name + '_LIVE' }) + '\n\n')
    return true
  }
  return false
} })
await runSmoke({ backend, screenshot: path.join(output, 'parallel-ui.png'), verify: async function () {
  const check = (value, message) => { if (!value) throw Error(message) }
  const wait = async (fn, message) => { for (let n = 0; n < 120; n++) { if (await fn()) return; await new Promise(resolve => setTimeout(resolve, 35)) } throw Error(message) }
  const status = () => fetch('/api/v1/desktop-smoke/parallel').then(r => r.json())
  const create = async name => {
    document.querySelector('.sidebar-new').click()
    await wait(() => document.querySelector('.composer textarea'), 'Composer missing')
    const field = document.querySelector('.composer textarea')
    field.value = name; field.dispatchEvent(new Event('input', { bubbles: true }))
    await new Promise(resolve => requestAnimationFrame(resolve))
    check(!document.querySelector('.composer button[type="submit"]').disabled, 'Another session blocks ' + name)
    field.closest('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    await wait(async () => (await status()).some(s => s.name === name), 'Request did not start: ' + name)
  }
  const select = async name => {
    const button = [...document.querySelectorAll('.sidebar-session')].find(b => b.querySelector('.sidebar-session-title')?.textContent === name)
    check(button, 'Session missing: ' + name); button.click()
    await wait(() => document.querySelector('.chat-turn')?.textContent.includes(name), 'Session did not activate: ' + name)
  }
  await create('PARALLEL_A')
  await create('PARALLEL_B')
  check((await status()).filter(s => !s.closed).length === 2, 'Both sessions must stay connected')
  location.hash = '#settings'
  await wait(() => document.querySelector('.settings-page'), 'Settings navigation failed')
  await select('PARALLEL_A')
  await wait(() => document.body.textContent.includes('PARALLEL_A_LIVE'), 'A lost live text after remount')
  await select('PARALLEL_B')
  await wait(() => document.body.textContent.includes('PARALLEL_B_LIVE'), 'B lost live text after remount')
  document.querySelector('.composer .voice-button').click()
  await wait(async () => (await status()).find(s => s.name === 'PARALLEL_B')?.closed, 'B did not stop')
  check(!(await status()).find(s => s.name === 'PARALLEL_A').closed, 'Stopping B interrupted A')
  await wait(() => document.querySelector('.composer button[type="submit"]'), 'B did not settle after stop')
  const retry = document.querySelector('.composer textarea')
  retry.value = '/retry'; retry.dispatchEvent(new Event('input', { bubbles: true }))
  retry.closest('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
  await wait(async () => (await status()).some(s => s.name === 'PARALLEL_B_RETRY'), 'B retry did not start')
  await create('PARALLEL_C')
  await fetch('/api/v1/desktop-smoke/finish?name=PARALLEL_C')
  await wait(() => document.body.textContent.includes('PARALLEL_C_COMPLETE'), 'C did not finish')
  const field = document.querySelector('.composer textarea')
  field.value = 'KEEP_MY_DRAFT'; field.dispatchEvent(new Event('input', { bubbles: true }))
  await fetch('/api/v1/desktop-smoke/finish?name=PARALLEL_B_RETRY')
  await fetch('/api/v1/desktop-smoke/finish?name=PARALLEL_A')
  await wait(async () => (await status()).find(s => s.name === 'PARALLEL_A')?.done, 'A did not finish in background')
  check(field.value === 'KEEP_MY_DRAFT', 'Background completion changed the active draft')
  await select('PARALLEL_A')
  await wait(() => document.body.textContent.includes('PARALLEL_A_COMPLETE'), 'A final answer was lost')
  check(!document.body.textContent.includes('PARALLEL_C_COMPLETE'), 'Cross-session reply contamination')
  const result = { concurrent: true, navigation: true, isolatedStop: true, concurrentRetry: true, backgroundCompletion: true, draftPreserved: true }
  await fetch('/api/v1/desktop-smoke/result', { method: 'POST', body: JSON.stringify(result) })
  return result
} })
