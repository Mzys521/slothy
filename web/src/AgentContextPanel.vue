<script setup lang="ts">
import { computed, ref, useId, watch } from 'vue'
import { PanelRightClose } from 'lucide-vue-next'
import { activeWorkspace, runningSessions, workspaceName, workspaceDirectory, type Session } from './chat'
import { settings, t, readLocal, writeLocal } from './preferences'
import { workRequest } from './workApi'
import WorkDetails from './WorkDetails.vue'
import ContextMonitorTabs from './monitor/ContextMonitorTabs.vue'
import EnvironmentContext from './monitor/EnvironmentContext.vue'
import LoadedTools from './monitor/LoadedTools.vue'
import RelatedMemories from './monitor/RelatedMemories.vue'
import type { MemorySnippet } from './monitor/memoryCards'

const props = defineProps<{ session?: Session; memoryId?: string }>()
const emit = defineEmits<{ close: []; graph: [id: string]; evidence: [thread: string, run: string] }>()
interface Runtime {
  ready: boolean
  model: string | null
  work_tools: string[]
  chat_tools: string[]
  tool_groups?: Record<string, string[]>
}

const prefix = useId()
const panelWidth = ref(Number(readLocal('adaptive.contextPanelWidth', 480)))

/** Drag the invisible left edge to resize the context monitor. / 拖动不可见左边缘调整面板宽度。 */
function startResize(event: PointerEvent) {
  event.preventDefault()
  const startX = event.clientX
  const startWidth = panelWidth.value
  const target = event.currentTarget as HTMLElement
  target.setPointerCapture(event.pointerId)
  const move = (moveEvent: PointerEvent) => {
    panelWidth.value = Math.min(560, Math.max(260, startWidth + startX - moveEvent.clientX))
  }
  const up = () => {
    target.removeEventListener('pointermove', move)
    window.removeEventListener('pointerup', up)
    writeLocal('adaptive.contextPanelWidth', panelWidth.value)
  }
  target.addEventListener('pointermove', move)
  window.addEventListener('pointerup', up)
}
const selectedTab = ref<'memory' | 'environment' | 'tools' | 'work'>('memory')
const runtime = ref<Runtime>()
const runtimeStatus = ref<'loading' | 'ready' | 'unavailable'>('loading')
const remoteMemories = ref<MemorySnippet[]>([])
const memoryLoading = ref(false)
const memoryError = ref(false)
const turn = computed(() => props.session?.turns.at(-1))
const workMode = computed(() => props.session?.mode === 'work')
const pending = computed(() => props.session?.pendingReview)
const memoryIds = computed(() => props.memoryId
  ? [props.memoryId]
  : pending.value?.used_memory_ids ?? turn.value?.usedMemoryIds)
const trace = computed(() => pending.value?.memory_trace ?? turn.value?.memoryTrace)
const isRunning = computed(() => !!props.session && runningSessions.has(props.session.id))
const status = computed(() => {
  if (isRunning.value) return t('Running', '运行中')
  if (pending.value) return t('Awaiting approval', '等待确认')
  if (props.session?.archived) return t('Archived', '已归档')
  if (turn.value) return t('Idle', '空闲')
  return t('Ready', '就绪')
})
const memoryEnabled = computed(() => settings.personalizedMemory && (turn.value?.memory ?? true))
const modelLabel = computed(() => turn.value?.metrics?.model || runtime.value?.model ||
  (runtimeStatus.value === 'loading' ? t('Checking…', '正在读取…') : t('Unavailable', '暂不可用')))
const toolNames = computed(() => (workMode.value ? runtime.value?.work_tools : runtime.value?.chat_tools) ?? [])

// Follow a newly requested memory id: switch to the memory tab and expand that entry.
// 响应新传入的记忆 id：切换到记忆标签并展开该条。
watch(() => props.memoryId, id => {
  if (!id) return
  selectedTab.value = 'memory'
}, { immediate: true })

// Reload the runtime description whenever the active session changes; ignore stale responses.
// 切换会话时重新读取运行环境描述，并忽略过期的响应。
watch(() => props.session?.id, async (_, __, onCleanup) => {
  runtime.value = undefined
  let current = true
  onCleanup(() => { current = false })
  runtimeStatus.value = 'loading'
  try {
    const result = await workRequest<Runtime>('/runtime')
    if (current) {
      runtime.value = result
      runtimeStatus.value = 'ready'
    }
  } catch {
    if (current) runtimeStatus.value = 'unavailable'
  }
}, { immediate: true })

// Fetch every memory referenced by the latest turn or by the requested memory id.
// 拉取最近一轮或指定记忆 id 所引用的全部记忆。
watch([memoryIds, () => props.session?.id], async ([ids], _, onCleanup) => {
  remoteMemories.value = []
  memoryLoading.value = false
  memoryError.value = false
  if (!ids?.length) return
  let current = true
  onCleanup(() => { current = false })
  memoryLoading.value = true
  const records = await Promise.allSettled([...new Set(ids)].map(id =>
    workRequest<MemorySnippet>('/memories/' + encodeURIComponent(id)),
  ))
  if (!current) return
  remoteMemories.value = records.flatMap(record => record.status === 'fulfilled' ? [record.value] : [])
  memoryError.value = records.some(record => record.status === 'rejected')
  memoryLoading.value = false
}, { immediate: true })

</script>

<template>
  <aside class="context-monitor" :style="{ flexBasis: panelWidth + 'px', '--panel-width': panelWidth + 'px' }" :aria-label="t('Context monitor', '上下文监控')">
    <div class="panel-resize-handle" aria-hidden="true" @pointerdown="startResize"></div>
    <header class="monitor-header">
      <h2>{{ t('Context monitor', '上下文监控') }}</h2>
      <button type="button" class="chat-icon" :aria-label="t('Close context monitor', '关闭上下文监控')" @click="emit('close')"><PanelRightClose /></button>
    </header>
    <ContextMonitorTabs v-model="selectedTab" :prefix="prefix" />

    <div v-show="selectedTab === 'work'" :id="prefix + '-panel-work'" class="monitor-content" role="tabpanel" tabindex="0" :aria-labelledby="prefix + '-work'">
      <WorkDetails :thread="session?.apiThreadId" :run="pending?.run_id ?? session?.pendingRun?.run_id ?? turn?.serverRunId" />
    </div>

    <div v-show="selectedTab === 'memory'" :id="prefix + '-panel-memory'" class="monitor-content" role="tabpanel" tabindex="0" :aria-labelledby="prefix + '-memory'">
      <RelatedMemories :ids="memoryIds" :trace="trace" :details="remoteMemories" :loading="memoryLoading" :error="memoryError" :focus="memoryId"
        @graph="id => emit('graph', id)" @evidence="(thread, run) => emit('evidence', thread, run)" />
    </div>

    <div v-show="selectedTab === 'environment'" :id="prefix + '-panel-environment'" class="monitor-content" role="tabpanel" tabindex="0" :aria-labelledby="prefix + '-environment'">
      <EnvironmentContext :workspace="workspaceName(activeWorkspace)" :directory="workspaceDirectory(activeWorkspace)"
        :session="session" :turn="turn" :status="status" :model="modelLabel" :memory-enabled="memoryEnabled" />
    </div>

    <div v-show="selectedTab === 'tools'" :id="prefix + '-panel-tools'" class="monitor-content" role="tabpanel" tabindex="0" :aria-labelledby="prefix + '-tools'">
      <LoadedTools :names="toolNames" :groups="runtime?.tool_groups" :work-mode="workMode"
        :loading="runtimeStatus === 'loading'" :ready="runtime?.ready === true" />
    </div>
  </aside>
</template>
