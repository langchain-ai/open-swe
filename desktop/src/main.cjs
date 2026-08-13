const fs = require("node:fs");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const {
  app,
  BrowserWindow,
  ipcMain,
  Menu,
  dialog,
  net,
  protocol,
  session,
  shell,
} = require("electron");
const { AcpSession, dcodeTarget } = require("./acp-client.cjs");
const {
  captureCheckpoint,
  checkpointRef,
  deleteRefs,
  readDiff,
  repoRoot,
  staleRefs,
} = require("./git-diff.cjs");
const {
  closeAllTerminals,
  configureTerminalIpc,
} = require("./terminal-manager.cjs");
const {
  addProject,
  readProjects,
  removeProject,
} = require("./project-store.cjs");
const {
  APP_ORIGIN,
  APP_URL,
  appRedirectUrl,
  backendRequestUrl,
  isAppUrl,
  isGithubOAuthUrl,
  isTrustedPermissionRequest,
  isTrustedProxyRequest,
  localCallbackUrl,
  resolveBackendUrl,
  resolveAppRuntime,
  staticFilePath,
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

protocol.registerSchemesAsPrivileged([
  {
    scheme: "open-swe",
    privileges: {
      standard: true,
      secure: true,
      supportFetchAPI: true,
      corsEnabled: true,
      stream: true,
      codeCache: true,
    },
  },
]);

let backendUrl = null;
let mainWindow = null;
let setupWindow = null;
let oauthNavigationActive = false;
let quitting = false;
const acpSessions = new Map();
// sessionId -> { repo, ref }: the worktree snapshot a local session started from, so
// the diff panel can show what the agent changed rather than the repo's prior state.
const acpCheckpoints = new Map();

function requireTrustedDesktopIpc(event) {
  const senderUrl = event.senderFrame?.url || event.sender.getURL();
  if (!isAppUrl(senderUrl)) throw new Error("Forbidden");
}

function sendAcpEvent(sessionId, event) {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send("desktop:acp-event", {
      sessionId,
      event,
      session: acpSessions.get(sessionId)?.summary(),
    });
  }
}

async function recordAcpCheckpoint(localSession) {
  const repo = await repoRoot(localSession.cwd);
  if (!repo) return;
  const ref = checkpointRef(localSession.id);
  const live = [...acpCheckpoints.values()]
    .filter((checkpoint) => checkpoint.repo === repo)
    .map((checkpoint) => checkpoint.ref);
  try {
    // Refs from sessions this or an earlier process lost track of; never pushed,
    // but they should not pile up in the user's own repository either.
    deleteRefs(repo, await staleRefs(repo, [...live, ref]));
    await captureCheckpoint(repo, ref);
    acpCheckpoints.set(localSession.id, { repo, ref });
  } catch {}
}

function projectsPath() {
  return path.join(app.getPath("userData"), "desktop-projects.json");
}

function listProjects() {
  return readProjects(projectsPath());
}

function sendProjectsChanged() {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send("desktop:projects-changed", listProjects());
  }
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

function resolveAcpProjectPath(localSessionId, value) {
  const localSession = acpSessions.get(localSessionId);
  if (!localSession || typeof value !== "string" || value.length === 0) {
    return null;
  }
  try {
    const projectRoot = fs.realpathSync(localSession.cwd);
    if (
      !listProjects().some((project) => {
        try {
          return fs.realpathSync(project.cwd) === projectRoot;
        } catch {
          return false;
        }
      })
    ) {
      return null;
    }
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

function configureDesktopIpc() {
  ipcMain.handle("desktop:projects", (event) => {
    requireTrustedDesktopIpc(event);
    return listProjects();
  });

  ipcMain.handle("desktop:add-project", async (event) => {
    requireTrustedDesktopIpc(event);
    const options = {
      title: "Add a project from This Mac",
      properties: ["openDirectory", "createDirectory"],
    };
    const result = mainWindow
      ? await dialog.showOpenDialog(mainWindow, options)
      : await dialog.showOpenDialog(options);
    if (result.canceled || !result.filePaths[0]) return null;
    const project = addProject(projectsPath(), result.filePaths[0]);
    sendProjectsChanged();
    return project;
  });

  ipcMain.handle("desktop:remove-project", async (event, cwd) => {
    requireTrustedDesktopIpc(event);
    const project = listProjects().find((item) => item.cwd === cwd);
    if (!project) return false;
    const options = {
      type: "warning",
      title: "Remove project",
      message: `Remove “${project.name}” from Open SWE?`,
      detail: `${project.cwd}\n\nThis does not delete files from your Mac.`,
      buttons: ["Cancel", "Remove"],
      defaultId: 0,
      cancelId: 0,
    };
    const result = mainWindow
      ? await dialog.showMessageBox(mainWindow, options)
      : await dialog.showMessageBox(options);
    if (result.response !== 1) return false;
    const removed = removeProject(projectsPath(), project.cwd);
    if (removed) sendProjectsChanged();
    return removed;
  });

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

  ipcMain.handle("desktop:resolve-acp-project-path", (event, input) => {
    requireTrustedDesktopIpc(event);
    return resolveAcpProjectPath(input?.localSessionId, input?.path);
  });

  ipcMain.handle("desktop:acp-start", async (event, input) => {
    requireTrustedDesktopIpc(event);
    if (
      !input ||
      typeof input.cwd !== "string" ||
      !path.isAbsolute(input.cwd) ||
      !fs.existsSync(input.cwd) ||
      !fs.statSync(input.cwd).isDirectory()
    ) {
      throw new Error("Choose a valid local project directory");
    }
    const cwd = fs.realpathSync(input.cwd);
    if (!listProjects().some((project) => project.cwd === cwd)) {
      throw new Error(
        "Add this project to Open SWE before starting a local agent",
      );
    }
    const localSession = new AcpSession({
      cwd,
      target: dcodeTarget({
        modelId: typeof input.modelId === "string" ? input.modelId : undefined,
        effort: typeof input.effort === "string" ? input.effort : undefined,
      }),
      env: process.env,
      onEvent: sendAcpEvent,
      requestPermission: async () => true,
    });
    acpSessions.set(localSession.id, localSession);
    try {
      await localSession.initialize();
    } catch (error) {
      acpSessions.delete(localSession.id);
      localSession.close();
      throw error;
    }
    await recordAcpCheckpoint(localSession);
    void localSession
      .prompt(input.prompt || "", input.images || [])
      .catch(() => {});
    return localSession.snapshot();
  });

  ipcMain.handle("desktop:acp-prompt", async (event, input) => {
    requireTrustedDesktopIpc(event);
    const localSession = acpSessions.get(input?.sessionId);
    if (!localSession)
      throw new Error("Local Deep Agents Code session not found");
    await localSession.prompt(input.prompt || "", input.images || []);
    return localSession.snapshot();
  });

  ipcMain.handle("desktop:acp-cancel", (event, sessionId) => {
    requireTrustedDesktopIpc(event);
    acpSessions.get(sessionId)?.cancel();
  });

  ipcMain.handle("desktop:acp-session", (event, sessionId) => {
    requireTrustedDesktopIpc(event);
    return acpSessions.get(sessionId)?.snapshot() || null;
  });

  ipcMain.handle("desktop:acp-diff", async (event, sessionId) => {
    requireTrustedDesktopIpc(event);
    const localSession = acpSessions.get(sessionId);
    const checkpoint = acpCheckpoints.get(sessionId);
    if (
      !localSession ||
      !checkpoint ||
      !listProjects().some((project) => project.cwd === localSession.cwd)
    ) {
      return { status: "missing", files: [], truncated: false };
    }
    try {
      return await readDiff(checkpoint.repo, checkpoint.ref);
    } catch {
      return { status: "error", files: [], truncated: false };
    }
  });

  ipcMain.handle("desktop:acp-sessions", (event) => {
    requireTrustedDesktopIpc(event);
    return [...acpSessions.values()].map((localSession) =>
      localSession.summary(),
    );
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

function bundledUiPath() {
  return app.isPackaged
    ? path.join(process.resourcesPath, "ui")
    : path.resolve(__dirname, "../../ui/.output/public");
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

async function proxyBackendRequest(request) {
  const source = new URL(request.url);
  const headers = new Headers(request.headers);
  const pageUrl = mainWindow?.webContents.getURL() || "";
  if (!isTrustedProxyRequest(request.method, pageUrl, request.url)) {
    return new Response("Forbidden", { status: 403 });
  }
  headers.delete("host");
  headers.set("accept-encoding", "identity");
  headers.set("origin", APP_ORIGIN);
  const targetUrl = backendRequestUrl(backendUrl, request.url);
  const cookies = await session.defaultSession.cookies.get({ url: targetUrl });
  if (cookies.length) {
    headers.set(
      "cookie",
      cookies.map(({ name, value }) => `${name}=${value}`).join("; "),
    );
  } else {
    headers.delete("cookie");
  }

  const body = ["GET", "HEAD"].includes(request.method)
    ? undefined
    : request.body;
  const upstream = await fetch(targetUrl, {
    method: request.method,
    headers,
    body,
    redirect: "manual",
    ...(body ? { duplex: "half" } : {}),
  });
  await storeResponseCookies(targetUrl, upstream);

  const location = upstream.headers.get("location");
  if (location && source.pathname.endsWith("/callback")) {
    const responseHeaders = new Headers(upstream.headers);
    responseHeaders.set("location", appRedirectUrl(location));
    return new Response(upstream.body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: responseHeaders,
    });
  }
  return upstream;
}

async function storeResponseCookies(targetUrl, response) {
  const values = response.headers.getSetCookie?.() ?? [];
  for (const value of values) {
    const [pair, ...attributes] = value.split(";");
    const separator = pair.indexOf("=");
    if (separator <= 0) continue;
    const name = pair.slice(0, separator).trim();
    const cookieValue = pair.slice(separator + 1).trim();
    const details = {
      url: targetUrl,
      name,
      value: cookieValue,
      path: "/",
    };
    let remove = false;
    for (const rawAttribute of attributes) {
      const [rawName, ...rawValue] = rawAttribute.trim().split("=");
      const attributeName = rawName.toLowerCase();
      const attributeValue = rawValue.join("=");
      if (attributeName === "path" && attributeValue)
        details.path = attributeValue;
      else if (attributeName === "domain" && attributeValue)
        details.domain = attributeValue;
      else if (attributeName === "secure") details.secure = true;
      else if (attributeName === "httponly") details.httpOnly = true;
      else if (attributeName === "max-age") {
        const seconds = Number(attributeValue);
        if (Number.isFinite(seconds) && seconds > 0) {
          details.expirationDate = Date.now() / 1000 + seconds;
        } else if (seconds === 0) {
          remove = true;
        }
      }
    }
    const cookieUrl = new URL(details.path, targetUrl).toString();
    if (remove) await session.defaultSession.cookies.remove(cookieUrl, name);
    else await session.defaultSession.cookies.set(details);
  }
}

async function clearBackendCookies(url) {
  for (const cookie of await session.defaultSession.cookies.get({ url })) {
    await session.defaultSession.cookies.remove(
      new URL(cookie.path, url).toString(),
      cookie.name,
    );
  }
}

async function serveBundledUi(request) {
  if (!backendUrl)
    return new Response("Backend is not configured", { status: 503 });
  const url = new URL(request.url);
  if (url.pathname.startsWith("/dashboard/api"))
    return proxyBackendRequest(request);
  if (!["GET", "HEAD"].includes(request.method)) {
    return new Response("Method not allowed", { status: 405 });
  }

  const root = bundledUiPath();
  let filePath = staticFilePath(root, request.url);
  if (
    !filePath ||
    !fs.existsSync(filePath) ||
    !fs.statSync(filePath).isFile()
  ) {
    if (path.extname(url.pathname))
      return new Response("Not found", { status: 404 });
    filePath = path.join(root, "_shell.html");
  }
  if (!fs.existsSync(filePath)) {
    return new Response("Bundled UI is missing. Run pnpm run build:ui.", {
      status: 500,
    });
  }
  return net.fetch(pathToFileURL(filePath).toString());
}

async function loadApp(window) {
  oauthNavigationActive = false;
  if (!backendUrl) return;
  try {
    await window.loadURL(APP_URL);
  } catch (error) {
    if (!window.isDestroyed()) await window.loadURL(errorPage(error));
  }
}

function createMenu() {
  const backendSettingsItem = {
    label: "Backend URL…",
    click: () => createSetupWindow(),
  };
  const template = [
    ...(process.platform === "darwin"
      ? [
          {
            label: app.name,
            submenu: [
              { role: "about" },
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
    ...(process.platform === "darwin"
      ? []
      : [
          {
            label: "File",
            submenu: [
              backendSettingsItem,
              { type: "separator" },
              { role: "quit" },
            ],
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
          label: "Open SWE on GitHub",
          click: () =>
            void shell.openExternal("https://github.com/langchain-ai/open-swe"),
        },
      ],
    },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

function handleNavigation(window, event, url) {
  const callback = backendUrl ? localCallbackUrl(url, backendUrl) : null;
  if (callback) {
    oauthNavigationActive = false;
    event.preventDefault();
    void window.loadURL(callback);
    return;
  }
  if (isAppUrl(url)) {
    oauthNavigationActive = false;
    return;
  }
  if (isGithubOAuthUrl(url)) {
    oauthNavigationActive = true;
    return;
  }
  const target = new URL(url);
  if (oauthNavigationActive && target.protocol === "https:") return;
  event.preventDefault();
  if (["http:", "https:", "mailto:"].includes(target.protocol)) {
    void shell.openExternal(url);
  }
}

function createWindow() {
  if (!backendUrl) return createSetupWindow();
  const window = new BrowserWindow({
    title: appRuntime.name,
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
      isGithubOAuthUrl(url) ||
      (oauthNavigationActive && protocol === "https:")
    ) {
      oauthNavigationActive = true;
      void window.loadURL(url);
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
      if (previousUrl && previousUrl !== backendUrl) {
        await clearBackendCookies(previousUrl);
        await session.defaultSession.clearStorageData({ origin: APP_URL });
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
  void window.loadFile(path.join(__dirname, "setup.html"));
  return window;
}

function configurePermissions() {
  session.defaultSession.setPermissionRequestHandler(
    (webContents, permission, callback, details) => {
      callback(
        isTrustedPermissionRequest(
          permission,
          details.requestingUrl || webContents.getURL(),
        ),
      );
    },
  );
  session.defaultSession.setPermissionCheckHandler(
    (_webContents, permission, requestingOrigin) =>
      isTrustedPermissionRequest(permission, requestingOrigin),
  );
}

const hasSingleInstanceLock = app.requestSingleInstanceLock();
if (!hasSingleInstanceLock) {
  app.quit();
} else {
  app.on("second-instance", () => {
    const window = mainWindow || setupWindow || createWindow();
    if (window.isMinimized()) window.restore();
    window.show();
    window.focus();
  });

  app.whenReady().then(() => {
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
    protocol.handle("open-swe", serveBundledUi);
    configurePermissions();
    configureDesktopIpc();
    createMenu();
    createWindow();
    configureTerminalIpc({
      ipcMain,
      requireTrusted: requireTrustedDesktopIpc,
      getWindow: () => mainWindow,
      listProjects,
      getAcpSession: (sessionId) => acpSessions.get(sessionId),
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
    void closeAllTerminals().finally(() => {
      for (const localSession of acpSessions.values()) localSession.close();
      acpSessions.clear();
      for (const { repo, ref } of acpCheckpoints.values())
        deleteRefs(repo, [ref]);
      acpCheckpoints.clear();
      app.quit();
    });
  });
}
