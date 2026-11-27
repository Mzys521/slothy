<script setup lang="ts">
import { t } from '../preferences'
import ConnectorDialog from './ConnectorDialog.vue'
import { transportLabel, type Connector } from './types'

defineProps<{ transport: Connector['transport']; name: string; action: string }>()
const emit = defineEmits<{ close: []; approve: [] }>()
</script>

<template>
  <ConnectorDialog compact :title="t('Confirm MCP connection', '确认连接 MCP')" @close="emit('close')">
    <div class="connector-confirm">
      <p><strong>{{ name }}</strong> <span class="muted">{{ transportLabel(transport) }}</span></p>
      <p v-if="transport === 'stdio'">{{ t('This will run the configured program under your operating system account to discover tools. Confirm that you trust this program.', '将以当前系统账户权限运行配置的程序来发现工具。请确认你信任该程序。') }}</p>
      <p v-else>{{ t('Slothy will contact the configured endpoint and send its request headers to discover tools.', 'Slothy 将访问配置的服务地址并发送请求头，以发现工具。') }}</p>
      <footer class="connector-dialog-actions">
        <button type="button" class="quiet-button" autofocus @click="emit('close')">{{ t('Cancel', '取消') }}</button>
        <button type="button" class="accent-button" @click="emit('approve')">{{ action }}</button>
      </footer>
    </div>
  </ConnectorDialog>
</template>
