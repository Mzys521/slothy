<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { ArrowRight, Check, ChevronLeft, Eye, EyeOff, Globe2, KeyRound, LockKeyhole, Plus, Settings2, X } from 'lucide-vue-next'
import { brandAsset } from './brand'
import { settings, t } from './preferences'
import { configurationLoaded, loadProviderSettings, setupRequired, type ProviderModel } from './providerSettings'
import { useProviderEditor } from './useProviderEditor'

const props = defineProps<{ open: boolean; desktop: boolean; preview: boolean }>()
const emit = defineEmits<{ complete: []; visibility: [visible: boolean] }>()
const { providers, templates, kind, form, editId, catalog, savedKey, showKey, busy, error, notice, lastOperation, discoverManual,
  newModel, addModelOpen, openEditor, selectTemplate, save, discover, addModel, showModelForm } = useProviderEditor()
type Step = 'welcome' | 'builtin' | 'custom' | 'success'
const step = ref<Step>('welcome')
const card = ref<HTMLElement>()
const connecting = ref(false)
const leaving = ref(false)
const cycleClosed = ref(false)
const activePath = ref<'builtin' | 'custom' | null>(null)
let cycleStarted = false
let disposed = false
let successTimer: ReturnType<typeof setTimeout> | undefined
let fadeTimer: ReturnType<typeof setTimeout> | undefined
/** Stays visible while connecting or animating the success state, even if the parent closes the prop. / 连接中或成功动画期间保持可见，即使父组件的 open 属性已关闭。 */
const visible = computed(() => !cycleClosed.value && (props.open || connecting.value || step.value === 'success'))
/** Busy whenever a provider save/discovery is running or the onboarding is connecting. / 保存/发现提供商或引导连接进行中时为忙碌状态。 */
const working = computed(() => busy.value || connecting.value)
const names = computed(() => templates.value.map(item => item.name).join(' · '))
const title = computed(() => step.value === 'welcome' ? 'Slothy · 思洛'
  : step.value === 'success' ? t('Connected', '已连接')
  : step.value === 'builtin' ? t('Choose your provider', '选择你的服务商') : t('Custom connection', '自定义连接'))
const subtitle = computed(() => step.value === 'welcome' ? t('Connect a model, and let Slothy get to know you.', '连接你的模型，让思洛慢慢读懂你。')
  : step.value === 'success' ? t('Your configuration is saved on this device. Opening your workspace…', '配置已保存在本机，正在打开工作台…')
  : step.value === 'builtin' ? t('Choose a service, add your key, and make yourself at home.', '选择服务商，填入密钥，即可开始。')
  : t('Use any compatible OpenAI or Anthropic API.', '连接兼容 OpenAI 或 Anthropic 协议的 API。'))

/** Moves keyboard focus to the current step's primary control. / 将键盘焦点移到当前步骤的主要控件。 */
function focusStep() {
  void nextTick(() => {
    if (!visible.value) return
    const target = card.value?.querySelector<HTMLElement>('[data-onboarding-focus]')
    ;(target ?? card.value)?.focus()
  })
}
// Report visibility changes to the shell and focus the new step when it becomes visible. / 向壳层报告可见性变化，并在可见时聚焦新步骤。
watch(visible, value => { emit('visibility', value); if (value) focusStep() }, { immediate: true })
watch(step, focusStep)
/** Resets every onboarding step to the welcome state. / 将所有引导步骤重置为欢迎状态。 */
function startCycle() {
  clearTimeout(successTimer); clearTimeout(fadeTimer)
  cycleStarted = true; cycleClosed.value = false; leaving.value = false
  activePath.value = null; step.value = 'welcome'; openEditor()
}
// Start or resume the flow whenever the parent opens the overlay. / 父组件打开引导层时开始或继续流程。
watch(() => props.open, open => {
  if (!open) return
  if (!cycleStarted || cycleClosed.value) startCycle()
  focusStep()
}, { immediate: true })

/**
 * Selects a built-in provider template, reusing an existing connection when present.
 *
 * 选择内置提供商模板；已有连接时复用该连接。
 *
 * @param id - Provider template id. / 提供商模板 ID。
 * @returns Nothing. / 无返回值。
 */
function chooseTemplate(id: string) {
  if (working.value) return
  const existing = providers.value.find(item => item.template === id)
  openEditor(existing)
  kind.value = 'builtin'; form.template = id; form.enabled = true
  if (!existing) {
    selectTemplate()
    if (providers.value.some(item => item.id === form.id)) form.id = id + '-' + crypto.randomUUID().slice(0, 8)
  } else {
    const defaults = templates.value.find(item => item.id === id)?.model_ids ?? []
    const available = new Map(catalog.value.map(item => [item.id, item]))
    for (const model of defaults) if (!available.has(model)) available.set(model, { id: model, name: model })
    catalog.value = [...available.values()]
    if (!form.models.length) form.models = catalog.value.map(item => ({ ...item }))
  }
  addModelOpen.value = false
}
/**
 * Switches between the built-in template path and the custom-connection path.
 *
 * 在内置模板路径与自定义连接路径之间切换。
 *
 * @param path - Which onboarding path to enter. / 要进入的引导路径。
 * @returns Nothing. / 无返回值。
 */
function choosePath(path: 'builtin' | 'custom') {
  if (working.value) return
  if (path !== activePath.value) {
    if (path === 'builtin') chooseTemplate(templates.value[0]?.id ?? '')
    else {
      openEditor(); kind.value = 'custom'; editId.value = ''
      Object.assign(form, { id: 'custom-' + crypto.randomUUID().slice(0, 12), name: t('Custom connection', '自定义连接'), template: '', protocol: 'openai', base_url: '', api_key: '', enabled: true, models: [] })
      catalog.value = []; addModelOpen.value = false
    }
    activePath.value = path
  }
  step.value = path
}
/**
 * Adds or removes a model from the current provider form.
 *
 * 将模型加入当前提供商表单，或从中移除。
 *
 * @param model - Model catalog entry to toggle. / 要切换的模型目录项。
 * @returns Nothing. / 无返回值。
 */
function toggleModel(model: ProviderModel) {
  const index = form.models.findIndex(item => item.id === model.id)
  if (index >= 0) form.models.splice(index, 1)
  else form.models.push({ ...model })
}
/** Returns to the previous onboarding step or welcome screen. / 返回上一个引导步骤或欢迎页。 */
function back() {
  if (working.value || step.value === 'success') return
  if (addModelOpen.value) addModelOpen.value = false
  else step.value = 'welcome'
}
/**
 * Handles Escape to go back and keeps Tab focus cycling inside the dialog.
 *
 * 处理 Escape 返回上一步，并让 Tab 焦点在对话框内部循环。
 *
 * @param event - Keyboard event from the overlay. / 来自引导层的键盘事件。
 * @returns Nothing. / 无返回值。
 */
function keydown(event: KeyboardEvent) {
  if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); back(); return }
  if (event.key !== 'Tab') return
  const focusable = Array.from(card.value?.querySelectorAll<HTMLElement>('button:enabled,input:enabled,select:enabled,textarea:enabled,a[href],[tabindex="0"]') ?? [])
    .filter(node => node.getClientRects().length > 0)
  const first = focusable[0], last = focusable.at(-1)
  if (!first || !last) { event.preventDefault(); card.value?.focus(); return }
  if (event.shiftKey && (document.activeElement === first || document.activeElement === card.value)) { event.preventDefault(); last.focus() }
  else if (!event.shiftKey && (document.activeElement === last || !card.value?.contains(document.activeElement))) { event.preventDefault(); first.focus() }
}
/** Clears the entered key and animates into the success state. / 清空已输入的密钥并动画进入成功状态。 */
function finish() {
  if (step.value === 'success') return
  form.api_key = ''; showKey.value = false; step.value = 'success'
  successTimer = setTimeout(() => {
    leaving.value = true
    fadeTimer = setTimeout(() => {
      if (!configurationLoaded.value || setupRequired.value) {
        startCycle()
        return
      }
      cycleClosed.value = true; emit('complete')
    }, 200)
  }, 450)
}
/** Saves the provider form and finishes onboarding when the setup is usable. / 保存提供商表单，并在配置可用时完成引导。 */
async function connect() {
  if (working.value) return
  connecting.value = true
  try {
    const result = await save()
    if (disposed) return
    if (!result) return
    if (result.setup_required || setupRequired.value) {
      error.value = t('The saved provider is not usable yet. Check the key, enabled state and selected models.', '已保存，但提供商尚不可用。请检查密钥、启用状态与模型选择。')
      return
    }
    finish()
  } finally { connecting.value = false }
}
// Finish automatically once configuration loads and no setup is required; restart if it becomes unusable. / 配置加载完成且无需设置时自动完成；若配置变为不可用则重新开始。
watch([configurationLoaded, setupRequired], ([loaded, required]) => {
  if (!cycleStarted || connecting.value) return
  if ((!loaded || required) && step.value === 'success') {
    startCycle()
    return
  }
  if (!cycleClosed.value && loaded && !required && !props.preview) finish()
})
// Clear pending timers and the in-memory API key when the overlay unmounts. / 卸载引导层时清除待执行的定时器与内存中的 API 密钥。
onUnmounted(() => { disposed = true; clearTimeout(successTimer); clearTimeout(fadeTimer); form.api_key = ''; showKey.value = false })
</script>

<template>
  <Teleport to="body">
    <div v-if="visible" class="onboarding-overlay" :class="{ 'onboarding-desktop': desktop, 'onboarding-leaving': leaving }" data-testid="onboarding-overlay" @keydown="keydown">
      <section ref="card" class="onboarding-card" role="dialog" aria-modal="true" aria-labelledby="onboarding-title" aria-describedby="onboarding-description" tabindex="-1" :aria-busy="working">
        <header class="onboarding-header" :class="{ 'onboarding-welcome-header': step === 'welcome', 'onboarding-success-header': step === 'success' }">
          <label v-if="step === 'welcome'" class="onboarding-language"><span class="sr-only">{{ t('Language', '语言') }}</span><Globe2 aria-hidden="true" /><select v-model="settings.language" :aria-label="t('Welcome language', '欢迎页语言')"><option value="zh-CN" lang="zh-CN">简体中文</option><option value="en" lang="en">English</option></select></label>
          <button v-if="step !== 'welcome' && step !== 'success'" class="onboarding-back" type="button" :disabled="working" :aria-label="t('Back to welcome', '返回欢迎页')" @click="back"><ChevronLeft />{{ t('Back', '返回') }}</button>
          <div v-if="step === 'success'" class="onboarding-connected" data-onboarding-focus tabindex="-1"><Check aria-hidden="true" /></div>
          <img v-else :src="brandAsset('mark.png')" class="onboarding-mascot" alt="">
          <h1 id="onboarding-title">{{ title }}</h1>
          <p id="onboarding-description">{{ subtitle }}</p>
        </header>

        <div v-if="step === 'welcome'" class="onboarding-body onboarding-paths">
          <button type="button" class="onboarding-path" data-onboarding-focus :aria-label="t('Choose a provider · Recommended', '选择服务商 · 推荐')" @click="choosePath('builtin')">
            <span class="onboarding-path-icon"><Globe2 /></span><span class="onboarding-recommended">{{ t('Recommended', '推荐') }}</span>
            <strong>{{ t('Choose a provider', '选择服务商') }}</strong><span>{{ t('A familiar model, ready to connect.', '从熟悉的模型服务商开始。') }}</span>
            <small>{{ names || t('Use a built-in connection template', '使用内置连接模板') }}</small><ArrowRight class="onboarding-path-arrow" />
          </button>
          <button type="button" class="onboarding-path" :aria-label="t('Custom connection', '自定义连接')" @click="choosePath('custom')">
            <span class="onboarding-path-icon"><Settings2 /></span><strong>{{ t('Custom connection', '自定义连接') }}</strong><span>{{ t('Use your gateway or a self-hosted API.', '使用你的网关或自部署服务。') }}</span>
            <small>{{ t('OpenAI-compatible / Anthropic', 'OpenAI 兼容 / Anthropic') }}</small><ArrowRight class="onboarding-path-arrow" />
          </button>
        </div>

        <div v-else-if="step !== 'success'" class="onboarding-body">
          <form id="onboarding-provider-form" class="onboarding-form" @submit.prevent="connect">
            <fieldset :disabled="working">
              <legend class="sr-only">{{ t('Model provider connection', '模型服务连接') }}</legend>
              <template v-if="step === 'builtin'">
                <div class="onboarding-providers" role="group" :aria-label="t('Provider', '服务商')">
                  <button v-for="template in templates" :key="template.id" type="button" :aria-pressed="form.template === template.id" @click="chooseTemplate(template.id)"><span>{{ template.name.slice(0, 1) }}</span>{{ template.name }}<Check v-if="form.template === template.id" /></button>
                </div>
                <p v-if="!templates.length" class="onboarding-inline-error" role="alert">{{ t('No provider templates were returned. Retry or choose a custom connection.', '服务未返回模板，请重试或返回选择自定义连接。') }} <button class="text-link" type="button" @click="loadProviderSettings().catch(() => {})">{{ t('Retry', '重试') }}</button></p>
              </template>
              <template v-else>
                <label class="onboarding-field"><span>{{ t('API base URL', 'API 根地址') }}</span><input v-model="form.base_url" data-onboarding-focus type="url" required autocomplete="url" placeholder="https://api.example.com/v1"><small>{{ t('Use http(s); do not append /chat/completions.', '使用 http(s)，无需追加 /chat/completions。') }}</small></label>
                <div class="onboarding-field-pair"><label class="onboarding-field"><span>{{ t('Protocol', 'API 协议') }}</span><select v-model="form.protocol"><option value="openai">{{ t('OpenAI compatible', 'OpenAI 兼容') }}</option><option value="anthropic">Anthropic</option></select></label><label class="onboarding-field"><span>{{ t('Connection name', '连接名称') }}</span><input v-model="form.name" required maxlength="80" autocomplete="off"></label></div>
              </template>
              <label class="onboarding-field"><span><KeyRound aria-hidden="true" />{{ t('API key', 'API 密钥') }}</span><span class="onboarding-secret"><input v-model="form.api_key" :data-onboarding-focus="step === 'builtin' ? '' : undefined" :type="showKey ? 'text' : 'password'" :required="!savedKey" autocomplete="new-password" autocapitalize="off" spellcheck="false" :placeholder="savedKey ? t('Saved securely · leave blank to keep', '已安全保存 · 留空保留原密钥') : t('Paste your API key', '粘贴你的 API 密钥')"><button type="button" :aria-label="showKey ? t('Hide API key', '隐藏 API 密钥') : t('Show API key', '显示 API 密钥')" :aria-pressed="showKey" @click="showKey = !showKey"><EyeOff v-if="showKey" /><Eye v-else /></button></span><small v-if="step === 'builtin'">{{ form.base_url }}</small></label>

              <div class="onboarding-model-heading"><h2>{{ t('Models', '可用模型') }}<span>{{ form.models.length }} {{ t('selected', '个已选') }}</span></h2><button type="button" class="text-link" :disabled="!form.api_key.trim() && !savedKey" @click="discover">{{ t('Fetch models', '获取模型列表') }}</button></div>
              <p v-if="!catalog.length" class="onboarding-model-hint">{{ step === 'custom' ? t('Fetch the catalog or add a model ID to continue.', '获取目录或手动添加模型 ID 后即可继续。') : t('This template has no default models. Fetch the catalog or add a model ID below.', '暂无默认模型。可以获取目录，或手动添加模型 ID。') }}</p>
              <div v-else class="onboarding-models" :aria-label="t('Selected models', '模型选择')"><label v-for="model in catalog" :key="model.id"><input type="checkbox" :checked="form.models.some(item => item.id === model.id)" @change="toggleModel(model)"><span>{{ model.name || model.id }}<small v-if="model.name && model.name !== model.id">{{ model.id }}</small></span></label></div>
              <button v-if="!addModelOpen" class="onboarding-add-model" type="button" @click="showModelForm"><Plus />{{ t('Add a model manually', '手动添加模型') }}</button>
              <div v-else class="onboarding-model-editor">
                <label class="onboarding-field"><span>Model ID</span><input v-model="newModel.id" maxlength="128" placeholder="model-id" autocomplete="off" @keydown.enter.prevent="addModel"></label>
                <div class="onboarding-field-pair"><label class="onboarding-field"><span>{{ t('Display name (optional)', '显示名称（可选）') }}</span><input v-model="newModel.name" maxlength="128" autocomplete="off" @keydown.enter.prevent="addModel"></label><label class="onboarding-field"><span>{{ t('Context window (optional)', '上下文窗口（可选）') }}</span><input v-model="newModel.context" type="number" min="1024" max="10000000" step="1" inputmode="numeric" @keydown.enter.prevent="addModel"></label></div>
                <div class="onboarding-model-editor-actions"><button type="button" class="text-link" @click="addModelOpen = false">{{ t('Cancel', '取消') }}</button><button type="button" class="onboarding-add-confirm" @click="addModel">{{ t('Add model', '添加模型') }}</button></div>
              </div>
              <p v-if="error" class="onboarding-inline-error" role="alert">{{ error }}<span v-if="step === 'custom' && lastOperation === 'discover' && discoverManual"> {{ t('You can also add the model ID manually and retry.', '也可以手动添加模型 ID 后重试。') }}</span></p>
              <p v-if="notice" class="onboarding-notice" role="status">{{ notice }}</p>
            </fieldset>
          </form>
        </div>
        <div v-else class="onboarding-success-body" role="status"><span class="onboarding-ready-dot"></span>{{ t('Ready for your first conversation', '准备好开始第一段对话了') }}</div>

        <footer class="onboarding-footer">
          <p><LockKeyhole aria-hidden="true" />{{ t('Your key is stored on this device.', '密钥仅保存在这台设备。') }}<span>{{ t('Encrypted with Windows account protection.', '使用 Windows 账户保护加密。') }}</span></p>
          <button v-if="step !== 'welcome' && step !== 'success'" form="onboarding-provider-form" class="onboarding-connect" type="submit" :disabled="working" :aria-label="working ? t('Connecting', '正在连接') : t('Connect and open workspace', '连接并进入工作台')"><span v-if="working" class="onboarding-progress" aria-hidden="true"></span>{{ connecting ? t('Connecting…', '正在连接…') : busy ? t('Fetching…', '正在获取…') : t('Connect', '连接') }}<ArrowRight v-if="!working" aria-hidden="true" /></button>
        </footer>
      </section>
    </div>
  </Teleport>
</template>
