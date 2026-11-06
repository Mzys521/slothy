export interface RunActivity {
  id: string
  kind: 'prepare' | 'context' | 'model' | 'tool'
  status: 'running' | 'done' | 'requested' | 'approval' | 'error' | 'rejected'
  name?: string | null
  arguments?: string | null
  result?: string | null
  memory_count?: number | null
  approved?: boolean
}

export interface ModelItem {
  id: string
  provider: string
  display_name?: string | null
  reasoning_levels: string[]
  default_effort: string | null
}

export interface RunMetrics {
  model: string | null
  total_tokens: number
  output_tokens: number
  input_tokens: number
  cached_input_tokens: number
  cache_reported_input_tokens: number
  model_seconds: number
  context_tokens: number
  context_window: number | null
}

export interface ToolReview {
  approval_id: string
  thread_id: string
  run_id: string
  calls: { name: string; arguments: Record<string, unknown> }[]
  used_memory_ids?: string[]
  message?: string
  activity?: RunActivity[]
  metrics?: RunMetrics | null
}

export interface WorkReply {
  run_id: string
  response: string
  used_memory_ids?: string[]
  profile_version?: number | null
  activity?: RunActivity[]
  metrics?: RunMetrics | null
}

export interface MemoryReceipt {
  id: string
  title: string
  scope: 'global' | 'project' | 'thread'
  type?: 'episodic' | 'semantic' | 'preference'
}

export function memoryTypeName(type: NonNullable<MemoryReceipt['type']>, translate: (en: string, zh: string) => string) {
  return {
    episodic: translate('Episodic memory', '情景记忆'),
    semantic: translate('Semantic memory', '语义记忆'),
    preference: translate('Preference memory', '偏好记忆'),
  }[type]
}

export function memoryScope(scope: MemoryReceipt['scope'], translate: (en: string, zh: string) => string) {
  return {
    global: translate('Across conversations', '跨会话'),
    project: translate('This project', '当前项目'),
    thread: translate('This conversation', '当前会话'),
  }[scope]
}

export function threadPath(id: string) {
  return '/threads/' + encodeURIComponent(id)
}

export interface UploadedAttachment {
  id: string
  filename: string
  byte_size: number
}

export function uploadAttachment(thread: string, file: File) {
  return apiRequest<UploadedAttachment>(threadPath(thread) + '/attachments?filename=' +
    encodeURIComponent(file.name), {
    method: 'POST', body: file, headers: { 'Content-Type': 'application/octet-stream' },
  })
}

export class ApiError extends Error {
  constructor(message: string, readonly status: number, readonly code?: string) {
    super(message)
  }
}

export async function workRequest<T>(path: string, method = 'GET', body?: object): Promise<T> {
  return apiRequest<T>(path, {
    method,
    ...(body && { body: JSON.stringify(body), headers: { 'Content-Type': 'application/json' } }),
  })
}

type StreamUpdate =
  | { event: 'activity'; run_id: string; items: RunActivity[]; metrics?: RunMetrics | null }
  | { event: 'delta'; text: string }

export async function streamWorkRequest(
  path: string, body: object, update: (event: StreamUpdate) => void,
): Promise<ToolReview | WorkReply> {
  const response = await fetch('/api/v1' + path, {
    method: 'POST',
    headers: { Accept: 'text/event-stream', 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!response.ok) return readApiResponse(response)
  if (!response.body || !response.headers.get('content-type')?.startsWith('text/event-stream')) {
    throw new ApiError('The server did not return an event stream.', response.status)
  }
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  try {
    while (true) {
      const chunk = await reader.read()
      buffer += decoder.decode(chunk.value, { stream: !chunk.done })
      let boundary = /\r?\n\r?\n/.exec(buffer)
      while (boundary) {
        const frame = buffer.slice(0, boundary.index).split(/\r?\n/)
        buffer = buffer.slice(boundary.index + boundary[0].length)
        const event = frame.find(line => line.startsWith('event:'))?.slice(6).trim()
        const data = frame.filter(line => line.startsWith('data:')).map(line => line.slice(5)).join('\n')
        if (data) {
          const payload = JSON.parse(data)
          if (event === 'error') throw new ApiError(payload.message, response.status, payload.code)
          if (event === 'done' || event === 'approval_required') return payload
          if (event === 'delta' || event === 'activity') update({ ...payload, event })
        }
        boundary = /\r?\n\r?\n/.exec(buffer)
      }
      if (chunk.done) throw new ApiError('The Agent stream ended before a reply or approval.', response.status)
    }
  } finally {
    await reader.cancel().catch(() => {})
    reader.releaseLock()
  }
}

async function apiRequest<T>(path: string, options: RequestInit): Promise<T> {
  return readApiResponse(await fetch('/api/v1' + path, options))
}

export function uploadAccountAvatar<T>(image: Blob) {
  return apiRequest<T>('/account/avatar', {
    method: 'PUT', body: image, headers: { 'Content-Type': 'image/png' },
  })
}

export function uploadConnector<T>(kind: 'local' | 'mcp' | 'skill', file: File) {
  return apiRequest<T>('/connectors/import?kind=' + kind + '&filename=' + encodeURIComponent(file.name), {
    method: 'POST', body: file, headers: { 'Content-Type': 'application/octet-stream' },
  })
}

async function readApiResponse<T>(response: Response): Promise<T> {
  if (response.status === 204) return undefined as T
  const value = await response.json().catch(() => {
    throw new ApiError('The server did not return valid JSON.', response.status)
  })
  if (!response.ok) {
    throw new ApiError(value?.message || 'The work server is unavailable.', response.status, value?.code)
  }
  return value as T
}
