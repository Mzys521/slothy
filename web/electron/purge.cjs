/**
 * Local data purge helpers: confirmation gate, managed paths and residual scanning.
 *
 * 本地数据清除辅助：确认口令、受管路径与残留检查。
 */

const fs = require('node:fs/promises')
const path = require('node:path')

const CONFIRMATION = '确认删除'
const MANAGED_DIRECTORIES = ['data', 'logs']

/**
 * Normalizes a user-entered confirmation string. / 规范化用户输入的确认文本。
 *
 * @param value - Raw input from the renderer. / 渲染层传入的原始文本。
 * @returns Trimmed text, or an empty string for non-strings. / 去空白文本；非字符串为空串。
 */
function normalizeConfirmation(value) {
  return typeof value === 'string' ? value.trim() : ''
}

/**
 * Reports whether the explicit confirmation phrase was entered. / 是否输入了显式确认口令。
 *
 * @param value - Raw input from the renderer. / 渲染层传入的原始文本。
 * @returns True only for the exact phrase. / 仅完全匹配时返回 true。
 */
function confirmed(value) {
  return normalizeConfirmation(value) === CONFIRMATION
}

/**
 * Lists application-managed directories under the Electron userData root.
 *
 * 列出 Electron userData 根目录下由应用管理的目录。
 *
 * @param userData - `app.getPath('userData')`. / Electron 用户数据根目录。
 * @returns Absolute managed paths. / 受管绝对路径列表。
 */
function managedPaths(userData) {
  return MANAGED_DIRECTORIES.map(name => path.join(userData, name))
}

/**
 * Removes one managed directory, tolerating a missing path.
 *
 * 删除单个受管目录；目标不存在时视为成功。
 *
 * @param target - Absolute managed path. / 受管绝对路径。
 * @returns Structured removal result. / 结构化删除结果。
 */
async function removeManagedPath(target) {
  try {
    await fs.rm(target, { recursive: true, force: true, maxRetries: 3, retryDelay: 120 })
  } catch (error) {
    return { path: target, ok: false, error: error instanceof Error ? error.message : String(error) }
  }
  return { path: target, ok: true }
}

/**
 * Removes every managed directory and reports failures plus any remaining paths.
 *
 * 删除全部受管目录，并报告失败项与残留路径。
 *
 * @param userData - `app.getPath('userData')`. / Electron 用户数据根目录。
 * @returns Removed paths, failed paths and residual managed paths. / 已删除、失败与残留路径。
 */
async function purgeManagedData(userData) {
  const removed = []
  const failed = []
  for (const target of managedPaths(userData)) {
    const result = await removeManagedPath(target)
    if (result.ok) removed.push(target)
    else failed.push({ path: target, error: result.error })
  }
  const remaining = []
  for (const target of managedPaths(userData)) {
    try {
      await fs.access(target)
      remaining.push(target)
    } catch {
      // Missing is the expected result. / 不存在即符合预期。
    }
  }
  return { removed, failed, remaining }
}

module.exports = {
  CONFIRMATION,
  confirmed,
  normalizeConfirmation,
  managedPaths,
  purgeManagedData,
}
