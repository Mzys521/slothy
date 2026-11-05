import { spawn } from 'node:child_process'
import { parseArgs } from 'node:util'
import { fileURLToPath } from 'node:url'
import { createServer } from 'vite'
import electron from 'electron'
import desktop from '../desktop.config.cjs'
import { createSmokeBackend, runSmoke } from './smoke.mjs'

const root = fileURLToPath(new URL('..', import.meta.url))
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

async function stop(code = 0) {
  if (stopping) return
  stopping = true
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
      ...(testBackend && { proxy: { '/api': { target: testBackend.origin } } }),
    },
  })
  await server.listen()
  server.printUrls()
  const env = { ...process.env, VITE_DEV_SERVER_URL: server.resolvedUrls.local[0] }
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
