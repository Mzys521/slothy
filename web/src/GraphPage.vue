<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref, shallowRef, watch, useId } from 'vue'
import { select } from 'd3-selection'
import { zoom, zoomIdentity } from 'd3-zoom'
import { Hand, Maximize2, Minus, Network, Pause, Play, Plus, RefreshCw, X } from 'lucide-vue-next'
import { type GraphPoint, type MemoryGraphResponse } from './graph'
import { GraphMotion } from './graphMotion'
import { t } from './preferences'
import { activeWorkspace, personalWorkspaceId } from './chat'
import { ApiError, memoryScope, workRequest } from './workApi'

const selected = defineModel<string>({ required: true })
const emit = defineEmits<{ detail: [id: string] }>()
const canvas = ref<SVGSVGElement>()
const graphLayer = ref<SVGGElement>()
const transform = ref('')
const scale = ref(1)
const pan = ref(true)
const hovered = ref('')
const focused = ref('')
const dragging = ref('')
const motionEnabled = ref(true)
const reducedMotion = ref(false)
const pageHidden = ref(false)
const panning = ref(false)
const live = ref<MemoryGraphResponse>()
const loading = ref(false)
const loadError = ref('')
const focusWarning = ref('')
const limit = ref(100)
let graphRevision = 0
const target = ref('')
const relation = ref('related')
const directed = ref(false)
const saving = ref(false)
const relationError = ref('')
const deleting = ref('')
const arrowId = 'graph-arrow-' + useId().replaceAll(':', '')
const memoryTypes = [
  { id: 'episodic', en: 'Episodic', zh: '情景记忆' },
  { id: 'semantic', en: 'Semantic', zh: '语义记忆' },
  { id: 'preference', en: 'Preference', zh: '偏好记忆' },
  { id: 'identity', en: 'Identity', zh: '身份' },
  { id: 'skill', en: 'Skill', zh: '技能' },
] as const
const available = shallowRef<GraphPoint[]>([])
const simulation = new GraphMotion()
const animating = computed(() => motionEnabled.value && !reducedMotion.value && !pageHidden.value && !panning.value)
const motionState = computed(() => reducedMotion.value ? 'reduced' : !motionEnabled.value ? 'paused' : pageHidden.value ? 'hidden' : panning.value ? 'interacting' : available.value.length ? 'running' : 'idle')
// Animation loop bookkeeping: rAF handle, last frame time, mount flag, motion media query and active drag state.
// 动画循环状态：rAF 句柄、上一帧时间、挂载标记、动态效果媒体查询与当前拖拽状态。
let frame: number | undefined
let lastFrame = 0
let mounted = false
let motionQuery: MediaQueryList | undefined
let drag: { id: string; pointer: number; element: Element; startX: number; startY: number; offsetX: number; offsetY: number; moved: boolean } | undefined

/** Cancel any queued animation frame and reset timing. / 取消已排队的动画帧并重置计时。 */
function stopMotion() {
  if (frame !== undefined) cancelAnimationFrame(frame)
  frame = undefined; lastFrame = 0
}
/**
 * Advance the force simulation, publish a snapshot and queue the next frame.
 * 推进力导向模拟，发布节点快照并排队下一帧。
 *
 * @param timestamp - Frame time supplied by requestAnimationFrame. / requestAnimationFrame 提供的帧时间。
 */
function animate(timestamp: number) {
  frame = undefined
  if (!mounted || !animating.value || !available.value.length) return
  if (!lastFrame) lastFrame = timestamp
  const elapsed = timestamp - lastFrame
  if (elapsed >= 32) {
    simulation.step(elapsed, new Set([selected.value, hovered.value, focused.value, dragging.value]))
    available.value = simulation.snapshot()
    lastFrame = timestamp
  }
  frame = requestAnimationFrame(animate)
}
/** Start the animation loop only while motion is allowed and nodes exist. / 仅在允许动态效果且存在节点时启动动画循环。 */
function syncMotion() {
  if (!mounted || !animating.value || !available.value.length) { stopMotion(); return }
  if (frame === undefined) frame = requestAnimationFrame(animate)
}
/** Mirror the system reduced-motion preference into component state. / 将系统“减少动态效果”偏好同步到组件状态。 */
function motionPreference() { reducedMotion.value = motionQuery?.matches ?? false }
/** Pause motion while the document is hidden and drop any active drag. / 页面隐藏时暂停动态效果并结束当前拖拽。 */
function visibilityChanged() {
  pageHidden.value = document.hidden
  if (pageHidden.value) releaseDrag()
}
watch(animating, syncMotion)
// Feed new server data into the simulation and publish the first layout snapshot.
// 将新的服务端数据送入力导向模拟，并发布首帧布局快照。
watch(live, graph => {
  simulation.update(graph ?? { nodes: [], edges: [] })
  available.value = simulation.snapshot()
  syncMotion()
})
// Draw each edge between node borders rather than centres, trimming both ends by the node radius.
// 连线绘制在节点边缘而非圆心，两端按节点半径回收。
const edges = computed(() => {
  const points = new Map(available.value.map(point => [point.id, point]))
  return (live.value?.edges ?? []).flatMap(edge => {
    const source = points.get(edge.source)
    const target = points.get(edge.target)
    if (!source || !target) return []
    const distance = Math.hypot(target.x - source.x, target.y - source.y) || 1
    const dx = (target.x - source.x) / distance, dy = (target.y - source.y) / distance
    return [{ ...edge, source, target, x1: source.x + dx * radius(source.importance), y1: source.y + dy * radius(source.importance),
      x2: target.x - dx * (radius(target.importance) + 4), y2: target.y - dy * (radius(target.importance) + 4) }]
  })
})
const neighbors = computed(() => new Set(edges.value.flatMap(({ source, target }) => source.id === selected.value ? [target.id] : target.id === selected.value ? [source.id] : [])))
const selectedPoint = computed(() => available.value.find(point => point.id === selected.value))
const connected = computed(() => (live.value?.edges ?? []).filter(edge => edge.source === selected.value || edge.target === selected.value))
// Reload when a focus id asks for a memory that is not inside the current graph window.
// 当请求定位的记忆不在当前图谱窗口内时重新加载。
watch(selected, () => {
  target.value = ''; deleting.value = ''; relationError.value = ''; focusWarning.value = ''
  if (selected.value && !loading.value && !available.value.some(point => point.id === selected.value)) void loadGraph()
})
// Zoom/pan: the wheel always zooms; dragging starts outside nodes so node gestures keep working.
// 缩放与平移：滚轮始终缩放；仅在节点之外启动拖拽，避免干扰节点手势。
const zoomBehavior = zoom<SVGSVGElement, unknown>()
  .scaleExtent([.55, 3.5])
  .filter(event => event.type === 'wheel' || (pan.value && !event.button && !event.target?.closest?.('.graph-node')))
  .on('start', () => { panning.value = true })
  .on('zoom', event => {
    transform.value = event.transform.toString()
    scale.value = event.transform.k
  })
  .on('end', () => { panning.value = false })

// Wire reduced-motion, visibility and d3-zoom listeners, then start the simulation.
// 绑定减少动态效果、页面可见性与 d3 缩放监听，然后启动力导向模拟。
onMounted(() => {
  mounted = true
  motionQuery = window.matchMedia('(prefers-reduced-motion: reduce)')
  motionPreference(); visibilityChanged()
  motionQuery.addEventListener('change', motionPreference)
  document.addEventListener('visibilitychange', visibilityChanged)
  if (canvas.value) select(canvas.value).call(zoomBehavior).on('dblclick.zoom', null)
  syncMotion()
})
// Detach every listener and animation frame registered on mount.
// 卸载时移除挂载阶段注册的所有监听与动画帧。
onBeforeUnmount(() => {
  mounted = false; stopMotion()
  motionQuery?.removeEventListener('change', motionPreference)
  document.removeEventListener('visibilitychange', visibilityChanged)
  releaseDrag()
  if (canvas.value) select(canvas.value).on('.zoom', null)
})
watch(activeWorkspace, () => { ++graphRevision; loading.value = false; live.value = undefined; selected.value = ''; void loadGraph() })
void loadGraph()
/** Load the graph window, retrying without focus when the focused memory is unavailable. / 加载图谱窗口；定位的记忆不可用时退回普通加载。 */
async function loadGraph() {
  if (loading.value) return
  const revision = ++graphRevision
  loading.value = true
  loadError.value = ''
  try {
    const query = '/graph?limit=' + limit.value + (activeWorkspace.value !== personalWorkspaceId ? '&project_id=' + encodeURIComponent(activeWorkspace.value) : '')
    try {
      const graph = await workRequest<MemoryGraphResponse>(query + (selected.value ? '&focus_id=' + encodeURIComponent(selected.value) : ''))
      if (revision === graphRevision) live.value = graph
    } catch (error) {
      if (!(error instanceof ApiError && error.code === 'GRAPH_FOCUS_UNAVAILABLE')) throw error
      focusWarning.value = t('The requested memory is archived or no longer available in the active graph.', '要定位的记忆已归档或不在有效图谱中。')
      const graph = await workRequest<MemoryGraphResponse>(query)
      if (revision === graphRevision) live.value = graph
    }
  } catch (error) {
    if (revision === graphRevision) loadError.value = error instanceof Error ? error.message : t('Could not load the memory graph.', '无法加载记忆图谱。')
  } finally {
    if (revision === graphRevision) loading.value = false
  }
}
/**
 * Build a stable DOM key for one edge.
 * 为一条连线生成稳定的 DOM 键。
 *
 * @param edge - Graph edge from the API response. / 接口返回的图谱连线。
 * @returns "source:target" key. / "source:target" 键。
 */
function edgeKey(edge: MemoryGraphResponse['edges'][number]) { return edge.id ?? edge.source + ':' + edge.target + ':' + edge.relation }
/**
 * Describe an edge from the selected node's point of view, including its direction.
 * 从当前选中节点的视角描述一条连线及其方向。
 *
 * @param edge - Edge connected to the selected node. / 与选中节点相连的连线。
 * @returns Localized direction marker plus the other end's title. / 本地化方向标记与另一端节点标题。
 */
function linkedLabel(edge: MemoryGraphResponse['edges'][number]) {
  const other = available.value.find(point => point.id === (edge.source === selected.value ? edge.target : edge.source))
  return (edge.directed ? (edge.source === selected.value ? '→ ' : '← ') : '↔ ') + (other?.title ?? other?.id ?? '')
}
/** Persist a new manual relationship between the selected and target memories. / 保存当前记忆与目标记忆之间的手动关联。 */
async function saveRelationship() {
  if (!selected.value || !target.value || !relation.value.trim() || saving.value) return
  saving.value = true; relationError.value = ''
  try {
    await workRequest('/graph/links', 'PUT', { source_id: selected.value, target_id: target.value, relation: relation.value.trim(), relevance: .7, directed: directed.value })
    target.value = ''; await loadGraph()
  } catch (error) { relationError.value = error instanceof Error ? error.message : String(error) }
  finally { saving.value = false }
}
/**
 * Delete one relationship and reload the graph.
 * 删除一条关联并重新加载图谱。
 *
 * @param edge - Edge to remove. / 要解除的连线。
 */
async function removeRelationship(edge: MemoryGraphResponse['edges'][number]) {
  saving.value = true; relationError.value = ''
  try {
    await workRequest('/graph/links?source_id=' + encodeURIComponent(edge.source) + '&target_id=' + encodeURIComponent(edge.target) + '&directed=' + edge.directed, 'DELETE')
    deleting.value = ''; await loadGraph()
  } catch (error) { relationError.value = error instanceof Error ? error.message : String(error) }
  finally { saving.value = false }
}
/**
 * Human-readable node label, falling back to a generic related-node label.
 * 生成可读的节点标签，缺省时回退为通用“关联节点”。
 *
 * @param point - Node layout entry. / 节点布局条目。
 * @returns Localized display label. / 本地化显示标签。
 */
function nodeLabel(point: GraphPoint) {
  return point.title ? t(point.title, point.zhTitle ?? point.title) : t('Related node', '关联节点')
}
/**
 * Clear the selection when the click did not land on a node.
 * 当点击未落在节点上时清除选中状态。
 *
 * @param event - Click event from the SVG canvas. / SVG 画布上的点击事件。
 */
function clearFromCanvas(event: MouseEvent) {
  if (!(event.target as Element).closest('.graph-node')) selected.value = ''
}
/** Restore the default zoom and pan transform. / 恢复默认的缩放与平移变换。 */
function reset() {
  if (canvas.value) select(canvas.value).call(zoomBehavior.transform, zoomIdentity)
}
/**
 * Zoom the canvas by a multiplicative factor.
 * 按倍数缩放画布。
 *
 * @param factor - Zoom multiplier applied by d3-zoom. / d3-zoom 应用的缩放倍数。
 */
function zoomBy(factor: number) {
  if (canvas.value) select(canvas.value).call(zoomBehavior.scaleBy, factor)
}
/**
 * Map memory importance to a node radius in SVG units.
 * 将记忆重要性映射为 SVG 节点半径。
 *
 * @param importance - Importance in the 0–1 range. / 0–1 区间的重要性。
 * @returns Circle radius in SVG units. / SVG 单位下的圆半径。
 */
function radius(importance: number) {
  return 2.5 + 22 * importance
}
/**
 * Convert a pointer event to graph-layer coordinates, ignoring the current pan/zoom transform.
 * 将指针事件转换为不受当前缩放/平移影响的图层坐标。
 *
 * @param event - Pointer event from the canvas. / 画布上的指针事件。
 * @returns Graph-layer position, or undefined when the layer is not mounted. / 图层坐标；图层未挂载时返回 undefined。
 */
function pointerPosition(event: PointerEvent) {
  const matrix = graphLayer.value?.getScreenCTM()
  return matrix ? new DOMPoint(event.clientX, event.clientY).matrixTransform(matrix.inverse()) : undefined
}
/**
 * Begin dragging a node once the pointer is captured.
 * 捕获指针后开始拖拽节点。
 *
 * @param event - Pointerdown event on the node. / 节点上的 pointerdown 事件。
 * @param point - Node layout entry being dragged. / 被拖拽的节点布局条目。
 */
function startNodeDrag(event: PointerEvent, point: GraphPoint) {
  if (event.button !== 0 || !event.isPrimary || drag) return
  const position = pointerPosition(event)
  if (!position) return
  const element = event.currentTarget as Element
  drag = { id: point.id, pointer: event.pointerId, element, startX: event.clientX, startY: event.clientY,
    offsetX: position.x - point.x, offsetY: position.y - point.y, moved: false }
  dragging.value = point.id
  element.setPointerCapture(event.pointerId)
}
/**
 * Move the dragged node and publish the updated layout snapshot.
 * 移动被拖拽的节点并发布更新后的布局快照。
 *
 * @param event - Pointermove event tracked by the node element. / 节点元素跟踪的 pointermove 事件。
 */
function moveNode(event: PointerEvent) {
  if (!drag || drag.pointer !== event.pointerId) return
  if (!drag.moved && Math.hypot(event.clientX - drag.startX, event.clientY - drag.startY) < 3) return
  const position = pointerPosition(event)
  if (!position) return
  event.preventDefault()
  drag.moved = true
  simulation.moveNode(drag.id, position.x - drag.offsetX, position.y - drag.offsetY)
  available.value = simulation.snapshot()
}
/**
 * Finish the drag and select the node when it actually moved.
 * 结束拖拽；确实发生移动时选中该节点。
 *
 * @param event - Pointerup, pointercancel or lostpointercapture event. / pointerup、pointercancel 或 lostpointercapture 事件。
 */
function endNodeDrag(event: PointerEvent) {
  if (!drag || drag.pointer !== event.pointerId) return
  if (event.type === 'pointerup' && drag.moved) selected.value = drag.id
  releaseDrag()
}
/** Release pointer capture and clear the current drag state. / 释放指针捕获并清除当前拖拽状态。 */
function releaseDrag() {
  const current = drag
  drag = undefined; dragging.value = ''
  if (current?.element.hasPointerCapture(current.pointer)) current.element.releasePointerCapture(current.pointer)
}
/** Emit the selected memory id so the parent opens its detail view. / 抛出选中记忆 id，由父组件打开详情。 */
function openMemory() {
  if (selectedPoint.value?.memory) {
    emit('detail', selectedPoint.value.fact_ids?.[0] ?? selectedPoint.value.memory)
  }
}
</script>

<template>
  <main class="graph-page" :class="{ 'graph-motion-running': animating, 'graph-node-dragging': dragging }" :data-motion-state="motionState" @keydown.esc.stop="selected = ''">
    <header class="graph-toolbar">
      <h1><Network />{{ t('Graph', '图谱') }}</h1>
      <div class="graph-actions">
        <button class="icon-button" :disabled="reducedMotion || !available.length" :aria-pressed="motionEnabled && !reducedMotion"
          :title="reducedMotion ? t('Motion reduced by system preference', '系统已启用减少动态效果') : motionEnabled ? t('Pause motion', '暂停动态') : t('Resume motion', '恢复动态')"
          :aria-label="reducedMotion ? t('Motion reduced by system preference', '系统已启用减少动态效果') : motionEnabled ? t('Pause motion', '暂停动态') : t('Resume motion', '恢复动态')" @click="motionEnabled = !motionEnabled"><Pause v-if="motionEnabled && !reducedMotion" /><Play v-else /></button>
        <button class="icon-button" :disabled="loading" :aria-label="t('Refresh graph', '刷新图谱')" @click="loadGraph"><RefreshCw /></button>
        <button class="icon-button" :disabled="scale <= .55" :title="t('Zoom out', '缩小')" :aria-label="t('Zoom out', '缩小')" @click="zoomBy(.8)"><Minus /></button>
        <button class="icon-button" :disabled="scale >= 3.5" :title="t('Zoom in', '放大')" :aria-label="t('Zoom in', '放大')" @click="zoomBy(1.25)"><Plus /></button>
        <button class="icon-button" :aria-pressed="pan" :title="t('Pan canvas', '平移画布')" :aria-label="t('Pan canvas', '平移画布')" @click="pan = !pan"><Hand /></button>
        <button class="icon-button" :title="t('Fit graph', '适应画布')" :aria-label="t('Fit graph', '适应画布')" @click="reset"><Maximize2 /></button>
      </div>
    </header>
    <div v-if="focusWarning" class="graph-notice" role="status">{{ focusWarning }} <button class="text-link" @click="emit('detail', selected)">{{ t('View memory', '查看记忆') }}</button></div>
    <div v-if="live?.truncated" class="graph-notice" role="status">{{ t(`Showing ${live.nodes.length} of ${live.total} memories.`, `当前显示 ${live.nodes.length} / ${live.total} 条记忆。`) }}<button v-if="limit < 250" class="text-link" :disabled="loading" @click="limit = 250; loadGraph()">{{ t('Show more', '显示更多') }}</button><span v-else>{{ t('Open a memory detail to focus any memory beyond this view.', '可从记忆详情定位此视图之外的记忆。') }}</span></div>
    <div v-if="loading || loadError || !available.length" class="graph-status" role="status">
      <p>{{ loading ? t('Loading memories…', '正在加载记忆…') : loadError || t('No memories to display.', '暂无可显示的记忆。') }}</p>
      <button v-if="loadError" class="outline-button" @click="loadGraph">{{ t('Retry', '重试') }}</button>
    </div>
    <svg v-show="available.length" ref="canvas" class="graph-canvas" :class="{ pannable: pan }"
      viewBox="36 9 1235 738" :aria-label="t('Memory relationship graph', '记忆关系图谱')" tabindex="0" @click="clearFromCanvas">
      <defs><marker :id="arrowId" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L8 4L0 8Z" fill="context-stroke" /></marker></defs>
      <g ref="graphLayer" :transform="transform">
        <line v-for="edge in edges" :key="edge.id ?? edge.source.id + ':' + edge.target.id + ':' + edge.relation"
          :x1="edge.x1" :y1="edge.y1" :x2="edge.x2" :y2="edge.y2" :marker-end="edge.directed ? `url(#${arrowId})` : undefined"
          vector-effect="non-scaling-stroke" :class="{ 'edge-active': edge.source.id === selected || edge.target.id === selected,
            'edge-dim': selectedPoint && edge.source.id !== selected && edge.target.id !== selected }"><title>{{ edge.relation }}</title></line>
        <g v-for="point in available" :key="point.id" class="graph-node" :data-memory-type="point.type"
          :class="{ 'node-selected': selected === point.id, 'node-related': neighbors.has(point.id),
            'node-dim': selectedPoint && selected !== point.id && !neighbors.has(point.id) }"
          :transform="`translate(${point.x},${point.y})`" role="button" :tabindex="point.title ? 0 : -1"
          :aria-label="nodeLabel(point)"
          :aria-pressed="selected === point.id" @pointerenter="hovered = point.id" @pointerleave="hovered = ''"
          @focus="focused = point.id" @blur="focused = ''" @pointerdown.stop="startNodeDrag($event, point)" @pointermove="moveNode" @pointerup="endNodeDrag" @pointercancel="endNodeDrag" @lostpointercapture="endNodeDrag"
          @click.stop="selected = point.id" @keydown.enter.prevent="selected = point.id" @keydown.space.prevent="selected = point.id" @dblclick.stop="openMemory">
          <circle v-if="selected === point.id" class="node-halo" :r="radius(point.importance) + 7" />
          <circle v-if="selected === point.id" class="node-selection-ring" :r="radius(point.importance) + 4" vector-effect="non-scaling-stroke" />
          <circle class="node-dot" :r="radius(point.importance)" />
          <text v-if="hovered === point.id || selected === point.id || (point.title && neighbors.has(point.id))"
            :x="selected === point.id ? 0 : radius(point.importance) + 4"
            :y="selected === point.id ? radius(point.importance) + 22 : 13"
            :text-anchor="selected === point.id ? 'middle' : 'start'">{{ nodeLabel(point) }}</text>
        </g>
      </g>
    </svg>
    <aside v-if="selectedPoint" class="graph-selection" :aria-label="t('Memory relationships', '记忆关联')">
      <header><h2>{{ selectedPoint.title }}</h2><button class="icon-button" :aria-label="t('Close', '关闭')" @click="selected = ''"><X /></button></header>
      <button class="text-link" @click="openMemory">{{ t('Open memory details', '查看记忆详情') }}</button>
      <p v-if="selectedPoint.scope" class="muted">{{ memoryScope(selectedPoint.scope, t) }}</p><p v-if="selectedPoint.effective_frequency != null" class="muted">{{ t('Concept confirmations', '概念确认会话') }} {{ selectedPoint.effective_frequency }} · {{ t('Frequency strength', '频次强度') }} {{ (selectedPoint.strength ?? 0).toFixed(2) }}</p>
      <p v-if="!connected.length" class="muted">{{ t('No relationships yet.', '暂无关联。') }}</p>
      <div v-for="edge in connected" :key="edgeKey(edge)" class="graph-relationship">
        <p>{{ linkedLabel(edge) }}</p><small class="muted">{{ edge.relation === 'proficiency' ? t('Proficiency in', '熟练度对应技能') : edge.relation }} · {{ edge.origin === 'semantic' ? t('Semantic fact', '语义事实') : t('Manual', '手动关联') }}</small><small v-if="edge.effective_frequency != null">{{ t('Confirmed sessions', '有效确认会话') }} {{ edge.effective_frequency }} · {{ t('Frequency strength', '频次强度') }} {{ (edge.strength ?? 0).toFixed(2) }}</small>
        <button v-if="edge.origin === 'semantic' && edge.fact_id" class="text-link" @click="emit('detail', edge.fact_id)">{{ t('Open source fact', '查看来源事实') }}</button>
        <template v-else-if="deleting === edgeKey(edge)"><p>{{ t('Remove this relationship?', '解除这条关联？') }}</p><button class="quiet-button" :disabled="saving" @click="deleting = ''">{{ t('Cancel', '取消') }}</button><button class="quiet-button" :disabled="saving" @click="removeRelationship(edge)">{{ t('Confirm removal', '确认解除') }}</button></template>
        <button v-else class="text-link" :disabled="saving" @click="deleting = edgeKey(edge)">{{ t('Remove relationship', '解除关联') }}</button>
      </div>
      <form v-if="selectedPoint?.node_kind !== 'concept'" class="graph-link-form" @submit.prevent="saveRelationship">
        <label for="graph-target">{{ t('Link another memory', '关联另一条记忆') }}</label>
        <select id="graph-target" v-model="target" required><option value="" disabled>{{ t('Choose a memory', '选择记忆') }}</option><option v-for="point in available.filter(point => point.id !== selected && point.node_kind !== 'concept')" :key="point.id" :value="point.id">{{ point.title }}</option></select>
        <label for="graph-relation">{{ t('Relationship', '关联说明') }}</label><input id="graph-relation" v-model="relation" required maxlength="100">
        <label class="check-line"><input v-model="directed" type="checkbox">{{ t('Directed: current → selected memory', '有向关系：当前记忆 → 所选记忆') }}</label>
        <p v-if="relationError" role="alert">{{ relationError }}</p>
        <button class="accent-button" :disabled="saving || !target || !relation.trim()">{{ t('Save relationship', '保存关联') }}</button>
      </form>
    </aside>
    <footer class="graph-footer">
      <span v-for="type in memoryTypes" :key="type.id" :data-memory-type="type.id" class="graph-type"><i aria-hidden="true"></i>{{ t(type.en, type.zh) }}</span>
      <span class="graph-importance"><svg width="66" height="26" viewBox="0 0 66 26" aria-hidden="true"><circle cx="5" cy="13" r="4"/><circle cx="24" cy="13" r="7"/><circle cx="51" cy="13" r="12"/></svg>{{ t('Dot size · Importance', '点大小 · 重要性') }}</span>
      <span><svg width="46" height="24" viewBox="0 0 46 24" aria-hidden="true"><path d="M1 12h44"/></svg>{{ t('Line · Relationship', '连线 · 关联') }}</span>
      <button v-if="(selectedPoint?.memory || selectedPoint?.fact_ids?.length)" class="text-link graph-open" @click="openMemory">{{ t('Open memory', '打开记忆') }}</button>
    </footer>
  </main>
</template>
