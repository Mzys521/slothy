/**
 * CLI wrapper that audits an unpacked release directory with the production audit rule set.
 *
 * 命令行包装脚本：用生产审计规则检查已解包的发布目录。
 */

import path from 'node:path'
import { readFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { audit } from './audit-production.cjs'
const web = fileURLToPath(new URL('..', import.meta.url))
const metadata = JSON.parse(await readFile(path.join(web, 'package.json'), 'utf8'))
const releaseVersion = metadata.displayVersion || metadata.version
// Optional first argument overrides the versioned win-unpacked location.
// 可选的首个参数可覆盖按版本存放的 win-unpacked 路径。
await audit(
  path.resolve(
    process.argv[2] || path.join(web, 'release/slothy', releaseVersion, 'win-unpacked'),
  ),
)
