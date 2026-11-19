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
  setMenuCommands: (commands) =>
    ipcRenderer.invoke("desktop:set-menu-commands", commands),
  onMenuCommand: (callback) => {
    const listener = (_event, commandId, viaShortcut) => {
      if (typeof commandId === "string")
        callback(commandId, viaShortcut === true);
    };
    ipcRenderer.on("desktop:menu-command", listener);
    return () => ipcRenderer.removeListener("desktop:menu-command", listener);
  },
  writeClipboard: (value) =>
    ipcRenderer.invoke("desktop:write-clipboard", value),
  copyPageLink: () => ipcRenderer.invoke("desktop:copy-page-link"),
  openInBrowser: (path) => ipcRenderer.invoke("desktop:open-in-browser", path),
  getVersion: () => ipcRenderer.invoke("desktop:version"),
  getUpdateState: () => ipcRenderer.invoke("desktop:update-state"),
  installUpdate: () => ipcRenderer.invoke("desktop:install-update"),
  onUpdateState: (callback) => {
    const listener = (_event, state) => callback(state);
    ipcRenderer.on("desktop:update-state", listener);
    return () => ipcRenderer.removeListener("desktop:update-state", listener);
  },
  connectService: (provider, options) =>
    ipcRenderer.invoke("desktop:connect-service", provider, options),
  cancelConnectService: (provider) =>
    ipcRenderer.invoke("desktop:cancel-connect-service", provider),
  openExternal: (url) => ipcRenderer.invoke("desktop:open-external", url),
  ensureLocalAccessBridge: () =>
    ipcRenderer.invoke("desktop:ensure-local-access-bridge"),
  localAccessGrants: (threadId) =>
    ipcRenderer.invoke("desktop:local-access-grants", threadId),
  revokeLocalAccessGrants: (threadId) =>
    ipcRenderer.invoke("desktop:revoke-local-access-grants", threadId),
  onLocalAccessGrantsChanged: (callback) => {
    const listener = () => callback();
    ipcRenderer.on("desktop:local-access-grants-changed", listener);
    return () =>
      ipcRenderer.removeListener(
        "desktop:local-access-grants-changed",
        listener,
      );
  },
  localAccessRequests: (threadId) =>
    ipcRenderer.invoke("desktop:local-access-requests", threadId),
  reviewLocalAccessRequest: (id) =>
    ipcRenderer.invoke("desktop:review-local-access-request", id),
  onLocalAccessRequestsChanged: (callback) => {
    const listener = () => callback();
    ipcRenderer.on("desktop:local-access-requests-changed", listener);
    return () =>
      ipcRenderer.removeListener(
        "desktop:local-access-requests-changed",
        listener,
      );
  },
  setAppearance: (appearance) =>
    ipcRenderer.invoke("desktop:set-appearance", appearance),
});

ipcRenderer.on("desktop:fullscreen-change", (_event, fullscreen) => {
  document.documentElement.classList.toggle("desktop-fullscreen", fullscreen);
});

window.addEventListener("DOMContentLoaded", () => {
  if (process.platform !== "darwin") return;

  const style = document.createElement("style");
  style.textContent = `
    [data-desktop-drag-strip] {
      -webkit-app-region: drag;
      pointer-events: none;
      position: fixed;
      top: 0;
      left: 118px;
      right: 0;
      height: 44px;
      user-select: none;
    }

    .desktop-fullscreen [data-desktop-drag-strip] {
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
});
