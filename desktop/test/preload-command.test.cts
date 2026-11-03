const test = require("node:test");
const assert = require("node:assert/strict");
const Module = require("node:module");
const { readFileSync } = require("node:fs");
const { runInNewContext } = require("node:vm");

test("cached UI can still start LangSmith sign-in through the connection bridge", async () => {
  const source = readFileSync(require.resolve("../build/main.cjs"), "utf8");
  let connect;
  const logins = [];
  const context = {
    APP_URL: "open-swe://app/",
    ipcMain: {
      handle: (_channel, handler) => {
        connect = handler;
      },
    },
    requireTrustedDesktopIpc: (event) => assert.equal(event, "trusted"),
    startExternalLogin: async (url) => {
      logins.push(url);
    },
    startConnectFlow: async (provider) => provider,
  };
  runInNewContext(
    source.slice(
      source.indexOf('ipcMain.handle("desktop:connect-service"'),
      source.indexOf('ipcMain.handle("desktop:open-external"'),
    ),
    context,
  );
  await connect("trusted", "langsmith");
  assert.deepEqual(logins, [
    "open-swe://app/dashboard/api/auth/langsmith/login",
  ]);
  assert.equal(await connect("trusted", "github"), "github");
  assert.equal(await connect("trusted", "slack"), "slack");
  await assert.rejects(connect("untrusted", "langsmith"));
  assert.equal(logins.length, 1);
});

test("preload command subscriptions validate IDs and unsubscribe", () => {
  let exposed;
  const listeners = new Map();
  const electron = {
    contextBridge: {
      exposeInMainWorld: (_name, bridge) => {
        exposed = bridge;
      },
    },
    ipcRenderer: {
      invoke: () => undefined,
      on: (channel, listener) => listeners.set(channel, listener),
      removeListener: (channel, listener) => {
        if (listeners.get(channel) === listener) listeners.delete(channel);
      },
    },
  };
  const originalLoad = Module._load;
  const originalWindow = global.window;
  Module._load = function (request, parent, isMain) {
    if (request === "electron") return electron;
    return originalLoad.call(this, request, parent, isMain);
  };
  global.window = { addEventListener: () => undefined };

  try {
    const preloadPath = require.resolve("../build/preload.cjs");
    delete require.cache[preloadPath];
    require(preloadPath);

    const received = [];
    const unsubscribe = exposed.onCommand((commandId) =>
      received.push(commandId),
    );
    const listener = listeners.get("desktop:command");
    listener(undefined, "new-thread");
    listener(undefined, "open-url");

    assert.deepEqual(received, ["new-thread"]);
    unsubscribe();
    assert.equal(listeners.has("desktop:command"), false);
  } finally {
    Module._load = originalLoad;
    global.window = originalWindow;
  }
});
