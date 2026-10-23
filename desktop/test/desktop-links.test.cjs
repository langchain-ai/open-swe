const test = require("node:test");
const assert = require("node:assert/strict");
const { DesktopLinks } = require("../build/desktop-links.cjs");

function desktopLinks(backendUrl) {
  const browserLinks = [];
  const appLinks = [];
  const links = new DesktopLinks({
    backendUrl: () => backendUrl,
    openApp: (url) => appLinks.push(url),
    openExternal: async (url) => browserLinks.push(url),
  });
  return {
    browserLinks,
    appLinks,
    ready: () => links.ready(),
    open: (url) => links.receive(url),
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
