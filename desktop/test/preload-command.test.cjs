const test = require("node:test");
const assert = require("node:assert/strict");
const Module = require("node:module");

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
