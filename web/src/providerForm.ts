import type { ProviderModel } from './providerSettings'

export interface ProviderDraft {
  id: string; name: string; template: string; protocol: 'openai' | 'anthropic'
  base_url: string; api_key: string; enabled: boolean; models: ProviderModel[]
}
export type ProviderIssue = 'id' | 'name' | 'url' | 'key' | 'models' | 'model-id' | 'model-name' | 'context' | 'duplicate'

export function providerIssue(form: ProviderDraft, savedKey: boolean, requireModels = true): ProviderIssue | undefined {
  if (!/^[a-z][a-z0-9-]{0,47}$/.test(form.id.trim())) return 'id'
  if (!form.name.trim() || form.name.trim().length > 80) return 'name'
  try {
    const url = new URL(form.base_url.trim())
    if (!['http:', 'https:'].includes(url.protocol) || !url.hostname || url.username || url.password || url.search || url.hash) return 'url'
  } catch { return 'url' }
  if (!form.api_key.trim() && !savedKey) return 'key'
  return providerModelIssue(form.models, requireModels)
}

export function providerModelIssue(models: ProviderModel[], requireModels = true): ProviderIssue | undefined {
  if ((requireModels && !models.length) || models.length > 200) return 'models'
  const ids = new Set<string>()
  for (const model of models) {
    if (!/^[^\s\x00-\x1f]{1,128}$/.test(model.id)) return 'model-id'
    if (model.name != null && (!model.name.trim() || model.name.length > 128)) return 'model-name'
    if (ids.has(model.id)) return 'duplicate'
    ids.add(model.id)
    if (model.context_window != null && (!Number.isInteger(model.context_window) || model.context_window < 1024 || model.context_window > 10_000_000)) return 'context'
  }
}

export function providerPayload(form: ProviderDraft, kind: 'builtin' | 'custom') {
  return {
    id: form.id.trim(), name: form.name.trim(), template: kind === 'builtin' ? form.template : null,
    protocol: form.protocol, base_url: form.base_url.trim(), enabled: form.enabled,
    models: form.models.map(item => ({ ...item })),
    ...(form.api_key.trim() ? { api_key: form.api_key.trim() } : {}),
  }
}

export function developmentOnboarding(development: boolean, search: string) {
  return development && new URLSearchParams(search).get('onboarding') === '1'
}
