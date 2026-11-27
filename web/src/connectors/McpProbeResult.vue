<script setup lang="ts">
import { t } from '../preferences'
import { connectorMessage, type McpResult } from './types'

defineProps<{ result: McpResult }>()
</script>

<template>
  <section class="connector-result" :data-result="result.ok ? 'ok' : 'error'" :role="result.ok ? 'status' : 'alert'">
    <div class="connector-result-head">
      <strong>{{ result.ok ? t('Connected', '连接成功') : t('Connection failed', '连接失败') }}</strong>
      <span v-if="result.ok">{{ t(result.tool_count === 1 ? '1 tool discovered' : `${result.tool_count} tools discovered`, `发现 ${result.tool_count} 个工具`) }}</span>
      <span v-else-if="result.http_status">HTTP {{ result.http_status }}</span>
    </div>
    <p v-if="result.response_status === 'tools_listed'">{{ t('MCP handshake complete. The server returned a tool list.', 'MCP 握手完成，服务已返回工具列表。') }}</p>
    <p v-else>{{ connectorMessage(result.code, result.message) }}</p>
    <ul v-if="result.tools.length" class="connector-probe-tools" :aria-label="t('Discovered tool names', '发现的工具名称')">
      <li v-for="tool in result.tools" :key="tool.name"><code>{{ tool.name }}</code></li>
    </ul>
  </section>
</template>
