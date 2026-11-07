<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { Check, ChevronDown, Eye, EyeOff, LoaderCircle, Plus, Trash2, X } from 'lucide-vue-next'
import { t } from './preferences'
import { brandAsset } from './brand'
import { workRequest } from './workApi'
import { loadProviderSettings, setupRequired, type ProviderConfig } from './providerSettings'
import { useProviderEditor } from './useProviderEditor'

const { providers, templates, editing, editId, kind, saving, discovering, error, notice, showKey,
  addModelOpen, newModel, form, savedKey, canSave, canDiscover, selectTemplate, changeKind,
  openEditor, closeEditor, save: saveProvider, discover, addModel, showModelForm } = useProviderEditor()
const loading = ref(true)
const customSettings = ref(true)
const deleteTarget = ref<ProviderConfig>()
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
        <div class="api-catalog-heading"><div><h2>{{ t('Model catalog', '模型目录') }}</h2><p v-if="!form.models.length">{{ t('Add or fetch the models you want to use.', '添加或获取你希望使用的模型。') }}</p></div><button type="button" :disabled="!canDiscover || discovering || saving" @click="discover"><LoaderCircle v-if="discovering" class="spin" />{{ t('Fetch available models', '获取可用模型') }}</button></div>
        <p v-if="!form.models.length" class="api-empty-catalog">{{ t('No models will appear in the model selector until you add a model ID.', '模型选择器中将不显示任何模型；请先获取或添加模型 ID。') }}</p>
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
