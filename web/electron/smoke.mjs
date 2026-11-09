/**
 * Development smoke harness: boots a fake API, launches Electron and verifies the renderer over the DevTools protocol.
 *
 * 开发冒烟测试工具：启动伪 API、拉起 Electron，并通过 DevTools 协议校验渲染进程。
 */

import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { createServer } from 'node:http'
import { mkdtemp, readFile, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import electron from 'electron'
import desktop from '../desktop.config.cjs'

const root = fileURLToPath(new URL('..', import.meta.url))

/**
 * Creates a loopback fake API that answers the endpoints the renderer needs.
 *
 * 创建一个回环地址的伪 API，用于响应渲染进程需要的接口。
 *
 * @param options - Harness options. / 测试参数。
 * @param options.firstRun - Return `setup_required: true` so the onboarding overlay appears. / 为 true 时返回 `setup_required: true`，触发首次运行引导。
 * @returns Fake server handle with `backend` and `origin`. / 伪服务器句柄，包含 `backend` 与 `origin`。
 */
export async function createSmokeBackend({ firstRun = false, handle } = {}) {
  const png = await readFile(path.join(root, 'electron/assets/icon.png'))
  // A single handler mimics the real API surface: SSE, avatar bytes, uploads and JSON reads.
  // 单个处理器模拟真实 API 面：SSE、头像字节、上传与 JSON 读取。
  const backend = createServer(async (request, response) => {
    if (request.headers.origin && request.headers.origin !== desktop.apiRequestOrigin) {
      response.writeHead(403).end('Origin rejected by the test API.')
      return
    }
    response.setHeader('Access-Control-Allow-Origin', request.headers.origin || 'null')
    response.setHeader('Access-Control-Allow-Methods', 'GET,POST,OPTIONS')
    response.setHeader('Access-Control-Allow-Headers', 'content-type')
    if (handle && await handle(request, response)) return
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
    } else if (request.url === '/api/v1/threads/smoke/artifacts/' + 'a'.repeat(64)) {
      response.writeHead(200, { 'Content-Type': 'application/octet-stream' }).end('SLOTHY_ARTIFACT_OK')
    } else {
      let body = { ok: true }
      if (request.url?.startsWith('/api/v1/workspaces')) body = { items: [{ id: 'personal', name: 'Personal workspace' }], next_offset: null }
      if (request.url?.startsWith('/api/v1/threads')) body = { items: [], next_offset: null }
      if (request.url === '/api/v1/models') body = { items: [], default: null }
      if (request.url === '/api/v1/settings/providers') body = { items: [], templates: [], setup_required: firstRun }
      if (request.url === '/api/v1/account') body = { display_name: null, handle: null, avatar_version: null }
      response.writeHead(200, { 'Content-Type': 'application/json' }).end(JSON.stringify(body))
    }
  })
  await new Promise(resolve => backend.listen(0, '127.0.0.1', resolve))
  const origin = 'http://127.0.0.1:' + backend.address().port
  return { backend, origin }
}

/**
 * Launches Electron, drives the renderer through CDP and asserts the desktop contract.
 *
 * 启动 Electron，通过 CDP 驱动渲染进程并断言桌面端契约。
 *
 * @param options - Harness options. / 测试参数。
 * @param options.devServer - Vite dev server URL when testing development mode. / 测试开发模式时使用的 Vite 开发服务器 URL。
 * @param options.backend - Pre-created fake backend; a new one is created when omitted. / 已创建的伪后端；省略时自动创建。
 * @param options.firstRun - Simulate a fresh installation with the onboarding overlay. / 模拟带引导浮层的全新安装。
 * @returns Resolves when every assertion passed. / 全部断言通过后完成。
 */
export async function runSmoke({ devServer, backend: testBackend, firstRun = false, verify, inspect, screenshot, viewport } = {}) {
  const { backend, origin } = testBackend || await createSmokeBackend({ firstRun })
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
    // Send one expression to the page and await its serialized result.
    // 向页面发送一个表达式并等待其序列化结果。
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
    if (viewport) {
      const id = ++sequence
      await new Promise((resolve, reject) => {
        pending.set(id, { resolve, reject })
        socket.send(JSON.stringify({ id, method: 'Emulation.setDeviceMetricsOverride', params: {
          width: viewport.width, height: viewport.height, deviceScaleFactor: 1, mobile: false,
        } }))
      })
    }
    let result
    for (let attempt = 0; attempt < 5; attempt++) {
      try {
        result = await evaluate('(' + verifyRenderer.toString() + ')(' + JSON.stringify(apiBase) + ',' + JSON.stringify(firstRun) + ')')
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
    assert.equal(result.info.version, manifest.displayVersion || manifest.version)
    assert.equal(result.info.name, desktop.productName)
    assert.ok(result.hasVue)
    assert.ok(result.directoryPicker)
    assert.ok(result.json)
    assert.ok(result.artifactDownload)
    assert.equal(result.upload, 6)
    assert.ok(result.streaming)
    assert.ok(result.isolatedFrame)
    assert.equal(result.onboarding, firstRun)
    assert.equal(result.backgroundInert, firstRun)
    assert.equal(result.titlebarControls, 3)
    if (firstRun) {
      assert.equal(result.onboardingTop, 40)
      assert.ok(result.focusInOnboarding)
      assert.ok(result.cardFits)
      assert.notEqual(result.hash, '#settings/api')
    }
    if (!devServer) assert.equal(result.avatarWidth, 512)
    if (verify) console.log('Additional renderer checks:', JSON.stringify(await evaluate('(' + verify.toString() + ')()')))
    if (inspect) await inspect({ evaluate, command: (method, params = {}, sessionId) => {
      const id = ++sequence
      return new Promise((resolve, reject) => {
        pending.set(id, { resolve, reject })
        socket.send(JSON.stringify({ id, method, params, sessionId }))
      })
    } })
    if (screenshot) {
      const id = ++sequence
      const capture = await new Promise((resolve, reject) => {
        pending.set(id, { resolve, reject })
        socket.send(JSON.stringify({ id, method: 'Page.captureScreenshot', params: { format: 'png' } }))
      })
      await writeFile(screenshot, Buffer.from(capture.data, 'base64'))
    }
    console.log('Desktop smoke passed:', devServer ? 'Vite development' : 'dist/index.html')
    console.log('Verified Vue, preload whitelist, renderer/frame isolation, JSON API, binary upload, SSE, avatar loading and ' + (firstRun ? 'first-run overlay below native window controls.' : 'configured startup without onboarding.'))
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

/**
 * Runs inside the page: probes the preload bridge, isolation, HTTP, upload, SSE and avatar behavior.
 *
 * 在页面内运行：探测预加载桥、隔离性、HTTP、上传、SSE 与头像加载行为。
 *
 * @param base - API origin prefix; empty string means same-origin. / API 源前缀；空串表示同源。
 * @param firstRun - Whether the onboarding overlay is expected. / 是否预期出现首次运行引导浮层。
 * @returns Renderer facts asserted by `runSmoke`. / 供 `runSmoke` 断言的渲染进程观测值。
 */
async function verifyRenderer(base, firstRun) {
  for (let attempt = 0; attempt < 100 && (
    !window.desktop || document.readyState !== 'complete' ||
    !document.querySelector('#app')?.children.length
  ); attempt++) {
    await new Promise(resolve => setTimeout(resolve, 50))
  }
  const info = await window.desktop.getAppInfo()
  for (let attempt = 0; attempt < 100 && (document.querySelector('.configuration-startup') ||
    firstRun && !document.querySelector('[data-testid="onboarding-overlay"]')); attempt++) {
    await new Promise(resolve => setTimeout(resolve, 50))
  }
  const response = await fetch(base + '/api/v1/desktop-smoke')
  const json = (await response.json()).ok
  const artifactResponse = await fetch(base + '/api/v1/threads/smoke/artifacts/' + 'a'.repeat(64))
  const artifact = await artifactResponse.blob()
  const artifactUrl = URL.createObjectURL(artifact)
  const artifactDownload = artifactResponse.ok && await artifact.text() === 'SLOTHY_ARTIFACT_OK' && artifactUrl.startsWith('blob:')
  URL.revokeObjectURL(artifactUrl)
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
    onboarding: Boolean(document.querySelector('[data-testid="onboarding-overlay"]')),
    backgroundInert: document.querySelector('.app-shell')?.inert ?? false,
    titlebarControls: document.querySelectorAll('.desktop-window-controls button').length,
    onboardingTop: document.querySelector('.onboarding-overlay')?.getBoundingClientRect().top,
    focusInOnboarding: document.querySelector('.onboarding-card')?.contains(document.activeElement),
    cardFits: (() => { const rect = document.querySelector('.onboarding-card')?.getBoundingClientRect(); return Boolean(rect && rect.top >= 40 && rect.bottom <= innerHeight && rect.left >= 0 && rect.right <= innerWidth) })(),
    hash: location.hash,
    node: typeof window.require, process: typeof window.process,
    directoryPicker: typeof window.desktop.selectDirectory === 'function',
    platform: window.desktop.platform, info,
    hasVue: document.querySelector('#app')?.children.length > 0,
    json, artifactDownload, upload: (await uploaded.json()).size,
    streaming: first.includes('first') && !first.includes('last') && rest.includes('last'),
    avatarWidth, isolatedFrame,
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  runSmoke().then(() => runSmoke({ firstRun: true })).catch(error => {
    console.error(error)
    process.exitCode = 1
  })
}
