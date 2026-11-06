export const DEFAULT_ACCENT = '#60DD07'
export const DEFAULT_PALETTE = 'green'

export function normalizeAccent(value: unknown): string | undefined {
  if (typeof value !== 'string') return
  const hex = value.trim().replace(/^#/, '')
  if (/^[\da-f]{3}$/i.test(hex)) return '#' + [...hex].map(char => char + char).join('').toUpperCase()
  if (/^[\da-f]{6}$/i.test(hex)) return '#' + hex.toUpperCase()
}

export function savedPalette(value: unknown): string {
  return ['green', 'copper', 'neutral', 'slate', 'custom'].includes(String(value)) ? String(value) : DEFAULT_PALETTE
}

function rgb(hex: string): number[] {
  return [1, 3, 5].map(index => parseInt(hex.slice(index, index + 2), 16))
}
function mix(color: number[], target: number[], weight: number): number[] {
  return color.map((channel, index) => Math.round(channel * (1 - weight) + target[index]! * weight))
}
function luminance(color: number[]): number {
  const linear = color.map(channel => {
    const srgb = channel / 255
    return srgb <= .04045 ? srgb / 12.92 : ((srgb + .055) / 1.055) ** 2.4
  })
  return linear[0]! * .2126 + linear[1]! * .7152 + linear[2]! * .0722
}
function contrast(a: number[], b: number[]): number {
  const first = luminance(a), second = luminance(b)
  return (Math.max(first, second) + .05) / (Math.min(first, second) + .05)
}
function hex(color: number[]): string {
  return '#' + color.map(channel => channel.toString(16).padStart(2, '0')).join('').toUpperCase()
}

export function customAccentTokens(value: unknown) {
  const accent = normalizeAccent(value) ?? DEFAULT_ACCENT
  const color = rgb(accent)
  // Match the 14% accent wash over the darkest/lightest neutral control surfaces.
  // Keep normal-sized accent text readable even for very light or very dark choices.
  const ink = (surface: string, endpoint: string) => {
    const background = mix(rgb(surface), color, .14)
    for (let step = 0; step <= 100; step++) {
      const candidate = mix(color, rgb(endpoint), step / 100)
      if (contrast(candidate, background) >= 5.5) return hex(candidate)
    }
    return endpoint
  }
  return {
    '--custom-accent': accent,
    '--custom-accent-light-ink': ink('#EEEDE9', '#252520'),
    '--custom-accent-dark-ink': ink('#242424', '#F5F5F3'),
  }
}
