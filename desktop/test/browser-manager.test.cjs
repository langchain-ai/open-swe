const assert = require("node:assert/strict");
const test = require("node:test");
const {
  computeNavStatus,
  createBrowserManager,
  hardenWebviewAttach,
  isWebUrl,
  stepZoom,
} = require("../build/browser-manager.cjs");

class FakeWebContents {
  constructor(id, host) {
    this.id = id;
    this.hostWebContents = host;
    this.listeners = new Map();
    this.url = "";
    this.title = "";
    this.loading = false;
    this.zoom = 1;
    this.loaded = [];
    this.reloads = 0;
    this.history = { back: false, forward: false };
    this.destroyed = false;
    this.type = "webview";
    this.debugger = {
      attached: false,
      commands: [],
      isAttached: () => this.debugger.attached,
      attach: () => {
        this.debugger.attached = true;
      },
      detach: () => {
        this.debugger.attached = false;
      },
      sendCommand: async (method, params) => {
        this.debugger.commands.push({ method, params });
      },
    };
    this.navigationHistory = {
      canGoBack: () => this.history.back,
      canGoForward: () => this.history.forward,
      goBack: () => (this.wentBack = true),
      goForward: () => (this.wentForward = true),
    };
  }
  isDestroyed() {
    return this.destroyed;
  }
  getType() {
    return this.type;
  }
  getURL() {
    return this.url;
  }
  getTitle() {
    return this.title;
  }
  isLoading() {
    return this.loading;
  }
  setZoomFactor(value) {
    this.zoom = value;
  }
  async loadURL(url) {
    this.loaded.push(url);
    this.url = url;
  }
  reload() {
    this.reloads += 1;
  }
  reloadIgnoringCache() {
    this.hardReloads = (this.hardReloads ?? 0) + 1;
  }
  setWindowOpenHandler(handler) {
    this.windowOpenHandler = handler;
  }
  on(event, listener) {
    const set = this.listeners.get(event) ?? new Set();
    set.add(listener);
    this.listeners.set(event, set);
  }
  once(event, listener) {
    this.on(event, listener);
  }
  off(event, listener) {
    this.listeners.get(event)?.delete(listener);
  }
  emit(event, ...args) {
    for (const listener of this.listeners.get(event) ?? [])
      listener({}, ...args);
  }
  isDevToolsOpened() {
    return false;
  }
  openDevTools() {
    this.devToolsOpened = true;
  }
}

function fixture() {
  const host = { id: 1 };
  const mainWindow = { webContents: host, isDestroyed: () => false };
  const guests = new Map();
  const manager = createBrowserManager({
    webContents: { fromId: (id) => guests.get(id) ?? null },
    getMainWindow: () => mainWindow,
    fetchFavicon: async () => "data:image/png;base64,AAAA",
    now: () => "2026-01-01T00:00:00.000Z",
  });
  const states = [];
  manager.onStateChange((state) => states.push(state));
  const addGuest = (id, hostContents = host) => {
    const guest = new FakeWebContents(id, hostContents);
    guests.set(id, guest);
    return guest;
  };
  return { manager, states, addGuest, host };
}

test("computeNavStatus derives idle, loading, success, and keeps a failure", () => {
  const wc = new FakeWebContents(7, null);
  assert.deepEqual(computeNavStatus(wc, null), { kind: "idle" });
  wc.url = "http://localhost:3000/";
  wc.loading = true;
  assert.deepEqual(computeNavStatus(wc, null), {
    kind: "loading",
    url: "http://localhost:3000/",
    title: "",
  });
  wc.loading = false;
  wc.title = "Dev";
  assert.equal(computeNavStatus(wc, null).kind, "success");
  const failed = {
    kind: "failed",
    url: "http://localhost:3000/",
    title: "",
    code: -102,
    description: "ERR_CONNECTION_REFUSED",
  };
  assert.equal(computeNavStatus(wc, failed), failed);
});

test("stepZoom walks Chrome's ladder and clamps", () => {
  assert.equal(stepZoom(1, 1), 1.1);
  assert.equal(stepZoom(1, -1), 0.9);
  assert.equal(stepZoom(5, 1), 5);
  assert.equal(stepZoom(0.25, -1), 0.25);
  assert.equal(stepZoom(1.05, 1), 1.1);
});

test("isWebUrl only admits http(s)", () => {
  assert.equal(isWebUrl("http://localhost:5173/"), true);
  assert.equal(isWebUrl("https://example.com"), true);
  assert.equal(isWebUrl("javascript:alert(1)"), false);
  assert.equal(isWebUrl("file:///etc/passwd"), false);
  assert.equal(isWebUrl(42), false);
});

test("hardenWebviewAttach rejects foreign partitions and forces the sandbox flags", () => {
  let prevented = false;
  const event = { preventDefault: () => (prevented = true) };
  assert.equal(
    hardenWebviewAttach(event, {}, { partition: "persist:other" }),
    false,
  );
  assert.equal(prevented, true);
  const prefs = {
    sandbox: false,
    nodeIntegration: true,
    contextIsolation: false,
    preload: "/evil.js",
  };
  const params = {
    partition: "persist:open-swe-browser",
    preload: "file:///evil.js",
  };
  assert.equal(
    hardenWebviewAttach({ preventDefault() {} }, prefs, params),
    true,
  );
  assert.deepEqual(prefs, {
    sandbox: true,
    nodeIntegration: false,
    nodeIntegrationInSubFrames: false,
    contextIsolation: true,
  });
  assert.equal("preload" in params, false);
});

test("registerWebview only adopts webview guests of the main window", async () => {
  const { manager, addGuest } = fixture();
  manager.createTab("t1", { zoomFactor: 1.25, colorScheme: "dark" });
  const foreign = addGuest(2, { id: 99 });
  await assert.rejects(
    manager.registerWebview("t1", foreign.id),
    /not a browser tab/,
  );
  const notWebview = addGuest(3);
  notWebview.type = "window";
  await assert.rejects(
    manager.registerWebview("t1", notWebview.id),
    /not a browser tab/,
  );
  await assert.rejects(
    manager.registerWebview("missing", 3),
    /Unknown browser tab/,
  );
});

test("navigate validates urls, queues until a guest registers, then loads and syncs", async () => {
  const { manager, states, addGuest } = fixture();
  manager.createTab("t1", { zoomFactor: 1.25, colorScheme: "dark" });
  await assert.rejects(
    manager.navigate("t1", "javascript:alert(1)"),
    /Only http and https/,
  );
  await manager.navigate("t1", "http://localhost:3000/");
  assert.deepEqual(states.at(-1).nav, {
    kind: "loading",
    url: "http://localhost:3000/",
    title: "",
  });

  const guest = addGuest(5);
  await manager.registerWebview("t1", guest.id);
  assert.equal(guest.zoom, 1.25);
  assert.deepEqual(guest.loaded, ["http://localhost:3000/"]);
  assert.deepEqual(guest.debugger.commands.at(-1), {
    method: "Emulation.setEmulatedMedia",
    params: { features: [{ name: "prefers-color-scheme", value: "dark" }] },
  });

  guest.title = "Dev server";
  guest.history.back = true;
  guest.emit("did-navigate");
  assert.deepEqual(states.at(-1).nav, {
    kind: "success",
    url: "http://localhost:3000/",
    title: "Dev server",
  });
  assert.equal(states.at(-1).canGoBack, true);
  assert.equal(states.at(-1).webContentsId, 5);

  guest.emit(
    "did-fail-load",
    -102,
    "ERR_CONNECTION_REFUSED",
    "http://localhost:3000/",
    true,
  );
  assert.equal(states.at(-1).nav.kind, "failed");
  // Electron follows a failed load with did-stop-loading; the failure stays.
  guest.emit("did-stop-loading");
  assert.equal(states.at(-1).nav.kind, "failed");

  // Popups are denied and opened in the same tab instead.
  const decision = guest.windowOpenHandler({
    url: "http://localhost:3000/next",
  });
  assert.deepEqual(decision, { action: "deny" });
  assert.equal(guest.loaded.at(-1), "http://localhost:3000/next");
});

test("favicons are kept per origin and cleared on cross-origin navigation", async () => {
  const { manager, states, addGuest } = fixture();
  manager.createTab("t1");
  const guest = addGuest(6);
  guest.url = "http://localhost:3000/";
  await manager.registerWebview("t1", guest.id);
  guest.emit("page-favicon-updated", ["http://localhost:3000/favicon.ico"]);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(states.at(-1).favicon, "data:image/png;base64,AAAA");
  guest.url = "https://example.com/";
  guest.emit("did-navigate");
  assert.equal(states.at(-1).favicon, null);
});

test("zoom steps apply to the guest and closeTab detaches listeners", async () => {
  const { manager, states, addGuest } = fixture();
  manager.createTab("t1");
  const guest = addGuest(8);
  await manager.registerWebview("t1", guest.id);
  manager.zoomIn("t1");
  assert.equal(guest.zoom, 1.1);
  assert.equal(states.at(-1).zoomFactor, 1.1);
  manager.resetZoom("t1");
  assert.equal(guest.zoom, 1);
  manager.closeTab("t1");
  assert.equal(guest.listeners.get("did-navigate").size, 0);
  assert.equal(manager.getTab("t1"), null);
});
