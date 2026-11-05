<script setup lang="ts">
import { computed } from 'vue'
import { CircleGauge, Database } from 'lucide-vue-next'
import type { Turn } from './chat'
import { settings, t } from './preferences'

const props = defineProps<{ turns: Turn[] }>()
const rounds = computed(() => props.turns.filter(turn => turn.serverRunId).length)
const known = computed(() => props.turns.flatMap(turn => turn.metrics ? [turn.metrics] : []))
const latest = computed(() => props.turns.at(-1)?.metrics)
const total = computed(() => known.value.reduce((sum, run) => sum + run.total_tokens, 0))
const cache = computed(() => {
  const reported = known.value.reduce((sum, run) => sum + run.cache_reported_input_tokens, 0)
  return reported ? known.value.reduce((sum, run) => sum + run.cached_input_tokens, 0) / reported : null
})
const context = computed(() => latest.value?.context_window
  ? latest.value.context_tokens / latest.value.context_window : null)
const rate = computed(() => latest.value?.model_seconds
  ? latest.value.output_tokens / latest.value.model_seconds : null)
const locale = computed(() => settings.language === 'zh-CN' ? 'zh-CN' : 'en-US')
const percent = (value: number | null) => value == null ? '—' : new Intl.NumberFormat(locale.value, {
  style: 'percent', maximumFractionDigits: 2,
}).format(value)
</script>

<template>
  <div class="conversation-stats" :aria-label="t('Conversation usage', '会话用量')">
    <span>
      <CircleGauge aria-hidden="true" />{{ t(rounds + (rounds === 1 ? ' turn' : ' turns'), rounds + ' 轮') }}
      <span aria-hidden="true">·</span>
      <span :title="t('Latest turn output tokens per second of model time', '最近一轮输出 token ÷ 模型调用耗时')">{{ rate == null ? '—' : rate.toFixed(1) }} tok/s</span>
    </span>
    <span>
      <Database aria-hidden="true" />
      <span :title="t('Total tokens reported in this conversation', '本会话已报告的累计 token 用量')">{{ known.length ? total.toLocaleString(locale) : '—' }} tok</span>
      <span aria-hidden="true">·</span>{{ t('Cache hit', '缓存命中') }} {{ percent(cache) }}
    </span>
    <span :title="t('Latest request tokens / model context window', '最近一次模型请求的 token 用量 ÷ 模型上下文窗口')">
      <i class="context-meter" :style="{ '--context-use': (Math.min(1, context ?? 0) * 100) + '%' }" aria-hidden="true"></i>
      {{ t('Context', '上下文') }} {{ percent(context) }}
    </span>
  </div>
</template>
