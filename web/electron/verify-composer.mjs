// Build-time interactive verification using the existing isolated Electron harness.
// 构建期交互验收，复用既有隔离 Electron 验证器。
import { mkdir } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { createSmokeBackend, runSmoke } from './smoke.mjs'

const web = fileURLToPath(new URL('..', import.meta.url))
const output = path.resolve(web, '../_internal/core-qa/composer')
await mkdir(output, { recursive: true })
const calls = []
let streamIndex = 0
let pendingRun
const archive = []
const fixture = await createSmokeBackend({ handle: async (request, response) => {
  const route = new URL(request.url, 'http://127.0.0.1').pathname
  const json = value => { response.writeHead(200, { 'Content-Type': 'application/json' }).end(JSON.stringify(value)); return true }
  if (route === '/api/v1/desktop-smoke/composer-log') return json(calls)
  if (route.startsWith('/api/v1/desktop-smoke')) return false
  if (route === '/api/v1/models') return json({ default: 'model-a', items: ['model-a', 'model-b'].map(id => ({ id, provider: 'test', reasoning_levels: [], default_effort: null })) })
  if (route.startsWith('/api/v1/references/')) return json({ items: route.endsWith('/files') ? [{ id: 'guide.md', name: 'guide.md', path: 'guide.md', source: 'workspace', bytes: 12 }] : [] })
  if (route.endsWith('/approval')) return json(null)
  if (route === '/api/v1/threads/thread-ui/messages') return json({ items: archive, next_cursor: null })
  if (route.includes('/artifacts/') && route.includes('thread-ui')) {
    response.writeHead(200, { 'Content-Type': 'application/octet-stream' }).end('EXPORT_FIXTURE')
    return true
  }
  if (request.method !== 'POST' && request.method !== 'PATCH') return false
  let raw = ''
  for await (const part of request) raw += part
  const body = raw ? JSON.parse(raw) : {}
  calls.push({ route, body })
  if (route === '/api/v1/threads') return json({ thread_id: 'thread-ui', title: '', mode: 'chat' })
  if (route.endsWith('/compact')) return json({ compacted: true, version: 1, summary: 'Context compacted / 已压缩上下文' })
  if (route.endsWith('/export')) return json({ ok: true, summary: 'Exported conversation.docx', data: {}, artifacts: [{ id: 'a'.repeat(64), name: 'conversation.docx', bytes: 14, rel_path: 'managed/conversation.docx', url: '/api/v1/threads/thread-ui/artifacts/' + 'a'.repeat(64) }] })
  if (route === '/api/v1/chat' || route.endsWith('/resume')) {
    if (pendingRun && route === '/api/v1/chat') {
      response.writeHead(409, { 'Content-Type': 'application/json' }).end(JSON.stringify({ code: 'RUN_PENDING', message: 'Resume the original request first.' }))
      return true
    }
    response.writeHead(200, { 'Content-Type': 'text/event-stream' })
    const run = route.endsWith('/resume') ? pendingRun.run : 'run-' + ++streamIndex
    const original = route.endsWith('/resume') ? pendingRun.body : body
    response.write('event: activity\ndata: ' + JSON.stringify({ run_id: run, items: [], metrics: null }) + '\n\n')
    if (body.message === 'HOLD') {
      pendingRun = { run, body }
      const timer = setTimeout(() => response.end(), 30000)
      response.on('close', () => clearTimeout(timer))
      return true
    }
    const result = { run_id: run, thread_id: 'thread-ui', message: original.message,
      response: route.endsWith('/resume') ? 'RESUMED_REPLY' : 'MODEL_REPLY', used_memory_ids: [],
      references: (original.references || []).map(item => ({ ...item, label: 'guide.md', status: 'ok' })) }
    archive.push({ ...result, id: archive.length + 1, attachments: [] })
    pendingRun = undefined
    response.end('event: done\ndata: ' + JSON.stringify(result) + '\n\n')
    return true
  }
  return json({ ok: true })
} })

await runSmoke({ backend: fixture, screenshot: path.join(output, 'commands-dark.png'), verify: async function () {
  const check = (value, message) => { if (!value) throw new Error(message) }
  const until = async (condition, message) => {
    for (let index = 0; index < 120; index++) { if (condition()) return; await new Promise(resolve => setTimeout(resolve, 40)) }
    throw new Error(message)
  }
  await until(() => document.querySelector('.sidebar-new') && !document.querySelector('.sidebar-new').disabled, 'Workspaces did not finish loading')
  document.querySelector('.sidebar-new').click()
  await until(() => document.querySelector('.composer textarea'), 'Composer did not open')
  let field = document.querySelector('.composer textarea')
  const type = value => {
    field.focus(); field.value = value; field.setSelectionRange(value.length, value.length)
    field.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: value }))
  }
  const key = value => field.dispatchEvent(new KeyboardEvent('keydown', { key: value, bubbles: true, cancelable: true }))
  const palette = () => document.querySelector('.composer-palette')
  const option = value => Array.from(document.querySelectorAll('.palette-option')).find(item => item.textContent.includes('/' + value))
  const log = () => fetch('/api/v1/desktop-smoke/composer-log').then(response => response.json())
  const enterCommand = async value => {
    type('/' + value)
    if (!/^(?:retry|rename)\s/.test(value)) await until(() => palette() && !document.querySelector('.palette-options[aria-busy="true"]'), 'Command suggestions did not load')
    key('Enter'); await new Promise(resolve => setTimeout(resolve, 60))
  }
  document.querySelector('.composer-add').click()
  await until(() => option('attach'), 'Plus menu did not show file upload')
  check(document.activeElement === field, 'Plus menu moved input focus')
  check(document.querySelectorAll('.palette-option').length >= 20, 'Missing existing commands')
  check(palette().getBoundingClientRect().top >= 40, 'Welcome palette overlaps the titlebar')
  key('Escape'); await until(() => !palette(), 'Escape did not close menu')
  type('/')
  await until(() => option('new'), 'Slash did not show commands')
  key('End'); await until(() => field.getAttribute('aria-activedescendant') !== 'composer-option-0', 'End did not navigate')
  key('Home'); key('ArrowDown'); key('Escape')
  type('/help')
  field.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertFromPaste' }))
  await new Promise(resolve => setTimeout(resolve, 150))
  check(!palette(), 'Pasting opened suggestions')
  await enterCommand('model model-b')
  check(document.querySelector('.model-trigger').textContent.includes('model-b'), 'Model command did not reuse picker state')
  document.querySelector('.composer-command-notice header button')?.click()
  await until(() => !document.querySelector('.composer-command-notice'), 'Model status did not dismiss')
  type('/help')
  field.dispatchEvent(new CompositionEvent('compositionstart', { bubbles: true }))
  key('Enter')
  check(!document.querySelector('.composer-command-notice'), 'IME Enter executed a command')
  field.dispatchEvent(new CompositionEvent('compositionend', { bubbles: true, data: '' }))
  await until(() => option('help'), 'IME completion did not restore suggestions')
  key('Enter')
  await until(() => document.querySelector('.composer-command-notice'), 'Help command failed')
  document.querySelector('.composer-command-notice header button').click()
  await enterCommand('does-not-exist')
  check(document.querySelector('[role="alert"]')?.textContent.includes('未知'), 'Unknown command was not rejected')
  check(field.value === '/does-not-exist', 'Invalid command discarded the editable draft')
  check(!(await log()).some(item => item.route === '/api/v1/chat'), 'Commands were sent to the model')
  const fileInput = document.querySelector('.composer input[type="file"]')
  let pickerCalls = 0
  fileInput.click = () => pickerCalls++
  document.querySelector('.composer-add').click()
  await until(() => option('attach'), 'File action missing')
  option('attach').click()
  await until(() => pickerCalls === 1, 'Existing attachment picker was not invoked')
  const data = new DataTransfer(); data.items.add(new File(['fixture'], 'fixture.txt'))
  fileInput.files = data.files; fileInput.dispatchEvent(new Event('change', { bubbles: true }))
  await until(() => document.querySelector('.attachment-list'), 'Chosen file missing')
  await enterCommand('clear')
  check(!document.querySelector('.attachment-list'), 'Clear command did not clear the draft files')
  type('@file:guide')
  await until(() => document.querySelector('.palette-option')?.textContent.includes('guide.md'), 'File reference search failed')
  key('Tab')
  await until(() => document.querySelector('.composer-references .reference-chip'), 'Reference chip missing')
  key('ArrowLeft')
  await until(() => document.activeElement === document.querySelector('.composer-references button'), 'Could not navigate to a reference chip')
  document.activeElement.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true, cancelable: true }))
  await until(() => document.activeElement === field, 'Could not return from the reference chip')
  key('Backspace')
  await until(() => !document.querySelector('.composer-references'), 'Backspace did not remove the whole reference')
  type('@file:guide')
  await until(() => document.querySelector('.palette-option')?.textContent.includes('guide.md'), 'Reference search did not reopen')
  key('Tab')
  await until(() => document.querySelector('.composer-references .reference-chip'), 'Reference could not be added again')
  type('Use this reference'); key('Enter')
  await until(() => document.querySelector('.answer')?.textContent.includes('MODEL_REPLY'), 'Reference request did not complete')
  const sent = (await log()).find(item => item.route === '/api/v1/chat').body
  check(sent.model === 'model-b' && sent.references[0].id === 'guide.md' && !('label' in sent.references[0]), 'Reference request did not use the existing transport and identity schema')
  await enterCommand('compact')
  check(document.querySelector('.composer-command-card[role="region"]'), 'Compact confirmation missing')
  check(!(await log()).some(item => item.route.endsWith('/compact')), 'Compact ran without confirmation')
  document.querySelector('.composer-command-card[role="region"] > div button:last-child').click()
  await until(() => document.querySelector('.composer-command-notice')?.textContent.includes('已压缩'), 'Compact did not complete')
  await enterCommand('export docx')
  check(!(await log()).some(item => item.route.endsWith('/export')), 'Export ran without confirmation')
  document.querySelector('.composer-command-card[role="region"] > div button:last-child').click()
  await until(() => document.querySelector('.composer-command-notice')?.textContent.includes('conversation.docx'), 'Export did not return a downloadable artifact')
  const click = HTMLAnchorElement.prototype.click
  let downloaded = ''
  HTMLAnchorElement.prototype.click = function () { downloaded = this.download }
  document.querySelector('.composer-command-notice > button').click()
  await until(() => downloaded === 'conversation.docx', 'Export download failed')
  HTMLAnchorElement.prototype.click = click
  type('HOLD'); key('Enter')
  await until(() => document.querySelector('button[aria-label="停止运行"]'), 'Stop action not available')
  location.hash = 'settings'
  await until(() => !document.querySelector('.composer textarea'), 'Navigation did not leave the conversation')
  location.hash = 'workspace'
  await until(() => document.querySelector('.composer textarea'), 'Conversation did not return')
  field = document.querySelector('.composer textarea')
  await until(() => document.querySelector('button[aria-label="停止运行"]'), 'Stop request handle was lost during navigation')
  await enterCommand('stop')
  await until(() => document.querySelector('[role="alert"]')?.textContent.includes('已停止'), 'Stop did not cancel the request')
  await enterCommand('retry Edited request')
  await until(() => document.body.textContent.includes('编辑后的内容不能替换检查点'), 'Edited retry did not preserve the pending original run')
  await enterCommand('retry')
  await until(() => document.body.textContent.includes('RESUMED_REPLY'), 'Retry did not resume the interrupted run')
  const resumed = Array.from(document.querySelectorAll('article')).find(item => item.textContent.includes('RESUMED_REPLY'))
  check(resumed?.textContent.includes('HOLD') && !resumed.textContent.includes('Edited request'), 'Resume associated the answer with edited input')
  type('Ask @missing'); key('Enter')
  await until(() => document.querySelector('[aria-label="未解析的引用"]'), 'Unresolved reference confirmation missing')
  const remove = document.querySelector('[aria-label="未解析的引用"] > div button:nth-child(2)')
  remove.click()
  await until(() => !field.value.includes('@missing'), 'Unresolved reference was not removed')
  await enterCommand('clear')
  document.querySelector('.composer-command-notice header button')?.click()
  await enterCommand('new')
  document.querySelector('.composer-add').click()
  await until(() => option('attach'), 'Final menu did not open')
  const panel = palette().getBoundingClientRect()
  check(panel.top >= 40 && panel.right <= innerWidth && panel.left >= 0, 'Palette is clipped or outside the viewport')
  return { commands: 20, keyboard: true, ime: true, references: true, confirmation: true, stopped: true, resumed: true, authenticatedDownload: true }
} })

await runSmoke({ screenshot: path.join(output, 'commands-light.png'), verify: async function () {
  const until = async (condition, message) => {
    for (let index = 0; index < 100; index++) { if (condition()) return; await new Promise(resolve => setTimeout(resolve, 40)) }
    throw new Error(message)
  }
  await until(() => document.querySelector('.sidebar-new') && !document.querySelector('.sidebar-new').disabled, 'Workspace unavailable')
  document.querySelector('.sidebar-new').click()
  await until(() => document.querySelector('.composer textarea'), 'Composer unavailable')
  const input = document.querySelector('.composer textarea')
  input.focus(); input.value = '/theme light'; input.setSelectionRange(input.value.length, input.value.length)
  input.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText' }))
  await until(() => document.querySelector('.palette-option'), 'Theme options unavailable')
  input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }))
  await until(() => document.documentElement.dataset.mode === 'light', 'Theme command failed')
  document.querySelector('.composer-command-notice header button')?.click()
  document.querySelector('.composer-add').click()
  await until(() => document.querySelector('.palette-option'), 'Add menu unavailable')
  const rectangle = document.querySelector('.composer-palette').getBoundingClientRect()
  if (rectangle.top < 40 || rectangle.right > innerWidth) throw new Error('Light theme palette is clipped')
  return { lightTheme: true, fitsViewport: true }
} })
