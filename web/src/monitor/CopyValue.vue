<script setup lang="ts">
import { onUnmounted, ref } from 'vue'
import { Check, Copy } from 'lucide-vue-next'
import { copyText } from '../clipboard'
import { t } from '../preferences'

const props = defineProps<{ value: string; label: string }>()
const feedback = ref('')
let timer: ReturnType<typeof setTimeout>
onUnmounted(() => clearTimeout(timer))
async function copy() {
  clearTimeout(timer)
  try {
    await copyText(props.value)
    feedback.value = t('Copied', '已复制')
  } catch {
    feedback.value = t('Copy failed', '复制失败')
  }
  timer = setTimeout(() => { feedback.value = '' }, 1800)
}
</script>
<template>
  <button type="button" class="copy-value" :aria-label="label" :title="label" @click="copy">
    <slot /><Check v-if="feedback === t('Copied', '已复制')" aria-hidden="true" /><Copy v-else aria-hidden="true" />
    <span role="status">{{ feedback }}</span>
  </button>
</template>
<style scoped>
.copy-value { display: inline-flex; align-items: center; gap: 5px; min-width: 0; min-height: 28px; max-width: 100%; flex-wrap: wrap; text-align: start; color: var(--muted); font: var(--type-sm)/1.45 var(--mono); overflow-wrap: anywhere; }
.copy-value:hover { color: var(--accent-ink); }
.copy-value svg { width: 12px; height: 12px; }
.copy-value span { font-family: var(--font); color: var(--ink); }
.copy-value span:empty { display: none; }
</style>
