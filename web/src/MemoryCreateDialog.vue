<script setup lang="ts">
import { ref } from 'vue'
import { activeWorkspace, personalWorkspaceId, workspaceName } from './chat'
import { t } from './preferences'
import { workRequest } from './workApi'

const emit = defineEmits<{ saved: [] }>()
const dialog = ref<HTMLDialogElement>()
const title = ref('')
const content = ref('')
const dimension = ref('preference')
const scope = ref('global')
const skillTarget = ref('python')
const protectedFact = ref(true)
const busy = ref(false)
const error = ref('')
/** Reset the draft fields and open the modal. / 重置草稿字段并打开弹窗。 */
function open() { title.value = ''; content.value = ''; scope.value = 'global'; error.value = ''; dialog.value?.showModal() }
/** Validate and persist the new memory, mapping the chosen category to an API memory type. / 校验并保存新记忆，将所选类别映射为接口的记忆类型。 */
async function save() {
  busy.value = true
  error.value = ''
  try {
    const profile = ['identity', 'skill', 'preference', 'habit', 'goal'].includes(dimension.value)
    await workRequest('/memories', 'POST', {
      title: title.value.trim(), content: content.value.trim(),
      type: dimension.value === 'episodic' ? 'episodic' : ['preference', 'habit'].includes(dimension.value) ? 'preference' : 'semantic',
      scope: scope.value, scope_ref: scope.value === 'project' ? activeWorkspace.value : null,
      ...(profile ? { profile_dimension: dimension.value } : {}), source: 'manual', protected: protectedFact.value,
      ...(dimension.value === 'identity' ? { value: content.value.trim(), profile_key: 'identity.name', scope: 'global', scope_ref: null } : {}),
      ...(dimension.value === 'skill' ? { value: content.value.trim(), profile_key: 'skills.' + skillTarget.value.trim().toLowerCase() + '.level' } : {}),
    })
    dialog.value?.close()
    emit('saved')
  } catch (reason) { error.value = reason instanceof Error ? reason.message : String(reason) }
  finally { busy.value = false }
}
</script>

<template>
  <button class="accent-button" type="button" @click="open">{{ t('Add memory', '添加记忆') }}</button>
  <dialog ref="dialog" class="modal" @cancel="busy && $event.preventDefault()">
    <form class="workspace-form" @submit.prevent="save">
      <h2>{{ t('Add memory', '添加记忆') }}</h2>
      <label for="new-memory-title">{{ t('Title / attribute name', '标题／属性名称') }}</label>
      <input id="new-memory-title" v-model="title" required maxlength="100">
      <label for="new-memory-value">{{ t('Content', '内容') }}</label>
      <textarea id="new-memory-value" v-model="content" required maxlength="800" rows="4"></textarea>
      <label for="new-memory-kind">{{ t('Category', '类别') }}</label>
      <select id="new-memory-kind" v-model="dimension"><option value="identity">{{ t('Name', '姓名') }}</option><option value="skill">{{ t('Skill proficiency', '技能熟练度') }}</option><option value="preference">{{ t('Preference', '偏好') }}</option><option value="habit">{{ t('Habit', '习惯') }}</option><option value="goal">{{ t('Long-term goal', '长期目标') }}</option><option value="fact">{{ t('Fact', '事实') }}</option><option value="episodic">{{ t('Episode', '情景事件') }}</option></select>
      <template v-if="dimension === 'skill'"><label for="new-memory-target">{{ t('Skill key', '技能标识') }}</label><input id="new-memory-target" v-model="skillTarget" required pattern="[a-z][a-z0-9_:-]*" placeholder="python"><p class="muted">{{ t('Use beginner, intermediate, advanced or expert as the value.', '内容填写：新手、中级、熟练或精通。') }}</p></template>
      <label class="check-line"><input v-model="protectedFact" type="checkbox">{{ t('Protect from capacity archiving', '不因容量压力自动归档') }}</label>
      <label for="new-memory-scope">{{ t('Scope', '生效范围') }}</label>
      <select id="new-memory-scope" v-model="scope" :disabled="dimension === 'identity'"><option value="global">{{ t('Across conversations', '跨会话') }}</option><option v-if="activeWorkspace !== personalWorkspaceId" value="project">{{ workspaceName(activeWorkspace) }}</option></select>
      <p class="muted">{{ t('Preferences, habits and goals also update your profile. The same attribute in the same scope replaces its earlier value.', '偏好、习惯和目标会同步更新画像。同一范围内的同名画像属性会替换旧值。') }}</p>
      <p v-if="error" role="alert">{{ error }}</p>
      <footer><button type="button" class="quiet-button" :disabled="busy" @click="dialog?.close()">{{ t('Cancel', '取消') }}</button><button class="accent-button" :disabled="busy || !title.trim() || !content.trim()">{{ t('Save memory', '保存记忆') }}</button></footer>
    </form>
  </dialog>
</template>
