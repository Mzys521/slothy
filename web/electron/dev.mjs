/**
 * Development launcher: starts the Vite dev server, then spawns Electron, with optional smoke-test backend mode.
 *
 * 开发启动器：先启动 Vite 开发服务器，再启动 Electron；支持可选的冒烟测试后端模式。
 */

import { spawn } from 'node:child_process'
import { parseArgs } from 'node:util'
import { fileURLToPath } from 'node:url'
import { createServer } from 'vite'
import electron from 'electron'
import desktop from '../desktop.config.cjs'
import { createSmokeBackend, runSmoke } from './smoke.mjs'

const root = fileURLToPath(new URL('..', import.meta.url))
// CLI flags: --host, --port, --strictPort and --smoke.
// 命令行参数：--host、--port、--strictPort 与 --smoke。
const { values } = parseArgs({
  options: {
    host: { type: 'string', default: '127.0.0.1' },
    port: { type: 'string', default: String(desktop.devPort) },
    strictPort: { type: 'boolean', default: false },
    smoke: { type: 'boolean', default: false },
  },
})

let server
let child
let testBackend
let stopping = false

/**
 * Tears down the Electron child, the Vite server and the smoke backend exactly once.
 *
 * 只执行一次地清理 Electron 子进程、Vite 服务器与冒烟后端。
 *
 * @param code - Process exit code recorded on completion. / 完成时记录的进程退出码。
 * @returns Resolves when every resource is released. / 所有资源释放后完成。
 */
async function stop(code = 0) {
  if (stopping) return
  stopping = true
  // Kill the whole Electron tree on Windows so renderer helpers do not linger.
  // 在 Windows 上结束整棵 Electron 进程树，避免渲染辅助进程残留。
  if (child && child.exitCode === null) {
    if (process.platform === 'win32') {
      const taskkill = spawn('taskkill', ['/PID', String(child.pid), '/T', '/F'], {
        windowsHide: true, stdio: 'ignore',
      })
      await new Promise(resolve => { taskkill.once('close', resolve); taskkill.once('error', resolve) })
    } else child.kill('SIGTERM')
  }
  await server?.close()
  if (testBackend?.backend.listening) {
    testBackend.backend.closeAllConnections()
    await new Promise(resolve => testBackend.backend.close(resolve))
  }
  process.exitCode = code
}

process.once('SIGINT', () => void stop())
process.once('SIGTERM', () => void stop())

try {
  if (values.smoke) testBackend = await createSmokeBackend()
  server = await createServer({
    root,
    server: {
      host: values.host, port: Number(values.port), strictPort: values.strictPort,
      // Smoke mode proxies the API to the in-process fake backend.
      // 冒烟模式下把 API 代理到进程内的伪后端。
      ...(testBackend && { proxy: { '/api': { target: testBackend.origin } } }),
    },
  })
  await server.listen()
  server.printUrls()
  const env = { ...process.env, VITE_DEV_SERVER_URL: server.resolvedUrls.local[0] }
  // Electron must run as a GUI app, not as a Node script, inside this Node process.
  // 在本 Node 进程内启动 Electron 时必须作为 GUI 应用而非 Node 脚本运行。
  delete env.ELECTRON_RUN_AS_NODE
  if (values.smoke) {
    await runSmoke({ devServer: env.VITE_DEV_SERVER_URL, backend: testBackend })
    await stop()
  } else {
    child = spawn(electron, ['.'], { cwd: root, env, stdio: 'inherit', windowsHide: false })
    child.once('error', error => { console.error(error.message); void stop(1) })
    child.once('exit', code => void stop(code ?? 1))
  }
} catch (error) {
  console.error(error.message)
  await stop(1)
}
