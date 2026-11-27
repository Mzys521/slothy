<script setup lang="ts">
import { computed } from 'vue'
import { t } from '../preferences'
import type { MemoryTrace } from '../workApi'
import { recallCards, traceLabel, type MemorySnippet } from './memoryCards'
import MemoryCard from './MemoryCard.vue'

const props = defineProps<{ ids?: string[]; trace?: MemoryTrace; details: MemorySnippet[]; loading: boolean; error: boolean; focus?: string }>()
const emit = defineEmits<{ graph: [id: string]; evidence: [thread: string, run: string] }>()
const cards = computed(() => recallCards(props.ids ?? [], props.trace, props.details, props.focus))
const included = computed(() => cards.value.filter(card => card.fact?.included !== false))
const excluded = computed(() => cards.value.filter(card => card.fact?.included === false))
const sources = computed(() => {
  const counts = new Map<string, number>()
  for (const { fact } of included.value) {
    const reason = fact?.reason ?? 'unrecorded'
    counts.set(reason, (counts.get(reason) ?? 0) + 1)
  }
  return Array.from(counts, ([key, count]) => traceLabel(key, t) + ' ' + count).join(' · ')
})
const note = computed(() => {
  if (!props.trace) return ''
  if (props.trace.status === 'provided' && props.trace.response_source === 'model' && included.value.length)
    return t('These facts were included in context. The model may or may not use them.', '以下事实已放入上下文，不代表模型必然采纳。')
  return traceLabel(props.trace.status, t)
})
</script>
<template>
  <div class="recall-view">
    <section v-if="!focus && included.length" class="monitor-card recall-summary" data-testid="memory-trace">
      <div class="recall-totals">
        <div><span class="monitor-caption">{{ t('Recalled memories', '召回条数') }}</span><strong>{{ included.length }}</strong></div>
        <div v-if="trace"><span class="monitor-caption">{{ t('Estimated budget', '预算估算') }}</span><p>{{ trace.context_token_estimate }} <small>tok</small></p></div>
      </div>
      <p v-if="sources" class="recall-sources">{{ sources }}</p>
      <p v-if="note" class="recall-note">{{ note }}</p>
    </section>
    <p v-if="loading" class="monitor-muted" role="status">{{ t('Loading memories…', '正在加载记忆…') }}</p>
    <section v-else-if="!included.length" class="monitor-card recall-empty" data-testid="memory-trace">
      <h3>{{ t('No memories were provided for this reply.', '本次回复未提供记忆事实。') }}</h3>
      <p v-if="note && trace?.status !== 'no_match'" class="monitor-muted">{{ note }}</p>
    </section>
    <MemoryCard v-for="card in included" :key="card.id" :card="card" :focused="focus === card.id"
      @graph="id => emit('graph', id)" @evidence="(thread, run) => emit('evidence', thread, run)" />
    <h3 v-if="excluded.length" class="monitor-caption">{{ t('Excluded from context', '未纳入上下文') }} · {{ excluded.length }}</h3>
    <MemoryCard v-for="card in excluded" :key="card.id" :card="card"
      @graph="id => emit('graph', id)" @evidence="(thread, run) => emit('evidence', thread, run)" />
    <p v-if="error" class="monitor-muted" role="status">{{ t('Some referenced memories are unavailable.', '部分引用记忆暂不可用。') }}</p>
  </div>
</template>
<style scoped>
.recall-view { display: grid; gap: 10px; }
.recall-summary { display: grid; gap: 10px; }
.recall-empty { display: grid; gap: 6px; }
.recall-totals { display: flex; gap: 28px; align-items: flex-start; }
.recall-totals > div + div { padding-inline-start: 22px; border-inline-start: 1px solid var(--line); }
.recall-totals strong { display: block; font-size: var(--type-hero); line-height: 1.12; }
.recall-totals p { font-size: var(--type-section); font-variant-numeric: tabular-nums; }
.recall-totals small, .recall-note { color: var(--muted); font-size: var(--type-sm); }
.recall-sources { font-size: var(--type-body); }
.recall-note { border-top: 1px solid var(--line); padding-top: 8px; line-height: 1.55; }
</style>
