<script setup lang="ts">
import { ref, watch } from 'vue'
import { t } from './preferences'
import { workRequest, type UploadedAttachment } from './workApi'
import ConnectorsPage from './ConnectorsPage.vue'

const props = defineProps<{ kind: 'plugins' | 'library' }>()
const files = ref<UploadedAttachment[]>([])
const offset = ref<number | null>(null)
const loading = ref(false)
const error = ref('')

/**
 * Loads the attachment library, optionally appending the next page.
 *
 * 加载附件资料库，可选择追加下一页。
 *
 * @param more - True to append the next page instead of replacing the list. / 为 true 时追加下一页而非替换列表。
 * @returns Resolves when the request finishes. / 请求完成后结束。
 */
async function load(more = false) {
  if (props.kind !== 'library') return
  loading.value = true
  error.value = ''
  try {
    if (props.kind === 'library') {
      const page = await workRequest<{ items: UploadedAttachment[]; next_offset: number | null }>(
        '/attachments?offset=' + (more ? offset.value : 0),
      )
      files.value = more ? [...files.value, ...page.items] : page.items
      offset.value = page.next_offset
    }
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : t('Could not load data.', '无法加载数据。')
  } finally {
    loading.value = false
  }
}
// Reload whenever the active collection switches between plugins and library. / 在插件与资料库之间切换时重新加载。
watch(() => props.kind, () => { void load() }, { immediate: true })
</script>

<template>
  <ConnectorsPage v-if="kind === 'plugins'" />
  <main v-else class="simple-page">
    <header><h1>{{ kind === 'library' ? t('Files', '文件资料库') : t('Tools', '已装载工具') }}</h1></header>
    <p v-if="loading" role="status">{{ t('Loading…', '正在加载…') }}</p>
    <div v-if="error"><p role="alert">{{ error }}</p><button class="text-link" @click="load()">{{ t('Retry', '重试') }}</button></div>
    <template v-else-if="kind === 'library'">
      <p v-if="!loading && !files.length">{{ t('No uploaded files.', '暂无上传文件。') }}</p>
      <div v-for="file in files" :key="file.id" class="simple-row">{{ file.filename }} · {{ file.byte_size.toLocaleString() }} B</div>
      <button v-if="offset !== null" class="outline-button" :disabled="loading" @click="load(true)">{{ t('Load more', '加载更多') }}</button>
    </template>
  </main>
</template>
