const { spawn } = require('node:child_process')
const { randomBytes } = require('node:crypto')
const path = require('node:path')

// This module is shipped; build and verification entry points are never packaged.
async function startBackend({ resourcesPath, userData, development }) {
  const token = randomBytes(32).toString('hex')
  const executable = development
    ? path.resolve(__dirname, '../../core/.venv/Scripts/python.exe')
    : path.join(resourcesPath, 'backend', 'slothy-service.exe')
  const args = development ? [path.resolve(__dirname, '../../core/desktop/entry.py')] : []
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

async function stopBackend(backend) {
  if (!backend || backend.child.exitCode !== null) return
  const child = backend.child
  if (process.platform === 'win32') {
    await new Promise(resolve => {
      const killer = spawn('taskkill', ['/pid', String(child.pid), '/t', '/f'], { windowsHide: true, stdio: 'ignore' })
      killer.once('exit', resolve)
      killer.once('error', () => { child.kill(); resolve() })
    })
  } else child.kill('SIGTERM')
}

module.exports = { startBackend, stopBackend }
