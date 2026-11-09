<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { t } from './preferences'
import { threadPath, workRequest } from './workApi'

interface JournalEvent {
  schema_version?: number
  event_type?: string
  timestamp?: string
  session_id?: string
  run_id?: string
  step_id?: string
  model_call_id?: string
  provider?: string
  model?: string
  parameters?: Record<string, unknown>
  context?: Record<string, number | boolean>
  tools?: { count?: number; names?: string[]; schema_tokens?: number }
  usage?: Record<string, number | string | null>
  duration_ms?: number
  status?: string
  response_status?: string | null
}

const props = defineProps<{ thread?: string }>()
const events = ref<JournalEvent[]>([])
const selectedId = ref('')
const loading = ref(false)
const error = ref('')
const labels: Record<string, [string, string]> = {
  system: ['System prompt', '系统提示词'],
  runtime: ['Runtime context', '运行上下文'],
  tool_schema: ['Tool schemas', '工具定义'],
  history: ['Conversation history', '历史消息'],
  user: ['Current input', '当前输入'],
  memory: ['Memory & profile', '记忆与画像'],
  other: ['Other', '其他'],
}
const colors: Record<string, string> = {
  system: '#8b7cf6', runtime: '#5bb7d9', tool_schema: '#d9a45b',
  history: '#6fbf8f', user: '#e06c75', memory: '#c586c0', other: '#687080',
}
const selected = computed(() => events.value.find(item => item.model_call_id === selectedId.value))
const categories = computed(() => {
  const context = selected.value?.context ?? {}
  return Object.keys(labels).map(key => ({
    key,
    label: t(...labels[key]!),
    value: Number(context[key] ?? 0),
    color: colors[key]!,
  })).filter(item => item.value > 0)
})
const total = computed(() => categories.value.reduce((sum, item) => sum + item.value, 0))
watch(() => props.thread, load, { immediate: true })

/** Load persisted JSONL work records for the active session. / 读取当前会话的持久化记录。 */
async function load() {
  selectedId.value = ''
  events.value = []
  error.value = ''
  if (!props.thread) return
  loading.value = true
  try {
    const result = await workRequest<{ items: JournalEvent[] }>(threadPath(props.thread) + '/work-details')
    events.value = result.items ?? []
    selectedId.value = events.value.at(-1)?.model_call_id ?? ''
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : String(reason)
  } finally { loading.value = false }
}
</script>

<template>
  <div class="work-details">
    <p v-if="loading" class="monitor-muted">{{ t('Loading work details…', '正在读取工作详情…') }}</p>
    <p v-else-if="error" role="alert">{{ error }}</p>
    <p v-else-if="!events.length" class="monitor-muted">{{ t('No model calls recorded for this session yet.', '该会话还没有模型调用记录。') }}</p>
    <template v-else>
      <p class="monitor-muted">{{ t(`${events.length} model calls`, `共 ${events.length} 次模型调用`) }}</p>
      <div class="work-bars" v-if="selected?.context">
        <div class="work-bars-total"><strong>{{ total }}</strong> <small>{{ t('est. input tokens', '估算输入 Token') }}</small></div>
        <div class="work-bar-row" v-for="item in categories" :key="item.key">
          <span>{{ item.label }}</span>
          <div><i :style="{ width: (total ? item.value / total * 100 : 0) + '%', background: item.color }"></i></div>
          <small>{{ item.value }} · {{ total ? Math.round(item.value / total * 100) : 0 }}%</small>
        </div>
      </div>
      <section class="work-capacity" v-if="selected">
        <h4>{{ selected.model || '—' }} <small>{{ selected.provider }}</small></h4>
        <p>{{ t('Input', '输入') }} {{ selected.usage?.input_tokens ?? t('unknown', '未知') }}
          · {{ t('Output', '输出') }} {{ selected.usage?.output_tokens ?? t('unknown', '未知') }}
          · {{ t('Cache', '缓存') }} {{ selected.usage?.cache_hit_tokens ?? t('unknown', '未知') }}</p>
        <p>{{ t('Duration', '耗时') }} {{ selected.duration_ms ?? '—' }} ms · {{ selected.status }}
          <span v-if="selected.usage?.source === 'provider'">· {{ t('provider usage', 'Provider 实际用量') }}</span>
          <span v-else>· {{ t('estimated / unknown', '估算或未知') }}</span></p>
      </section>
      <details v-for="item in [...events].reverse()" :key="item.model_call_id" class="work-call"
        :open="item.model_call_id === selectedId" @toggle="($event.target as HTMLDetailsElement).open && (selectedId = item.model_call_id || '')">
        <summary><span>{{ item.model || item.provider }}</span><small>{{ item.status }} · {{ item.duration_ms }} ms</small></summary>
        <dl>
          <div><dt>{{ t('Run', '运行') }}</dt><dd>{{ item.run_id }}</dd></div>
          <div><dt>{{ t('Step', '步骤') }}</dt><dd>{{ item.step_id }}</dd></div>
          <div><dt>{{ t('Model call', '模型调用') }}</dt><dd>{{ item.model_call_id }}</dd></div>
          <div><dt>{{ t('Parameters', '参数') }}</dt><dd><code>{{ JSON.stringify(item.parameters ?? {}) }}</code></dd></div>
          <div><dt>{{ t('Tools', '工具') }}</dt><dd>{{ item.tools?.count ?? 0 }} · {{ item.tools?.schema_tokens ?? 0 }} {{ t('est. tokens', '估算 Token') }}</dd></div>
          <div><dt>{{ t('Context', '上下文') }}</dt><dd>{{ JSON.stringify(item.context ?? {}) }}</dd></div>
        </dl>
      </details>
    </template>
  </div>
</template>

<style scoped>
.work-details { display: grid; gap: 12px; }
.work-bars { display: grid; gap: 8px; }
.work-bars-total { display: flex; align-items: baseline; gap: 6px; }
.work-bars-total strong { font-size: 22px; }
.work-bars-total small { color: var(--muted); }
.work-bar-row { display: grid; grid-template-columns: 96px 1fr 70px; align-items: center; gap: 8px; font-size: 11px; }
.work-bar-row > span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--muted); }
.work-bar-row > div { height: 8px; overflow: hidden; border-radius: 999px; background: var(--line); }
.work-bar-row i { display: block; height: 100%; border-radius: inherit; }
.work-bar-row small { color: var(--muted); text-align: right; white-space: nowrap; }
.work-capacity { padding: 10px; border-radius: var(--radius); background: var(--canvas); }
.work-capacity h4 { margin: 0 0 6px; font-size: 12px; }
.work-capacity h4 small, .work-call summary small { color: var(--muted); font-weight: 400; }
.work-capacity p { margin: 3px 0; color: var(--muted); font-size: 11px; }
.work-call { border-top: 1px solid var(--line); padding-top: 6px; }
.work-call summary { display: flex; justify-content: space-between; gap: 8px; cursor: pointer; font-size: 12px; }
.work-call dl { display: grid; gap: 4px; margin: 8px 0 0; font-size: 10px; }
.work-call dl div { display: grid; grid-template-columns: 58px 1fr; gap: 6px; }
.work-call dt { color: var(--muted); }
.work-call dd { margin: 0; overflow-wrap: anywhere; }
.work-call code { white-space: pre-wrap; }
</style>
