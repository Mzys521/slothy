<script setup lang="ts">
/**
 * Background memory-learning status panel with its own poll loop.
 *
 * Keeping the poll here means the memory page frame and other lists do not re-render
 * every 1.8 s while learning is pending.
 *
 * 独立轮询的后台记忆学习状态面板；轮询留在子组件内，记忆页框架与其他列表不会每 1.8 秒重渲染。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { t } from './preferences'
import { workRequest } from './workApi'

type Learning = {
  enabled: boolean
  total_turns: number
  pending: number
  processed: number
  failed: number
  suggestions: number
  observed: number
  profile_version: number
  engine: 'rule_review' | 'llm_learning'
  mode: string
  dead: number
  last_error: string | null
  statistics?: {
    states: Record<string, number>
    events: Record<string, number>
    reinforcement_dedup_rate: number | null
  }
}

const emit = defineEmits<{ reload: [] }>()
const learning = ref<Learning>()
const busy = ref(false)
const error = ref('')
const notice = ref('')
let timer: number | undefined
let disposed = false

const emptyDescription = computed(() => {
  if (learning.value?.mode === 'shadow') return t('Shadow mode evaluates conversations without saving memory or profile changes.', '影子运行只评估对话，不保存记忆或画像变更。')
  if (learning.value?.mode === 'off') return t('Resume background learning in settings to analyze completed conversations.', '在设置中恢复后台学习后，将继续分析已完成的对话。')
  return learning.value?.engine === 'llm_learning' && learning.value?.mode === 'auto'
    ? t('The model saves durable, evidence-backed memories automatically. Ordinary task questions do not create a user profile.', '模型会自动沉淀有证据的长期记忆；普通任务提问不会被编造成用户画像。')
    : t('Confirmed identity, skill and preference facts project into one profile.', '已确认的身份、技能和偏好等事实投影到同一份画像。')
})

/**
 * Reads the current learning status and schedules the next poll only while work is pending.
 *
 * 读取当前学习状态；仅在有任务待处理时安排下一次轮询。
 */
async function load() {
  if (disposed) return
  window.clearTimeout(timer)
  try {
    const value = await workRequest<Learning>('/memory-learning')
    const wasPending = (learning.value?.pending ?? 0) > 0
    learning.value = value
    error.value = ''
    if (wasPending && value.pending === 0) emit('reload')
    if (value.pending > 0) timer = window.setTimeout(load, 1800)
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : t('Could not load learning status.', '无法读取学习状态。')
  }
}

onMounted(load)
onBeforeUnmount(() => { disposed = true; window.clearTimeout(timer) })

/**
 * Queues all completed turns for background reanalysis, then refreshes this panel.
 *
 * 将所有已完成对话重新加入后台分析队列，然后刷新本面板。
 */
async function reprocess() {
  if (busy.value) return
  busy.value = true
  error.value = ''
  try {
    const result = await workRequest<{ queued: number }>('/memory-learning/reprocess', 'POST', {})
    notice.value = t(`${result.queued} completed turns queued for reanalysis.`, `已提交 ${result.queued} 轮已完成对话重新分析。`)
    emit('reload')
    await load()
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : t('Could not queue reanalysis.', '无法提交重新分析。')
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <section v-if="learning" class="memory-learning-status" aria-live="polite">
    <div>
      <h2>{{ t('Memory learning', '记忆学习') }}</h2>
      <p v-if="!learning.enabled" class="muted">{{ t('Personalized memory is off.', '定制记忆已关闭。') }}</p>
      <p v-else class="muted">{{ t(`${learning.processed} turns analyzed · ${learning.pending} pending · ${learning.suggestions} to review`, `已分析 ${learning.processed} 轮 · 待处理 ${learning.pending} 轮 · 待确认 ${learning.suggestions} 项`) }}</p>
      <p v-if="learning.statistics" class="muted">{{ t('Reinforcement deduplication', '强化去重率') }} {{ learning.statistics.reinforcement_dedup_rate == null ? '—' : (learning.statistics.reinforcement_dedup_rate * 100).toFixed(1) + '%' }} · {{ t('Conflicts', '冲突版本') }} {{ learning.statistics.events.conflict ?? 0 }} · {{ t('Capacity blocks', '容量阻塞') }} {{ learning.statistics.events.capacity_blocked ?? 0 }}</p>
      <p v-if="learning.mode === 'off' && learning.enabled" class="muted">{{ t('Background learning is paused in settings.', '已在设置中暂停后台学习。') }}</p>
      <p v-if="learning.failed" role="alert">{{ learning.dead ? t(`${learning.dead} turns reached the retry limit. Reanalyze to try again.`, `${learning.dead} 轮已达到重试上限，可重新分析。`) : t('Some turns could not be analyzed and will retry.', '部分轮次分析失败，系统会重试。') }} <code v-if="learning.last_error">{{ learning.last_error }}</code></p>
      <p v-else-if="learning.enabled && !learning.pending && !learning.suggestions && !learning.profile_version" class="muted">{{ emptyDescription }}</p>
      <p v-if="error" role="alert">{{ error }}</p>
      <p v-if="notice" role="status">{{ notice }}</p>
    </div>
    <button class="quiet-button" :disabled="busy || !learning.enabled || learning.pending > 0" @click="reprocess">{{ t('Reanalyze conversations', '重新分析已有对话') }}</button>
  </section>
</template>
