import type { GraphMemoryType, GraphPoint, MemoryGraphResponse } from './graph'

interface Particle extends GraphPoint {
  type: GraphMemoryType
  vx: number
  vy: number
  ax: number
  ay: number
  anchorX: number
  anchorY: number
  phase: number
  mass: number
  radius: number
}
interface Spring { source: Particle; target: Particle; relevance: number; phase: number }

function hashId(id: string) {
  return [...id].reduce((value, character) => Math.imul(value ^ character.charCodeAt(0), 16777619) >>> 0, 2166136261)
}
const clamp = (value: number, min: number, max: number) => Math.max(min, Math.min(max, value))

/** Category neighborhoods, weaker same-kind attraction and stronger real-link springs. */
export class GraphMotion {
  private particles: Particle[] = []
  private springs: Spring[] = []
  private time = 0

  update(graph: Pick<MemoryGraphResponse, 'nodes' | 'edges'>) {
    const previous = new Map(this.particles.map(point => [point.id, point]))
    const kinds = [...new Set(graph.nodes.map(node => node.type))].sort()
    const centers = new Map(kinds.map((kind, index) => {
      const angle = -Math.PI * .8 + index * Math.PI * 2 / kinds.length
      return [kind, { x: 635 + (kinds.length > 1 ? Math.cos(angle) * 290 : 0),
        y: 374 + (kinds.length > 1 ? Math.sin(angle) * 160 : 0) }] as const
    }))
    this.particles = [...graph.nodes].sort((a, b) => a.id.localeCompare(b.id)).map(node => {
      const hash = hashId(node.id)
      const distance = graph.nodes.length === 1 ? 0 : Math.sqrt(((hash >>> 8) % 10000 + .5) / 10000)
      const phase = hash / 4294967296 * Math.PI * 2
      const center = centers.get(node.type)!
      const anchorX = center.x + Math.cos(phase) * distance * (kinds.length > 1 ? 210 : 495)
      const anchorY = center.y + Math.sin(phase) * distance * (kinds.length > 1 ? 150 : 305)
      const importance = clamp(node.importance, 0, 1)
      return { x: anchorX, y: anchorY, vx: 0, vy: 0, ax: 0, ay: 0, anchorX, anchorY, phase,
        ...previous.get(node.id), id: node.id, memory: node.id, title: node.label, type: node.type,
        importance, mass: 1 + importance * 1.8, radius: 2.5 + importance * 22 }
    })
    const points = new Map(this.particles.map(point => [point.id, point]))
    const pairs = new Map<string, Spring>()
    for (const edge of graph.edges) {
      const source = points.get(edge.source), target = points.get(edge.target)
      if (!source || !target || source === target) continue
      const key = JSON.stringify([source.id, target.id].sort())
      const relevance = clamp(edge.relevance, 0, 1)
      if ((pairs.get(key)?.relevance ?? -1) >= relevance) continue
      pairs.set(key, { source, target, relevance, phase: hashId(key) / 4294967296 * Math.PI * 2 })
    }
    this.springs = [...pairs.values()]
  }

  snapshot(): GraphPoint[] {
    return this.particles.map(({ id, memory, title, type, importance, x, y }) => ({ id, memory, title, type, importance, x, y }))
  }

  moveNode(id: string, x: number, y: number) {
    const point = this.particles.find(point => point.id === id)
    if (!point || !Number.isFinite(x) || !Number.isFinite(y)) return
    point.x = point.anchorX = clamp(x, 70 + point.radius, 1200 - point.radius)
    point.y = point.anchorY = clamp(y, 45 + point.radius, 700 - point.radius)
    point.vx = point.vy = 0
  }

  step(milliseconds: number, held: ReadonlySet<string> = new Set()) {
    if (!Number.isFinite(milliseconds) || milliseconds <= 0) return
    // A resumed tab must never catch up a long hidden interval in one jump.
    const duration = Math.min(milliseconds, 50)
    const steps = Math.ceil(duration / (1000 / 60))
    const dt = duration / steps / (1000 / 60)
    for (let i = 0; i < steps; i++) this.integrate(dt, held)
  }

  private integrate(dt: number, held: ReadonlySet<string>) {
    this.time += dt / 60
    const globalAttraction = .000015 / Math.sqrt(Math.max(1, this.particles.length))
    const categorySizes = new Map<string, number>()
    for (const point of this.particles) categorySizes.set(point.type, (categorySizes.get(point.type) ?? 0) + 1)
    for (const point of this.particles) {
      // Distinct slow phases keep the field alive after the initial spring settling.
      point.ax = (point.anchorX - point.x) * .00018 + Math.sin(this.time * .65 + point.phase) * .013
      point.ay = (point.anchorY - point.y) * .00018 + Math.cos(this.time * .53 + point.phase * 1.7) * .013
    }
    for (let i = 0; i < this.particles.length; i++) {
      const a = this.particles[i]!
      for (let j = i + 1; j < this.particles.length; j++) {
        const b = this.particles[j]!
        let dx = b.x - a.x, dy = b.y - a.y
        let distance = Math.hypot(dx, dy)
        if (distance < .01) {
          dx = Math.cos(a.phase + b.phase + 1) * .1
          dy = Math.sin(a.phase + b.phase + 1) * .1
          distance = .1
        }
        const minimum = a.radius + b.radius + 18
        const repulsion = 900 / (distance * distance + 80) + Math.max(0, minimum - distance) * .035
        // A category preference is a layout force, never a fabricated graph relationship.
        const categoryAttraction = a.type === b.type ? .00008 / Math.sqrt(categorySizes.get(a.type)!) : 0
        const force = globalAttraction + categoryAttraction - repulsion / distance
        const fx = dx * force, fy = dy * force
        a.ax += fx; a.ay += fy
        b.ax -= fx; b.ay -= fy
      }
    }
    for (const spring of this.springs) {
      const { source, target, relevance } = spring
      const dx = target.x - source.x, dy = target.y - source.y
      const distance = Math.max(.1, Math.hypot(dx, dy))
      const rest = (128 + (1 - relevance) * 90 + source.radius + target.radius) * (1 + Math.sin(this.time * .55 + spring.phase) * .025)
      const force = (distance - rest) * (.0016 + relevance * .0014) / distance
      const fx = dx * force, fy = dy * force
      source.ax += fx; source.ay += fy
      target.ax -= fx; target.ay -= fy
    }
    for (const point of this.particles) {
      if (held.has(point.id)) { point.vx = point.vy = 0; continue }
      const damping = Math.pow(.88, dt)
      point.vx = (point.vx + point.ax / point.mass * dt) * damping
      point.vy = (point.vy + point.ay / point.mass * dt) * damping
      const speed = Math.hypot(point.vx, point.vy)
      if (speed > 1.8) { point.vx *= 1.8 / speed; point.vy *= 1.8 / speed }
      point.x = clamp(point.x + point.vx * dt, 70 + point.radius, 1200 - point.radius)
      point.y = clamp(point.y + point.vy * dt, 45 + point.radius, 700 - point.radius)
    }
  }
}
