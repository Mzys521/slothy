/**
 * Production packaging audit: proves the packaged app.asar and bundled backend contain no development or test artifacts.
 *
 * 生产打包审计：证明打包后的 app.asar 与随附后端中不包含开发或测试产物。
 */

const { readdir, readFile, stat } = require('node:fs/promises')
const path = require('node:path')
const { execFile } = require('node:child_process')
const { promisify } = require('node:util')

/**
 * Audits an unpacked Electron build directory.
 *
 * 审计一个已解包的 Electron 构建目录。
 *
 * @param appOutDir - Unpacked application directory, for example `release/slothy/win-unpacked`. / 已解包的应用目录，例如 `release/slothy/win-unpacked`。
 * @returns Resolves after every production check passed. / 所有生产校验通过后完成。
 */
async function audit(appOutDir) {
  const asar = await import('@electron/asar')
  const resources = path.join(appOutDir, 'resources')
  const archive = path.join(resources, 'app.asar')
  const entries = asar.listPackage(archive).map(name => name.replaceAll('\\', '/'))
  // Any test, source, env or database file inside the archive is a packaging defect.
  // 归档内出现测试、源码、环境或数据库文件都视为打包缺陷。
  const forbidden = /(?:^|\/)(?:tests?|fixtures|__mocks__|node_modules|src|\.env(?:\.[^/]*)?|vite\.config[^/]*|tsconfig[^/]*|smoke\.[^/]*|dev\.mjs|.*\.map|.*\.sqlite(?:-wal|-shm)?|.*\.db)(?:\/|$)/i
  const invalid = entries.filter(name => forbidden.test(name))
  if (invalid.length) throw new Error('Non-production files in app.asar: ' + invalid.slice(0, 15).join(', '))
  // These four entries are the minimum runnable desktop application.
  // 这四项是桌面应用可运行的最小入口集合。
  for (const name of ['/dist/index.html', '/electron/main.cjs', '/electron/preload.cjs', '/electron/backend.cjs']) {
    if (!entries.includes(name)) throw new Error('Missing production entry: ' + name)
  }
  const metadata = JSON.parse(asar.extractFile(archive, 'package.json').toString())
  if (metadata.name !== 'slothy' || metadata.devDependencies && Object.keys(metadata.devDependencies).length) {
    throw new Error('The production manifest contains a wrong brand or development dependencies.')
  }
  // Backend resources must not contain test runners, Python tooling or build specs.
  // 后端资源中不允许出现测试运行器、Python 工具链或构建 spec。
  const prohibited = /(?:^|\/)(?:tests?|testing|fixtures|pytest(?:[^/]*)?|_pytest|pytest_asyncio|ruff|pip|ensurepip|setuptools|PyInstaller|debugpy|pdb|bdb|\.env(?:\.[^/]*)?|.*\.sqlite(?:-wal|-shm)?|.*\.spec)(?:\/|$)/i
  let count = 0
  /**
   * Recursively rejects prohibited backend files and counts the remaining ones.
   *
   * 递归拒绝禁止的后端文件并统计其余文件数量。
   *
   * @param directory - Directory currently being inspected. / 当前正在检查的目录。
   */
  async function inspect(directory) {
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      const filename = path.join(directory, entry.name)
      const relative = path.relative(resources, filename).replaceAll('\\', '/')
      if (prohibited.test(relative)) throw new Error('Non-production backend file: ' + relative)
      if (entry.isDirectory()) await inspect(filename)
      else count++
    }
  }
  const backend = path.join(resources, 'backend/slothy-service.exe')
  if (!(await stat(backend)).isFile()) throw new Error('The independent backend is missing.')
  await inspect(path.join(resources, 'backend'))
  // Ask the embedded Python interpreter to audit its own frozen bundle.
  // 让内嵌 Python 解释器审计其自身的冻结包。
  const core = path.resolve(__dirname, '../../core')
  const python = process.env.SLOTHY_BUILD_PYTHON || path.join(core, '.venv/Scripts/python.exe')
  const pythonAudit = await promisify(execFile)(python, [path.join(core, 'desktop/audit_bundle.py'), backend], { windowsHide: true })
  console.log('Embedded Python audit:', pythonAudit.stdout.trim())
  console.log(`Production audit passed: slothy, ${entries.length} app entries, ${count} backend files; no development data or test entry points.`)
}

/**
 * electron-builder style after-pack hook.
 *
 * electron-builder 风格的 after-pack 钩子。
 *
 * @param context - Builder context exposing `appOutDir`. / 构建上下文，提供 `appOutDir`。
 * @returns The audit result. / 审计结果。
 */
module.exports = async context => audit(context.appOutDir)
module.exports.audit = audit
