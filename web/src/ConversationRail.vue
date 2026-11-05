<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import type { Turn } from './chat'
import { t } from './preferences'

const props = defineProps<{ turns: Turn[]; scroller?: HTMLElement }>()
const rail = ref<HTMLElement>()
const marks = ref<HTMLElement>()
const active = ref(0)
const hovered = ref<number>()
const previewY = ref(0)
const preview = computed(() => hovered.value === undefined ? undefined : props.turns[hovered.value])
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)')

function sync() {
  const area = props.scroller
  if (!area) return
  const items = area.querySelectorAll<HTMLElement>('.chat-turn')
  if (area.scrollHeight - area.scrollTop - area.clientHeight < 12 && area.scrollTop > 0) {
    active.value = Math.max(0, items.length - 1)
    return
  }
  const anchor = area.getBoundingClientRect().top + area.clientHeight * .35
  let index = 0
  for (const [position, item] of Array.from(items).entries()) {
    if (item.getBoundingClientRect().top > anchor) break
    index = position
  }
  active.value = index
}

watch(() => props.scroller, (area, _, cleanup) => {
  if (!area) return
  area.addEventListener('scroll', sync, { passive: true })
  cleanup(() => area.removeEventListener('scroll', sync))
  void nextTick(sync)
}, { immediate: true })
watch(() => props.turns.map(turn => turn.id), () => void nextTick(sync))
watch(active, index => {
  const list = marks.value
  const mark = list?.children[index]
  if (!list || !mark) return
  const viewport = list.getBoundingClientRect()
  const bounds = mark.getBoundingClientRect()
  if (bounds.top < viewport.top) list.scrollTop += bounds.top - viewport.top
  if (bounds.bottom > viewport.bottom) list.scrollTop += bounds.bottom - viewport.bottom
})

function reveal(index: number, event: Event) {
  const button = event.currentTarget as HTMLElement
  const bounds = rail.value?.getBoundingClientRect()
  if (!bounds) return
  hovered.value = index
  previewY.value = Math.min(bounds.height, Math.max(0, button.getBoundingClientRect().top - bounds.top + 8))
}

function jump(index: number) {
  const area = props.scroller
  const item = area?.querySelectorAll<HTMLElement>('.chat-turn')[index]
  if (!area || !item) return
  const top = item.getBoundingClientRect().top - area.getBoundingClientRect().top + area.scrollTop - 12
  area.scrollTo({ top, behavior: reducedMotion.matches ? 'instant' : 'smooth' })
}

function markWidth(index: number) {
  const distance = Math.abs(index - (hovered.value ?? active.value))
  return (distance === 0 ? 39 : distance === 1 ? 29 : distance === 2 ? 20 : 11) + 'px'
}
</script>

<template>
  <nav ref="rail" class="conversation-rail" :aria-label="t('Jump to a turn', '快速定位对话')" @pointerleave="hovered = undefined">
    <div ref="marks" class="rail-marks" @scroll="hovered = undefined">
      <button v-for="(turn, index) in turns" :key="turn.id" type="button" class="rail-mark"
        :aria-current="index === active ? 'location' : undefined"
        :aria-label="t('Turn ' + (index + 1), '第 ' + (index + 1) + ' 轮') + ' · ' + (turn.prompt || turn.files.join(', ')).slice(0, 100)"
        @pointerenter="reveal(index, $event)" @focus="reveal(index, $event)" @blur="hovered = undefined" @click="jump(index)">
        <span :style="{ width: markWidth(index) }"></span>
      </button>
    </div>
    <div v-if="preview" class="rail-preview" :style="{ top: previewY + 'px' }" aria-hidden="true">
      <strong>{{ preview.prompt || preview.files.join(', ') }}</strong>
      <p v-if="preview.serverReply">{{ preview.serverReply.slice(0, 240) }}</p>
    </div>
  </nav>
</template>
