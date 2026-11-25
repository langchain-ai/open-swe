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
  setLocalAccessThread: (threadId, bounds) =>
    ipcRenderer.invoke("desktop:set-local-access-thread", threadId, bounds),
  pendingLocalAccessThreads: () =>
    ipcRenderer.invoke("desktop:pending-local-access-threads"),
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

ipcRenderer.on("desktop:tabbed", (_event, tabbed) => {
  document.documentElement.classList.toggle("desktop-tabbed", tabbed === true);
});

const THEME_TOKENS = [
  "--bg-surface-level-1",
  "--bg-surface-level-2",
  "--bg-surface-level-2-hover",
  "--text-primary",
  "--text-secondary",
  "--border-default",
  "--radius-md",
  "--font-sans",
];
// A CSS color as #rrggbb, which the shell can paint; Robin's tokens may be oklch().
function hexColor(color) {
  const context = document.createElement("canvas").getContext("2d");
  if (!context || !color) return "";
  context.fillStyle = color;
  context.fillRect(0, 0, 1, 1);
  const [red, green, blue, alpha] = context.getImageData(0, 0, 1, 1).data;
  if (!alpha) return "";
  return `#${[red, green, blue].map((value) => value.toString(16).padStart(2, "0")).join("")}`;
}
let reportedTheme = "";
function reportTheme() {
  const style = getComputedStyle(document.documentElement);
  const tokens = Object.fromEntries(
    THEME_TOKENS.map((name) => [name, style.getPropertyValue(name).trim()]),
  );
  tokens["--page-background"] = document.body
    ? hexColor(getComputedStyle(document.body).backgroundColor)
    : "";
  tokens["--strip-background"] = hexColor(tokens["--bg-surface-level-2"]);
  const serialized = JSON.stringify(tokens);
  if (serialized === reportedTheme) return;
  reportedTheme = serialized;
  ipcRenderer.send("desktop:tab-theme", tokens);
}

// The shell brings a new tab forward only once Robin has drawn real content.
new PerformanceObserver((entries, observer) => {
  if (
    entries
      .getEntries()
      .some((entry) => entry.name === "first-contentful-paint")
  ) {
    observer.disconnect();
    ipcRenderer.send("desktop:tab-painted");
  }
}).observe({ type: "paint", buffered: true });

window.addEventListener("DOMContentLoaded", () => {
  reportTheme();
  new MutationObserver(reportTheme).observe(document.documentElement, {
    attributes: true,
    attributeFilter: ["class", "style", "data-theme"],
  });
  matchMedia("(prefers-color-scheme: dark)").addEventListener(
    "change",
    reportTheme,
  );
  window.addEventListener("load", reportTheme);
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

    :is(.desktop-fullscreen, .desktop-tabbed) [data-desktop-drag-strip] {
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

    :is(.desktop-fullscreen, .desktop-tabbed)
      :is([data-sidebar-collapse], [data-sidebar-expand]) {
      left: 12px !important;
    }

    /* The window buttons sit in the tab strip, so the page's title-bar
       controls take their place at the left edge. */
    .desktop-tabbed [data-desktop-window-controls] > * {
      left: 12px !important;
    }
  `;
  document.head.append(style);
});
