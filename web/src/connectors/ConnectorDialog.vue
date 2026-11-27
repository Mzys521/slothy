<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, useId } from 'vue'
import { X } from 'lucide-vue-next'
import { t } from '../preferences'

const props = defineProps<{ title: string; busy?: boolean; compact?: boolean }>()
const emit = defineEmits<{ close: [] }>()
const dialog = ref<HTMLDialogElement>()
const titleId = useId()
onMounted(() => dialog.value?.showModal())
onBeforeUnmount(() => { if (dialog.value?.open) dialog.value.close() })

function close() {
  if (!props.busy) dialog.value?.close()
}
</script>

<template>
  <dialog ref="dialog" class="modal connector-dialog" :class="{ 'connector-dialog-compact': compact }"
    :aria-labelledby="titleId" @close="emit('close')" @cancel="busy && $event.preventDefault()">
    <header class="connector-dialog-header">
      <h2 :id="titleId">{{ title }}</h2>
      <button type="button" class="icon-button" :aria-label="t('Close', '关闭')" :disabled="busy" @click="close"><X aria-hidden="true" /></button>
    </header>
    <slot />
  </dialog>
</template>
