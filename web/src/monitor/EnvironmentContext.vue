<script setup lang="ts">
import type { Session, Turn } from '../chat'
import { t } from '../preferences'
import CopyValue from './CopyValue.vue'

defineProps<{
  workspace: string
  directory: string
  session?: Session
  turn?: Turn
  status: string
  model: string
  memoryEnabled: boolean
}>()
</script>

<template>
  <div class="environment-view">
    <section class="monitor-card">
      <h3 class="monitor-caption">{{ t('Workspace', '工作区') }}</h3>
      <p class="environment-title">{{ workspace }}</p>
      <dl><div>
        <dt>{{ t('Working directory', '工作目录') }}</dt>
        <dd class="directory-value">
          <code v-if="directory">{{ directory }}</code><span v-else>—</span>
          <CopyValue v-if="directory" :value="directory" :label="t('Copy working directory', '复制工作目录')" />
        </dd>
      </div></dl>
    </section>

    <template v-if="session">
      <section class="monitor-card">
        <header>
          <h3 class="monitor-caption">{{ t('Session', '会话') }}</h3>
          <span class="monitor-badge" role="status"><span class="sr-only">{{ t('Status', '状态') }} </span>{{ status }}</span>
        </header>
        <p class="environment-title">{{ session.title || t('New session', '新建会话') }}</p>
        <dl>
          <div><dt>{{ t('Mode', '模式') }}</dt><dd>{{ session.mode === 'work' ? t('Work · Local Agent', '工作 · 本地 Agent') : t('Chat', '聊天') }}</dd></div>
          <div v-if="session.mode === 'work' && turn?.work">
            <dt>{{ t('Tool approval', '工具审批') }}</dt>
            <dd>{{ turn.work.approval === 'every' ? t('Every call', '逐项批准') : t('Risky actions', '危险操作需确认') }}</dd>
          </div>
        </dl>
      </section>
      <section class="monitor-card">
        <h3 class="environment-title">{{ t('Model & memory', '模型与记忆') }}</h3>
        <dl>
          <div><dt>{{ t('Model', '模型') }}</dt><dd>{{ model }}</dd></div>
          <div><dt>{{ t('Personalized memory', '定制记忆') }}</dt>
            <dd><span class="monitor-badge" :class="{ enabled: memoryEnabled }">{{ memoryEnabled ? t('On', '开启') : t('Off', '关闭') }}</span></dd>
          </div>
          <div v-if="turn?.profileVersion != null"><dt>{{ t('Profile version', '画像版本') }}</dt><dd>{{ turn.profileVersion }}</dd></div>
        </dl>
      </section>
    </template>
  </div>
</template>

<style scoped>
.environment-view { display: grid; gap: 10px; }
.monitor-card { overflow-wrap: anywhere; }
header, .directory-value { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.environment-title { color: var(--ink); font-size: var(--type-reading); font-weight: 600; line-height: 1.45; }
p.environment-title { margin-top: 4px; }
dl { display: grid; gap: 8px; margin: 12px 0 0; border-top: 1px solid var(--line); padding-top: 10px; }
dl > div { display: grid; grid-template-columns: minmax(0, 30%) minmax(0, 1fr); align-items: baseline; gap: 10px; }
dt { color: var(--muted); font-size: var(--type-sm); }
dd { min-width: 0; margin: 0; font-variant-numeric: tabular-nums; }
.directory-value code { min-width: 0; font-size: var(--type-body); }
.copy-value { flex: none; justify-content: center; min-width: 32px; min-height: 32px; }
.enabled { color: var(--accent-ink); background: var(--accent-wash); }
@container monitor (width < 360px) {
  dl > div { grid-template-columns: minmax(0, 1fr); gap: 2px; }
}
</style>
