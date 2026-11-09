<script setup lang="ts">
/**
 * Collapsible reasoning trace, separated from the assistant answer.
 *
 * It starts expanded while the model is thinking and collapses automatically once the
 * reasoning completes; users can reopen it. The text is display-only and never enters
 * prompt/history content.
 *
 * 可折叠的思维链展示，与助手回答分离；思考中默认展开，推理完成后自动收起，用户可再次展开。
 * 该文本仅用于展示，不进入提示词或历史消息正文。
 */
import { computed, ref, watch } from 'vue'
import { t } from './preferences'

const props = defineProps<{ text: string; streaming?: boolean }>()
const open = ref(Boolean(props.streaming))
const summary = computed(() =>
  t(`${props.text.length} characters of reasoning`, `思考过程 ${props.text.length} 字`),
)

watch(() => props.streaming, streaming => {
  if (!streaming) open.value = false
}, { immediate: true })
watch(() => props.text, () => {
  if (props.streaming) open.value = true
})
</script>

<template>
  <details v-if="text" class="reasoning-trace" :open="open" @toggle="open = ($event.target as HTMLDetailsElement).open">
    <summary>
      <span>{{ t('Reasoning', '思考过程') }}</span>
      <small>{{ streaming ? t('thinking…', '思考中…') : summary }}</small>
    </summary>
    <pre>{{ text }}</pre>
  </details>
</template>

<style scoped>
.reasoning-trace { margin: 0 0 8px; padding: 6px 10px; border-left: 2px solid var(--strong-line); color: var(--muted); font-size: var(--type-sm, 12px); }
.reasoning-trace summary { display: flex; align-items: center; gap: 8px; cursor: pointer; list-style: none; }
.reasoning-trace summary::-webkit-details-marker { display: none; }
.reasoning-trace summary span { color: var(--ink); }
.reasoning-trace pre { max-height: 260px; margin: 8px 0 2px; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; font: inherit; line-height: 1.6; }
</style>
