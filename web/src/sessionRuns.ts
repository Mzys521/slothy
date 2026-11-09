/** Session-owned live requests, independent of mounted pages. / 独立于页面生命周期的会话运行状态。 */
import { reactive, ref, shallowReactive } from 'vue'

export const runningSessions = reactive(new Set<string>())
export const runningRequests = shallowReactive(new Map<string, AbortController>())
export const runningTurnIds = reactive<Record<string, string>>({})
export const pendingSessionActions = reactive(new Set<string>())
export const pendingSessionCommands = reactive(new Set<string>())
export const liveReplies = ref<Record<string, string>>({})
export const liveReasoning = ref<Record<string, string>>({})

export function beginSessionRequest(session: string, turn: string): AbortController {
  if (runningRequests.has(session)) throw new Error('This conversation already has an active request.')
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
