import { ref } from 'vue'
import { workRequest } from './workApi'

export interface ProviderModel { id: string; name?: string | null; context_window?: number | null }
export interface ProviderTemplate { id: string; name: string; base_url: string; protocol: 'openai' | 'anthropic'; model_ids: string[] }
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
export interface ProviderList { items: ProviderConfig[]; templates: ProviderTemplate[]; setup_required: boolean }
export const setupRequired = ref(false)
export const configurationLoaded = ref(false)
export const configurationError = ref('')
export const configurationLoading = ref(false)
export const providerConfigs = ref<ProviderConfig[]>([])
export const providerTemplates = ref<ProviderTemplate[]>([])
let pending: Promise<ProviderList> | undefined
let revision = 0

export function loadProviderSettings(options: { refresh?: boolean } = {}): Promise<ProviderList> {
  if (pending && !options.refresh) return pending
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
