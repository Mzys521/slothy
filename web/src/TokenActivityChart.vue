<script setup lang="ts">
import { computed, ref, useId, watch } from 'vue'
import { settings, t } from './preferences'
import { buildTokenActivity, formatTokenCount, heatLevel, type ActivityCell, type ActivityInput, type ActivityWeek } from './tokenActivity'

const props = defineProps<{ usage?: ActivityInput }>()
const view = ref<'daily' | 'weekly' | 'cumulative'>('daily')
const series = computed(() => props.usage ? buildTokenActivity(props.usage) : undefined)
const weeks = computed(() => series.value?.weeks ?? [])
const bars = computed(() => view.value === 'cumulative' ? series.value?.cumulative ?? [] : weeks.value)
const barPeak = computed(() => Math.max(1, ...bars.value.map(bar => bar.tokens ?? 0)))
const focusDay = ref(0)
const focusBar = ref(52)
const tooltipId = 'activity-tooltip-' + useId().replaceAll(':', '')
const tooltip = ref<{ key: string; title: string; tokens: number | null; partial: boolean; x: number; y: number; below: boolean }>()
const locale = computed(() => settings.language === 'zh-CN' ? 'zh-CN' : 'en-US')
/** Month captions aligned to the heatmap weeks, repeating only when the month changes. / 与热力图周对齐的月份标题，仅当月份变化时重复显示。 */
const months = computed(() => {
  let previous = ''
  return weeks.value.map(week => {
    const first = week.days.find(day => !day.future && day.date.slice(0, 7) !== previous)
    if (!first) return ''
    previous = first.date.slice(0, 7)
    return new Intl.DateTimeFormat(locale.value, { month: 'short', timeZone: 'UTC' }).format(new Date(first.date + 'T00:00:00Z'))
  })
})

/**
 * Formats a token count with an optional lower-bound marker.
 *
 * 格式化 Token 数量，可按需添加下界标记。
 *
 * @param tokens - Token count; null means usage was not reported. / Token 数量；null 表示未上报用量。
 * @param partial - True when the total is a lower bound. / 汇总为下界时为 true。
 * @returns Display text such as "≥ 1.2K tokens". / 例如 "≥ 1.2K tokens" 的展示文本。
 */
function tokenLabel(tokens: number | null, partial = false) {
  return (partial && tokens != null ? '≥ ' : '') + formatTokenCount(tokens) + ' tokens'
}
/**
 * Builds the accessible label for a heatmap day.
 *
 * 生成热力图某一天的无障碍标签。
 *
 * @param day - Activity cell for one UTC day. / 某个 UTC 日的活动单元。
 * @returns Date and token label. / 日期与 Token 文本。
 */
function dayLabel(day: ActivityCell) { return day.date + ' · ' + tokenLabel(day.tokens, (day.unreported ?? 0) > 0) }
/**
 * Builds the title/tooltip text for a weekly (or cumulative) bar.
 *
 * 生成每周（或累计）柱状的标题/提示文本。
 *
 * @param week - Activity week represented by the bar. / 柱状对应的活动周。
 * @returns Localized week range or cumulative label. / 本地化的周区间或累计标签。
 */
function weekTitle(week: ActivityWeek) {
  return view.value === 'cumulative' ? t('Through ', '截至 ') + week.end : week.date + ' – ' + week.end
}
/**
 * Positions the shared tooltip relative to the hovered/focused element.
 *
 * 将共用提示框定位到悬停/聚焦元素附近。
 *
 * @param event - Pointer or focus event on a chart target. / 图表目标上的指针或聚焦事件。
 * @param key - Stable key identifying the target. / 标识目标的稳定键。
 * @param title - Tooltip title text. / 提示框标题文本。
 * @param tokens - Token count for the target. / 目标的 Token 数量。
 * @param partial - True when the token total is a lower bound. / Token 汇总为下界时为 true。
 * @returns Nothing. / 无返回值。
 */
function show(event: Event, key: string, title: string, tokens: number | null, partial = false) {
  const rect = (event.currentTarget as Element).getBoundingClientRect()
  const below = rect.top < 100
  tooltip.value = { key, title, tokens, partial, below,
    x: Math.max(120, Math.min(window.innerWidth - 120, rect.left + rect.width / 2)),
    y: below ? rect.bottom + 10 : rect.top - 10 }
}
/**
 * Shows the tooltip for a heatmap day, skipping future days.
 *
 * 显示热力图某天的提示框，跳过未来日期。
 *
 * @param event - Pointer or focus event on the day cell. / 日单元上的指针或聚焦事件。
 * @param day - Activity cell for the day. / 当天的活动单元。
 * @returns Nothing. / 无返回值。
 */
function showDay(event: Event, day: ActivityCell) { if (!day.future) show(event, day.date, day.date, day.tokens, (day.unreported ?? 0) > 0) }
/**
 * Shows the tooltip for a weekly/cumulative bar.
 *
 * 显示每周/累计柱状的提示框。
 *
 * @param event - Pointer or focus event on the bar target. / 柱状目标上的指针或聚焦事件。
 * @param bar - Activity week represented by the bar. / 柱状对应的活动周。
 * @returns Nothing. / 无返回值。
 */
function showBar(event: Event, bar: ActivityWeek) { show(event, bar.date, weekTitle(bar), bar.tokens, bar.partial) }
/** Hides the shared tooltip. / 隐藏共用的提示框。 */
function hideTooltip() { tooltip.value = undefined }
/**
 * Moves focus between heatmap day cells using arrow/Home/End keys.
 *
 * 使用方向键/Home/End 在热力图日单元之间移动焦点。
 *
 * @param event - Keyboard event on the day cell. / 日单元上的键盘事件。
 * @param index - Flat day index of the current cell. / 当前单元的平铺日索引。
 * @returns Nothing. / 无返回值。
 */
function moveDay(event: KeyboardEvent, index: number) {
  const last = series.value?.lastDay ?? 0
  const next = { ArrowLeft: index - 7, ArrowRight: index + 7, ArrowUp: index - 1, ArrowDown: index + 1,
    Home: event.ctrlKey ? 0 : index - index % 7, End: event.ctrlKey ? last : Math.min(last, index - index % 7 + 6) }[event.key]
  if (next === undefined) return
  event.preventDefault()
  const target = Math.max(0, Math.min(last, next))
  ;(event.currentTarget as Element).closest('.account-heatmap')?.querySelector<HTMLButtonElement>(`[data-day-index="${target}"]`)?.focus()
}
/**
 * Moves focus between weekly/cumulative bars using arrow/Home/End keys.
 *
 * 使用方向键/Home/End 在每周/累计柱状之间移动焦点。
 *
 * @param event - Keyboard event on the bar target. / 柱状目标上的键盘事件。
 * @param index - Index of the current bar. / 当前柱状的索引。
 * @returns Nothing. / 无返回值。
 */
function moveBar(event: KeyboardEvent, index: number) {
  const next = { ArrowLeft: index - 1, ArrowRight: index + 1, Home: 0, End: bars.value.length - 1 }[event.key]
  if (next === undefined) return
  event.preventDefault()
  const target = Math.max(0, Math.min(bars.value.length - 1, next))
  ;(event.currentTarget as Element).closest('.account-bar-chart')?.querySelector<HTMLButtonElement>(`[data-bar-index="${target}"]`)?.focus()
}
// Keep the roving-tabindex focus on the latest day when usage data changes, and hide stale tooltips on view switches. / 用量数据变化时把焦点停在最新日期，并在切换视图时隐藏过期提示。
watch(() => props.usage, () => { focusDay.value = series.value?.lastDay ?? 0; hideTooltip() }, { immediate: true })
watch(view, hideTooltip)
// Expose tooltip dismissal so parent panels can close it when they scroll or close. / 暴露提示框关闭方法，供父面板滚动或关闭时调用。
defineExpose({ hideTooltip })
</script>

<template>
  <section class="account-activity" @keydown.esc="hideTooltip">
    <div class="account-section-heading">
      <h2>{{ t('Token activity', 'Token 活动') }}</h2>
      <div class="account-chart-tabs" role="group" :aria-label="t('Activity view', '活动视图')">
        <button v-for="tab in (['daily', 'weekly', 'cumulative'] as const)" :key="tab" type="button" :aria-pressed="view === tab" @click="view = tab">
          {{ tab === 'daily' ? t('Daily', '每天') : tab === 'weekly' ? t('Weekly', '每周') : t('Cumulative', '累计总量') }}
        </button>
      </div>
    </div>
    <div v-if="series" class="account-chart-scroll" @scroll.passive="hideTooltip">
      <div class="account-chart">
        <div v-if="view === 'daily'" class="account-heatmap" role="group" :aria-label="t('Daily token heatmap, use arrow keys to explore dates', '每日 Token 热力图，可用方向键切换日期')">
          <div v-for="(week, weekIndex) in weeks" :key="week.date" class="account-week">
            <button v-for="(day, dayIndex) in week.days" :key="day.date" type="button" class="account-day"
              :data-date="day.date" :data-day-index="weekIndex * 7 + dayIndex" :data-level="heatLevel(day.tokens, series.peak)"
              :class="{ 'account-future': day.future, 'account-day-unknown': day.tokens == null }"
              :disabled="day.future" :aria-hidden="day.future || undefined" :tabindex="!day.future && focusDay === weekIndex * 7 + dayIndex ? 0 : -1"
              :aria-label="dayLabel(day)" :aria-describedby="tooltip?.key === day.date ? tooltipId : undefined"
              @pointerenter="showDay($event, day)" @pointerleave="hideTooltip" @click="showDay($event, day)"
              @focus="focusDay = weekIndex * 7 + dayIndex; showDay($event, day)" @blur="hideTooltip" @keydown="moveDay($event, weekIndex * 7 + dayIndex)"></button>
          </div>
        </div>
        <div v-else class="account-bar-chart" :data-view="view" role="group" :aria-label="view === 'weekly' ? t('Weekly token consumption', '每周 Token 消耗柱形图') : t('Cumulative recorded tokens at each week’s end', '截至每周末的已记录累计 Token 柱形图')">
          <button v-for="(bar, index) in bars" :key="bar.date" type="button" class="account-bar-target" :data-date="bar.date" :data-bar-index="index"
            :tabindex="focusBar === index ? 0 : -1" :aria-label="weekTitle(bar) + ' · ' + tokenLabel(bar.tokens, bar.partial)"
            :aria-describedby="tooltip?.key === bar.date ? tooltipId : undefined"
            @pointerenter="showBar($event, bar)" @pointerleave="hideTooltip" @click="showBar($event, bar)"
            @focus="focusBar = index; showBar($event, bar)" @blur="hideTooltip" @keydown="moveBar($event, index)">
            <span class="account-bar" :class="{ 'account-bar-empty': !bar.tokens, 'account-bar-unknown': bar.tokens == null }" :style="{ height: bar.tokens ? Math.max(1, bar.tokens / barPeak * 100) + '%' : '2px' }"></span>
          </button>
        </div>
        <div class="account-months" aria-hidden="true"><span v-for="(month, index) in months" :key="index">{{ month }}</span></div>
      </div>
    </div>
    <div v-if="series && view === 'daily'" class="account-heat-legend" :aria-label="t('Darker color means more tokens', '颜色越深表示 Token 消耗越多')"><span>{{ t('Less', '少') }}</span><i v-for="level in [0, 1, 2, 3, 4, 5]" :key="level" :data-level="level"></i><span>{{ t('More', '多') }}</span></div>
    <p class="account-chart-note">{{ t('Saved runs, by UTC date. Forks count once. Missing usage is shown as —; partial totals are marked ≥.', '按已保存运行的 UTC 日期统计，分叉历史仅计一次。未上报用量显示 —，不完整汇总标注 ≥。') }}<template v-if="view === 'cumulative'"> {{ t('Each bar shows the recorded total through that week’s end.', '每根柱表示截至当周末的已记录累计用量。') }}</template></p>
    <Teleport to="body">
      <div v-if="tooltip" :id="tooltipId" class="account-token-tooltip" role="tooltip"
        :style="{ left: tooltip.x + 'px', top: tooltip.y + 'px', transform: tooltip.below ? 'translateX(-50%)' : 'translate(-50%, -100%)' }">
        <span>{{ tooltip.title }}</span><strong>{{ tokenLabel(tooltip.tokens, tooltip.partial) }}</strong>
        <small v-if="tooltip.tokens == null">{{ t('Usage was not reported.', '本次用量未上报。') }}</small>
        <small v-else-if="tooltip.partial">{{ t('Some runs did not report usage.', '部分运行未上报用量。') }}</small>
      </div>
    </Teleport>
  </section>
</template>
