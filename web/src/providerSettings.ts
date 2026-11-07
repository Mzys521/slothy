/**
 * Provider configuration store: cached provider list, first-run flag and a deduplicated loader.
 *
 * 提供商配置存储：缓存提供商列表、首次运行标记以及去重的加载器。
 */

import { ref } from 'vue'
import { workRequest } from './workApi'

/** One model entry inside a provider configuration. / 提供商配置中的单个模型条目。 */
export interface ProviderModel { id: string; name?: string | null; context_window?: number | null }
/** Built-in provider preset offered in the editor. / 编辑器中提供的内置提供商预设。 */
export interface ProviderTemplate { id: string; name: string; base_url: string; protocol: 'openai' | 'anthropic'; model_ids: string[] }
/** Provider configuration as exposed publicly; API keys are never returned. / 对外暴露的提供商配置；API 密钥不会返回。 */
export interface ProviderConfig {
  id: string
  name: string
  template: string | null
  protocol: 'openai' | 'anthropic'
  base_url: string
  models: ProviderModel[]
  enabled: boolean
  has_api_key: boolean
}
/** Response of `GET /settings/providers`. / `GET /settings/providers` 的响应。 */
export interface ProviderList { items: ProviderConfig[]; templates: ProviderTemplate[]; setup_required: boolean }
export const setupRequired = ref(false)
export const configurationLoaded = ref(false)
export const configurationError = ref('')
export const configurationLoading = ref(false)
export const providerConfigs = ref<ProviderConfig[]>([])
export const providerTemplates = ref<ProviderTemplate[]>([])
let pending: Promise<ProviderList> | undefined
let revision = 0

/**
 * Loads provider settings, sharing the in-flight request unless a refresh is forced.
 *
 * 加载提供商设置；除非强制刷新，否则复用进行中的请求。
 *
 * @param options - Load options. / 加载选项。
 * @param options.refresh - Bypass request coalescing and fetch fresh data. / 为 true 时跳过请求合并，强制拉取最新数据。
 * @returns Provider list payload. / 提供商列表载荷。
 */
export function loadProviderSettings(options: { refresh?: boolean } = {}): Promise<ProviderList> {
  if (pending && !options.refresh) return pending
  // Only the newest revision may write shared state, so late responses cannot win.
  // 只有最新版本号可以写入共享状态，避免迟到响应覆盖新数据。
  const request = ++revision
  configurationError.value = ''
  configurationLoading.value = true
  pending = (async () => {
    try {
      const result = await workRequest<ProviderList>('/settings/providers')
      if (request === revision) {
        providerConfigs.value = result.items
        providerTemplates.value = result.templates
        setupRequired.value = result.setup_required
        configurationLoaded.value = true
      }
      return result
    } catch (error) {
      if (request === revision) {
        configurationLoaded.value = false
        configurationError.value = error instanceof Error ? error.message : String(error)
      }
      throw error
    } finally {
      if (request === revision) { pending = undefined; configurationLoading.value = false }
    }
  })()
  return pending
}
