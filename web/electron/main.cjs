/**
 * Main process entry point: owns window creation, the backend child lifecycle, IPC and request hardening.
 *
 * 主进程入口：负责窗口创建、后端子进程生命周期、IPC 与请求加固。
 */

const { app, BrowserWindow, clipboard, dialog, ipcMain, net, screen, session, shell } = require('electron')
const path = require('node:path')
const { fileURLToPath } = require('node:url')
const { startBackend, stopBackend } = require('./backend.cjs')
const { selectWorkingDirectory } = require('./directory.cjs')
const desktop = require('../desktop.config.cjs')
const { displayVersion } = require('../package.json')

const distDirectory = path.join(__dirname, '..', 'dist')
const indexFile = path.join(distDirectory, 'index.html')
const devServer = !app.isPackaged ? process.env.VITE_DEV_SERVER_URL : undefined
let api = new URL(!app.isPackaged && process.env.DESKTOP_API_ORIGIN || desktop.apiOrigin)
if (!['http:', 'https:'].includes(api.protocol) || api.pathname !== '/' || api.username || api.password) {
  throw new Error('DESKTOP_API_ORIGIN must be an HTTP(S) origin without a path or credentials.')
}
if (devServer) {
  const url = new URL(devServer)
  if (url.protocol !== 'http:' || !['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname)) {
    throw new Error('The Vite development server must use a loopback HTTP address.')
  }
}

let mainWindow
let backend
let quitting = false

if (app.isPackaged) {
  app.commandLine.removeSwitch('remote-debugging-port')
  app.commandLine.removeSwitch('remote-debugging-pipe')
  app.commandLine.removeSwitch('inspect')
  app.commandLine.removeSwitch('inspect-brk')
}

/**
 * Reports whether a URL is the trusted application page (dev server origin or the packaged index file).
 *
 * 判断某个 URL 是否为可信应用页面（开发服务器源或打包后的 index 文件）。
 *
 * @param url - Candidate URL string. / 待判断的 URL 字符串。
 * @returns `true` for the real app page, `false` for anything else. / 真正的应用页面返回 `true`，其余返回 `false`。
 */
function isAppPage(url) {
  try {
    const target = new URL(url)
    if (devServer) return target.origin === new URL(devServer).origin
    return target.protocol === 'file:' && path.resolve(fileURLToPath(target)) === path.resolve(indexFile)
  } catch {
    return false
  }
}

/**
 * Reports whether a frame is the top-level app frame, never a nested or remote frame.
 *
 * 判断某个 frame 是否为顶层应用 frame，而非嵌套或远程 frame。
 *
 * @param frame - Electron `WebFrameMain` (may be null). / Electron `WebFrameMain`（可能为 null）。
 * @returns `true` only for the trusted top frame. / 只有可信顶层 frame 才返回 `true`。
 */
function isAppFrame(frame) {
  return Boolean(frame && !frame.parent && isAppPage(frame.url))
}

/**
 * Opens an http(s) link in the operating system browser; other protocols are ignored.
 *
 * 在操作系统浏览器中打开 http(s) 链接；其他协议一律忽略。
 *
 * @param url - Target link requested by the page. / 页面请求的目标链接。
 */
function openExternal(url) {
  try {
    const target = new URL(url)
    if (['https:', 'http:'].includes(target.protocol)) {
      void shell.openExternal(target.href).catch(error => console.error('Could not open link:', error.message))
    }
  } catch {
    // Unsupported navigation requests stay inside the sandbox.
    // 不支持的导航请求会被限制在沙箱内。
  }
}

/**
 * Installs the origin/token rewrite rules and the custom `file` protocol used by packaged builds.
 *
 * 安装打包构建所需的源/令牌改写规则与自定义 `file` 协议。
 */
function configureProductionRequests() {
  const desktopSession = session.defaultSession
  const filter = { urls: [api.origin + '/api/v1/*'] }

  desktopSession.webRequest.onBeforeSendHeaders(filter, (details, callback) => {
    if (details.frame?.parent) return callback({ cancel: true })
    if (!isAppFrame(details.frame)) return callback({ cancel: true })
    const headers = { ...details.requestHeaders }
    for (const name of Object.keys(headers)) {
      if (name.toLowerCase() === 'origin') delete headers[name]
    }
    // Match an origin already accepted by the existing local FastAPI server.
    // 匹配本地 FastAPI 服务器已接受过的来源。
    headers.Origin = desktop.apiRequestOrigin
    if (backend) headers['X-Slothy-Token'] = backend.token
    callback({ requestHeaders: headers })
  })

  desktopSession.webRequest.onHeadersReceived(filter, (details, callback) => {
    if (!isAppFrame(details.frame)) return callback({})
    const headers = { ...details.responseHeaders }
    for (const name of Object.keys(headers)) {
      if (name.toLowerCase() === 'access-control-allow-origin') delete headers[name]
    }
    headers['Access-Control-Allow-Origin'] = [devServer ? new URL(devServer).origin : 'null']
    callback({ responseHeaders: headers })
  })

  desktopSession.protocol.handle('file', async request => {
    const url = new URL(request.url)
    if (url.hostname) return new Response('Not found', { status: 404 })
    // Avatar image URLs do not pass through window.fetch.
    // 头像图片 URL 不经过 window.fetch。
    const apiPath = url.pathname.replace(/^\/[a-zA-Z]:(?=\/api\/v1\/)/, '')
    if (apiPath === '/api/v1/account/avatar') {
      try {
        return await fetch(new URL(apiPath + url.search, api.origin).href, {
          headers: backend ? { 'X-Slothy-Token': backend.token } : {},
        })
      } catch {
        return new Response('The backend is unavailable.', { status: 502 })
      }
    }
    const file = fileURLToPath(url)
    const relative = path.relative(distDirectory, file)
    if (relative.startsWith('..') || path.isAbsolute(relative)) {
      return new Response('Not found', { status: 404 })
    }
    return net.fetch(request, { bypassCustomProtocolHandlers: true })
  })
}

/**
 * Creates the hardened frameless window and loads the dev server or the packaged page.
 *
 * 创建加固后的无边框窗口，并加载开发服务器或打包页面。
 *
 * @returns Resolves once the window is shown. / 窗口显示后完成。
 */
async function createWindow() {
  const area = screen.getPrimaryDisplay().workAreaSize
  mainWindow = new BrowserWindow({
    ...desktop.window,
    width: Math.min(desktop.window.width, area.width),
    height: Math.min(desktop.window.height, area.height),
    title: desktop.productName,
    icon: path.join(app.getAppPath(), desktop.icon),
    show: false,
    frame: false,
    titleBarStyle: 'hidden',
    roundedCorners: true,
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
      webviewTag: false,
      devTools: !app.isPackaged,
      additionalArguments: ['--desktop-api-origin=' + api.origin],
    },
  })
  mainWindow.on('closed', () => { mainWindow = undefined })
  mainWindow.on('page-title-updated', event => event.preventDefault())
  mainWindow.on('maximize', () => mainWindow.webContents.send('desktop:maximized', true))
  mainWindow.on('unmaximize', () => mainWindow.webContents.send('desktop:maximized', false))
  mainWindow.webContents.setWindowOpenHandler(details => {
    if (isAppPage(details.referrer.url)) openExternal(details.url)
    return { action: 'deny' }
  })
  mainWindow.webContents.on('will-navigate', (event, url) => {
    if (isAppPage(url)) return
    event.preventDefault()
    openExternal(url)
  })
  mainWindow.webContents.on('will-attach-webview', event => event.preventDefault())
  mainWindow.webContents.on('did-fail-load', (_event, code, message, url, isMainFrame) => {
    if (isMainFrame && code !== -3) console.error('Page load failed:', code, message, url)
  })

  if (devServer) await mainWindow.loadURL(devServer)
  else await mainWindow.loadFile(indexFile)
  mainWindow.show()
}

app.setName(desktop.productName)
app.setAppUserModelId(desktop.appId)
if (!app.isPackaged && process.env.DESKTOP_USER_DATA) app.setPath('userData', process.env.DESKTOP_USER_DATA)
const ownsInstance = app.requestSingleInstanceLock()
if (!ownsInstance) app.quit()
app.on('second-instance', () => {
  if (mainWindow) { if (mainWindow.isMinimized()) mainWindow.restore(); mainWindow.show(); mainWindow.focus() }
})

app.whenReady().then(async () => {
  if (!ownsInstance) return
  if (app.isPackaged || !process.env.DESKTOP_API_ORIGIN) {
    backend = await startBackend({ resourcesPath: process.resourcesPath, userData: app.getPath('userData'), development: !app.isPackaged })
    api = new URL(backend.origin)
    backend.child.once('exit', code => {
      if (!quitting) {
        dialog.showErrorBox('slothy', '本地服务意外退出 (' + code + ')。请重新打开 slothy。\n' + backend.getLastError())
        app.quit()
      }
    })
  }
  session.defaultSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false))
  session.defaultSession.setPermissionCheckHandler(() => false)
  configureProductionRequests()
  ipcMain.handle('desktop:get-app-info', event => {
    if (!isAppFrame(event.senderFrame)) throw new Error('Untrusted IPC sender.')
    return { name: desktop.productName, version: displayVersion || app.getVersion(), platform: process.platform }
  })
  /**
   * Resolves the sender window after validating that the IPC frame is trusted.
   *
   * 在确认 IPC 来源 frame 可信后解析发送方窗口。
   *
   * @param event - Electron IPC event. / Electron IPC 事件。
   * @returns Owning window, or `null` when unavailable. / 所属窗口；不可用时返回 `null`。
   */
  const fromWindow = event => {
    if (!isAppFrame(event.senderFrame)) throw new Error('Untrusted IPC sender.')
    return BrowserWindow.fromWebContents(event.sender)
  }
  // Window-control and trusted-desktop IPC handlers; every one validates the sender frame.
  // 窗口控制与可信桌面 IPC 处理器；每个处理器都会校验发送方 frame。
  ipcMain.handle('desktop:minimize', event => { fromWindow(event)?.minimize() })
  ipcMain.handle('desktop:toggle-maximize', event => {
    const window = fromWindow(event)
    if (window?.isMaximized()) window.unmaximize()
    else window?.maximize()
    return window?.isMaximized() ?? false
  })
  ipcMain.handle('desktop:is-maximized', event => fromWindow(event)?.isMaximized() ?? false)
  ipcMain.handle('desktop:close', event => { fromWindow(event)?.close() })
  // Directory selection stays in the main process so the renderer only receives a path.
  // 目录选择保留在主进程，渲染进程只会收到路径。
  ipcMain.handle('desktop:select-directory', async event => {
    return selectWorkingDirectory(dialog, fromWindow(event))
  })
  // Clipboard writes are validated and bounded before reaching the native clipboard.
  // 剪贴板写入在进入原生剪贴板前先做类型与长度校验。
  ipcMain.handle('desktop:write-clipboard', (event, text) => {
    fromWindow(event)
    if (typeof text !== 'string' || text.length > 2_000_000) throw new Error('Invalid clipboard text.')
    clipboard.writeText(text)
  })
  await createWindow()
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      void createWindow().catch(error => dialog.showErrorBox('slothy', error.message))
    }
  })
}).catch(error => {
  console.error(error)
  dialog.showErrorBox('slothy 启动失败', error.message)
  app.quit()
})

app.on('before-quit', event => {
  if (quitting) return
  quitting = true
  if (backend) {
    event.preventDefault()
    void stopBackend(backend).finally(() => app.quit())
  }
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})
