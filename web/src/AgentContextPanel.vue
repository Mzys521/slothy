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
  concepts?: { id: string; label: string; kind: string }[]
  source_concept_id?: string | null
  target_concept_id?: string | null
  predicate?: string
  effective_frequency?: number
  strength?: number
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
const trace = computed(() => pending.value?.memory_trace ?? turn.value?.memoryTrace)
const traceLabels: Record<string, [string, string]> = {
  provided: ['Evidence provided to the model', '已向模型提供参考事实'],
  identity_fact: ['Answered directly from identity facts', '根据身份事实直接回答'],
  identity_conflict: ['Identity changes await confirmation', '身份变更存在冲突，尚待确认'],
  no_match: ['No relevant facts matched', '没有匹配到相关事实'],
  no_record: ['No confirmed identity record', '没有已确认的身份记录'],
  disabled: ['Personalized memory is off', '定制记忆已关闭'],
  pending_learning: ['Memory learning is pending', '记忆仍在整理中'],
  pending_confirmation: ['Identity facts await confirmation', '身份事实尚待确认'],
  read_failed: ['Memory read failed', '记忆读取失败'],
  budget_excluded: ['Excluded by the context budget', '受上下文预算限制未提供'],
  concept_relation: ['Semantic concept → related fact', '语义概念 → 关联事实'],
  identity_intent: ['Direct identity recall', '本人身份意图直召'],
  lexical: ['Local text match', '本地文本匹配'],
  profile_attribute: ['Current profile attribute', '当前画像属性'],
}
function traceLabel(code: string) { const pair = traceLabels[code]; return pair ? t(...pair) : code }
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

// Follow a newly requested memory id: switch to the memory tab and expand that entry.
// 响应新传入的记忆 id：切换到记忆标签并展开该条。
watch(() => props.memoryId, id => {
  if (!id) return
  selectedTab.value = 'memory'
  openMemory.value = id
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

/**
 * Move tab selection with the arrow, Home and End keys.
 * 使用方向键、Home 和 End 键切换标签。
 *
 * @param event - Keydown event from the tab list. / 标签列表上的键盘事件。
 */
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
      <section v-if="trace" class="memory-trace" data-testid="memory-trace">
        <p class="monitor-muted">{{ traceLabel(trace.status) }}</p>
        <p v-if="trace.response_source === 'model' && trace.facts.some(fact => fact.included)" class="monitor-muted">{{ t('These facts were included in context; model adoption is not asserted.', '以下事实已放入上下文，不代表模型必然采纳。') }}</p>
        <details v-if="trace.facts.length"><summary>{{ t('Recall details', '召回详情') }} · {{ trace.context_token_estimate }} {{ t('estimated tokens', '预算估算') }}</summary>
          <ul><li v-for="fact in trace.facts" :key="fact.id"><button class="text-link" @click="openMemory = fact.id">{{ fact.id.slice(0, 8) }}</button> · {{ traceLabel(fact.reason) }}<span v-if="fact.strength != null"> · {{ t('Frequency strength', '频次强度') }} {{ fact.strength.toFixed(2) }}</span><small v-if="fact.concept_ids.length">{{ t('Concept path', '概念路径') }} {{ fact.concept_ids.map(id => id.slice(0, 8)).join(' → ') }}</small></li></ul>
        </details>
      </section>
      <p v-if="memoryLoading" class="monitor-muted" role="status">{{ t('Loading memories…', '正在加载记忆…') }}</p>
      <p v-else-if="!snippets.length && !memoryError" class="monitor-muted">
        {{ memoryIds ? t('No facts were provided for this reply.', '本次回复未提供记忆事实。') : t('No memory references recorded yet.', '暂无记忆引用记录。') }}
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
      <p v-if="expanded?.source_concept_id && expanded?.target_concept_id" class="monitor-muted">{{ expanded.concepts?.find(c => c.id === expanded?.source_concept_id)?.label }} → {{ expanded.concepts?.find(c => c.id === expanded?.target_concept_id)?.label }} · {{ expanded.predicate === 'proficiency' ? t('Skill proficiency', '技能熟练度') : expanded.predicate }}</p>
      <p v-if="expanded?.effective_frequency != null" class="monitor-muted">{{ t('Confirmed sessions', '有效确认会话') }} {{ expanded.effective_frequency }} · {{ t('Frequency strength', '频次强度') }} {{ (expanded.strength ?? 0).toFixed(2) }}</p>
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
