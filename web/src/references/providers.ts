import { workRequest } from '../workApi'
import type { ReferenceItem, ReferenceKind } from './types'

const endpoints = { folder: 'folders', file: 'files', session: 'sessions', plugin: 'plugins' } as const
export async function searchReferences(kind: ReferenceKind, query: string, scope: { workspace: string; thread?: string }, signal?: AbortSignal): Promise<ReferenceItem[]> {
  const params = new URLSearchParams({ q: query, limit: '8', workspace_id: scope.workspace })
  if (scope.thread) params.set('thread_id', scope.thread)
  if (kind === 'folder' && query.endsWith('/')) { params.set('path', query); params.set('q', '') }
  const page = await workRequest<{ items: Record<string, unknown>[] }>('/references/' + endpoints[kind] + '?' + params, 'GET', undefined, { signal })
  return page.items.map(item => ({ kind, id: String(item.id ?? item.path),
    label: String(item.title || item.name || item.path || item.id),
    path: kind === 'folder' ? String(item.path) : undefined,
    source: kind === 'file' ? item.source as ReferenceItem['source'] : undefined,
    status: item.enabled === false ? 'disabled' : 'ok',
    sublabel: String(item.path || item.description || item.updated_at || ''),
  }))
}
