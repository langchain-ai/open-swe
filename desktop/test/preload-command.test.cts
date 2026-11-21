const test = require("node:test");
const assert = require("node:assert/strict");
const Module = require("node:module");

test("preload commands validate IDs; local approval APIs only request presentation", () => {
  let exposed;
  const listeners = new Map();
  const invocations: unknown[][] = [];
  const electron = {
    contextBridge: {
      exposeInMainWorld: (_name, bridge) => {
        exposed = bridge;
      },
    },
    ipcRenderer: {
      invoke: (...args: unknown[]) => invocations.push(args),
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

    const bounds = { x: 240, y: 0, width: 760, height: 700 };
    exposed.setLocalAccessThread("thread", bounds);
    exposed.setLocalAccessThread("thread", null);
    exposed.pendingLocalAccessThreads();
    exposed.localAccessRequests("thread");
    exposed.reviewLocalAccessRequest("request");
    assert.deepEqual(invocations, [
      ["desktop:set-local-access-thread", "thread", bounds],
      ["desktop:set-local-access-thread", "thread", null],
      ["desktop:pending-local-access-threads"],
      ["desktop:local-access-requests", "thread"],
      ["desktop:review-local-access-request", "request"],
    ]);
  } finally {
    Module._load = originalLoad;
    global.window = originalWindow;
  }
});
