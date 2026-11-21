/**
 * Read ownership: only the latest read of a target may publish its result.
 *
 * 读请求所有权：只有某个目标的最新读取可以提交其结果。
 */

/** Identity of one read attempt. / 一次读取尝试的标识。 */
export interface ReadToken<T> {
  readonly target: T
  readonly generation: number
}

/**
 * Tracks one read generation per target so late responses cannot overwrite newer state.
 *
 * 按目标维护读取代次，使迟到的响应不能覆盖更新的状态。
 */
export class ReadGuard<T> {
  private readonly generations = new Map<T, number>()

  /**
   * Starts a new read for a target and invalidates every earlier read of it.
   *
   * 为目标开始新的读取，并作废该目标此前所有读取。
   *
   * @param target - Target value the read belongs to. / 该读取所属的目标值。
   * @returns Token to check before writing. / 写入前用于校验的令牌。
   */
  begin(target: T): ReadToken<T> {
    const generation = (this.generations.get(target) ?? 0) + 1
    this.generations.set(target, generation)
    return { target, generation }
  }

  /**
   * Reports whether a read still owns the target it started for.
   *
   * 判断某次读取是否仍拥有其开始时的目标。
   *
   * @param token - Token captured when the read started. / 读取开始时捕获的令牌。
   * @param current - Target value at write time. / 写入时的目标值。
   * @returns True when the target is unchanged and no newer read started. /
   *     目标未变且没有更新的读取开始时为 true。
   */
  owns(token: ReadToken<T>, current: T): boolean {
    return Object.is(token.target, current) && (this.generations.get(current) ?? 0) === token.generation
  }

  /**
   * Invalidates the current read of a target without starting another one.
   *
   * 作废目标的当前读取，但不开始新读取。
   *
   * @param target - Target whose read is abandoned. / 放弃读取的目标。
   */
  invalidate(target: T): void {
    this.generations.set(target, (this.generations.get(target) ?? 0) + 1)
  }
}
