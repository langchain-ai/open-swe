const { randomBytes } = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const {
  app,
  BrowserWindow,
  ipcMain,
  Menu,
  dialog,
  nativeTheme,
  net,
  protocol,
  safeStorage,
  session,
  shell,
} = require("electron");
const { autoUpdater } = require("electron-updater");
const { BackendSupervisor } = require("./backend-supervisor.cjs");
const { LocalThreadStore } = require("./local-thread-store.cjs");
const {
  addWorktree,
  captureCheckpoint,
  checkoutBranch,
  checkpointRef,
  currentBranch,
  localBranches,
  deleteRefs,
  readBranchDiff,
  readDiff,
  removeWorktree,
  repoRoot,
  repositoryMetadata,
  restoreWorktree,
  validBranchName,
} = require("./git-diff.cjs");
const {
  closeAllTerminals,
  configureTerminalIpc,
  closeThreadTerminals,
} = require("./terminal-manager.cjs");
const {
  addProject,
  readProjects,
  removeProject,
} = require("./project-store.cjs");
const { beginLogin } = require("./login-server.cjs");
const { OpenAiOAuthManager } = require("./openai-oauth.cjs");
const { isDesktopCommandId } = require("./commands.cjs");
const {
  APP_ORIGIN,
  APP_URL,
  SESSION_COOKIE_NAME,
  appRedirectUrl,
  backendRequestUrl,
  desktopExchangeUrl,
  connectExchangeUrl,
  connectLoginUrl,
  desktopLoginUrl,
  isAppLoginUrl,
  isAppUrl,
  isConnectProvider,
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
let loginFlow = null;
const connectFlows = new Map();
let quitting = false;
let localThreadStore = null;
let lastActivity = {};
let backendSupervisor = null;
let openAiOAuth = null;
let updateState = { status: "idle" };

function setUpdateState(status, version) {
  updateState = { status, ...(version ? { version } : {}) };
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send("desktop:update-state", updateState);
  }
}

function configureAutoUpdater() {
  if (!app.isPackaged) return;
  autoUpdater.autoDownload = true;
  autoUpdater.autoInstallOnAppQuit = true;
  autoUpdater.allowPrerelease = false;
  autoUpdater.on("update-available", (info) =>
    setUpdateState("downloading", info.version),
  );
  autoUpdater.on("update-downloaded", (info) =>
    setUpdateState("ready", info.version),
  );
  autoUpdater.on("error", (error) => {
    console.warn("Desktop update failed", error);
    setUpdateState("idle", undefined);
  });
  void autoUpdater
    .checkForUpdates()
    .catch((error) =>
      console.warn("Could not check for desktop updates", error),
    );
}

function sendDesktopCommand(commandId) {
  if (!isDesktopCommandId(commandId) || !mainWindow || mainWindow.isDestroyed())
    return;
  mainWindow.webContents.send("desktop:command", commandId);
}

function requireTrustedDesktopIpc(event) {
  const senderUrl = event.senderFrame?.url || event.sender.getURL();
  if (!isAppUrl(senderUrl)) throw new Error("Forbidden");
}

function projectsPath() {
  return path.join(app.getPath("userData"), "desktop-projects.json");
}

function worktreesPath() {
  return path.join(app.getPath("userData"), "worktrees");
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

/**
 * Worktrees the app made are the only ones it may take over or delete: one the
 * user created themselves is theirs, and the backend refuses to run in it.
 */
function managedWorktree(candidate) {
  return typeof candidate === "string" &&
    path.isAbsolute(candidate) &&
    pathIsInside(worktreesPath(), path.normalize(candidate))
    ? path.normalize(candidate)
    : null;
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

function threadRoot(thread) {
  const project = thread ? registeredProject(thread.cwd) : null;
  if (!project) return null;
  return thread.worktreePath || project;
}

function resolveLocalProjectPath(localSessionId, value) {
  const localSession = localThreadStore.get(localSessionId);
  if (!localSession || typeof value !== "string" || value.length === 0)
    return null;
  try {
    const root = threadRoot(localSession);
    if (!root) return null;
    const projectRoot = fs.realpathSync(root);
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

async function recordLocalCheckpoint(thread) {
  const repo = await repoRoot(thread.worktreePath || thread.cwd);
  if (!repo) return thread;
  const ref = checkpointRef(thread.id);
  await captureCheckpoint(repo, ref);
  const branch = await currentBranch(repo);
  return localThreadStore.setCheckpoint(thread.id, { repo, ref, branch });
}

async function syncThreadBranch(thread) {
  if (!thread?.checkpoint.repo) return thread;
  const branch = await currentBranch(thread.checkpoint.repo);
  if (!branch || branch === thread.checkpoint.branch) return thread;
  return (
    localThreadStore.setCheckpoint(thread.id, {
      ...thread.checkpoint,
      branch,
    }) ?? thread
  );
}

/**
 * A worktree thread owns its checkout outright. A thread running in the
 * project's own checkout shares it with every other session, so the branch that
 * happens to be checked out is only this thread's while this thread is running.
 */
async function diffThread(threadId) {
  const thread = localThreadStore.get(threadId);
  if (!thread) return thread;
  if (thread.worktreePath) return syncThreadBranch(thread);
  const activity = await backendSupervisor.threadActivity();
  return activity?.[threadId] === "running" ? syncThreadBranch(thread) : thread;
}

async function createThreadWorktree(thread, baseBranch) {
  const repo = await repoRoot(thread.cwd);
  if (!repo) throw new Error("Local projects must be git repositories");
  const base =
    (await validBranchName(repo, baseBranch)) ??
    (await currentBranch(repo)) ??
    "HEAD";
  const token = randomBytes(4).toString("hex");
  const worktree = path.join(
    worktreesPath(),
    `${path.basename(repo)}-${token}`,
  );
  await addWorktree(repo, worktree, `open-swe/local-${token}`, base);
  return localThreadStore.setWorktree(thread.id, worktree, true);
}

/**
 * Two agents in one working tree overwrite each other's edits and fight over
 * its branch, and the backend now runs local threads concurrently, so a tree an
 * agent is working in is off limits to everything else.
 */
async function assertWorkspaceFree(root, exceptThreadId = null) {
  const activity = await backendSupervisor.threadActivity();
  if (!activity) throw new Error("Could not reach the local Open SWE backend");
  const busy = localThreadStore
    .list()
    .find(
      (thread) =>
        thread.id !== exceptThreadId &&
        activity[thread.id] === "running" &&
        threadRoot(thread) === root,
    );
  if (busy)
    throw new Error(
      `“${busy.title}” is working in ${path.basename(root)}. Stop it, or use a worktree.`,
    );
}

/**
 * A branch can only be checked out in one working tree, so a thread starting on
 * one that already has a worktree of this app's runs in that worktree rather
 * than trying to create a second checkout of it.
 */
async function startThreadWorktree(thread, baseBranch) {
  const project = await repoRoot(thread.cwd);
  const existing = project
    ? (await localBranches(project)).find((ref) => ref.name === baseBranch)
        ?.worktreePath
    : null;
  if (!existing || !managedWorktree(existing))
    return createThreadWorktree(thread, baseBranch);
  await assertWorkspaceFree(existing, thread.id);
  return localThreadStore.setWorktree(thread.id, existing);
}

async function moveThreadWorkspace(thread, worktreePath) {
  if ((thread.worktreePath || null) === worktreePath) return thread;
  await closeThreadTerminals(thread.id);
  return recordLocalCheckpoint(
    localThreadStore.setWorktree(thread.id, worktreePath),
  );
}

async function ensureThreadWorktree(thread) {
  if (!thread?.worktreePath || fs.existsSync(thread.worktreePath))
    return thread;
  const repo = registeredProject(thread.cwd) && (await repoRoot(thread.cwd));
  const branch = thread.checkpoint.branch;
  if (!repo || !branch) return thread;
  await restoreWorktree(repo, thread.worktreePath, branch);
  return thread;
}

/**
 * Every worktree this app made for the thread, including ones it has since
 * moved off, minus any another thread is in or owns.
 */
async function discardThreadWorktree(thread) {
  const others = localThreadStore.list().filter((it) => it.id !== thread.id);
  const owned = thread.ownedWorktrees.filter(
    (worktree) =>
      managedWorktree(worktree) &&
      !others.some(
        (other) =>
          other.worktreePath === worktree ||
          other.ownedWorktrees.includes(worktree),
      ),
  );
  if (!owned.length) return;
  const repo = await repoRoot(thread.cwd);
  if (!repo) return;
  for (const worktree of owned) await removeWorktree(repo, worktree);
}

function configureDesktopIpc() {
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
    if (updateState.status !== "ready") return false;
    quitting = true;
    await Promise.all([
      closeAllTerminals(),
      backendSupervisor?.close(),
      openAiOAuth?.close(),
    ]);
    autoUpdater.quitAndInstall(false, true);
    return true;
  });

  ipcMain.handle("desktop:projects", (event) => {
    requireTrustedDesktopIpc(event);
    return listProjects();
  });

  ipcMain.handle("desktop:project-branches", async (event, cwd) => {
    requireTrustedDesktopIpc(event);
    const project = typeof cwd === "string" ? registeredProject(cwd) : null;
    if (!project) return { current: null, branches: [] };
    const [current, branches] = await Promise.all([
      currentBranch(project),
      localBranches(project),
    ]);
    return { current, branches };
  });

  ipcMain.handle("desktop:checkout-project-branch", async (event, input) => {
    requireTrustedDesktopIpc(event);
    const project =
      input && typeof input.cwd === "string"
        ? registeredProject(input.cwd)
        : null;
    if (!project) throw new Error("Project is not registered");
    await assertWorkspaceFree(project);
    return checkoutBranch(project, input.branch);
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

  ipcMain.handle("desktop:connect-service", async (event, provider) => {
    requireTrustedDesktopIpc(event);
    return startConnectFlow(provider);
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
  ipcMain.handle("desktop:start-local-thread", async (event, input) => {
    requireTrustedDesktopIpc(event);
    const cwd =
      typeof input?.cwd === "string" ? registeredProject(input.cwd) : null;
    if (!cwd)
      throw new Error(
        "Add a valid project to Open SWE before starting a local agent",
      );
    await backendSupervisor.start();
    if (input?.workspaceMode !== "worktree") await assertWorkspaceFree(cwd);
    let thread = localThreadStore.create({ ...input, cwd });
    try {
      if (input?.workspaceMode === "worktree")
        thread = await startThreadWorktree(thread, input?.baseBranch);
      thread = await recordLocalCheckpoint(thread);
      await backendSupervisor.createThread(thread.id);
    } catch (error) {
      localThreadStore.delete(thread.id);
      if (thread.checkpoint.repo && thread.checkpoint.ref)
        deleteRefs(thread.checkpoint.repo, [thread.checkpoint.ref]);
      await discardThreadWorktree(thread).catch(() => {});
      throw error;
    }
    return thread;
  });
  ipcMain.handle("desktop:get-local-prompt", (event, threadId) => {
    requireTrustedDesktopIpc(event);
    return localThreadStore.pendingPrompt(threadId);
  });
  ipcMain.handle("desktop:clear-local-prompt", (event, threadId) => {
    requireTrustedDesktopIpc(event);
    return localThreadStore.clearPrompt(threadId);
  });
  ipcMain.handle("desktop:get-local-thread", async (event, threadId) => {
    requireTrustedDesktopIpc(event);
    const thread = await ensureThreadWorktree(localThreadStore.get(threadId));
    if (!thread) return null;
    await backendSupervisor.createThread(thread.id);
    return thread;
  });
  ipcMain.handle("desktop:list-local-threads", (event) => {
    requireTrustedDesktopIpc(event);
    return localThreadStore.list();
  });
  ipcMain.handle("desktop:local-activity", async (event) => {
    requireTrustedDesktopIpc(event);
    const activity = await backendSupervisor.threadActivity();
    if (!activity) return lastActivity;
    for (const [threadId, status] of Object.entries(lastActivity)) {
      if (status === "running" && activity[threadId] !== "running")
        localThreadStore.update(threadId, { viewed: false });
    }
    lastActivity = activity;
    return activity;
  });
  ipcMain.handle("desktop:update-local-thread", async (event, input) => {
    requireTrustedDesktopIpc(event);
    return localThreadStore.update(input?.threadId, {
      ...(typeof input?.viewed === "boolean" ? { viewed: input.viewed } : {}),
      ...(typeof input?.archived === "boolean"
        ? { archived: input.archived }
        : {}),
      ...(typeof input?.modelId === "string" ? { modelId: input.modelId } : {}),
      ...(typeof input?.effort === "string" ? { effort: input.effort } : {}),
    });
  });
  ipcMain.handle("desktop:delete-local-thread", async (event, threadId) => {
    requireTrustedDesktopIpc(event);
    const thread = localThreadStore.get(threadId);
    if (!thread) return false;
    const activity = await backendSupervisor.threadActivity();
    if (!activity || activity[threadId] === "running")
      throw new Error("Stop the local agent before deleting it");
    await closeThreadTerminals(threadId);
    try {
      await backendSupervisor.deleteThread(threadId);
    } catch (error) {
      console.warn("Could not delete local LangGraph thread", error);
    }
    localThreadStore.delete(threadId);
    if (thread.checkpoint.repo && thread.checkpoint.ref)
      deleteRefs(thread.checkpoint.repo, [thread.checkpoint.ref]);
    await discardThreadWorktree(thread);
    return true;
  });
  /**
   * Move a thread onto a branch. A branch already checked out somewhere can
   * only be worked on there, so the thread follows it: into that worktree, or
   * back into the project's own checkout. Anything else is checked out in the
   * tree the thread is already in.
   */
  ipcMain.handle("desktop:set-local-branch", async (event, input) => {
    requireTrustedDesktopIpc(event);
    const thread = localThreadStore.get(input?.threadId);
    if (!thread) throw new Error("Local thread not found");
    const project = registeredProject(thread.cwd);
    if (!project) throw new Error("Project is not registered");
    const branch = await validBranchName(project, input?.branch);
    if (!branch) throw new Error("Branch name is required");
    const activity = await backendSupervisor.threadActivity();
    if (!activity || activity[thread.id] === "running")
      throw new Error("Stop the local agent before switching its branch");

    const ref = (await localBranches(project)).find(
      (candidate) => candidate.name === branch,
    );
    if (ref?.worktreePath) {
      if (!managedWorktree(ref.worktreePath))
        throw new Error(
          `“${branch}” is checked out in ${ref.worktreePath}, which Open SWE does not manage.`,
        );
      await assertWorkspaceFree(ref.worktreePath, thread.id);
      return moveThreadWorkspace(thread, ref.worktreePath);
    }
    if (ref?.current) {
      await assertWorkspaceFree(project, thread.id);
      return moveThreadWorkspace(thread, null);
    }
    const root = threadRoot(thread);
    await assertWorkspaceFree(root, thread.id);
    await checkoutBranch(root, branch);
    return syncThreadBranch(thread);
  });

  ipcMain.handle("desktop:get-local-diff", async (event, threadId) => {
    requireTrustedDesktopIpc(event);
    const thread = await diffThread(threadId);
    if (
      !thread ||
      !registeredProject(thread.cwd) ||
      !thread.checkpoint.repo ||
      !thread.checkpoint.ref
    )
      return { status: "missing", files: [], truncated: false };
    try {
      const [diff, repository] = await Promise.all([
        readDiff(thread.checkpoint.repo, thread.checkpoint.ref),
        repositoryMetadata(thread.checkpoint.repo),
      ]);
      return { ...diff, repository };
    } catch {
      return { status: "error", files: [], truncated: false };
    }
  });
  ipcMain.handle("desktop:get-local-pr-diff", async (event, threadId) => {
    requireTrustedDesktopIpc(event);
    const thread = await diffThread(threadId);
    if (!thread || !registeredProject(thread.cwd) || !thread.checkpoint.repo)
      return { status: "missing", files: [], truncated: false };
    try {
      const repository = await repositoryMetadata(thread.checkpoint.repo);
      if (!repository.pr)
        return { status: "missing", files: [], truncated: false, repository };
      const diff = await readBranchDiff(
        thread.checkpoint.repo,
        repository.pr.baseRef,
        thread.checkpoint.branch,
      );
      return { ...diff, repository };
    } catch {
      return { status: "error", files: [], truncated: false };
    }
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
  if (!isTrustedProxyRequest(pageUrl)) {
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

async function serveBundledUi(request) {
  if (!backendUrl)
    return new Response("Backend is not configured", { status: 503 });
  const url = new URL(request.url);
  if (url.pathname.startsWith("/dashboard/api"))
    return proxyBackendRequest(request);
  if (
    url.pathname === "/local-graph" ||
    url.pathname.startsWith("/local-graph/")
  )
    return backendSupervisor.proxy(request);
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

/**
 * Link a Slack or Notion account from the desktop app.
 *
 * The consent leg has to run in the user's own browser, which carries neither
 * the app's session cookie nor the flow's state cookie — that mismatch is why
 * connecting used to fail here. So the app starts the flow itself, sends the
 * browser only to the provider, and redeems the loopback handoff under its own
 * session, which is also what decides whose account the connection lands on.
 */
async function startConnectFlow(provider) {
  if (!backendUrl || !isConnectProvider(provider)) return false;
  connectFlows.get(provider)?.cancel();
  connectFlows.delete(provider);

  let flow;
  try {
    flow = await beginLogin({ connect: true });
  } catch (error) {
    dialog.showErrorBox(
      `${appRuntime.name} could not connect ${provider}`,
      `Could not open a local listener: ${error.message}`,
    );
    return false;
  }
  connectFlows.set(provider, flow);
  try {
    const started = await backendFetch(
      connectLoginUrl(backendUrl, provider, flow),
      { redirect: "manual" },
    );
    const location = started.headers.get("location");
    if (!location) {
      throw new Error(`Backend did not start the flow (${started.status})`);
    }
    await shell.openExternal(location);

    const code = await flow.code;
    if (connectFlows.get(provider) !== flow) return false;
    if (!code) return false;

    const exchange = await backendFetch(
      connectExchangeUrl(backendUrl, provider),
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ code, verifier: flow.verifier }),
      },
    );
    if (!exchange.ok) {
      throw new Error(`Backend rejected the connection (${exchange.status})`);
    }
    return true;
  } catch (error) {
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
  await session.defaultSession.cookies.set({
    url: backendUrl,
    name: SESSION_COOKIE_NAME,
    value: payload.session,
    path: "/",
    httpOnly: true,
    secure: new URL(backendUrl).protocol === "https:",
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
  if (isAppLoginUrl(url)) {
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
  if (isAppUrl(url)) return;
  event.preventDefault();
  const target = new URL(url);
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
    const scheme = new URL(url).protocol;
    if (isAppLoginUrl(url)) {
      void startExternalLogin();
    } else if (["http:", "https:", "mailto:"].includes(scheme)) {
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
        ),
      );
    },
  );
  session.defaultSession.setPermissionCheckHandler(
    (_webContents, permission, requestingOrigin, details) =>
      isTrustedPermissionRequest(permission, requestingOrigin, details),
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

    localThreadStore = new LocalThreadStore(
      path.join(app.getPath("userData"), "desktop-local-threads.json"),
    );
    openAiOAuth = new OpenAiOAuthManager({
      storagePath: path.join(app.getPath("userData"), "openai-auth.bin"),
      encryptString: (value) => {
        if (!safeStorage.isEncryptionAvailable()) {
          throw new Error("Secure credential storage is unavailable");
        }
        return safeStorage.encryptString(value);
      },
      decryptString: (value) => safeStorage.decryptString(value),
    });
    await openAiOAuth.startBroker().catch((error) => {
      console.warn("Could not start the local OpenAI credential broker", error);
    });
    backendSupervisor = new BackendSupervisor({
      isPackaged: app.isPackaged,
      repoRoot: path.resolve(__dirname, "../.."),
      resourcesPath: process.resourcesPath,
      stateDir: path.join(app.getPath("userData"), "local-backend"),
      projectsFile: projectsPath(),
      worktreesDir: worktreesPath(),
      providerEnv: () => openAiOAuth?.backendEnv() || {},
      openAiOAuthAvailable: () =>
        openAiOAuth?.status().signedIn === true &&
        Boolean(openAiOAuth?.backendEnv().OPEN_SWE_OPENAI_OAUTH_BROKER_URL),
    });
    protocol.handle("open-swe", serveBundledUi);
    configurePermissions();
    configureDesktopIpc();
    createMenu();
    createWindow();
    configureAutoUpdater();
    configureTerminalIpc({
      ipcMain,
      requireTrusted: requireTrustedDesktopIpc,
      getWindow: () => mainWindow,
      getSessionRoot: (id) => threadRoot(localThreadStore.get(id)),
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
      backendSupervisor?.close(),
      openAiOAuth?.close(),
    ]).finally(() => {
      app.quit();
    });
  });
}
