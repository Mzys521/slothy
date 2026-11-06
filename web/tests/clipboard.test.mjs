import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import ts from 'typescript'
const code = ts.transpileModule(readFileSync(new URL('../src/clipboard.ts', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText
const { copyText } = await import('data:text/javascript;base64,' + Buffer.from(code).toString('base64'))

function environment({ desktop, browser, fallback = true } = {}) {
  let copied, removed = false, focusRestored = false, selected = false, restoredRange
  const range = { original: true }
  const field = { style: {}, setAttribute() {}, select() { selected = true }, remove() { removed = true } }
  const selection = { rangeCount: 1, getRangeAt: () => range, removeAllRanges() {}, addRange(value) { restoredRange = value } }
  const clipboard = browser || { writeText: async value => { copied = value } }
  globalThis.window = { desktop, getSelection: () => selection }
  Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { clipboard } })
  globalThis.document = { activeElement: { focus: () => { focusRestored = true } },
    createElement: () => field, body: { append() {} }, execCommand: command => {
      assert.equal(command, 'copy'); copied = field.value; return fallback
    } }
  return () => ({ copied, removed, focusRestored, selected, restoredRange, range })
}

test('desktop copy uses the native bridge and copies only the requested message', async () => {
  let copied
  environment({ desktop: { writeClipboard: async value => { copied = value } },
    browser: { writeText: () => { throw new Error('Browser clipboard must not be used') } } })
  await copyText('Single **reply**\nline two')
  assert.equal(copied, 'Single **reply**\nline two')
})

test('browser clipboard receives exact original Markdown', async () => {
  const result = environment()
  await copyText('Only this message')
  assert.equal(result().copied, 'Only this message')
  assert.equal(result().selected, false)
})

test('permission rejection falls back and restores the original focus and selection', async () => {
  const result = environment({ browser: { writeText: async () => { throw new Error('Denied') } } })
  await copyText('Fallback text')
  assert.equal(result().copied, 'Fallback text')
  assert.equal(result().focusRestored, true)
  assert.equal(result().removed, true)
  assert.equal(result().restoredRange, result().range)
})

test('real failures are reported and the temporary copy field is cleaned up', async () => {
  const result = environment({ browser: {}, fallback: false })
  await assert.rejects(copyText('Cannot copy'), /denied/)
  assert.equal(result().removed, true)
  assert.equal(result().focusRestored, true)
  await assert.rejects(copyText(''), /no text/)
})
