<script setup lang="ts">
import { computed, ref } from 'vue'
import { ArrowUpRight, Download, FileText } from 'lucide-vue-next'
import { t } from './preferences'
import { changedFiles, downloadArtifact } from './toolResults'
import { threadPath, type RunActivity, type ToolArtifact } from './workApi'

const props = defineProps<{ threadId: string; activity?: RunActivity[] }>()
const files = computed(() => changedFiles(props.activity))
const opening = ref(new Set<string>())
const feedback = ref<Record<string, 'missing' | 'revealed' | 'error'>>({})
const desktopOpen = Boolean(window.desktop?.openArtifact)

function actionLabel(file: ToolArtifact) {
  return desktopOpen ? t(`Open ${file.rel_path}`, `打开 ${file.rel_path}`)
    : t(`Download ${file.rel_path}`, `下载 ${file.rel_path}`)
}

async function accessFile(file: ToolArtifact) {
  if (opening.value.has(file.id)) return
  opening.value.add(file.id)
  delete feedback.value[file.id]
  try {
    if (window.desktop?.openArtifact) {
      const result = await window.desktop.openArtifact(props.threadId, file.id)
      if (result !== 'opened') feedback.value[file.id] = result
    } else {
      const url = `/api/v1${threadPath(props.threadId)}/artifacts/${file.id}`
      await downloadArtifact({ ...file, url })
    }
  } catch {
    feedback.value[file.id] = 'error'
  } finally {
    opening.value.delete(file.id)
  }
}
</script>

<template>
  <section v-if="files.length" class="changed-files" :aria-label="t('Files changed in this reply', '本次修改的文件')">
    <h3>{{ t('Changed files', '本次修改的文件') }}</h3>
    <ul>
      <li v-for="file in files" :key="file.id">
        <button type="button" class="changed-file" :title="actionLabel(file)" :aria-label="actionLabel(file)"
          :disabled="opening.has(file.id)" :aria-busy="opening.has(file.id)" @click="accessFile(file)">
          <FileText class="changed-file-icon" aria-hidden="true" />
          <span class="changed-file-content">
            <strong>{{ file.name }}</strong>
            <small v-if="file.rel_path !== file.name">{{ file.rel_path }}</small>
          </span>
          <component :is="desktopOpen ? ArrowUpRight : Download" class="changed-file-action" aria-hidden="true" />
        </button>
        <p class="changed-file-feedback" role="status">
          <template v-if="feedback[file.id] === 'revealed'">{{ t('File located in its folder.', '已在文件夹中定位此文件。') }}</template>
          <template v-else-if="feedback[file.id] === 'missing'">{{ t('This file could not be found at its original location.', '在原位置找不到该文件。') }}</template>
          <template v-else-if="feedback[file.id] === 'error'">{{ desktopOpen ? t('Could not open this file.', '无法打开该文件。') : t('Could not download this file.', '无法下载该文件。') }}</template>
        </p>
      </li>
    </ul>
  </section>
</template>
