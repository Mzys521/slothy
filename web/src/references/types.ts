export type ReferenceKind = 'folder' | 'file' | 'session' | 'plugin'
export interface ReferenceItem {
  kind: ReferenceKind
  id: string
  label: string
  source?: 'workspace' | 'attachment'
  path?: string
  status?: 'ok' | 'missing' | 'disabled'
  reference_id?: string
  sublabel?: string
  truncated?: boolean
  included?: boolean
}
export function referenceKey(item: ReferenceItem) { return `${item.kind}:${item.source ?? ''}:${item.path ?? item.id}` }
export function referencePayload(item: ReferenceItem) {
  return { kind: item.kind, ...(item.kind === 'folder' ? { path: item.path ?? item.id } : { id: item.id }), ...(item.source ? { source: item.source } : {}) }
}
