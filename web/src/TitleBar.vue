<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { Minus, Square, Copy, X } from 'lucide-vue-next'
import { t } from './preferences'
import { brandAsset } from './brand'
const maximized = ref(false)
const desktop = window.desktop
let unsubscribe: (() => void) | undefined
onMounted(async () => {
  if (!desktop) return
  maximized.value = await desktop.isMaximized()
  unsubscribe = desktop.onMaximized(value => { maximized.value = value })
})
onUnmounted(() => unsubscribe?.())
</script>
<template>
  <header v-if="desktop" class="desktop-titlebar">
    <div class="desktop-titlebar-brand"><img :src="brandAsset('mark.png')" alt=""><span>slothy</span></div>
    <div class="desktop-window-controls">
      <button type="button" :aria-label="t('Minimize', '最小化')" @click="desktop.minimize()"><Minus /></button>
      <button type="button" :aria-label="maximized ? t('Restore window', '还原窗口') : t('Maximize', '最大化')" @click="desktop.toggleMaximize()"><component :is="maximized ? Copy : Square" /></button>
      <button class="desktop-window-close" type="button" :aria-label="t('Close', '关闭')" @click="desktop.close()"><X /></button>
    </div>
  </header>
</template>
