/**
 * Build step that freezes the FastAPI backend into a self-contained PyInstaller executable.
 *
 * 构建步骤：用 PyInstaller 将 FastAPI 后端冻结为自包含的可执行文件。
 */

import { spawn } from 'node:child_process'
import { access, mkdir, rm } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const web = path.resolve(fileURLToPath(new URL('..', import.meta.url)))
const core = path.resolve(web, '../core')
const build = path.join(web, 'build/backend')
const output = path.join(web, 'build/runtime')
// Refuse to delete anything outside the web workspace, then rebuild both trees.
// 拒绝删除 web 工作区之外的任何路径，然后重建两个目录。
for (const target of [build, output]) {
  if (!target.startsWith(web + path.sep)) throw new Error('Unsafe build directory.')
  await rm(target, { recursive: true, force: true })
  await mkdir(target, { recursive: true })
}
const python = process.env.SLOTHY_BUILD_PYTHON || path.join(core, '.venv/Scripts/python.exe')
await access(python)
// PyInstaller reads `core/desktop/service.spec`; its work dir and dist dir stay inside web/build.
// PyInstaller 读取 `core/desktop/service.spec`，工作目录与产物目录都限制在 web/build 内。
const child = spawn(python, ['-m', 'PyInstaller', '--noconfirm', '--clean',
  '--distpath', output, '--workpath', build, path.join(core, 'desktop/service.spec')],
{ cwd: core, stdio: 'inherit', windowsHide: true })
const exit = await new Promise((resolve, reject) => { child.once('error', reject); child.once('exit', resolve) })
if (exit) process.exit(Number(exit) || 1)
// Fail the build when the expected executable is absent.
// 若预期的可执行文件不存在，则让构建失败。
await access(path.join(output, 'slothy-service/slothy-service.exe'))
console.log('slothy backend runtime built from application code only.')
