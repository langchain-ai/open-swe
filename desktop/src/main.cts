const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const {
  app,
  BrowserWindow,
  ipcMain,
  Menu,
  dialog,
  session,
  shell,
} = require("electron");
const { LocalGraphClient } = require("./local-graph-client.cjs");
const { ApplicationSupervisor } = require("./application-supervisor.cjs");
const { LocalThreadStore } = require("./local-thread-store.cjs");
const {
  closeAllTerminals,
  configureTerminalIpc,
  closeThreadTerminals,
} = require("./terminal-manager.cjs");
const { readProjects } = require("./project-store.cjs");
const { beginLogin } = require("./login-server.cjs");
const { OpenAiOAuthManager } = require("./openai-oauth.cjs");
const { isDesktopCommandId } = require("./commands.cjs");
const {
  APP_ORIGIN,
  SESSION_COOKIE_NAME,
  desktopExchangeUrl,
  desktopLoginUrl,
  hostedSessionCookieUrl,
  isAppLoginUrl,
  isAppUrl,
  isNavigationAbort,
  isTrustedPermissionRequest,
  localCallbackUrl,
  resolveBackendUrl,
  resolveAppRuntime,
  validateBackendUrl,
} = require("./config.cjs");

const appRuntime = resolveAppRuntime({
  argv: process.argv,
  isPackaged: app.isPackaged,
  appDataPath: app.getPath("appData"),
});
const isDevelopment = appRuntime.isDevelopment;
if (appRuntime.userDataPath) {
  fs.mkdirSync(appRuntime.userDataPath, { recursive: true });
  app.setName(appRuntime.name);
  app.setPath("userData", appRuntime.userDataPath);
}
app.setAppUserModelId(appRuntime.appUserModelId);

let backendUrl = null;
let mainWindow = null;
let setupWindow = null;
let loginFlow = null;
let quitting = false;
let localThreadStore = null;
let backendSupervisor = null;
let applicationSupervisor = null;
let openAiOAuth = null;

function sendDesktopCommand(commandId) {
  if (!isDesktopCommandId(commandId) || !mainWindow || mainWindow.isDestroyed())
    return;
  mainWindow.webContents.send("desktop:command", commandId);
}

function requireTrustedDesktopIpc(event) {
  const senderUrl = event.senderFrame?.url || event.sender.getURL();
  if (!applicationSupervisor?.isTrustedUrl(senderUrl) && !isAppUrl(senderUrl))
    throw new Error("Forbidden");
}

function projectsPath() {
  return path.join(app.getPath("userData"), "desktop-projects.json");
}

function listProjects() {
  return readProjects(projectsPath());
}

function pathIsInside(root, candidate) {
  const relative = path.relative(root, candidate);
  return (
    relative === "" ||
    (!relative.startsWith(`..${path.sep}`) &&
      relative !== ".." &&
      !path.isAbsolute(relative))
  );
}

function registeredProject(cwd) {
  try {
    const canonical = fs.realpathSync(cwd);
    return listProjects().some((project) => project.cwd === canonical)
      ? canonical
      : null;
  } catch {
    return null;
  }
}

function resolveLocalProjectPath(localSessionId, value) {
  const localSession = localThreadStore.get(localSessionId);
  if (!localSession || typeof value !== "string" || value.length === 0)
    return null;
  try {
    const projectRoot = fs.realpathSync(localSession.cwd);
    if (!registeredProject(projectRoot)) return null;
    const windowsAbsolute = path.win32.isAbsolute(value);
    if (windowsAbsolute && process.platform !== "win32") return null;
    const candidate = fs.realpathSync(
      path.isAbsolute(value) || windowsAbsolute
        ? value
        : path.resolve(projectRoot, value),
    );
    if (!pathIsInside(projectRoot, candidate)) return null;
    const relative = path.relative(projectRoot, candidate);
    return relative === "" ? "." : relative.split(path.sep).join("/");
  } catch {
    return null;
  }
}

/** A running thread owns the checkout, so its branch can still be changing. */
function configureDesktopIpc() {
  ipcMain.handle("desktop:open-external", async (event, value) => {
    requireTrustedDesktopIpc(event);
    if (typeof value !== "string" || value.length > 8_192) return false;
    let url;
    try {
      url = new URL(value);
    } catch {
      return false;
    }
    if (url.protocol !== "http:" && url.protocol !== "https:") return false;
    await shell.openExternal(url.href);
    return true;
  });

  ipcMain.handle("desktop:resolve-local-project-path", (event, input) => {
    requireTrustedDesktopIpc(event);
    return resolveLocalProjectPath(input?.localSessionId, input?.path);
  });
  ipcMain.handle("desktop:local-model-credential-status", (event, modelId) => {
    requireTrustedDesktopIpc(event);
    return backendSupervisor.credentialStatus(modelId);
  });
  ipcMain.handle("desktop:local-openai-sign-in", async (event) => {
    requireTrustedDesktopIpc(event);
    if (!openAiOAuth) throw new Error("OpenAI sign-in is unavailable");
    return openAiOAuth.login((url) => shell.openExternal(url));
  });
}

function configPath() {
  return path.join(app.getPath("userData"), "desktop-config.json");
}

function readStoredBackendUrl() {
  try {
    const config = JSON.parse(fs.readFileSync(configPath(), "utf8"));
    return typeof config.backendUrl === "string"
      ? validateBackendUrl(config.backendUrl)
      : undefined;
  } catch {
    return undefined;
  }
}

function storeBackendUrl(value) {
  const url = validateBackendUrl(value.trim());
  fs.mkdirSync(path.dirname(configPath()), { recursive: true });
  fs.writeFileSync(
    configPath(),
    `${JSON.stringify({ backendUrl: url }, null, 2)}\n`,
    {
      mode: 0o600,
    },
  );
  return url;
}

function iconPath() {
  return app.isPackaged
    ? path.join(process.resourcesPath, "icon.png")
    : path.resolve(__dirname, "../resources/icon.png");
}

function errorPage(error) {
  const message = String(error?.message || error);
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
      <h1>Open SWE could not start</h1>
      <p>${escapeHtml(message)}</p>
      <p>Use View → Reload to try again.</p>
    </main>
  </body>
</html>`;
  return `data:text/html;charset=utf-8,${encodeURIComponent(html)}`;
}

function escapeHtml(value) {
  return value.replace(/[&<>"']/g, (character) => {
    return {
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[character];
  });
}

async function clearBackendCookies(url) {
  for (const cookie of await session.defaultSession.cookies.get({ url })) {
    await session.defaultSession.cookies.remove(
      new URL(cookie.path, url).toString(),
      cookie.name,
    );
  }
}

async function loadApp(window) {
  try {
    const { appUrl } = await applicationSupervisor.start();
    await window.loadURL(appUrl);
  } catch (error) {
    if (isNavigationAbort(error) || window.isDestroyed()) return;
    try {
      await window.loadURL(errorPage(error));
    } catch (loadError) {
      if (!isNavigationAbort(loadError)) throw loadError;
    }
  }
}

function createMenu() {
  const backendSettingsItem = {
    label: "Backend URL…",
    click: () => createSetupWindow(),
  };
  const settingsItem = {
    id: "open-settings",
    label: "Settings…",
    accelerator: "CmdOrCtrl+,",
    click: () => sendDesktopCommand("open-settings"),
  };
  const template = [
    ...(process.platform === "darwin"
      ? [
          {
            label: app.name,
            submenu: [
              { role: "about" },
              settingsItem,
              backendSettingsItem,
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
    {
      label: "File",
      submenu: [
        {
          id: "new-thread",
          label: "New Thread",
          click: () => sendDesktopCommand("new-thread"),
        },
        {
          id: "show-command-palette",
          label: "Search Commands and Threads…",
          accelerator: "CmdOrCtrl+K",
          click: () => sendDesktopCommand("show-command-palette"),
        },
        ...(process.platform === "darwin"
          ? []
          : [{ type: "separator" }, settingsItem, backendSettingsItem]),
        { type: "separator" },
        { role: process.platform === "darwin" ? "close" : "quit" },
      ],
    },
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
          id: "toggle-sidebar",
          label: "Toggle Sidebar",
          accelerator: "CmdOrCtrl+B",
          click: () => sendDesktopCommand("toggle-sidebar"),
        },
        { type: "separator" },
        {
          label: "Reload",
          accelerator: "CmdOrCtrl+R",
          click: () => {
            if (mainWindow) void loadApp(mainWindow);
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
          id: "show-keyboard-shortcuts",
          label: "Keyboard Shortcuts",
          accelerator: "CmdOrCtrl+/",
          click: () => sendDesktopCommand("show-keyboard-shortcuts"),
        },
        { type: "separator" },
        {
          label: "Open SWE on GitHub",
          click: () =>
            void shell.openExternal("https://github.com/langchain-ai/open-swe"),
        },
      ],
    },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

async function startExternalLogin() {
  if (!backendUrl) return;
  loginFlow?.cancel();
  loginFlow = null;

  let flow;
  try {
    flow = await beginLogin();
  } catch (error) {
    dialog.showErrorBox(
      `${appRuntime.name} sign-in failed`,
      `Could not open a local sign-in listener: ${error.message}`,
    );
    return;
  }
  loginFlow = flow;
  void shell.openExternal(desktopLoginUrl(backendUrl, flow));

  const code = await flow.code;
  if (loginFlow !== flow) return;
  loginFlow = null;
  if (!code) return;

  try {
    await completeExternalLogin(flow.verifier, code);
  } catch (error) {
    dialog.showErrorBox(`${appRuntime.name} sign-in failed`, error.message);
  }
}

async function completeExternalLogin(verifier, code) {
  const response = await fetch(desktopExchangeUrl(backendUrl), {
    method: "POST",
    headers: { "content-type": "application/json", origin: APP_ORIGIN },
    body: JSON.stringify({ code, verifier }),
  });
  if (!response.ok) {
    throw new Error(`Backend rejected the sign-in (${response.status})`);
  }
  const payload = await response.json();
  if (typeof payload?.session !== "string") {
    throw new Error("Backend returned no session");
  }
  const runtimeOrigin = applicationSupervisor?.origin();
  if (!runtimeOrigin) throw new Error("Desktop application server is not running");
  await session.defaultSession.cookies.set({
    url: hostedSessionCookieUrl(runtimeOrigin),
    name: SESSION_COOKIE_NAME,
    value: payload.session,
    path: "/",
    httpOnly: true,
    secure: new URL(runtimeOrigin).protocol === "https:",
    expirationDate: Date.now() / 1000 + Number(payload.expires_in),
  });

  const window =
    mainWindow && !mainWindow.isDestroyed() ? mainWindow : createWindow();
  if (window.isMinimized()) window.restore();
  window.show();
  window.focus();
  app.focus({ steal: true });
  await loadApp(window);
}

function handleNavigation(window, event, url) {
  if (
    isAppLoginUrl(url) ||
    (applicationSupervisor?.isTrustedUrl(url) &&
      new URL(url).pathname === "/dashboard/api/auth/login")
  ) {
    event.preventDefault();
    void startExternalLogin();
    return;
  }
  const callback = backendUrl ? localCallbackUrl(url, backendUrl) : null;
  if (callback) {
    event.preventDefault();
    void window.loadURL(callback);
    return;
  }
  if (isAppUrl(url) || applicationSupervisor?.isTrustedUrl(url)) return;
  event.preventDefault();
  const target = new URL(url);
  if (["http:", "https:", "mailto:"].includes(target.protocol)) {
    void shell.openExternal(url);
  }
}

function createWindow() {
  const window = new BrowserWindow({
    title: appRuntime.name,
    width: 1440,
    height: 900,
    minWidth: 480,
    minHeight: 600,
    backgroundColor: "#ffffff",
    icon: iconPath(),
    show: false,
    ...(process.platform === "darwin"
      ? {
          titleBarStyle: "hiddenInset",
          trafficLightPosition: { x: 16, y: 14 },
        }
      : {}),
    webPreferences: {
      contextIsolation: true,
      navigateOnDragDrop: false,
      nodeIntegration: false,
      preload: path.join(__dirname, "preload.cjs"),
      sandbox: true,
    },
  });

  window.once("ready-to-show", () => window.show());
  window.on("closed", () => {
    if (mainWindow === window) mainWindow = null;
  });
  window.webContents.setWindowOpenHandler(({ url }) => {
    const protocol = new URL(url).protocol;
    if (
      isAppLoginUrl(url) ||
      (applicationSupervisor?.isTrustedUrl(url) &&
        new URL(url).pathname === "/dashboard/api/auth/login")
    ) {
      void startExternalLogin();
    } else if (["http:", "https:", "mailto:"].includes(protocol)) {
      void shell.openExternal(url);
    }
    return { action: "deny" };
  });
  window.webContents.on("will-navigate", (event, url) =>
    handleNavigation(window, event, url),
  );
  window.webContents.on("will-redirect", (event, url) =>
    handleNavigation(window, event, url),
  );
  window.webContents.on("will-attach-webview", (event) =>
    event.preventDefault(),
  );
  window.webContents.on("did-finish-load", () =>
    window.webContents.send("desktop:fullscreen-change", window.isFullScreen()),
  );
  window.on("enter-full-screen", () =>
    window.webContents.send("desktop:fullscreen-change", true),
  );
  window.on("leave-full-screen", () =>
    window.webContents.send("desktop:fullscreen-change", false),
  );
  mainWindow = window;
  void loadApp(window);
  return window;
}

function createSetupWindow() {
  if (setupWindow && !setupWindow.isDestroyed()) {
    setupWindow.show();
    setupWindow.focus();
    return setupWindow;
  }

  const window = new BrowserWindow({
    title: `Configure ${appRuntime.name}`,
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
  });

  window.once("ready-to-show", () => window.show());
  window.on("closed", () => {
    if (setupWindow === window) setupWindow = null;
  });
  window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  window.webContents.on("will-navigate", async (event, targetUrl) => {
    if (!targetUrl.startsWith("open-swe-setup://configure")) return;
    event.preventDefault();
    try {
      const value = new URL(targetUrl).searchParams.get("url");
      if (!value) throw new Error("Enter a backend URL");
      const previousUrl = backendUrl;
      backendUrl = storeBackendUrl(value);
      if (previousUrl !== backendUrl) {
        const previousRuntimeOrigin = applicationSupervisor?.origin();
        if (previousUrl) await clearBackendCookies(previousUrl);
        if (previousRuntimeOrigin) {
          await clearBackendCookies(hostedSessionCookieUrl(previousRuntimeOrigin));
          await session.defaultSession.clearStorageData({ origin: previousRuntimeOrigin });
        }
        await applicationSupervisor?.setBackendUrl(backendUrl);
      }
      if (mainWindow && !mainWindow.isDestroyed()) await loadApp(mainWindow);
      else createWindow();
      window.close();
    } catch (error) {
      dialog.showErrorBox(
        `Invalid ${appRuntime.name} backend URL`,
        error.message,
      );
    }
  });

  setupWindow = window;
  void window.loadFile(path.join(__dirname, "../src/setup.html"));
  return window;
}

function configurePermissions() {
  session.defaultSession.setPermissionRequestHandler(
    (webContents, permission, callback, details) => {
      callback(
        isTrustedPermissionRequest(
          permission,
          details.requestingUrl || webContents.getURL(),
          details,
          applicationSupervisor?.origin(),
        ),
      );
    },
  );
  session.defaultSession.setPermissionCheckHandler(
    (_webContents, permission, requestingOrigin, details) =>
      isTrustedPermissionRequest(
        permission,
        requestingOrigin,
        details,
        applicationSupervisor?.origin(),
      ),
  );
}

const hasSingleInstanceLock = app.requestSingleInstanceLock();
if (!hasSingleInstanceLock) {
  app.quit();
} else {
  app.on("second-instance", (_event, commandLine) => {
    if (isDevelopment) {
      app.relaunch({ args: commandLine.slice(1) });
      app.quit();
      return;
    }
    const window = mainWindow || setupWindow || createWindow();
    if (window.isMinimized()) window.restore();
    window.show();
    window.focus();
  });

  app.whenReady().then(async () => {
    try {
      backendUrl = resolveBackendUrl({
        argv: process.argv.slice(1),
        env: process.env,
        isPackaged: app.isPackaged,
        storedUrl: readStoredBackendUrl(),
      });
    } catch (error) {
      dialog.showErrorBox(
        `Invalid ${appRuntime.name} backend URL`,
        error.message,
      );
      app.exit(1);
      return;
    }

    if (process.platform === "darwin") app.dock.setIcon(iconPath());
    localThreadStore = new LocalThreadStore(
      path.join(app.getPath("userData"), "desktop-local-threads.json"),
    );
    openAiOAuth = new OpenAiOAuthManager({
      sharedCredentialsPath: path.join(
        process.env.CODEX_HOME || path.join(os.homedir(), ".codex"),
        "auth.json",
      ),
    });
    await openAiOAuth.startBroker().catch((error) => {
      console.warn("Could not start the local OpenAI credential broker", error);
    });
    backendSupervisor = new LocalGraphClient({
      origin: () => applicationSupervisor?.origin() ?? null,
      env: () => openAiOAuth?.backendEnv() || {},
      openAiOAuthAvailable: () => openAiOAuth?.status().signedIn === true,
    });
    applicationSupervisor = new ApplicationSupervisor({
      isPackaged: app.isPackaged,
      repoRoot: path.resolve(__dirname, "../.."),
      resourcesPath: process.resourcesPath,
      stateDir: path.join(app.getPath("userData"), "local-backend"),
      backendUrl,
      env: {
        OPEN_SWE_LOCAL_PROJECTS_FILE: projectsPath(),
        ...(openAiOAuth?.backendEnv() || {}),
      },
    });
    configurePermissions();
    configureDesktopIpc();
    createMenu();
    createWindow();
    configureTerminalIpc({
      ipcMain,
      requireTrusted: requireTrustedDesktopIpc,
      getWindow: () => mainWindow,
      listProjects,
      getLocalThread: (id) => localThreadStore.get(id),
      userDataPath: app.getPath("userData"),
    });

    app.on("activate", () => {
      if (BrowserWindow.getAllWindows().length === 0) createWindow();
    });
  });

  app.on("window-all-closed", () => {
    if (process.platform !== "darwin") app.quit();
  });

  app.on("before-quit", (event) => {
    if (quitting) return;
    event.preventDefault();
    quitting = true;
    void Promise.all([
      closeAllTerminals(),
      applicationSupervisor?.close(),
      openAiOAuth?.close(),
    ]).finally(() => {
      app.quit();
    });
  });
}
