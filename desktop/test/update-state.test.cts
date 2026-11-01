const test = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { runInNewContext } = require("node:vm");
const { EventEmitter } = require("node:events");

test("macOS waits for native staging and clears installation errors", () => {
  const source = readFileSync(require.resolve("../build/main.cjs"), "utf8");
  const autoUpdater = new EventEmitter();
  const nativeAutoUpdater = new EventEmitter();
  const states = [];
  const errors = [];
  const context = {
    autoUpdater,
    nativeAutoUpdater,
    process: { platform: "darwin" },
    app: Object.assign(new EventEmitter(), { isPackaged: true }),
    powerMonitor: new EventEmitter(),
    console: { warn() {} },
    dialog: { showErrorBox: (...args) => errors.push(args) },
    setUpdateState: (status, version) => states.push({ status, version }),
    updateState: { status: "downloading", version: "1.2.3" },
    checkForUpdatesInBackground() {},
    setInterval: () => ({ unref() {} }),
    clearTimeout() {},
    quitting: true,
  };
  runInNewContext(
    source.slice(
      source.indexOf("let updateInstallTimer"),
      source.indexOf("async function checkForDesktopUpdates"),
    ) + "\nconfigureAutoUpdater();",
    context,
  );
  autoUpdater.emit("update-downloaded", { version: "1.2.3" });
  assert.deepEqual(states, []);
  nativeAutoUpdater.emit("update-downloaded");
  assert.deepEqual(states.pop(), { status: "ready", version: "1.2.3" });
  context.updateState.status = "installing";
  autoUpdater.emit("error", new Error("staging failed"));
  assert.equal(states.pop().status, "idle");
  assert.equal(context.quitting, false);
  assert.match(errors[0][1], /staging failed/);
});
