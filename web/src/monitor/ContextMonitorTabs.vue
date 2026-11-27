<script setup lang="ts">
import { ref } from 'vue'
import { Activity, Boxes, Fingerprint, FolderOpen } from 'lucide-vue-next'
import { t } from '../preferences'

const tabs = [
  { id: 'memory', icon: Fingerprint, en: 'Related memories', zh: '关联记忆' },
  { id: 'environment', icon: FolderOpen, en: 'Environment', zh: '环境上下文' },
  { id: 'tools', icon: Boxes, en: 'Loaded tools', zh: '已装载工具' },
  { id: 'work', icon: Activity, en: 'Work details', zh: '工作详情' },
] as const
const selected = defineModel<typeof tabs[number]['id']>({ required: true })
defineProps<{ prefix: string }>()
const list = ref<HTMLElement>()
function moveTab(event: KeyboardEvent) {
  const index = tabs.findIndex(tab => tab.id === selected.value)
  const count = tabs.length
  const next = { ArrowLeft: (index + count - 1) % count, ArrowRight: (index + 1) % count, Home: 0, End: count - 1 }[event.key]
  if (next === undefined) return
  event.preventDefault()
  selected.value = tabs[next]!.id
  list.value?.querySelectorAll('button')[next]?.focus()
}
</script>
<template>
  <div ref="list" class="monitor-tabs" role="tablist" :aria-label="t('Agent context', 'Agent 上下文')" @keydown="moveTab">
    <button v-for="tab in tabs" :id="prefix + '-' + tab.id" :key="tab.id" type="button" role="tab"
      :aria-selected="selected === tab.id" :aria-controls="prefix + '-panel-' + tab.id" :tabindex="selected === tab.id ? 0 : -1"
      :aria-label="t(tab.en, tab.zh)" @click="selected = tab.id">
      <component :is="tab.icon" aria-hidden="true" /><span>{{ t(tab.en, tab.zh) }}</span>
    </button>
  </div>
</template>
<style scoped>
.monitor-tabs { flex: none; display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 2px; padding: 3px; margin-inline: 14px; background: var(--neutral); border: 1px solid var(--line); border-radius: var(--radius); }
button { position: relative; display: grid; place-items: center; min-height: 34px; border-radius: calc(var(--radius) - 4px); font-size: var(--type-sm); color: var(--muted); white-space: nowrap; transition: color 150ms, background 150ms; }
button:hover { color: var(--ink); }
button[aria-selected="true"] { color: var(--accent-ink); background: var(--accent-wash); font-weight: 650; }
button svg { display: none; width: 17px; height: 17px; }
@container monitor (max-width: 29.25em) {
  button svg { display: block; }
  button span { display: none; }
  button:is(:hover, :focus-visible) span { position: absolute; z-index: 2; inset-block-end: calc(100% + 6px); display: block; padding: 4px 8px; background: var(--neutral); color: var(--ink); border: 1px solid var(--strong-line); border-radius: 6px; }
  button:first-child span { inset-inline-start: 0; }
  button:last-child span { inset-inline-end: 0; }
}
</style>
