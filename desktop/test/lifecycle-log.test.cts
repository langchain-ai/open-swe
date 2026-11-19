const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { EventEmitter } = require("node:events");
const { LifecycleLog } = require("../build/lifecycle-log.cjs");

function tempLog(maxBytes) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "lifecycle-log-"));
  const file = path.join(dir, "desktop-lifecycle.log");
  return { file, log: () => new LifecycleLog(file, maxBytes) };
}

function fakeApp() {
  const app = new EventEmitter();
  app.calls = [];
  app.quit = () => app.calls.push("quit");
  app.exit = (code) => app.calls.push(`exit ${code}`);
  app.relaunch = () => app.calls.push("relaunch");
  return app;
}

test("records one line per event with flattened details", () => {
  const { log } = tempLog();
  const lifecycle = log();
  lifecycle.record("start", {
    version: "1.2.3",
    note: "a\n  b",
    skip: undefined,
  });
  const lines = lifecycle.read().trimEnd().split("\n");
  assert.equal(lines.length, 1);
  assert.match(
    lines[0],
    new RegExp(
      `^\\S+Z pid=${process.pid} start version=1\\.2\\.3 note=a \\| b$`,
    ),
  );
});

test("a quit nobody in the app asked for is marked external", () => {
  const { log } = tempLog();
  const lifecycle = log();
  const app = fakeApp();
  lifecycle.watchQuits(app);
  app.emit("before-quit");
  assert.match(lifecycle.read(), /before-quit requestedByApp=false/);
});

test("an app-initiated quit keeps working and records its caller", () => {
  const { log } = tempLog();
  const lifecycle = log();
  const app = fakeApp();
  lifecycle.watchQuits(app);
  app.relaunch();
  app.quit();
  app.emit("before-quit");
  app.emit("quit", {}, 0);
  app.exit(1);
  assert.deepEqual(app.calls, ["relaunch", "quit", "exit 1"]);
  const text = lifecycle.read();
  assert.match(text, /app\.quit stack=at .*lifecycle-log\.test/);
  assert.match(text, /before-quit requestedByApp=true/);
  assert.match(text, /quit exitCode=0/);
  assert.match(text, /app\.exit code=1 stack=/);
});

test("keeps the newest lines once the file outgrows its limit", () => {
  const { file, log } = tempLog(2000);
  const lifecycle = log();
  for (let i = 0; i < 100; i++) {
    lifecycle.record("event", { note: "日志".repeat(100), i });
    assert.ok(fs.statSync(file).size <= 2000);
  }
  const reopened = log();
  assert.equal(reopened.read(), lifecycle.read());
  const lines = reopened.read().trimEnd().split("\n");
  assert.match(lines.at(-1), / i=99$/);
  assert.doesNotMatch(reopened.read(), /\uFFFD/);
  assert.match(lines[0], /^\d{4}-\d\d-\d\dT/);
});

test("an unwritable path never throws", () => {
  const lifecycle = new LifecycleLog("/nonexistent-dir/desktop-lifecycle.log");
  lifecycle.record("start");
  assert.equal(lifecycle.read(), null);
});
