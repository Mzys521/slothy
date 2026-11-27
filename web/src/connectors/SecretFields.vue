<script setup lang="ts">
import { ref, useId } from 'vue'
import { Eye, EyeOff, Plus, X } from 'lucide-vue-next'
import { t } from '../preferences'
import type { SecretRow } from './types'

const props = defineProps<{ title: string; limit: number }>()
const rows = defineModel<SecretRow[]>({ required: true })
const shown = ref(new Set<number>())
const prefix = useId()
function add() {
  if (rows.value.length >= props.limit) return
  const id = Math.max(0, ...rows.value.map(row => row.id)) + 1
  rows.value = [...rows.value, { id, key: '', value: '' }]
}
function remove(id: number) {
  rows.value = rows.value.filter(row => row.id !== id)
  shown.value.delete(id)
}
function toggleVisibility(id: number) {
  if (shown.value.has(id)) shown.value.delete(id)
  else shown.value.add(id)
}
</script>

<template>
  <fieldset class="connector-field-list">
    <legend>{{ title }}</legend>
    <div v-for="(row, index) in rows" :key="row.id" class="connector-secret-row">
      <label>
        {{ t('Key', '键名') }}
        <input v-model="row.key" required maxlength="180" :aria-label="t('Key', '键名') + ' ' + (index + 1)" autocomplete="off" spellcheck="false">
      </label>
      <div class="connector-secret-input">
        <label :for="prefix + '-' + row.id">{{ t('Value', '值') }}</label>
        <div class="connector-secret-value">
          <input :id="prefix + '-' + row.id" :value="row.value ?? ''" :type="shown.has(row.id) ? 'text' : 'password'" maxlength="4096"
            :placeholder="row.value === null ? t('Saved. Type to replace.', '已保存，输入可替换') : undefined"
            :aria-label="t('Value', '值') + ' ' + (index + 1)"
            autocomplete="off" spellcheck="false" @input="row.value = ($event.target as HTMLInputElement).value">
          <button v-if="row.value !== null" type="button" class="icon-button" :aria-label="t('Show value', '显示值') + ' ' + (index + 1)"
            :aria-pressed="shown.has(row.id)" @click="toggleVisibility(row.id)">
            <EyeOff v-if="shown.has(row.id)" aria-hidden="true" /><Eye v-else aria-hidden="true" />
          </button>
        </div>
      </div>
      <button type="button" class="icon-button connector-remove-field" :aria-label="t('Remove row', '移除行') + ' ' + (index + 1)" @click="remove(row.id)"><X aria-hidden="true" /></button>
    </div>
    <button type="button" class="quiet-button connector-field-add" :disabled="rows.length >= limit" @click="add"><Plus aria-hidden="true" />{{ t('Add entry', '新增一项') }}</button>
  </fieldset>
</template>
