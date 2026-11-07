/**
 * Pure provider-form validation and payload shaping, shared by the editor composable and tests.
 *
 * 纯函数式的提供商表单校验与请求体构造，供编辑组合式函数与测试共用。
 */

import type { ProviderModel } from './providerSettings'

/** Editable provider draft held by the settings form. / 设置表单中可编辑的提供商草稿。 */
export interface ProviderDraft {
  id: string; name: string; template: string; protocol: 'openai' | 'anthropic'
  base_url: string; api_key: string; enabled: boolean; models: ProviderModel[]
}
/** Validation failure codes used to pick a localized message. / 用于挑选本地化文案的校验错误码。 */
export type ProviderIssue = 'id' | 'name' | 'url' | 'key' | 'models' | 'model-id' | 'model-name' | 'context' | 'duplicate'

/**
 * Validates a whole provider draft, returning the first problem found.
 *
 * 校验完整的提供商草稿，返回第一个发现的问题。
 *
 * @param form - Draft being edited. / 正在编辑的草稿。
 * @param savedKey - Whether this provider already has a stored API key. / 该提供商是否已保存过 API 密钥。
 * @param requireModels - Require at least one model (disable for discovery). / 是否要求至少一个模型（发现目录时传 false）。
 * @returns First issue code, or `undefined` when the draft is valid. / 首个错误码；草稿合法时返回 `undefined`。
 */
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

/**
 * Validates the model list: identity, naming, context window and duplicates.
 *
 * 校验模型列表：标识、命名、上下文窗口与重复项。
 *
 * @param models - Models currently attached to the draft. / 当前挂到草稿上的模型。
 * @param requireModels - Require at least one model. / 是否要求至少一个模型。
 * @returns First issue code, or `undefined` when valid. / 首个错误码；合法时返回 `undefined`。
 */
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

/**
 * Builds the API payload for a draft, omitting an unchanged blank API key.
 *
 * 构造草稿对应的 API 请求体；空的 API 密钥不会包含在内。
 *
 * @param form - Validated draft. / 已校验的草稿。
 * @param kind - Built-in template or fully custom provider. / 内置模板或完全自定义提供商。
 * @returns Payload accepted by `POST/PUT /settings/providers`. / `POST/PUT /settings/providers` 接受的请求体。
 */
export function providerPayload(form: ProviderDraft, kind: 'builtin' | 'custom') {
  return {
    id: form.id.trim(), name: form.name.trim(), template: kind === 'builtin' ? form.template : null,
    protocol: form.protocol, base_url: form.base_url.trim(), enabled: form.enabled,
    models: form.models.map(item => ({ ...item })),
    ...(form.api_key.trim() ? { api_key: form.api_key.trim() } : {}),
  }
}

/**
 * Detects the `?onboarding=1` development shortcut for the first-run screen.
 *
 * 识别首次运行页面的 `?onboarding=1` 开发快捷入口。
 *
 * @param development - Whether the build is a development build. / 是否为开发构建。
 * @param search - `location.search` of the current page. / 当前页面的 `location.search`。
 * @returns `true` when the onboarding overlay should be forced. / 需要强制显示引导浮层时返回 `true`。
 */
export function developmentOnboarding(development: boolean, search: string) {
  return development && new URLSearchParams(search).get('onboarding') === '1'
}
