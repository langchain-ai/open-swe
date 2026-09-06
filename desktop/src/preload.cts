const { contextBridge, ipcRenderer } = require("electron");
const desktopCommandIds = new Set([
  "new-thread",
  "show-command-palette",
  "open-settings",
  "show-keyboard-shortcuts",
  "toggle-sidebar",
]);

function isDesktopCommandId(value) {
  return typeof value === "string" && desktopCommandIds.has(value);
}

contextBridge.exposeInMainWorld("openSweDesktop", {
  isDesktop: true,
  onCommand: (callback) => {
    const listener = (_event, commandId) => {
      if (isDesktopCommandId(commandId)) callback(commandId);
    };
    ipcRenderer.on("desktop:command", listener);
    return () => ipcRenderer.removeListener("desktop:command", listener);
  },
  listProjects: () => ipcRenderer.invoke("desktop:projects"),
  getProjectBranches: (cwd) =>
    ipcRenderer.invoke("desktop:project-branches", cwd),
  watchProjectHead: (cwd) =>
    ipcRenderer.invoke("desktop:watch-project-head", cwd),
  onProjectHeadChanged: (callback) => {
    const listener = (_event, cwd) => callback(cwd);
    ipcRenderer.on("desktop:project-head-changed", listener);
    return () =>
      ipcRenderer.removeListener("desktop:project-head-changed", listener);
  },
  setLocalBranch: (input) =>
    ipcRenderer.invoke("desktop:set-local-branch", { ...input }),
  checkoutProjectBranch: (input) =>
    ipcRenderer.invoke("desktop:checkout-project-branch", { ...input }),
  addProject: () => ipcRenderer.invoke("desktop:add-project"),
  removeProject: (cwd) => ipcRenderer.invoke("desktop:remove-project", cwd),
  getVersion: () => ipcRenderer.invoke("desktop:version"),
  getUpdateState: () => ipcRenderer.invoke("desktop:update-state"),
  installUpdate: () => ipcRenderer.invoke("desktop:install-update"),
  onUpdateState: (callback) => {
    const listener = (_event, state) => callback(state);
    ipcRenderer.on("desktop:update-state", listener);
    return () => ipcRenderer.removeListener("desktop:update-state", listener);
  },
  connectService: (provider) =>
    ipcRenderer.invoke("desktop:connect-service", provider),
  openExternal: (url) => ipcRenderer.invoke("desktop:open-external", url),
  resolveLocalProjectPath: (input) =>
    ipcRenderer.invoke("desktop:resolve-local-project-path", { ...input }),
  localModelCredentialStatus: (modelId) =>
    ipcRenderer.invoke("desktop:local-model-credential-status", modelId),
  signInLocalOpenAI: () => ipcRenderer.invoke("desktop:local-openai-sign-in"),
  startLocalThread: (input) =>
    ipcRenderer.invoke("desktop:start-local-thread", input),
  getLocalPrompt: (threadId) =>
    ipcRenderer.invoke("desktop:get-local-prompt", threadId),
  clearLocalPrompt: (threadId) =>
    ipcRenderer.invoke("desktop:clear-local-prompt", threadId),
  getLocalThread: (threadId) =>
    ipcRenderer.invoke("desktop:get-local-thread", threadId),
  setAppearance: (appearance) =>
    ipcRenderer.invoke("desktop:set-appearance", appearance),
  listLocalThreads: () => ipcRenderer.invoke("desktop:list-local-threads"),
  localActivity: () => ipcRenderer.invoke("desktop:local-activity"),
  updateLocalThread: (input) =>
    ipcRenderer.invoke("desktop:update-local-thread", input),
  deleteLocalThread: (threadId) =>
    ipcRenderer.invoke("desktop:delete-local-thread", threadId),
  getLocalDiff: (threadId) =>
    ipcRenderer.invoke("desktop:get-local-diff", threadId),
  getLocalPrDiff: (threadId) =>
    ipcRenderer.invoke("desktop:get-local-pr-diff", threadId),
  getProjectDiff: (cwd) => ipcRenderer.invoke("desktop:get-project-diff", cwd),
  onProjectsChanged: (callback) => {
    const listener = (_event, projects) => callback(projects);
    ipcRenderer.on("desktop:projects-changed", listener);
    return () =>
      ipcRenderer.removeListener("desktop:projects-changed", listener);
  },
  terminal: {
    attach: (input) =>
      ipcRenderer.invoke("desktop:terminal-attach", { ...input }),
    open: (input) =>
      ipcRenderer.invoke("desktop:terminal-attach", { ...input }),
    write: (input) =>
      ipcRenderer.invoke("desktop:terminal-write", { ...input }),
    resize: (input) =>
      ipcRenderer.invoke("desktop:terminal-resize", { ...input }),
    clear: (input) =>
      ipcRenderer.invoke("desktop:terminal-clear", { ...input }),
    restart: (input) =>
      ipcRenderer.invoke("desktop:terminal-restart", { ...input }),
    detach: (input) =>
      ipcRenderer.invoke("desktop:terminal-detach", { ...input }),
    close: (input) =>
      ipcRenderer.invoke("desktop:terminal-close", { ...input }),
    list: (localSessionId) =>
      ipcRenderer.invoke("desktop:terminal-list", localSessionId),
    subscribeMetadata: (localSessionId) =>
      ipcRenderer.invoke("desktop:terminal-metadata-subscribe", localSessionId),
    detachMetadata: (localSessionId) =>
      ipcRenderer.invoke("desktop:terminal-metadata-detach", localSessionId),
    onEvent: (callback) => {
      const listener = (_event, terminalEvent) => callback(terminalEvent);
      ipcRenderer.on("desktop:terminal-event", listener);
      return () =>
        ipcRenderer.removeListener("desktop:terminal-event", listener);
    },
    onMetadata: (callback) => {
      const listener = (_event, metadataEvent) => callback(metadataEvent);
      ipcRenderer.on("desktop:terminal-metadata", listener);
      return () =>
        ipcRenderer.removeListener("desktop:terminal-metadata", listener);
    },
  },
});

const DRAG_REGION_ID = "open-swe-desktop-drag-region";
const DRAG_REGION_HEIGHT = 44;

ipcRenderer.on("desktop:fullscreen-change", (_event, fullscreen) => {
  document.documentElement.classList.toggle("desktop-fullscreen", fullscreen);
});

window.addEventListener("DOMContentLoaded", () => {
  if (process.platform !== "darwin") return;

  const style = document.createElement("style");
  style.textContent = `
    #${DRAG_REGION_ID} {
      -webkit-app-region: drag;
      pointer-events: none;
      position: fixed;
      top: 0;
      left: 118px;
      right: 0;
      height: ${DRAG_REGION_HEIGHT}px;
      z-index: 2147483647;
      user-select: none;
    }

    .desktop-fullscreen #${DRAG_REGION_ID} {
      left: 0;
    }

    [data-sidebar-frame] > div:first-child,
    [data-desktop-drag-region] {
      -webkit-app-region: drag;
    }

    a, button, input, textarea, select, summary, [contenteditable="true"],
    [role="button"], [role="checkbox"], [role="combobox"], [role="link"],
    [role="menuitem"], [role="separator"], [role="switch"], [role="tab"],
    [role="textbox"], [data-no-drag] {
      -webkit-app-region: no-drag;
    }

    [data-sidebar-expand] {
      -webkit-app-region: no-drag;
      left: 90px !important;
    }

    .desktop-fullscreen :is([data-sidebar-collapse], [data-sidebar-expand]) {
      left: 12px !important;
    }
  `;
  document.head.append(style);

  const dragRegion = document.createElement("div");
  dragRegion.id = DRAG_REGION_ID;
  dragRegion.setAttribute("aria-hidden", "true");
  document.body.prepend(dragRegion);
});
