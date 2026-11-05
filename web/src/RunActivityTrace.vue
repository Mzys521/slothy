<script setup lang="ts">
import { Check, ChevronRight, Circle, CircleAlert, LoaderCircle, ShieldCheck, X } from 'lucide-vue-next'
import { t } from './preferences'
import type { RunActivity } from './workApi'

const props = defineProps<{ items: RunActivity[]; interrupted?: boolean }>()

function status(entry: RunActivity) {
  if (props.interrupted && ['running', 'requested'].includes(entry.status)) return 'interrupted'
  return entry.status
}

function label(entry: RunActivity) {
  if (entry.kind === 'prepare') return t('Run prepared', '已准备会话')
  if (entry.kind === 'context') {
    return entry.status === 'done'
      ? t(`Context prepared · ${entry.memory_count ?? 0} memories used`, `已准备上下文 · 使用 ${entry.memory_count ?? 0} 条记忆`)
      : t('Preparing context', '正在准备上下文')
  }
  if (entry.kind === 'model') {
    return entry.status === 'done' ? t('Model responded', '模型已返回') : t('Model is processing', '模型正在处理')
  }
  return entry.name
}

function statusLabel(entry: RunActivity) {
  return {
    requested: t('Requested', '已请求'),
    approval: t('Awaiting approval', '等待审批'),
    running: t('Running', '运行中'),
    done: t('Completed', '已完成'),
    error: t('Failed', '执行失败'),
    rejected: t('Declined', '已拒绝'),
    interrupted: t('Interrupted', '已中断'),
  }[status(entry)]
}

function icon(entry: RunActivity) {
  return {
    requested: Circle, approval: ShieldCheck, running: LoaderCircle,
    done: Check, error: CircleAlert, rejected: X, interrupted: CircleAlert,
  }[status(entry)]
}
</script>

<template>
  <div v-for="entry in items" :key="entry.id">
    <details v-if="entry.kind === 'tool'" class="trace-tool">
      <summary class="tool-line">
        <ChevronRight class="trace-chevron" aria-hidden="true" />
        <component :is="icon(entry)" :class="['trace-status', status(entry), { 'loading-icon': status(entry) === 'running' }]" aria-hidden="true" />
        <span class="trace-tool-name">{{ label(entry) }}</span>
        <span>· {{ statusLabel(entry) }}</span>
        <span v-if="entry.approved">· {{ t('Approved', '已批准') }}</span>
      </summary>
      <div class="trace-details">
        <div v-if="entry.arguments != null">{{ t('Arguments', '调用参数') }}<pre>{{ entry.arguments }}</pre></div>
        <div v-if="entry.result != null">{{ t('Tool result', '工具返回') }}<pre>{{ entry.result }}</pre></div>
      </div>
    </details>
    <div v-else class="tool-line">
      <component :is="icon(entry)" :class="['trace-status', status(entry), { 'loading-icon': status(entry) === 'running' }]" aria-hidden="true" />
      <span>{{ status(entry) === 'interrupted' ? statusLabel(entry) : label(entry) }}</span>
    </div>
  </div>
</template>

<style scoped>
.trace-tool summary { cursor: pointer; list-style: none; }
.trace-tool summary::-webkit-details-marker { display: none; }
.trace-chevron { flex: 0 0 14px; }
.trace-tool[open] .trace-chevron { transform: rotate(90deg); }
.trace-tool-name { overflow-wrap: anywhere; }
.trace-status { flex: 0 0 14px; }
.trace-status.done { color: var(--success); }
.trace-status.error, .trace-status.interrupted { color: var(--danger); }
.trace-details { border-left: 1px solid var(--strong-line); margin: 2px 0 6px 6px; padding-left: 12px; }
.trace-details pre { max-height: 220px; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; margin: 3px 0 7px; }
</style>
