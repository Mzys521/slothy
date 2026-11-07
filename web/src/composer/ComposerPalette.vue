<script setup lang="ts">
import { Command, File, Folder, MessageSquare, Plug, Plus } from 'lucide-vue-next'
import { onMounted, onUnmounted, ref } from 'vue'
import { t } from '../preferences'
import type { PaletteItem } from './types'
defineProps<{ items: PaletteItem[]; selected: number; loading: boolean; failed: boolean; title: string }>()
const emit = defineEmits<{ choose: [item: PaletteItem]; hover: [index: number] }>()
const icons = { folder: Folder, file: File, session: MessageSquare, plugin: Plug, command: Command, add: Plus }
const root = ref<HTMLElement>()
const maximumHeight = ref(460)
let observer: ResizeObserver | undefined
function measure() {
  const top = root.value?.parentElement?.getBoundingClientRect().top ?? 512
  maximumHeight.value = Math.max(140, Math.min(460, innerHeight * .55, top - 52))
}
onMounted(() => {
  measure()
  observer = new ResizeObserver(measure)
  if (root.value?.parentElement) observer.observe(root.value.parentElement)
  window.addEventListener('resize', measure)
})
onUnmounted(() => { observer?.disconnect(); window.removeEventListener('resize', measure) })
</script>

<template>
  <section ref="root" class="composer-palette" :style="{ maxHeight: maximumHeight + 'px' }" :aria-label="title">
    <div class="palette-heading"><span>{{ title }}</span><kbd>Esc</kbd></div>
    <div id="composer-palette-list" class="palette-options" role="listbox" :aria-label="title" :aria-busy="loading">
      <template v-for="(item, index) in items" :key="item.id">
        <p v-if="index === 0 || item.group !== items[index - 1]?.group" class="palette-group" role="presentation">{{ item.group }}</p>
        <button :id="'composer-option-' + index" type="button" role="option" tabindex="-1"
          :aria-selected="selected === index" :aria-disabled="item.disabled || undefined"
          :class="['palette-option', { 'is-selected': selected === index, 'is-disabled': item.disabled }]"
          @pointerdown.prevent @pointermove="!item.disabled && emit('hover', index)" @click="!item.disabled && emit('choose', item)">
          <component :is="icons[item.icon as keyof typeof icons] || Command" aria-hidden="true" />
          <span class="palette-label"><span>{{ item.title }}</span><small>{{ item.description }}</small></span>
          <kbd v-if="item.command">/{{ item.command }}</kbd>
          <span v-if="item.reference?.status === 'disabled'" class="palette-disabled">{{ t('Disabled', '未启用') }}</span>
        </button>
      </template>
      <p v-if="loading" class="palette-empty" role="status">{{ t('Loading…', '正在加载…') }}</p>
      <p v-else-if="failed" class="palette-empty" role="status">{{ t('Suggestions unavailable. You can keep typing.', '无法获取候选，可继续输入。') }}</p>
      <p v-else-if="!items.length" class="palette-empty" role="status">{{ t('No matches. Try another name.', '无匹配项，请换个名称。') }}</p>
    </div>
    <footer class="palette-footer">{{ t('↑ ↓ to browse · Enter / Tab to select', '↑ ↓ 浏览 · Enter / Tab 选择') }}</footer>
  </section>
</template>

<style scoped>
.composer-palette { position: absolute; z-index: 30; bottom: calc(100% + 8px); left: 0; width: 100%; max-height: min(460px, 55vh); display: flex; flex-direction: column; padding: 10px 8px 6px; background: var(--neutral); border-radius: 24px; color: var(--ink); text-align: left; }
.palette-heading { display: flex; align-items: center; justify-content: space-between; padding: 4px 12px 7px; font-size: 14px; color: var(--muted); }
.palette-options { overflow: auto; overscroll-behavior: contain; max-height: 360px; scrollbar-width: thin; }
.palette-group { margin: 10px 12px 4px; font-size: 12px; color: var(--muted); }
.palette-option { width: 100%; display: flex; align-items: center; gap: 12px; border: 0; border-radius: 12px; background: transparent; color: inherit; text-align: left; padding: 10px 12px; min-height: 46px; cursor: pointer; }
.palette-option.is-selected { background: var(--accent-wash); }
.palette-option.is-disabled { opacity: .42; cursor: default; }
.palette-option > svg { flex: 0 0 20px; width: 20px; height: 20px; color: var(--muted); }
.palette-label { display: flex; align-items: baseline; flex: 1; min-width: 0; gap: 10px; font-size: 14px; }
.palette-label > span { flex-shrink: 0; }
.palette-label small { color: var(--muted); font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
kbd, .palette-disabled { font-family: inherit; font-size: 11px; color: var(--muted); font-weight: 400; }
.palette-footer, .palette-empty { padding: 8px 12px; margin: 0; color: var(--muted); font-size: 12px; }
.palette-footer { font-size: 11px; }
@media (max-width: 640px) { .palette-label { display: block; } .palette-label small { display: block; margin-top: 3px; } .palette-option { gap: 8px; } }
</style>
