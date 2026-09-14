const test = require("node:test");
const assert = require("node:assert/strict");
const {
  ConsoleLogBuffer,
  buildDiagnosticsReport,
  captureProcessConsole,
  diagnosticsFileName,
  normalizeConsoleMessage,
  redactSecrets,
} = require("../build/diagnostics.cjs");

const APP = {
  name: "Open SWE",
  version: "0.2.9",
  isPackaged: true,
  electron: "43.4.1",
  chrome: "146.0.0.0",
  node: "24.0.0",
  platform: "darwin",
  arch: "arm64",
  osRelease: "24.6.0",
  locale: "en-US",
};

test("normalizes renderer console messages and drops unknown levels to info", () => {
  const at = new Date("2026-09-14T10:00:00Z");
  assert.deepEqual(
    normalizeConsoleMessage(
      {
        level: "error",
        message: "boom",
        sourceId: "open-swe://app/assets/index.js",
        lineNumber: 12,
      },
      at,
    ),
    {
      at: "2026-09-14T10:00:00.000Z",
      level: "error",
      message: "boom",
      source: "open-swe://app/assets/index.js:12",
    },
  );
  assert.equal(
    normalizeConsoleMessage({ level: 3, message: "x" }, at).level,
    "error",
  );
  assert.equal(
    normalizeConsoleMessage({ level: 0, message: "x" }, at).level,
    "debug",
  );
  assert.equal(
    normalizeConsoleMessage({ level: "verbose", message: 1 }, at).level,
    "info",
  );
  assert.equal(
    normalizeConsoleMessage({ level: "verbose", message: 1 }, at).message,
    "1",
  );
});

test("buffer keeps only the newest entries", () => {
  const buffer = new ConsoleLogBuffer(2);
  for (const message of ["a", "b", "c"]) {
    buffer.push({ at: "t", level: "info", message, source: null });
  }
  assert.deepEqual(
    buffer.entries().map((entry) => entry.message),
    ["b", "c"],
  );
});

test("captures the main process console without silencing it", () => {
  const buffer = new ConsoleLogBuffer();
  const seen = [];
  const fakeConsole = {
    log: (...args) => seen.push(["log", ...args]),
    info: (...args) => seen.push(["info", ...args]),
    warn: (...args) => seen.push(["warn", ...args]),
    error: (...args) => seen.push(["error", ...args]),
  };
  const restore = captureProcessConsole(buffer, fakeConsole);
  fakeConsole.warn("Could not check for updates", new Error("offline"));
  restore();
  fakeConsole.warn("after restore");

  assert.equal(buffer.entries().length, 1);
  assert.equal(buffer.entries()[0].level, "warning");
  assert.match(
    buffer.entries()[0].message,
    /Could not check for updates Error: offline/,
  );
  assert.equal(seen.length, 2);
});

test("redacts sessions, bearer tokens, and provider keys", () => {
  const text = [
    "cookie: osw_session=abc.def.ghi; theme=dark",
    "Authorization: Bearer eyJhbGciOi.payload.sig",
    "GET /callback?code=1234&state=xyz&other=1",
    "token ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123 and sk-abcdefghijklmnopqrstuvwxyz",
  ].join("\n");
  const redacted = redactSecrets(text);
  assert.doesNotMatch(
    redacted,
    /abc\.def\.ghi|eyJhbGciOi|1234|xyz|ghp_ABCDEF|sk-abcdef/,
  );
  assert.match(redacted, /osw_session=\[redacted\]; theme=dark/);
  assert.match(redacted, /code=\[redacted\]&state=\[redacted\]&other=1/);
});

test("builds a readable report with every section", () => {
  const report = buildDiagnosticsReport({
    app: APP,
    backendHost: "swe.example.com",
    renderer: [
      {
        at: "2026-09-14T10:00:00.000Z",
        level: "error",
        message: "Failed to fetch\nsecond line",
        source: "index.js:1",
      },
    ],
    main: [],
    perf: '{"spans":[]}',
    generatedAt: new Date("2026-09-14T10:01:00Z"),
  });
  assert.match(
    report,
    /^Open SWE diagnostics report\nGenerated: 2026-09-14T10:01:00.000Z/,
  );
  assert.match(report, /version: 0\.2\.9\n/);
  assert.match(report, /backend: swe\.example\.com/);
  assert.match(
    report,
    /## Renderer console \(1 entries, newest last\)\n2026-09-14T10:00:00.000Z error   index\.js:1\n  Failed to fetch\n  second line/,
  );
  assert.match(
    report,
    /## Main process console \(0 entries, newest last\)\n\(empty\)/,
  );
  assert.match(report, /## Performance spans\n\{"spans":\[\]\}/);
});

test("names the file by timestamp", () => {
  assert.equal(
    diagnosticsFileName(new Date("2026-09-14T10:01:02.345Z")),
    "open-swe-diagnostics-2026-09-14T10-01-02.txt",
  );
});
