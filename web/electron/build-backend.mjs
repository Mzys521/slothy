import { spawn } from 'node:child_process'
import { access, mkdir, rm } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const web = path.resolve(fileURLToPath(new URL('..', import.meta.url)))
const core = path.resolve(web, '../core')
const build = path.join(web, 'build/backend')
const output = path.join(web, 'build/runtime')
for (const target of [build, output]) {
  if (!target.startsWith(web + path.sep)) throw new Error('Unsafe build directory.')
  await rm(target, { recursive: true, force: true })
  await mkdir(target, { recursive: true })
}
const python = process.env.SLOTHY_BUILD_PYTHON || path.join(core, '.venv/Scripts/python.exe')
await access(python)
const child = spawn(python, ['-m', 'PyInstaller', '--noconfirm', '--clean',
  '--distpath', output, '--workpath', build, path.join(core, 'desktop/service.spec')],
{ cwd: core, stdio: 'inherit', windowsHide: true })
const exit = await new Promise((resolve, reject) => { child.once('error', reject); child.once('exit', resolve) })
if (exit) process.exit(Number(exit) || 1)
await access(path.join(output, 'slothy-service/slothy-service.exe'))
console.log('slothy backend runtime built from application code only.')
