const { readdir, readFile, stat } = require('node:fs/promises')
const path = require('node:path')
const { execFile } = require('node:child_process')
const { promisify } = require('node:util')

async function audit(appOutDir) {
  const asar = await import('@electron/asar')
  const resources = path.join(appOutDir, 'resources')
  const archive = path.join(resources, 'app.asar')
  const entries = asar.listPackage(archive).map(name => name.replaceAll('\\', '/'))
  const forbidden = /(?:^|\/)(?:tests?|fixtures|__mocks__|node_modules|src|\.env(?:\.[^/]*)?|vite\.config[^/]*|tsconfig[^/]*|smoke\.[^/]*|dev\.mjs|.*\.map|.*\.sqlite(?:-wal|-shm)?|.*\.db)(?:\/|$)/i
  const invalid = entries.filter(name => forbidden.test(name))
  if (invalid.length) throw new Error('Non-production files in app.asar: ' + invalid.slice(0, 15).join(', '))
  for (const name of ['/dist/index.html', '/electron/main.cjs', '/electron/preload.cjs', '/electron/backend.cjs']) {
    if (!entries.includes(name)) throw new Error('Missing production entry: ' + name)
  }
  const metadata = JSON.parse(asar.extractFile(archive, 'package.json').toString())
  if (metadata.name !== 'slothy' || metadata.devDependencies && Object.keys(metadata.devDependencies).length) {
    throw new Error('The production manifest contains a wrong brand or development dependencies.')
  }
  const prohibited = /(?:^|\/)(?:tests?|testing|fixtures|pytest(?:[^/]*)?|_pytest|pytest_asyncio|ruff|pip|ensurepip|setuptools|PyInstaller|debugpy|pdb|bdb|\.env(?:\.[^/]*)?|.*\.sqlite(?:-wal|-shm)?|.*\.spec)(?:\/|$)/i
  let count = 0
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
  const core = path.resolve(__dirname, '../../core')
  const python = process.env.SLOTHY_BUILD_PYTHON || path.join(core, '.venv/Scripts/python.exe')
  const pythonAudit = await promisify(execFile)(python, [path.join(core, 'desktop/audit_bundle.py'), backend], { windowsHide: true })
  console.log('Embedded Python audit:', pythonAudit.stdout.trim())
  console.log(`Production audit passed: slothy, ${entries.length} app entries, ${count} backend files; no development data or test entry points.`)
}

module.exports = async context => audit(context.appOutDir)
module.exports.audit = audit
