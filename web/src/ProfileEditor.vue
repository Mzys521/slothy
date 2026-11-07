<script setup lang="ts">
import { ref } from 'vue'
import { Plus, Trash2 } from 'lucide-vue-next'
import { t } from './preferences'
import { ApiError, workRequest } from './workApi'

type ProfileAttribute = { key: string; value: unknown; dimension: string; scope: 'global' | 'project' | 'thread'; scope_ref?: string | null; memory_id?: string }
type Profile = { version: number; attributes?: ProfileAttribute[] }
const props = defineProps<{ profile?: Profile }>()
const emit = defineEmits<{ saved: [] }>()
const dialog = ref<HTMLDialogElement>()
const rows = ref<(Omit<ProfileAttribute, 'value'> & { id: string; value: string; originalValue?: unknown })[]>([])
const original = ref<Profile>({ version: 0, attributes: [] })
const busy = ref(false)
const error = ref('')
const conflict = ref(false)
/**
 * Renders an arbitrary stored preference value as editable text.
 *
 * 将任意已保存的偏好值渲染为可编辑文本。
 *
 * @param value - Preference value from the server. / 服务端返回的偏好值。
 * @returns String form of the value. / 值的字符串形式。
 */
const display = (value: unknown) => typeof value === 'string' ? value : JSON.stringify(value) ?? ''
/**
 * Replaces the editable rows with the values from a profile snapshot.
 *
 * 用某个画像快照中的值替换可编辑行。
 *
 * @param profile - Profile version to edit. / 要编辑的画像版本。
 * @returns Nothing. / 无返回值。
 */
function reset(profile: Profile) {
  original.value = profile
  rows.value = (profile.attributes ?? []).map(attribute => ({ ...attribute, id: crypto.randomUUID(), value: display(attribute.value), originalValue: attribute.value }))
  if (!rows.value.length) add()
  error.value = ''
  conflict.value = false
}
/** Appends an empty preference row. / 追加一行空白偏好。 */
function add() { rows.value.push({ id: crypto.randomUUID(), key: '', value: '', dimension: 'preference', scope: 'global', scope_ref: null }) }
/** Opens the dialog with the currently loaded profile. / 使用当前已加载的画像打开对话框。 */
function open() { reset(props.profile ?? { version: 0, attributes: [] }); dialog.value?.showModal() }
/** Reloads the latest profile after a version conflict. / 版本冲突后重新加载最新画像。 */
async function reload() {
  busy.value = true
  try { reset(await workRequest<Profile>('/profile')); emit('saved') }
  catch (reason) { error.value = reason instanceof Error ? reason.message : String(reason) }
  finally { busy.value = false }
}
/** Validates and saves the edited preferences using the original version. / 使用原始版本校验并保存编辑后的偏好。 */
async function save() {
  const entries = rows.value.filter(row => row.key.trim() || row.value.trim())
  if (entries.some(row => !row.key.trim() || !row.value.trim()) || new Set(entries.map(row => [row.scope, row.scope_ref, row.key.trim()].join(':'))).size !== entries.length) {
    error.value = t('Use unique names and fill both fields.', '请填写属性名称与内容，属性名称不能重复。')
    return
  }
  busy.value = true
  error.value = ''
  try {
    const attributes = entries.map(row => {
      let value: unknown = row.value === display(row.originalValue) ? row.originalValue : row.value.trim()
      if (row.dimension !== 'identity' && value === row.value.trim()) {
        try { value = JSON.parse(row.value.trim()) } catch { /* plain text / 普通文本 */ }
      }
      return { key: row.key.trim(), dimension: row.dimension, value,
        scope: row.dimension === 'identity' ? 'global' : row.scope,
        scope_ref: row.dimension === 'identity' ? null : row.scope_ref ?? null }
    })
    await workRequest('/profile', 'PATCH', { expected_version: original.value.version, attributes,
      change_summary: t('Updated profile facts', '更新画像事实') })
    dialog.value?.close()
    emit('saved')
  } catch (reason) {
    conflict.value = reason instanceof ApiError && reason.code === 'VERSION_CONFLICT'
    error.value = conflict.value ? t('The profile changed. Reload the latest version before editing again.', '画像已被更新，请载入最新版本后重新编辑。') : reason instanceof Error ? reason.message : String(reason)
  } finally { busy.value = false }
}
</script>

<template>
  <button class="outline-button" type="button" @click="open">{{ t('Edit profile', '编辑画像') }}</button>
  <dialog ref="dialog" class="modal profile-editor" @cancel="busy && $event.preventDefault()">
    <form class="workspace-form" @submit.prevent="save">
      <h2>{{ t('Profile attributes', '画像属性') }}</h2>
      <p class="muted">{{ t('Edits update the same evidence-backed facts used by learning. Identity uses identity.name; skill levels use skills.python.level.', '编辑会更新自动学习使用的同一份事实。姓名使用 identity.name，技能程度使用 skills.python.level。') }}</p>
      <div v-for="row in rows" :key="row.id" class="preference-editor-row v3-profile-row">
        <select v-model="row.dimension" :aria-label="t('Dimension', '画像维度')"><option value="identity">{{ t('Identity', '身份') }}</option><option value="skill">{{ t('Skill', '技能') }}</option><option value="preference">{{ t('Preference', '偏好') }}</option><option value="habit">{{ t('Habit', '习惯') }}</option><option value="goal">{{ t('Goal', '目标') }}</option></select>
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

<style scoped>
.v3-profile-row { grid-template-columns: minmax(84px, .7fr) minmax(120px, 1fr) minmax(120px, 1.4fr) 32px; }
.v3-profile-row select { min-width: 0; }
@media (max-width: 600px) { .v3-profile-row { grid-template-columns: 1fr 1fr; } }
</style>
