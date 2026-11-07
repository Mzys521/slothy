/**
 * Spawns, handshakes with and stops the bundled FastAPI backend child process.
 *
 * 启动、握手并停止随附的 FastAPI 后端子进程。
 */

const { spawn } = require('node:child_process')
const { randomBytes } = require('node:crypto')
const path = require('node:path')

// This module is shipped; build and verification entry points are never packaged.
// 本模块会随包发布；构建与验证入口永远不会被打包。
/**
 * Starts the backend and waits until it is listening and healthy.
 *
 * 启动后端，并等待其进入监听且健康就绪的状态。
 *
 * @param options - Startup options. / 启动选项。
 * @param options.resourcesPath - Electron `process.resourcesPath`, used to locate the frozen service. / Electron `process.resourcesPath`，用于定位冻结服务。
 * @param options.userData - Per-user data directory; the backend stores its data under `data/`. / 每用户数据目录，后端数据放在其 `data/` 子目录。
 * @param options.development - Run the Python entry point from the virtualenv instead of the packaged exe. / 为 true 时使用虚拟环境中的 Python 入口而非打包 exe。
 * @returns Backend handle with `child`, `origin`, `token` and `getLastError()`. / 后端句柄，包含 `child`、`origin`、`token` 与 `getLastError()`。
 */
async function startBackend({ resourcesPath, userData, development }) {
  // A fresh random token authorizes every API request for this process lifetime.
  // 每次启动生成随机令牌，仅本次进程生命周期内授权 API 请求。
  const token = randomBytes(32).toString('hex')
  const executable = development
    ? path.resolve(__dirname, '../../core/.venv/Scripts/python.exe')
    : path.join(resourcesPath, 'backend', 'slothy-service.exe')
  const args = development ? [path.resolve(__dirname, '../../core/desktop/entry.py')] : []
  // Pass through only OS plumbing variables so inherited developer credentials never reach the backend.
  // 只透传操作系统必需变量，避免继承的开发凭据进入后端。
  const env = Object.fromEntries(Object.entries(process.env).filter(([name]) =>
    ['PATH', 'PATHEXT', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'USERPROFILE', 'APPDATA',
      'LOCALAPPDATA', 'COMSPEC', 'PROCESSOR_ARCHITECTURE', 'NUMBER_OF_PROCESSORS'].includes(name.toUpperCase())))
  Object.assign(env, {
    SLOTHY_DESKTOP: '1', SLOTHY_DATA_DIR: path.join(userData, 'data'), SLOTHY_API_TOKEN: token,
    SLOTHY_PARENT_PID: String(process.pid),
    PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8',
  })
  if (development) env.PYTHONPATH = path.resolve(__dirname, '../../core')
  const child = spawn(executable, args, { env, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] })
  let lastError = ''
  child.stderr.on('data', chunk => { lastError = (lastError + chunk.toString()).slice(-3000) })
  try {
    // The backend prints a single JSON line `{"event":"listening","port":N}` once bound.
    // 后端绑定端口后会打印一行 JSON：`{"event":"listening","port":N}`。
    const origin = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('slothy 服务启动超时。' + lastError)), 60_000)
      let output = ''
      child.once('error', error => { clearTimeout(timer); reject(error) })
      child.once('exit', code => { clearTimeout(timer); reject(new Error('slothy 服务启动失败 (' + code + ')。' + lastError)) })
      child.stdout.on('data', chunk => {
        output += chunk.toString()
        const lines = output.split(/\r?\n/)
        output = lines.pop()
        for (const line of lines) {
          try {
            const message = JSON.parse(line)
            if (message.event === 'listening' && Number.isInteger(message.port) && message.port > 0 && message.port < 65536) {
              clearTimeout(timer)
              resolve('http://127.0.0.1:' + message.port)
            }
          } catch { /* uvicorn diagnostics are not startup messages. */ }
        }
      })
    })
    // Poll the health endpoint while FastAPI finishes its lifespan initialization.
    // 在 FastAPI 完成 lifespan 初始化期间轮询健康检查接口。
    let ready = false
    for (let attempt = 0; attempt < 120; attempt++) {
      if (child.exitCode !== null) throw new Error('slothy 服务已退出。' + lastError)
      try {
        const response = await fetch(origin + '/api/v1/health', {
          headers: { 'X-Slothy-Token': token }, signal: AbortSignal.timeout(1000),
        })
        if (response.ok) { ready = true; break }
      } catch { /* Keep waiting for FastAPI's lifespan initialization. */ }
      await new Promise(resolve => setTimeout(resolve, 200))
    }
    if (!ready) throw new Error('slothy 服务未能完成初始化。' + lastError)
    return { child, origin, token, getLastError: () => lastError }
  } catch (error) {
    child.kill()
    throw error
  }
}

/**
 * Stops the backend process tree, force-killing it on Windows.
 *
 * 停止后端进程树；在 Windows 上强制结束。
 *
 * @param backend - Handle returned by `startBackend`; ignored when already exited. / `startBackend` 返回的句柄；已退出时忽略。
 * @returns Resolves once the process tree is gone. / 进程树结束后完成。
 */
async function stopBackend(backend) {
  if (!backend || backend.child.exitCode !== null) return
  const child = backend.child
  // `taskkill /t` also reaps Python child workers that would otherwise survive.
  // `taskkill /t` 同时清理否则会残留的 Python 子工作进程。
  if (process.platform === 'win32') {
    await new Promise(resolve => {
      const killer = spawn('taskkill', ['/pid', String(child.pid), '/t', '/f'], { windowsHide: true, stdio: 'ignore' })
      killer.once('exit', resolve)
      killer.once('error', () => { child.kill(); resolve() })
    })
  } else child.kill('SIGTERM')
}

module.exports = { startBackend, stopBackend }
