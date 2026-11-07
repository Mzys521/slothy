/**
 * Appearance tokens: accent normalization and accessible custom-accent CSS variables.
 *
 * 外观变量：强调色规范化，以及满足可读性的自定义强调色 CSS 变量。
 */

export const DEFAULT_ACCENT = '#60DD07'
export const DEFAULT_PALETTE = 'green'

/**
 * Normalizes a user-entered hex color to uppercase `#RRGGBB`.
 *
 * 将用户输入的十六进制颜色规范化为大写 `#RRGGBB`。
 *
 * @param value - Raw stored value of unknown type. / 类型未知的原始存储值。
 * @returns Normalized color, or `undefined` when the input is not valid hex. / 规范化颜色；非合法十六进制时返回 `undefined`。
 */
export function normalizeAccent(value: unknown): string | undefined {
  if (typeof value !== 'string') return
  const hex = value.trim().replace(/^#/, '')
  if (/^[\da-f]{3}$/i.test(hex)) return '#' + [...hex].map(char => char + char).join('').toUpperCase()
  if (/^[\da-f]{6}$/i.test(hex)) return '#' + hex.toUpperCase()
}

/**
 * Restricts a stored palette name to the supported set.
 *
 * 将存储的配色名称限制在受支持集合内。
 *
 * @param value - Raw stored value of unknown type. / 类型未知的原始存储值。
 * @returns A supported palette name, falling back to the default. / 受支持的配色名称，非法时回退默认值。
 */
export function savedPalette(value: unknown): string {
  return ['green', 'copper', 'neutral', 'slate', 'custom'].includes(String(value)) ? String(value) : DEFAULT_PALETTE
}

/**
 * Splits a hex color into 0-255 RGB channels.
 *
 * 将十六进制颜色拆分为 0-255 的 RGB 通道。
 *
 * @param hex - `#RRGGBB` string. / `#RRGGBB` 字符串。
 * @returns Three integer channels. / 三个整数通道。
 */
function rgb(hex: string): number[] {
  return [1, 3, 5].map(index => parseInt(hex.slice(index, index + 2), 16))
}
/**
 * Linearly interpolates two colors.
 *
 * 对两种颜色做线性插值。
 *
 * @param color - Starting color channels. / 起始颜色通道。
 * @param target - End color channels. / 目标颜色通道。
 * @param weight - Weight of the target, 0-1. / 目标颜色的权重，0-1。
 * @returns Interpolated channels. / 插值后的通道。
 */
function mix(color: number[], target: number[], weight: number): number[] {
  return color.map((channel, index) => Math.round(channel * (1 - weight) + target[index]! * weight))
}
/**
 * Computes relative luminance using the WCAG sRGB formula.
 *
 * 按 WCAG sRGB 公式计算相对亮度。
 *
 * @param color - RGB channels, 0-255. / 0-255 的 RGB 通道。
 * @returns Relative luminance, 0-1. / 0-1 的相对亮度。
 */
function luminance(color: number[]): number {
  const linear = color.map(channel => {
    const srgb = channel / 255
    return srgb <= .04045 ? srgb / 12.92 : ((srgb + .055) / 1.055) ** 2.4
  })
  return linear[0]! * .2126 + linear[1]! * .7152 + linear[2]! * .0722
}
/**
 * Computes the WCAG contrast ratio between two colors.
 *
 * 计算两种颜色之间的 WCAG 对比度。
 *
 * @param a - First RGB color. / 第一种 RGB 颜色。
 * @param b - Second RGB color. / 第二种 RGB 颜色。
 * @returns Contrast ratio in the 1-21 range. / 1-21 范围内的对比度。
 */
function contrast(a: number[], b: number[]): number {
  const first = luminance(a), second = luminance(b)
  return (Math.max(first, second) + .05) / (Math.min(first, second) + .05)
}
/**
 * Formats RGB channels back into an uppercase `#RRGGBB` string.
 *
 * 将 RGB 通道格式化为大写 `#RRGGBB` 字符串。
 *
 * @param color - RGB channels, 0-255. / 0-255 的 RGB 通道。
 * @returns Hex color string. / 十六进制颜色字符串。
 */
function hex(color: number[]): string {
  return '#' + color.map(channel => channel.toString(16).padStart(2, '0')).join('').toUpperCase()
}

/**
 * Derives the CSS variables for the custom accent, including contrast-safe ink colors.
 *
 * 推导自定义强调色所需的 CSS 变量，包含满足对比度的文字颜色。
 *
 * @param value - Stored accent value; invalid input falls back to the default accent. / 存储的强调色值；非法时回退默认强调色。
 * @returns CSS custom properties to apply on the document root. / 应用到文档根节点的 CSS 自定义属性。
 */
export function customAccentTokens(value: unknown) {
  const accent = normalizeAccent(value) ?? DEFAULT_ACCENT
  const color = rgb(accent)
  // Match the 14% accent wash over the darkest/lightest neutral control surfaces.
  // Keep normal-sized accent text readable even for very light or very dark choices.
  // 与 14% 强调色叠加到最深/最浅中性控件表面的效果保持一致。
  // 即使选择很浅或很深的强调色，也要保证常规字号文字可读。
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
