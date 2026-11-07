/**
 * Shared memory-graph types for the force-layout renderer.
 *
 * 记忆图谱共享类型定义，供力导向渲染使用。
 */

/** Memory kinds exposed by the graph API. / 图谱 API 暴露的记忆类型。 */
export type GraphMemoryType = 'episodic' | 'semantic' | 'preference' | 'identity' | 'skill'

/** A drawable graph node with its simulated position. / 带模拟坐标的可绘制图节点。 */
export type GraphPoint = {
  id: string
  x: number
  y: number
  importance: number
  title?: string
  zhTitle?: string
  memory?: string
  type?: GraphMemoryType
  scope?: 'global' | 'project' | 'thread'
  scope_ref?: string | null
  node_kind?: string
  fact_ids?: string[]
  effective_frequency?: number
  strength?: number
}

/** Raw graph payload returned by `GET /graph`. / `GET /graph` 返回的原始图谱数据。 */
export interface MemoryGraphResponse {
  nodes: { id: string; label: string; type: GraphMemoryType; importance: number; scope?: 'global' | 'project' | 'thread'; scope_ref?: string | null; node_kind?: string; concept_kind?: string | null; fact_ids?: string[]; effective_frequency?: number; strength?: number }[]
  edges: { source: string; target: string; relation: string; relevance: number; directed: boolean; origin: string; id?: string | null; fact_id?: string | null; effective_frequency?: number; strength?: number; evidence_ids?: string[] }[]
  total: number
  truncated: boolean
  limit: number
}
