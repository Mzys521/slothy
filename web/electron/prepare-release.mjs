import { rm, mkdir } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
const web = path.resolve(fileURLToPath(new URL('..', import.meta.url)))
const output = path.resolve(web, 'release/slothy')
if (!output.startsWith(web + path.sep)) throw new Error('Unsafe release directory.')
// Only generated output is removed. Development and installed user profiles are untouched.
await rm(output, { recursive: true, force: true })
await mkdir(output, { recursive: true })
