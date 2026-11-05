import assert from 'node:assert/strict'
import test from 'node:test'
import path from 'node:path'
import { selectWorkingDirectory } from './directory.cjs'

test('directory choice is attached to its trusted window and normalized', async () => {
  const window = { owner: 'test-window' }
  const chosen = path.resolve('build/qa-workspaces/native-workspace')
  const result = await selectWorkingDirectory({ async showOpenDialog(parent, options) {
    assert.equal(parent, window)
    assert.ok(options.properties.includes('openDirectory'))
    return { canceled: false, filePaths: [chosen] }
  } }, window)
  assert.equal(result, chosen)
})

test('canceling or returning no selection does not create a directory grant', async () => {
  for (const result of [{ canceled: true, filePaths: [] }, { canceled: false, filePaths: [] }]) {
    assert.equal(await selectWorkingDirectory({ async showOpenDialog() { return result } }, {}), null)
  }
})

test('a missing application window is rejected before opening the picker', async () => {
  let called = false
  await assert.rejects(selectWorkingDirectory({ async showOpenDialog() { called = true } }, null))
  assert.equal(called, false)
})
