<script setup lang="ts">
import { File, Folder, MessageSquare, Plug, X } from 'lucide-vue-next'
import { t } from '../preferences'
import type { ReferenceItem } from '../references/types'
defineProps<{ item: ReferenceItem; removable?: boolean }>()
defineEmits<{ remove: [] }>()
const icons = { folder: Folder, file: File, session: MessageSquare, plugin: Plug }
</script>
<template>
  <span :class="['reference-chip', { 'reference-unavailable': item.status === 'missing' }]" :title="item.sublabel || item.id">
    <component :is="icons[item.kind]" aria-hidden="true" /><span>{{ item.label }}</span>
    <small v-if="item.status === 'disabled'">{{ t('Disabled', '未启用') }}</small>
    <small v-if="item.status === 'missing'">{{ t('Unavailable', '已失效') }}</small>
    <small v-else-if="item.included === false">{{ t('Over budget', '超出预算') }}</small>
    <small v-else-if="item.truncated">{{ t('Truncated', '已截断') }}</small>
    <button v-if="removable" type="button" :aria-label="t('Remove reference', '移除引用') + ': ' + item.label" @click="$emit('remove')" @keydown.backspace.prevent="$emit('remove')"><X /></button>
  </span>
</template>
<style scoped>
.reference-chip { display: inline-flex; align-items: center; gap: 6px; max-width: 100%; padding: 6px 9px; border-radius: 10px; color: var(--ink); background: var(--neutral); font-size: 12px; }
.reference-chip > svg, .reference-chip button svg { width: 14px; height: 14px; flex-shrink: 0; }
.reference-chip > span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.reference-chip small { color: var(--muted); white-space: nowrap; }
.reference-chip button { display: inline-flex; padding: 2px; color: var(--muted); background: transparent; border: 0; cursor: pointer; }
.reference-unavailable { opacity: .55; }
</style>
