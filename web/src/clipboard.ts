/**
 * Clipboard helper with a desktop bridge fast-path and a legacy DOM fallback.
 *
 * 剪贴板助手：优先走桌面桥，其次使用传统 DOM 兜底方案。
 */

/**
 * Copy exactly one message, including in the permission-restricted desktop renderer.
 *
 * 仅复制一条消息，在权限受限的桌面渲染进程中同样可用。
 *
 * @param value - Text to place on the clipboard; must be non-empty. / 要写入剪贴板的文本，必须非空。
 * @returns Resolves after the text was copied. / 文本复制完成后完成。
 */
export async function copyText(value: string): Promise<void> {
  if (!value) throw new Error('There is no text to copy.')
  // The sandboxed renderer may lack clipboard permission, so ask the main process first.
  // 沙箱渲染进程可能没有剪贴板权限，因此优先请求主进程。
  if (window.desktop?.writeClipboard) {
    await window.desktop.writeClipboard(value)
    return
  }
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(value)
      return
    }
  } catch {
    // Older browsers and embedded views can deny the asynchronous clipboard API.
    // 旧版浏览器与内嵌视图可能拒绝异步剪贴板 API。
  }
  // Legacy fallback: a hidden textarea plus `execCommand('copy')`, restoring focus and selection.
  // 传统兜底：隐藏 textarea 加 `execCommand('copy')`，结束后恢复焦点与选区。
  const active = document.activeElement as HTMLElement | null
  const selection = window.getSelection()
  const ranges = Array.from({ length: selection?.rangeCount ?? 0 }, (_, i) => selection!.getRangeAt(i))
  const field = document.createElement('textarea')
  field.value = value
  field.setAttribute('readonly', '')
  field.style.cssText = 'position:fixed;left:-9999px;top:0;opacity:0'
  document.body.append(field)
  try {
    field.select()
    if (!document.execCommand('copy')) throw new Error('Clipboard access was denied.')
  } finally {
    field.remove()
    active?.focus({ preventScroll: true })
    selection?.removeAllRanges()
    for (const range of ranges) selection?.addRange(range)
  }
}
