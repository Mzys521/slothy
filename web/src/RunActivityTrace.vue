<script setup lang="ts">
import { Check, ChevronRight, Circle, CircleAlert, LoaderCircle, ShieldCheck, X } from 'lucide-vue-next'
import { computed, ref } from 'vue'
import { t } from './preferences'
import type { RunActivity, RunMetrics, ToolArtifact } from './workApi'
import { activityResult, callRisk, downloadArtifact, irreversibleCall, metricSummary, riskLabel, safeArtifactUrl } from './toolResults'

const props = defineProps<{ items: RunActivity[]; interrupted?: boolean; metrics?: RunMetrics | null }>()
const results = computed(() => new Map(props.items.map(entry => [entry.id, activityResult(entry)])))
const downloading = ref<Record<string, boolean>>({})
const downloadError = ref('')

/** Save an authenticated response as a local download. / 将认证响应保存为本地下载。 */
async function download(artifact: ToolArtifact) {
  downloading.value[artifact.id] = true
  downloadError.value = ''
  try {
    await downloadArtifact(artifact)
  } catch {
    downloadError.value = t('Could not download the artifact. Try again.', '产物下载失败，请重试。')
  } finally {
    downloading.value[artifact.id] = false
  }
}

/**
 * Resolves the display status of an activity entry, marking unfinished work as interrupted.
 *
 * 解析活动条目的展示状态，将未完成的工作标记为已中断。
 *
 * @param entry - One run-activity entry. / 单条运行活动记录。
 * @returns Effective status used for labels and icons. / 用于标签与图标的生效状态。
 */
function status(entry: RunActivity) {
  if (props.interrupted && ['running', 'requested'].includes(entry.status)) return 'interrupted'
  return entry.status
}

/**
 * Builds the status-aware primary label for an activity entry.
 *
 * 为活动条目生成结合状态的标题文本。
 *
 * @param entry - One run-activity entry. / 单条运行活动记录。
 * @returns Human-readable label. / 可读标签。
 */
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

/**
 * Returns the localized status caption for an activity entry.
 *
 * 返回活动条目的本地化状态说明。
 *
 * @param entry - One run-activity entry. / 单条运行活动记录。
 * @returns Localized status text. / 本地化状态文本。
 */
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

/**
 * Picks the lucide icon component for an activity entry's status.
 *
 * 为活动条目的状态选择对应的 lucide 图标组件。
 *
 * @param entry - One run-activity entry. / 单条运行活动记录。
 * @returns Icon component for the effective status. / 生效状态对应的图标组件。
 */
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
        <span v-if="callRisk(entry)" :class="{ 'tool-risk-danger': irreversibleCall(entry) }">· {{ riskLabel(callRisk(entry), t) }}</span>
      </summary>
      <div class="trace-details">
        <div v-if="entry.arguments != null">{{ t('Arguments', '调用参数') }}<pre>{{ entry.arguments }}</pre></div>
        <template v-if="results.get(entry.id)">
          <p>{{ results.get(entry.id)!.summary }}</p>
          <p v-if="results.get(entry.id)!.error" role="status">{{ results.get(entry.id)!.error!.code }} · {{ results.get(entry.id)!.error!.hint }}</p>
          <ul v-if="entry.artifacts?.length">
            <li v-for="artifact in entry.artifacts" :key="artifact.id">
              <button v-if="safeArtifactUrl(artifact.url)" class="artifact-download" :disabled="downloading[artifact.id]" @click="download(artifact)">{{ artifact.name }}</button>
              <span v-else>{{ artifact.name }}</span> · {{ artifact.bytes }} B <code>{{ artifact.rel_path }}</code>
            </li>
          </ul>
          <details><summary>{{ t('Structured result', '结构化结果') }}</summary><pre>{{ entry.result }}</pre></details>
        </template>
        <div v-else-if="entry.result != null">{{ t('Tool result', '工具返回') }}<pre>{{ entry.result }}</pre></div>
      </div>
    </details>
    <div v-else class="tool-line">
      <component :is="icon(entry)" :class="['trace-status', status(entry), { 'loading-icon': status(entry) === 'running' }]" aria-hidden="true" />
      <span>{{ status(entry) === 'interrupted' ? statusLabel(entry) : label(entry) }}</span>
    </div>
  </div>
  <p v-if="downloadError" role="status">{{ downloadError }}</p>
  <details v-if="metrics?.tools?.length" class="trace-tool-metrics">
    <summary>{{ t('Tool measurements', '工具度量') }}</summary>
    <dl><template v-for="metric in metrics.tools" :key="metric.name"><dt>{{ metric.name }}</dt><dd>{{ metricSummary(metric, t) }}</dd></template></dl>
  </details>
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
.trace-details code { overflow-wrap: anywhere; }
.artifact-download { padding: 0; color: inherit; background: none; border: 0; text-decoration: underline; cursor: pointer; }
.trace-tool-metrics { margin-top: 8px; color: var(--muted); }
.trace-tool-metrics summary { cursor: pointer; }
.trace-tool-metrics dd { margin: 3px 0 8px; overflow-wrap: anywhere; }
</style>
