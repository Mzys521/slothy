<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { settings, t } from './preferences'
import { normalizeAccent } from './appearance'
import MemorySettings from './MemorySettings.vue'

const props = defineProps<{ section: 'appearance' | 'language' | 'memory' }>()
const pageTitle = computed(() => props.section === 'appearance' ? t('Appearance', '外观')
  : props.section === 'language' ? t('Language', '语言') : t('Personalized memory', '定制记忆'))
const pageDescription = computed(() => props.section === 'appearance'
  ? t('A consistent look, wherever you work.', '统一应用的颜色、字体和显示方式。')
  : props.section === 'language' ? t('Choose the language used throughout slothy.', '选择 slothy 所有界面的显示语言。')
  : t('Control how slothy uses your long-term memories.', '管理 slothy 如何使用和积累你的长期记忆。'))

const palettes = [
  { id: 'green', label: 'Slothy green · Default', zh: '思洛绿 · 默认' },
  { id: 'copper', label: 'Copper', zh: '铜色' },
  { id: 'neutral', label: 'Neutral', zh: '中性灰' },
  { id: 'slate', label: 'Slate blue', zh: '石板蓝' },
  { id: 'custom', label: 'Custom', zh: '自定义' },
]
const customDraft = ref(settings.customAccent)
const invalidAccent = ref(false)
// Keep the HEX draft in sync when the stored custom accent changes elsewhere. / 存储的自定义强调色在其他位置变化时同步 HEX 草稿。
watch(() => settings.customAccent, value => { customDraft.value = value; invalidAccent.value = false })
/** Normalizes the draft HEX color and stores it when valid. / 规整草稿中的 HEX 颜色，有效时写入设置。 */
function applyCustomAccent() {
  const color = normalizeAccent(customDraft.value)
  invalidAccent.value = !color
  if (color) { settings.customAccent = color; customDraft.value = color }
}
/**
 * Applies the color chosen in the native color picker.
 *
 * 应用原生取色器选择的颜色。
 *
 * @param event - Input event from the color picker. / 取色器触发的输入事件。
 * @returns Nothing. / 无返回值。
 */
function pickCustomAccent(event: Event) {
  customDraft.value = (event.target as HTMLInputElement).value
  applyCustomAccent()
}
</script>

<template>
  <main class="settings-page">
    <h1>{{ pageTitle }}</h1>
    <p class="settings-subtitle">{{ pageDescription }}</p>
    <div class="settings-form">
      <section v-if="section === 'appearance'" class="setting">
        <div><h2>{{ t('Color mode', '颜色模式') }}</h2><p>{{ t('Choose how the app looks.', '选择应用的明暗外观。') }}</p></div>
        <div class="segmented" role="group" :aria-label="t('Color mode', '颜色模式')">
          <button v-for="mode in [{ id: 'system', label: t('System', '跟随系统') }, { id: 'light', label: t('Light', '浅色') }, { id: 'dark', label: t('Dark', '深色') }]" :key="mode.id" :aria-pressed="settings.mode === mode.id" @click="settings.mode = mode.id">{{ mode.label }}</button>
        </div>
      </section>
      <section v-if="section === 'appearance'" class="setting">
        <div><h2 id="font-label">{{ t('Font', '字体') }}</h2><p>{{ t('Set the UI font for text and interface.', '选择界面的显示字体。') }}</p></div>
        <div class="setting-control">
          <select v-model="settings.font" aria-labelledby="font-label">
            <option value="system">{{ t('System Sans', '系统无衬线') }}</option>
            <option value="geist">Geist</option>
            <option value="serif">{{ t('Serif', '衬线字体') }}</option>
          </select>
        </div>
      </section>
      <section v-if="section === 'appearance'" class="setting">
        <div><h2 id="palette-label">{{ t('Color palette', '配色') }}</h2><p>{{ t('Choose the accent for selected items and controls.', '选择选中项和控件的高亮色。') }}</p></div>
        <div class="setting-control">
        <div class="palette-options" role="radiogroup" aria-labelledby="palette-label">
          <label v-for="palette in palettes" :key="palette.id" :class="{ 'palette-custom-option': palette.id === 'custom' }">
            <input v-model="settings.palette" type="radio" name="palette" :value="palette.id">
            {{ t(palette.label, palette.zh) }}
            <span class="swatch" :class="'swatch-' + palette.id" aria-hidden="true"></span>
          </label>
        </div>
        <div v-if="settings.palette === 'custom'" class="custom-palette-fields">
          <input type="color" :value="settings.customAccent" :aria-label="t('Choose custom accent color', '选择自定义强调色')" @input="pickCustomAccent">
          <label for="custom-accent">{{ t('HEX color', 'HEX 色值') }}<input id="custom-accent" v-model="customDraft" type="text" maxlength="7" spellcheck="false" autocomplete="off" placeholder="#60DD07" :aria-invalid="invalidAccent" :aria-describedby="invalidAccent ? 'custom-accent-error' : undefined" @change="applyCustomAccent" @keydown.enter.prevent="applyCustomAccent"></label>
          <p v-if="invalidAccent" id="custom-accent-error" role="alert">{{ t('Enter a HEX color, such as #60DD07.', '请输入 HEX 色值，例如 #60DD07。') }}</p>
        </div>
        </div>
      </section>
      <section v-if="section === 'language'" class="setting">
        <div><h2 id="language-label">{{ t('Language', '语言') }}</h2><p>{{ t('Choose the display language for the app.', '选择应用的显示语言。') }}</p></div>
        <div class="setting-control">
          <select v-model="settings.language" aria-labelledby="language-label">
            <option value="en" lang="en">English</option>
            <option value="zh-CN" lang="zh-CN">简体中文</option>
          </select>
        </div>
      </section>
      <MemorySettings v-if="section === 'memory'" />
    </div>
  </main>
</template>
