import type { RunActivity, ToolArtifact, ToolMetric, ToolResult } from './workApi'

export function activityResult(entry: RunActivity): ToolResult | null {
  if (entry.summary) return { ok: !entry.error_code, summary: entry.summary, artifacts: entry.artifacts ?? [],
    error: entry.error_code ? { code: entry.error_code, message: entry.summary, retryable: false, hint: entry.error_hint } : null }
  return parseToolResult(entry.result)
}

export function parseToolResult(value?: string | null): ToolResult | null {
  if (!value) return null
  try {
    const result = JSON.parse(value)
    if (typeof result?.ok !== 'boolean' || typeof result.summary !== 'string'
      || !Array.isArray(result.artifacts) || (!result.ok && typeof result.error?.code !== 'string')) return null
    return result as ToolResult
  } catch { return null }
}

export function safeArtifactUrl(value?: string | null): string | undefined {
  return value && /^\/api\/v1\/threads\/[^/?#]+\/artifacts\/[a-f0-9]{64}$/.test(value) ? value : undefined
}

/** Fetch through the existing authenticated desktop transport. / 通过既有桌面认证通道获取产物。 */
export async function fetchArtifact(artifact: ToolArtifact, request: typeof fetch = fetch): Promise<Blob> {
  const path = safeArtifactUrl(artifact.url)
  if (!path) throw new Error('Invalid artifact URL.')
  const response = await request(path)
  if (!response.ok) throw new Error('Artifact download failed.')
  return response.blob()
}

/** Share authenticated downloads across tool results and composer exports. / 工具产物与命令导出共用认证下载。 */
export async function downloadArtifact(artifact: ToolArtifact) {
  const url = URL.createObjectURL(await fetchArtifact(artifact))
  const link = document.createElement('a')
  link.href = url
  link.download = artifact.name
  document.body.append(link)
  link.click()
  link.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 30_000)
}

export function riskLabel(risk: string | null | undefined, translate: (en: string, zh: string) => string) {
  return ({ read: translate('Read', '只读'), write: translate('Write', '写入'),
    execute: translate('Execute', '代码执行'), network: translate('Network', '网络') })[risk ?? '']
    ?? translate('Unclassified', '未分类')
}

export function metricSummary(metric: ToolMetric, translate: (en: string, zh: string) => string) {
  return translate(
    `${metric.calls} calls · ${metric.ok} successful · ${metric.errors} errors · ${metric.rejected} declined · ${metric.duration_ms} ms · approval ${metric.approval_wait_ms} ms · ${metric.output_bytes} B · ${metric.artifacts} artifacts · ${metric.timed_out} timeouts · ${metric.reused} reused`,
    `${metric.calls} 次调用 · 成功 ${metric.ok} · 失败 ${metric.errors} · 拒绝 ${metric.rejected} · 耗时 ${metric.duration_ms} ms · 审批等待 ${metric.approval_wait_ms} ms · 输出 ${metric.output_bytes} B · 产物 ${metric.artifacts} · 超时 ${metric.timed_out} · 复用 ${metric.reused}`,
  )
}
