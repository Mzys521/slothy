<script setup lang="ts">
import { ref, watch } from 'vue'
import { t } from './preferences'
import { workRequest, type UploadedAttachment } from './workApi'

const props = defineProps<{ kind: 'plugins' | 'library' }>()
const files = ref<UploadedAttachment[]>([])
const tools = ref<string[]>([])
const offset = ref<number | null>(null)
const loading = ref(false)
const error = ref('')

async function load(more = false) {
  loading.value = true
  error.value = ''
  try {
    if (props.kind === 'library') {
      const page = await workRequest<{ items: UploadedAttachment[]; next_offset: number | null }>(
        '/attachments?offset=' + (more ? offset.value : 0),
      )
      files.value = more ? [...files.value, ...page.items] : page.items
      offset.value = page.next_offset
    } else {
      tools.value = (await workRequest<{ work_tools: string[] }>('/runtime')).work_tools
    }
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : t('Could not load data.', '无法加载数据。')
  } finally {
    loading.value = false
  }
}
watch(() => props.kind, () => { void load() }, { immediate: true })
</script>

<template>
  <main class="simple-page">
    <header><h1>{{ kind === 'library' ? t('Files', '文件资料库') : t('Tools', '已装载工具') }}</h1></header>
    <p v-if="loading" role="status">{{ t('Loading…', '正在加载…') }}</p>
    <div v-if="error"><p role="alert">{{ error }}</p><button class="text-link" @click="load()">{{ t('Retry', '重试') }}</button></div>
    <template v-else-if="kind === 'library'">
      <p v-if="!loading && !files.length">{{ t('No uploaded files.', '暂无上传文件。') }}</p>
      <div v-for="file in files" :key="file.id" class="simple-row">{{ file.filename }} · {{ file.byte_size.toLocaleString() }} B</div>
      <button v-if="offset !== null" class="outline-button" :disabled="loading" @click="load(true)">{{ t('Load more', '加载更多') }}</button>
    </template>
    <template v-else>
      <p v-if="!loading && !tools.length">{{ t('No tools loaded.', '暂无已装载工具。') }}</p>
      <div v-for="tool in tools" :key="tool" class="simple-row"><code>{{ tool }}</code></div>
    </template>
  </main>
</template>
