const { contextBridge, ipcRenderer } = require('electron')

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
    onMaximized: listener => {
      const handler = (_event, value) => { if (typeof value === 'boolean') listener(value) }
      ipcRenderer.on('desktop:maximized', handler)
      return () => ipcRenderer.removeListener('desktop:maximized', handler)
    },
  }))
}
