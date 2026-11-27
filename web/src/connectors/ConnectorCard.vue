<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ChevronDown, FileText, Plug, Terminal } from 'lucide-vue-next'
import { t } from '../preferences'
import { workRequest } from '../workApi'
import { riskLabel } from '../toolResults'
import McpProbeResult from './McpProbeResult.vue'
import ToolDetails from './ToolDetails.vue'
import { connectionLabel, connectorError, transportLabel, type Connector, type ConnectorDetail,
  type ConnectionStatus, type McpResult } from './types'

const props = defineProps<{ item: Connector; busy: boolean; action?: string; error?: string; result?: McpResult }>()
const emit = defineEmits<{ toggle: []; edit: []; test: []; refresh: []; remove: [] }>()
const detailsOpen = ref(false)
const loadingDetail = ref(false)
const detailError = ref('')
const detail = ref<ConnectorDetail>()
const detailAttempt = ref(0)
const state = computed<ConnectionStatus>(() => {
  if (props.action && props.item.kind === 'mcp' && (props.action !== 'toggle' || !props.item.enabled)) return 'connecting'
  if (!props.item.enabled) return 'disabled'
  if (props.result) return props.result.ok ? 'connected' : 'failed'
  return props.item.connection_status ?? 'unchecked'
})
const references = computed(() => props.item.reference_count ?? Math.max(0, props.item.file_count - 1))
const summary = computed(() => {
  if (props.item.kind === 'skill') {
    const count = references.value
    return t(count === 1 ? '1 reference file' : `${count} reference files`, `${count} 个参考文件`)
  }
  if (props.item.kind === 'local') return riskLabel(props.item.risk, t)
  const count = props.item.tools.length
  return t(count === 1 ? '1 discovered tool' : `${count} discovered tools`, `已发现 ${count} 个工具`)
})

watch([detailsOpen, () => props.item.revision, () => props.item.tools.join(','), detailAttempt],
  async ([open], _, onCleanup) => {
    if (!open) return
    const controller = new AbortController()
    onCleanup(() => controller.abort())
    loadingDetail.value = true
    detailError.value = ''
    detail.value = undefined
    try {
      const result = await workRequest<ConnectorDetail>('/connectors/' + encodeURIComponent(props.item.id),
        'GET', undefined, { signal: controller.signal })
      if (!controller.signal.aborted) detail.value = result
    } catch (cause) {
      if (!controller.signal.aborted) detailError.value = connectorError(cause)
    } finally {
      if (!controller.signal.aborted) loadingDetail.value = false
    }
  })
</script>

<template>
  <article class="connector-card" :data-connector-id="item.id">
    <div class="connector-card-header">
      <component :is="item.kind === 'local' ? Terminal : item.kind === 'mcp' ? Plug : FileText" class="connector-card-icon" aria-hidden="true" />
      <div class="connector-card-name">
        <h3>{{ item.name }}</h3>
        <div class="connector-card-meta">
          <span v-if="item.builtin">{{ t('Built-in', '内置') }}</span>
          <span v-if="item.kind === 'mcp'">{{ transportLabel(item.transport) }}</span>
          <span>{{ summary }}</span>
          <span v-if="item.kind === 'mcp'" class="connector-state" :data-state="state"
            :title="state === 'connected' ? t('Last connection check succeeded. Tools connect on demand.', '最近一次连接检查成功。调用工具时按需连接。') : undefined"
            >{{ connectionLabel(state) }}</span>
          <span v-else class="connector-state" :data-state="item.enabled ? 'connected' : 'disabled'">{{ item.enabled ? t('Enabled', '已启用') : t('Disabled', '未启用') }}</span>
        </div>
      </div>
      <button type="button" class="settings-switch" role="switch" :aria-checked="item.enabled"
        :aria-label="t('Enable ', '启用 ') + item.name" :disabled="busy" @click="emit('toggle')"><span></span></button>
    </div>
    <p v-if="item.description" class="connector-card-description">{{ item.description }}</p>
    <p v-if="error" class="connector-feedback" data-result="error" role="alert">{{ error }}</p>
    <McpProbeResult v-if="result" :result="result" />
    <div v-if="!item.builtin" class="connector-card-actions">
      <button v-if="item.kind !== 'local'" type="button" class="quiet-button" :disabled="busy" @click="emit('edit')">{{ t('Edit', '编辑') }}</button>
      <template v-if="item.kind === 'mcp'">
        <button type="button" class="quiet-button" :disabled="busy" @click="emit('test')">{{ t('Test connection', '测试连接') }}</button>
        <button type="button" class="quiet-button" :disabled="busy" @click="emit('refresh')">{{ t('Refresh tools', '刷新工具列表') }}</button>
      </template>
      <button type="button" class="quiet-button connector-delete" :disabled="busy" :aria-label="t('Delete ', '删除 ') + item.name" @click="emit('remove')">{{ t('Delete', '删除') }}</button>
    </div>
    <details class="connector-details" @toggle="detailsOpen = ($event.currentTarget as HTMLDetailsElement).open">
      <summary><ChevronDown aria-hidden="true" />{{ item.kind === 'local' ? t('Permissions & parameters', '权限与参数') : t('View details', '查看详情') }}</summary>
      <div v-if="detailsOpen" class="connector-detail-body">
        <p v-if="loadingDetail" class="muted" role="status">{{ t('Loading details…', '正在读取详情…') }}</p>
        <div v-else-if="detailError" role="alert">
          <p>{{ detailError }}</p><button class="text-link" type="button" @click="detailAttempt++">{{ t('Retry', '重试') }}</button>
        </div>
        <template v-else-if="detail">
          <template v-if="detail.skill">
            <h4>SKILL.md</h4><pre>{{ detail.skill.markdown }}</pre>
            <ul v-if="detail.skill.references.length" class="connector-reference-list" :aria-label="t('Reference files', '参考文件')">
              <li v-for="file in detail.skill.references" :key="file"><code>{{ file }}</code></li>
            </ul>
          </template>
          <template v-else>
            <ul v-if="item.kind === 'local' && item.capabilities?.length" class="connector-permissions">
              <li v-for="capability in item.capabilities" :key="capability.name">
                {{ capability.modes.map(mode => mode === 'work' ? t('Work', '工作') : t('Chat', '聊天')).join(' / ') }} ·
                {{ capability.requires_approval ? t('Approval required', '需要审批') : t('Follows session approval policy', '遵循会话审批策略') }}
              </li>
            </ul>
            <ToolDetails v-for="tool in detail.tools" :key="tool.name" :tool="tool" />
            <p v-if="!detail.tools.length" class="muted">{{ t('No tool definitions saved.', '暂无已保存的工具定义。') }}</p>
          </template>
        </template>
      </div>
    </details>
  </article>
</template>
