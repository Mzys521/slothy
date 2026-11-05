<script setup lang="ts">
import { Archive, Brain, Info, KeyRound, Languages, Palette } from 'lucide-vue-next'
import { t } from './preferences'
import { brandAsset } from './brand'

defineProps<{ page: string; setupRequired: boolean }>()
const version = __APP_VERSION__
const sections = [
  { route: 'settings', en: 'Appearance', zh: '外观', icon: Palette },
  { route: 'settings/language', en: 'Language', zh: '语言', icon: Languages },
  { route: 'settings/api', en: 'API configuration', zh: 'API 配置', icon: KeyRound },
  { route: 'settings/memory', en: 'Personalized memory', zh: '定制记忆', icon: Brain },
  { route: 'settings/archives', en: 'Archived sessions', zh: '已归档会话', icon: Archive },
  { route: 'settings/about', en: 'About slothy', zh: '关于 slothy', icon: Info },
]
function locked(route: string, required: boolean) {
  return required && route !== 'settings/api' && route !== 'settings/about'
}
</script>

<template>
  <section class="settings-layout" :aria-label="t('Settings', '设置')">
    <aside class="settings-navigation">
      <h1>{{ t('Settings', '设置') }}</h1>
      <nav :aria-label="t('Settings sections', '设置分类')">
        <a v-for="section in sections" :key="section.route"
          :href="locked(section.route, setupRequired) ? undefined : '#' + section.route"
          :aria-current="page === section.route ? 'page' : undefined"
          :aria-disabled="locked(section.route, setupRequired) || undefined"
          :tabindex="locked(section.route, setupRequired) ? -1 : undefined"
          :title="locked(section.route, setupRequired) ? t('Configure an API key first.', '请先完成 API 配置。') : undefined">
          <component :is="section.icon" aria-hidden="true" /><span>{{ t(section.en, section.zh) }}</span>
        </a>
      </nav>
      <a class="settings-brand-footer" href="#settings/about" :aria-label="t('About slothy', '关于 slothy')">
        <img :src="brandAsset('mark.png')" alt="">
        <span><strong>slothy</strong><small>v{{ version }}</small></span>
      </a>
    </aside>
    <div class="settings-panel"><slot /></div>
  </section>
</template>
