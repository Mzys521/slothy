<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { settings, t } from './preferences'
import { ApiError, workRequest, type ModelItem } from './workApi'
import { loadMemorySettings, saveMemorySettings, type MemoryConfiguration, type MemoryConfigurationPublic } from './memorySettings'

type NumberKey = Exclude<keyof MemoryConfiguration, 'mode' | 'model'>
type NumberField = {
  key: NumberKey
  en: string
  zh: string
  description: [string, string]
  unit: [string, string]
  min: number
  max: number
  step?: number
}
const groups: { id: string; title: [string, string]; fields: NumberField[] }[] = [
  { id: 'schedule', title: ['Scheduling', '调度与批量'], fields: [
    { key: 'batch_size', en: 'Batch size', zh: '单批轮数', description: ['Maximum conversation turns in one batch.', '每次规划处理的对话轮数上限。'], unit: ['turns', '轮'], min: 1, max: 64 },
    { key: 'idle_seconds', en: 'Idle time', zh: '空闲等待', description: ['Idle time before background consolidation.', '对话空闲多久后进行后台整合。'], unit: ['seconds', '秒'], min: 1, max: 86400 },
    { key: 'consolidate_every', en: 'Consolidate every', zh: '整合间隔', description: ['New turns required to trigger consolidation.', '积累指定数量的新轮次后触发整合。'], unit: ['turns', '轮'], min: 1, max: 1000 },
  ] },
  { id: 'retention', title: ['Retention & forgetting', '保留与遗忘'], fields: [
    { key: 'forget_interval_hours', en: 'Forgetting interval', zh: '遗忘检查间隔', description: ['Time between background decay checks.', '后台执行衰减检查的时间间隔。'], unit: ['hours', '小时'], min: 1, max: 720 },
    { key: 'active_limit', en: 'Active memory limit', zh: '有效记忆上限', description: ['Target maximum number of active memories.', '单个用户可保留的有效记忆数量上限。'], unit: ['memories', '条'], min: 1, max: 100000 },
    { key: 'episodic_ttl_days', en: 'Episodic memory lifetime', zh: '情景记忆保留期', description: ['Time before summarizing and archiving episodes.', '情景记忆在压缩为摘要并归档前的保留天数。'], unit: ['days', '天'], min: 1, max: 3650 },
    { key: 'fact_ttl_days', en: 'Fact memory lifetime', zh: '事实记忆保留期', description: ['Unused facts are eligible for archival after this period.', '事实记忆超过此天数未使用后可归档。'], unit: ['days', '天'], min: 1, max: 3650 },
    { key: 'retention_days', en: 'Archived memory retention', zh: '归档后保留期', description: ['Retention before a planner may permanently remove archived memories.', '归档记忆在规划器可永久清除前的保留天数。'], unit: ['days', '天'], min: 1, max: 3650 },
  ] },
  { id: 'decisions', title: ['Decisions & budget', '决策与预算'], fields: [
    { key: 'auto_confirm_score', en: 'Auto-apply confidence', zh: '自动应用置信度', description: ['Minimum confidence for automatically accepting explicit statements (0–1).', '自动接受明确个人陈述的最低置信度（0–1）。'], unit: ['', ''], min: 0, max: 1, step: 0.01 },
    { key: 'observed_min_threads', en: 'Minimum source conversations', zh: '观察记忆最少来源', description: ['Distinct conversations required for an observed preference.', '观察所得偏好至少需要多少个不同会话作为证据。'], unit: ['conversations', '个会话'], min: 1, max: 100 },
    { key: 'daily_token_budget', en: 'Daily token budget', zh: '每日 Token 预算', description: ['Daily model budget for the planner. Zero disables planner model calls.', '规划器每日模型用量上限；设为 0 表示不允许规划器调用模型。'], unit: ['tokens', 'tokens'], min: 0, max: 10000000 },
  ] },
]
const state = ref<MemoryConfigurationPublic>()
const draft = ref<MemoryConfiguration>()
const loading = ref(true)
const saving = ref(false)
const toggling = ref(false)
const error = ref('')
const toggleError = ref('')
const notice = ref('')
const conflict = ref(false)
const models = ref<ModelItem[]>([])
const modelsLoading = ref(false)
const modelsError = ref('')
const payload = computed(() => draft.value ? { ...draft.value, model: draft.value.model?.trim() || null } : undefined)
const dirty = computed(() => !!state.value && JSON.stringify(payload.value) !== JSON.stringify(state.value.configuration))
const enabled = computed(() => state.value?.runtime.personalization_enabled ?? false)
const llmLearning = computed(() => state.value?.runtime.engine === 'llm_learning')
const appliedFields = computed(() => new Set(state.value?.runtime.applied_fields ?? []))
const engineDescription = computed(() => {
  if (!enabled.value) return t('Personalized memory is off', '定制记忆已关闭')
  if (!llmLearning.value) return t('Rule extraction · user review', '规则提取 · 人工审核')
  const mode = state.value?.runtime.mode
  return mode === 'off' ? t('LLM learning · paused', 'LLM 记忆学习 · 已暂停')
    : mode === 'shadow' ? t('LLM judgment · shadow mode', 'LLM 自主判断 · 影子运行')
    : mode === 'review' ? t('LLM judgment · user review', 'LLM 自主判断 · 人工审核')
    : t('LLM judgment · automatic memory', 'LLM 自主判断 · 自动沉淀')
})
const modes = computed(() => [
  { id: 'auto', label: t('Auto', '自动'), description: t('Apply eligible memories automatically.', '符合条件的记忆自动应用。') },
  { id: 'review', label: t('Review', '人工审核'), description: t('Apply memory candidates after your confirmation.', '候选经你确认后应用。') },
  { id: 'shadow', label: t('Shadow', '影子运行'), description: t('Evaluate candidates without updating memory or profiles.', '评估候选，不更新记忆或画像。') },
  { id: 'off', label: t('Off', '关闭规划器'), description: t('Pause planner processing.', '暂停规划器处理。') },
])
const selectedMode = computed(() => modes.value.find(item => item.id === draft.value?.mode))
function message(reason: unknown) { return reason instanceof Error ? reason.message : String(reason) }
function accept(value: MemoryConfigurationPublic) {
  state.value = value
  draft.value = { ...value.configuration }
  settings.personalizedMemory = value.runtime.personalization_enabled
}
async function load() {
  if (saving.value || toggling.value) return
  loading.value = true; error.value = ''; notice.value = ''
  try { accept(await loadMemorySettings()); conflict.value = false }
  catch (reason) { error.value = message(reason) }
  finally { loading.value = false }
}
async function loadModels() {
  modelsLoading.value = true; modelsError.value = ''
  try { models.value = (await workRequest<{ items: ModelItem[] }>('/models')).items }
  catch (reason) { modelsError.value = message(reason) }
  finally { modelsLoading.value = false }
}
async function toggle() {
  if (!state.value || toggling.value || saving.value) return
  toggling.value = true; toggleError.value = ''
  try {
    const result = await workRequest<{ personalization_enabled: boolean }>('/settings', 'PATCH', { personalization_enabled: !enabled.value })
    state.value.runtime.personalization_enabled = result.personalization_enabled
    settings.personalizedMemory = result.personalization_enabled
  } catch (reason) { toggleError.value = message(reason) }
  finally { toggling.value = false }
}
async function save() {
  if (!state.value || !payload.value || saving.value || !dirty.value || conflict.value) return
  saving.value = true; error.value = ''; notice.value = ''
  try {
    accept(await saveMemorySettings(payload.value, state.value.revision))
    notice.value = llmLearning.value
      ? t('Saved. Active settings apply to the next background batch.', '已保存，生效参数将用于下一批后台学习。')
      : t('Configuration saved for the memory planner.', '记忆规划器配置已保存。')
  } catch (reason) {
    conflict.value = reason instanceof ApiError && reason.status === 409
    error.value = conflict.value
      ? t('These settings were changed elsewhere. Your draft is kept; load the latest settings before editing again.', '配置已在其他窗口更新。当前草稿已保留，请加载最新配置后重新编辑。')
      : message(reason)
  } finally { saving.value = false }
}
function reset(toDefaults = false) {
  if (!state.value || saving.value) return
  draft.value = { ...(toDefaults ? state.value.defaults : state.value.configuration) }
  notice.value = toDefaults ? t('Defaults restored in this draft. Save to keep them.', '已在草稿中恢复默认值，保存后才会写入。') : ''
  if (!conflict.value) error.value = ''
}
watch(draft, () => { notice.value = '' }, { deep: true, flush: 'sync' })
onMounted(() => { void load(); void loadModels() })
</script>

<template>
  <section class="memory-settings" :aria-busy="loading">
    <p v-if="loading" class="memory-config-status" role="status">{{ t('Reading memory settings…', '正在读取记忆配置…') }}</p>
    <div v-if="error" class="memory-config-error" role="alert">
      <p>{{ error }}</p>
      <button class="quiet-button" type="button" :disabled="loading || saving || toggling" @click="load">{{ conflict ? t('Load latest configuration', '加载最新配置') : t('Retry', '重试') }}</button>
    </div>
    <template v-if="state && draft && !loading">
      <section class="setting memory-live-setting">
        <div><h2 id="memory-live-label">{{ t('Enable personalized memory', '启用定制记忆') }}</h2><p>{{ t('Use saved memories and learn from new conversations. Changes apply immediately.', '使用已保存的记忆，并从新对话中学习。此开关立即生效。') }}</p><p v-if="toggleError" class="memory-config-error" role="alert">{{ toggleError }}</p></div>
        <button class="settings-switch" type="button" role="switch" aria-labelledby="memory-live-label" :aria-checked="enabled" :disabled="toggling || saving" @click="toggle"><span></span></button>
      </section>
      <div class="memory-planner-status">
        <span class="memory-config-label">{{ t('Current engine', '当前引擎') }}</span>
        <strong>{{ engineDescription }}</strong>
        <template v-if="llmLearning">
          <p>{{ t('Completed conversations are evaluated in the background. Reliable, evidence-backed memories are saved and projected into your profile. Uncertain or sensitive candidates remain reviewable.', '已完成的对话由 LLM 在后台判断；可靠且有原文证据的记忆自动保存并形成画像，证据不足或敏感候选保留待审。') }}</p>
          <p v-if="!state.runtime.model_available && enabled" role="alert">{{ t('No memory model is available. Configure an API provider or choose an available model below. Conversations remain queued.', '记忆模型不可用，请配置 API 或在下方选择可用模型；待处理对话会保留。') }}</p>
          <p v-else class="muted">{{ t('Model', '模型') }} · {{ state.runtime.effective_model || t('Follow conversation model', '跟随会话模型') }} · {{ t('Reported today', '今日已上报用量') }} {{ state.runtime.reported_tokens.toLocaleString() }} tokens<span v-if="state.runtime.estimated_tokens"> · {{ t('Unreported budget reservation', '未上报用量的预算预留') }} {{ state.runtime.estimated_tokens.toLocaleString() }}</span></p>
          <p class="muted">{{ t('Fields marked “Reserved” are saved for future consolidation and forgetting. They do not trigger automatic deletion.', '标记“预留”的字段供后续整合与遗忘使用，当前不触发自动删除。') }}</p>
        </template>
        <p v-else>{{ t('The legacy rule-and-review engine is active.', '当前使用兼容的规则提取与审核引擎。') }}</p>
      </div>
      <form class="memory-config-form" @submit.prevent="save">
        <fieldset :disabled="saving || toggling || conflict">
          <section class="memory-config-section" aria-labelledby="memory-planner-title">
            <header><h2 id="memory-planner-title">{{ t('Memory planner configuration', '记忆规划器配置') }}</h2><span>{{ llmLearning ? t('Live configuration', '运行配置') : t('Preconfiguration', '预配置') }} · v{{ state.revision }}</span></header>
            <div class="setting">
              <div><h3 id="memory-mode-label">{{ t('Processing mode', '处理模式') }}</h3><p>{{ selectedMode?.description }}</p></div>
              <select v-model="draft.mode" aria-labelledby="memory-mode-label"><option v-for="mode in modes" :key="mode.id" :value="mode.id">{{ mode.label }}</option></select>
            </div>
            <div class="setting">
              <div><h3 id="memory-model-label">{{ t('Planner model', '规划模型') }}</h3><p>{{ t('Leave empty to follow the main model. Use a configured provider/model ID.', '留空跟随主模型，也可填写已配置的提供商/模型 ID。') }}</p></div>
              <div class="memory-model-control">
                <input v-model="draft.model" list="memory-model-options" maxlength="256" :placeholder="t('Follow main model', '跟随主模型')" aria-labelledby="memory-model-label" autocomplete="off">
                <datalist id="memory-model-options"><option v-for="model in models" :key="model.id" :value="model.id">{{ model.display_name || model.id }}</option></datalist>
                <small v-if="modelsLoading">{{ t('Reading configured models…', '正在读取已配置模型…') }}</small>
                <div v-if="modelsError" class="memory-config-error" role="alert"><p>{{ modelsError }}</p><button type="button" class="quiet-button" @click="loadModels">{{ t('Retry model list', '重试模型列表') }}</button></div>
              </div>
            </div>
          </section>
          <details v-for="group in groups" :key="group.id" class="memory-config-group" open>
            <summary>{{ t(...group.title) }}</summary>
            <div v-for="field in group.fields" :key="field.key" class="setting">
              <div><h3 :id="'memory-' + field.key">{{ t(field.en, field.zh) }}<small v-if="llmLearning && !appliedFields.has(field.key)"> · {{ t('Reserved', '预留') }}</small></h3><p :id="'memory-help-' + field.key">{{ t(...field.description) }}</p></div>
              <div class="memory-number-control">
                <input v-model.number="draft[field.key]" type="number" required :min="field.min" :max="field.max" :step="field.step ?? 1" :aria-labelledby="'memory-' + field.key" :aria-describedby="'memory-help-' + field.key">
                <span>{{ t(...field.unit) }}</span>
              </div>
            </div>
          </details>
        </fieldset>
        <footer class="memory-config-actions">
          <p role="status" aria-live="polite">{{ notice || (dirty ? t('Unsaved changes', '有未保存的修改') : state.revision ? t('Saved on this device’s server', '配置保存在本机服务端') : t('Deployment defaults · not saved yet', '部署默认值 · 尚未保存')) }}</p>
          <div><button type="button" class="quiet-button" :disabled="saving || conflict" @click="reset(true)">{{ t('Restore defaults', '恢复默认值') }}</button><button type="button" class="quiet-button" :disabled="saving || !dirty || conflict" @click="reset()">{{ t('Discard', '撤销修改') }}</button><button type="submit" class="accent-button" :disabled="saving || toggling || !dirty || conflict">{{ saving ? t('Saving…', '正在保存…') : t('Save configuration', '保存配置') }}</button></div>
        </footer>
      </form>
    </template>
  </section>
</template>
