/**
 * Sandboxed preload bridge: exposes a frozen, minimal `window.desktop` API to the trusted main frame only.
 *
 * 沙箱预加载桥：仅向可信主框架暴露冻结且最小化的 `window.desktop` API。
 */

const { contextBridge, ipcRenderer } = require('electron')

// Sub-frames never receive the bridge, so embedded content cannot reach IPC.
// 子框架永远拿不到该桥，从而内嵌内容无法触达 IPC。
if (process.isMainFrame) {
  const prefix = '--desktop-api-origin='
  const apiOrigin = process.argv.find(argument => argument.startsWith(prefix))?.slice(prefix.length)

  contextBridge.exposeInMainWorld('desktop', Object.freeze({
    platform: process.platform,
    apiOrigin,
    getAppInfo: () => ipcRenderer.invoke('desktop:get-app-info'),
    minimize: () => ipcRenderer.invoke('desktop:minimize'),
    toggleMaximize: () => ipcRenderer.invoke('desktop:toggle-maximize'),
    isMaximized: () => ipcRenderer.invoke('desktop:is-maximized'),
    close: () => ipcRenderer.invoke('desktop:close'),
    selectDirectory: () => ipcRenderer.invoke('desktop:select-directory'),
    writeClipboard: text => ipcRenderer.invoke('desktop:write-clipboard', text),
    // Subscribes to maximize state; the returned function detaches the listener.
    // 订阅最大化状态；返回的函数用于解绑监听。
    onMaximized: listener => {
      const handler = (_event, value) => { if (typeof value === 'boolean') listener(value) }
      ipcRenderer.on('desktop:maximized', handler)
      return () => ipcRenderer.removeListener('desktop:maximized', handler)
    },
  }))
}
