export interface JournalEvent {
  schema_version?: number
  event_type?: string
  timestamp?: string
  session_id?: string
  run_id?: string
  step_id?: string
  model_call_id?: string
  provider?: string
  model?: string
  parameters?: Record<string, unknown>
  context?: Record<string, number | boolean>
  tools?: { count?: number; names?: string[]; schema_tokens?: number }
  usage?: Record<string, number | string | null>
  duration_ms?: number
  status?: string
  response_status?: string | null
}

export interface DonutSegment {
  key: string
  label: string
  value: number
  color: string
}

const categories = [
  ['system', 'System prompt', '系统提示词', '--chart-system'],
  ['runtime', 'Runtime context', '运行上下文', '--chart-runtime'],
  ['tool_schema', 'Tool schemas', '工具定义', '--chart-tool-schema'],
  ['history', 'Conversation history', '历史消息', '--chart-history'],
  ['user', 'Current input', '当前输入', '--chart-user'],
  ['memory', 'Memory & profile', '记忆与画像', '--chart-memory'],
  ['other', 'Other', '其他', '--chart-other'],
] as const

export function estimateSegments(calls: JournalEvent[], t: (en: string, zh: string) => string): DonutSegment[] {
  return categories.map(([key, en, zh, token]) => ({
    key, label: t(en, zh), color: 'var(' + token + ')',
    value: calls.reduce((sum, call) => {
      const value = call.context?.[key]
      return sum + (typeof value === 'number' && value > 0 ? value : 0)
    }, 0),
  }))
}

export function donutArcs(segments: DonutSegment[], total: number) {
  const sorted = segments.filter(item => Number.isFinite(item.value) && item.value > 0).sort((a, b) => b.value - a.value)
  const sum = sorted.reduce((n, item) => n + item.value, 0)
  const gap = sorted.length > 1 ? 1.25 : 0 // Degrees at radius 65 ≈ 1.4 SVG pixels.
  let extra = 0
  const sweeps = sorted.map((item, i) => {
    const exact = 360 * item.value / sum
    const bonus = i > 0 ? Math.max(0, 2 + gap - exact) : 0
    extra += bonus
    return exact + bonus
  })
  let cursor = 0
  return sorted.map((item, index) => {
    const sweep = sweeps[index]! - (index === 0 ? extra : 0)
    const percent = total > 0 ? item.value / total * 100 : 0
    const start = cursor + gap / 2
    cursor += sweep
    return { ...item, start, sweep, painted: sweep - gap, percent: percent < 1 ? '<1%' : Math.round(percent) + '%' }
  })
}

export function providerUsage(calls: JournalEvent[]) {
  function measure(read: (call: JournalEvent) => unknown) {
    const values = calls.map(read).filter((n): n is number => typeof n === 'number' && Number.isFinite(n) && n >= 0)
    const sum = values.reduce((a, b) => a + b, 0)
    const complete = values.length === calls.length
    return { sum, complete, display: values.length ? (complete ? '' : '≥ ') + sum : '—' }
  }
  const field = (key: string) => measure(call => call.usage?.source === 'provider' ? call.usage[key] : null)
  const input = field('input_tokens'), output = field('output_tokens'), cache = field('cache_hit_tokens')
  const hitRate = input.complete && cache.complete && input.sum > 0 ? cache.sum / input.sum : null
  return { input, output, cache, hitRate, duration: measure(call => call.duration_ms) }
}
