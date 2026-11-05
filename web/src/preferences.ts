import { computed, reactive, watch } from 'vue'

export function readLocal<T>(key: string, fallback: T): T {
  try {
    return JSON.parse(localStorage.getItem(key) ?? 'null') ?? fallback
  } catch {
    return fallback
  }
}

export function writeLocal(key: string, value: unknown) {
  try {
    localStorage.setItem(key, JSON.stringify(value))
  } catch {
    // The current session still works when browser storage is unavailable.
  }
}

const saved = readLocal<Partial<{
  mode: string
  palette: string
  font: string
  language: string
  personalizedMemory: boolean
  appearanceVersion: number
}>>('adaptive.preferences', {})

export const settings = reactive({
  language: saved.language ?? 'zh-CN',
  personalizedMemory: saved.personalizedMemory ?? readLocal('adaptive.useMemory', true),
  mode: saved.appearanceVersion ? saved.mode ?? 'dark' : 'dark',
  palette: saved.appearanceVersion === 2 && (saved.palette === 'slate' || saved.palette === 'neutral') ? saved.palette : 'copper',
  font: saved.font === 'geist' || saved.font === 'serif' ? saved.font : 'system',
  appearanceVersion: 2,
})

export const zh = computed(() => settings.language === 'zh-CN')
export const t = (english: string, chinese: string) => zh.value ? chinese : english

watch(settings, () => {
  const root = document.documentElement
  root.dataset.mode = settings.mode
  root.dataset.palette = settings.palette
  root.dataset.font = settings.font
  root.lang = settings.language
  writeLocal('adaptive.preferences', settings)
}, { immediate: true })
