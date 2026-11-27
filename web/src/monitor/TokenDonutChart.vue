<script setup lang="ts">
import { computed, ref, useId } from 'vue'
import { donutArcs, type DonutSegment } from './workMetrics'

const props = withDefaults(defineProps<{ segments: DonutSegment[]; total: number; centerLabel?: string; size?: number }>(),
  { centerLabel: '估算输入 Token', size: 160 })
const arcs = computed(() => donutArcs(props.segments, props.total))
const hover = ref('')
const focus = ref('')
const active = computed(() => arcs.value.find(arc => arc.key === (hover.value || focus.value)))
const maskId = 'donut-' + useId()
const description = computed(() => [props.centerLabel + ' ' + props.total, ...arcs.value.map(s => s.label + ' ' + s.value + ' (' + s.percent + ')')].join(' · '))
</script>
<template>
  <div class="token-donut" :style="{ '--donut-size': size + 'px' }">
    <div class="donut-plot">
      <svg viewBox="0 0 160 160" role="img" :aria-label="description">
        <defs><mask :id="maskId" maskUnits="userSpaceOnUse" x="0" y="0" width="160" height="160" style="mask-type: alpha">
          <circle class="donut-reveal" cx="80" cy="80" r="65" fill="none" stroke="var(--ink)" stroke-width="26" pathLength="360"
            stroke-dasharray="360 360" />
        </mask></defs>
        <circle v-if="!arcs.length" cx="80" cy="80" r="65" fill="none" stroke="var(--line)" stroke-width="15" />
        <g :mask="'url(#' + maskId + ')'" transform="rotate(-90 80 80)">
          <circle v-for="arc in arcs" :key="arc.key" class="donut-arc" :data-key="arc.key" :data-active="active?.key === arc.key"
            :class="{ faded: active && active.key !== arc.key }" cx="80" cy="80" r="65" fill="none" :stroke="arc.color" pathLength="360"
            :stroke-dasharray="arc.painted + ' ' + (360 - arc.painted)" :stroke-dashoffset="-arc.start"
            @pointerenter="hover = arc.key" @pointerleave="hover = ''" />
        </g>
      </svg>
      <div class="donut-center" aria-hidden="true"><strong>{{ active?.value ?? total }}</strong><span>{{ active ? active.label + ' · ' + active.percent : centerLabel }}</span></div>
    </div>
    <ul v-if="arcs.length" class="donut-legend">
      <li v-for="arc in arcs" :key="arc.key" :data-key="arc.key" :data-active="active?.key === arc.key" tabindex="0"
        @pointerenter="hover = arc.key" @pointerleave="hover = ''" @focus="focus = arc.key" @blur="focus = ''">
        <i aria-hidden="true" :style="{ background: arc.color }"></i><span>{{ arc.label }}</span><b>{{ arc.value }}</b><small>{{ arc.percent }}</small>
      </li>
    </ul>
  </div>
</template>
<style scoped>
.token-donut { display: grid; grid-template-columns: minmax(112px, min(38%, var(--donut-size))) minmax(0, 1fr); gap: 10px; align-items: center; font-variant-numeric: tabular-nums; }
.donut-plot { width: 100%; max-width: var(--donut-size); position: relative; aspect-ratio: 1; container-type: inline-size; }
.donut-plot svg { display: block; width: 100%; height: 100%; }
.donut-center { position: absolute; inset: 23% 15%; display: flex; flex-direction: column; align-items: center; justify-content: center; pointer-events: none; text-align: center; overflow-wrap: anywhere; }
.donut-center strong { font-size: clamp(20px, 18cqi, 29px); line-height: 1.12; letter-spacing: -.035em; }
.donut-center span { margin-top: 4px; color: var(--muted); font-size: var(--type-xs); line-height: 1.25; }
.donut-arc { stroke-width: 15; pointer-events: visibleStroke; transition: opacity 150ms, stroke-width 150ms; }
.donut-arc[data-active="true"] { stroke-width: 20; }
.donut-arc.faded { opacity: .36; }
.donut-reveal { animation: draw-ring 400ms ease-out both; }
@keyframes draw-ring { from { stroke-dashoffset: 360; } to { stroke-dashoffset: 0; } }
.donut-legend { display: grid; gap: 2px; list-style: none; padding: 0; margin: 0; }
.donut-legend li { display: grid; grid-template-columns: 8px minmax(0, 1fr) auto 3.2ch; align-items: center; gap: 6px; min-height: 28px; padding: 3px 5px; font-size: var(--type-sm); border-radius: 5px; transition: background 150ms; }
.donut-legend li[data-active="true"] { background: var(--neutral); }
.donut-legend i { width: 7px; height: 7px; border-radius: 2px; }
.donut-legend span { overflow-wrap: anywhere; }
.donut-legend b, .donut-legend small { text-align: right; font-size: inherit; }
.donut-legend small { color: var(--muted); }
@container monitor (width < 360px) {
  .token-donut { grid-template-columns: minmax(0, 1fr); }
  .donut-plot { justify-self: center; }
}
@media (prefers-reduced-motion: reduce) {
  .donut-reveal { animation: none; }
  .donut-arc, .donut-legend li { transition: none; }
}
</style>
