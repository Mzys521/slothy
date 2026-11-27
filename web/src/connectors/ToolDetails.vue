<script setup lang="ts">
import { computed } from 'vue'
import { ChevronDown } from 'lucide-vue-next'
import { t } from '../preferences'
import type { ToolDefinition } from './types'

const props = defineProps<{ tool: ToolDefinition }>()
function object(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}
const parameters = computed(() => {
  const schema = props.tool.input_schema
  const required = new Set(Array.isArray(schema.required) ? schema.required : [])
  return Object.entries(object(schema.properties)).map(([name, value]) => {
    const field = object(value)
    return {
      name,
      required: required.has(name),
      type: typeof field.type === 'string' ? field.type : Array.isArray(field.type) ? field.type.join(' / ') : '',
      description: typeof field.description === 'string' ? field.description : '',
    }
  })
})
</script>

<template>
  <section class="connector-tool-detail">
    <h4><code>{{ tool.name }}</code></h4>
    <p v-if="tool.description && tool.description !== tool.name">{{ tool.description }}</p>
    <dl v-if="parameters.length" class="connector-parameters">
      <div v-for="parameter in parameters" :key="parameter.name">
        <dt><code>{{ parameter.name }}</code></dt>
        <dd><span v-if="parameter.type">{{ parameter.type }} · </span>{{ parameter.required ? t('Required', '必填') : t('Optional', '选填') }}
          <p v-if="parameter.description">{{ parameter.description }}</p>
        </dd>
      </div>
    </dl>
    <details class="connector-schema">
      <summary><ChevronDown aria-hidden="true" />{{ t('Full parameter schema', '完整参数定义') }}</summary>
      <pre>{{ JSON.stringify(tool.input_schema, null, 2) }}</pre>
    </details>
  </section>
</template>
