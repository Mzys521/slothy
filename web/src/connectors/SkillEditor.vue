<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { FilePlus2, X } from 'lucide-vue-next'
import { t } from '../preferences'
import { workRequest } from '../workApi'
import MarkdownText from '../MarkdownText.vue'
import ConnectorDialog from './ConnectorDialog.vue'
import { connectorError, type Connector, type ConnectorDetail } from './types'

const props = defineProps<{ item?: Connector }>()
const emit = defineEmits<{ close: []; saved: [item: Connector] }>()
const name = ref(props.item?.name ?? '')
const description = ref(props.item?.description ?? '')
const instructions = ref('')
const enabled = ref(props.item?.enabled ?? false)
const revision = ref<string | null>()
const references = ref<string[]>([])
const removed = ref(new Set<string>())
const uploads = ref<File[]>([])
const preview = ref(false)
const loaded = ref(!props.item)
const busy = ref<'loading' | 'saving' | ''>('')
const error = ref('')
const fileError = ref('')
const picker = ref<HTMLInputElement>()
const maxUpload = 10 * 1024 * 1024
const existing = computed(() => references.value.filter(path => !removed.value.has(path)))
const canSave = computed(() => !!name.value.trim() && !!instructions.value.trim())
const collidingNames = computed(() => {
  const filenames = new Set(existing.value.map(path => path.toLowerCase()))
  return uploads.value.some(file => filenames.has('references/' + file.name.toLowerCase()))
})

async function load() {
  if (!props.item) return
  busy.value = 'loading'
  error.value = ''
  try {
    const { item, skill } = await workRequest<ConnectorDetail>('/connectors/' + encodeURIComponent(props.item.id))
    if (!skill) throw new Error(t('The skill bundle is unavailable.', '暂时无法读取技能包。'))
    name.value = item.name
    description.value = item.description
    enabled.value = item.enabled
    revision.value = item.revision
    instructions.value = skill.instructions
    references.value = skill.references
    loaded.value = true
  } catch (cause) {
    error.value = connectorError(cause)
  } finally {
    busy.value = ''
  }
}
onMounted(load)

function addFiles(event: Event) {
  const field = event.target as HTMLInputElement
  const incoming = [...field.files ?? []]
  field.value = ''
  if (!incoming.length) return
  const merged = [...uploads.value, ...incoming]
  if (merged.reduce((size, file) => size + file.size, 0) > maxUpload) {
    fileError.value = t('New reference files must total at most 10 MiB.', '新参考文件合计不能超过 10 MiB。')
    return
  }
  if (merged.length + existing.value.length > 511) {
    fileError.value = t('A skill can contain up to 511 reference files.', '每个 Skill 最多可有 511 个参考文件。')
    return
  }
  if (new Set(merged.map(file => file.name.toLowerCase())).size !== merged.length) {
    fileError.value = t('New reference filenames must be unique.', '新上传的参考文件不能重名。')
    return
  }
  if (incoming.some(file => file.name.toLowerCase() === 'skill.md')) {
    fileError.value = t('Use the instruction editor to update SKILL.md.', '请通过指令编辑器更新 SKILL.md。')
    return
  }
  uploads.value = merged
  fileError.value = ''
}

function changeReference(path: string) {
  if (removed.value.has(path)) removed.value.delete(path)
  else removed.value.add(path)
}

function encodeFile(file: File) {
  return new Promise<{ name: string; data: string }>((resolve, reject) => {
    const reader = new FileReader()
    reader.onerror = reader.onabort = () => reject(new Error(t('Could not read a reference file.', '无法读取参考文件。')))
    reader.onload = () => resolve({ name: file.name, data: String(reader.result).split(',', 2)[1] ?? '' })
    reader.readAsDataURL(file)
  })
}

async function save() {
  if (busy.value || !loaded.value || !canSave.value || collidingNames.value) return
  busy.value = 'saving'
  error.value = ''
  try {
    const files = await Promise.all(uploads.value.map(encodeFile))
    const path = props.item ? '/connectors/' + encodeURIComponent(props.item.id) + '/skill' : '/connectors/skills'
    emit('saved', await workRequest<Connector>(path, props.item ? 'PUT' : 'POST', {
      name: name.value.trim(), description: description.value.trim(), instructions: instructions.value,
      enabled: enabled.value, revision: revision.value, files, remove_files: [...removed.value],
    }))
  } catch (cause) {
    error.value = connectorError(cause)
  } finally {
    busy.value = ''
  }
}
</script>

<template>
  <ConnectorDialog :title="item ? t('Edit Skill', '编辑 Skill') : t('Create Skill', '创建 Skill')" :busy="!!busy" @close="emit('close')">
    <div v-if="!loaded" class="connector-confirm">
      <p v-if="busy" role="status">{{ t('Loading skill…', '正在读取技能…') }}</p>
      <template v-else><p role="alert">{{ error }}</p><button type="button" class="outline-button" @click="load">{{ t('Retry', '重试') }}</button></template>
    </div>
    <form v-else class="connector-form" @submit.prevent="save">
      <fieldset :disabled="!!busy" class="connector-fields">
        <label>{{ t('Skill name', '技能名称') }}<input v-model.trim="name" required maxlength="120" autocomplete="off"></label>
        <label>{{ t('Description (optional)', '技能描述（选填）') }}<textarea v-model="description" rows="2" maxlength="2000" /></label>
        <section class="skill-instructions">
          <div class="skill-editor-heading">
            <label for="skill-instructions">{{ t('Instructions', '技能指令') }} <small class="muted">Markdown</small></label>
            <div class="skill-preview-toggle" role="group" :aria-label="t('Instruction view', '指令视图')">
              <button type="button" :aria-pressed="!preview" @click="preview = false">{{ t('Write', '编辑') }}</button>
              <button type="button" :aria-pressed="preview" @click="preview = true">{{ t('Preview', '预览') }}</button>
            </div>
          </div>
          <textarea v-if="!preview" id="skill-instructions" v-model="instructions" rows="8" maxlength="32000" required />
          <div v-else class="skill-preview" :aria-label="t('Markdown preview', 'Markdown 预览')" tabindex="0">
            <MarkdownText v-if="instructions.trim()" :text="instructions" />
            <p v-else class="muted">{{ t('Write instructions to preview them here.', '填写技能指令后可在此预览。') }}</p>
          </div>
        </section>
        <section class="skill-files">
          <div class="skill-editor-heading">
            <h3>{{ t('Reference files', '参考文件') }}</h3>
            <button type="button" class="outline-button connector-secondary" @click="picker?.click()"><FilePlus2 aria-hidden="true" />{{ t('Add files', '添加文件') }}</button>
          </div>
          <input ref="picker" type="file" multiple hidden @change="addFiles">
          <p class="muted">{{ t('New files go into references/. UTF-8 text files up to 1 MiB are readable by the Agent.', '新文件保存到 references/。Agent 可读取不超过 1 MiB 的 UTF-8 文本。') }}</p>
          <ul v-if="references.length || uploads.length" class="skill-file-list">
            <li v-for="path in references" :key="path" :class="{ 'file-removed': removed.has(path) }">
              <code>{{ path }}</code>
              <button type="button" class="quiet-button" :aria-label="(removed.has(path) ? t('Restore ', '恢复 ') : t('Remove ', '移除 ')) + path"
                @click="changeReference(path)">{{ removed.has(path) ? t('Restore', '恢复') : t('Remove', '移除') }}</button>
            </li>
            <li v-for="(file, i) in uploads" :key="'upload:' + file.name">
              <code>references/{{ file.name }}</code>
              <button type="button" class="icon-button" :aria-label="t('Remove ', '移除 ') + file.name" @click="uploads.splice(i, 1); fileError = ''"><X aria-hidden="true" /></button>
            </li>
          </ul>
          <p v-if="fileError || collidingNames" class="connector-field-error" role="alert">
            {{ fileError || t('Remove the existing reference before replacing it with the same filename.', '替换同名文件前，请先移除已有的参考文件。') }}
          </p>
        </section>
        <label class="check-line"><input v-model="enabled" type="checkbox">{{ t('Enable after saving', '保存后启用') }}</label>
      </fieldset>
      <p v-if="error" class="connector-feedback" data-result="error" role="alert">{{ error }}</p>
      <footer class="connector-dialog-actions">
        <button type="button" class="quiet-button" :disabled="!!busy" @click="emit('close')">{{ t('Cancel', '取消') }}</button>
        <button type="submit" class="accent-button" :disabled="!!busy || !canSave || collidingNames">{{ busy === 'saving' ? t('Saving…', '保存中…') : t('Save Skill', '保存技能') }}</button>
      </footer>
    </form>
  </ConnectorDialog>
</template>
