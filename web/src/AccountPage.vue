<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Camera, Pencil, X } from 'lucide-vue-next'
import { accountProfile, avatarUrl, loadAccount, saveAccount, setAccountAvatar } from './account'
import UserAvatar from './UserAvatar.vue'
import TokenActivityChart from './TokenActivityChart.vue'
import { formatTokenCount, type ActivityDay } from './tokenActivity'
import { settings, t } from './preferences'
import { workRequest } from './workApi'

interface AccountActivity {
  as_of: string
  daily: ActivityDay[]
  prior_tokens: number
  total_tokens: number | null
  peak_daily_tokens: number | null
  longest_model_seconds: number | null
  longest_streak: number
  current_streak: number
  turns: number
  chat_turns: number
  reasoning_effort: string | null
  reasoning_share: number | null
  distinct_tools: number
  tool_calls: number
  memory_tokens: number
  unreported_calls: number
}
const usage = ref<AccountActivity>()
const loading = ref(false)
const busy = ref(false)
const error = ref('')
const upload = ref<HTMLInputElement>()
const editDialog = ref<HTMLDialogElement>()
const displayName = ref('')
const handle = ref('')
const activityChart = ref<InstanceType<typeof TokenActivityChart>>()
const locale = computed(() => settings.language === 'zh-CN' ? 'zh-CN' : 'en-US')
/**
 * Format a number using the active locale, with an em dash for missing values.
 * 按当前语言格式化数字，缺省值显示破折号。
 *
 * @param value - Number to format, or null/undefined. / 待格式化的数字，可为 null 或 undefined。
 * @returns Localized number string or an em dash. / 本地化数字字符串或破折号。
 */
const number = (value: number | null | undefined) => value == null ? '—' : value.toLocaleString(locale.value)
// Shared token-count formatter used by the totals and the activity chart.
// 与活跃度图表共用的 token 数量格式化函数。
const compact = formatTokenCount
/**
 * Format a 0–1 ratio as a localized percentage.
 * 将 0–1 的比率格式化为本地化百分比。
 *
 * @param value - Ratio to format, or null/undefined. / 待格式化的比率，可为 null 或 undefined。
 * @returns Localized percentage string or an em dash. / 本地化百分比字符串或破折号。
 */
const percent = (value: number | null | undefined) => value == null ? '—'
  : new Intl.NumberFormat(locale.value, { style: 'percent', maximumFractionDigits: 1 }).format(value)
// Map the stored reasoning-effort id to its short display label.
// 将存储的思考强度标识映射为简短显示名。
const effortName = computed(() => ({
  none: 'Off', low: 'Low', high: 'High', max: 'Max',
})[usage.value?.reasoning_effort ?? ''] ?? '—')

/**
 * Render a duration in seconds as a short localized string.
 * 将秒数时长渲染为简短的本地化文本。
 *
 * @param seconds - Duration in seconds, or null/undefined. / 时长（秒），可为 null 或 undefined。
 * @returns Short duration text such as "3.2 s", "12 min" or "2 h 5 min". / 简短时长文本，如 "3.2 秒"、"12 分" 或 "2 小时 5 分"。
 */
function duration(seconds: number | null | undefined) {
  if (seconds == null) return '—'
  if (seconds < 60) return t(seconds.toFixed(1) + ' s', seconds.toFixed(1) + ' 秒')
  const minutes = Math.floor(seconds / 60)
  const hours = Math.floor(minutes / 60)
  return hours
    ? t(hours + ' h ' + minutes % 60 + ' min', hours + ' 小时 ' + minutes % 60 + ' 分')
    : t(minutes + ' min ' + Math.floor(seconds % 60) + ' s', minutes + ' 分 ' + Math.floor(seconds % 60) + ' 秒')
}

/** Load the local account profile and usage activity together. / 同时加载本地账户资料与用量活动数据。 */
async function load() {
  loading.value = true
  error.value = ''
  const results = await Promise.allSettled([
    loadAccount(),
    workRequest<AccountActivity>('/account/activity').then(value => { usage.value = value }),
  ])
  if (results.some(result => result.status === 'rejected')) {
    error.value = t('Could not load your account. Please retry.', '用户资料加载失败，请重试。')
  }
  loading.value = false
}
onMounted(load)

/** Prefill the edit dialog from the current account profile. / 使用当前账户资料预填编辑弹窗。 */
function edit() {
  displayName.value = accountProfile.value?.display_name ?? ''
  handle.value = accountProfile.value?.handle ?? ''
  editDialog.value?.showModal()
}

/**
 * Run a save action while tracking busy state and surfacing failures.
 * 在跟踪忙碌状态并展示失败信息的同时执行保存操作。
 *
 * @param action - Save callback to execute. / 要执行的保存回调。
 */
async function change(action: () => Promise<void>) {
  if (busy.value) return
  busy.value = true
  error.value = ''
  try {
    await action()
    editDialog.value?.close()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : t('Could not save changes.', '保存失败。')
  } finally {
    busy.value = false
  }
}

/** Validate the handle and save the edited profile fields. / 校验用户名并保存编辑后的资料字段。 */
function submitProfile() {
  const username = handle.value.trim().replace(/^@/, '')
  if (username && !/^[a-zA-Z0-9_.-]{1,32}$/.test(username)) {
    error.value = t('Use up to 32 letters, digits, dots, underscores or hyphens for your handle.', '用户名限 32 位，可使用英文字母、数字、点、下划线和连字符。')
    return
  }
  void change(() => saveAccount(displayName.value.trim() || null, username || null))
}

/**
 * Validate, center-crop and upload the selected avatar image.
 * 校验、居中裁剪并上传选中的头像图片。
 *
 * @param event - Change event from the hidden avatar file input. / 隐藏头像文件输入框的 change 事件。
 */
async function uploadAvatar(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  if (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type) || file.size > 10 * 1024 * 1024) {
    error.value = t('Choose a PNG, JPG or WebP image under 10 MiB.', '请选择 10 MiB 以内的 PNG、JPG 或 WebP 图片。')
    return
  }
  await change(async () => {
    const bitmap = await createImageBitmap(file)
    try {
      const canvas = document.createElement('canvas')
      canvas.width = canvas.height = 256
      const context = canvas.getContext('2d')
      if (!context) throw new Error(t('Could not process this image.', '无法处理这张图片。'))
      const size = Math.min(bitmap.width, bitmap.height)
      context.drawImage(bitmap, (bitmap.width - size) / 2, (bitmap.height - size) / 2, size, size, 0, 0, 256, 256)
      const image = await new Promise<Blob>((resolve, reject) => {
        canvas.toBlob(blob => blob ? resolve(blob) : reject(new Error(t('Image conversion failed.', '图片转换失败。'))), 'image/png')
      })
      await setAccountAvatar(image)
    } finally {
      bitmap.close()
    }
  })
}
</script>

<template>
  <main class="account-page" @scroll.passive="activityChart?.hideTooltip()">
    <header class="account-identity">
      <button class="account-avatar-upload" type="button" :disabled="busy || loading"
        :aria-label="t('Upload avatar', '上传头像')" :title="t('Upload avatar', '上传头像')" @click="upload?.click()">
        <UserAvatar /><span class="account-camera"><Camera aria-hidden="true" /></span>
      </button>
      <input ref="upload" type="file" accept="image/png,image/jpeg,image/webp" hidden @change="uploadAvatar">
      <h1>{{ accountProfile?.display_name || t('Local account', '本地账户') }}</h1>
      <p v-if="accountProfile?.handle" class="account-handle">@{{ accountProfile.handle }}</p>
      <button type="button" class="account-edit" :disabled="loading" @click="edit">
        <Pencil aria-hidden="true" />{{ t('Edit profile', '编辑资料') }}
      </button>
    </header>
    <p v-if="loading || busy" class="account-notice" role="status">{{ busy ? t('Saving…', '正在保存…') : t('Loading…', '正在加载…') }}</p>
    <p v-if="error" class="account-notice" role="alert">{{ error }} <button class="text-link" :disabled="loading" @click="load">{{ t('Retry', '重试') }}</button></p>

    <section class="account-totals" :aria-label="t('Usage overview', '用量概览')">
      <div><strong :title="number(usage?.total_tokens)">{{ usage?.unreported_calls && usage?.total_tokens != null ? '≥ ' : '' }}{{ compact(usage?.total_tokens) }}</strong><span>{{ t('Total tokens', '累计 Token 数') }}</span></div>
      <div><strong :title="number(usage?.peak_daily_tokens)">{{ compact(usage?.peak_daily_tokens) }}</strong><span>{{ t('Peak daily tokens', '每日 Token 峰值') }}</span></div>
      <div><strong>{{ duration(usage?.longest_model_seconds) }}</strong><span>{{ t('Longest model time', '最长模型耗时') }}</span></div>
      <div><strong>{{ usage ? t(usage.longest_streak + ' days', usage.longest_streak + ' 天') : '—' }}</strong><span>{{ t('Longest streak', '最长连续天数') }}</span></div>
      <div><strong>{{ usage ? t(usage.current_streak + ' days', usage.current_streak + ' 天') : '—' }}</strong><span>{{ t('Current streak', '当前连续天数') }}</span></div>
    </section>

    <TokenActivityChart ref="activityChart" :usage="usage" />

    <section class="account-insights">
      <h2>{{ t('Insights', '洞察') }}</h2>
      <dl>
        <dt>{{ t('Chat mode share', '聊天模式占比') }}</dt><dd>{{ usage?.turns ? percent(usage.chat_turns / usage.turns) : '—' }}</dd>
        <dt>{{ t('Most used thinking effort', '最常用的思考强度') }}</dt><dd>{{ effortName }}<template v-if="usage?.reasoning_share != null"> · {{ percent(usage.reasoning_share) }}</template></dd>
        <dt>{{ t('Tools explored', '已使用的工具种类') }}</dt><dd>{{ number(usage?.distinct_tools) }}</dd>
        <dt>{{ t('Tool executions', '工具执行次数') }}</dt><dd>{{ number(usage?.tool_calls) }}</dd>
      </dl>
    </section>

    <dialog ref="editDialog" class="modal account-edit-dialog">
      <form @submit.prevent="submitProfile">
        <header><h2>{{ t('Edit profile', '编辑资料') }}</h2><button type="button" class="quiet-button" :aria-label="t('Close', '关闭')" @click="editDialog?.close()"><X /></button></header>
        <label>{{ t('Display name', '显示名称') }}<input v-model="displayName" maxlength="60" autocomplete="nickname"></label>
        <label>{{ t('Handle', '用户名') }}<input v-model="handle" maxlength="33" autocomplete="username" placeholder="@username"></label>
        <p v-if="error" role="alert">{{ error }}</p>
        <footer>
          <button v-if="avatarUrl" type="button" class="quiet-button" :disabled="busy" @click="change(() => setAccountAvatar(null))">{{ t('Remove avatar', '移除头像') }}</button>
          <button type="submit" class="accent-button" :disabled="busy">{{ t('Save', '保存') }}</button>
        </footer>
      </form>
    </dialog>
  </main>
</template>
