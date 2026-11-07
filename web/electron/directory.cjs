/**
 * Native directory picker used by the trusted main process for workspace selection.
 *
 * 原生目录选择器：主进程用它为工作区选择可信目录。
 */

const path = require('node:path')

/**
 * Opens a directory picker owned by the given window and normalizes the choice.
 *
 * 在指定窗口下打开目录选择框，并对选择结果做路径规范化。
 *
 * @param dialog - Electron `dialog` module (injected for testability). / Electron `dialog` 模块（为可测试性注入）。
 * @param window - Trusted `BrowserWindow` that owns the modal picker. / 拥有该模态选择框的可信 `BrowserWindow`。
 * @returns Resolved absolute directory path, or `null` when the user cancels. / 解析后的绝对目录路径；用户取消时返回 `null`。
 */
async function selectWorkingDirectory(dialog, window) {
  if (!window) throw new Error('No application window.')
  // The dialog title is user-facing copy and stays intentionally Chinese.
  // 对话框标题属于界面文案，按既有约定保持中文。
  const result = await dialog.showOpenDialog(window, {
    title: '选择工作区目录', properties: ['openDirectory', 'createDirectory'],
  })
  const selected = result.canceled ? undefined : result.filePaths[0]
  return typeof selected === 'string' && selected ? path.resolve(selected) : null
}

module.exports = { selectWorkingDirectory }
