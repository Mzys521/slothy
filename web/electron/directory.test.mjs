/**
 * Unit tests for the native directory picker contract (window ownership, normalization, cancellation).
 *
 * 原生目录选择器契约的单元测试（窗口归属、路径规范化、取消行为）。
 */

import assert from 'node:assert/strict'
import test from 'node:test'
import path from 'node:path'
import { selectWorkingDirectory } from './directory.cjs'

// The fake dialog asserts that the modal parent is the trusted window and that
// directory selection is requested, then echoes a normalized absolute path.
// 伪对话框会断言模态父窗口为可信窗口、请求了目录选择，并回显规范化后的绝对路径。
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

// Both an explicit cancel and an empty file list must yield no grant.
// 明确取消与返回空列表这两种情况都不得产生任何授权。
test('canceling or returning no selection does not create a directory grant', async () => {
  for (const result of [{ canceled: true, filePaths: [] }, { canceled: false, filePaths: [] }]) {
    assert.equal(await selectWorkingDirectory({ async showOpenDialog() { return result } }, {}), null)
  }
})

// A missing window must fail before the picker opens, so no dialog can be hijacked.
// 缺少窗口时必须在打开选择框前失败，避免对话框被滥用。
test('a missing application window is rejected before opening the picker', async () => {
  let called = false
  await assert.rejects(selectWorkingDirectory({ async showOpenDialog() { called = true } }, null))
  assert.equal(called, false)
})
