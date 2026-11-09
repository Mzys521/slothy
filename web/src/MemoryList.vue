<script setup lang="ts">
/**
 * Saved-memory list with its own request and action state.
 *
 * The list owns loading, filtering, restore and delete so the surrounding memory page
 * does not re-render when only list data changes.
 *
 * 已保存记忆列表，独立管理请求与操作状态；列表数据变化时不会带动外层记忆页重渲染。
 */
import { onMounted, ref } from 'vue'
import { t } from './preferences'
import { memoryScope, memoryTypeName, workRequest, type MemoryReceipt } from './workApi'

const emit = defineEmits<{ memory: [id: string] }>()
const memories = ref<MemoryReceipt[]>([])
const status = ref('active')
const loading = ref(false)
const busy = ref(false)
const error = ref('')
const restoring = ref<MemoryReceipt>()
const deleting = ref<MemoryReceipt>()

/**
 * Loads one page of memories for the selected status. / 加载所选状态的一页记忆。
 */
async function load() {
  loading.value = true
  error.value = ''
  try {
    const result = await workRequest<{ items: MemoryReceipt[] }>('/memories?limit=200&status=' + status.value)
    memories.value = result.items
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : t('Could not load memories.', '无法加载记忆。')
  } finally {
    loading.value = false
  }
}

onMounted(load)
defineExpose({ refresh: load })

/**
 * Runs one mutating action, clears confirmation state and reloads the list.
 *
 * 执行一次修改操作，清理确认状态并重新加载列表。
 *
 * @param action - Async mutation to run. / 要执行的异步修改。
 */
async function change(action: () => Promise<void>) {
  if (busy.value) return
  busy.value = true
  error.value = ''
  try {
    await action()
    restoring.value = undefined
    deleting.value = undefined
    await load()
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : t('Could not save changes.', '无法保存更改。')
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <section>
    <div class="memory-section-heading">
      <h2>{{ t('Saved memories', '已保存记忆') }}</h2>
      <select v-model="status" :aria-label="t('Memory status', '记忆状态')" @change="load">
        <option value="active">{{ t('Active', '有效记忆') }}</option>
        <option value="archived">{{ t('Archived', '已归档记忆') }}</option>
        <option value="superseded">{{ t('Superseded', '已被更正') }}</option>
        <option value="invalidated">{{ t('Invalidated', '已失效') }}</option>
      </select>
    </div>
    <p v-if="error" role="alert">{{ error }}</p>
    <p v-if="!memories.length && !loading" class="muted">{{ t('No saved memories yet.', '暂无已保存记忆。') }}</p>
    <div v-for="item in memories" :key="item.id" class="saved-memory-row">
      <button class="simple-row" @click="emit('memory', item.id)">
        {{ item.title }}
        <small>{{ item.type ? memoryTypeName(item.type, t) + ' · ' : '' }}{{ memoryScope(item.scope, t) }}</small>
        <small v-if="item.effective_frequency != null">{{ t('Confirmed sessions', '有效确认会话') }} {{ item.effective_frequency }} · {{ t('Frequency strength', '频次强度') }} {{ (item.strength ?? 0).toFixed(2) }}<span v-if="item.protected"> · {{ t('Protected', '受保护') }}</span></small>
      </button>
      <button v-if="status === 'archived'" class="quiet-button" :disabled="busy" @click="restoring = item">{{ t('Restore', '恢复') }}</button>
      <button class="quiet-button" :disabled="busy" @click="deleting = item">{{ t('Delete', '删除') }}</button>
    </div>
    <div v-if="restoring" class="memory-review-item" role="group" :aria-label="t('Restore memory', '恢复记忆')">
      <p>{{ restoring.title }} — {{ t('Restore this memory? If it is a profile attribute, its value replaces the current value in the same scope.', '恢复这条记忆？如果它属于画像属性，将替换同一范围内当前的属性值。') }}</p>
      <div>
        <button class="quiet-button" :disabled="busy" @click="restoring = undefined">{{ t('Cancel', '取消') }}</button>
        <button class="accent-button" :disabled="busy" @click="change(async () => { if (restoring) await workRequest('/memories/' + encodeURIComponent(restoring.id), 'PATCH', { status: 'active' }) })">{{ t('Confirm restore', '确认恢复') }}</button>
      </div>
    </div>
    <div v-if="deleting" role="group" :aria-label="t('Confirm deletion', '确认删除')">
      <p>{{ t('Permanently delete this memory?', '永久删除这条记忆？') }} {{ deleting.title }}</p>
      <button class="quiet-button" :disabled="busy" @click="deleting = undefined">{{ t('Cancel', '取消') }}</button>
      <button class="accent-button" :disabled="busy" @click="change(async () => { if (deleting) await workRequest('/memories/' + encodeURIComponent(deleting.id), 'DELETE') })">{{ t('Delete', '删除') }}</button>
    </div>
  </section>
</template>
