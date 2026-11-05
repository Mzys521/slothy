<script setup lang="ts">
import { computed } from 'vue'
import { settings, t } from './preferences'
import MemorySettings from './MemorySettings.vue'

const props = defineProps<{ section: 'appearance' | 'language' | 'memory' }>()
const pageTitle = computed(() => props.section === 'appearance' ? t('Appearance', '外观')
  : props.section === 'language' ? t('Language', '语言') : t('Personalized memory', '定制记忆'))
const pageDescription = computed(() => props.section === 'appearance'
  ? t('A consistent look, wherever you work.', '统一应用的颜色、字体和显示方式。')
  : props.section === 'language' ? t('Choose the language used throughout slothy.', '选择 slothy 所有界面的显示语言。')
  : t('Control how slothy uses your long-term memories.', '管理 slothy 如何使用和积累你的长期记忆。'))

const palettes = [
  { id: 'copper', label: 'Copper', zh: '铜色' },
  { id: 'neutral', label: 'Neutral', zh: '中性灰' },
  { id: 'slate', label: 'Slate blue', zh: '石板蓝' },
]
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
        <div class="palette-options" role="radiogroup" aria-labelledby="palette-label">
          <label v-for="palette in palettes" :key="palette.id">
            <input v-model="settings.palette" type="radio" name="palette" :value="palette.id">
            {{ t(palette.label, palette.zh) }}
            <span class="swatch" :class="'swatch-' + palette.id" aria-hidden="true"></span>
          </label>
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
