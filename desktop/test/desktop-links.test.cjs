const test = require("node:test");
const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const { readFileSync } = require("node:fs");
const { runInNewContext } = require("node:vm");
const { desktopDeepLinkUrl } = require("../build/config.cjs");

function desktopLinks(backendUrl) {
  const source = readFileSync(require.resolve("../build/main.cjs"), "utf8");
  const browserLinks = [];
  const appLinks = [];
  const window = {
    isDestroyed: () => false,
    isMinimized: () => false,
    show() {},
    focus() {},
  };
  const context = {
    app: new EventEmitter(),
    backendUrl,
    desktopDeepLinkUrl,
    mainWindow: window,
    createWindow: (url) => {
      appLinks.push(url);
      return window;
    },
    loadApp: (_window, url) => appLinks.push(url),
    shell: {
      openExternal: async (url) => browserLinks.push(url),
    },
    URL,
    console,
  };
  runInNewContext(
    source.slice(
      source.indexOf("let pendingDeepLink"),
      source.indexOf("const hasSingleInstanceLock"),
    ),
    context,
  );
  return {
    browserLinks,
    appLinks,
    ready: () => runInNewContext("openPendingDesktopLink()", context),
    open: (url) => {
      let prevented = false;
      context.app.emit(
        "continue-activity",
        { preventDefault: () => (prevented = true) },
        "NSUserActivityTypeBrowsingWeb",
        {},
        { webpageURL: url },
      );
      assert.equal(prevented, true);
    },
  };
}

test("routes other deployments to the browser without navigating desktop", () => {
  const links = desktopLinks("https://preview.example");
  links.ready();
  links.open("https://prod.example/agents/thread-1?tab=plan#latest");
  links.open("https://preview.example/agents/thread-2");
  assert.deepEqual(links.browserLinks, [
    "https://prod.example/agents/thread-1?tab=plan#latest",
  ]);
  assert.deepEqual(links.appLinks, ["open-swe://app/agents/thread-2"]);
});

test("resolves cold-start links once, including unconfigured backends", () => {
  for (const backend of [
    null,
    "https://preview.example",
    "https://prod.example",
  ]) {
    const links = desktopLinks(backend);
    const url = "https://prod.example/review";
    links.open(url);
    assert.deepEqual(links.browserLinks, []);
    assert.deepEqual(links.appLinks, []);
    assert.equal(links.ready(), backend === "https://prod.example");
    assert.equal(links.ready(), false);
    assert.deepEqual(
      links.browserLinks,
      backend === "https://prod.example" ? [] : [url],
    );
    assert.deepEqual(
      links.appLinks,
      backend === "https://prod.example" ? ["open-swe://app/review"] : [],
    );
  }
});

test("does not forward unsafe activity URLs to the OS", () => {
  const links = desktopLinks("https://preview.example");
  links.ready();
  for (const url of ["file:///tmp/secret", "javascript:alert(1)", "not a URL"])
    links.open(url);
  assert.deepEqual(links.browserLinks, []);
  assert.deepEqual(links.appLinks, []);
});
