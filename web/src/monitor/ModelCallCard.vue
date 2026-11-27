<script setup lang="ts">
import { computed } from 'vue'
import { ChevronDown } from 'lucide-vue-next'
import { t } from '../preferences'
import CopyValue from './CopyValue.vue'
import type { JournalEvent } from './workMetrics'

const props = defineProps<{ call: JournalEvent; number?: number }>()
const ids = computed(() => [
  { label: t('Run', '运行 ID'), value: props.call.run_id },
  { label: t('Step', '步骤 ID'), value: props.call.step_id },
  { label: t('Model call', '模型调用 ID'), value: props.call.model_call_id },
])
const json = computed(() => JSON.stringify(props.call.context, null, 2))
const failed = computed(() => ['failed', 'error'].includes(props.call.status ?? ''))
</script>
<template>
  <details class="monitor-card model-call">
    <summary>
      <span class="call-heading"><small v-if="number != null">{{ t('Call', '调用') }} {{ number }}</small><strong>{{ call.model || call.provider || '—' }}</strong></span>
      <span class="monitor-badge" :data-state="failed ? 'failed' : call.status">{{ failed ? 'error' : call.status ?? '—' }}</span>
      <span class="call-duration">{{ call.duration_ms ?? '—' }} ms</span><ChevronDown class="monitor-chevron" aria-hidden="true" />
    </summary>
    <dl>
      <div v-if="call.provider"><dt>{{ t('Provider', '服务商') }}</dt><dd>{{ call.provider }}</dd></div>
      <div v-for="id in ids" :key="id.label"><dt>{{ id.label }}</dt><dd><CopyValue v-if="id.value" :value="id.value" :label="t('Copy ', '复制') + id.label">{{ id.value }}</CopyValue><span v-else>—</span></dd></div>
      <div><dt>{{ t('Parameters', '参数') }}</dt><dd><code>{{ JSON.stringify(call.parameters ?? {}) }}</code></dd></div>
      <div><dt>{{ t('Tools', '工具数') }}</dt><dd>{{ call.tools?.count ?? '—' }} <small v-if="call.tools?.schema_tokens != null">· {{ call.tools.schema_tokens }} {{ t('est. tokens', '估算 Token') }}</small></dd></div>
    </dl>
    <details v-if="json" class="call-context"><summary><ChevronDown class="monitor-chevron" aria-hidden="true" />{{ t('Context JSON', '上下文 JSON') }}</summary>
      <CopyValue :value="json" :label="t('Copy context JSON', '复制上下文 JSON')">{{ t('Copy JSON', '复制 JSON') }}</CopyValue><pre>{{ json }}</pre>
    </details>
  </details>
</template>
<style scoped>
.model-call { min-width: 0; }
summary { display: flex; align-items: center; gap: 8px; list-style: none; }
summary::-webkit-details-marker { display: none; }
.call-heading { display: grid; flex: 1; min-width: 0; }
.call-heading strong { overflow-wrap: anywhere; line-height: 1.4; }
.call-heading small, dt, dd small, .call-duration { color: var(--muted); font-size: var(--type-sm); }
.call-duration { white-space: nowrap; font-variant-numeric: tabular-nums; }
[data-state="success"] { color: var(--success); background: color-mix(in srgb, var(--success) 12%, var(--surface)); }
[data-state="failed"] { color: var(--danger); background: color-mix(in srgb, var(--danger) 12%, var(--surface)); }
dl { display: grid; gap: 7px; border-top: 1px solid var(--line); padding-block: 10px; margin-top: 12px; }
dl > div { display: grid; grid-template-columns: minmax(60px, 24%) minmax(0, 1fr); align-items: baseline; gap: 8px; }
dd { margin: 0; min-width: 0; overflow-wrap: anywhere; }
.call-context { border-top: 1px solid var(--line); padding-top: 7px; }
.call-context summary { color: var(--muted); min-height: 30px; font-size: var(--type-sm); }
.call-context pre { white-space: pre-wrap; overflow-wrap: anywhere; padding: 8px; border-radius: 6px; background: var(--canvas); font-size: var(--type-sm); }
</style>
