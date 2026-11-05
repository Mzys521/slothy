import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { audit } from './audit-production.cjs'
const web = fileURLToPath(new URL('..', import.meta.url))
await audit(path.resolve(process.argv[2] || path.join(web, 'release/slothy/win-unpacked')))
