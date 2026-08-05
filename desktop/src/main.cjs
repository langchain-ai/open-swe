const fs = require("node:fs")
const path = require("node:path")
const { app, BrowserWindow, Menu, dialog, session, shell } = require("electron")
const {
  isTrustedPermissionRequest,
  resolveDashboardUrl,
  validateDashboardUrl,
} = require("./config.cjs")

const isDevelopment = !app.isPackaged || process.argv.includes("--dev")
let dashboardUrl = null
let mainWindow = null
let setupWindow = null

function configPath() {
  return path.join(app.getPath("userData"), "desktop-config.json")
}

function readStoredDashboardUrl() {
  try {
    const config = JSON.parse(fs.readFileSync(configPath(), "utf8"))
    return typeof config.dashboardUrl === "string"
      ? validateDashboardUrl(config.dashboardUrl)
      : undefined
  } catch {
    return undefined
  }
}

function storeDashboardUrl(value) {
  const url = validateDashboardUrl(value.trim())
  fs.mkdirSync(path.dirname(configPath()), { recursive: true })
  fs.writeFileSync(configPath(), `${JSON.stringify({ dashboardUrl: url }, null, 2)}\n`, {
    mode: 0o600,
  })
  return url
}

function iconPath() {
  return app.isPackaged
    ? path.join(process.resourcesPath, "icon.png")
    : path.resolve(__dirname, "../resources/icon.png")
}

function errorPage(error) {
  const message = String(error?.message || error)
  const html = `<!doctype html>
<html>
  <head>
    <meta charset="utf-8">
    <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
    <meta name="color-scheme" content="light dark">
    <title>Open SWE</title>
    <style>
      body { margin: 0; min-height: 100vh; display: grid; place-items: center; font: 14px system-ui, sans-serif; }
      main { max-width: 520px; padding: 32px; text-align: center; }
      h1 { font-size: 22px; }
      p { color: GrayText; line-height: 1.5; overflow-wrap: anywhere; }
    </style>
  </head>
  <body>
    <main>
      <h1>Open SWE could not be reached</h1>
      <p>${escapeHtml(dashboardUrl ?? "")}</p>
      <p>${escapeHtml(message)}</p>
      <p>Use View → Reload to try again.</p>
    </main>
  </body>
</html>`
  return `data:text/html;charset=utf-8,${encodeURIComponent(html)}`
}

function escapeHtml(value) {
  return value.replace(/[&<>"']/g, (character) => {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[
      character
    ]
  })
}

async function loadDashboard(window) {
  if (!dashboardUrl) return
  try {
    await window.loadURL(dashboardUrl)
  } catch (error) {
    if (!window.isDestroyed()) await window.loadURL(errorPage(error))
  }
}

function createMenu() {
  const dashboardSettingsItem = {
    label: "Dashboard URL…",
    click: () => createSetupWindow(),
  }
  const template = [
    ...(process.platform === "darwin"
      ? [
          {
            label: app.name,
            submenu: [
              { role: "about" },
              dashboardSettingsItem,
              { type: "separator" },
              { role: "services" },
              { type: "separator" },
              { role: "hide" },
              { role: "hideOthers" },
              { role: "unhide" },
              { type: "separator" },
              { role: "quit" },
            ],
          },
        ]
      : []),
    ...(process.platform === "darwin"
      ? []
      : [
          {
            label: "File",
            submenu: [dashboardSettingsItem, { type: "separator" }, { role: "quit" }],
          },
        ]),
    {
      label: "Edit",
      submenu: [
        { role: "undo" },
        { role: "redo" },
        { type: "separator" },
        { role: "cut" },
        { role: "copy" },
        { role: "paste" },
        { role: "selectAll" },
      ],
    },
    {
      label: "View",
      submenu: [
        {
          label: "Reload",
          accelerator: "CmdOrCtrl+R",
          click: () => {
            if (mainWindow) void loadDashboard(mainWindow)
          },
        },
        ...(isDevelopment ? [{ role: "toggleDevTools" }] : []),
        { type: "separator" },
        { role: "resetZoom" },
        { role: "zoomIn" },
        { role: "zoomOut" },
        { type: "separator" },
        { role: "togglefullscreen" },
      ],
    },
    {
      label: "Window",
      submenu: [{ role: "minimize" }, { role: "zoom" }, { role: "close" }],
    },
    {
      role: "help",
      submenu: [
        {
          label: "Open SWE on GitHub",
          click: () => void shell.openExternal("https://github.com/langchain-ai/open-swe"),
        },
      ],
    },
  ]
  Menu.setApplicationMenu(Menu.buildFromTemplate(template))
}

function createWindow() {
  if (!dashboardUrl) return createSetupWindow()
  const window = new BrowserWindow({
    title: "Open SWE",
    width: 1440,
    height: 900,
    minWidth: 900,
    minHeight: 600,
    backgroundColor: "#ffffff",
    icon: iconPath(),
    show: false,
    ...(process.platform === "darwin"
      ? {
          titleBarStyle: "hiddenInset",
          trafficLightPosition: { x: 16, y: 18 },
        }
      : {}),
    webPreferences: {
      contextIsolation: true,
      navigateOnDragDrop: false,
      nodeIntegration: false,
      preload: path.join(__dirname, "preload.cjs"),
      sandbox: true,
    },
  })

  window.once("ready-to-show", () => window.show())
  window.on("closed", () => {
    if (mainWindow === window) mainWindow = null
  })

  window.webContents.setWindowOpenHandler(({ url }) => {
    if (["http:", "https:", "mailto:"].includes(new URL(url).protocol)) {
      void shell.openExternal(url)
    }
    return { action: "deny" }
  })
  window.webContents.on("will-navigate", (event, url) => {
    const protocol = new URL(url).protocol
    if (protocol !== "http:" && protocol !== "https:") event.preventDefault()
  })
  window.webContents.on("will-attach-webview", (event) => event.preventDefault())

  mainWindow = window
  void loadDashboard(window)
  return window
}

function createSetupWindow() {
  if (setupWindow && !setupWindow.isDestroyed()) {
    setupWindow.show()
    setupWindow.focus()
    return setupWindow
  }

  const window = new BrowserWindow({
    title: "Configure Open SWE",
    width: 560,
    height: 460,
    minWidth: 480,
    minHeight: 420,
    backgroundColor: "#ffffff",
    icon: iconPath(),
    show: false,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  })

  window.once("ready-to-show", () => window.show())
  window.on("closed", () => {
    if (setupWindow === window) setupWindow = null
  })
  window.webContents.setWindowOpenHandler(() => ({ action: "deny" }))
  window.webContents.on("will-navigate", (event, targetUrl) => {
    if (!targetUrl.startsWith("open-swe-setup://configure")) return
    event.preventDefault()
    try {
      const value = new URL(targetUrl).searchParams.get("url")
      if (!value) throw new Error("Enter a dashboard URL")
      dashboardUrl = storeDashboardUrl(value)
      if (mainWindow && !mainWindow.isDestroyed()) void loadDashboard(mainWindow)
      else createWindow()
      window.close()
    } catch (error) {
      dialog.showErrorBox("Invalid Open SWE dashboard URL", error.message)
    }
  })

  setupWindow = window
  void window.loadFile(path.join(__dirname, "setup.html"))
  return window
}

function configurePermissions() {
  session.defaultSession.setPermissionRequestHandler(
    (webContents, permission, callback, details) => {
      callback(
        isTrustedPermissionRequest(
          dashboardUrl,
          permission,
          details.requestingUrl || webContents.getURL()
        )
      )
    }
  )
  session.defaultSession.setPermissionCheckHandler(
    (_webContents, permission, requestingOrigin) =>
      isTrustedPermissionRequest(dashboardUrl, permission, requestingOrigin)
  )
}

const hasSingleInstanceLock = app.requestSingleInstanceLock()
if (!hasSingleInstanceLock) {
  app.quit()
} else {
  app.on("second-instance", () => {
    const window = mainWindow || setupWindow || createWindow()
    if (window.isMinimized()) window.restore()
    window.show()
    window.focus()
  })

  app.whenReady().then(() => {
    try {
      dashboardUrl = resolveDashboardUrl({
        argv: process.argv.slice(1),
        env: process.env,
        isPackaged: app.isPackaged,
        storedUrl: readStoredDashboardUrl(),
      })
    } catch (error) {
      dialog.showErrorBox("Invalid Open SWE dashboard URL", error.message)
      app.exit(1)
      return
    }

    app.setAppUserModelId("com.langchain.openswe")
    if (process.platform === "darwin") app.dock.setIcon(iconPath())
    configurePermissions()
    createMenu()
    createWindow()

    app.on("activate", () => {
      if (BrowserWindow.getAllWindows().length === 0) createWindow()
    })
  })

  app.on("window-all-closed", () => {
    if (process.platform !== "darwin") app.quit()
  })
}
