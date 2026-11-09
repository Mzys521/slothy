import assert from 'node:assert/strict'
import { mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import { tmpdir } from 'node:os'
import path from 'node:path'
import test from 'node:test'

const require = createRequire(import.meta.url)
const { CONFIRMATION, confirmed, managedPaths, purgeManagedData } = require('../electron/purge.cjs')

test('purge requires the exact confirmation phrase', () => {
  assert.equal(confirmed(CONFIRMATION), true)
  assert.equal(confirmed(` ${CONFIRMATION} `), true)
  assert.equal(confirmed('delete'), false)
  assert.equal(confirmed(''), false)
  assert.equal(confirmed(undefined), false)
})

test('managed paths stay under the user data root', () => {
  const root = path.join(tmpdir(), 'slothy-purge-root')
  assert.deepEqual(managedPaths(root), [
    path.join(root, 'data'),
    path.join(root, 'logs'),
  ])
})

test('purge removes managed data and reports no residuals', async () => {
  const root = await mkdtemp(path.join(tmpdir(), 'slothy-purge-'))
  try {
    await mkdir(path.join(root, 'data', 'artifacts'), { recursive: true })
    await writeFile(path.join(root, 'data', 'checkpoints.sqlite'), 'db')
    await mkdir(path.join(root, 'logs'))
    await writeFile(path.join(root, 'logs', 'app.log'), 'log')
    const result = await purgeManagedData(root)
    assert.deepEqual(result.failed, [])
    assert.deepEqual(result.remaining, [])
    assert.equal(result.removed.length, 2)
  } finally {
    await rm(root, { recursive: true, force: true })
  }
})
