<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import './connectors/connectors.css'
import { ChevronDown, FileArchive, Plug, Plus, Terminal, Upload } from 'lucide-vue-next'
import { uploadConnector, workRequest } from './workApi'
import { t } from './preferences'
import ConnectorCard from './connectors/ConnectorCard.vue'
import ConnectorDialog from './connectors/ConnectorDialog.vue'
import ConfirmConnection from './connectors/ConfirmConnection.vue'
import McpEditor from './connectors/McpEditor.vue'
import SkillEditor from './connectors/SkillEditor.vue'
import { connectorError, type Connector, type ConnectorKind, type McpResult } from './connectors/types'

const items = ref<Connector[]>([])
const kind = ref<ConnectorKind>('local')
const loading = ref(false)
const failedLoad = ref(false)
const busy = ref<{ id?: string; action: 'import' | 'toggle' | 'delete' | 'test' | 'refresh' }>()
const error = ref('')
const errorOwner = ref<string>()
const notice = ref('')
const input = ref<HTMLInputElement>()
const editor = ref<{ kind: 'mcp' | 'skill'; item?: Connector }>()
const deletion = ref<Connector>()
const consent = ref<{ item: Connector; action: 'toggle' | 'test' | 'refresh' }>()
const probes = ref<Record<string, McpResult | undefined>>({})
const categories = [
  { id: 'local', label: ['Local Tools', '本地工具'], icon: Terminal },
  { id: 'mcp', label: ['MCP', 'MCP'], icon: Plug },
  { id: 'skill', label: ['Skills', 'Skills'], icon: FileArchive },
] as const
const category = computed(() => categories.find(item => item.id === kind.value)!)
const visible = computed(() => items.value.filter(item => item.kind === kind.value))
const description = computed(() => ({
  local: t('Manage the tools your Agent can use in the current workspace.', '管理 Agent 在当前工作区可使用的工具。'),
  mcp: t('Connect services over stdio, Streamable HTTP or SSE and discover the tools they provide.', '通过 stdio、Streamable HTTP 或 SSE 连接服务并发现可用工具。'),
  skill: t('Create reusable instructions and reference files for your Agent.', '为 Agent 创建可复用的工作指令，并附上需要的参考资料。'),
})[kind.value])
const emptyText = computed(() => ({
  local: t('Import a tool manifest or a ZIP to add your first local tool.', '导入工具清单或 ZIP 包，即可添加第一个本地工具。'),
  mcp: t('Add a server with the form or import an existing configuration.', '使用表单添加服务，或导入已有的 MCP 配置。'),
  skill: t('Create a Skill with its instructions and references, or import a ZIP.', '填写指令和参考资料来创建 Skill，也可直接导入 ZIP 技能包。'),
})[kind.value])
const importLabels = computed(() => ({
  local: t('Import local tool', '导入本地工具'),
  mcp: t('Import configuration', '导入配置'),
  skill: t('Import Skill', '导入 Skill'),
})[kind.value])
const example = computed(() => kind.value === 'mcp'
  ? JSON.stringify({ mcpServers: { 'my-server': { url: 'https://your-server.example/mcp', type: 'streamable-http' } } }, null, 2)
  : JSON.stringify({ name: 'my_tool', description: 'Describe the operation', command: 'python',
    args: ['${plugin}/tool.py'], input_schema: { type: 'object', properties: { text: { type: 'string' } }, required: ['text'] } }, null, 2))

function select(next: ConnectorKind) {
  kind.value = next
  if (!failedLoad.value) error.value = ''
  errorOwner.value = undefined
  notice.value = ''
}

async function load() {
  if (loading.value) return
  loading.value = true
  failedLoad.value = false
  error.value = ''
  errorOwner.value = undefined
  try {
    items.value = (await workRequest<{ items: Connector[] }>('/connectors')).items
  } catch (cause) {
    error.value = connectorError(cause)
    failedLoad.value = true
  } finally {
    loading.value = false
  }
}
onMounted(load)

function replace(item: Connector) {
  const index = items.value.findIndex(row => row.id === item.id)
  if (index < 0) items.value.push(item)
  else items.value[index] = item
}

function saveToList(item: Connector) {
  replace(item)
  delete probes.value[item.id]
  editor.value = undefined
  error.value = ''
  errorOwner.value = undefined
  notice.value = t('Configuration saved.', '配置已保存。')
}

async function run(action: NonNullable<typeof busy.value>, perform: () => Promise<void>) {
  if (busy.value) return
  busy.value = action
  error.value = ''
  errorOwner.value = action.id
  notice.value = ''
  try {
    await perform()
  } catch (cause) {
    error.value = connectorError(cause)
  } finally {
    busy.value = undefined
  }
}

function importFile(event: Event) {
  const field = event.target as HTMLInputElement
  const file = field.files?.[0]
  field.value = ''
  if (!file) return
  void run({ action: 'import' }, async () => {
    if (file.size > 10 * 1024 * 1024) throw new Error(t('Imports may be up to 10 MiB.', '导入文件不能超过 10 MiB。'))
    const result = await uploadConnector<{ items: Connector[] }>(kind.value, file)
    result.items.forEach(replace)
    notice.value = t('Imported. Use the switch to enable it.', '已导入，可通过开关启用。')
  })
}

function toggle(item: Connector) {
  if (item.kind === 'mcp' && !item.enabled) consent.value = { item, action: 'toggle' }
  else void switchConnector(item)
}

function switchConnector(item: Connector) {
  return run({ id: item.id, action: 'toggle' }, async () => {
    const updated = await workRequest<Connector>(connectorPath(item), 'PATCH',
      { enabled: !item.enabled, revision: item.revision })
    replace(updated)
    delete probes.value[item.id]
    notice.value = updated.enabled ? t('Connector enabled.', '已启用连接器。') : t('Connector disabled.', '已停用连接器。')
  })
}

function connectorPath(item: Connector) {
  return '/connectors/' + encodeURIComponent(item.id)
}

function connect() {
  const approval = consent.value
  if (!approval) return
  consent.value = undefined
  const { item, action } = approval
  if (action === 'toggle') {
    void switchConnector(item)
    return
  }
  void run({ id: item.id, action }, async () => {
    delete probes.value[item.id]
    const result = await workRequest<McpResult>(connectorPath(item) + '/' + action, 'POST',
      { confirmed: true, revision: item.revision })
    if (result.connector) replace(result.connector)
    probes.value[item.id] = result
  })
}

function remove() {
  const item = deletion.value
  if (!item) return
  void run({ id: item.id, action: 'delete' }, async () => {
    await workRequest(connectorPath(item), 'DELETE')
    items.value = items.value.filter(row => row.id !== item.id)
    delete probes.value[item.id]
    deletion.value = undefined
    notice.value = t('Connector deleted.', '已删除连接器。')
  })
}
</script>

<template>
  <main class="connectors-page">
    <div class="connector-layout">
      <header class="connectors-heading"><h1>{{ t('Connectors', '连接器') }}</h1></header>
      <nav class="connector-tabs" :aria-label="t('Connector categories', '连接器分类')">
        <button v-for="tab in categories" :key="tab.id" type="button" :disabled="!!busy" :aria-pressed="kind === tab.id" @click="select(tab.id)">
          <component :is="tab.icon" aria-hidden="true" />{{ t(tab.label[0], tab.label[1]) }}
        </button>
      </nav>

      <header class="connector-section-heading">
        <h2>{{ t(category.label[0], category.label[1]) }}</h2>
        <p>{{ description }}</p>
      </header>
      <div class="connector-add" role="group" :aria-label="t('Add connectors', '添加连接器')">
        <button v-if="kind === 'mcp'" class="accent-button" type="button" :disabled="!!busy || loading" @click="editor = { kind: 'mcp' }"><Plus aria-hidden="true" />{{ t('Add MCP', '添加 MCP') }}</button>
        <button v-if="kind === 'skill'" class="accent-button" type="button" :disabled="!!busy || loading" @click="editor = { kind: 'skill' }"><Plus aria-hidden="true" />{{ t('Create Skill', '创建 Skill') }}</button>
        <button class="outline-button connector-secondary" type="button" :disabled="!!busy || loading" @click="input?.click()"><Upload aria-hidden="true" />
          {{ busy?.action === 'import' ? t('Importing…', '正在导入…') : importLabels }}
        </button>
        <input ref="input" type="file" :accept="kind === 'skill' ? '.zip' : kind === 'local' ? '.json,.zip' : '.json'" hidden @change="importFile">
      </div>
      <section class="connector-inventory">
        <header><h3>{{ t('Configured', '已配置') }}</h3><span class="muted">{{ visible.length }}</span></header>
        <p v-if="error && !errorOwner && !deletion" class="connector-feedback" data-result="error" role="alert">{{ error }}
          <button v-if="failedLoad" class="text-link" type="button" :disabled="loading" @click="load">{{ t('Retry', '重试') }}</button>
        </p>
        <p v-if="notice" class="connector-feedback" role="status">{{ notice }}</p>
        <p v-if="loading" class="connector-empty" role="status">{{ t('Loading connectors…', '正在加载连接器…') }}</p>
        <div v-else-if="!visible.length && !failedLoad" class="connector-empty">
          <component :is="category.icon" aria-hidden="true" />
          <div><strong>{{ t('No connectors here yet', '还没有连接器') }}</strong><p>{{ emptyText }}</p></div>
        </div>
        <ConnectorCard v-for="item in visible" :key="item.id" :item="item" :busy="!!busy || loading"
          :action="busy?.id === item.id && busy.action !== 'delete' ? busy.action : undefined" :result="probes[item.id]"
          :error="errorOwner === item.id && !deletion ? error : undefined"
          @toggle="toggle(item)" @edit="editor = { kind: item.kind === 'skill' ? 'skill' : 'mcp', item }"
          @test="consent = { item, action: 'test' }" @refresh="consent = { item, action: 'refresh' }"
          @remove="deletion = item; error = ''; notice = ''" />
      </section>
      <details :key="kind" class="connector-help">
        <summary><ChevronDown aria-hidden="true" />{{ t('Formats, permissions & help', '导入格式、权限与帮助') }}</summary>
        <template v-if="kind === 'skill'">
          <p>{{ t('Use a ZIP containing exactly one SKILL.md. The Agent can read UTF-8 reference files with read_skill_file. Importing or saving a Skill does not run its scripts.', 'ZIP 中需有且仅有一个 SKILL.md。Agent 可通过 read_skill_file 读取 UTF-8 参考文件，导入和保存均不会执行包内脚本。') }}</p>
        </template>
        <template v-else>
          <p v-if="kind === 'local'">{{ t('Import a JSON manifest or a ZIP containing tool.json and its files. Programs use standard input and output for JSON. The interpreter and dependencies must be installed separately.', '支持 JSON 清单和含 tool.json 的 ZIP 包。程序通过标准输入和输出传递 JSON，解释器和依赖需提前安装。') }}</p>
          <p v-else>{{ t('Import a standard mcpServers JSON file. Stdio servers need a trusted, already installed program. Test connection performs discovery. Refresh tools also updates the stored tool definitions.', '支持标准 mcpServers JSON。stdio 需要已安装且可信的程序。「测试连接」会发现工具，「刷新工具列表」也会更新已保存的工具定义。') }}</p>
          <pre>{{ example }}</pre>
          <p>{{ t('Imported connectors start disabled. Agent calls to imported executable tools require approval.', '导入后默认关闭。Agent 调用导入的执行类工具需经审批。') }}</p>
        </template>
      </details>
    </div>

    <McpEditor v-if="editor?.kind === 'mcp'" :item="editor.item" @close="editor = undefined" @saved="saveToList" />
    <SkillEditor v-if="editor?.kind === 'skill'" :item="editor.item" @close="editor = undefined" @saved="saveToList" />
    <ConfirmConnection v-if="consent" :name="consent.item.name" :transport="consent.item.transport"
      :action="consent.action === 'toggle' ? t('Connect and enable', '连接并启用') : consent.action === 'test' ? t('Test connection', '测试连接') : t('Refresh tools', '刷新工具列表')"
      @close="consent = undefined" @approve="connect" />
    <ConnectorDialog v-if="deletion" compact :busy="!!busy" :title="t('Delete connector?', '删除连接器？')" @close="deletion = undefined">
      <div class="connector-confirm">
        <p>{{ t('The configuration and files for this connector will be removed.', '此连接器的配置和文件将被删除。') }}</p>
        <p><strong>{{ deletion.name }}</strong></p>
        <p v-if="error" class="connector-feedback" data-result="error" role="alert">{{ error }}</p>
        <footer class="connector-dialog-actions">
          <button type="button" class="quiet-button" autofocus :disabled="!!busy" @click="deletion = undefined">{{ t('Cancel', '取消') }}</button>
          <button type="button" class="outline-button connector-delete" :disabled="!!busy" @click="remove">{{ busy ? t('Deleting…', '删除中…') : t('Delete', '删除') }}</button>
        </footer>
      </div>
    </ConnectorDialog>
  </main>
</template>
