/** Copy exactly one message, including in the permission-restricted desktop renderer. */
export async function copyText(value: string): Promise<void> {
  if (!value) throw new Error('There is no text to copy.')
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
  }
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
