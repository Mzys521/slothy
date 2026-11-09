/**
 * HTTP/SSE client for the local backend, including the streaming chat protocol.
 *
 * 本地后端的 HTTP/SSE 客户端，包含流式对话协议。
 */

import type { ReferenceItem } from './references/types'

/** One step of a run's activity trace. / 运行活动轨迹中的一个步骤。 */
export interface RunActivity {
  id: string
  kind: 'prepare' | 'context' | 'model' | 'tool'
  status: 'running' | 'done' | 'requested' | 'approval' | 'error' | 'rejected'
  name?: string | null
  arguments?: string | null
  result?: string | null
  memory_count?: number | null
  approved?: boolean
  risk?: string | null
  artifacts?: ToolArtifact[]
  duration_ms?: number | null
  error_code?: string | null
  summary?: string | null
  error_hint?: string | null
}

/** A downloadable artifact produced by a tool call. / 工具调用产生的可下载产物。 */
export interface ToolArtifact {
  id: string
  name: string
  bytes: number
  rel_path: string
  url?: string | null
}

/** Normalized tool-result envelope returned to the model and the UI. / 返回给模型与界面的统一工具结果信封。 */
export interface ToolResult {
  ok: boolean
  summary: string
  data?: Record<string, unknown> | unknown[] | null
  artifacts: ToolArtifact[]
  next_offset?: number | null
  error?: { code: string; message: string; retryable: boolean; hint?: string | null } | null
}

/** Per-tool execution counters reported in run metrics. / 运行指标中按工具统计的执行计数。 */
export interface ToolMetric {
  name: string
  calls: number
  ok: number
  errors: number
  rejected: number
  approval_wait_ms: number
  duration_ms: number
  output_bytes: number
  output_chars: number
  artifacts: number
  timed_out: number
  reused: number
}

/** A selectable model from `GET /models`. / `GET /models` 返回的可选模型。 */
export interface ModelItem {
  id: string
  provider: string
  display_name?: string | null
  reasoning_levels: string[]
  default_effort: string | null
}

/** Token and timing metrics reported for a run. / 单次运行的 token 与耗时指标。 */
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
  tools?: ToolMetric[]
  usage_reported?: boolean
}

/** Metadata-only diagnostics: context evidence is not a claim of model adoption. / 仅含元数据的上下文证据诊断。 */
export interface MemoryTrace {
  schema_version: number
  response_source: 'model' | 'identity_fact'
  status: string
  intent?: string | null
  context_token_estimate: number
  facts: { id: string; included: boolean; reason: string; score?: number | null; strength?: number | null; concept_ids: string[]; relation_path?: { source?: string | null; predicate?: string | null; target?: string | null } }[]
  warnings: string[]
}

/** Tool approval request streamed when work mode pauses. / 工作模式暂停时下发的工具审批请求。 */
export interface ToolReview {
  approval_id: string
  thread_id: string
  run_id: string
  calls: { name: string; arguments: Record<string, unknown>; risk?: string | null; review_hint?: string | null }[]
  used_memory_ids?: string[]
  message?: string
  activity?: RunActivity[]
  metrics?: RunMetrics | null
  references?: ReferenceItem[]
  used_reference_ids?: string[]
  memory_trace?: MemoryTrace
  response_source?: string
}

/** Final assistant reply of a run. / 单次运行的最终助手回复。 */
export interface WorkReply {
  run_id: string
  response: string
  reasoning?: string
  message?: string | null
  used_memory_ids?: string[]
  profile_version?: number | null
  activity?: RunActivity[]
  metrics?: RunMetrics | null
  references?: ReferenceItem[]
  used_reference_ids?: string[]
  memory_trace?: MemoryTrace
  response_source?: string
}

/** A memory receipt shown under a completed turn. / 在已完成轮次下方展示的记忆回执。 */
export interface MemoryReceipt {
  id: string
  title: string
  scope: 'global' | 'project' | 'thread'
  type?: 'episodic' | 'semantic' | 'preference' | 'identity' | 'skill'
  effective_frequency?: number
  strength?: number
  status?: string
  protected?: boolean
}

/**
 * Localizes a memory type label.
 *
 * 本地化记忆类型标签。
 *
 * @param type - Memory type from the API. / API 返回的记忆类型。
 * @param translate - Active bilingual translator, usually `t`. / 当前双语翻译函数，通常是 `t`。
 * @returns Localized label. / 本地化后的标签。
 */
export function memoryTypeName(type: NonNullable<MemoryReceipt['type']>, translate: (en: string, zh: string) => string) {
  return {
    episodic: translate('Episodic memory', '情景记忆'),
    semantic: translate('Semantic memory', '语义记忆'),
    preference: translate('Preference memory', '偏好记忆'),
    identity: translate('Identity fact', '身份事实'),
    skill: translate('Skill fact', '技能事实'),
  }[type]
}

/**
 * Localizes a memory scope label.
 *
 * 本地化记忆作用域标签。
 *
 * @param scope - Memory scope from the API. / API 返回的记忆作用域。
 * @param translate - Active bilingual translator, usually `t`. / 当前双语翻译函数，通常是 `t`。
 * @returns Localized label. / 本地化后的标签。
 */
export function memoryScope(scope: MemoryReceipt['scope'], translate: (en: string, zh: string) => string) {
  return {
    global: translate('Across conversations', '跨会话'),
    project: translate('This project', '当前项目'),
    thread: translate('This conversation', '当前会话'),
  }[scope]
}

/**
 * Builds the URL path for one thread.
 *
 * 构造单个线程的 URL 路径。
 *
 * @param id - Thread id. / 线程 ID。
 * @returns Encoded `/threads/{id}` path. / 编码后的 `/threads/{id}` 路径。
 */
export function threadPath(id: string) {
  return '/threads/' + encodeURIComponent(id)
}

/** Uploaded attachment metadata. / 已上传附件元数据。 */
export interface UploadedAttachment {
  id: string
  filename: string
  byte_size: number
}

/**
 * Uploads one file as a raw octet stream.
 *
 * 以原始八位字节流上传单个文件。
 *
 * @param thread - Thread the file is attached to. / 文件所属的线程。
 * @param file - Browser `File` selected by the user. / 用户选择的浏览器 `File`。
 * @returns Attachment metadata from the server. / 服务端返回的附件元数据。
 */
export function uploadAttachment(thread: string, file: File, signal?: AbortSignal) {
  return apiRequest<UploadedAttachment>(threadPath(thread) + '/attachments?filename=' +
    encodeURIComponent(file.name), {
    method: 'POST', body: file, headers: { 'Content-Type': 'application/octet-stream' }, signal,
  })
}

/** Error carrying the backend `code` and HTTP `status`. / 携带后端 `code` 与 HTTP `status` 的错误。 */
export class ApiError extends Error {
  /**
   * Creates an API error.
   *
   * 创建 API 错误。
   *
   * @param message - Human-readable message. / 人类可读的消息。
   * @param status - HTTP status code. / HTTP 状态码。
   * @param code - Machine-readable backend error code. / 机器可读的后端错误码。
   */
  constructor(message: string, readonly status: number, readonly code?: string) {
    super(message)
  }
}

/**
 * Sends a JSON request and parses the JSON response.
 *
 * 发送 JSON 请求并解析 JSON 响应。
 *
 * @typeParam T - Expected response payload type. / 期望的响应载荷类型。
 * @param path - Path below `/api/v1`. / `/api/v1` 之下的路径。
 * @param method - HTTP method, default GET. / HTTP 方法，默认 GET。
 * @param body - Optional object serialized as the JSON body. / 可选对象，将序列化为 JSON 请求体。
 * @returns Parsed payload. / 解析后的载荷。
 */
export async function workRequest<T>(path: string, method = 'GET', body?: object, options: RequestInit = {}): Promise<T> {
  return apiRequest<T>(path, {
    ...options,
    method,
    ...(body && { body: JSON.stringify(body), headers: { 'Content-Type': 'application/json' } }),
  })
}

type StreamUpdate =
  | { event: 'activity'; run_id: string; items: RunActivity[]; metrics?: RunMetrics | null }
  | { event: 'delta'; text: string }
  | { event: 'reasoning'; text: string }

/**
 * POSTs a request and consumes the server-sent event stream until a terminal frame.
 *
 * 发送 POST 请求，并消费服务端事件流直到收到终止帧。
 *
 * @param path - Path below `/api/v1`. / `/api/v1` 之下的路径。
 * @param body - JSON request body. / JSON 请求体。
 * @param update - Callback receiving every `delta` and `activity` frame. / 接收每个 `delta` 与 `activity` 帧的回调。
 * @returns Final `done` reply or `approval_required` review. / `done` 的最终回复或 `approval_required` 的审批请求。
 */
export async function streamWorkRequest(
  path: string, body: object, update: (event: StreamUpdate) => void,
  signal?: AbortSignal,
): Promise<ToolReview | WorkReply> {
  const response = await fetch('/api/v1' + path, {
    method: 'POST',
    headers: { Accept: 'text/event-stream', 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  })
  // Errors before streaming starts are ordinary JSON responses.
  // 在流开始前发生的错误以普通 JSON 响应返回。
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
      // Frames are separated by a blank line; keep the trailing partial frame in the buffer.
      // 帧之间以空行分隔；缓冲区保留尚未完整的尾帧。
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
          if (event === 'delta' || event === 'activity' || event === 'reasoning') update({ ...payload, event })
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

/**
 * Sends a request below `/api/v1` and parses the response.
 *
 * 向 `/api/v1` 之下发送请求并解析响应。
 *
 * @typeParam T - Expected response payload type. / 期望的响应载荷类型。
 * @param path - Path below `/api/v1`. / `/api/v1` 之下的路径。
 * @param options - Fetch options. / fetch 选项。
 * @returns Parsed payload. / 解析后的载荷。
 */
async function apiRequest<T>(path: string, options: RequestInit): Promise<T> {
  return readApiResponse(await fetch('/api/v1' + path, options))
}

/**
 * Uploads a processed PNG avatar.
 *
 * 上传已处理好的 PNG 头像。
 *
 * @typeParam T - Expected response payload type. / 期望的响应载荷类型。
 * @param image - PNG blob to upload. / 要上传的 PNG 数据。
 * @returns Server response payload. / 服务端响应载荷。
 */
export function uploadAccountAvatar<T>(image: Blob) {
  return apiRequest<T>('/account/avatar', {
    method: 'PUT', body: image, headers: { 'Content-Type': 'image/png' },
  })
}

/**
 * Imports a connector bundle of the given kind.
 *
 * 导入指定类型的连接器包。
 *
 * @typeParam T - Expected response payload type. / 期望的响应载荷类型。
 * @param kind - Connector source kind. / 连接器来源类型。
 * @param file - File selected by the user. / 用户选择的文件。
 * @returns Server response payload. / 服务端响应载荷。
 */
export function uploadConnector<T>(kind: 'local' | 'mcp' | 'skill', file: File) {
  return apiRequest<T>('/connectors/import?kind=' + kind + '&filename=' + encodeURIComponent(file.name), {
    method: 'POST', body: file, headers: { 'Content-Type': 'application/octet-stream' },
  })
}

/**
 * Parses a JSON API response, mapping failures onto `ApiError`.
 *
 * 解析 JSON API 响应，并把失败映射为 `ApiError`。
 *
 * @typeParam T - Expected response payload type. / 期望的响应载荷类型。
 * @param response - Fetch response to read. / 待读取的 fetch 响应。
 * @returns Parsed payload, `undefined` for 204. / 解析后的载荷；204 返回 `undefined`。
 */
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
