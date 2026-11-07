<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref } from 'vue'
import { t } from './preferences'
import { memoryScope, memoryTypeName, workRequest, type MemoryReceipt } from './workApi'
import ProfileEditor from './ProfileEditor.vue'
import MemoryCreateDialog from './MemoryCreateDialog.vue'

type Evidence = { thread_id: string; run_id: string; quote: string; source: string; recorded_at?: string | null }
/** Memory candidate awaiting user review, including its evidence and target profile attribute. / 待用户审核的记忆候选，包含证据与目标画像属性。 */
type Suggestion = { id: string; title: string; scope: MemoryReceipt['scope']; scope_ref: string | null; value: string; dimension: string; profile_key: string; pending_reason?: string | null; evidence: Evidence[] }
type Attribute = { key: string; value: unknown; dimension: string; scope: MemoryReceipt['scope']; scope_ref: string | null; memory_id: string; target_label?: string | null; predicate?: string }
/** Versioned user profile snapshot plus counters for attributes removed or permanently erased. / 带版本的用户画像快照，以及被移除或永久清除的属性计数。 */
type Profile = { version: number; attributes?: Attribute[]; removed_attributes?: number; redacted_attributes?: number }
type Version = { version: number; change_summary: string; created_at: string; removed_attributes?: number; redacted_attributes?: number }
/** Background memory-learning status reported by the server. / 服务端返回的后台记忆学习状态。 */
type Learning = { enabled: boolean; total_turns: number; pending: number; processed: number; failed: number; suggestions: number; observed: number; profile_version: number; engine: 'rule_review' | 'llm_learning'; mode: string; dead: number; last_error: string | null; statistics?: { states: Record<string, number>; events: Record<string, number>; reinforcement_dedup_rate: number | null } }

const emit = defineEmits<{ memory: [id: string]; evidence: [thread: string, run: string] }>()
const suggestions = ref<Suggestion[]>([])
const memories = ref<(MemoryReceipt & { content: string })[]>([])
const profile = ref<Profile>()
const versions = ref<Version[]>([])
const historical = ref<Profile>()
const editing = ref('')
const draft = ref('')
const draftKey = ref('')
const deleting = ref<MemoryReceipt>()
const busy = ref(false)
const loading = ref(false)
const loadError = ref('')
const actionError = ref('')
const learning = ref<Learning>()
/** Explains the empty learning state for the current engine/mode. / 根据当前引擎与模式解释学习状态为空的原因。 */
const emptyLearningDescription = computed(() => {
  if (learning.value?.mode === 'shadow') return t('Shadow mode evaluates conversations without saving memory or profile changes.', '影子运行只评估对话，不保存记忆或画像变更。')
  if (learning.value?.mode === 'off') return t('Resume background learning in settings to analyze completed conversations.', '在设置中恢复后台学习后，将继续分析已完成的对话。')
  return learning.value?.engine === 'llm_learning' && learning.value?.mode === 'auto'
    ? t('The model saves durable, evidence-backed memories automatically. Ordinary task questions do not create a user profile.', '模型会自动沉淀有证据的长期记忆；普通任务提问不会被编造成用户画像。')
    : t('Confirmed identity, skill and preference facts project into one profile.', '已确认的身份、技能和偏好等事实投影到同一份画像。')
})
const notice = ref('')
const memoryStatus = ref('active')
const restoring = ref<MemoryReceipt>()
const nextVersion = ref<number | null>(null)
const historyLimit = ref(200)
const loadingVersions = ref(false)
let poll: number | undefined
let disposed = false
const selectedProfile = computed(() => historical.value ?? profile.value)
const dimensionNames: Record<string, [string, string]> = {
  identity: ['Identity', '身份'], skill: ['Skill', '技能'],
  preference: ['Preference', '偏好'], habit: ['Habit', '习惯'],
  goal: ['Long-term goal', '长期目标'], fact: ['Fact', '事实'],
  episodic: ['Episodic memory', '情景记忆'],
}
/**
 * Returns the localized label for a suggestion/attribute dimension.
 *
 * 返回建议或属性维度的本地化名称。
 *
 * @param dimension - Dimension key such as preference, habit or goal. / 维度键，例如 preference、habit 或 goal。
 * @returns Localized label, or the raw key when it is unknown. / 本地化名称；未知维度返回原始键。
 */
function dimensionName(dimension: string) {
  const label = dimensionNames[dimension]
  return label ? t(...label) : dimension
}
/** Show target-specific skills rather than a universal beginner label. / 技能展示始终带对象，避免全局“新手”标签。 */
function attributeText(attribute: Attribute) {
  const value = typeof attribute.value === 'string' ? attribute.value : JSON.stringify(attribute.value)
  if (attribute.dimension !== 'skill') return dimensionName(attribute.dimension) + ' · ' + value
  const target = attribute.target_label || attribute.key.split('.').slice(1, -1).join('.')
  const levels: Record<string, [string, string]> = { beginner: ['Beginner', '新手'], intermediate: ['Intermediate', '中级'], advanced: ['Advanced', '熟练'], expert: ['Expert', '精通'] }
  const level = typeof attribute.value === 'string' ? levels[attribute.value] : undefined
  const description = attribute.key.endsWith('.learning') ? attribute.value ? t('Learning', '正在学习') : t('Not currently learning', '当前未学习') : level ? t(...level) : value
  return target + ' · ' + description
}

/** Diffs the historical profile snapshot against the current one for attributes and manual preferences. / 对比历史画像快照与当前画像的属性及手动偏好差异。 */
const differences = computed(() => {
  if (!historical.value || !profile.value) return []
  /** Builds a stable identity key for a profile attribute. / 为画像属性生成稳定的标识键。 */
  const key = (attribute: Attribute) => JSON.stringify([attribute.key, attribute.scope, attribute.scope_ref])
  const previous = new Map((historical.value.attributes ?? []).map(attribute => [key(attribute), attribute]))
  const current = new Map((profile.value.attributes ?? []).map(attribute => [key(attribute), attribute]))
  const attributes = [...new Set([...previous.keys(), ...current.keys()])].flatMap(id => {
    const before = previous.get(id)
    const after = current.get(id)
    if (JSON.stringify(before?.value) === JSON.stringify(after?.value)) return []
    return [{ id, before: before?.value, after: after?.value }]
  })
  return attributes
})

/** Loads suggestions, memories, profile, versions and learning status; schedules a follow-up refresh while work is pending. / 加载建议、记忆、画像、版本与学习状态，并在有待处理任务时安排轮询刷新。 */
async function load() {
  if (disposed || loading.value) return
  window.clearTimeout(poll)
  loading.value = true
  loadError.value = ''
  const results = await Promise.allSettled([
    workRequest<{ items: Suggestion[] }>('/memory-suggestions').then(value => { suggestions.value = value.items }),
    workRequest<{ items: (MemoryReceipt & { content: string })[] }>('/memories?limit=200&status=' + memoryStatus.value).then(value => { memories.value = value.items }),
    workRequest<Profile>('/profile').then(value => { profile.value = value }),
    workRequest<{ items: Version[]; next_cursor: number | null; retention_limit: number }>('/profile/versions').then(value => { versions.value = value.items; nextVersion.value = value.next_cursor; historyLimit.value = value.retention_limit }),
    workRequest<Learning>('/memory-learning').then(value => { learning.value = value }),
  ])
  if (results.some(result => result.status === 'rejected')) {
    loadError.value = t('Some memory data could not be loaded.', '部分记忆数据加载失败。')
  }
  loading.value = false
  // Poll again while learning is busy or newer profile/suggestion data is expected. / 当学习任务繁忙或预计会有更新的画像/建议数据时再次轮询。
  if (!disposed && (learning.value?.pending || (learning.value?.profile_version ?? 0) > (profile.value?.version ?? 0) || (learning.value?.suggestions ?? 0) > suggestions.value.length)) {
    poll = window.setTimeout(load, 1800)
  }
}
// Refresh when the window regains focus, and clean up listeners/timers on unmount. / 窗口重获焦点时刷新，并在卸载时清理监听器与定时器。
onMounted(() => { void load(); window.addEventListener('focus', load) })
onBeforeUnmount(() => { disposed = true; window.clearTimeout(poll); window.removeEventListener('focus', load) })

/** Appends the next page of older profile versions. / 追加下一页更早的画像版本。 */
async function moreVersions() {
  if (nextVersion.value === null || loadingVersions.value) return
  loadingVersions.value = true
  try {
    const result = await workRequest<{ items: Version[]; next_cursor: number | null }>('/profile/versions?before=' + nextVersion.value)
    versions.value.push(...result.items)
    nextVersion.value = result.next_cursor
  } catch (error) { actionError.value = error instanceof Error ? error.message : String(error) }
  finally { loadingVersions.value = false }
}
/** Queues all completed turns for background reanalysis. / 将所有已完成对话加入后台重新分析队列。 */
async function reprocess() {
  await change(async () => {
    const result = await workRequest<{ queued: number }>('/memory-learning/reprocess', 'POST', {})
    notice.value = t(`${result.queued} completed turns queued for reanalysis.`, `已提交 ${result.queued} 轮已完成对话重新分析。`)
  })
}
/** Number of attributes permanently erased from the displayed historical snapshot. / 当前展示的历史快照中被永久清除的属性数量。 */
const erasedCount = computed(() => historical.value?.redacted_attributes ?? 0)

/**
 * Runs a mutating action with busy/error handling, then reloads all panels.
 *
 * 以忙碌/错误处理执行会修改数据的操作，随后重新加载所有面板。
 *
 * @param action - Async mutation to execute before refreshing. / 刷新前要执行的异步修改操作。
 * @returns Resolves when the action and refresh finish. / 操作与刷新完成后结束。
 */
async function change(action: () => Promise<void>) {
  if (busy.value) return
  busy.value = true
  actionError.value = ''
  try {
    await action()
    editing.value = ''
    deleting.value = undefined
    restoring.value = undefined
    historical.value = undefined
    await load()
  } catch (error) {
    actionError.value = error instanceof Error ? error.message : t('Could not save changes.', '无法保存更改。')
  } finally {
    busy.value = false
  }
}

/**
 * Confirms or ignores a memory suggestion, optionally with an edited value or target profile attribute.
 *
 * 确认或忽略一条记忆建议，可附带修改后的内容或要替换的画像属性。
 *
 * @param item - Suggestion being reviewed. / 正在审核的建议。
 * @param action - confirm to save it, ignore to dismiss it. / confirm 表示保存，ignore 表示忽略。
 * @returns Resolves when the review request and reload complete. / 审核请求与重新加载完成后结束。
 */
function review(item: Suggestion, action: 'confirm' | 'ignore') {
  const value = action === 'confirm' && editing.value === item.id ? draft.value.trim() : undefined
  return change(async () => {
    await workRequest('/memory-suggestions/' + encodeURIComponent(item.id) + '/review', 'POST', { action, value,
      ...(action === 'confirm' && editing.value === item.id && draftKey.value ? { profile_key: draftKey.value } : {}) })
  })
}

/**
 * Loads one historical profile version for comparison with the current one.
 *
 * 加载一个历史画像版本，用于与当前版本对比。
 *
 * @param version - Profile version number to load. / 要加载的画像版本号。
 * @returns Resolves once the snapshot is loaded or the error is shown. / 快照加载完成或错误显示后结束。
 */
async function showVersion(version: number) {
  actionError.value = ''
  try {
    historical.value = await workRequest<Profile>('/profile/versions/' + version)
  } catch {
    actionError.value = t('Could not load this version.', '无法加载此版本。')
  }
}
</script>

<template>
  <main class="simple-page memory-management">
    <header>
      <h1>{{ t('Memory and user profile', '长期记忆与用户画像') }}</h1>
      <div class="memory-page-actions"><MemoryCreateDialog @saved="load" /><button class="outline-button" :disabled="loading || busy" @click="load">{{ t('Refresh', '刷新') }}</button></div>
    </header>
    <p v-if="loading" role="status">{{ t('Loading…', '正在加载…') }}</p>
    <p v-if="loadError" role="alert">{{ loadError }}</p>
    <p v-if="actionError" role="alert">{{ actionError }}</p>
    <p v-if="notice" role="status">{{ notice }}</p>
    <section v-if="learning" class="memory-learning-status" aria-live="polite">
      <div><h2>{{ t('Memory learning', '记忆学习') }}</h2>
        <p v-if="!learning.enabled" class="muted">{{ t('Personalized memory is off.', '定制记忆已关闭。') }}</p>
        <p v-else class="muted">{{ t(`${learning.processed} turns analyzed · ${learning.pending} pending · ${learning.suggestions} to review`, `已分析 ${learning.processed} 轮 · 待处理 ${learning.pending} 轮 · 待确认 ${learning.suggestions} 项`) }}</p>
        <p v-if="learning.statistics" class="muted">{{ t('Reinforcement deduplication', '强化去重率') }} {{ learning.statistics.reinforcement_dedup_rate == null ? '—' : (learning.statistics.reinforcement_dedup_rate * 100).toFixed(1) + '%' }} · {{ t('Conflicts', '冲突版本') }} {{ learning.statistics.events.conflict ?? 0 }} · {{ t('Capacity blocks', '容量阻塞') }} {{ learning.statistics.events.capacity_blocked ?? 0 }}</p>
        <p v-if="learning.mode === 'off' && learning.enabled" class="muted">{{ t('Background learning is paused in settings.', '已在设置中暂停后台学习。') }}</p>
        <p v-if="learning.failed" role="alert">{{ learning.dead ? t(`${learning.dead} turns reached the retry limit. Reanalyze to try again.`, `${learning.dead} 轮已达到重试上限，可重新分析。`) : t('Some turns could not be analyzed and will retry.', '部分轮次分析失败，系统会重试。') }} <code v-if="learning.last_error">{{ learning.last_error }}</code></p>
        <p v-else-if="learning.enabled && !learning.pending && !learning.suggestions && !profile?.version" class="muted">{{ emptyLearningDescription }}</p>
      </div>
      <button class="quiet-button" :disabled="busy || loading || !learning.enabled || learning.pending > 0" @click="reprocess">{{ t('Reanalyze conversations', '重新分析已有对话') }}</button>
    </section>

    <section v-if="suggestions.length || learning?.mode === 'review'">
      <h2>{{ t('Suggestions to review', '待审核建议') }}</h2>
      <p v-if="!suggestions.length && !loading" class="muted">{{ t('No suggestions waiting for review.', '暂无待审核建议。') }}</p>
      <article v-for="item in suggestions" :key="item.id" class="memory-review-item">
        <h3>{{ item.title }}</h3>
        <p class="muted">{{ dimensionName(item.dimension) }} · {{ memoryScope(item.scope, t) }}</p><p v-if="item.pending_reason" class="muted">{{ item.pending_reason === 'ambiguous_target' ? t('The skill target is unclear. Add a target-specific fact before confirming.', '技能对象不明确，请先补充带对象的事实。') : t('Capacity is full; review protected facts first.', '容量已满，请先检查受保护的事实。') }}</p>
        <label v-if="editing === item.id">
          {{ t('Proposed memory', '建议记忆') }}
          <textarea v-model="draft" maxlength="800" rows="2"></textarea>
        </label>
        <label v-if="editing === item.id && ['preference','habit','goal'].includes(item.dimension)">
          {{ t('Profile attribute to update', '要更新的画像属性') }}
          <select v-model="draftKey"><option :value="item.profile_key">{{ t('Detected attribute', '系统识别的属性') }} · {{ item.title }}</option>
            <option v-for="attribute in (profile?.attributes ?? []).filter(attribute => attribute.dimension === item.dimension && attribute.scope === item.scope && attribute.scope_ref === item.scope_ref && attribute.key !== item.profile_key)" :key="attribute.memory_id" :value="attribute.key">{{ t('Replace', '替换') }} · {{ attribute.value }}</option>
          </select>
          <small class="muted">{{ t('Choose an existing attribute when this statement changes an earlier preference.', '如果这条陈述改变了先前的偏好，请选择对应的已有属性。') }}</small>
        </label>
        <p v-else>{{ item.value }}</p>
        <details>
          <summary>{{ t('Evidence', '原始证据') }} ({{ item.evidence.length }})</summary>
          <blockquote v-for="entry in item.evidence" :key="entry.thread_id + entry.run_id">
            {{ entry.quote }}
            <small v-if="entry.recorded_at">{{ t('Reported', '报告于') }} <time :datetime="entry.recorded_at">{{ new Date(entry.recorded_at).toLocaleString() }}</time></small>
            <button class="text-link" @click="emit('evidence', entry.thread_id, entry.run_id)">{{ t('Find source conversation', '查看来源会话') }}</button>
          </blockquote>
        </details>
        <footer>
          <button class="quiet-button" :disabled="busy" @click="review(item, 'ignore')">{{ t('Ignore', '忽略') }}</button>
          <button v-if="editing !== item.id" class="outline-button" :disabled="busy" @click="editing = item.id; draft = item.value; draftKey = item.profile_key">{{ t('Edit', '修改') }}</button>
          <button class="accent-button" :disabled="busy || item.pending_reason === 'ambiguous_target' || (editing === item.id && draft.trim().length < 2)" @click="review(item, 'confirm')">{{ t('Confirm memory', '确认记忆') }}</button>
        </footer>
      </article>
    </section>

    <section>
      <div class="memory-section-heading"><h2>{{ t('User profile', '用户画像') }} <small v-if="selectedProfile?.version">v{{ selectedProfile.version }}</small></h2><ProfileEditor :profile="profile" @saved="load" /></div>
      <p v-if="!selectedProfile?.attributes?.length && !loading" class="muted">{{ t('No confirmed profile attributes yet.', '暂无已确认的画像属性。') }}</p>
      <button v-for="attribute in selectedProfile?.attributes" :key="attribute.memory_id" class="simple-row" @click="emit('memory', attribute.memory_id)">
        {{ attributeText(attribute) }} <small>{{ memoryScope(attribute.scope, t) }}</small>
      </button>
      <details v-if="versions.length">
        <summary>{{ t('Version history', '版本历史') }}</summary>
        <button v-for="version in versions" :key="version.version" class="simple-row" @click="showVersion(version.version)">
          v{{ version.version }} · {{ version.created_at }}<small v-if="version.removed_attributes">{{ t(`Removed ${version.removed_attributes} attributes`, `移除 ${version.removed_attributes} 项属性`) }}</small>
        </button>
        <button v-if="nextVersion !== null" class="quiet-button" :disabled="loadingVersions" @click="moreVersions">{{ t('Load earlier versions', '加载更早版本') }}</button>
        <p class="muted">{{ t(`Up to ${historyLimit} recent versions are retained.`, `保留最近 ${historyLimit} 个版本。`) }}</p>
        <button v-if="historical" class="text-link" @click="historical = undefined">{{ t('Return to current version', '返回当前版本') }}</button>
        <p v-for="change in differences" :key="change.id">{{ change.before ?? '—' }} → {{ change.after ?? '—' }}</p>
        <p v-if="erasedCount" class="muted">{{ t(`${erasedCount} attributes in this snapshot were permanently erased.`, `此历史快照中的 ${erasedCount} 项属性内容已永久清除。`) }}</p>
      </details>
    </section>

    <section>
      <div class="memory-section-heading"><h2>{{ t('Saved memories', '已保存记忆') }}</h2><select v-model="memoryStatus" :aria-label="t('Memory status', '记忆状态')" @change="load"><option value="active">{{ t('Active', '有效记忆') }}</option><option value="archived">{{ t('Archived', '已归档记忆') }}</option><option value="superseded">{{ t('Superseded', '已被更正') }}</option><option value="invalidated">{{ t('Invalidated', '已失效') }}</option></select></div>
      <p v-if="!memories.length && !loading" class="muted">{{ t('No saved memories yet.', '暂无已保存记忆。') }}</p>
      <div v-for="item in memories" :key="item.id" class="saved-memory-row">
        <button class="simple-row" @click="emit('memory', item.id)">{{ item.title }}
          <small>{{ item.type ? memoryTypeName(item.type, t) + ' · ' : '' }}{{ memoryScope(item.scope, t) }}</small><small v-if="item.effective_frequency != null">{{ t('Confirmed sessions', '有效确认会话') }} {{ item.effective_frequency }} · {{ t('Frequency strength', '频次强度') }} {{ (item.strength ?? 0).toFixed(2) }}<span v-if="item.protected"> · {{ t('Protected', '受保护') }}</span></small>
        </button>
        <button v-if="memoryStatus === 'archived'" class="quiet-button" :disabled="busy" @click="restoring = item">{{ t('Restore', '恢复') }}</button>
        <button class="quiet-button" :disabled="busy" @click="deleting = item">{{ t('Delete', '删除') }}</button>
      </div>
      <div v-if="restoring" class="memory-review-item" role="group" :aria-label="t('Restore memory', '恢复记忆')">
        <p>{{ restoring.title }} — {{ t('Restore this memory? If it is a profile attribute, its value replaces the current value in the same scope.', '恢复这条记忆？如果它属于画像属性，将替换同一范围内当前的属性值。') }}</p>
        <div><button class="quiet-button" :disabled="busy" @click="restoring = undefined">{{ t('Cancel', '取消') }}</button><button class="accent-button" :disabled="busy" @click="change(async () => { if (restoring) await workRequest('/memories/' + encodeURIComponent(restoring.id), 'PATCH', { status: 'active' }) })">{{ t('Confirm restore', '确认恢复') }}</button></div>
      </div>
      <div v-if="deleting" role="group" :aria-label="t('Confirm deletion', '确认删除')">
        <p>{{ t('Permanently delete this memory?', '永久删除这条记忆？') }} {{ deleting.title }}</p>
        <button class="quiet-button" :disabled="busy" @click="deleting = undefined">{{ t('Cancel', '取消') }}</button>
        <button class="accent-button" :disabled="busy" @click="change(async () => { if (deleting) await workRequest('/memories/' + encodeURIComponent(deleting.id), 'DELETE') })">{{ t('Delete', '删除') }}</button>
      </div>
    </section>
  </main>
</template>
