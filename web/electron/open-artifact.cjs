const { spawn } = require('node:child_process')
const { open, realpath, stat } = require('node:fs/promises')
const path = require('node:path')

const DOCUMENT_TYPES = new Set([
  '.txt', '.md', '.markdown', '.pdf', '.docx', '.pptx', '.xlsx', '.odt', '.odp', '.ods',
  '.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.tif', '.tiff', '.mp3', '.wav', '.mp4',
])

async function openTextInNotepad(file) {
  if (process.platform !== 'win32') return false
  const sample = Buffer.alloc(4096)
  const handle = await open(file, 'r')
  try {
    const { bytesRead } = await handle.read(sample)
    const bytes = sample.subarray(0, bytesRead)
    if (bytes.includes(0)) return false
    new TextDecoder('utf-8', { fatal: true }).decode(bytes, { stream: bytesRead === sample.length })
  } catch (error) {
    if (error instanceof TypeError) return false
    throw error
  } finally {
    await handle.close()
  }
  // An explicit editor opens script files as text without invoking their file association.
  const editor = path.join(process.env.SystemRoot || 'C:\\Windows', 'System32', 'notepad.exe')
  return new Promise(resolve => {
    const child = spawn(editor, [file], { detached: true, stdio: 'ignore' })
    child.once('error', () => resolve(false))
    child.once('spawn', () => { child.unref(); resolve(true) })
  })
}

/** Open a registered conversation artifact without accepting a renderer-supplied path. */
async function openArtifact(
  shell, origin, token, threadId, artifactId, { request = fetch, openText = openTextInNotepad } = {},
) {
  if (typeof threadId !== 'string' || !/^[\w-]{1,128}$/.test(threadId) ||
      typeof artifactId !== 'string' || !/^[a-f0-9]{64}$/.test(artifactId)) {
    throw new Error('Invalid file identity.')
  }
  const base = new URL(origin)
  if (!['127.0.0.1', 'localhost', '[::1]'].includes(base.hostname)) {
    throw new Error('The local backend is required to open files.')
  }
  const route = `/api/v1/threads/${threadId}/artifacts/${artifactId}/location`
  const response = await request(new URL(route, base), {
    headers: token ? { 'X-Slothy-Token': token } : {},
    redirect: 'error',
    signal: AbortSignal.timeout(8000),
  })
  if (response.status === 404) return 'missing'
  if (!response.ok) throw new Error('Could not locate the file.')
  const { path: file } = await response.json()
  if (typeof file !== 'string' || !path.isAbsolute(file) || /[\x00-\x1f]/.test(file)) {
    throw new Error('Invalid file location.')
  }
  if (process.platform === 'win32' &&
      (/^\\\\[.?]\\/.test(file) || file.slice(path.parse(file).root.length).includes(':'))) {
    throw new Error('Invalid file location.')
  }
  try {
    const resolved = await realpath(file)
    if (path.relative(file, resolved) || !(await stat(resolved)).isFile()) return 'missing'
    if (DOCUMENT_TYPES.has(path.extname(resolved).toLowerCase()) && !await shell.openPath(resolved)) {
      return 'opened'
    }
    if (await openText(resolved)) return 'opened'
    shell.showItemInFolder(resolved)
    return 'revealed'
  } catch (error) {
    if (error?.code === 'ENOENT' || error?.code === 'ENOTDIR') return 'missing'
    throw error
  }
}

module.exports = { openArtifact }
