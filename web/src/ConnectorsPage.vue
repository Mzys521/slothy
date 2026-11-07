<script setup lang="ts">
import { computed, ref, onMounted, nextTick, watch } from 'vue'
import { FileArchive, Plug, Terminal, Upload, Trash2 } from 'lucide-vue-next'
import { workRequest, uploadConnector } from './workApi'
import { t } from './preferences'
import { riskLabel } from './toolResults'
type Kind = 'local' | 'mcp' | 'skill'
interface Connector { id: string; kind: Kind; name: string; description: string; enabled: boolean; builtin: boolean; tools: string[]; transport: string | null; file_count: number; risk?: string; platforms?: string[]; available?: boolean; capabilities?: { name: string; risk: string; modes: string[]; requires_approval: boolean }[] }
const items = ref<Connector[]>([])
const kind = ref<Kind>('local')
const busy = ref('')
const loading = ref(false)
const error = ref('')
const notice = ref('')
const deleting = ref<Connector>()
const input = ref<HTMLInputElement>()
const dialog = ref<HTMLElement>()
// Move focus into the confirmation dialog as soon as it opens.
// 确认弹窗打开后立即将焦点移入其中。
watch(deleting, item => { if (item) void nextTick(() => dialog.value?.querySelector<HTMLButtonElement>('button')?.focus()) })
/**
 * Handle Escape and keep Tab focus trapped inside the removal dialog.
 * 处理 Escape，并将 Tab 焦点限制在移除确认弹窗内。
 *
 * @param event - Keyboard event from the dialog. / 弹窗上的键盘事件。
 */
function dialogKey(event: KeyboardEvent) {
  if (event.key === 'Escape' && !busy.value) { deleting.value = undefined; return }
  if (event.key !== 'Tab') return
  const buttons = dialog.value?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)')
  if (!buttons?.length) { event.preventDefault(); return }
  const first = buttons[0]!, last = buttons[buttons.length - 1]!
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
}
const categories = computed(() => [
  { id: 'local' as const, title: t('Local Tools', '本地工具'), icon: Terminal },
  { id: 'mcp' as const, title: 'MCP', icon: Plug },
  { id: 'skill' as const, title: 'Skills', icon: FileArchive },
])
const visible = computed(() => items.value.filter(item => item.kind === kind.value))
const description = computed(() => ({
  local: t('Run local tools in the current workspace. Each tool can be enabled individually.', '在当前工作目录调用本地工具，可单独启用或关闭。'),
  mcp: t('Connect MCP servers over stdio, Streamable HTTP or SSE.', '通过 stdio、Streamable HTTP 或 SSE 连接 MCP 服务。'),
  skill: t('Import a ZIP containing SKILL.md and its reference files.', '导入包含 SKILL.md 及参考文件的 ZIP 技能包。'),
})[kind.value])
const example = computed(() => kind.value === 'mcp' ? JSON.stringify({ mcpServers: { 'my-server': { command: 'npx', args: ['--no-install', 'your-installed-mcp-server'] } } }, null, 2) : JSON.stringify({ name: 'my_tool', description: 'Describe the operation', command: 'python', args: ['${plugin}/tool.py'], input_schema: { type: 'object', properties: { text: { type: 'string' } }, required: ['text'], additionalProperties: false } }, null, 2))
/**
 * Turn an unknown thrown value into a user-facing error message.
 * 将未知异常转换为面向用户的错误提示。
 *
 * @param cause - Value thrown by the failed request. / 请求失败时抛出的值。
 */
function fail(cause: unknown) { error.value = cause instanceof Error ? cause.message : t('The connector request failed.', '连接器请求失败。') }
/** Reload all connectors from the server. / 从服务端重新加载全部连接器。 */
async function load() {
  loading.value = true; error.value = ''
  try { items.value = (await workRequest<{ items: Connector[] }>('/connectors')).items }
  catch (cause) { fail(cause) }
  finally { loading.value = false }
}
onMounted(load)
/**
 * Validate and upload the file chosen in the import picker.
 * 校验并上传导入选择器选中的文件。
 *
 * @param event - Change event from the hidden file input. / 隐藏文件输入框的 change 事件。
 */
async function importFile(event: Event) {
  const field = event.target as HTMLInputElement
  const file = field.files?.[0]; field.value = ''
  if (!file) return
  const importedKind = kind.value
  error.value = ''; notice.value = ''; busy.value = 'import'
  try {
    if (file.size > 10 * 1024 * 1024) throw new Error(t('The import must be smaller than 10 MiB.', '导入文件不能超过 10 MiB。'))
    const result = await uploadConnector<{ items: Connector[] }>(importedKind, file)
    items.value.push(...result.items)
    notice.value = t('Imported. Enable it when you are ready.', '已导入，开启右侧开关后即可使用。')
  } catch (cause) { fail(cause) }
  finally { busy.value = '' }
}
/**
 * Enable or disable one connector and replace it with the server response.
 * 启用或停用单个连接器，并用服务端返回值替换本地记录。
 *
 * @param item - Connector row to toggle. / 待切换的连接器记录。
 */
async function toggle(item: Connector) {
  busy.value = item.id; error.value = ''; notice.value = ''
  try {
    const updated = await workRequest<Connector>('/connectors/' + encodeURIComponent(item.id), 'PATCH', { enabled: !item.enabled })
    items.value = items.value.map(row => row.id === item.id ? updated : row)
  } catch (cause) { fail(cause) }
  finally { busy.value = '' }
}
/** Remove the connector awaiting confirmation and drop it from the list. / 移除确认中的连接器并从列表中删除。 */
async function remove() {
  const item = deleting.value
  if (!item) return
  busy.value = item.id; error.value = ''
  try {
    await workRequest('/connectors/' + encodeURIComponent(item.id), 'DELETE')
    items.value = items.value.filter(row => row.id !== item.id); deleting.value = undefined
  } catch (cause) { fail(cause) }
  finally { busy.value = '' }
}
</script>
<template>
  <main class="connectors-page">
    <header class="connectors-heading"><div><h1>{{ t('Connectors', '连接器') }}</h1><p>{{ t('Give your Agent the tools and knowledge it needs.', '为 Agent 连接所需的工具与知识。') }}</p></div></header>
    <nav class="connector-tabs" :aria-label="t('Connector categories', '连接器分类')">
      <button v-for="category in categories" :key="category.id" type="button" :aria-pressed="kind === category.id" @click="kind = category.id; error = ''; notice = ''"><component :is="category.icon" />{{ category.title }}<span>{{ items.filter(item => item.kind === category.id).length }}</span></button>
    </nav>
    <section class="connector-section">
      <header><div><h2>{{ categories.find(category => category.id === kind)?.title }}</h2><p>{{ description }}</p></div><button class="accent-button" type="button" :disabled="!!busy || loading" @click="input?.click()"><Upload />{{ busy === 'import' ? t('Importing…', '正在导入…') : t('Import', '导入') }}</button></header>
      <input ref="input" type="file" :accept="kind === 'skill' ? '.zip' : kind === 'local' ? '.json,.zip' : '.json'" hidden @change="importFile">
      <p v-if="error" class="connector-error" role="alert">{{ error }} <button class="text-link" :disabled="loading" @click="load">{{ t('Reload', '重新加载') }}</button></p>
      <p v-if="notice" class="connector-notice" role="status">{{ notice }}</p>
      <p v-if="loading" class="connector-empty" role="status">{{ t('Loading…', '正在加载…') }}</p>
      <p v-else-if="!visible.length && !error" class="connector-empty">{{ t('Nothing imported yet.', '尚未导入。') }}</p>
      <div v-for="item in visible" :key="item.id" class="connector-row" :class="{ 'connector-off': !item.enabled }">
        <component :is="kind === 'local' ? Terminal : kind === 'mcp' ? Plug : FileArchive" class="connector-row-icon" />
        <div class="connector-row-info"><h3>{{ item.name }}<span v-if="item.builtin">{{ t('Built-in', '内置') }}</span><span v-else-if="item.transport">{{ item.transport }}</span></h3><p :title="item.description">{{ item.description || (kind === 'skill' ? t('Skill package', '技能包') + ' · ' + item.file_count + ' ' + t('files', '个文件') : item.tools.length ? item.tools.length + ' ' + t('tools', '个工具') : t('Connect when enabled', '启用时连接并获取工具')) }}</p>
          <p>{{ riskLabel(item.risk, t) }} · {{ item.available ? t('Available', '当前可用') : t('Disabled', '已停用') }}<span v-if="!item.builtin && item.kind !== 'skill'"> · {{ t('Work mode', 'Work 模式') }}</span></p>
          <details v-if="item.capabilities?.length"><summary>{{ t('Tool permissions', '工具权限') }}</summary><p v-for="capability in item.capabilities" :key="capability.name">{{ capability.name }} · {{ riskLabel(capability.risk, t) }} · {{ capability.requires_approval ? t('Approval required', '需要审批') : t('Read without approval', '读取免审') }}</p></details>
        </div>
        <button v-if="!item.builtin" class="icon-button connector-delete" type="button" :disabled="!!busy" :title="t('Remove', '移除')" :aria-label="t('Remove ', '移除 ') + item.name" @click="deleting = item"><Trash2 /></button>
        <button class="settings-switch" type="button" role="switch" :aria-checked="item.enabled" :aria-label="t('Enable ', '启用 ') + item.name" :disabled="!!busy || loading" @click="toggle(item)"><span></span></button>
      </div>
    </section>
    <details class="connector-help"><summary>{{ t('Import format & permissions', '导入格式与使用范围') }}</summary>
      <template v-if="kind === 'skill'"><p>{{ t('A ZIP must contain one SKILL.md, at the root or inside a folder. Reference files are available through read_skill_file; bundled scripts do not run on import.', 'ZIP 根目录或子目录中需有且仅有一个 SKILL.md。Agent 可通过 read_skill_file 读取参考文件，导入时不会执行包内脚本。') }}</p></template>
      <template v-else><p>{{ kind === 'local' ? t('Import a JSON manifest, or a ZIP with tool.json and its files. Programs receive JSON on stdin and return JSON on stdout. ${plugin} points to the bundle folder. An installed interpreter is required.', '导入 JSON 清单，或包含 tool.json 和程序文件的 ZIP。工具从标准输入接收 JSON，从标准输出返回 JSON；${plugin} 指向包目录，解释器需已安装。') : t('Import standard mcpServers JSON. Each server appears separately. Enabling starts the configured program or contacts its URL to discover tools. Disable and re-enable to refresh the tool list.', '导入标准 mcpServers JSON，每个服务单独显示。开启时将启动配置的程序或连接 URL 获取工具；关闭再开启可刷新工具列表。') }}</p><pre>{{ example }}</pre></template>
      <p>{{ t('Imports start disabled. Tool calls require approval. Local programs and MCP servers run with your operating system account; import only sources you trust. No dependencies are installed automatically.', '导入后默认关闭，调用导入的工具需经审批。本地程序与 MCP 服务使用当前系统账户权限，请仅导入可信来源；不会自动安装依赖。') }}</p>
    </details>
    <div v-if="deleting" class="api-modal-backdrop" @click.self="!busy && (deleting = undefined)" @keydown="dialogKey"><section ref="dialog" class="connector-remove-dialog" role="dialog" aria-modal="true" :aria-label="t('Remove connector', '移除连接器')"><h2>{{ t('Remove ', '移除 ') + deleting.name }}?</h2><p>{{ t('Its imported configuration and bundle will be removed. You can import the original file again.', '将移除其配置和导入的文件包，之后可用原文件重新导入。') }}</p><footer><button class="quiet-button" :disabled="!!busy" @click="deleting = undefined">{{ t('Cancel', '取消') }}</button><button class="accent-button" :disabled="!!busy" @click="remove">{{ t('Remove', '移除') }}</button></footer></section></div>
  </main>
</template>
