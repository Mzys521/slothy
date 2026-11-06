import { computed, onUnmounted, reactive, ref } from 'vue'
import { t } from './preferences'
import { ApiError, workRequest } from './workApi'
import { loadProviderSettings, providerConfigs, providerTemplates, type ProviderConfig, type ProviderModel } from './providerSettings'
import { providerIssue, providerModelIssue, providerPayload, type ProviderDraft, type ProviderIssue } from './providerForm'

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
  function changeKind(value: 'builtin' | 'custom') {
    if (kind.value === value || editId.value) return
    kind.value = value; form.api_key = ''; showKey.value = false
    if (value === 'builtin') selectTemplate()
    else { Object.assign(form, { id: '', name: '', protocol: 'openai', base_url: '', models: [] }); catalog.value = [] }
  }
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
  function closeEditor() {
    if (!busy.value) { editing.value = false; form.api_key = ''; showKey.value = false; error.value = '' }
  }
  async function save() {
    if (busy.value) return
    lastOperation.value = 'save'
    const issue = providerIssue(form, savedKey.value)
    if (issue) { error.value = issueMessage(issue); return }
    saving.value = true; error.value = ''; notice.value = ''
    try {
      await workRequest('/settings/providers' + (editId.value ? '/' + encodeURIComponent(editId.value) : ''), editId.value ? 'PUT' : 'POST', providerPayload(form, kind.value))
      // Erase the plaintext as soon as the local backend has accepted it, even if reload fails.
      form.api_key = ''; showKey.value = false
      editId.value = form.id.trim(); committedKey.value = editId.value
      const result = await loadProviderSettings({ refresh: true })
      return result
    } catch (reason) { report(reason) }
    finally { saving.value = false }
  }
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
  function showModelForm() { Object.assign(newModel, { id: '', name: '', context: '' }); addModelOpen.value = true; error.value = '' }
  onUnmounted(() => { form.api_key = ''; showKey.value = false })
  return { providers, templates, editing, editId, kind, saving, discovering, busy, error, notice, showKey, addModelOpen, catalog, lastOperation, discoverManual, newModel, form, savedKey, canSave, canDiscover,
    selectTemplate, changeKind, openEditor, closeEditor, save, discover, addModel, showModelForm }
}
