/**
 * Deterministic force layout for the memory graph: category neighborhoods, repulsion and spring links.
 *
 * 记忆图谱的确定性力导向布局：类别邻域、排斥力与弹簧连线。
 */

import type { GraphMemoryType, GraphPoint, MemoryGraphResponse } from './graph'

/** Simulated node state with velocity, anchor and phase. / 带速度、锚点与相位的模拟节点状态。 */
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
/** One undirected spring between two particles. / 两个粒子之间的一条无向弹簧。 */
interface Spring { source: Particle; target: Particle; relevance: number; phase: number }

/**
 * Hashes a node id into a stable 32-bit seed for layout jitter.
 *
 * 将节点 ID 哈希为稳定的 32 位种子，用于布局抖动。
 *
 * @param id - Node identifier. / 节点标识。
 * @returns Unsigned 32-bit hash. / 无符号 32 位哈希值。
 */
function hashId(id: string) {
  return [...id].reduce((value, character) => Math.imul(value ^ character.charCodeAt(0), 16777619) >>> 0, 2166136261)
}
/**
 * Clamps a number into the inclusive range.
 *
 * 将数值限制在闭区间内。
 *
 * @param value - Input value. / 输入值。
 * @param min - Lower bound. / 下界。
 * @param max - Upper bound. / 上界。
 * @returns Clamped value. / 限制后的值。
 */
const clamp = (value: number, min: number, max: number) => Math.max(min, Math.min(max, value))

/**
 * Category neighborhoods, weaker same-kind attraction and stronger real-link springs.
 *
 * 类别邻域布局：同类别弱吸引，真实连线弹簧更强。
 */
export class GraphMotion {
  private particles: Particle[] = []
  private springs: Spring[] = []
  private time = 0

  /**
   * Recomputes anchors and springs from a new graph payload, reusing existing positions.
   *
   * 基于新的图谱数据重算锚点与弹簧，并复用已有位置。
   *
   * @param graph - Nodes and edges returned by `GET /graph`. / `GET /graph` 返回的节点与边。
   */
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
        ...previous.get(node.id), id: node.id, memory: node.id, title: node.label, type: node.type, node_kind: node.node_kind, scope: node.scope, scope_ref: node.scope_ref, fact_ids: node.fact_ids, effective_frequency: node.effective_frequency, strength: node.strength,
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

  /**
   * Returns drawable points with their current simulated positions.
   *
   * 返回带当前模拟坐标的可绘制点。
   *
   * @returns Snapshot of all particles. / 所有粒子的快照。
   */
  snapshot(): GraphPoint[] {
    return this.particles.map(({ id, memory, title, type, importance, x, y, node_kind, fact_ids, effective_frequency, strength, scope, scope_ref }) => ({
      id, memory, title, type, importance, x, y, node_kind, scope, scope_ref, fact_ids: fact_ids ? [...fact_ids] : undefined, effective_frequency, strength,
    }))
  }

  /**
   * Pins a node to a position chosen by the user and stops its velocity.
   *
   * 将节点固定到用户指定的位置并清零速度。
   *
   * @param id - Node id. / 节点 ID。
   * @param x - Target horizontal coordinate. / 目标横坐标。
   * @param y - Target vertical coordinate. / 目标纵坐标。
   */
  moveNode(id: string, x: number, y: number) {
    const point = this.particles.find(point => point.id === id)
    if (!point || !Number.isFinite(x) || !Number.isFinite(y)) return
    point.x = point.anchorX = clamp(x, 70 + point.radius, 1200 - point.radius)
    point.y = point.anchorY = clamp(y, 45 + point.radius, 700 - point.radius)
    point.vx = point.vy = 0
  }

  /**
   * Advances the simulation by a wall-clock delta, split into fixed sub-steps.
   *
   * 按真实时间增量推进模拟，并拆分为固定子步。
   *
   * @param milliseconds - Elapsed time since the previous frame. / 距上一帧的经过时间。
   * @param held - Ids of nodes currently held by the pointer. / 当前被指针按住的节点 ID 集合。
   */
  step(milliseconds: number, held: ReadonlySet<string> = new Set()) {
    if (!Number.isFinite(milliseconds) || milliseconds <= 0) return
    // A resumed tab must never catch up a long hidden interval in one jump.
    // 标签页恢复时，绝不能用一次跳跃补上长时间隐藏期间的间隔。
    const duration = Math.min(milliseconds, 50)
    const steps = Math.ceil(duration / (1000 / 60))
    const dt = duration / steps / (1000 / 60)
    for (let i = 0; i < steps; i++) this.integrate(dt, held)
  }

  /**
   * Integrates one sub-step of attraction, repulsion, springs and damping.
   *
   * 对一个子步积分吸引力、排斥力、弹簧力与阻尼。
   *
   * @param dt - Normalized sub-step length in frames. / 以帧为单位的归一化子步长度。
   * @param held - Ids of nodes currently held by the pointer. / 当前被指针按住的节点 ID 集合。
   */
  private integrate(dt: number, held: ReadonlySet<string>) {
    this.time += dt / 60
    const globalAttraction = .000015 / Math.sqrt(Math.max(1, this.particles.length))
    const categorySizes = new Map<string, number>()
    for (const point of this.particles) categorySizes.set(point.type, (categorySizes.get(point.type) ?? 0) + 1)
    for (const point of this.particles) {
      // Distinct slow phases keep the field alive after the initial spring settling.
      // 各不相同的缓慢相位让力场在初始弹簧稳定后仍保持活性。
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
        // 类别偏好只是布局力，绝不表示虚构的图谱关系。
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
