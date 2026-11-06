<script setup lang="ts">
import { ref, onUnmounted } from 'vue'
import { Check, Copy } from 'lucide-vue-next'
import { copyText } from './clipboard'
import { t } from './preferences'
const props = defineProps<{ text: string }>()
const status = ref<'idle' | 'done' | 'error'>('idle')
let timer: ReturnType<typeof setTimeout>
onUnmounted(() => clearTimeout(timer))
async function copy() {
  clearTimeout(timer)
  try { await copyText(props.text); status.value = 'done' }
  catch { status.value = 'error' }
  timer = setTimeout(() => { status.value = 'idle' }, 2500)
}
</script>
<template>
  <span class="message-copy-action">
    <button type="button" class="message-copy" :disabled="!text" :title="status === 'done' ? t('Copied', '已复制') : t('Copy message', '复制此条消息')" :aria-label="t('Copy message', '复制此条消息')" @click="copy"><Check v-if="status === 'done'" /><Copy v-else /></button>
    <span class="copy-message-status" :class="{ 'copy-message-error': status === 'error' }" role="status">{{ status === 'error' ? t('Copy failed. Please try again.', '复制失败，请重试。') : status === 'done' ? t('Copied', '已复制') : '' }}</span>
  </span>
</template>
