import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { createServer } from 'node:http'
import { mkdtemp, readFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import electron from 'electron'
import desktop from '../desktop.config.cjs'

const root = fileURLToPath(new URL('..', import.meta.url))

export async function createSmokeBackend() {
  const png = await readFile(path.join(root, 'electron/assets/icon.png'))
  const backend = createServer(async (request, response) => {
    if (request.headers.origin && request.headers.origin !== desktop.apiRequestOrigin) {
      response.writeHead(403).end('Origin rejected by the test API.')
      return
    }
    response.setHeader('Access-Control-Allow-Origin', request.headers.origin || 'null')
    response.setHeader('Access-Control-Allow-Methods', 'GET,POST,OPTIONS')
    response.setHeader('Access-Control-Allow-Headers', 'content-type')
    if (request.method === 'OPTIONS') {
      response.writeHead(204).end()
    } else if (request.url === '/api/v1/desktop-smoke/stream') {
      response.writeHead(200, { 'Content-Type': 'text/event-stream' })
      response.write('event: delta\ndata: {"text":"first"}\n\n')
      setTimeout(() => response.end('event: done\ndata: {"text":"last"}\n\n'), 200)
    } else if (request.url?.startsWith('/api/v1/account/avatar')) {
      response.writeHead(200, { 'Content-Type': 'image/png' }).end(png)
    } else if (request.url === '/api/v1/desktop-smoke/upload') {
      let size = 0
      for await (const chunk of request) size += chunk.length
      response.writeHead(200, { 'Content-Type': 'application/json' }).end(JSON.stringify({ size }))
    } else {
      let body = { ok: true }
      if (request.url?.startsWith('/api/v1/workspaces')) body = { items: [{ id: 'personal', name: 'Personal workspace' }] }
      if (request.url?.startsWith('/api/v1/threads')) body = { items: [], next_offset: null }
      if (request.url === '/api/v1/models') body = { items: [], default: null }
      if (request.url === '/api/v1/settings/providers') body = { items: [], templates: [], setup_required: false }
      if (request.url === '/api/v1/account') body = { display_name: null, handle: null, avatar_version: null }
      response.writeHead(200, { 'Content-Type': 'application/json' }).end(JSON.stringify(body))
    }
  })
  await new Promise(resolve => backend.listen(0, '127.0.0.1', resolve))
  const origin = 'http://127.0.0.1:' + backend.address().port
  return { backend, origin }
}

export async function runSmoke({ devServer, backend: testBackend } = {}) {
  const { backend, origin } = testBackend || await createSmokeBackend()
  const userData = await mkdtemp(path.join(tmpdir(), 'slothy-electron-'))
  const env = {
    ...process.env,
    DESKTOP_USER_DATA: userData,
    DESKTOP_API_ORIGIN: origin,
  }
  if (devServer) env.VITE_DEV_SERVER_URL = devServer
  else delete env.VITE_DEV_SERVER_URL
  delete env.ELECTRON_RUN_AS_NODE

  const executable = electron
  const args = ['.']
  args.push('--remote-debugging-port=0')
  const child = spawn(executable, args, {
    cwd: root, env, stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true,
  })
  let socket
  let timeout
  try {
    const debugging = await new Promise((resolve, reject) => {
      timeout = setTimeout(() => reject(new Error('Electron did not start within 45 seconds.')), 45_000)
      let output = ''
      const inspect = chunk => {
        output += chunk.toString()
        const address = output.match(/DevTools listening on (ws:\/\/127\.0\.0\.1:\d+\/[^\s]+)/)
        if (address) resolve(address[1])
      }
      child.stdout.on('data', inspect)
      child.stderr.on('data', inspect)
      child.once('error', reject)
      child.once('exit', code => reject(new Error('Electron exited early (' + code + '): ' + output.slice(-1500))))
    })
    clearTimeout(timeout)
    const debuggingOrigin = 'http://' + new URL(debugging).host
    let target
    for (let attempt = 0; attempt < 80; attempt++) {
      const pages = await fetch(debuggingOrigin + '/json/list').then(response => response.json())
      target = pages.find(page => page.type === 'page' &&
        (devServer ? page.url.startsWith(devServer) : page.url.startsWith('file:')))
      if (target) break
      await new Promise(resolve => setTimeout(resolve, 100))
    }
    assert.ok(target, 'The desktop renderer did not load.')
    socket = new WebSocket(target.webSocketDebuggerUrl)
    await new Promise((resolve, reject) => {
      socket.addEventListener('open', resolve, { once: true })
      socket.addEventListener('error', reject, { once: true })
    })
    let sequence = 0
    const pending = new Map()
    socket.addEventListener('message', message => {
      const result = JSON.parse(message.data)
      const handler = pending.get(result.id)
      if (!handler) return
      pending.delete(result.id)
      if (result.error) handler.reject(new Error(result.error.message))
      else handler.resolve(result.result)
    })
    const evaluate = async expression => {
      const id = ++sequence
      const deadline = setTimeout(() => {
        pending.get(id)?.reject(new Error('Renderer verification timed out.'))
        pending.delete(id)
      }, 30_000)
      let response
      try {
        response = await new Promise((resolve, reject) => {
          pending.set(id, { resolve, reject })
          socket.send(JSON.stringify({
            id, method: 'Runtime.evaluate',
            params: { expression, awaitPromise: true, returnByValue: true },
          }))
        })
      } finally {
        clearTimeout(deadline)
      }
      if (response.exceptionDetails) {
        throw new Error(response.exceptionDetails.exception?.description || response.exceptionDetails.text)
      }
      return response.result.value
    }
    const apiBase = ''
    let result
    for (let attempt = 0; attempt < 5; attempt++) {
      try {
        result = await evaluate('(' + verifyRenderer.toString() + ')(' + JSON.stringify(apiBase) + ')')
        break
      } catch (error) {
        if (!/Execution context was destroyed|Cannot find context/.test(error.message) || attempt === 4) throw error
        await new Promise(resolve => setTimeout(resolve, 250))
      }
    }
    assert.equal(result.node, 'undefined')
    assert.equal(result.process, 'undefined')
    assert.equal(result.platform, process.platform)
    assert.ok(result.info.version)
    const manifest = JSON.parse(await readFile(path.join(root, 'package.json'), 'utf8'))
    assert.equal(result.info.version, manifest.version)
    assert.equal(result.info.name, desktop.productName)
    assert.ok(result.hasVue)
    assert.ok(result.directoryPicker)
    assert.ok(result.json)
    assert.equal(result.upload, 6)
    assert.ok(result.streaming)
    assert.ok(result.isolatedFrame)
    if (!devServer) assert.equal(result.avatarWidth, 512)
    console.log('Desktop smoke passed:', devServer ? 'Vite development' : 'dist/index.html')
    console.log('Verified Vue, preload whitelist, renderer/frame isolation, JSON API, binary upload, SSE and avatar loading.')
  } finally {
    clearTimeout(timeout)
    socket?.close()
    if (child.exitCode === null && child.pid) {
      if (process.platform === 'win32') {
        const killer = spawn('taskkill', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' })
        await new Promise(resolve => { killer.once('close', resolve); killer.once('error', resolve) })
      } else child.kill('SIGTERM')
    }
    backend.closeAllConnections()
    await new Promise(resolve => backend.close(resolve))
  }
}

async function verifyRenderer(base) {
  for (let attempt = 0; attempt < 100 && (
    !window.desktop || document.readyState !== 'complete' ||
    !document.querySelector('#app')?.children.length
  ); attempt++) {
    await new Promise(resolve => setTimeout(resolve, 50))
  }
  const info = await window.desktop.getAppInfo()
  const response = await fetch(base + '/api/v1/desktop-smoke')
  const json = (await response.json()).ok
  const uploaded = await fetch(base + '/api/v1/desktop-smoke/upload', {
    method: 'POST', body: new Blob(['upload']), headers: { 'Content-Type': 'application/octet-stream' },
  })
  const stream = await fetch(base + '/api/v1/desktop-smoke/stream')
  const reader = stream.body.getReader()
  const first = new TextDecoder().decode((await reader.read()).value)
  let rest = ''
  while (true) {
    const chunk = await reader.read()
    if (chunk.done) break
    rest += new TextDecoder().decode(chunk.value)
  }
  let avatarWidth = 0
  if (location.protocol === 'file:') {
    avatarWidth = await new Promise((resolve, reject) => {
      const avatar = new Image()
      avatar.onload = () => resolve(avatar.naturalWidth)
      avatar.onerror = () => reject(new Error('Avatar image could not load.'))
      avatar.src = '/api/v1/account/avatar'
    })
  }
  const isolatedFrame = await new Promise((resolve, reject) => {
    const iframe = document.createElement('iframe')
    iframe.setAttribute('sandbox', 'allow-scripts')
    const timer = setTimeout(() => reject(new Error('Frame isolation check timed out.')), 3000)
    const receive = event => {
      if (event.source !== iframe.contentWindow || event.data?.kind !== 'desktop-isolation') return
      clearTimeout(timer)
      window.removeEventListener('message', receive)
      iframe.remove()
      resolve(event.data.bridge === 'undefined' && event.data.node === 'undefined')
    }
    window.addEventListener('message', receive)
    iframe.srcdoc = '<script>parent.postMessage({kind:"desktop-isolation",bridge:typeof window.desktop,node:typeof window.require},"*")</script>'
    document.body.append(iframe)
  })
  return {
    node: typeof window.require, process: typeof window.process,
    directoryPicker: typeof window.desktop.selectDirectory === 'function',
    platform: window.desktop.platform, info,
    hasVue: document.querySelector('#app')?.children.length > 0,
    json, upload: (await uploaded.json()).size,
    streaming: first.includes('first') && !first.includes('last') && rest.includes('last'),
    avatarWidth, isolatedFrame,
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  runSmoke().catch(error => {
    console.error(error)
    process.exitCode = 1
  })
}
