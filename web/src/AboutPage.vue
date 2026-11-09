<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { t } from './preferences'
import { brandAsset } from './brand'

const version = ref(__APP_VERSION__)
const isDesktop = !!window.desktop
const purgeOpen = ref(false)
const confirmation = ref('')
const purgeBusy = ref(false)
const purgeError = ref('')
const purgeResult = ref<{
  ok: boolean
  removed: string[]
  failed: { path: string; error: string }[]
  remaining: string[]
}>()
const canPurge = computed(() => confirmation.value.trim() === '确认删除' && !purgeBusy.value)
const purgeFinished = computed(() => purgeResult.value !== undefined)
const logsError = ref('')

/** Opens the application-managed log directory through the desktop bridge. / 通过桌面桥打开日志目录。 */
async function openLogs() {
  logsError.value = ''
  try { await window.desktop?.openLogs() }
  catch (reason) { logsError.value = reason instanceof Error ? reason.message : String(reason) }
}

// Desktop builds report the packaged app version; the web build keeps the build-time constant.
// 桌面端读取打包后的应用版本号，网页端沿用构建期常量。
onMounted(async () => { if (window.desktop) version.value = (await window.desktop.getAppInfo()).version })

/** Opens the high-risk confirmation dialog with a clean entry state. / 打开高风险确认弹窗并清空输入。 */
function openPurge() {
  purgeOpen.value = true
  confirmation.value = ''
  purgeBusy.value = false
  purgeError.value = ''
  purgeResult.value = undefined
}

/** Closes the dialog; blocked while deletion is running. / 关闭弹窗；删除执行中不可关闭。 */
function closePurge() {
  if (!purgeBusy.value) purgeOpen.value = false
}

/**
 * Executes the purge only after the exact local confirmation phrase is entered.
 *
 * 仅在输入准确确认口令后执行清除；失败时展示未清除项，不误报成功。
 */
async function purge() {
  if (!canPurge.value || !window.desktop?.purgeLocalData) return
  purgeBusy.value = true
  purgeError.value = ''
  try {
    purgeResult.value = await window.desktop.purgeLocalData(confirmation.value)
  } catch (reason) {
    purgeError.value = reason instanceof Error ? reason.message : String(reason)
  } finally {
    purgeBusy.value = false
  }
}

/** Relaunches the app after a successful purge. / 清除成功后重新启动应用。 */
async function restart() {
  if (purgeResult.value?.ok && window.desktop?.restartApp) await window.desktop.restartApp()
}
</script>

<template>
  <main class="about-page">
    <h1 class="settings-content-title">{{ t('About slothy', '关于 slothy') }}</h1>
    <div class="about-identity"><img :src="brandAsset('mark.png')" alt="slothy"><h2>slothy</h2><p>{{ t('Your personal, adaptive agent.', '理解你，陪伴你成长的智能体。') }}</p><span>{{ t('Version', '版本') }} {{ version }}</span></div>
    <dl><div><dt>{{ t('Platform', '平台') }}</dt><dd>{{ isDesktop ? 'Windows · x64' : t('Web', '网页') }}</dd></div><div><dt>{{ t('Data', '数据') }}</dt><dd>{{ t('Stored locally on your device', '保存在本机用户目录') }}</dd></div><div><dt>{{ t('API keys', 'API 密钥') }}</dt><dd>{{ t('Encrypted with your Windows account', '使用 Windows 账户保护加密') }}</dd></div></dl>

    <section v-if="isDesktop" class="about-danger">
      <h2>{{ t('Local data', '本地数据') }}</h2>
      <p>{{ t('Clear all conversations, memory, profiles, knowledge indexes, checkpoints, caches and local configuration created by slothy. This cannot be undone.', '清除 slothy 产生的全部会话、记忆、画像、知识索引、检查点、缓存与本地配置；此操作不可恢复。') }}</p>
      <div class="about-log-actions">
        <button type="button" class="quiet-button" @click="openLogs">{{ t('View logs', '查看日志') }}</button>
        <span v-if="logsError" role="alert">{{ logsError }}</span>
      </div>
      <button type="button" class="danger-button" @click="openPurge">{{ t('Clear all local data', '清除所有本地数据') }}</button>
    </section>

    <div v-if="purgeOpen" class="about-modal-backdrop" role="presentation" @click.self="closePurge">
      <section class="about-modal" role="dialog" aria-modal="true" :aria-label="t('Clear all local data', '清除所有本地数据')" @keydown.esc="closePurge">
        <h2>{{ t('Delete all local slothy data?', '删除所有本地 Slothy 数据？') }}</h2>
        <template v-if="!purgeFinished">
          <ul>
            <li>{{ t('Conversations, archived turns and attachments', '会话、归档轮次与附件') }}</li>
            <li>{{ t('Long-term memory, user profiles and knowledge indexes', '长期记忆、用户画像与知识索引') }}</li>
            <li>{{ t('Databases, checkpoints, WAL/SHM files, caches, logs and local configuration', '数据库、检查点、WAL/SHM 文件、缓存、日志与本地配置') }}</li>
            <li>{{ t('Provider keys, connector configuration and generated artifacts', '模型密钥、连接器配置与生成产物') }}</li>
          </ul>
          <p class="about-danger-note">{{ t('This action is irreversible. External files and workspace folders outside slothy data are not deleted.', '此操作不可恢复；不会删除 Slothy 数据目录之外的外部文件与工作区目录。') }}</p>
          <label class="about-confirm-field">{{ t('Type', '请输入') }} <strong>确认删除</strong> {{ t('to continue', '以继续') }}
            <input v-model="confirmation" type="text" autocomplete="off" spellcheck="false" :disabled="purgeBusy" @keydown.enter.prevent>
          </label>
          <p v-if="purgeError" role="alert">{{ purgeError }}</p>
          <div class="about-modal-actions">
            <button type="button" class="quiet-button" :disabled="purgeBusy" @click="closePurge">{{ t('Cancel', '取消') }}</button>
            <button type="button" class="danger-button" :disabled="!canPurge" @click="purge">{{ purgeBusy ? t('Clearing…', '正在清除…') : t('Delete everything', '确认删除') }}</button>
          </div>
        </template>
        <template v-else>
          <p v-if="purgeResult?.ok" role="status">{{ t('All managed local data was removed. Restart slothy to start fresh.', '已清除全部受管本地数据。请重启 Slothy 以全新状态启动。') }}</p>
          <p v-else role="alert">{{ t('Some data could not be removed.', '部分数据未能清除。') }}</p>
          <ul v-if="purgeResult?.failed.length">
            <li v-for="item in purgeResult.failed" :key="item.path"><code>{{ item.path }}</code> — {{ item.error }}</li>
          </ul>
          <ul v-if="purgeResult?.remaining.length">
            <li v-for="item in purgeResult.remaining" :key="item"><code>{{ item }}</code> — {{ t('still exists', '仍存在') }}</li>
          </ul>
          <div class="about-modal-actions">
            <button v-if="purgeResult?.ok" type="button" class="accent-button" @click="restart">{{ t('Restart slothy', '重启 Slothy') }}</button>
            <button type="button" class="quiet-button" @click="closePurge">{{ t('Close', '关闭') }}</button>
          </div>
        </template>
      </section>
    </div>
  </main>
</template>

<style scoped>
.about-danger { margin-top: 40px; padding-top: 22px; border-top: 1px solid var(--strong-line); }
.about-danger h2 { margin: 0 0 6px; font-size: var(--type-section); }
.about-danger p { max-width: 680px; color: var(--muted); line-height: 1.6; }
.danger-button { padding: 8px 14px; border: 1px solid #c23d40; border-radius: var(--radius); background: transparent; color: #c23d40; font: inherit; cursor: pointer; }
.danger-button:disabled { cursor: not-allowed; opacity: .55; }
.about-modal-backdrop { position: fixed; inset: 0; z-index: 40; display: grid; place-items: center; padding: 24px; background: rgb(0 0 0 / 45%); }
.about-modal { width: min(560px, 100%); max-height: 80dvh; overflow: auto; padding: 22px; border-radius: var(--radius-surface); background: var(--surface); color: var(--ink); box-shadow: 0 20px 60px rgb(0 0 0 / 28%); }
.about-modal h2 { margin-top: 0; font-size: var(--type-section); }
.about-modal ul { padding-left: 18px; line-height: 1.8; }
.about-danger-note { color: #c23d40; font-weight: 500; }
.about-confirm-field { display: grid; gap: 6px; margin: 14px 0; }
.about-confirm-field input { padding: 8px 10px; border: 1px solid var(--strong-line); border-radius: var(--radius); background: var(--canvas); color: var(--ink); font: inherit; }
.about-modal-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 16px; }
.about-log-actions { display: flex; align-items: center; gap: 10px; margin: 8px 0; }
.about-log-actions span { color: #c23d40; font-size: 12px; }
</style>
