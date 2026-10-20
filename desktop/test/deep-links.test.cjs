const test = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { runInNewContext } = require("node:vm");
const { EventEmitter } = require("node:events");
const { desktopDeepLinkUrl } = require("../build/config.cjs");

test("accepts macOS web activities and opens only trusted thread links", () => {
  const source = readFileSync(require.resolve("../build/main.cjs"), "utf8");
  const app = new EventEmitter();
  let currentUrl = "open-swe://app/agents/previous-thread";
  const window = {
    isDestroyed: () => false,
    isMinimized: () => false,
    show() {},
    focus() {},
    loadURL: async (url) => {
      currentUrl = url;
    },
  };
  runInNewContext(
    source.slice(
      source.indexOf("let pendingDeepLink"),
      source.indexOf("const hasSingleInstanceLock"),
    ),
    {
      app,
      backendUrl: "https://openswe.langchain.dev",
      desktopDeepLinkUrl,
      mainWindow: window,
    },
  );

  const event = () => ({
    defaultPrevented: false,
    preventDefault() {
      this.defaultPrevented = true;
    },
  });
  const unrelated = event();
  app.emit("will-continue-activity", unrelated, "unrelated-activity");
  assert.equal(unrelated.defaultPrevented, false);

  const ready = event();
  app.emit("will-continue-activity", ready, "NSUserActivityTypeBrowsingWeb");
  assert.equal(ready.defaultPrevented, true);

  const navigation = event();
  app.emit(
    "continue-activity",
    navigation,
    "NSUserActivityTypeBrowsingWeb",
    {},
    {
      webpageURL:
        "https://openswe.langchain.dev/agents/linked-thread?subagent=child",
    },
  );
  assert.equal(navigation.defaultPrevented, true);
  assert.equal(
    currentUrl,
    "open-swe://app/agents/linked-thread?subagent=child",
  );

  const untrusted = event();
  app.emit(
    "continue-activity",
    untrusted,
    "NSUserActivityTypeBrowsingWeb",
    {},
    {
      webpageURL: "https://evil.example/agents/another-thread",
    },
  );
  assert.equal(untrusted.defaultPrevented, false);
  assert.equal(
    currentUrl,
    "open-swe://app/agents/linked-thread?subagent=child",
  );
});
