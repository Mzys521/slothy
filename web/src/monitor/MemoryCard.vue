<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, useId, watch } from 'vue'
import { ArrowUpRight, ChevronDown } from 'lucide-vue-next'
import { t } from '../preferences'
import { memoryTypeName } from '../workApi'
import { traceLabel, type RecallCard } from './memoryCards'
import CopyValue from './CopyValue.vue'

const props = defineProps<{ card: RecallCard; focused?: boolean }>()
const emit = defineEmits<{ graph: [id: string]; evidence: [thread: string, run: string] }>()
const detail = computed(() => props.card.detail)
const fact = computed(() => props.card.fact)
const strength = computed(() => fact.value?.strength ?? detail.value?.strength)
const title = computed(() => detail.value?.content || detail.value?.title || t('Memory details unavailable', '暂无记忆正文'))
const relation = computed(() => {
  const record = detail.value
  if (!record?.source_concept_id || !record.target_concept_id) return ''
  const label = (id: string) => record.concepts?.find(item => item.id === id)?.label ?? id.slice(0, 8)
  return [label(record.source_concept_id) + ' → ' + label(record.target_concept_id),
    record.predicate === 'proficiency' ? t('Skill proficiency', '技能熟练度') : record.predicate].filter(Boolean).join(' · ')
})
const body = ref<HTMLElement>()
const expanded = ref(false)
const longTitle = ref(false)
const evidenceOpen = ref(false)
const evidenceId = useId()
let observer: ResizeObserver | undefined
function measureTitle() {
  const el = body.value
  if (el?.clientWidth) longTitle.value = el.scrollHeight > parseFloat(getComputedStyle(el).lineHeight) * 2 + 1
}
onMounted(() => {
  observer = new ResizeObserver(measureTitle)
  if (body.value) observer.observe(body.value)
})
onUnmounted(() => observer?.disconnect())
watch(title, measureTitle, { flush: 'post' })
watch(() => props.focused, value => { expanded.value = !!value }, { immediate: true })
</script>
<template>
  <article class="monitor-card recall-card" :data-memory-id="card.id">
    <h3><span ref="body" class="recall-title" :class="{ clamped: !expanded }">{{ title }}</span></h3>
    <button v-if="longTitle" type="button" class="recall-more" :aria-expanded="expanded" @click="expanded = !expanded">
      {{ expanded ? t('Show less', '收起全文') : t('Read more', '展开全文') }}
    </button>
    <div class="recall-badges">
      <span v-if="detail?.type" class="monitor-badge" :data-type="detail.type">{{ memoryTypeName(detail.type, t) }}</span>
      <span class="monitor-badge">{{ traceLabel(fact?.reason ?? 'unrecorded', t) }}</span>
    </div>
    <div v-if="strength != null || detail?.effective_frequency != null" class="recall-metrics">
      <span v-if="strength != null" class="recall-strength">
        {{ t('Frequency strength', '频次强度') }}
        <meter min="0" max="1" :value="strength" :aria-label="t('Frequency strength', '频次强度')"></meter>
        <b>{{ strength.toFixed(2) }}</b>
      </span>
      <span v-if="detail?.effective_frequency != null">{{ t('Confirmed sessions', '有效确认会话') }} <b>{{ detail.effective_frequency }}</b></span>
    </div>
    <p v-if="relation" class="monitor-muted">{{ relation }}</p>
    <p v-if="fact?.concept_ids.length" class="monitor-muted">{{ t('Concept path', '概念路径') }} {{ fact.concept_ids.map(id => id.slice(0, 8)).join(' → ') }}</p>
    <div class="recall-meta">
      <CopyValue :value="card.id" :label="t('Copy memory ID', '复制记忆 ID') + ' ' + card.id">{{ card.id.slice(0, 8) }}</CopyValue>
      <button type="button" class="monitor-link" @click="emit('graph', card.id)">{{ t('Open in Graph', '在图谱中打开') }}<ArrowUpRight aria-hidden="true" /></button>
    </div>
    <div v-if="detail?.evidence?.length" class="recall-evidence">
      <button type="button" :aria-expanded="evidenceOpen" :aria-controls="evidenceId" @click="evidenceOpen = !evidenceOpen">
        <ChevronDown class="monitor-chevron" aria-hidden="true" />{{ t('Original evidence', '原始证据') }} <span>{{ detail.evidence.length }}</span>
      </button>
      <div :id="evidenceId" class="evidence-fold" :class="{ open: evidenceOpen }" :inert="!evidenceOpen">
        <div><blockquote v-for="(item, index) in detail.evidence" :key="index">
          <p>{{ item.quote }}</p>
          <time v-if="item.recorded_at" :datetime="item.recorded_at">{{ new Date(item.recorded_at).toLocaleString() }}</time>
          <button type="button" class="monitor-link" @click="emit('evidence', item.thread_id, item.run_id)">{{ t('View source conversation', '查看来源会话') }}<ArrowUpRight aria-hidden="true" /></button>
        </blockquote></div>
      </div>
    </div>
  </article>
</template>
<style scoped>
.recall-card { display: grid; gap: 8px; overflow-wrap: anywhere; }
.recall-title { display: block; white-space: pre-line; font-size: var(--type-reading); line-height: 1.5; font-weight: 600; }
.clamped { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.recall-more { width: fit-content; color: var(--accent-ink); font-size: var(--type-sm); }
.recall-badges, .recall-metrics, .recall-meta { display: flex; align-items: center; flex-wrap: wrap; gap: 6px 9px; }
.monitor-badge[data-type] { --badge-color: var(--success); color: var(--badge-color); background: color-mix(in srgb, var(--badge-color) 13%, var(--surface)); }
.monitor-badge[data-type="identity"] { --badge-color: var(--pending); }
.monitor-badge[data-type="skill"] { --badge-color: var(--chart-tool-schema); }
.monitor-badge[data-type="semantic"] { --badge-color: var(--chart-memory); }
.monitor-badge[data-type="episodic"] { --badge-color: var(--chart-runtime); }
.recall-metrics { color: var(--muted); font-size: var(--type-sm); column-gap: 18px; }
.recall-metrics b { color: var(--ink); font-weight: 500; font-variant-numeric: tabular-nums; }
.recall-strength { display: inline-flex; gap: 6px; align-items: center; }
meter { appearance: none; width: 48px; height: 4px; background: var(--line); border-radius: 4px; }
meter::-webkit-meter-bar { height: 4px; border: 0; background: var(--line); }
meter::-webkit-meter-optimum-value { background: var(--accent); border-radius: inherit; }
meter::-moz-meter-bar { background: var(--accent); }
.recall-meta { justify-content: space-between; }
.recall-evidence { border-top: 1px solid var(--line); padding-top: 4px; }
.recall-evidence > button { display: flex; min-height: 28px; gap: 6px; align-items: center; color: var(--muted); font-size: var(--type-sm); }
.recall-evidence > button span { font-variant-numeric: tabular-nums; }
.evidence-fold { display: grid; grid-template-rows: 0fr; transition: grid-template-rows 150ms; }
.evidence-fold.open { grid-template-rows: 1fr; }
.evidence-fold > div { overflow: hidden; min-height: 0; }
blockquote { margin: 8px 0 2px; padding-inline-start: 10px; border-inline-start: 2px solid var(--strong-line); }
blockquote p { white-space: pre-line; }
blockquote time { display: block; color: var(--muted); }
</style>
