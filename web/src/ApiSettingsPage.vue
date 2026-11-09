<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Check, ChevronDown, Eye, EyeOff, LoaderCircle, Plus, Trash2, X } from 'lucide-vue-next'
import { t } from './preferences'
import { brandAsset } from './brand'
import { workRequest } from './workApi'
import { loadProviderSettings, setupRequired, type ProviderConfig } from './providerSettings'
import { useProviderEditor } from './useProviderEditor'

const { providers, templates, editing, editId, kind, saving, discovering, error, notice, showKey,
  addModelOpen, newModel, form, savedKey, canSave, canDiscover, selectTemplate, changeKind,
  openEditor, closeEditor, save: saveProvider, discover, addModel, showModelForm,
  selectedModels, modelQuery, modelTesting, modelResults, visibleModels, configuredIds, catalog,
  canTestModels, toggleModel, selectAllVisible, clearSelection, testAndAdd } = useProviderEditor()
const loading = ref(true)
const customSettings = ref(true)
const deleteTarget = ref<ProviderConfig>()
const decisionModel = ref<string | null>(null)
const decisionOptions = ref<string[]>([])
const decisionBusy = ref(false)
const decisionNotice = ref('')
const configuredDecisionModels = computed(() => providers.value.flatMap(provider =>
  provider.models.map(model => provider.id + '/' + model.id)))

/** Load the selected decision model. / 读取决策模型选择。 */
async function loadDecision() {
  try {
    const result = await workRequest<{ model: string | null; models: string[] }>('/settings/decision')
    decisionModel.value = result.model
    decisionOptions.value = result.models
  } catch { /* optional setting */ }
}

/** Persist the decision model; null keeps LLM self-driven mode. / 保存决策模型。 */
async function saveDecision() {
  decisionBusy.value = true; decisionNotice.value = ''
  try {
    const result = await workRequest<{ model: string | null; models: string[] }>(
      '/settings/decision', 'PUT', { model: decisionModel.value },
    )
    decisionModel.value = result.model
    decisionOptions.value = result.models
    decisionNotice.value = result.model
      ? t('Decision model saved. This model drives routing and the chat model handles fallback.', '决策模型已保存；由该模型驱动路由，失败时聊天模型自驱动兜底。')
      : t('Decision model cleared. The chat model decides autonomously.', '已清除决策模型；改由聊天模型自主决策。')
  } catch (reason) { decisionNotice.value = reason instanceof Error ? reason.message : String(reason) }
  finally { decisionBusy.value = false }
}
const searchKey = ref('')
const showSearchKey = ref(false)
const searchConfigured = ref(false)
const searchSource = ref<string | null>(null)
const searchIndependent = ref(false)
const searchBusy = ref(false)
const searchNotice = ref('')

/** Load Search credential status without exposing the key. / 读取搜索凭据状态。 */
async function loadSearch() {
  try {
    const status = await workRequest<{ configured: boolean; source: string | null; independent: boolean }>('/settings/search')
    searchConfigured.value = status.configured
    searchSource.value = status.source
    searchIndependent.value = status.independent
  } catch { /* Optional feature; do not break provider settings. / 可选功能，不阻断提供商设置。 */ }
}

/** Save the independent Search key and refresh tool availability. / 保存独立搜索密钥并刷新工具。 */
async function saveSearch() {
  if (!searchKey.value.trim()) return
  searchBusy.value = true; searchNotice.value = ''
  try {
    await workRequest('/settings/search', 'PUT', { api_key: searchKey.value })
    searchKey.value = ''
    searchNotice.value = t('Search key saved. The search tool is now available.', '搜索密钥已保存，搜索工具已启用。')
    await loadSearch()
    await loadProviderSettings({ refresh: true })
  } catch (reason) { searchNotice.value = reason instanceof Error ? reason.message : String(reason) }
  finally { searchBusy.value = false }
}

/** Test the typed or resolved Search key. / 测试输入或已解析的搜索密钥。 */
async function testSearch() {
  searchBusy.value = true; searchNotice.value = ''
  try {
    const result = await workRequest<{ ok: boolean; count?: number; error?: { code: string; message: string } }>(
      '/settings/search/test', 'POST', searchKey.value.trim() ? { api_key: searchKey.value } : {},
    )
    searchNotice.value = result.ok
      ? t(`Search connection works (${result.count ?? 0} result).`, `搜索连接正常（${result.count ?? 0} 条结果）。`)
      : `${result.error?.code ?? 'SEARCH_FAILED'}: ${result.error?.message ?? ''}`
  } catch (reason) { searchNotice.value = reason instanceof Error ? reason.message : String(reason) }
  finally { searchBusy.value = false }
}

/** Remove only the independent Search key. / 仅删除独立搜索密钥。 */
async function clearSearch() {
  searchBusy.value = true; searchNotice.value = ''
  try {
    await workRequest('/settings/search', 'DELETE')
    searchNotice.value = t('Independent Search key removed.', '独立搜索密钥已删除。')
    await loadSearch()
    await loadProviderSettings({ refresh: true })
  } catch (reason) { searchNotice.value = reason instanceof Error ? reason.message : String(reason) }
  finally { searchBusy.value = false }
}
/** Load the provider catalog and surface any failure through the shared error state. / 读取提供商目录，失败时写入共享错误状态。 */
async function load() {
  loading.value = true; error.value = ''
  try { await loadProviderSettings() }
  catch (reason) { error.value = reason instanceof Error ? reason.message : String(reason) }
  finally { loading.value = false }
}
/** Persist the edited provider and close the editor on success. / 保存正在编辑的提供商，成功后关闭编辑器。 */
async function save() {
  if (await saveProvider()) { editing.value = false; notice.value = t('Provider saved.', '提供商已保存。') }
}
/** Delete the provider selected for removal and refresh the catalog. / 删除待移除的提供商并刷新目录。 */
async function remove() {
  if (!deleteTarget.value) return
  saving.value = true; error.value = ''
  try {
    await workRequest('/settings/providers/' + encodeURIComponent(deleteTarget.value.id), 'DELETE')
    deleteTarget.value = undefined
    await loadProviderSettings({ refresh: true })
  } catch (reason) { error.value = reason instanceof Error ? reason.message : String(reason) }
  finally { saving.value = false }
}
onMounted(load)
onMounted(loadSearch)
onMounted(loadDecision)
</script>

<template>
  <main class="api-settings-page">
    <div class="api-settings-content">
      <header class="api-page-heading">
        <div v-if="setupRequired" class="setup-welcome"><img :src="brandAsset('mark.png')" alt=""><div><h1>{{ t('Welcome to slothy', '欢迎使用 slothy') }}</h1><p>{{ t('Connect your model provider to get started.', '配置你自己的 API 密钥，即可开始使用。') }}</p></div></div>
        <h1 v-if="!setupRequired">{{ t('API configuration', 'API 配置') }}</h1>
      </header>

      <template v-if="!editing">
        <h2 class="api-section-title">{{ t('Models', '模型') }}</h2>
        <p class="api-page-description">{{ t('Enter each provider’s API key to use its models.', '填入各提供商的 API 密钥即可使用其模型。') }}</p>
        <section class="search-settings">
          <h3>{{ t('DeepSeek Search', 'DeepSeek 搜索') }}</h3>
          <p class="api-page-description">{{ t('An independent Search API key powers real-time web search. If absent, the configured DeepSeek model key is reused. Without either key the search tool stays disabled.', '独立搜索密钥用于实时网页搜索；未配置时复用已配置的 DeepSeek 模型密钥；两者都没有时搜索工具保持禁用。') }}</p>
          <p class="api-status">
            <span class="provider-dot" :class="{ 'provider-dot-off': !searchConfigured }"></span>
            {{ searchConfigured ? (searchSource === 'independent' ? t('Configured with a Search key', '已使用独立搜索密钥') : t('Using the DeepSeek model key', '使用 DeepSeek 模型密钥')) : t('Search disabled: no key configured', '搜索已禁用：未配置密钥') }}
          </p>
          <label class="api-field"><span>{{ t('Search API key', '搜索 API 密钥') }}</span><div class="api-secret"><input v-model="searchKey" :type="showSearchKey ? 'text' : 'password'" autocomplete="new-password" :placeholder="searchConfigured ? t('Saved securely · enter a key to replace', '已安全保存 · 输入新密钥以替换') : t('Enter Search API key', '输入搜索 API 密钥')"><button type="button" :aria-label="showSearchKey ? t('Hide key', '隐藏密钥') : t('Show key', '显示密钥')" @click="showSearchKey = !showSearchKey"><component :is="showSearchKey ? EyeOff : Eye" /></button></div></label>
          <div class="search-actions">
            <button class="api-button" type="button" :disabled="searchBusy || !searchKey.trim()" @click="saveSearch">{{ t('Save', '保存') }}</button>
            <button class="api-button" type="button" :disabled="searchBusy" @click="testSearch">{{ t('Test connection', '测试连接') }}</button>
            <button v-if="searchIndependent" class="api-delete" type="button" :disabled="searchBusy" @click="clearSearch">{{ t('Delete key', '删除密钥') }}</button>
          </div>
          <p v-if="searchNotice" class="api-help">{{ searchNotice }}</p>
        </section>
        <section class="search-settings">
          <h3>{{ t('Decision model', '决策模型') }}</h3>
          <p class="api-page-description">{{ t('Choose a configured model to drive routing, tool selection and context plans. Leave empty to let the chat model decide autonomously; decision failures also fall back to the chat model.', '选择已配置模型来驱动路由、工具选择与上下文计划。留空则由聊天模型自主决策；决策失败同样回退到聊天模型。') }}</p>
          <label class="api-field"><span>{{ t('Decision model', '决策模型') }}</span>
            <select v-model="decisionModel">
              <option :value="null">{{ t('None — chat model self-driven', '无 — 聊天模型自驱动') }}</option>
              <option v-for="id in (decisionOptions.length ? decisionOptions : configuredDecisionModels)" :key="id" :value="id">{{ id }}</option>
            </select>
          </label>
          <div class="search-actions"><button class="api-button" type="button" :disabled="decisionBusy" @click="saveDecision">{{ decisionBusy ? t('Saving…', '正在保存…') : t('Save decision model', '保存决策模型') }}</button></div>
          <p v-if="decisionNotice" class="api-help">{{ decisionNotice }}</p>
        </section>
        <p v-if="loading" class="api-status"><LoaderCircle class="spin" />{{ t('Loading providers…', '正在读取提供商…') }}</p>
        <div v-else class="provider-list">
          <div v-for="provider in providers" :key="provider.id" class="provider-item">
            <span>{{ provider.name }}</span><span class="provider-dot" :class="{ 'provider-dot-off': !provider.enabled || !provider.has_api_key }" :aria-label="provider.enabled && provider.has_api_key ? t('Configured', '已配置') : t('Disabled', '已停用')"></span>
            <div class="provider-actions"><button class="api-button" type="button" @click="openEditor(provider)">{{ t('Edit', '编辑') }}</button><button class="api-delete" type="button" @click="deleteTarget = provider">{{ t('Delete', '删除') }}</button></div>
          </div>
          <button class="provider-add" type="button" @click="openEditor()"><Plus />{{ t('Add model provider', '添加模型提供商') }}</button>
        </div>
        <p v-if="setupRequired && !loading" class="api-help">{{ t('Your API key is stored only on this device. Add at least one model to continue.', '密钥仅保存在这台设备。请添加提供商及至少一个模型后继续。') }}</p>
        <div v-if="!setupRequired && providers.length" class="api-page-footer"><a href="#workspace" class="api-save">{{ t('Start chatting', '开始对话') }}<Check /></a></div>
      </template>

      <form v-else class="provider-editor" @submit.prevent="save">
        <div class="api-mode-switch" role="group" :aria-label="t('Provider type', '提供商类型')">
          <button type="button" :aria-pressed="kind === 'builtin'" :disabled="!!editId && kind !== 'builtin'" @click="changeKind('builtin')">{{ t('Third-party provider', '第三方模型提供商') }}</button>
          <button type="button" :aria-pressed="kind === 'custom'" :disabled="!!editId && kind !== 'custom'" @click="changeKind('custom')">{{ t('Custom model API', '自定义模型 API') }}</button>
        </div>
        <p class="api-page-description">{{ kind === 'builtin' ? t('Choose a provider such as OpenAI, Anthropic or Kimi, then enter its API key.', '从内置目录中选择 OpenAI、Anthropic、Kimi 等提供商，填入其 API 密钥即可使用。') : t('Connect a gateway, self-hosted service or compatible OpenAI / Anthropic API. Enter the address, protocol and model.', '连接中转站、自部署服务或其他兼容 OpenAI / Anthropic 协议的接口，需填写 API 地址、协议和模型。') }}</p>

        <label v-if="kind === 'builtin'" class="api-field api-provider-select"><span>{{ t('Provider', '提供商') }}</span><select v-model="form.template" :disabled="!!editId" @change="selectTemplate"><option v-for="template in templates" :key="template.id" :value="template.id">{{ template.name }}</option></select><ChevronDown /></label>
        <template v-else>
          <label class="api-field"><span>Provider ID</span><input v-model="form.id" placeholder="acme-gateway" pattern="[a-z][a-z0-9-]{0,47}" maxlength="48" required :disabled="!!editId"><small>{{ t('An identifier starting with a lowercase letter, unique across providers.', '以小写字母开头的标识，在请求中唯一标识该提供商，并用于派生凭据名。') }}</small></label>
          <label class="api-field"><span>{{ t('Display name', '显示名称') }}</span><input v-model="form.name" :placeholder="t('Display name', '显示名称')" required maxlength="80"></label>
          <label class="api-field"><span>{{ t('API address', 'API 地址') }}</span><input v-model="form.base_url" type="url" placeholder="https://gateway.example/v1" required></label>
          <label class="api-field api-provider-select"><span>{{ t('API protocol', 'API 协议') }}</span><select v-model="form.protocol"><option value="openai">OpenAI Chat Completions</option><option value="anthropic">Anthropic Messages</option></select><ChevronDown /></label>
        </template>
        <label class="api-field"><span>{{ t('API key', 'API 密钥') }}<span class="api-field-hint" :title="t('Keys are encrypted with Windows account protection and never returned by the API.', '密钥使用 Windows 账户保护加密，接口不会回传密钥。')">?</span></span><div class="api-secret"><input v-model="form.api_key" :type="showKey ? 'text' : 'password'" autocomplete="new-password" :placeholder="savedKey ? t('Saved securely · leave blank to keep', '已安全保存 · 留空保持原密钥') : t('Enter API key', '输入 API 密钥')" :required="!savedKey"><button type="button" :aria-label="showKey ? t('Hide key', '隐藏密钥') : t('Show key', '显示密钥')" @click="showKey = !showKey"><component :is="showKey ? EyeOff : Eye" /></button></div></label>
        <template v-if="kind === 'builtin'">
          <div class="api-editor-divider"></div>
          <button class="api-custom-toggle" type="button" :aria-expanded="customSettings" @click="customSettings = !customSettings"><ChevronDown :class="{ 'is-closed': !customSettings }" />{{ t('Custom settings', '自定义设置') }}</button>
          <label v-if="customSettings" class="api-field"><span>{{ t('API address', 'API 地址') }}</span><input v-model="form.base_url" type="url" required><small>{{ t('Use the provider’s API base address; do not append /chat/completions.', '填写提供商的 API 根地址，无需追加 /chat/completions。') }}</small></label>
        </template>
        <div class="api-editor-divider"></div>
        <div class="api-catalog-heading"><div><h2>{{ t('Model catalog', '模型目录') }}</h2><p v-if="!form.models.length">{{ t('Fetch the catalog, select models, and test them before adding.', '获取目录后勾选模型，测试通过才会加入已配置模型。') }}</p></div><button type="button" :disabled="!canDiscover || discovering || saving" @click="discover"><LoaderCircle v-if="discovering" class="spin" />{{ t('Fetch available models', '获取可用模型') }}</button></div>
        <div class="provider-model-picker">
          <input v-model="modelQuery" type="search" :placeholder="t('Search model ID or name', '搜索模型 ID 或名称')" :disabled="!catalog.length">
          <div class="model-picker-actions">
            <button type="button" class="api-button" :disabled="!visibleModels.length" @click="selectAllVisible">{{ t('Select all', '全选当前') }}</button>
            <button type="button" class="api-button" :disabled="!selectedModels.length" @click="clearSelection">{{ t('Clear selection', '清空选择') }}</button>
            <button type="button" class="api-save" :disabled="!canTestModels" @click="testAndAdd"><LoaderCircle v-if="modelTesting" class="spin" />{{ t('Test and add selected', '测试并添加所选') }}</button>
          </div>
          <ul class="model-options" v-if="visibleModels.length">
            <li v-for="item in visibleModels" :key="item.id">
              <label><input type="checkbox" :checked="selectedModels.includes(item.id)" :disabled="configuredIds.has(item.id) || modelTesting" @change="toggleModel(item.id)">
                <span><strong>{{ item.name || item.id }}</strong><small>{{ item.id }}</small></span></label>
              <em v-if="configuredIds.has(item.id)">{{ t('Added', '已添加') }}</em>
              <em v-else-if="modelResults[item.id]" :class="{ 'model-test-failed': !modelResults[item.id]!.ok }">
                {{ modelResults[item.id]!.ok ? t('Verified', '验证通过') : modelResults[item.id]!.message }} · {{ modelResults[item.id]!.duration_ms }} ms
              </em>
            </li>
          </ul>
          <p v-else class="api-help">{{ t('No catalog loaded. Fetch models or add an ID manually.', '暂无模型目录；请获取模型或手动添加 ID。') }}</p>
        </div>
        <h3 class="api-section-title">{{ t('Added models', '已添加模型') }}</h3>
        <p v-if="!form.models.length" class="api-empty-catalog">{{ t('No models are configured yet. Tests must pass before models appear here.', '尚未配置模型；只有测试通过的模型才会出现在这里。') }}</p>
        <div v-else class="api-model-list"><div v-for="(item, index) in form.models" :key="item.id"><span><strong>{{ item.name || item.id }}</strong><small v-if="item.name && item.name !== item.id">{{ item.id }}</small></span><button type="button" :aria-label="t('Remove model', '移除模型') + ' ' + item.id" @click="form.models.splice(index, 1)"><X /></button></div></div>
        <button type="button" class="api-button api-add-model" @click="showModelForm"><Plus />{{ t('Add model', '添加模型') }}</button>
        <div class="api-page-footer"><button class="api-button" type="button" :disabled="saving || discovering" @click="closeEditor">{{ t('Cancel', '取消') }}</button><button class="api-save" type="submit" :disabled="!canSave || saving || discovering"><LoaderCircle v-if="saving" class="spin" />{{ t('Save', '保存') }}</button></div>
      </form>
      <p v-if="error" class="api-error" role="alert">{{ error }}<button v-if="!editing" type="button" @click="load">{{ t('Retry', '重试') }}</button></p>
      <p v-if="notice" class="api-notice" role="status">{{ notice }}</p>
    </div>

    <div v-if="addModelOpen || deleteTarget" class="api-modal-backdrop" @click.self="!saving && (addModelOpen = false, deleteTarget = undefined)" @keydown.esc="!saving && (addModelOpen = false, deleteTarget = undefined)">
      <form v-if="addModelOpen" class="api-dialog" @submit.prevent="addModel"><h2>{{ t('Add model', '添加模型') }}</h2><label class="api-field"><span>Model ID</span><input v-model="newModel.id" required maxlength="128" pattern="[^\s]+" autofocus placeholder="model-id"></label><label class="api-field"><span>{{ t('Display name (optional)', '显示名称（可选）') }}</span><input v-model="newModel.name" maxlength="128"></label><label class="api-field"><span>{{ t('Context window (optional)', '上下文窗口（可选）') }}</span><input v-model="newModel.context" type="number" min="1024" max="10000000" placeholder="131072"></label><p v-if="form.models.some(item => item.id === newModel.id.trim())" class="api-error">{{ t('This model already exists.', '该模型已在目录中。') }}</p><footer><button class="api-button" type="button" @click="addModelOpen = false">{{ t('Cancel', '取消') }}</button><button class="api-save" type="submit">{{ t('Add', '添加') }}</button></footer></form>
      <section v-else-if="deleteTarget" class="api-dialog" role="alertdialog" aria-modal="true" aria-labelledby="delete-provider-title"><h2 id="delete-provider-title">{{ t('Delete provider?', '删除提供商？') }}</h2><p>{{ t(`Remove ${deleteTarget.name} and its saved API key? Existing conversations will be kept.`, `删除 ${deleteTarget.name} 及其已保存的 API 密钥？已有会话会保留。`) }}</p><footer><button class="api-button" type="button" :disabled="saving" @click="deleteTarget = undefined">{{ t('Cancel', '取消') }}</button><button class="api-save danger-action" type="button" :disabled="saving" @click="remove">{{ t('Delete', '删除') }}</button></footer></section>
    </div>
  </main>
</template>
