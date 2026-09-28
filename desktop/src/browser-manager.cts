/**
 * Main-process side of the in-app browser for local sessions.
 *
 * The renderer owns the `<webview>` elements; this module adopts their guest
 * webContents once registered, drives navigation and zoom, and pushes a
 * serialisable snapshot of each tab back over IPC whenever it changes.
 */
const {
  nativeImage,
  session: electronSession,
  webContents: electronWebContents,
} = require("electron");

const BROWSER_PARTITION = "persist:open-swe-browser";
/**
 * Whitespace-free, JS-boolean values: Electron's parser splits on commas and
 * treats `no` as a truthy string. `will-attach-webview` re-asserts the
 * security-critical flags regardless, so this string cannot loosen them.
 */
const WEBVIEW_PREFERENCES =
  "contextIsolation=true,sandbox=true,nodeIntegration=false";
const ZOOM_LEVELS = [
  0.25, 0.33, 0.5, 0.67, 0.75, 0.8, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2, 2.5, 3, 4,
  5,
];
const ZOOM_EPSILON = 0.001;
const COLOR_SCHEMES = new Set(["system", "light", "dark"]);
// `clipboard-sanitized-write` backs navigator.clipboard.writeText(); the
// permission *check* handler must allow it too or dev-server "copy" buttons fail.
const ALLOWED_GUEST_PERMISSIONS = new Set([
  "clipboard-read",
  "clipboard-sanitized-write",
  "notifications",
]);
const MAX_TAB_ID_LENGTH = 128;
const MAX_URL_LENGTH = 2048;
const FAVICON_MAX_BYTES = 64 * 1024;
const FAVICON_MAX_CANDIDATES = 4;
const FAVICON_TIMEOUT_MS = 5_000;
const FAVICON_SIZE = 32;
const ABORTED_LOAD_ERROR_CODE = -3;

function isBrowserPartition(partition) {
  return partition === BROWSER_PARTITION;
}

function isWebUrl(url) {
  if (
    typeof url !== "string" ||
    url.length === 0 ||
    url.length > MAX_URL_LENGTH
  )
    return false;
  try {
    const protocol = new URL(url).protocol;
    return protocol === "http:" || protocol === "https:";
  } catch {
    return false;
  }
}

function safeOrigin(url) {
  try {
    const parsed = new URL(url);
    return parsed.protocol === "http:" || parsed.protocol === "https:"
      ? parsed.origin
      : null;
  } catch {
    return null;
  }
}

function stepZoom(current, direction) {
  const index = ZOOM_LEVELS.findIndex(
    (level) => Math.abs(level - current) < ZOOM_EPSILON,
  );
  if (index === -1) {
    if (direction > 0) return ZOOM_LEVELS.find((level) => level > current) ?? 5;
    return [...ZOOM_LEVELS].reverse().find((level) => level < current) ?? 0.25;
  }
  return ZOOM_LEVELS[
    Math.min(ZOOM_LEVELS.length - 1, Math.max(0, index + direction))
  ];
}

function normalizeZoom(value) {
  return typeof value === "number" &&
    Number.isFinite(value) &&
    value >= 0.25 &&
    value <= 5
    ? value
    : 1;
}

/**
 * Derives the tab's navigation state from the guest. Electron emits
 * did-stop-loading after did-fail-load; the failure is kept until a new load
 * actually starts so the error page does not flash to "success".
 */
function computeNavStatus(wc, previous) {
  const url = wc.getURL();
  if (!url || url === "about:blank") return { kind: "idle" };
  const title = wc.getTitle();
  if (wc.isLoading()) return { kind: "loading", url, title };
  if (previous && previous.kind === "failed" && previous.url === url)
    return previous;
  return { kind: "success", url, title };
}

/**
 * Only our partition may attach, and the guest can never gain Node or a
 * preload whatever the renderer asked for.
 */
function hardenWebviewAttach(event, webPreferences, params) {
  if (
    typeof params?.partition !== "string" ||
    !isBrowserPartition(params.partition)
  ) {
    event.preventDefault();
    return false;
  }
  webPreferences.sandbox = true;
  webPreferences.nodeIntegration = false;
  webPreferences.nodeIntegrationInSubFrames = false;
  webPreferences.contextIsolation = true;
  delete webPreferences.preload;
  delete params.preload;
  return true;
}

function configureBrowserSession(partition = BROWSER_PARTITION) {
  const browserSession = electronSession.fromPartition(partition);
  browserSession.setPermissionRequestHandler((_wc, permission, callback) => {
    callback(ALLOWED_GUEST_PERMISSIONS.has(permission));
  });
  browserSession.setPermissionCheckHandler((_wc, permission) =>
    ALLOWED_GUEST_PERMISSIONS.has(permission),
  );
  return browserSession;
}

async function captureFavicon(wc, candidates) {
  for (const candidate of candidates.slice(0, FAVICON_MAX_CANDIDATES)) {
    if (typeof candidate !== "string") continue;
    if (candidate.startsWith("data:image/") && candidate.length <= 8192)
      return candidate;
    if (!isWebUrl(candidate)) continue;
    try {
      const response = await wc.session.fetch(candidate, {
        signal: AbortSignal.timeout(FAVICON_TIMEOUT_MS),
      });
      if (!response.ok) continue;
      const type = response.headers.get("content-type") || "";
      if (!type.startsWith("image/")) continue;
      const buffer = Buffer.from(await response.arrayBuffer());
      if (buffer.byteLength === 0 || buffer.byteLength > FAVICON_MAX_BYTES)
        continue;
      const image = nativeImage.createFromBuffer(buffer);
      if (image.isEmpty()) continue;
      return image
        .resize({ width: FAVICON_SIZE, height: FAVICON_SIZE })
        .toDataURL();
    } catch {
      // Try the next candidate; a missing favicon is not an error worth surfacing.
    }
  }
  return null;
}

function createBrowserManager({
  webContents = electronWebContents,
  getMainWindow,
  fetchFavicon = captureFavicon,
  now = () => new Date().toISOString(),
}) {
  const tabs = new Map<string, any>();
  const listeners = new Set<(state: unknown) => void>();

  function serialize(tab) {
    return {
      tabId: tab.tabId,
      webContentsId: tab.webContentsId,
      nav: tab.nav,
      canGoBack: tab.canGoBack,
      canGoForward: tab.canGoForward,
      zoomFactor: tab.zoomFactor,
      colorScheme: tab.colorScheme,
      favicon: tab.favicon,
      updatedAt: now(),
    };
  }

  function emit(tab) {
    const snapshot = serialize(tab);
    for (const listener of listeners) listener(snapshot);
  }

  function requireTab(tabId) {
    const tab = tabs.get(tabId);
    if (!tab) throw new Error(`Unknown browser tab: ${tabId}`);
    return tab;
  }

  function guestOf(tab) {
    if (tab.webContentsId === null) return null;
    const wc = webContents.fromId(tab.webContentsId);
    return wc && !wc.isDestroyed() ? wc : null;
  }

  function requireGuest(tabId) {
    const wc = guestOf(requireTab(tabId));
    if (!wc) throw new Error("The browser tab has no page yet.");
    return wc;
  }

  function sync(tab, wc) {
    if (wc.isDestroyed() || tab.webContentsId !== wc.id) return;
    const nav = computeNavStatus(wc, tab.nav);
    const navOrigin = nav.kind === "idle" ? null : safeOrigin(nav.url);
    if (tab.faviconOrigin !== null && tab.faviconOrigin !== navOrigin) {
      tab.favicon = null;
      tab.faviconOrigin = null;
    }
    tab.nav = nav;
    tab.canGoBack = wc.navigationHistory.canGoBack();
    tab.canGoForward = wc.navigationHistory.canGoForward();
    emit(tab);
  }

  async function applyColorScheme(tab, wc) {
    if (wc.isDestroyed()) return;
    const dbg = wc.debugger;
    try {
      if (tab.colorScheme === "system" && !dbg.isAttached()) return;
      if (!dbg.isAttached()) dbg.attach("1.3");
      await dbg.sendCommand("Emulation.setEmulatedMedia", {
        features: [
          {
            name: "prefers-color-scheme",
            value: tab.colorScheme === "system" ? "" : tab.colorScheme,
          },
        ],
      });
    } catch (error) {
      // DevTools or another debugger owns the guest; the choice is kept on
      // the tab and re-applied once the session is free again.
      console.warn(
        "Could not apply the browser color scheme",
        error?.message ?? error,
      );
    }
  }

  function detach(tab) {
    if (tab.detachListeners) {
      tab.detachListeners();
      tab.detachListeners = null;
    }
    tab.webContentsId = null;
  }

  function attach(tab, wc) {
    const onSync = () => sync(tab, wc);
    const onFailed = (
      _event,
      errorCode,
      errorDescription,
      validatedURL,
      isMainFrame,
    ) => {
      if (!isMainFrame || errorCode === ABORTED_LOAD_ERROR_CODE) return;
      if (tab.webContentsId !== wc.id) return;
      tab.nav = {
        kind: "failed",
        url: validatedURL || (tab.nav.kind === "idle" ? "" : tab.nav.url),
        title: tab.nav.kind === "idle" ? "" : tab.nav.title,
        code: errorCode,
        description: errorDescription,
      };
      emit(tab);
    };
    const onFavicon = (_event, favicons) => {
      if (tab.webContentsId !== wc.id) return;
      const pageOrigin = safeOrigin(wc.getURL());
      if (!pageOrigin || !Array.isArray(favicons)) return;
      const generation = ++tab.faviconGeneration;
      void fetchFavicon(wc, favicons).then((dataUrl) => {
        if (
          !dataUrl ||
          tab.faviconGeneration !== generation ||
          tab.webContentsId !== wc.id ||
          wc.isDestroyed() ||
          safeOrigin(wc.getURL()) !== pageOrigin
        )
          return;
        tab.favicon = dataUrl;
        tab.faviconOrigin = pageOrigin;
        emit(tab);
      });
    };
    const onDestroyed = () => {
      if (tab.webContentsId !== wc.id) return;
      tab.detachListeners = null;
      tab.webContentsId = null;
      emit(tab);
    };
    const onDevToolsClosed = () => {
      if (tab.webContentsId === wc.id) void applyColorScheme(tab, wc);
    };
    wc.on("did-start-loading", onSync);
    wc.on("did-stop-loading", onSync);
    wc.on("did-navigate", onSync);
    wc.on("did-navigate-in-page", onSync);
    wc.on("page-title-updated", onSync);
    wc.on("did-fail-load", onFailed);
    wc.on("page-favicon-updated", onFavicon);
    wc.on("devtools-closed", onDevToolsClosed);
    wc.once("destroyed", onDestroyed);
    // Links that ask for a new window open in the same tab; nothing the
    // page does can spawn native windows.
    wc.setWindowOpenHandler((details) => {
      if (isWebUrl(details.url) && !wc.isDestroyed()) {
        void wc.loadURL(details.url).catch(() => undefined);
      }
      return { action: "deny" };
    });
    tab.detachListeners = () => {
      if (wc.isDestroyed()) return;
      wc.off("did-start-loading", onSync);
      wc.off("did-stop-loading", onSync);
      wc.off("did-navigate", onSync);
      wc.off("did-navigate-in-page", onSync);
      wc.off("page-title-updated", onSync);
      wc.off("did-fail-load", onFailed);
      wc.off("page-favicon-updated", onFavicon);
      wc.off("devtools-closed", onDevToolsClosed);
      wc.off("destroyed", onDestroyed);
      if (wc.debugger.isAttached()) {
        try {
          wc.debugger.detach();
        } catch {}
      }
    };
  }

  return {
    createTab(tabId, defaults) {
      const existing = tabs.get(tabId);
      if (existing) {
        emit(existing);
        return;
      }
      const tab = {
        tabId,
        webContentsId: null,
        nav: { kind: "idle" },
        canGoBack: false,
        canGoForward: false,
        zoomFactor: normalizeZoom(defaults?.zoomFactor),
        colorScheme: COLOR_SCHEMES.has(defaults?.colorScheme)
          ? defaults.colorScheme
          : "system",
        favicon: null,
        faviconOrigin: null,
        faviconGeneration: 0,
        pendingUrl: null,
        detachListeners: null,
      };
      tabs.set(tabId, tab);
      emit(tab);
    },
    closeTab(tabId) {
      const tab = tabs.get(tabId);
      if (!tab) return;
      detach(tab);
      tabs.delete(tabId);
    },
    async registerWebview(tabId, webContentsId) {
      const tab = requireTab(tabId);
      const wc = webContents.fromId(webContentsId);
      const mainWindow = getMainWindow();
      if (
        !wc ||
        wc.isDestroyed() ||
        wc.getType() !== "webview" ||
        !mainWindow ||
        mainWindow.isDestroyed() ||
        wc.hostWebContents !== mainWindow.webContents
      ) {
        throw new Error("The page is not a browser tab of this window.");
      }
      if (tab.webContentsId === wc.id && tab.detachListeners) {
        // The same guest re-announced itself (dom-ready after did-attach).
        // Chromium may have just handed it the app window's zoom; push ours back.
        wc.setZoomFactor(tab.zoomFactor);
        sync(tab, wc);
        return;
      }
      detach(tab);
      tab.webContentsId = wc.id;
      // Asserted before the first paint: a guest attaching while the app UI is
      // zoomed inherits the embedder's zoom, which is not the tab's.
      wc.setZoomFactor(tab.zoomFactor);
      attach(tab, wc);
      await applyColorScheme(tab, wc);
      sync(tab, wc);
      if (tab.pendingUrl) {
        const url = tab.pendingUrl;
        tab.pendingUrl = null;
        await wc.loadURL(url);
      }
    },
    async navigate(tabId, url) {
      const tab = requireTab(tabId);
      if (!isWebUrl(url)) {
        throw new Error("Only http and https pages can open in the browser.");
      }
      tab.nav = {
        kind: "loading",
        url,
        title: tab.nav.kind === "idle" ? "" : tab.nav.title,
      };
      emit(tab);
      const wc = guestOf(tab);
      if (!wc) {
        tab.pendingUrl = url;
        return;
      }
      if (wc.getURL() === url) {
        wc.reload();
        return;
      }
      await wc.loadURL(url);
    },
    goBack(tabId) {
      const wc = requireGuest(tabId);
      if (wc.navigationHistory.canGoBack()) wc.navigationHistory.goBack();
    },
    goForward(tabId) {
      const wc = requireGuest(tabId);
      if (wc.navigationHistory.canGoForward()) wc.navigationHistory.goForward();
    },
    reload(tabId) {
      requireGuest(tabId).reload();
    },
    hardReload(tabId) {
      requireGuest(tabId).reloadIgnoringCache();
    },
    zoomIn(tabId) {
      this.setZoom(tabId, stepZoom(requireTab(tabId).zoomFactor, 1));
    },
    zoomOut(tabId) {
      this.setZoom(tabId, stepZoom(requireTab(tabId).zoomFactor, -1));
    },
    resetZoom(tabId) {
      this.setZoom(tabId, 1);
    },
    setZoom(tabId, zoomFactor) {
      const tab = requireTab(tabId);
      const next = normalizeZoom(zoomFactor);
      if (Math.abs(next - tab.zoomFactor) < ZOOM_EPSILON) return;
      tab.zoomFactor = next;
      const wc = guestOf(tab);
      if (wc) wc.setZoomFactor(next);
      emit(tab);
    },
    async setColorScheme(tabId, colorScheme) {
      const tab = requireTab(tabId);
      if (!COLOR_SCHEMES.has(colorScheme))
        throw new Error("Unknown color scheme.");
      tab.colorScheme = colorScheme;
      emit(tab);
      const wc = guestOf(tab);
      if (wc) await applyColorScheme(tab, wc);
    },
    openDevTools(tabId) {
      const tab = requireTab(tabId);
      const wc = requireGuest(tabId);
      if (wc.isDevToolsOpened()) {
        wc.devToolsWebContents?.focus();
        return;
      }
      // DevTools needs the debugger session; the color scheme returns with it.
      if (wc.debugger.isAttached()) {
        try {
          wc.debugger.detach();
        } catch {}
      }
      wc.openDevTools({ mode: "detach" });
      void tab;
    },
    getConfig() {
      return {
        partition: BROWSER_PARTITION,
        webPreferences: WEBVIEW_PREFERENCES,
      };
    },
    getTab(tabId) {
      const tab = tabs.get(tabId);
      return tab ? serialize(tab) : null;
    },
    onStateChange(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    shutdown() {
      for (const tab of tabs.values()) detach(tab);
      tabs.clear();
      listeners.clear();
    },
  };
}

function validTabId(value) {
  return (
    typeof value === "string" &&
    value.length > 0 &&
    value.length <= MAX_TAB_ID_LENGTH
  );
}

function configureBrowserIpc({ ipcMain, requireTrusted, getWindow }) {
  configureBrowserSession(BROWSER_PARTITION);
  const manager = createBrowserManager({ getMainWindow: getWindow });

  function handle(channel, operation) {
    ipcMain.handle(channel, async (event, ...args) => {
      requireTrusted(event);
      return operation(...args);
    });
  }
  function tabOperation(channel, operation) {
    handle(channel, (tabId, ...rest) => {
      if (!validTabId(tabId)) throw new Error("Invalid browser tab id.");
      return operation(tabId, ...rest);
    });
  }

  handle("desktop:browser-config", () => manager.getConfig());
  tabOperation("desktop:browser-create-tab", (tabId, defaults) =>
    manager.createTab(
      tabId,
      defaults && typeof defaults === "object" ? defaults : undefined,
    ),
  );
  tabOperation("desktop:browser-close-tab", (tabId) => manager.closeTab(tabId));
  tabOperation("desktop:browser-register-webview", (tabId, webContentsId) => {
    if (!Number.isInteger(webContentsId) || webContentsId <= 0)
      throw new Error("Invalid webContents id.");
    return manager.registerWebview(tabId, webContentsId);
  });
  tabOperation("desktop:browser-navigate", (tabId, url) =>
    manager.navigate(tabId, url),
  );
  tabOperation("desktop:browser-go-back", (tabId) => manager.goBack(tabId));
  tabOperation("desktop:browser-go-forward", (tabId) =>
    manager.goForward(tabId),
  );
  tabOperation("desktop:browser-reload", (tabId) => manager.reload(tabId));
  tabOperation("desktop:browser-hard-reload", (tabId) =>
    manager.hardReload(tabId),
  );
  tabOperation("desktop:browser-zoom-in", (tabId) => manager.zoomIn(tabId));
  tabOperation("desktop:browser-zoom-out", (tabId) => manager.zoomOut(tabId));
  tabOperation("desktop:browser-reset-zoom", (tabId) =>
    manager.resetZoom(tabId),
  );
  tabOperation("desktop:browser-set-color-scheme", (tabId, colorScheme) =>
    manager.setColorScheme(tabId, colorScheme),
  );
  tabOperation("desktop:browser-open-devtools", (tabId) =>
    manager.openDevTools(tabId),
  );

  manager.onStateChange((state) => {
    const window = getWindow();
    if (window && !window.isDestroyed())
      window.webContents.send("desktop:browser-state", state);
  });
  return manager;
}

module.exports = {
  BROWSER_PARTITION,
  WEBVIEW_PREFERENCES,
  captureFavicon,
  computeNavStatus,
  configureBrowserIpc,
  configureBrowserSession,
  createBrowserManager,
  hardenWebviewAttach,
  isBrowserPartition,
  isWebUrl,
  stepZoom,
};
