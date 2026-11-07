/**
 * User preferences store: language, appearance, personalized-memory flag and localStorage helpers.
 *
 * 用户偏好存储：语言、外观、定制记忆开关以及 localStorage 助手。
 */

import { computed, reactive, watch } from 'vue'
import { DEFAULT_ACCENT, customAccentTokens, normalizeAccent, savedPalette } from './appearance'

/**
 * Reads and JSON-parses a localStorage entry.
 *
 * 读取并 JSON 解析一个 localStorage 条目。
 *
 * @typeParam T - Expected stored value type. / 期望的存储值类型。
 * @param key - Storage key. / 存储键。
 * @param fallback - Value returned when missing or unparsable. / 缺失或解析失败时返回的默认值。
 * @returns Parsed value or the fallback. / 解析结果或默认值。
 */
export function readLocal<T>(key: string, fallback: T): T {
  try {
    return JSON.parse(localStorage.getItem(key) ?? 'null') ?? fallback
  } catch {
    return fallback
  }
}

/**
 * Serializes a value into localStorage, ignoring quota or privacy-mode errors.
 *
 * 将值序列化写入 localStorage，忽略配额或隐私模式导致的错误。
 *
 * @param key - Storage key. / 存储键。
 * @param value - JSON-serializable value. / 可 JSON 序列化的值。
 */
export function writeLocal(key: string, value: unknown) {
  try {
    localStorage.setItem(key, JSON.stringify(value))
  } catch {
    // The current session still works when browser storage is unavailable.
    // 浏览器存储不可用时，当前会话仍可正常工作。
  }
}

const saved = readLocal<Partial<{
  mode: string
  palette: string
  customAccent: string
  font: string
  language: string
  personalizedMemory: boolean
  appearanceVersion: number
}>>('adaptive.preferences', {})

// Reactive singleton; defaults to Chinese UI, dark mode and the accent palette.
// 响应式单例；默认中文界面、深色模式与强调色配色。
export const settings = reactive({
  language: saved.language ?? 'zh-CN',
  personalizedMemory: saved.personalizedMemory ?? readLocal('adaptive.useMemory', true),
  mode: saved.appearanceVersion ? saved.mode ?? 'dark' : 'dark',
  palette: savedPalette(saved.palette),
  customAccent: normalizeAccent(saved.customAccent) ?? DEFAULT_ACCENT,
  font: saved.font === 'geist' || saved.font === 'serif' ? saved.font : 'system',
  appearanceVersion: 3,
})

/** Whether the UI currently renders Chinese copy. / 界面当前是否使用中文文案。 */
export const zh = computed(() => settings.language === 'zh-CN')
// Every string in the app goes through this helper; Chinese sources stay untouched.
// 应用内所有文案都经由该助手；中文源字符串保持不变。
/**
 * Picks the active-language string from a bilingual pair.
 *
 * 从双语对中挑出当前语言的字符串。
 *
 * @param english - English copy. / 英文文案。
 * @param chinese - Chinese copy. / 中文文案。
 * @returns The string for the active language. / 当前语言对应的字符串。
 */
export const t = (english: string, chinese: string) => zh.value ? chinese : english

// Mirror appearance and language onto the document root, and persist on every change.
// 将外观与语言同步到文档根节点，并在每次变化时持久化。
watch(settings, () => {
  const root = document.documentElement
  root.dataset.mode = settings.mode
  root.dataset.palette = settings.palette
  root.dataset.font = settings.font
  root.lang = settings.language
  for (const [token, value] of Object.entries(customAccentTokens(settings.customAccent))) root.style.setProperty(token, value)
  writeLocal('adaptive.preferences', settings)
}, { immediate: true })
