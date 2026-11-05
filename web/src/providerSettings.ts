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

export async function loadProviderSettings() {
  configurationError.value = ''
  try {
    const result = await workRequest<ProviderList>('/settings/providers')
    setupRequired.value = result.setup_required
    configurationLoaded.value = true
    return result
  } catch (error) {
    configurationError.value = error instanceof Error ? error.message : String(error)
    throw error
  }
}
