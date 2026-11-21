/** Session-owned live requests, independent of mounted pages. / 独立于页面生命周期的会话运行状态。 */
import { reactive, ref, shallowReactive } from 'vue'

export const runningSessions = reactive(new Set<string>())
export const runningRequests = shallowReactive(new Map<string, AbortController>())
export const runningTurnIds = reactive<Record<string, string>>({})
export const pendingSessionActions = reactive(new Set<string>())
export const pendingSessionCommands = reactive(new Set<string>())
export const liveReplies = ref<Record<string, string>>({})
export const liveReasoning = ref<Record<string, string>>({})
export const sessionVersions = reactive<Record<string, number>>({})

/**
 * Reads the current data version of a session.
 * 读取会话当前的数据版本。
 *
 * @param session - Session id. / 会话 ID。
 * @returns Monotonic version; reads captured with an older value must not write. /
 *     单调递增的版本；以此前版本开始的读取不得写入。
 */
export function sessionVersion(session: string): number {
  return sessionVersions[session] ?? 0
}

/**
 * Marks a session's data as changed so earlier reads become stale.
 * 标记会话数据已变化，使更早的读取失效。
 *
 * A version is only ever raised, never deleted: removing the entry would let an
 * in-flight read whose version was 0 match the reset value again.
 * 版本只增不删：删除条目会让版本为 0 的在途读取再次匹配重置值。
 *
 * @param session - Session id. / 会话 ID。
 * @returns The new version. / 新的版本值。
 */
export function bumpSessionVersion(session: string): number {
  const version = sessionVersion(session) + 1
  sessionVersions[session] = version
  return version
}

export function beginSessionRequest(session: string, turn: string): AbortController {
  if (runningRequests.has(session)) throw new Error('This conversation already has an active request.')
  // The whole run and its completion window belongs to this version.
  // 整个运行及其完成窗口都属于该版本。
  bumpSessionVersion(session)
  const controller = new AbortController()
  runningRequests.set(session, controller)
  runningTurnIds[session] = turn
  runningSessions.add(session)
  return controller
}

export function finishSessionRequest(session: string, controller: AbortController): boolean {
  // An old completion must never clear a newer request's stop handle or busy state.
  // 旧请求的完成回调不得清理新请求的停止句柄或忙碌状态。
  if (runningRequests.get(session) !== controller) return false
  const turn = runningTurnIds[session]
  if (turn) { delete liveReplies.value[turn]; delete liveReasoning.value[turn] }
  runningRequests.delete(session)
  runningSessions.delete(session)
  delete runningTurnIds[session]
  return true
}
