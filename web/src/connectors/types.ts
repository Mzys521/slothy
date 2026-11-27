import { t } from '../preferences'
import { ApiError } from '../workApi'

export type ConnectorKind = 'local' | 'mcp' | 'skill'
export type Transport = 'stdio' | 'streamable-http' | 'sse'
export type ConnectionStatus = 'disabled' | 'unchecked' | 'connecting' | 'connected' | 'failed'

export interface Capability {
  name: string
  risk: string
  modes: string[]
  requires_approval: boolean
}

export interface Connector {
  id: string
  kind: ConnectorKind
  name: string
  description: string
  enabled: boolean
  builtin: boolean
  tools: string[]
  transport: Transport | 'http' | null
  file_count: number
  reference_count?: number | null
  risk?: string
  available?: boolean
  capabilities?: Capability[]
  connection_status?: ConnectionStatus | null
  revision?: string | null
}

export interface ToolDefinition {
  name: string
  description: string
  input_schema: Record<string, unknown>
}

export interface McpConfig {
  type: Connector['transport']
  command?: string | null
  args?: string[]
  cwd?: string | null
  env?: Record<string, string | null>
  url?: string | null
  headers?: Record<string, string | null>
  timeout?: number | null
}

export interface ConnectorDetail {
  item: Connector
  tools: ToolDefinition[]
  mcp?: McpConfig | null
  skill?: { markdown: string; instructions: string; references: string[] } | null
}

export interface McpResult {
  ok: boolean
  response_status: 'tools_listed' | 'failed'
  tool_count: number
  tools: ToolDefinition[]
  code?: string | null
  message?: string | null
  http_status?: number | null
  connector?: Connector | null
}

export interface SecretRow {
  id: number
  key: string
  value: string | null
  savedKey?: string
}

export const transports: Transport[] = ['stdio', 'streamable-http', 'sse']

export function transportLabel(transport: Connector['transport']) {
  if (transport === 'stdio') return 'stdio'
  if (transport === 'sse') return 'SSE'
  return 'Streamable HTTP'
}

export function connectionLabel(status: ConnectionStatus) {
  switch (status) {
    case 'connecting': return t('Connecting…', '连接中…')
    case 'connected': return t('Connected', '已连接')
    case 'failed': return t('Connection failed', '连接失败')
    case 'unchecked': return t('Not checked', '待验证')
    default: return t('Disabled', '未启用')
  }
}

export function connectorMessage(code?: string | null, detail?: string | null) {
  switch (code) {
    case 'MCP_HTTP_STATUS': return t('The MCP endpoint rejected the request.', 'MCP 端点拒绝了请求。')
    case 'MCP_PROGRAM_MISSING': return t('Command or working directory not found.', '找不到启动命令或工作目录。')
    case 'MCP_PERMISSION_DENIED': return t('The command could not be started.', '启动命令权限不足。')
    case 'MCP_TIMEOUT': return t('MCP discovery timed out.', 'MCP 工具发现超时。')
    case 'MCP_UNREACHABLE': return t('Could not reach the MCP endpoint.', '无法访问 MCP 服务地址。')
    case 'MCP_INVALID_TOOLS': return t('MCP returned an invalid tool schema.', 'MCP 返回的工具参数定义无效。')
    case 'MCP_INVALID_RESPONSE': return t('MCP returned an invalid response.', 'MCP 返回的响应格式无效。')
    case 'MCP_HANDSHAKE_FAILED':
      return t('Handshake or tool discovery did not finish. Check the server and credentials.', '握手或工具发现未完成，请检查服务和凭据。')
    case 'CONNECTOR_CHANGED': return t('This connector changed. Reopen it before editing.', '连接器已更新，请重新打开后再编辑。')
    case 'CONNECTOR_EXISTS': return t('This file or connector name is already in use.', '此文件已导入，或同名连接器已存在。')
    case 'CONNECTOR_CONNECTION_FAILED':
      return t('Could not enable MCP. Check the connection with Test connection.', '无法启用 MCP，可通过「测试连接」查看原因。')
    default: return detail || t('The connector request failed.', '连接器请求失败。')
  }
}

export function connectorError(cause: unknown) {
  if (cause instanceof ApiError) {
    const message = connectorMessage(cause.code, cause.message)
    const status = cause.code === 'MCP_HTTP_STATUS' ? cause.message.match(/HTTP \d{3}/)?.[0] : undefined
    return status ? message + ' ' + status : message
  }
  return connectorMessage(null, cause instanceof Error ? cause.message : null)
}
