/**
 * Composable that owns the provider editor form state and save/discover operations.
 *
 * 提供商编辑器组合式函数：持有表单状态以及保存/发现目录等操作。
 */

import { computed, onUnmounted, reactive, ref } from 'vue'
import { t } from './preferences'
import { ApiError, workRequest } from './workApi'
import { loadProviderSettings, providerConfigs, providerTemplates, type ProviderConfig, type ProviderModel } from './providerSettings'
import { providerIssue, providerModelIssue, providerPayload, type ProviderDraft, type ProviderIssue } from './providerForm'

/**
 * Maps a validation issue code onto a localized message.
 *
 * 将校验错误码映射为本地化文案。
 *
 * @param issue - Validation issue code. / 校验错误码。
 * @returns Localized message for the issue. / 该错误对应的本地化文案。
 */
function issueMessage(issue: ProviderIssue) {
  return {
    id: t('Use a provider ID starting with a lowercase letter (up to 48 characters).', '提供商 ID 需以小写字母开头，最多 48 个字符。'),
    name: t('Enter a display name (up to 80 characters).', '请输入显示名称，最多 80 个字符。'),
    url: t('Enter an HTTP(S) base URL without credentials, query or fragment.', '请输入 http(s) API 根地址，不包含账号、查询参数或片段。'),
    key: t('Enter an API key.', '请输入 API 密钥。'),
    models: t('Select or add at least one model (up to 200).', '请至少选择或添加一个模型，最多 200 个。'),
    'model-id': t('Model IDs cannot contain whitespace and must be at most 128 characters.', '模型 ID 不能包含空白字符，最多 128 个字符。'),
    'model-name': t('Model names must be at most 128 characters.', '模型名称最多 128 个字符。'),
    context: t('Context window must be an integer from 1,024 to 10,000,000.', '上下文窗口需为 1,024 至 10,000,000 的整数。'),
    duplicate: t('This model already exists.', '该模型已在目录中。'),
  }[issue]
}

/**
 * Creates the provider-editor state, computed flags and actions.
 *
 * 创建提供商编辑器的状态、计算属性与操作方法。
 *
 * @returns Reactive editor state and commands consumed by the settings page. / 设置页使用的响应式编辑器状态与命令。
 */
export function useProviderEditor() {
  const providers = providerConfigs
  const templates = providerTemplates
  const editing = ref(false)
  const editId = ref('')
  const kind = ref<'builtin' | 'custom'>('builtin')
  const saving = ref(false)
  const discovering = ref(false)
  const error = ref('')
  const notice = ref('')
  const showKey = ref(false)
  const addModelOpen = ref(false)
  const catalog = ref<ProviderModel[]>([])
  const lastOperation = ref<'save' | 'discover'>('save')
  const discoverManual = ref(false)
  const newModel = reactive({ id: '', name: '', context: '' })
  const form = reactive<ProviderDraft>({ id: '', name: '', template: '', protocol: 'openai', base_url: '', api_key: '', enabled: true, models: [] })
  const committedKey = ref('')
  const savedKey = computed(() => committedKey.value === editId.value && Boolean(editId.value)
    || Boolean(providers.value.find(item => item.id === editId.value)?.has_api_key))
  const busy = computed(() => saving.value || discovering.value)
  const canSave = computed(() => !providerIssue(form, savedKey.value))
  const canDiscover = computed(() => !providerIssue(form, savedKey.value, false))

  /**
   * Stores a failure message, masking the API key and mapping common statuses.
   *
   * 记录失败信息：遮蔽 API 密钥，并为常见状态码补充前缀说明。
   *
   * @param reason - Thrown error or unknown rejection value. / 抛出的错误或未知的拒绝值。
   */
  function report(reason: unknown) {
    let message = reason instanceof Error ? reason.message : String(reason)
    if (form.api_key.trim()) message = message.split(form.api_key.trim()).join('••••••••')
    const prefix = reason instanceof ApiError ? {
      422: t('Check your configuration.', '请检查配置信息。'),
      409: t('This provider conflicts with an existing configuration.', '提供商与现有配置冲突。'),
      502: t('Could not connect to the provider.', '无法连接服务商。'),
    }[reason.status] : undefined
    error.value = [prefix, message].filter(Boolean).join(' ')
  }

  /**
   * Fills the form from the selected built-in template.
   *
   * 用选中的内置模板填充表单。
   */
  function selectTemplate() {
    const template = templates.value.find(item => item.id === form.template)
    if (!template) return
    if (!editId.value) form.id = template.id
    form.name = template.name
    form.base_url = template.base_url
    form.protocol = template.protocol
    form.models = template.model_ids.map(id => ({ id, name: id }))
    catalog.value = form.models.map(item => ({ ...item }))
  }
  /**
   * Switches between built-in and custom provider input.
   *
   * 在内置提供商与自定义提供商输入之间切换。
   *
   * @param value - Target input kind. / 目标输入类型。
   */
  function changeKind(value: 'builtin' | 'custom') {
    if (kind.value === value || editId.value) return
    kind.value = value; form.api_key = ''; showKey.value = false
    if (value === 'builtin') selectTemplate()
    else { Object.assign(form, { id: '', name: '', protocol: 'openai', base_url: '', models: [] }); catalog.value = [] }
  }
  /**
   * Opens the editor for an existing provider or a new one.
   *
   * 打开编辑器：编辑已有提供商或新建提供商。
   *
   * @param provider - Provider to edit; omit to create a new one. / 要编辑的提供商；省略表示新建。
   */
  function openEditor(provider?: ProviderConfig) {
    error.value = ''; notice.value = ''; showKey.value = false; committedKey.value = ''; discoverManual.value = false
    editId.value = provider?.id ?? ''
    kind.value = provider?.template === null ? 'custom' : 'builtin'
    if (provider) {
      Object.assign(form, { ...provider, models: provider.models.map(item => ({ ...item })), api_key: '' })
      catalog.value = provider.models.map(item => ({ ...item }))
    } else {
      Object.assign(form, { id: '', name: '', template: templates.value[0]?.id ?? '', api_key: '', enabled: true, models: [] })
      selectTemplate()
    }
    editing.value = true
  }
  /**
   * Closes the editor unless a request is still running.
   *
   * 在请求未结束时不会关闭编辑器。
   */
  function closeEditor() {
    if (!busy.value) { editing.value = false; form.api_key = ''; showKey.value = false; error.value = '' }
  }
  /**
   * Creates or updates the provider, then reloads the configuration.
   *
   * 创建或更新提供商，然后重新加载配置。
   *
   * @returns Refreshed provider list, or `undefined` when validation or the request failed. / 刷新后的提供商列表；校验或请求失败时返回 `undefined`。
   */
  async function save() {
    if (busy.value) return
    lastOperation.value = 'save'
    const issue = providerIssue(form, savedKey.value)
    if (issue) { error.value = issueMessage(issue); return }
    saving.value = true; error.value = ''; notice.value = ''
    try {
      await workRequest('/settings/providers' + (editId.value ? '/' + encodeURIComponent(editId.value) : ''), editId.value ? 'PUT' : 'POST', providerPayload(form, kind.value))
      // Erase the plaintext as soon as the local backend has accepted it, even if reload fails.
      // 后端接受后立刻清除明文密钥，即使随后刷新配置失败。
      form.api_key = ''; showKey.value = false
      editId.value = form.id.trim(); committedKey.value = editId.value
      const result = await loadProviderSettings({ refresh: true })
      return result
    } catch (reason) { report(reason) }
    finally { saving.value = false }
  }
  /**
   * Asks the provider for its model catalog and merges new entries.
   *
   * 向提供商请求模型目录并合并新条目。
   *
   * @returns Discovered models response, or `undefined` when the request failed. / 发现到的模型响应；请求失败时返回 `undefined`。
   */
  async function discover() {
    if (busy.value) return
    lastOperation.value = 'discover'
    const issue = providerIssue(form, savedKey.value, false)
    if (issue) { error.value = issueMessage(issue); return }
    discovering.value = true; error.value = ''; notice.value = ''; discoverManual.value = false
    try {
      const result = await workRequest<{ items: ProviderModel[] }>('/settings/providers/discover', 'POST', providerPayload(form, kind.value))
      const existing = new Map(form.models.map(item => [item.id, item]))
      const available = new Map(catalog.value.map(item => [item.id, item]))
      for (const item of result.items) { if (!existing.has(item.id)) existing.set(item.id, item); available.set(item.id, item) }
      form.models = [...existing.values()].slice(0, 200)
      catalog.value = [...available.values()].slice(0, 200)
      notice.value = result.items.length ? t(`Fetched ${result.items.length} models.`, `已获取 ${result.items.length} 个模型。`) : t('The provider returned an empty catalog. Add a model ID manually.', '服务商返回了空目录，请手动添加模型 ID。')
      return result
    } catch (reason) { discoverManual.value = reason instanceof ApiError && [501, 502].includes(reason.status); report(reason) }
    finally { discovering.value = false }
  }
  /**
   * Validates and appends the model currently typed into the add-model form.
   *
   * 校验并把「添加模型」表单中的模型追加到列表。
   *
   * @returns `true` when the model was added. / 模型添加成功时返回 `true`。
   */
  function addModel() {
    const model: ProviderModel = { id: newModel.id.trim(), name: newModel.name.trim() || newModel.id.trim(), context_window: newModel.context ? Number(newModel.context) : null }
    const issue = providerModelIssue([...form.models, model])
    if (issue) { error.value = issueMessage(issue); return false }
    form.models.push(model)
    const existing = catalog.value.findIndex(item => item.id === model.id)
    if (existing < 0) catalog.value.push(model)
    else catalog.value[existing] = model
    addModelOpen.value = false; error.value = ''
    return true
  }
  /**
   * Resets the add-model form and reveals it.
   *
   * 重置「添加模型」表单并显示它。
   */
  function showModelForm() { Object.assign(newModel, { id: '', name: '', context: '' }); addModelOpen.value = true; error.value = '' }
  onUnmounted(() => { form.api_key = ''; showKey.value = false })
  return { providers, templates, editing, editId, kind, saving, discovering, busy, error, notice, showKey, addModelOpen, catalog, lastOperation, discoverManual, newModel, form, savedKey, canSave, canDiscover,
    selectTemplate, changeKind, openEditor, closeEditor, save, discover, addModel, showModelForm }
}
