<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { t } from './preferences'
import { threadPath, workRequest } from './workApi'
import { ReadGuard } from './readGuard'

import ModelCallCard from './monitor/ModelCallCard.vue'
import TokenDonutChart from './monitor/TokenDonutChart.vue'
import { estimateSegments, providerUsage, type JournalEvent } from './monitor/workMetrics'

const props = defineProps<{ thread?: string; run?: string }>()
const events = ref<JournalEvent[]>([])
const selectedIndex = ref<number | null>(null)
const loading = ref(false)
const error = ref('')
const reads = new ReadGuard<string>()
let controller: AbortController | undefined
const activeRun = computed(() => props.run ?? events.value.at(-1)?.run_id)
const calls = computed(() => events.value.filter(call => call.run_id === activeRun.value))
const earlier = computed(() => events.value.filter(call => call.run_id !== activeRun.value).reverse())
const scope = computed(() => selectedIndex.value == null ? calls.value : calls.value.slice(selectedIndex.value, selectedIndex.value + 1))
const segments = computed(() => estimateSegments(scope.value, t))
const total = computed(() => segments.value.reduce((sum, item) => sum + item.value, 0))
const estimatedCalls = computed(() => scope.value.filter(call => call.context).length)
const usage = computed(() => providerUsage(scope.value))
watch(activeRun, () => { selectedIndex.value = null })
watch(() => props.thread, () => void load(), { immediate: true })
onUnmounted(() => {
  // An unmounted panel must never publish a late response.
  // 已卸载的面板绝不能提交迟到的响应。
  reads.invalidate(props.thread ?? '')
  controller?.abort()
  controller = undefined
  clear()
})

/** Resets every value this panel owns. / 重置该面板拥有的全部状态。 */
function clear() {
  events.value = []
  selectedIndex.value = null
  error.value = ''
  loading.value = false
}

/** Load persisted JSONL work records for the active session. / 读取当前会话的持久化记录。 */
async function load() {
  const thread = props.thread ?? ''
  // The read owns this thread only while it stays the selection and no newer read starts.
  // 该读取只在其仍是选中会话且没有更新读取时拥有该会话。
  const token = reads.begin(thread)
  controller?.abort()
  controller = undefined
  clear()
  if (!thread) return
  const active = () => reads.owns(token, props.thread ?? '')
  const next = new AbortController()
  controller = next
  loading.value = true
  try {
    const result = await workRequest<{ items: JournalEvent[] }>(
      threadPath(thread) + '/work-details', 'GET', undefined, { signal: next.signal },
    )
    if (!active()) return
    events.value = result.items ?? []
  } catch (reason) {
    if (!active()) return
    error.value = reason instanceof Error ? reason.message : String(reason)
  } finally {
    if (active()) loading.value = false
  }
}
</script>

<template>
  <div class="work-details">
    <p v-if="loading" class="monitor-muted" role="status">{{ t('Loading work details…', '正在读取工作详情…') }}</p>
    <p v-else-if="error" role="alert">{{ error }}</p>
    <template v-else>
      <p v-if="!calls.length" class="monitor-card monitor-muted">{{ t('No model calls recorded for this turn.', '本轮暂无模型调用记录。') }}</p>
      <template v-else>
        <section class="monitor-card estimate-card">
          <header class="work-heading"><h3>{{ t('Estimated input breakdown', '输入构成估算') }}</h3><span>{{ t(`${calls.length} model calls`, `共 ${calls.length} 次模型调用`) }}</span></header>
          <div v-if="calls.length > 1" class="call-switch" role="group" :aria-label="t('Call scope', '调用范围')">
            <button type="button" :aria-pressed="selectedIndex == null" @click="selectedIndex = null">{{ t('Summary', '汇总') }}</button>
            <button v-for="(call, i) in calls" :key="call.model_call_id ?? i" type="button" :aria-pressed="selectedIndex === i" @click="selectedIndex = i">{{ t('Call', '调用') }} {{ i + 1 }}</button>
          </div>
          <TokenDonutChart v-if="estimatedCalls" :segments="segments" :total="total" :center-label="t('Est. input tokens', '估算输入 Token')" />
          <p v-if="estimatedCalls < scope.length" class="monitor-muted">{{ t(`Estimates available for ${estimatedCalls} of ${scope.length} calls.`, `${scope.length} 次调用中有 ${estimatedCalls} 次记录了估算。`) }}</p>
        </section>
        <section class="monitor-card provider-card">
          <header class="work-heading"><h3>{{ t('Provider usage', 'Provider 实际用量') }}</h3><span>{{ usage.duration.display }} ms</span></header>
          <dl><div><dt>{{ t('Input', '输入') }}</dt><dd>{{ usage.input.display }}</dd></div>
            <div><dt>{{ t('Output', '输出') }}</dt><dd>{{ usage.output.display }}</dd></div>
            <div><dt>{{ t('Cached', '缓存命中') }}</dt><dd>{{ usage.cache.display }}</dd></div></dl>
          <div v-if="usage.hitRate != null" class="cache-rate">
            <span>{{ t('Cache hit rate', '缓存命中率') }}</span>
            <progress :value="usage.hitRate" max="1" :aria-label="t('Cache hit rate', '缓存命中率')"></progress>
            <b>{{ (usage.hitRate * 100).toFixed(1) }}%</b>
          </div>
        </section>
        <h3 class="call-list-title">{{ t('Call details', '调用明细') }}</h3>
        <ModelCallCard v-for="(call, index) in calls" :key="call.model_call_id ?? index" :call="call" :number="index + 1" />
      </template>
      <details v-if="earlier.length" class="earlier-calls">
        <summary>{{ t('Previous turns', '历史轮次的调用') }} · {{ earlier.length }}</summary>
        <ModelCallCard v-for="(call, index) in earlier" :key="call.model_call_id ?? index" :call="call" />
      </details>
    </template>
  </div>
</template>

<style scoped>
.work-details { display: grid; gap: 10px; }
.estimate-card, .provider-card { display: grid; gap: 12px; }
.work-heading { display: flex; align-items: baseline; flex-wrap: wrap; justify-content: space-between; gap: 4px 8px; }
.work-heading h3 { font-size: var(--type-reading); }
.work-heading span, dt, .cache-rate, .call-list-title, .earlier-calls > summary { color: var(--muted); font-size: var(--type-sm); }
.call-switch { display: flex; max-width: 100%; min-width: 0; width: fit-content; overflow-x: auto; padding: 3px; border-radius: 8px; background: var(--neutral); }
.call-switch button { flex: none; padding: 3px 10px; min-height: 28px; border-radius: 5px; color: var(--muted); font-size: var(--type-sm); }
.call-switch button:focus-visible { outline-offset: -2px; }
.call-switch button[aria-pressed="true"] { background: var(--surface); color: var(--accent-ink); font-weight: 600; }
.provider-card dl { display: flex; gap: 10px 24px; flex-wrap: wrap; margin: 0; }
.provider-card dd { margin: 2px 0 0; font-size: var(--type-section); font-weight: 600; }
.provider-card, .work-heading span { font-variant-numeric: tabular-nums; }
.cache-rate { display: flex; align-items: center; gap: 10px; border-top: 1px solid var(--line); padding-top: 9px; }
.cache-rate b { color: var(--ink); font-weight: 500; }
progress { appearance: none; flex: 1; min-width: 10px; height: 4px; border: 0; border-radius: 4px; background: var(--line); }
progress::-webkit-progress-bar { background: var(--line); border-radius: inherit; }
progress::-webkit-progress-value { background: var(--success); border-radius: inherit; }
progress::-moz-progress-bar { background: var(--success); }
.call-list-title { margin-top: 4px; }
.earlier-calls .model-call { margin-top: 8px; }
</style>
