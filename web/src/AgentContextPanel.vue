<script setup lang="ts">
import { computed, ref, useId, watch } from 'vue'
import { AtSign, Braces, PanelRightClose } from 'lucide-vue-next'
import { activeWorkspace, runningSessions, workspaceName, workspaceDirectory, type Session } from './chat'
import { settings, t } from './preferences'
import { memoryTypeName, workRequest, type MemoryReceipt } from './workApi'

const props = defineProps<{ session?: Session; memoryId?: string }>()
const emit = defineEmits<{ close: []; evidence: [thread: string, run: string] }>()
const tabs = [
  { id: 'memory', en: 'Related memories', zh: '关联记忆' },
  { id: 'environment', en: 'Environment', zh: '环境上下文' },
  { id: 'tools', en: 'Loaded tools', zh: '已装载工具' },
] as const
type Tab = typeof tabs[number]['id']

interface MemorySnippet {
  id: string
  title: string
  content: string
  type?: MemoryReceipt['type']
  evidence?: { thread_id: string; run_id: string; quote: string; recorded_at?: string | null }[]
}
interface Runtime {
  ready: boolean
  model: string | null
  work_tools: string[]
  chat_tools: string[]
}

const prefix = useId()
const selectedTab = ref<Tab>('memory')
const tabList = ref<HTMLElement>()
const openMemory = ref<string>()
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
const snippets = remoteMemories
const expanded = computed(() => snippets.value.find(item => item.id === openMemory.value))
const isRunning = computed(() => !!props.session && runningSessions.has(props.session.id))
const status = computed(() => {
  if (isRunning.value) return t('Running', '运行中')
  if (pending.value) return t('Awaiting approval', '等待确认')
  if (props.session?.archived) return t('Archived', '已归档')
  if (turn.value) return t('Idle', '空闲')
  return t('Ready', '就绪')
})
const memoryEnabled = computed(() => settings.personalizedMemory && (turn.value?.memory ?? true))
const toolNames = computed(() => (workMode.value ? runtime.value?.work_tools : runtime.value?.chat_tools) ?? [])

watch(() => props.memoryId, id => {
  if (!id) return
  selectedTab.value = 'memory'
  openMemory.value = id
}, { immediate: true })

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

watch([memoryIds, () => props.session?.id], async ([ids], _, onCleanup) => {
  remoteMemories.value = []
  if (!props.memoryId) openMemory.value = undefined
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

function moveTab(event: KeyboardEvent) {
  const index = tabs.findIndex(tab => tab.id === selectedTab.value)
  const next = { ArrowLeft: (index + tabs.length - 1) % tabs.length, ArrowRight: (index + 1) % tabs.length, Home: 0, End: tabs.length - 1 }[event.key]
  if (next === undefined) return
  event.preventDefault()
  selectedTab.value = tabs[next]!.id
  tabList.value?.querySelectorAll('button')[next]?.focus()
}
</script>

<template>
  <aside class="context-monitor" :aria-label="t('Context monitor', '上下文监控')">
    <header class="monitor-header">
      <h2>{{ t('Context monitor', '上下文监控') }}</h2>
      <button type="button" class="chat-icon" :aria-label="t('Close context monitor', '关闭上下文监控')" @click="emit('close')"><PanelRightClose /></button>
    </header>
    <div ref="tabList" class="monitor-tabs" role="tablist" :aria-label="t('Agent context', 'Agent 上下文')" @keydown="moveTab">
      <button v-for="tab in tabs" :id="prefix + '-' + tab.id" :key="tab.id" type="button" role="tab"
        :aria-selected="selectedTab === tab.id" :aria-controls="prefix + '-panel-' + tab.id" :tabindex="selectedTab === tab.id ? 0 : -1"
        @click="selectedTab = tab.id">{{ t(tab.en, tab.zh) }}</button>
    </div>

    <div v-show="selectedTab === 'memory'" :id="prefix + '-panel-memory'" class="monitor-content" role="tabpanel" tabindex="0" :aria-labelledby="prefix + '-memory'">
      <p v-if="memoryLoading" class="monitor-muted" role="status">{{ t('Loading memories…', '正在加载记忆…') }}</p>
      <p v-else-if="!snippets.length && !memoryError" class="monitor-muted">
        {{ memoryIds ? t('No memories used in this reply.', '本次回复未使用记忆。') : t('No memory references recorded yet.', '暂无记忆引用记录。') }}
      </p>
      <ul v-if="snippets.length" class="memory-chips">
        <li v-for="item in snippets" :key="item.id">
          <button type="button" class="memory-chip" :aria-expanded="openMemory === item.id" :title="item.title"
            @click="openMemory = openMemory === item.id ? undefined : item.id">
            <AtSign aria-hidden="true" /><span>{{ item.title }}</span>
          </button>
        </li>
      </ul>
      <p v-if="expanded?.type" class="monitor-muted">{{ memoryTypeName(expanded.type, t) }}</p>
      <p v-if="expanded" class="monitor-snippet">{{ expanded.content }}</p>
      <details v-if="expanded?.evidence?.length" class="monitor-evidence">
        <summary>{{ t('Evidence', '原始证据') }}</summary>
        <blockquote v-for="item in expanded.evidence" :key="item.thread_id + item.run_id">
          {{ item.quote }}
          <small v-if="item.recorded_at">{{ t('Reported', '报告于') }} <time :datetime="item.recorded_at">{{ new Date(item.recorded_at).toLocaleString() }}</time></small>
          <button class="text-link" @click="emit('evidence', item.thread_id, item.run_id)">{{ t('View source conversation', '查看来源会话') }}</button>
        </blockquote>
      </details>
      <p v-if="memoryError" class="monitor-muted" role="status">{{ t('Some referenced memories are unavailable.', '部分引用记忆暂不可用。') }}</p>
      <slot name="memory-actions" />
    </div>

    <div v-show="selectedTab === 'environment'" :id="prefix + '-panel-environment'" class="monitor-content" role="tabpanel" tabindex="0" :aria-labelledby="prefix + '-environment'">
      <dl class="monitor-environment">
        <dt>{{ t('Workspace', '工作区') }}</dt><dd>{{ workspaceName(activeWorkspace) }}</dd>
        <dt>{{ t('Working directory', '工作目录') }}</dt><dd>{{ workspaceDirectory(activeWorkspace) || '—' }}</dd>
        <template v-if="session">
          <dt>{{ t('Session', '会话') }}</dt><dd>{{ session.title || t('New session', '新建会话') }}</dd>
          <dt>{{ t('Mode', '模式') }}</dt><dd>{{ workMode ? t('Work · Local Agent', '工作 · 本地 Agent') : t('Chat', '聊天') }}</dd>
          <dt>{{ t('Status', '状态') }}</dt><dd>{{ status }}</dd>
          <dt>{{ t('Personalized memory', '定制记忆') }}</dt><dd>{{ memoryEnabled ? t('On', '开启') : t('Off', '关闭') }}</dd>
          <dt>{{ t('Model', '模型') }}</dt><dd>{{ turn?.metrics?.model || runtime?.model || (runtimeStatus === 'loading' ? t('Checking…', '正在读取…') : t('Unavailable', '暂不可用')) }}</dd>
          <template v-if="turn?.profileVersion != null">
            <dt>{{ t('Profile version', '画像版本') }}</dt><dd>{{ turn.profileVersion }}</dd>
          </template>
          <template v-if="workMode">
            <template v-if="turn?.work">
              <dt>{{ t('Tool approval', '工具审批') }}</dt><dd>{{ turn.work.approval === 'every' ? t('Every call', '逐项批准') : t('Risky actions', '危险操作需确认') }}</dd>
            </template>
          </template>
        </template>
      </dl>
    </div>

    <div v-show="selectedTab === 'tools'" :id="prefix + '-panel-tools'" class="monitor-content" role="tabpanel" tabindex="0" :aria-labelledby="prefix + '-tools'">
      <ul v-if="toolNames.length" class="monitor-tools">
        <li v-for="name in toolNames" :key="name"><Braces aria-hidden="true" /><code>{{ name }}</code></li>
      </ul>
      <p v-else class="monitor-muted" role="status">
        {{ runtimeStatus === 'loading' ? t('Checking loaded tools…', '正在读取工具…')
          : runtimeStatus === 'unavailable' || !runtime?.ready ? t('Agent runtime unavailable.', 'Agent 运行环境暂不可用。')
          : t('No tools loaded.', '当前未装载工具。') }}
      </p>
    </div>
  </aside>
</template>
