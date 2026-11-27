<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { FolderOpen, Plus, X } from 'lucide-vue-next'
import { t } from '../preferences'
import { workRequest } from '../workApi'
import ConnectorDialog from './ConnectorDialog.vue'
import ConfirmConnection from './ConfirmConnection.vue'
import McpProbeResult from './McpProbeResult.vue'
import SecretFields from './SecretFields.vue'
import { connectorError, transportLabel, transports, type Connector, type ConnectorDetail,
  type McpConfig, type McpResult, type SecretRow, type Transport } from './types'

const props = defineProps<{ item?: Connector }>()
const emit = defineEmits<{ close: []; saved: [item: Connector] }>()
const form = ref<HTMLFormElement>()
const draft = reactive({
  name: props.item?.name ?? '',
  description: props.item?.description ?? '',
  enabled: props.item?.enabled ?? false,
  transport: 'stdio' as Transport,
  command: '',
  args: [] as string[],
  cwd: '',
  env: [] as SecretRow[],
  url: '',
  headers: [] as SecretRow[],
  timeout: '' as number | '',
})
const revision = ref<string | null>()
const loaded = ref(!props.item)
const busy = ref<'loading' | 'testing' | 'saving' | 'picking' | ''>('')
const confirm = ref<'test' | 'save'>()
const error = ref('')
const result = ref<McpResult>()
const canPickDirectory = typeof window.desktop?.selectDirectory === 'function'

function rows(values?: Record<string, string | null>) {
  return Object.keys(values ?? {}).map((key, id) => ({ key, id, value: null, savedKey: key }))
}

async function load() {
  if (!props.item) return
  busy.value = 'loading'
  error.value = ''
  try {
    const { item, mcp } = await workRequest<ConnectorDetail>('/connectors/' + encodeURIComponent(props.item.id))
    if (!mcp) throw new Error(t('MCP configuration is unavailable.', '暂时无法读取 MCP 配置。'))
    revision.value = item.revision
    Object.assign(draft, {
      name: item.name, description: item.description, enabled: item.enabled,
      transport: item.transport === 'http' ? 'streamable-http' : item.transport || 'stdio',
      command: mcp.command ?? '', args: mcp.args ?? [], cwd: mcp.cwd ?? '', env: rows(mcp.env),
      url: mcp.url ?? '', headers: rows(mcp.headers), timeout: mcp.timeout ?? '',
    })
    loaded.value = true
  } catch (cause) {
    error.value = connectorError(cause)
  } finally {
    busy.value = ''
  }
}
onMounted(load)

const isStdio = computed(() => draft.transport === 'stdio')
const nameError = computed(() => draft.name && !/^[a-z][a-z0-9_-]{0,47}$/i.test(draft.name.trim())
  ? t('Start with a letter. Use up to 48 letters, digits, hyphens or underscores.', '以英文字母开头，最多 48 个字母、数字、连字符或下划线。') : '')
const urlError = computed(() => {
  if (isStdio.value || !draft.url.trim()) return ''
  try {
    const url = new URL(draft.url)
    if (['http:', 'https:'].includes(url.protocol) && !url.username && !url.password) return ''
  } catch { /* Show validation below. */ }
  return t('Enter an HTTP(S) URL without embedded credentials.', '请输入不含内嵌凭据的 HTTP(S) 地址。')
})
const cwdError = computed(() => {
  if (!isStdio.value || !draft.cwd || /^(?:\/|[A-Za-z]:[\\/]|\\\\)/.test(draft.cwd)) return ''
  return t('Enter an absolute directory path.', '请输入工作目录的绝对路径。')
})
const rowsError = computed(() => {
  const headers = !isStdio.value
  const fields = headers ? draft.headers : draft.env
  const used = new Set<string>()
  for (const field of fields) {
    const key = field.key.trim()
    const lookup = headers ? key.toLowerCase() : key
    const saved = headers ? field.savedKey?.toLowerCase() : field.savedKey
    const valid = headers ? /^[!#$%&'*+.^_|~0-9A-Za-z\`-]+$/.test(key) : key && !key.includes('=')
    if (!valid || used.has(lookup))
      return t('Enter a valid, unique key on each row.', '请为每一行填写有效且唯一的键名。')
    if (field.value === null && lookup !== saved)
      return t('Enter a new value when changing a saved key.', '修改已保存的键名时，请重新填写值。')
    used.add(lookup)
  }
  return ''
})
const timeoutError = computed(() => !isStdio.value && draft.timeout !== '' &&
  (!Number.isInteger(draft.timeout) || draft.timeout < 1 || draft.timeout > 30)
  ? t('Use a whole number from 1 to 30.', '请输入 1 至 30 之间的整数。') : '')
const config = computed<McpConfig>(() => {
  const entries = (fields: SecretRow[]) => Object.fromEntries(fields.map(row => [row.key.trim(), row.value]))
  return isStdio.value
    ? { type: 'stdio', command: draft.command.trim(), args: draft.args, cwd: draft.cwd.trim() || null, env: entries(draft.env) }
    : { type: draft.transport, url: draft.url.trim(), headers: entries(draft.headers), timeout: draft.timeout || null }
})
watch(() => JSON.stringify(config.value), () => { result.value = undefined })

async function chooseDirectory() {
  if (busy.value || !window.desktop?.selectDirectory) return
  busy.value = 'picking'
  try {
    const directory = await window.desktop.selectDirectory()
    if (directory) draft.cwd = directory
  } catch (cause) {
    error.value = connectorError(cause)
  } finally {
    busy.value = ''
  }
}

function requestAction(action: 'test' | 'save') {
  if (busy.value || !loaded.value || !form.value?.reportValidity() ||
    nameError.value || urlError.value || cwdError.value || rowsError.value || timeoutError.value) return
  if (action === 'test' || draft.enabled) confirm.value = action
  else void perform(action, false)
}

function confirmed() {
  if (!confirm.value) return
  const action = confirm.value
  confirm.value = undefined
  void perform(action, true)
}

async function perform(action: 'test' | 'save', confirmed: boolean) {
  if (busy.value) return
  busy.value = action === 'test' ? 'testing' : 'saving'
  error.value = ''
  result.value = undefined
  try {
    if (action === 'test') {
      result.value = await workRequest<McpResult>('/connectors/mcp/test', 'POST',
        { config: config.value, confirmed, connector_id: props.item?.id, revision: revision.value })
      return
    }
    const path = props.item ? '/connectors/' + encodeURIComponent(props.item.id) + '/mcp' : '/connectors/mcp'
    emit('saved', await workRequest<Connector>(path, props.item ? 'PUT' : 'POST', {
      name: draft.name.trim(), description: draft.description.trim(), enabled: draft.enabled,
      config: config.value, revision: revision.value, confirmed,
    }))
  } catch (cause) {
    error.value = connectorError(cause)
  } finally {
    busy.value = ''
  }
}
</script>

<template>
  <ConnectorDialog :title="item ? t('Edit MCP', '编辑 MCP') : t('Add MCP', '添加 MCP')" :busy="!!busy" @close="emit('close')">
    <div v-if="!loaded" class="connector-confirm">
      <p v-if="busy" role="status">{{ t('Loading configuration…', '正在读取配置…') }}</p>
      <template v-else><p role="alert">{{ error }}</p><button type="button" class="outline-button" @click="load">{{ t('Retry', '重试') }}</button></template>
    </div>
    <form v-else ref="form" class="connector-form" @submit.prevent="requestAction('save')">
      <fieldset :disabled="!!busy" class="connector-fields">
        <div class="connector-form-pair">
          <label>
            {{ t('Server name', '服务名称') }}
            <input v-model.trim="draft.name" required maxlength="48" placeholder="my-server" pattern="[a-zA-Z][a-zA-Z0-9_\-]{0,47}"
              :aria-invalid="!!nameError" autocomplete="off" spellcheck="false">
            <small v-if="nameError" class="connector-field-error">{{ nameError }}</small>
          </label>
          <label>
            {{ t('Transport', '传输方式') }}
            <select v-model="draft.transport"><option v-for="transport in transports" :key="transport" :value="transport">{{ transportLabel(transport) }}</option></select>
          </label>
        </div>
        <label>
          {{ t('Description (optional)', '服务描述（选填）') }}
          <textarea v-model="draft.description" rows="2" maxlength="2000" />
        </label>
        <template v-if="isStdio">
          <label>
            {{ t('Command', '启动命令') }}
            <input v-model.trim="draft.command" required maxlength="2048" autocomplete="off" spellcheck="false">
          </label>
          <fieldset class="connector-field-list">
            <legend>{{ t('Arguments', '启动参数') }}</legend>
            <div v-for="(_, index) in draft.args" :key="index" class="connector-inline-field">
              <input v-model="draft.args[index]" :aria-label="t('Argument', '参数') + ' ' + (index + 1)" autocomplete="off" spellcheck="false">
              <button type="button" class="icon-button" :aria-label="t('Remove argument', '移除参数') + ' ' + (index + 1)" @click="draft.args.splice(index, 1)"><X aria-hidden="true" /></button>
            </div>
            <button type="button" class="quiet-button connector-field-add" :disabled="draft.args.length >= 64" @click="draft.args.push('')"><Plus aria-hidden="true" />{{ t('Add argument', '新增参数') }}</button>
          </fieldset>
          <div>
            <label for="mcp-cwd">{{ t('Working directory (optional)', '工作目录（选填）') }}</label>
            <div class="connector-inline-field">
              <input id="mcp-cwd" v-model.trim="draft.cwd" maxlength="2048" :aria-invalid="!!cwdError" autocomplete="off" spellcheck="false">
              <button v-if="canPickDirectory" type="button" class="icon-button" :aria-label="t('Choose working directory', '选择工作目录')" @click="chooseDirectory"><FolderOpen aria-hidden="true" /></button>
            </div>
            <small v-if="cwdError" class="connector-field-error">{{ cwdError }}</small>
          </div>
          <SecretFields v-model="draft.env" :title="t('Environment variables', '环境变量')" :limit="32" />
        </template>
        <template v-else>
          <div class="connector-form-pair connector-web-fields">
            <label>
              {{ t('Server URL', '服务地址') }}
              <input v-model.trim="draft.url" type="url" required maxlength="2048" :aria-invalid="!!urlError" autocomplete="off" spellcheck="false">
              <small v-if="urlError" class="connector-field-error">{{ urlError }}</small>
            </label>
            <label>
              {{ t('Timeout (seconds)', '超时（秒）') }}
              <input v-model.number="draft.timeout" type="number" min="1" max="30" step="1" :aria-invalid="!!timeoutError">
              <small v-if="timeoutError" class="connector-field-error">{{ timeoutError }}</small>
            </label>
          </div>
          <SecretFields v-model="draft.headers" :title="t('Request headers', '请求头')" :limit="16" />
        </template>
        <small v-if="rowsError" class="connector-field-error">{{ rowsError }}</small>
        <label class="check-line"><input v-model="draft.enabled" type="checkbox">{{ t('Enable after saving', '保存后启用') }}</label>
      </fieldset>
      <p v-if="error" class="connector-feedback" data-result="error" role="alert">{{ error }}</p>
      <McpProbeResult v-if="result" :result="result" />
      <footer class="connector-dialog-actions">
        <button type="button" class="quiet-button" :disabled="!!busy" @click="emit('close')">{{ t('Cancel', '取消') }}</button>
        <button type="button" class="outline-button connector-secondary" :disabled="!!busy" @click="requestAction('test')">{{ busy === 'testing' ? t('Connecting…', '连接中…') : t('Test connection', '测试连接') }}</button>
        <button type="submit" class="accent-button" :disabled="!!busy">{{ busy === 'saving' ? t('Saving…', '保存中…') : t('Save configuration', '保存配置') }}</button>
      </footer>
    </form>
    <ConfirmConnection v-if="confirm" :name="draft.name" :transport="draft.transport"
      :action="confirm === 'test' ? t('Test connection', '测试连接') : t('Save and enable', '保存并启用')"
      @close="confirm = undefined" @approve="confirmed" />
  </ConnectorDialog>
</template>
