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
const levelIndex = computed(() => Math.max(0, levels.value.indexOf(props.effort ?? '')))
const levelPosition = computed(() => (100 * levelIndex.value / Math.max(1, levels.value.length - 1)) + '%')
const modelLabel = computed(() => model.value === 'auto' ? t('Auto', '自动') : props.selected?.display_name || model.value)
const currentEffort = computed(() => effortLabel(levels.value[levelIndex.value]))
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

function effortLabel(value: string | undefined) {
  if (!value) return ''
  if (value === 'none') return 'Off'
  if (props.selected?.provider === 'mimo') return 'Thinking'
  if (value === 'low') return 'Low'
  if (value === 'max') return 'Max'
  return 'High'
}

function show(list: boolean) {
  showModels.value = list
  void nextTick(() => popup.value?.querySelector<HTMLElement>('input, button')?.focus())
}

function toggle() {
  open.value = !open.value
  if (open.value) show(!levels.value.length)
}

function close(restoreFocus = false) {
  open.value = false
  if (restoreFocus) trigger.value?.focus()
}

function choose(id: string) {
  model.value = id
  void nextTick(() => levels.value.length ? show(false) : close(true))
}

function setLevel(event: Event) {
  const level = levels.value[Number((event.target as HTMLInputElement).value)]
  if (level) emit('effort', level)
}

function onOutside(event: PointerEvent) {
  if (event.target instanceof Node && !root.value?.contains(event.target)) close()
}

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
