/**
 * Prepares release metadata: derives the user-facing version from package.json and resets
 * the current version's release output folder while keeping earlier versions.
 *
 * 准备发布元数据：从 package.json 推导界面上展示的版本号，并只重置当前版本的发布输出目录，
 * 保留更早版本的产物。
 */

import { rm, mkdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
const web = path.resolve(fileURLToPath(new URL('..', import.meta.url)))
const metadata = JSON.parse(await readFile(path.join(web, 'package.json'), 'utf8'))
// `displayVersion` wins over SemVer `version` so Windows can show a four-part build label.
// `displayVersion` 优先于 SemVer `version`，便于 Windows 展示四段式构建号。
const displayVersion = metadata.displayVersion || metadata.version
if (!/^\d+\.\d+\.\d+(?:\.\d+)?(?:-[A-Za-z0-9][A-Za-z0-9.-]*)?$/.test(displayVersion)) throw new Error('Invalid release display version.')
await mkdir(path.join(web, 'build'), { recursive: true })
// Keep the user-facing installer/Windows Apps label separate from SemVer package metadata.
// 让安装器 / Windows 应用列表展示的版本与 SemVer 包元数据保持分离。
await writeFile(path.join(web, 'build/release-version.nsh'), `; Generated from package.json; not a runtime resource.\n!undef VERSION\n!define VERSION "${displayVersion}"\n`)
const output = path.resolve(web, 'release/slothy', displayVersion)
if (!output.startsWith(web + path.sep)) throw new Error('Unsafe release directory.')
// Only generated output is removed. Development and installed user profiles are untouched.
// 只删除生成产物，开发环境与已安装用户数据不受影响。
await rm(output, { recursive: true, force: true })
await mkdir(output, { recursive: true })
