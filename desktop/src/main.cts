const fs = require("node:fs");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const {
  app,
  autoUpdater: nativeAutoUpdater,
  BaseWindow,
  BrowserWindow,
  clipboard,
  ipcMain,
  Menu,
  dialog,
  nativeTheme,
  net,
  protocol,
  powerMonitor,
  session,
  shell,
  webContents,
  WebContentsView,
} = require("electron");
const { autoUpdater } = require("electron-updater");
const {
  ConsoleLogBuffer,
  buildDiagnosticsReport,
  captureProcessConsole,
  diagnosticsFileName,
  redactSecrets,
  normalizeConsoleMessage,
} = require("./diagnostics.cjs");
const { LocalAccessBridge } = require("./local-access.cjs");
const { ApprovalQueue } = require("./approval-queue.cjs");
const { ApprovalPanel } = require("./approval-panel.cjs");
const { AppTabs } = require("./tabs.cjs");
const { DesktopUi } = require("./desktop-ui.cjs");
const { getUserShellEnv } = require("./shell-env.cjs");
const { beginLogin } = require("./login-server.cjs");
const { MenuCommands, isDesktopCommandId } = require("./commands.cjs");
const { FramePolicy } = require("./frame-policy.cjs");
const { DesktopLinks } = require("./desktop-links.cjs");
const { DesktopEnvironments } = require("./desktop-environments.cjs");
const { CloseConfirmation } = require("./close-confirmation.cjs");
const { LifecycleLog } = require("./lifecycle-log.cjs");
const {
  APP_ORIGIN,
  APP_URL,
  SESSION_COOKIE_NAME,
  appRedirectUrl,
  backendRequestUrl,
  desktopExchangeUrl,
  desktopLoginUrl,
  resolveAppRuntime,
  resolveBackendUrl,
  connectExchangeUrl,
  connectLoginUrl,
  isAppLoginUrl,
  isAppUrl,
  headersFramedByApp,
  isConnectProvider,
  isTrustedPermissionRequest,
  isTrustedProxyRequest,
  localCallbackUrl,
  parseDesktopEnvironments,
  validateBackendUrl,
} = require("./config.cjs");
const {
  migrateDesktopConfig,
  readSharedConfig,
  shareSession,
  sharedConfigPath,
  unshareSession,
  updateSharedConfig,
} = require("./shared-config.js");

const appRuntime = resolveAppRuntime({
  argv: process.argv,
  isPackaged: app.isPackaged,
  appDataPath: app.getPath("appData"),
});
const isDevelopment = appRuntime.isDevelopment;
if (appRuntime.userDataPath) {
  fs.mkdirSync(appRuntime.userDataPath, { recursive: true });
  app.setName(path.basename(appRuntime.userDataPath));
  app.setPath("userData", appRuntime.userDataPath);
}
app.setAppUserModelId(appRuntime.appUserModelId);

// Outlives the process, unlike the console buffers: says why a launch ended.
fs.mkdirSync(app.getPath("userData"), { recursive: true });
const lifecycleLog = new LifecycleLog(
  path.join(app.getPath("userData"), "desktop-lifecycle.log"),
);
lifecycleLog.watchQuits(app);
lifecycleLog.record("start", {
  version: app.getVersion(),
  packaged: app.isPackaged,
  platform: `${process.platform}-${process.arch}`,
  os: require("node:os").release(),
  argv: redactSecrets(process.argv.slice(1).join(" ")),
});
app.on("child-process-gone", (_event, details) =>
  lifecycleLog.record("child-process-gone", {
    type: details.type,
    reason: details.reason,
    exitCode: details.exitCode,
    name: details.name,
  }),
);

protocol.registerSchemesAsPrivileged([
  {
    scheme: "robin",
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
// The backend the app started on; `backendUrl` differs only while switched to
// one of its alternate environments, which lasts until the app restarts.
let primaryBackendUrl = null;
let alternativeEnvironments = [];
const desktopEnvironments = new DesktopEnvironments();
const desktopUi = {
  instance: null as InstanceType<typeof DesktopUi> | null,
  current() {
    if (!backendUrl) throw new Error("Backend is not configured");
    if (this.instance?.backendUrl !== backendUrl) {
      this.instance = new DesktopUi(
        backendUrl,
        fetch,
        path.join(app.getPath("userData"), "ui-cache"),
        (filename: string) => net.fetch(pathToFileURL(filename).toString()),
        process.env.OPEN_SWE_DESKTOP_UI_ARCHIVE ?? null,
        () =>
          void dialog.showMessageBox({
            type: "warning",
            message: `${appRuntime.name} needs an update`,
            detail: `Your backend has a newer UI than this version of ${appRuntime.name} can run. You are still on the previous UI; update the desktop app to get the new one.`,
          }),
      );
    }
    return this.instance;
  },
};
type Tab = Electron.WebContentsView;
let mainWindow: Electron.BaseWindow | null = null;
let appTabs: InstanceType<typeof AppTabs> | null = null;
let activeTab: Tab | null = null;
const tabMenuCommands = new WeakMap<Tab, unknown>();
const tabThreadScopes = new WeakMap<Tab, Map<string, unknown>>();
const menuCommands = new MenuCommands((commandId, viaShortcut) => {
  if (activeTab && !activeTab.webContents.isDestroyed())
    activeTab.webContents.send("desktop:menu-command", commandId, viaShortcut);
});

function sendToTabs(channel: string, ...args: unknown[]) {
  for (const tab of appTabs?.tabs ?? [])
    if (!tab.webContents.isDestroyed()) tab.webContents.send(channel, ...args);
}

function onTabActivated(tab: Tab | null) {
  activeTab = tab;
  menuCommands.reset();
  const commands = tab && tabMenuCommands.get(tab);
  if (commands) menuCommands.publish(commands);
  createMenu();
  approvalPanel.reset();
  for (const [threadId, bounds] of (tab && tabThreadScopes.get(tab)) ?? [])
    approvalPanel.setThread(threadId, bounds);
  approvalPanel.sync();
}

const panelHosts = new WeakMap<Tab, object>();
function approvalPanelHost() {
  const tab = activeTab;
  if (!tab || tab.webContents.isDestroyed()) return null;
  let host = panelHosts.get(tab);
  if (!host) {
    const contents = tab.webContents;
    host = {
      webContents: contents,
      contentView: tab,
      isDestroyed: () => contents.isDestroyed(),
      getContentBounds: () => tab.getBounds(),
      once: (_event: "closed", listener: () => void) =>
        contents.once("destroyed", listener),
    };
    panelHosts.set(tab, host);
  }
  return host;
}

// Installed builds have no terminal; "Save Diagnostics Report…" exports these.
const rendererConsole = new ConsoleLogBuffer(2000);
const mainConsole = new ConsoleLogBuffer(500);
captureProcessConsole(mainConsole);
let setupWindow = null;
let loginFlow = null;
const connectFlows = new Map();
let quitting = false;
let localAccessBridge = null;
// Local actions waiting on the person, decided only in the desktop's own panel.
const approvalQueue = new ApprovalQueue(() => {
  approvalPanel.sync();
  sendToTabs("desktop:local-access-requests-changed");
});
const approvalPanel = new ApprovalPanel(
  approvalQueue,
  approvalPanelHost,
  (folder) => localAccessBridge?.displayPath(folder) ?? folder,
);
type DesktopUpdateState = {
  status: "idle" | "downloading" | "ready" | "installing";
  version?: string;
};
let updateState: DesktopUpdateState = { status: "idle" };

function setUpdateState(
  status: DesktopUpdateState["status"],
  version?: string,
) {
  if (status !== updateState.status || version !== updateState.version)
    lifecycleLog.record("update-state", {
      from: updateState.status,
      to: status,
      version,
    });
  updateState = { status, ...(version ? { version } : {}) };
  sendToTabs("desktop:update-state", updateState);
}

let lastUpdateCheck = 0;
let updateCheck: ReturnType<typeof autoUpdater.checkForUpdates> | null = null;

async function checkAndDownloadLatest() {
  const result = await autoUpdater.checkForUpdates();
  const version = result?.updateInfo.version;
  if (
    !result?.isUpdateAvailable ||
    updateState.status === "downloading" ||
    version === updateState.version
  )
    return result;
  setUpdateState("downloading", version);
  void autoUpdater.downloadUpdate().catch(() => undefined);
  return result;
}

function checkForUpdates() {
  lastUpdateCheck = Date.now();
  updateCheck ??= checkAndDownloadLatest().finally(() => {
    updateCheck = null;
  });
  return updateCheck;
}

function checkForUpdatesInBackground() {
  if (
    updateState.status === "downloading" ||
    updateState.status === "installing" ||
    Date.now() - lastUpdateCheck < 60 * 60 * 1000
  )
    return;
  void checkForUpdates().catch((error) =>
    console.warn("Could not check for desktop updates", error),
  );
}

let updateInstallTimer: ReturnType<typeof setTimeout> | undefined;

function failDesktopUpdate(error: unknown) {
  console.warn("Desktop update failed", error);
  const installing = updateState.status === "installing";
  clearTimeout(updateInstallTimer);
  setUpdateState("idle");
  if (!installing) return;
  quitting = false;
  dialog.showErrorBox(
    "Could not install the update",
    `The update did not complete. Quit and reopen ${appRuntime.name} before continuing. ` +
      `If updating still fails, install the latest release from https://github.com/langchain-ai/open-swe/releases/latest.\n\n` +
      String(error),
  );
}

function configureAutoUpdater() {
  if (!app.isPackaged) return;
  autoUpdater.autoDownload = false;
  autoUpdater.autoInstallOnAppQuit = true;
  autoUpdater.allowPrerelease = false;
  if (process.platform === "darwin") {
    nativeAutoUpdater.on("update-downloaded", () =>
      setUpdateState("ready", updateState.version),
    );
  } else {
    autoUpdater.on("update-downloaded", (info) =>
      setUpdateState("ready", info.version),
    );
  }
  autoUpdater.on("error", failDesktopUpdate);
  checkForUpdatesInBackground();
  const timer = setInterval(checkForUpdatesInBackground, 4 * 60 * 60 * 1000);
  timer.unref();
  powerMonitor.on("resume", checkForUpdatesInBackground);
  app.on("activate", checkForUpdatesInBackground);
  app.on("browser-window-focus", checkForUpdatesInBackground);
}

async function checkForDesktopUpdates() {
  try {
    if (updateState.status === "installing") return;
    const result = await checkForUpdates();
    if (updateState.status === "ready") return;
    if (result?.isUpdateAvailable) {
      await dialog.showMessageBox({
        type: "info",
        message: `${appRuntime.name} ${result.updateInfo.version} is available`,
        detail: "The update is downloading and will be ready to install soon.",
      });
      return;
    }
    await dialog.showMessageBox({
      type: "info",
      message: `${appRuntime.name} is up to date`,
      detail: `Version ${app.getVersion()} is the latest available version.`,
    });
  } catch (error) {
    console.warn("Could not check for desktop updates", error);
    dialog.showErrorBox(
      `Could not check for ${appRuntime.name} updates`,
      error.message,
    );
  }
}

function sendDesktopCommand(commandId) {
  if (
    !isDesktopCommandId(commandId) ||
    !activeTab ||
    activeTab.webContents.isDestroyed()
  )
    return;
  activeTab.webContents.send("desktop:command", commandId);
}

function requireTrustedDesktopIpc(event) {
  const senderUrl = event.senderFrame?.url || event.sender.getURL();
  if (!isAppUrl(senderUrl)) throw new Error("Forbidden");
}

/**
 * Sends one bridge call to the backend under the app's session. A long poll
 * brings its own abort signal; anything else gives up rather than hold a quit.
 */
async function sendBridgeRequest(method, apiPath, { body, signal }) {
  if (!backendUrl) throw new Error("Robin is not connected to a backend");
  const response = await backendFetch(
    new URL(`/dashboard/api${apiPath}`, backendUrl).toString(),
    {
      method,
      headers: body ? { "content-type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
      // A reply carries a command's output, which can take a while to upload.
      signal:
        signal ??
        AbortSignal.timeout(apiPath.includes("/requests/") ? 120_000 : 15_000),
    },
  );
  const text = await response.text();
  if (!response.ok)
    throw Object.assign(
      new Error(`HTTP ${response.status}: ${text.slice(0, 600)}`),
      { status: response.status },
    );
  return text ? JSON.parse(text) : null;
}

function configureDesktopIpc() {
  ipcMain.handle("desktop:set-menu-commands", (event, commands) => {
    requireTrustedDesktopIpc(event);
    const tab = appTabs?.tabFor(event.sender);
    if (!tab) return [];
    tabMenuCommands.set(tab, commands);
    if (tab !== activeTab) {
      // A background tab's menu shows once it comes to the front.
      const pending = new MenuCommands(() => {});
      pending.publish(commands);
      return pending.nativeShortcutIds();
    }
    if (menuCommands.publish(commands)) createMenu();
    return menuCommands.nativeShortcutIds();
  });
  ipcMain.handle("desktop:write-clipboard", (event, value) => {
    requireTrustedDesktopIpc(event);
    if (typeof value === "string") clipboard.writeText(value);
  });
  ipcMain.handle("desktop:copy-page-link", async (event) => {
    requireTrustedDesktopIpc(event);
    if (!backendUrl) throw new Error("Robin is not connected to a backend");
    const source = new URL(event.sender.getURL());
    clipboard.writeText(
      await desktopEnvironments.dashboardPageUrl(
        backendUrl,
        source.pathname + source.search + source.hash,
      ),
    );
  });
  ipcMain.handle("desktop:open-in-browser", async (event, pagePath) => {
    requireTrustedDesktopIpc(event);
    if (!backendUrl) throw new Error("Robin is not connected to a backend");
    if (typeof pagePath !== "string") throw new Error("Invalid path");
    await shell.openExternal(
      await desktopEnvironments.dashboardPageUrl(backendUrl, pagePath),
    );
  });
  ipcMain.handle("desktop:version", (event) => {
    requireTrustedDesktopIpc(event);
    return app.getVersion();
  });
  ipcMain.handle("desktop:update-state", (event) => {
    requireTrustedDesktopIpc(event);
    return updateState;
  });
  ipcMain.handle("desktop:install-update", async (event) => {
    requireTrustedDesktopIpc(event);
    if (updateState.status === "installing") return true;
    if (updateState.status !== "ready") return false;
    const version = updateState.version;
    setUpdateState("installing", version);
    quitting = true;
    updateInstallTimer = setTimeout(
      () =>
        failDesktopUpdate(
          new Error("Installation timed out after 60 seconds."),
        ),
      60_000,
    );
    updateInstallTimer.unref();
    try {
      await localAccessBridge?.close().finally(() => {
        localAccessBridge = null;
      });
      if (!quitting) return false;
      autoUpdater.quitAndInstall(false, true);
      return true;
    } catch (error) {
      failDesktopUpdate(error);
      return false;
    }
  });

  ipcMain.handle(
    "desktop:connect-service",
    async (event, provider, options: unknown) => {
      requireTrustedDesktopIpc(event);
      const recoveryToken =
        options && typeof options === "object" && "recoveryToken" in options
          ? options.recoveryToken
          : undefined;
      if (
        recoveryToken !== undefined &&
        (provider !== "github" ||
          typeof recoveryToken !== "string" ||
          !recoveryToken ||
          recoveryToken.length > 16_384)
      )
        return false;
      return startConnectFlow(
        provider,
        typeof recoveryToken === "string" ? recoveryToken : undefined,
      );
    },
  );

  ipcMain.handle("desktop:cancel-connect-service", async (event, provider) => {
    requireTrustedDesktopIpc(event);
    if (!isConnectProvider(provider)) return;
    connectFlows.get(provider)?.cancel();
    connectFlows.delete(provider);
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

  ipcMain.on("desktop:tab-painted", (event) => {
    const tab = appTabs?.tabFor(event.sender);
    if (tab && isAppUrl(event.sender.getURL())) appTabs?.contentPainted(tab);
  });
  ipcMain.on("desktop:tab-theme", (event, tokens) => {
    const tab = appTabs?.tabFor(event.sender);
    if (tab && isAppUrl(event.sender.getURL())) appTabs?.setTheme(tab, tokens);
  });

  // macOS draws the traffic lights for the window's appearance, which follows
  // the OS unless told otherwise. Without this, running the app light on a dark
  // system (or vice versa) renders them for the wrong appearance and the
  // inactive dots all but disappear.
  ipcMain.handle("desktop:set-appearance", (event, value) => {
    requireTrustedDesktopIpc(event);
    if (value !== "light" && value !== "dark" && value !== "system")
      return false;
    nativeTheme.themeSource = value;
    return true;
  });

  ipcMain.handle("desktop:ensure-local-access-bridge", async (event) => {
    requireTrustedDesktopIpc(event);
    localAccessBridge ??= new LocalAccessBridge({
      send: sendBridgeRequest,
      env: getUserShellEnv,
      log: (message) => console.warn(message),
      onGrantsChanged: () => sendToTabs("desktop:local-access-grants-changed"),
      approve: (request, signal) => approvalQueue.request(request, signal),
    });
    return localAccessBridge.ensure();
  });
  ipcMain.handle("desktop:local-access-requests", (event, threadId) => {
    requireTrustedDesktopIpc(event);
    if (typeof threadId !== "string")
      throw new Error("A thread id is required");
    // What a thread's card shows; deciding is the approval panel's alone.
    return approvalQueue
      .list(threadId)
      .map(({ id, kind, concern, createdAt }) => ({
        id,
        kind,
        concern,
        createdAt,
      }));
  });
  ipcMain.handle(
    "desktop:set-local-access-thread",
    (event, threadId, bounds) => {
      requireTrustedDesktopIpc(event);
      const tab = appTabs?.tabFor(event.sender);
      if (!tab || typeof threadId !== "string") return false;
      const scopes = tabThreadScopes.get(tab) ?? new Map();
      tabThreadScopes.set(tab, scopes);
      if (bounds === null) scopes.delete(threadId);
      else scopes.set(threadId, bounds);
      return tab === activeTab
        ? approvalPanel.setThread(threadId, bounds)
        : true;
    },
  );
  ipcMain.handle("desktop:pending-local-access-threads", (event) => {
    requireTrustedDesktopIpc(event);
    return approvalQueue.pendingThreads();
  });
  ipcMain.handle("desktop:review-local-access-request", (event, id) => {
    requireTrustedDesktopIpc(event);
    if (typeof id === "string") approvalPanel.review(id);
  });
  ipcMain.handle("desktop:local-access-grants", (event, threadId) => {
    requireTrustedDesktopIpc(event);
    if (typeof threadId !== "string")
      throw new Error("A thread id is required");
    return (localAccessBridge?.grants.list(threadId) ?? []).map(
      ({ folder, until }) => ({
        folder: localAccessBridge?.displayPath(folder) ?? folder,
        until,
      }),
    );
  });
  ipcMain.handle("desktop:revoke-local-access-grants", (event, threadId) => {
    requireTrustedDesktopIpc(event);
    if (typeof threadId !== "string")
      throw new Error("A thread id is required");
    localAccessBridge?.grants.revoke(threadId);
  });
}

function profileConfigPath() {
  return path.join(app.getPath("userData"), "desktop-config.json");
}

/** Only packaged apps share configuration with the CLI. */
function configPath() {
  return !isDevelopment
    ? sharedConfigPath(require("node:os").homedir())
    : profileConfigPath();
}

function migrateStoredConfig() {
  if (isDevelopment) return;
  try {
    migrateDesktopConfig({
      home: require("node:os").homedir(),
      platform: process.platform,
      env: process.env,
    });
  } catch (error) {
    console.warn("Could not migrate desktop-config.json", error);
  }
}

function isBackendSessionCookie(cookie) {
  return (
    Boolean(backendUrl) &&
    cookie.name === SESSION_COOKIE_NAME &&
    cookie.domain.replace(/^\./, "") === new URL(backendUrl).hostname
  );
}

/** Mirror the app's session into the shared config, so `robin` is signed in whenever the app is. */
async function shareBackendSession() {
  if (isDevelopment) return;
  session.defaultSession.cookies.on(
    "changed",
    (_event, cookie, cause, removed) => {
      if (!isBackendSessionCookie(cookie)) return;
      if (removed && cause === "overwrite") return;
      if (removed && localAccessBridge) {
        const previous = localAccessBridge;
        localAccessBridge = null;
        void previous
          .close()
          .catch((error) =>
            console.warn("Could not close local access", error),
          );
      }
      try {
        if (removed) unshareSession(configPath(), backendUrl, cookie.value);
        else shareSession(configPath(), backendUrl, cookie.value);
      } catch (error) {
        console.warn("Could not share the desktop session with robin", error);
      }
    },
  );
  if (!backendUrl) return;
  try {
    const [cookie] = await session.defaultSession.cookies.get({
      url: backendUrl,
      name: SESSION_COOKIE_NAME,
    });
    if (cookie) shareSession(configPath(), backendUrl, cookie.value);
  } catch (error) {
    console.warn("Could not share the desktop session with robin", error);
  }
}

function readStoredBackendUrl() {
  try {
    const { backendUrl: storedBackendUrl } = readSharedConfig(configPath());
    return storedBackendUrl ? validateBackendUrl(storedBackendUrl) : undefined;
  } catch (error) {
    console.warn("Could not read the stored backend URL", error);
    return undefined;
  }
}

function storeBackendUrl(value) {
  const url = validateBackendUrl(value.trim());
  updateSharedConfig(configPath(), (config) => ({
    ...config,
    backendUrl: url,
  }));
  return url;
}

/** Ask the backend the app started on which others it offers to switch to. */
async function loadAlternativeEnvironments() {
  const url = primaryBackendUrl;
  if (!url) return;
  let environments = [];
  try {
    environments = parseDesktopEnvironments(
      await desktopEnvironments.response(url),
    );
  } catch {
    // Logged where the request fails.
  }
  if (url !== primaryBackendUrl) return;
  alternativeEnvironments = environments;
  createMenu();
}

/** Point the app at `url` and reload it there, dropping the last backend's page state. */
async function connectToBackend(url) {
  const previousUrl = backendUrl;
  backendUrl = url;
  void desktopEnvironments.response(url).catch(() => {});
  if (previousUrl && previousUrl !== url)
    await session.defaultSession.clearStorageData({ origin: APP_URL });
  createMenu();
  // Other tabs still show the previous backend.
  for (const tab of (appTabs?.tabs ?? []).slice())
    if (tab !== activeTab) appTabs?.close(tab);
  if (activeTab) await loadApp(activeTab);
  else createWindow();
}

function environmentMenuItem() {
  if (
    !primaryBackendUrl ||
    (!alternativeEnvironments.length && backendUrl === primaryBackendUrl)
  )
    return null;
  const environments = [
    { name: "Main", url: primaryBackendUrl },
    ...alternativeEnvironments,
  ];
  return {
    label: "Environment",
    submenu: environments.map(({ name, url }) => ({
      label: `${name} (${new URL(url).host})`,
      type: "radio",
      checked: url === backendUrl,
      click: () => {
        if (url !== backendUrl) void connectToBackend(url);
      },
    })),
  };
}

function iconPath() {
  return app.isPackaged
    ? path.join(process.resourcesPath, "icon.png")
    : path.resolve(__dirname, "../../ui/public/logo-mark.png");
}

function errorPage(error) {
  const message = String(error?.message || error);
  const html = `<!doctype html>
<html>
  <head>
    <meta charset="utf-8">
    <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
    <meta name="color-scheme" content="light dark">
    <title>Startup error - ${appRuntime.name}</title>
    <style>
      body { margin: 0; min-height: 100vh; display: grid; place-items: center; font: 14px system-ui, sans-serif; }
      main { max-width: 520px; padding: 32px; text-align: center; }
      h1 { font-size: 22px; }
      p { color: GrayText; line-height: 1.5; overflow-wrap: anywhere; }
    </style>
  </head>
  <body>
    <main>
      <h1>${appRuntime.name} could not start</h1>
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

// Electron sets `initiatorOrigin` on protocol requests, outside the standard Request type.
async function proxyBackendRequest(
  request: Request & { initiatorOrigin?: string },
) {
  const source = new URL(request.url);
  const headers = new Headers(request.headers);
  const pageUrls = (appTabs?.tabs ?? [])
    .filter((tab) => !tab.webContents.isDestroyed())
    .map((tab) => tab.webContents.getURL());
  if (!isTrustedProxyRequest(pageUrls)) {
    return new Response("Forbidden", { status: 403 });
  }
  const credentials = FramePolicy.proxyCredentials(
    request.method,
    source.pathname,
    request.initiatorOrigin,
  );
  if (credentials === "forbidden")
    return new Response("Forbidden", { status: 403 });
  if (credentials === "method-not-allowed")
    return new Response("Method not allowed", { status: 405 });
  headers.delete("host");
  headers.set("accept-encoding", "identity");
  const targetUrl = backendRequestUrl(backendUrl, request.url);
  if (credentials === "anonymous") {
    headers.delete("cookie");
    return fetch(targetUrl, {
      method: request.method,
      headers,
      redirect: "manual",
    });
  }
  headers.set("origin", APP_ORIGIN);
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
    const details: Electron.CookiesSetDetails = {
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

async function serveDesktopUi(request: Request) {
  if (!backendUrl)
    return new Response("Backend is not configured", { status: 503 });
  if (new URL(request.url).pathname.startsWith("/dashboard/api"))
    return proxyBackendRequest(request);
  if (!["GET", "HEAD"].includes(request.method))
    return new Response("Method not allowed", { status: 405 });
  return desktopUi.current().serve(request);
}

const appNavigations = new WeakMap<Tab, { url: string }>();
const failedAppUrls = new WeakMap<Tab, string>();

async function loadApp(tab: Tab, url = APP_URL) {
  if (!backendUrl) return;
  failedAppUrls.delete(tab);
  const navigation = { url };
  appNavigations.set(tab, navigation);
  // A closed tab's view no longer returns its contents.
  const contents = tab.webContents;
  try {
    await contents.loadURL(url);
  } catch (error) {
    console.warn("Desktop navigation failed", error);
    if (
      contents.isDestroyed() ||
      appNavigations.get(tab) !== navigation ||
      (error instanceof Error &&
        "code" in error &&
        error.code === "ERR_ABORTED")
    )
      return;
    failedAppUrls.set(tab, url);
    await contents
      .loadURL(errorPage(error))
      .catch((pageError) =>
        console.warn("Could not show desktop navigation error", pageError),
      );
  }
}

const closeConfirmation = new CloseConfirmation(app, dialog, appRuntime.name);

function createMenu() {
  const backendSettingsItem = {
    label: "Backend URL…",
    click: () => createSetupWindow(),
  };
  const environmentItem = environmentMenuItem();
  const backendItems = environmentItem
    ? [backendSettingsItem, environmentItem]
    : [backendSettingsItem];
  const settingsItems = menuCommands.items("app", [
    {
      id: "open-settings",
      label: "Settings…",
      accelerator: "CmdOrCtrl+,",
      click: () => sendDesktopCommand("open-settings"),
    },
  ]);
  const checkForUpdatesItem = {
    label: "Check for Updates…",
    enabled: app.isPackaged,
    click: () => void checkForDesktopUpdates(),
  };
  const template = [
    ...(process.platform === "darwin"
      ? [
          {
            label: app.name,
            submenu: [
              { role: "about" },
              ...settingsItems,
              checkForUpdatesItem,
              ...backendItems,
              { type: "separator" },
              { role: "services" },
              { type: "separator" },
              { role: "hide" },
              { role: "hideOthers" },
              { role: "unhide" },
              { type: "separator" },
              {
                label: `Quit ${app.name}`,
                accelerator: "Command+Q",
                click: () =>
                  void closeConfirmation.request(
                    "quit",
                    BaseWindow.getFocusedWindow(),
                  ),
              },
            ],
          },
        ]
      : []),
    {
      label: "File",
      submenu: [
        ...menuCommands.items("file", [
          {
            id: "new-thread",
            label: "New Thread",
            accelerator: "CmdOrCtrl+N",
            click: () => sendDesktopCommand("new-thread"),
          },
          {
            id: "show-command-palette",
            label: "Search Commands and Threads…",
            accelerator: "CmdOrCtrl+K",
            click: () => sendDesktopCommand("show-command-palette"),
          },
        ]),
        ...(process.platform === "darwin"
          ? []
          : [{ type: "separator" }, ...settingsItems, ...backendItems]),
        { type: "separator" },
        ...(process.platform === "darwin"
          ? [
              {
                label: "Close Window",
                click: () =>
                  void closeConfirmation.request(
                    "close",
                    BaseWindow.getFocusedWindow(),
                  ),
              },
            ]
          : [{ role: "quit" }]),
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
        { type: "separator" },
        ...menuCommands.items("edit"),
      ],
    },
    {
      label: "View",
      submenu: [
        ...menuCommands.items("view", [
          {
            id: "toggle-sidebar",
            label: "Toggle Sidebar",
            accelerator: "CmdOrCtrl+B",
            click: () => sendDesktopCommand("toggle-sidebar"),
          },
        ]),
        { type: "separator" },
        {
          label: "Reload",
          accelerator: "CmdOrCtrl+R",
          click: () => {
            if (!activeTab || activeTab.webContents.isDestroyed()) return;
            if (isAppUrl(activeTab.webContents.getURL()))
              activeTab.webContents.reload();
            else
              void loadApp(activeTab, failedAppUrls.get(activeTab) ?? APP_URL);
          },
        },
        // Installed builds too: the renderer is sandboxed with no Node access.
        { role: "toggleDevTools" },
        { type: "separator" },
        { role: "resetZoom" },
        { role: "zoomIn" },
        { role: "zoomOut" },
        { type: "separator" },
        { role: "togglefullscreen" },
      ],
    },
    ...menuCommands.commandMenus(),
    {
      label: "Window",
      ...(process.platform === "darwin" ? { role: "window" } : {}),
      submenu: [
        { role: "minimize" },
        { role: "zoom" },
        { type: "separator" },
        ...tabMenuItems(),
        ...(process.platform === "darwin"
          ? [{ type: "separator" }, { role: "front" }]
          : [
              { type: "separator" },
              // Ctrl+W closes the tab while there is another one.
              {
                role: "close",
                registerAccelerator: (appTabs?.tabs.length ?? 0) <= 1,
              },
            ]),
      ],
    },
    {
      role: "help",
      submenu: [
        ...menuCommands.items("help", [
          {
            id: "show-keyboard-shortcuts",
            label: "Keyboard Shortcuts",
            accelerator: "CmdOrCtrl+/",
            click: () => sendDesktopCommand("show-keyboard-shortcuts"),
          },
        ]),
        { type: "separator" },
        {
          label: `${appRuntime.name} on GitHub`,
          click: () =>
            void shell.openExternal("https://github.com/langchain-ai/open-swe"),
        },
        { type: "separator" },
        {
          label: "Save Diagnostics Report…",
          click: () => void saveDiagnosticsReport(),
        },
      ],
    },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

async function startExternalLogin(navigationUrl: string) {
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
  void shell.openExternal(desktopLoginUrl(backendUrl, flow, navigationUrl));

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

/**
 * Link a Slack or LangSmith account from the desktop app.
 *
 * The consent leg has to run in the user's own browser, which carries neither
 * the app's session cookie nor the flow's state cookie — that mismatch is why
 * connecting used to fail here. So the app starts the flow itself, sends the
 * browser only to the provider, and redeems the loopback handoff under its own
 * session, which is also what decides whose account the connection lands on.
 */
async function startConnectFlow(
  provider: string,
  recoveryToken?: string,
): Promise<boolean | { recoveryToken: string; error?: string }> {
  if (!backendUrl || !isConnectProvider(provider)) return false;
  connectFlows.get(provider)?.cancel();
  connectFlows.delete(provider);

  let flow;
  try {
    flow = await beginLogin({ connect: true });
  } catch (error) {
    if (recoveryToken) {
      return {
        recoveryToken,
        error: "Could not open a local sign-in listener. Please try again.",
      };
    }
    dialog.showErrorBox(
      `${appRuntime.name} could not connect ${provider}`,
      `Could not open a local listener: ${error.message}`,
    );
    return false;
  }
  connectFlows.set(provider, flow);
  try {
    const started = await backendFetch(
      connectLoginUrl(backendUrl, provider, flow, recoveryToken),
      { redirect: "manual" },
    );
    const location = started.headers.get("location");
    if (recoveryToken && started.status === 400) {
      return {
        recoveryToken,
        error:
          "This recovery link has expired or is no longer valid. Cancel and connect Slack again to restart recovery.",
      };
    }
    if (!location) {
      throw new Error(`Backend did not start the flow (${started.status})`);
    }
    const consent = new URL(location);
    const localConsent =
      isDevelopment &&
      consent.protocol === "http:" &&
      consent.origin === new URL(backendUrl).origin;
    if (
      (consent.protocol !== "https:" && !localConsent) ||
      consent.username ||
      consent.password
    ) {
      throw new Error("Backend returned an invalid consent URL");
    }
    await shell.openExternal(consent.href);

    const code = await flow.code;
    if (connectFlows.get(provider) !== flow) return false;
    if (!code) {
      return recoveryToken
        ? {
            recoveryToken,
            error: "GitHub connection was cancelled. Please try again.",
          }
        : false;
    }

    const exchange = await backendFetch(
      connectExchangeUrl(backendUrl, provider),
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ code, verifier: flow.verifier }),
      },
    );
    if (!exchange.ok) {
      if (exchange.status === 409) {
        const payload: unknown = await exchange.json();
        const detail =
          payload && typeof payload === "object" && "detail" in payload
            ? payload.detail
            : null;
        if (detail && typeof detail === "object" && "code" in detail) {
          if (
            provider === "slack" &&
            detail.code === "slack_account_conflict" &&
            "recovery_token" in detail &&
            typeof detail.recovery_token === "string" &&
            detail.recovery_token &&
            detail.recovery_token.length <= 16_384
          ) {
            return { recoveryToken: detail.recovery_token };
          }
          if (
            recoveryToken &&
            detail.code === "slack_recovery_failed" &&
            "message" in detail &&
            typeof detail.message === "string" &&
            detail.message.length <= 2_048
          ) {
            return { recoveryToken, error: detail.message };
          }
        }
      }
      if (recoveryToken && exchange.status === 400) {
        return {
          recoveryToken,
          error:
            "This recovery link has expired or is no longer valid. Cancel and connect Slack again to restart recovery.",
        };
      }
      throw new Error(`Backend rejected the connection (${exchange.status})`);
    }
    if (provider === "github") {
      await completeExternalLogin(flow.verifier, code, exchange);
    }
    return true;
  } catch (error) {
    if (recoveryToken) {
      return {
        recoveryToken,
        error:
          "Could not connect GitHub. Try again and choose the GitHub account you previously used with Robin.",
      };
    }
    dialog.showErrorBox(
      `${appRuntime.name} could not connect ${provider}`,
      error.message,
    );
    return false;
  } finally {
    if (connectFlows.get(provider) === flow) {
      flow.cancel();
      connectFlows.delete(provider);
    }
  }
}

/** Call the backend as the app: its own origin, and the session it holds. */
async function backendFetch(url, init: any = {}) {
  const headers = new Headers(init.headers);
  headers.set("origin", APP_ORIGIN);
  const cookies = await session.defaultSession.cookies.get({ url });
  if (cookies.length) {
    headers.set(
      "cookie",
      cookies.map(({ name, value }) => `${name}=${value}`).join("; "),
    );
  }
  return fetch(url, { ...init, headers });
}

async function completeExternalLogin(verifier, code, exchanged?: Response) {
  const response =
    exchanged ??
    (await fetch(desktopExchangeUrl(backendUrl), {
      method: "POST",
      headers: { "content-type": "application/json", origin: APP_ORIGIN },
      body: JSON.stringify({ code, verifier }),
    }));
  if (!response.ok) {
    throw new Error(`Backend rejected the sign-in (${response.status})`);
  }
  const payload = await response.json();
  if (typeof payload?.session !== "string") {
    throw new Error("Backend returned no session");
  }
  await session.defaultSession.cookies.set({
    url: backendUrl,
    name: SESSION_COOKIE_NAME,
    value: payload.session,
    path: "/",
    httpOnly: true,
    secure: new URL(backendUrl).protocol === "https:",
    expirationDate: Date.now() / 1000 + Number(payload.expires_in),
  });
  if (exchanged) return;

  const window =
    mainWindow && !mainWindow.isDestroyed() ? mainWindow : createWindow();
  if (window.isMinimized()) window.restore();
  window.show();
  window.focus();
  app.focus({ steal: true });
  if (activeTab) await loadApp(activeTab);
}

async function handleNavigation(tab: Tab, event, url, inNewTab = false) {
  if (desktopLinks.slackUrl(url)) {
    event.preventDefault();
    void desktopLinks
      .openSlack(url)
      .catch((error: unknown) =>
        dialog.showErrorBox(
          `${appRuntime.name} could not open Slack`,
          error instanceof Error ? error.message : String(error),
        ),
      );
    return;
  }
  if (isAppLoginUrl(url)) {
    event.preventDefault();
    void startExternalLogin(url);
    return;
  }
  if (isAppUrl(url)) return;
  event.preventDefault();
  const contents = tab.webContents;
  const navigationBackend = backendUrl;
  const targetUrl =
    (navigationBackend && localCallbackUrl(url, navigationBackend)) ||
    (await desktopLinks.appUrl(url, navigationBackend));
  if (contents.isDestroyed() || backendUrl !== navigationBackend) return;
  if (targetUrl) {
    if (inNewTab) openTab(targetUrl, tab, { background: true });
    else void contents.loadURL(targetUrl);
    return;
  }
  if (
    URL.canParse(url) &&
    ["http:", "https:", "mailto:"].includes(new URL(url).protocol)
  ) {
    void shell.openExternal(url);
  }
}

function showEditContextMenu(tab: Tab, params) {
  const { editFlags, isEditable, misspelledWord, dictionarySuggestions } =
    params;
  const linkURL = params.linkURL;
  const hasSelection = params.selectionText.trim().length > 0;
  if (!isEditable && !hasSelection && !linkURL) return;
  const template = [];
  if (isEditable && misspelledWord) {
    for (const suggestion of dictionarySuggestions)
      template.push({
        label: suggestion,
        click: () => tab.webContents.replaceMisspelling(suggestion),
      });
    if (!dictionarySuggestions.length)
      template.push({ label: "No suggestions", enabled: false });
    template.push(
      {
        label: "Add to Dictionary",
        click: () =>
          tab.webContents.session.addWordToSpellCheckerDictionary(
            misspelledWord,
          ),
      },
      { type: "separator" },
    );
  }
  if (isEditable)
    template.push(
      { role: "cut", enabled: editFlags.canCut },
      { role: "copy", enabled: editFlags.canCopy },
      { role: "paste", enabled: editFlags.canPaste },
      { type: "separator" },
      { role: "selectAll", enabled: editFlags.canSelectAll },
    );
  else if (hasSelection)
    template.push({ role: "copy", enabled: editFlags.canCopy });
  if (linkURL) {
    if (template.length) template.push({ type: "separator" });
    if (isAppUrl(linkURL) && !isAppLoginUrl(linkURL))
      template.push({
        label: "Open Link in New Tab",
        click: () => openInNewTab(tab, linkURL),
      });
    template.push({
      label: "Copy Link Address",
      click: () => clipboard.writeText(linkURL),
    });
  }
  Menu.buildFromTemplate(template).popup();
}

function openInNewTab(tab: Tab, url) {
  const target =
    backendUrl && isAppUrl(url) && !isAppLoginUrl(url)
      ? backendRequestUrl(backendUrl, url) + new URL(url).hash
      : url;
  void handleNavigation(tab, { preventDefault() {} }, target, true);
}

function openTab(
  url: string,
  after: Tab | null = activeTab,
  { background = false } = {},
): Tab | null {
  if (!appTabs || !mainWindow || mainWindow.isDestroyed()) {
    createWindow(url);
    return activeTab;
  }
  const tab = appTabs.add(after);
  if (background) appTabs.openInBackground(tab);
  else appTabs.activateWhenLoaded(tab);
  void loadApp(tab, url);
  return tab;
}

function tabMenuItems() {
  const count = appTabs?.tabs.length ?? 0;
  return [
    {
      label: "New Tab",
      accelerator: "CmdOrCtrl+T",
      enabled: Boolean(backendUrl),
      click: () => openTab(APP_URL),
    },
    {
      label: "Close Tab",
      // Only with another tab left: closing the last tab closes the window.
      ...(count > 1 ? { accelerator: "CmdOrCtrl+W" } : {}),
      enabled: count > 1,
      click: (_: unknown, window?: Electron.BaseWindow) =>
        window === mainWindow && activeTab && appTabs?.close(activeTab),
    },
    {
      label: "Close Other Tabs",
      accelerator: "CmdOrCtrl+Alt+W",
      enabled: count > 1,
      // The app menu also fires from other windows, such as settings.
      click: (_: unknown, window?: Electron.BaseWindow) =>
        window === mainWindow && activeTab && appTabs?.closeOthers(activeTab),
    },
    { type: "separator" },
    ...(
      [
        ["Show Next Tab", 1, "CmdOrCtrl+Alt+Right", "Ctrl+Tab"],
        ["Show Previous Tab", -1, "CmdOrCtrl+Alt+Left", "Ctrl+Shift+Tab"],
      ] as const
    ).flatMap(([label, step, accelerator, alias]) =>
      [accelerator, alias].map((key, index) => ({
        label,
        accelerator: key,
        visible: index === 0,
        enabled: count > 1,
        click: () => appTabs?.cycle(step),
      })),
    ),
    ...Array.from({ length: 9 }, (_, index) => ({
      label: index === 8 ? "Show Last Tab" : `Show Tab ${index + 1}`,
      accelerator: `CmdOrCtrl+${index + 1}`,
      visible: false,
      enabled: count > 1,
      click: () => appTabs?.select(index === 8 ? -1 : index),
    })),
  ];
}

function createTabView(): Tab {
  const tab = new WebContentsView({
    webPreferences: {
      contextIsolation: true,
      navigateOnDragDrop: false,
      nodeIntegration: false,
      preload: path.join(__dirname, "preload.cjs"),
      sandbox: true,
    },
  });
  const contents = tab.webContents;
  contents.on("render-process-gone", (_event, details) =>
    lifecycleLog.record("render-process-gone", {
      reason: details.reason,
      exitCode: details.exitCode,
    }),
  );
  // Cmd-click, middle-click and target="_blank" links.
  contents.setWindowOpenHandler(({ url }) => {
    openInNewTab(tab, url);
    return { action: "deny" };
  });
  contents.on("will-navigate", (event, url) =>
    handleNavigation(tab, event, url),
  );
  contents.on("will-frame-navigate", (event) => {
    if (event.isMainFrame) return;
    const initiatedByApp =
      event.initiator?.frameTreeNodeId === contents.mainFrame.frameTreeNodeId;
    const frames: { url: string; origin: string }[] = [];
    for (let frame = event.frame; frame; frame = frame.parent)
      frames.push({ url: frame.url, origin: frame.origin });
    if (FramePolicy.blocksNavigation(frames, event.url, initiatedByApp))
      event.preventDefault();
  });
  contents.on("will-redirect", (event) => {
    if (event.isMainFrame) handleNavigation(tab, event, event.url);
  });
  contents.on("will-attach-webview", (event) => event.preventDefault());
  contents.on("did-navigate", () => {
    tabMenuCommands.delete(tab);
    tabThreadScopes.delete(tab);
    if (activeTab === tab) {
      if (menuCommands.reset()) createMenu();
      approvalPanel.reset();
      approvalPanel.sync();
    }
  });
  contents.on("did-navigate-in-page", () => {
    if (activeTab === tab) approvalPanel.sync();
  });
  contents.on("did-finish-load", () =>
    contents.send(
      "desktop:fullscreen-change",
      Boolean(mainWindow?.isFullScreen()),
    ),
  );
  contents.on("console-message", (_event, details) =>
    rendererConsole.push(normalizeConsoleMessage(details)),
  );
  contents.on("context-menu", (_event, params) =>
    showEditContextMenu(tab, params),
  );
  return tab;
}

function createWindow(initialUrl = APP_URL) {
  if (!backendUrl) return createSetupWindow();
  const window = new BaseWindow({
    title: appRuntime.name,
    width: 1440,
    height: 900,
    minWidth: 480,
    minHeight: 600,
    backgroundColor: nativeTheme.shouldUseDarkColors ? "#111216" : "#ffffff",
    icon: iconPath(),
    show: false,
    ...(process.platform === "darwin"
      ? {
          titleBarStyle: "hiddenInset",
          trafficLightPosition: { x: 16, y: 14 },
        }
      : {}),
  });
  mainWindow = window;
  appTabs = new AppTabs(window, {
    createTab: createTabView,
    createStrip: () =>
      new WebContentsView({
        webPreferences: {
          contextIsolation: true,
          nodeIntegration: false,
          sandbox: true,
        },
      }),
    onActivate: onTabActivated,
    onNewTab: () => openTab(APP_URL),
    onTabsChanged: () => createMenu(),
    showMenu: (items) => Menu.buildFromTemplate(items).popup(),
    dark: () => nativeTheme.shouldUseDarkColors,
  });
  lifecycleLog.record("window-created", { id: window.id });
  window.on("close", () =>
    lifecycleLog.record("window-close", { id: window.id }),
  );
  window.on("closed", () => {
    lifecycleLog.record("window-closed");
    if (mainWindow !== window) return;
    mainWindow = null;
    appTabs = null;
    activeTab = null;
    approvalPanel.reset();
    if (menuCommands.reset()) createMenu();
  });
  window.on("focus", () => activeTab?.webContents.focus());
  window.on("resize", () => approvalPanel.sync());
  window.on("enter-full-screen", () =>
    sendToTabs("desktop:fullscreen-change", true),
  );
  window.on("leave-full-screen", () =>
    sendToTabs("desktop:fullscreen-change", false),
  );
  const tab = openTab(initialUrl, null);
  // Shown once the first page has painted, as a browser window's ready-to-show.
  const show = () => {
    if (!window.isDestroyed() && !window.isVisible()) window.show();
  };
  tab?.webContents.once("did-finish-load", show);
  tab?.webContents.once("did-fail-load", show);
  setTimeout(show, 3000);
  return window;
}

async function collectPerfSpans(tab: Tab | null) {
  if (!tab || tab.webContents.isDestroyed()) return null;
  try {
    const exported = await tab.webContents.executeJavaScript(
      "window.__openSwePerf ? window.__openSwePerf.export() : null",
      true,
    );
    return typeof exported === "string" ? exported : null;
  } catch {
    return null;
  }
}

async function saveDiagnosticsReport() {
  const window = mainWindow && !mainWindow.isDestroyed() ? mainWindow : null;
  const report = buildDiagnosticsReport({
    app: {
      name: appRuntime.name,
      version: app.getVersion(),
      isPackaged: app.isPackaged,
      electron: process.versions.electron,
      chrome: process.versions.chrome,
      node: process.versions.node,
      platform: process.platform,
      arch: process.arch,
      osRelease: require("node:os").release(),
      locale: app.getLocale(),
    },
    backendHost: backendUrl ? new URL(backendUrl).host : null,
    renderer: rendererConsole.entries(),
    main: mainConsole.entries(),
    lifecycle: lifecycleLog.read(),
    perf: await collectPerfSpans(activeTab),
  });
  const options = {
    title: "Save diagnostics report",
    defaultPath: path.join(app.getPath("downloads"), diagnosticsFileName()),
    filters: [{ name: "Text", extensions: ["txt"] }],
  };
  const result = window
    ? await dialog.showSaveDialog(window, options)
    : await dialog.showSaveDialog(options);
  if (result.canceled || !result.filePath) return;
  try {
    fs.writeFileSync(result.filePath, report, "utf8");
    shell.showItemInFolder(result.filePath);
  } catch (error) {
    dialog.showErrorBox("Could not save the diagnostics report", error.message);
  }
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
      const url = storeBackendUrl(value);
      if (primaryBackendUrl && primaryBackendUrl !== url)
        await clearBackendCookies(primaryBackendUrl);
      primaryBackendUrl = url;
      alternativeEnvironments = [];
      await connectToBackend(url);
      void loadAlternativeEnvironments();
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

/** The thread's browser panel frames a LangSmith page, which cannot name the app as a frame ancestor. */
function configureFraming() {
  session.defaultSession.webRequest.onHeadersReceived(
    { urls: ["https://*/*"] },
    (details, callback) => {
      // Requests no page made, like the main process's own, carry no webContentsId.
      const page =
        details.resourceType === "subFrame" &&
        details.webContentsId !== undefined
          ? webContents.fromId(details.webContentsId)?.getURL()
          : undefined;
      callback(
        isAppUrl(page ?? "")
          ? { responseHeaders: headersFramedByApp(details.responseHeaders) }
          : {},
      );
    },
  );
}

function configurePermissions() {
  session.defaultSession.setPermissionRequestHandler(
    (requester, permission, callback, details) => {
      callback(
        isTrustedPermissionRequest(
          permission,
          details.requestingUrl || requester.getURL(),
        ),
      );
    },
  );
  session.defaultSession.setPermissionCheckHandler(
    (_webContents, permission, requestingOrigin) =>
      isTrustedPermissionRequest(permission, requestingOrigin),
  );
}

const desktopLinks = new DesktopLinks({
  backendUrl: () => backendUrl,
  dashboardOrigins: (url: string) => desktopEnvironments.dashboardOrigins(url),
  hasPage: (pathname: string, url: string) =>
    url === backendUrl
      ? desktopUi.current().hasPage(pathname)
      : Promise.resolve(false),
  openApp: openDesktopLink,
  openExternal: (url: string) => shell.openExternal(url),
  fetchBackend: backendFetch,
});

function openDesktopLink(target: string) {
  const existingWindow =
    mainWindow && !mainWindow.isDestroyed() ? mainWindow : null;
  const window = existingWindow || createWindow(target);
  if (window.isMinimized()) window.restore();
  window.show();
  window.focus();
  if (existingWindow && activeTab) void loadApp(activeTab, target);
  return true;
}

app.on("will-continue-activity", (event, type) => {
  if (type === "NSUserActivityTypeBrowsingWeb") event.preventDefault();
});

app.on("continue-activity", (event, type, _userInfo, details) => {
  if (type !== "NSUserActivityTypeBrowsingWeb" || !details.webpageURL) return;
  event.preventDefault();
  desktopLinks.receive(details.webpageURL);
});

const hasSingleInstanceLock = app.requestSingleInstanceLock();
lifecycleLog.record("single-instance-lock", {
  acquired: hasSingleInstanceLock,
});
if (!hasSingleInstanceLock) {
  app.quit();
} else {
  app.on("second-instance", (_event, commandLine) => {
    lifecycleLog.record("second-instance");
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
    app.setName(appRuntime.name);
    lifecycleLog.record("ready");
    powerMonitor.on("shutdown", () => lifecycleLog.record("system-shutdown"));
    migrateStoredConfig();
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
    primaryBackendUrl = backendUrl;
    void shareBackendSession();

    protocol.handle("robin", serveDesktopUi);
    configurePermissions();
    configureFraming();
    configureDesktopIpc();
    createMenu();
    void loadAlternativeEnvironments();
    if (!desktopLinks.ready()) createWindow();
    configureAutoUpdater();

    app.on("activate", () => {
      if (BaseWindow.getAllWindows().length === 0) createWindow();
    });
  });

  app.on("window-all-closed", () => {
    if (process.platform !== "darwin") app.quit();
  });

  app.on("before-quit", (event) => {
    if (quitting) return;
    event.preventDefault();
    quitting = true;
    void (localAccessBridge?.close() ?? Promise.resolve()).finally(() => {
      localAccessBridge = null;
      app.quit();
    });
  });
}
