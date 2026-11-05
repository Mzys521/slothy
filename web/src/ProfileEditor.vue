<script setup lang="ts">
import { ref } from 'vue'
import { Plus, Trash2 } from 'lucide-vue-next'
import { t } from './preferences'
import { ApiError, workRequest } from './workApi'

type Profile = { version: number; preferences: Record<string, unknown> }
const props = defineProps<{ profile?: Profile }>()
const emit = defineEmits<{ saved: [] }>()
const dialog = ref<HTMLDialogElement>()
const rows = ref<{ id: string; key: string; value: string }[]>([])
const original = ref<Profile>({ version: 0, preferences: {} })
const busy = ref(false)
const error = ref('')
const conflict = ref(false)
const display = (value: unknown) => typeof value === 'string' ? value : JSON.stringify(value) ?? ''
function reset(profile: Profile) {
  original.value = profile
  rows.value = Object.entries(profile.preferences).map(([key, value]) => ({ id: crypto.randomUUID(), key, value: display(value) }))
  if (!rows.value.length) add()
  error.value = ''
  conflict.value = false
}
function add() { rows.value.push({ id: crypto.randomUUID(), key: '', value: '' }) }
function open() { reset(props.profile ?? { version: 0, preferences: {} }); dialog.value?.showModal() }
async function reload() {
  busy.value = true
  try { reset(await workRequest<Profile>('/profile')); emit('saved') }
  catch (reason) { error.value = reason instanceof Error ? reason.message : String(reason) }
  finally { busy.value = false }
}
async function save() {
  const entries = rows.value.filter(row => row.key.trim() || row.value.trim())
  if (entries.some(row => !row.key.trim() || !row.value.trim()) || new Set(entries.map(row => row.key.trim())).size !== entries.length) {
    error.value = t('Use unique names and fill both fields.', '请填写属性名称与内容，属性名称不能重复。')
    return
  }
  busy.value = true
  error.value = ''
  try {
    const preferences = Object.fromEntries(entries.map(row => [row.key.trim(),
      row.value === display(original.value.preferences[row.key.trim()]) ? original.value.preferences[row.key.trim()] : row.value.trim()]))
    await workRequest('/profile', 'PATCH', { expected_version: original.value.version, preferences, change_summary: t('Updated global preferences', '更新全局偏好') })
    dialog.value?.close()
    emit('saved')
  } catch (reason) {
    conflict.value = reason instanceof ApiError && reason.code === 'VERSION_CONFLICT'
    error.value = conflict.value ? t('The profile changed. Reload the latest version before editing again.', '画像已被更新，请载入最新版本后重新编辑。') : reason instanceof Error ? reason.message : String(reason)
  } finally { busy.value = false }
}
</script>

<template>
  <button class="outline-button" type="button" @click="open">{{ t('Edit global preferences', '编辑全局偏好') }}</button>
  <dialog ref="dialog" class="modal profile-editor" @cancel="busy && $event.preventDefault()">
    <form class="workspace-form" @submit.prevent="save">
      <h2>{{ t('Global preferences', '全局偏好') }}</h2>
      <p class="muted">{{ t('Applies across conversations. A matching name overrides a learned attribute.', '在所有会话中生效。同名属性以此处手动设置的值为准。') }}</p>
      <div v-for="row in rows" :key="row.id" class="preference-editor-row">
        <input v-model="row.key" :aria-label="t('Attribute name', '属性名称')" :placeholder="t('Attribute name', '属性名称')" maxlength="100">
        <input v-model="row.value" :aria-label="t('Attribute value', '属性内容')" :placeholder="t('Attribute value', '属性内容')" maxlength="2000">
        <button class="icon-button" type="button" :aria-label="t('Remove attribute', '移除属性')" @click="rows = rows.filter(item => item.id !== row.id)"><Trash2 /></button>
      </div>
      <button class="quiet-button" type="button" :disabled="rows.length >= 64" @click="add"><Plus />{{ t('Add attribute', '添加属性') }}</button>
      <p v-if="error" role="alert">{{ error }}</p>
      <button v-if="conflict" class="text-link" type="button" :disabled="busy" @click="reload">{{ t('Reload latest version', '载入最新版本') }}</button>
      <footer><button class="quiet-button" type="button" :disabled="busy" @click="dialog?.close()">{{ t('Cancel', '取消') }}</button><button class="accent-button" :disabled="busy || conflict">{{ t('Save', '保存') }}</button></footer>
    </form>
  </dialog>
</template>
