/** Checkpoint recovery after a renderer restart, through real Vue components.
 * / 通过真实 Vue 组件验证渲染进程重启后的检查点恢复。 */
import assert from 'node:assert/strict'
import { mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { createSmokeBackend, runSmoke } from './smoke.mjs'

const output = path.resolve(fileURLToPath(new URL('..', import.meta.url)), '../_internal/core-qa/checkpoint-resume')
await mkdir(output, { recursive: true })
const requests = [], archives = new Map(), streams = new Map()
const ids = ['checkpoint', 'lost-ack']
const saved = id => ({ run_id: id + '-run', checkpoint_id: id + '-v1', message: 'ORIGINAL_' + id,
  status: 'interrupted', attachments: [{ id: 'saved-file', filename: 'saved.txt', byte_size: 12 }],
  references: [], model_selection: { id: 'original-model', effort: 'high' }, personalization_enabled: false,
  activity: [{ id: 'first-tool', kind: 'tool', name: 'workspace_read', risk: 'read', status: 'done', result: 'SAVED_TOOL_RESULT' }] })
const backend = await createSmokeBackend({ handle: async (request, response) => {
  const url = new URL(request.url, 'http://localhost')
  const json = body => { response.writeHead(200, { 'Content-Type': 'application/json' }).end(JSON.stringify(body)); return true }
  if (url.pathname === '/api/v1/desktop-smoke/recovery-state') return json(requests)
  if (url.pathname === '/api/v1/desktop-smoke/finish') {
    const id = url.searchParams.get('id'), stream = streams.get(id)
    const result = { run_id: id + '-run', message: saved(id).message, response: 'COMPLETED_' + id,
      attachments: saved(id).attachments, activity: [], used_memory_ids: [] }
    archives.set(id, result)
    if (id === 'lost-ack') stream.end()
    else stream.end('event: done\ndata: ' + JSON.stringify(result) + '\n\n')
    return json({ ok: true })
  }
  if (url.pathname === '/api/v1/models') return json({ default: 'other-model', items: ['other-model', 'original-model'].map(id => ({ id, provider: 'test', reasoning_levels: ['none', 'high'], default_effort: 'none' })) })
  if (url.pathname === '/api/v1/threads') return json({ items: url.searchParams.get('archived') === 'true' ? [] : ids.map(id => ({ thread_id: id, title: id, mode: 'work', workspace_id: 'personal', archived: false })), next_offset: null })
  const id = ids.find(id => url.pathname.startsWith('/api/v1/threads/' + id + '/'))
  if (id && url.pathname.endsWith('/messages')) return json({ items: archives.has(id) ? [archives.get(id)] : [], next_cursor: null, pending_run: archives.has(id) ? null : saved(id) })
  if (url.pathname.endsWith('/approval')) return json(null)
  if (id && url.pathname.endsWith('/resume')) {
    let body = ''; for await (const chunk of request) body += chunk
    const input = JSON.parse(body || '{}')
    requests.push({ kind: 'resume', id, stream: url.searchParams.get('stream'), checkpoint: url.searchParams.get('checkpoint_id'), input })
    response.writeHead(200, { 'Content-Type': 'text/event-stream' })
    response.write('event: activity\ndata: ' + JSON.stringify({ run_id: id + '-run', items: saved(id).activity }) + '\n\n')
    response.write('event: delta\ndata: ' + JSON.stringify({ text: 'RESUMING_' + id }) + '\n\n')
    streams.set(id, response)
    return true
  }
  if (url.pathname === '/api/v1/chat') {
    requests.push({ kind: 'chat' }); response.writeHead(409).end(); return true
  }
  return false
} })

async function verifyAfterRestart() {
  const check = (ok, message) => { if (!ok) throw Error(message) }
  const wait = async fn => { for (let n = 0; n < 150; n++) { if (await fn()) return; await new Promise(resolve => setTimeout(resolve, 30)) } throw Error('Recovery UI did not reach expected state') }
  const state = () => fetch('/api/v1/desktop-smoke/recovery-state').then(r => r.json())
  for (const id of ['checkpoint', 'lost-ack']) {
    const button = [...document.querySelectorAll('.sidebar-session')].find(button => button.querySelector('.sidebar-session-title')?.textContent.trim() === id)
    check(button, 'Missing server conversation'); button.click()
    await wait(() => document.body.textContent.includes('ORIGINAL_' + id) && document.querySelector('.checkpoint-resume'))
    check(document.body.textContent.includes('saved.txt'), 'Checkpoint attachment was not restored')
    const input = document.querySelector('.composer textarea')
    input.value = 'KEEP_MY_NEXT_DRAFT'; input.dispatchEvent(new Event('input', { bubbles: true }))
    document.querySelector('.checkpoint-resume').click()
    await wait(async () => (await state()).some(item => item.id === id))
    await wait(() => document.body.textContent.includes('RESUMING_' + id))
    input.value = 'DRAFT_TYPED_WHILE_RESUMING'; input.dispatchEvent(new Event('input', { bubbles: true }))
    await fetch('/api/v1/desktop-smoke/finish?id=' + id)
    await wait(() => document.body.textContent.includes('COMPLETED_' + id) && !document.querySelector('.checkpoint-resume'))
    check(input.value === 'DRAFT_TYPED_WHILE_RESUMING', 'Resumption destroyed a new draft')
    check(!document.body.textContent.includes('stream ended before'), 'Lost completion was not reconciled from the archive')
    const turns = [...document.querySelectorAll('.chat-turn')]
    check(turns.length === 1 && turns[0].textContent.includes('ORIGINAL_' + id), 'Resume duplicated or rewrote the original request')
  }
  const requests = await state()
  check(requests.length === 2 && requests.every(item => item.kind === 'resume'), 'Resume fell back to a new chat request')
  check(requests.every(item => item.stream === 'true' && item.checkpoint === item.id + '-v1' && Object.keys(item.input).length === 0), 'Resume failed to use the saved checkpoint and streaming query')
  return { rendererRestart: true, originalRequest: true, attachments: true, streaming: true, draftPreserved: true, lostAcknowledgement: true, noResend: true, requests }
}

await runSmoke({ backend, screenshot: path.join(output, 'recovery.png'), inspect: async ({ evaluate, command }) => {
  await evaluate("localStorage.removeItem('adaptive.sessions');localStorage.removeItem('adaptive.activeSession');location.hash='#';true")
  await command('Page.reload')
  for (let attempt = 0; attempt < 100; attempt++) {
    try { if (await evaluate("document.querySelectorAll('.sidebar-session').length===2")) break } catch (error) { if (!/Execution context|Cannot find context/.test(error.message)) throw error }
    await new Promise(resolve => setTimeout(resolve, 50))
  }
  const result = await evaluate('(' + verifyAfterRestart.toString() + ')()')
  assert.ok(result.noResend && result.rendererRestart)
  await writeFile(path.join(output, 'recovery.json'), JSON.stringify(result, null, 2) + '\n')
  console.log('Checkpoint renderer checks:', JSON.stringify(result))
} })
