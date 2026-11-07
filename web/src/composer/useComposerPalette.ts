import { nextTick, onUnmounted, ref, type Ref } from 'vue'
import { detectTrigger, type ComposerTrigger } from '../references/parser'
import type { PaletteItem } from './types'

/** One state machine for typing and the plus button. / 输入触发与加号共用的状态机。 */
export function useComposerPalette(draft: Ref<string>, input: Ref<HTMLTextAreaElement | undefined>,
  search: (trigger: ComposerTrigger, signal: AbortSignal) => Promise<PaletteItem[]>,
  commit: (item: PaletteItem, trigger: ComposerTrigger) => void | Promise<void>) {
  const trigger = ref<ComposerTrigger | null>(null)
  const items = ref<PaletteItem[]>([])
  const selected = ref(0)
  const loading = ref(false)
  const failed = ref(false)
  let composing = false
  let timer: ReturnType<typeof setTimeout> | undefined
  let request: AbortController | undefined
  let revision = 0
  let dismissed = ''
  const identity = () => draft.value + ':' + input.value?.selectionStart

  function close(remember = true) {
    if (remember) dismissed = identity()
    revision++
    clearTimeout(timer)
    request?.abort()
    trigger.value = null
    loading.value = false
  }
  function show(value: ComposerTrigger) {
    close(false)
    trigger.value = value
    items.value = []
    selected.value = 0
    loading.value = true
    failed.value = false
    const epoch = revision
    timer = setTimeout(async () => {
      const controller = new AbortController()
      request = controller
      const deadline = setTimeout(() => controller.abort(), 1200)
      try {
        const found = await search(value, controller.signal)
        if (epoch !== revision) return
        items.value = found
        selected.value = Math.max(0, found.findIndex(item => !item.disabled))
      } catch {
        if (epoch === revision) failed.value = true
      } finally {
        clearTimeout(deadline)
        if (epoch === revision) loading.value = false
      }
    }, value.kind === 'reference' ? 120 : 80)
  }
  function update(event?: Event) {
    const field = input.value
    if (event?.type === 'click') dismissed = ''
    if (event instanceof InputEvent && event.inputType === 'insertFromPaste') { close(); return }
    if (!field || composing || field.selectionStart !== field.selectionEnd) { close(false); return }
    if (dismissed === identity()) return
    const found = detectTrigger(draft.value, field.selectionStart, composing)
    if (!found) { close(false); return }
    show(found)
  }
  async function choose(item = items.value[selected.value]) {
    if (!item || item.disabled || !trigger.value) return
    const origin = trigger.value
    close()
    await commit(item, origin)
  }
  function keydown(event: KeyboardEvent) {
    if (composing || event.isComposing || event.keyCode === 229) return true
    if (!trigger.value) return false
    if (event.key === 'Escape') { event.preventDefault(); close(); input.value?.focus(); return true }
    if (['ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) {
      event.preventDefault()
      const choices = items.value.map((item, index) => item.disabled ? -1 : index).filter(index => index >= 0)
      if (!choices.length) return true
      const index = Math.max(0, choices.indexOf(selected.value))
      selected.value = choices[event.key === 'Home' ? 0 : event.key === 'End' ? choices.length - 1
        : (index + (event.key === 'ArrowUp' ? -1 : 1) + choices.length) % choices.length]!
      void nextTick(() => document.getElementById('composer-option-' + selected.value)?.scrollIntoView({ block: 'nearest' }))
      return true
    }
    if ((event.key === 'Enter' && !event.shiftKey || event.key === 'Tab') && items.value.some(item => !item.disabled)) {
      event.preventDefault()
      void choose()
      return true
    }
    return false
  }
  function compositionStart() { composing = true; close(false) }
  function compositionEnd() { composing = false; void nextTick(() => update()) }
  function toggleAdd() {
    if (trigger.value?.kind === 'add') close()
    else show({ kind: 'add', start: input.value?.selectionStart ?? draft.value.length,
      end: input.value?.selectionStart ?? draft.value.length, query: '' })
    void nextTick(() => input.value?.focus())
  }
  onUnmounted(() => close())
  return { trigger, items, selected, loading, failed, show, close, update, choose, keydown,
    compositionStart, compositionEnd, toggleAdd }
}
