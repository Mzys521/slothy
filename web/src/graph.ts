export type GraphMemoryType = 'episodic' | 'semantic' | 'preference'

export type GraphPoint = {
  id: string
  x: number
  y: number
  importance: number
  title?: string
  zhTitle?: string
  memory?: string
  type?: GraphMemoryType
}

export interface MemoryGraphResponse {
  nodes: { id: string; label: string; type: GraphMemoryType; importance: number }[]
  edges: { source: string; target: string; relation: string; relevance: number; directed: boolean; origin: string }[]
  total: number
  truncated: boolean
  limit: number
}
