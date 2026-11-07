<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { Check, ChevronDown, ChevronRight, RotateCcw } from 'lucide-vue-next'
import { t } from './preferences'
import type { ModelItem } from './workApi'

const props = defineProps<{ items: ModelItem[]; selected?: ModelItem; effort?: string }>()
const model = defineModel<string>({ required: true })
const emit = defineEmits<{ effort: [value: string] }>()
const root = ref<HTMLElement>()
const trigger = ref<HTMLButtonElement>()
const popup = ref<HTMLElement>()
const open = ref(false)
const showModels = ref(false)
const levels = computed(() => props.selected?.reasoning_levels ?? [])
/** Index of the active effort level, clamped to a valid position. / 当前推理强度档位的索引，保证落在有效范围内。 */
const levelIndex = computed(() => Math.max(0, levels.value.indexOf(props.effort ?? '')))
/** Slider position as a percentage of the available effort range. / 以可用强度区间百分比表示的滑块位置。 */
const levelPosition = computed(() => (100 * levelIndex.value / Math.max(1, levels.value.length - 1)) + '%')
const modelLabel = computed(() => model.value === 'auto' ? t('Auto', '自动') : props.selected?.display_name || model.value)
const currentEffort = computed(() => effortLabel(levels.value[levelIndex.value]))
/** Models grouped by provider for the selection list. / 按提供商分组后的模型，用于选择列表。 */
const groups = computed(() => {
  const providers = new Map<string, ModelItem[]>()
  for (const item of props.items) {
    const group = providers.get(item.provider) ?? []
    group.push(item)
    providers.set(item.provider, group)
  }
  return providers
})
const providerNames: Record<string, string> = { deepseek: 'DeepSeek', mimo: 'MiMo', openai_compatible: 'OpenAI-compatible' }

/**
 * Maps a raw reasoning level to its display label.
 *
 * 将原始推理强度档位映射为显示名称。
 *
 * @param value - Raw effort level such as none, low, high or max. / 原始强度档位，例如 none、low、high 或 max。
 * @returns Short human-readable effort label, or an empty string. / 简短的可读强度名称，或空字符串。
 */
function effortLabel(value: string | undefined) {
  if (!value) return ''
  if (value === 'none') return 'Off'
  if (props.selected?.provider === 'mimo') return 'Thinking'
  if (value === 'low') return 'Low'
  if (value === 'max') return 'Max'
  return 'High'
}

/**
 * Switches between the model list and the effort slider, moving focus into the popup.
 *
 * 在模型列表与推理强度滑块之间切换，并将焦点移入弹层。
 *
 * @param list - True to show the model list, false to show the effort slider. / 为 true 显示模型列表，为 false 显示强度滑块。
 * @returns Nothing. / 无返回值。
 */
function show(list: boolean) {
  showModels.value = list
  void nextTick(() => popup.value?.querySelector<HTMLElement>('input, button')?.focus())
}

/** Opens or closes the popup, showing the right pane for the current state. / 打开或关闭弹层，并按当前状态显示对应面板。 */
function toggle() {
  open.value = !open.value
  if (open.value) show(!levels.value.length)
}

/** Open the existing model list from a composer command. / 命令复用模型选择列表。 */
defineExpose({ openModels: () => { open.value = true; show(true) } })

/**
 * Closes the popup, optionally returning focus to the trigger.
 *
 * 关闭弹层，可选择将焦点移回触发按钮。
 *
 * @param restoreFocus - True to focus the trigger button after closing. / 为 true 时关闭后将焦点移回触发按钮。
 * @returns Nothing. / 无返回值。
 */
function close(restoreFocus = false) {
  open.value = false
  if (restoreFocus) trigger.value?.focus()
}

/**
 * Selects a model id and opens the next pane or closes the popup.
 *
 * 选择模型 ID，并进入下一步面板或关闭弹层。
 *
 * @param id - Model id or auto. / 模型 ID 或 auto。
 * @returns Nothing. / 无返回值。
 */
function choose(id: string) {
  model.value = id
  void nextTick(() => levels.value.length ? show(false) : close(true))
}

/**
 * Emits the effort level picked on the range input.
 *
 * 触发范围输入框所选的推理强度档位。
 *
 * @param event - Input event from the effort range control. / 强度范围控件触发的输入事件。
 * @returns Nothing. / 无返回值。
 */
function setLevel(event: Event) {
  const level = levels.value[Number((event.target as HTMLInputElement).value)]
  if (level) emit('effort', level)
}

/**
 * Closes the popup when a pointer press happens outside the picker.
 *
 * 当指针在选择器外部按下时关闭弹层。
 *
 * @param event - Pointer-down event from the document. / 来自文档的指针按下事件。
 * @returns Nothing. / 无返回值。
 */
function onOutside(event: PointerEvent) {
  if (event.target instanceof Node && !root.value?.contains(event.target)) close()
}

// Close on outside presses; unregister the document listener on unmount. / 在外部按下时关闭；卸载时移除文档监听器。
onMounted(() => document.addEventListener('pointerdown', onOutside))
onUnmounted(() => document.removeEventListener('pointerdown', onOutside))
</script>

<template>
  <div ref="root" class="composer-option model-picker" @keydown.esc.stop="close(true)">
    <button ref="trigger" class="model-trigger" type="button" :aria-expanded="open"
      :aria-label="[t('LLM model', 'LLM 模型'), modelLabel, currentEffort].filter(Boolean).join(' · ')"
      :title="[selected?.id, currentEffort].filter(Boolean).join(' ')" @click="toggle">
      <span class="model-trigger-name">{{ modelLabel }}</span>
      <span v-if="currentEffort" class="model-trigger-effort" lang="en">{{ currentEffort }}</span>
      <ChevronDown aria-hidden="true" />
    </button>
    <div v-if="open" ref="popup" class="model-popup" role="group" :aria-label="t('Model settings', '模型设置')">
      <template v-if="showModels">
        <p>{{ t('Select a model', '选择模型') }}</p>
        <button type="button" class="model-row" :aria-pressed="model === 'auto'" @click="choose('auto')">
          <span>{{ t('Default', '默认') }}</span><Check v-if="model === 'auto'" aria-hidden="true" />
        </button>
        <template v-for="[provider, models] in groups" :key="provider">
          <p class="model-provider">{{ providerNames[provider] ?? provider }}</p>
          <button v-for="item in models" :key="item.id" type="button" class="model-row"
            :aria-pressed="model === item.id" @click="choose(item.id)">
            <span>{{ item.display_name || item.id }}</span><Check v-if="model === item.id" aria-hidden="true" />
          </button>
        </template>
      </template>
      <template v-else>
        <div class="effort-header">
          <button type="button" class="effort-model" @click="show(true)">
            <strong lang="en">{{ currentEffort }}</strong><span>{{ selected?.display_name || selected?.id }} <ChevronRight aria-hidden="true" /></span>
          </button>
          <button type="button" class="effort-reset" :title="t('Restore default', '恢复默认档位')"
            :aria-label="t('Restore default', '恢复默认档位')" @click="selected?.default_effort && emit('effort', selected.default_effort)">
            <RotateCcw aria-hidden="true" />
          </button>
        </div>
        <div class="effort-slider" :class="{ 'effort-max': levels.length > 1 && levelIndex === levels.length - 1 }"
          :style="{ '--level': levelPosition }">
          <span class="effort-visual" aria-hidden="true">
            <span class="effort-fill"></span>
            <span class="effort-marks">
              <span v-for="level in levels" :key="level"></span>
            </span>
            <span class="effort-thumb"></span>
          </span>
          <input class="effort-range" type="range" min="0" :max="levels.length - 1" step="1" :value="levelIndex"
            :aria-label="t('Thinking effort', '思考强度')" :aria-valuetext="currentEffort" @input="setLevel">
        </div>
      </template>
    </div>
  </div>
</template>
