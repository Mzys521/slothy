import { rm, mkdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
const web = path.resolve(fileURLToPath(new URL('..', import.meta.url)))
const metadata = JSON.parse(await readFile(path.join(web, 'package.json'), 'utf8'))
const displayVersion = metadata.displayVersion || metadata.version
if (!/^\d+\.\d+\.\d+(?:\.\d+)?(?:-[A-Za-z0-9][A-Za-z0-9.-]*)?$/.test(displayVersion)) throw new Error('Invalid release display version.')
await mkdir(path.join(web, 'build'), { recursive: true })
// Keep the user-facing installer/Windows Apps label separate from SemVer package metadata.
await writeFile(path.join(web, 'build/release-version.nsh'), `; Generated from package.json; not a runtime resource.\n!undef VERSION\n!define VERSION "${displayVersion}"\n`)
const output = path.resolve(web, 'release/slothy')
if (!output.startsWith(web + path.sep)) throw new Error('Unsafe release directory.')
// Only generated output is removed. Development and installed user profiles are untouched.
await rm(output, { recursive: true, force: true })
await mkdir(output, { recursive: true })
