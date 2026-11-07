/**
 * Token-activity aggregation for the account heatmap and cumulative chart.
 *
 * 账户热力图与累计曲线所用的 token 活跃度聚合逻辑。
 */

/** One reported day of activity. / 单日活跃度记录。 */
export interface ActivityDay { date: string; turns: number; tokens: number | null; unreported?: number }
/** Raw account activity payload. / 原始账户活跃度载荷。 */
export interface ActivityInput { as_of: string; daily: ActivityDay[]; prior_tokens: number }
/** A day cell placed on the 53×7 grid. / 放置在 53×7 网格上的日单元格。 */
export interface ActivityCell extends ActivityDay { future: boolean }
/** One aggregated week column. / 聚合后的单周列。 */
export interface ActivityWeek {
  date: string
  end: string
  days: ActivityCell[]
  tokens: number | null
  partial: boolean
  reported: boolean
}

const dayMs = 86_400_000

// Keep K/M stable across display languages instead of switching to 万/亿.
// 跨显示语言统一使用 K/M，避免切换为「万/亿」。
/**
 * Formats a token count with a compact K/M suffix.
 *
 * 使用紧凑的 K/M 后缀格式化 token 数量。
 *
 * @param value - Token count, possibly null or non-finite. / token 数量，可能为 null 或非有限值。
 * @returns Short display string, `—` when unknown. / 简短展示字符串，未知时返回 `—`。
 */
export function formatTokenCount(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '—'
  if (Math.abs(value) >= 999_950) return (value / 1_000_000).toFixed(1) + 'M'
  if (Math.abs(value) >= 1000) return (value / 1000).toFixed(1) + 'K'
  return value.toFixed(1)
}

/**
 * Builds the 53-week grid, weekly rows and cumulative series.
 *
 * 构建 53 周网格、每周行以及累计序列。
 *
 * @param input - Account activity payload from the backend. / 后端返回的账户活跃度载荷。
 * @returns Day cells, weeks, cumulative weeks, last-day index and peak value. / 日单元格、周、累计周、最后一天索引与峰值。
 */
export function buildTokenActivity(input: ActivityInput) {
  const end = Date.parse(input.as_of + 'T00:00:00Z')
  const weekday = (new Date(end).getUTCDay() + 6) % 7
  const start = end - (52 * 7 + weekday) * dayMs
  const activity = new Map(input.daily.map(day => [day.date, day]))
  const days: ActivityCell[] = Array.from({ length: 53 * 7 }, (_, index) => {
    const time = start + index * dayMs
    const date = new Date(time).toISOString().slice(0, 10)
    const entry = activity.get(date)
    return { date, future: time > end, turns: entry?.turns ?? 0, tokens: entry ? entry.tokens : 0, unreported: entry?.unreported ?? 0 }
  })
  // A week is partial when any elapsed day lacks reported usage; unknown totals stay null.
  // 只要有一天的用量未上报，该周即视为部分上报；未知总量保持 null。
  const weeks: ActivityWeek[] = Array.from({ length: 53 }, (_, index) => {
    const current = days.slice(index * 7, index * 7 + 7)
    const valid = current.filter(day => !day.future)
    const partial = valid.some(day => day.tokens == null || (day.unreported ?? 0) > 0)
    const reported = valid.some(day => day.tokens != null && (day.turns > 0 || day.tokens > 0))
    return { date: current[0]!.date, end: valid.at(-1)!.date, days: current, partial, reported,
      tokens: partial && !reported ? null : valid.reduce((total, day) => total + (day.tokens ?? 0), 0) }
  })
  let total = input.prior_tokens
  let partial = false
  let reported = input.prior_tokens > 0
  const cumulative = weeks.map(week => {
    total += week.tokens ?? 0
    partial ||= week.partial
    reported ||= week.reported
    return { ...week, partial, tokens: partial && !reported ? null : total }
  })
  return { days, weeks, cumulative, lastDay: 52 * 7 + weekday,
    peak: Math.max(1, ...days.filter(day => !day.future).map(day => day.tokens ?? 0)) }
}

/**
 * Maps a day's tokens onto one of five heat levels.
 *
 * 将单日 token 映射到五个热度等级之一。
 *
 * @param tokens - Day token count, or null when unknown. / 当日 token 数量，未知时为 null。
 * @param peak - Maximum day value used as the scale ceiling. / 作为刻度上限的日最大值。
 * @returns Heat level 0-5. / 0-5 的热度等级。
 */
export function heatLevel(tokens: number | null, peak: number): number {
  if (tokens == null || tokens <= 0) return 0
  return Math.min(5, Math.max(1, Math.ceil(Math.sqrt(tokens / Math.max(1, peak)) * 5)))
}
