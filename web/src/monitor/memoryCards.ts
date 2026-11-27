import type { MemoryReceipt, MemoryTrace } from '../workApi'

export interface MemorySnippet extends Pick<MemoryReceipt, 'id' | 'title' | 'type' | 'effective_frequency' | 'strength'> {
  content: string
  concepts?: { id: string; label: string; kind: string }[]
  source_concept_id?: string | null
  target_concept_id?: string | null
  predicate?: string
  evidence?: { thread_id: string; run_id: string; quote: string; recorded_at?: string | null }[]
}

export interface RecallCard {
  id: string
  fact?: MemoryTrace['facts'][number]
  detail?: MemorySnippet
}

export function recallCards(ids: string[], trace: MemoryTrace | undefined, details: MemorySnippet[], focus?: string) {
  const facts = new Map(trace?.facts.map(fact => [fact.id, fact]))
  const snippets = new Map(details.map(item => [item.id, item]))
  const keys = focus ? [focus] : new Set([...facts.keys(), ...ids, ...snippets.keys()])
  return Array.from(keys, id => ({ id, fact: facts.get(id), detail: snippets.get(id) }))
}

const traceLabels: Record<string, [string, string]> = {
  provided: ['Evidence provided to the model', '已向模型提供参考事实'],
  identity_fact: ['Answered directly from identity facts', '根据身份事实直接回答'],
  identity_conflict: ['Identity changes await confirmation', '身份变更存在冲突，尚待确认'],
  no_match: ['No relevant facts matched', '没有匹配到相关事实'],
  no_record: ['No confirmed identity record', '没有已确认的身份记录'],
  disabled: ['Personalized memory is off', '定制记忆已关闭'],
  pending_learning: ['Memory learning is pending', '记忆仍在整理中'],
  pending_confirmation: ['Identity facts await confirmation', '身份事实尚待确认'],
  read_failed: ['Memory read failed', '记忆读取失败'],
  budget_excluded: ['Excluded by the context budget', '受上下文预算限制未提供'],
  concept_relation: ['Related concept', '语义概念关联'],
  identity_intent: ['Direct identity recall', '本人身份意图直召'],
  lexical: ['Local text match', '本地文本匹配'],
  profile_attribute: ['Current profile attribute', '当前画像属性'],
  unrecorded: ['Source unrecorded', '来源未记录'],
}

export function traceLabel(code: string, t: (en: string, zh: string) => string) {
  const pair = traceLabels[code]
  return pair ? t(...pair) : code
}
