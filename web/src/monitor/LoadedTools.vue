<script setup lang="ts">
import { computed } from 'vue'
import { t } from '../preferences'
import { riskLabel } from '../toolResults'
import CopyValue from './CopyValue.vue'

const props = defineProps<{
  names: string[]
  groups?: Record<string, string[]>
  workMode: boolean
  loading: boolean
  ready: boolean
}>()

const tools = computed(() => {
  const risks = new Map<string, string>()
  for (const [risk, members] of Object.entries(props.groups ?? {})) {
    for (const name of members) risks.set(name, risk)
  }
  return Array.from(new Set(props.names), name => ({ name, risk: risks.get(name) }))
})
</script>

<template>
  <div class="loaded-tools">
    <template v-if="tools.length">
      <header class="monitor-card tools-summary">
        <h3>{{ workMode ? t('Work mode tools', '工作模式工具') : t('Chat mode tools', '聊天模式工具') }}</h3>
        <strong>{{ tools.length }} <small>{{ t(tools.length === 1 ? 'tool' : 'tools', '项') }}</small></strong>
        <p v-if="!ready" class="monitor-muted" role="status">{{ t('Agent runtime unavailable.', 'Agent 运行环境暂不可用。') }}</p>
      </header>
      <ul class="tool-cards">
        <li v-for="tool in tools" :key="tool.name" class="monitor-card">
          <code>{{ tool.name }}</code>
          <div class="tool-actions">
            <span class="monitor-badge" :data-risk="tool.risk">{{ riskLabel(tool.risk, t) }}</span>
            <CopyValue :value="tool.name" :label="t('Copy tool name', '复制工具名') + ' ' + tool.name" />
          </div>
        </li>
      </ul>
    </template>
    <p v-else class="monitor-card monitor-muted" role="status">
      {{ loading ? t('Checking loaded tools…', '正在读取工具…')
        : !ready ? t('Agent runtime unavailable.', 'Agent 运行环境暂不可用。')
        : t('No tools loaded.', '当前未装载工具。') }}
    </p>
  </div>
</template>

<style scoped>
.loaded-tools, .tool-cards { display: grid; gap: 10px; }
.tools-summary, .tool-actions { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.tools-summary { flex-wrap: wrap; }
.tools-summary h3 { font-size: var(--type-reading); }
.tools-summary strong { font-size: var(--type-title); line-height: 1.2; font-variant-numeric: tabular-nums; }
.tools-summary small { font-size: var(--type-sm); color: var(--muted); font-weight: 400; }
.tools-summary p { flex-basis: 100%; }
.tool-cards { grid-template-columns: repeat(2, minmax(0, 1fr)); margin: 0; padding: 0; list-style: none; }
.tool-cards li { display: flex; flex-direction: column; justify-content: space-between; gap: 6px; }
.tool-cards code { font-size: var(--type-body); font-weight: 600; overflow-wrap: anywhere; }
.copy-value { justify-content: center; min-width: 32px; min-height: 32px; }
[data-risk="read"] { color: var(--success); }
[data-risk="write"], [data-risk="network"] { color: var(--pending); }
[data-risk="delete"] { color: var(--danger); }
[data-risk="execute"] { color: var(--accent-ink); }
@container monitor (width < 360px) {
  .tool-cards { grid-template-columns: minmax(0, 1fr); }
  .tool-cards li { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; gap: 8px; }
  .tool-actions { flex-wrap: wrap; }
}
</style>
