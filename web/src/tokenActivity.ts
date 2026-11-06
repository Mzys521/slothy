export interface ActivityDay { date: string; turns: number; tokens: number | null; unreported?: number }
export interface ActivityInput { as_of: string; daily: ActivityDay[]; prior_tokens: number }
export interface ActivityCell extends ActivityDay { future: boolean }
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
export function formatTokenCount(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '—'
  if (Math.abs(value) >= 999_950) return (value / 1_000_000).toFixed(1) + 'M'
  if (Math.abs(value) >= 1000) return (value / 1000).toFixed(1) + 'K'
  return value.toFixed(1)
}

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

export function heatLevel(tokens: number | null, peak: number): number {
  if (tokens == null || tokens <= 0) return 0
  return Math.min(5, Math.max(1, Math.ceil(Math.sqrt(tokens / Math.max(1, peak)) * 5)))
}
