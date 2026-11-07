/**
 * CLI wrapper that audits an unpacked release directory with the production audit rule set.
 *
 * 命令行包装脚本：用生产审计规则检查已解包的发布目录。
 */

import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { audit } from './audit-production.cjs'
const web = fileURLToPath(new URL('..', import.meta.url))
// Optional first argument overrides the default win-unpacked location.
// 可选的首个参数可覆盖默认的 win-unpacked 路径。
await audit(path.resolve(process.argv[2] || path.join(web, 'release/slothy/win-unpacked')))
