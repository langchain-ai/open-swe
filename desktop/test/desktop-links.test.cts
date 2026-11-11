const test = require("node:test");
const assert = require("node:assert/strict");
const { DesktopLinks } = require("../build/desktop-links.cjs");

// Lets the async handling of a link on another origin settle.
const settle = () => new Promise((resolve) => setImmediate(resolve));

function desktopLinks(
  backendUrl,
  dashboardOrigins = [],
  fetchBackend = async () => new Response(null, { status: 401 }),
) {
  const browserLinks = [];
  const appLinks = [];
  const links = new DesktopLinks({
    backendUrl: () => backendUrl,
    dashboardOrigins: async () => dashboardOrigins,
    hasPage: async (pathname) =>
      /^\/(?:agents\/[^/]+(?:\/plan)?|review|my-settings\/connections)$/.test(
        pathname,
      ),
    openApp: (url) => appLinks.push(url),
    openExternal: async (url) => browserLinks.push(url),
    fetchBackend,
  });
  return {
    browserLinks,
    appLinks,
    slackUrl: (url) => links.slackUrl(url),
    openSlack: (url) => links.openSlack(url),
    ready: () => links.ready(),
    open: (url) => links.receive(url),
  };
}

test("routes other deployments to the browser without navigating desktop", async () => {
  const links = desktopLinks("https://preview.example");
  links.ready();
  links.open("https://prod.example/agents/thread-1?tab=plan#latest");
  links.open("https://preview.example/agents/thread-2");
  await settle();
  assert.deepEqual(links.browserLinks, [
    "https://prod.example/agents/thread-1?tab=plan#latest",
  ]);
  assert.deepEqual(links.appLinks, ["open-swe://app/agents/thread-2"]);
});

test("resolves cold-start links once, including unconfigured backends", async () => {
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
    assert.equal(links.ready(), false);
    assert.equal(links.ready(), false);
    await settle();
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

test("resolves app and same-backend Slack links without following authenticated redirects", async () => {
  const location =
    "https://workspace.slack.com/archives/C123/p1700000000123456?thread_ts=1700000000.000001&cid=C123";
  const requests = [];
  const links = desktopLinks(
    "https://configured.example",
    [],
    async (url, init) => {
      requests.push({ url, redirect: init.redirect });
      return new Response(null, { status: 302, headers: { location } });
    },
  );
  for (const origin of ["open-swe://app", "https://configured.example"]) {
    await links.openSlack(
      `${origin}/dashboard/api/slack/open?channel=C123&message_ts=1700000000.123456`,
    );
  }
  assert.deepEqual(
    requests,
    Array(2).fill({
      url: "https://configured.example/dashboard/api/slack/open?channel=C123&message_ts=1700000000.123456",
      redirect: "manual",
    }),
  );
  assert.deepEqual(links.browserLinks, [location, location]);
  assert.deepEqual(links.appLinks, []);
  for (const url of [
    "https://other.example/dashboard/api/slack/open?channel=C123",
    "https://configured.example/dashboard/api/slack/open/other",
    "open-swe://app/dashboard/api/auth/login",
    "open-swe://attacker@app/dashboard/api/slack/open",
    "open-swe://app:123/dashboard/api/slack/open",
    "not a URL",
  ]) {
    assert.equal(links.slackUrl(url), null);
    await assert.rejects(links.openSlack(url), /Invalid Slack link/);
  }
  assert.equal(requests.length, 2);
});

test("does not open failed or unsafe Slack resolutions", async () => {
  for (const [status, location] of [
    [401, "https://workspace.slack.com/archives/C123"],
    [302, "https://workspace.slack.com.evil.example/archives/C123"],
    [302, "https://app.slack.com/archives/C123"],
    [302, "https://workspace.slack.com/login"],
    [302, "https://attacker@workspace.slack.com/archives/C123"],
    [302, "https://workspace.slack.com:123/archives/C123"],
    [302, "http://workspace.slack.com/archives/C123"],
    [302, "file:///archives/C123"],
    [302, "/archives/C123"],
    [302, ""],
  ]) {
    const links = desktopLinks(
      "https://configured.example",
      [],
      async () => new Response(null, { status, headers: { location } }),
    );
    await assert.rejects(
      links.openSlack("open-swe://app/dashboard/api/slack/open?channel=C123"),
    );
    assert.deepEqual(links.browserLinks, []);
  }
});

test("does not forward unsafe activity URLs to the OS", async () => {
  const links = desktopLinks("https://preview.example");
  links.ready();
  for (const url of ["file:///tmp/secret", "javascript:alert(1)", "not a URL"])
    links.open(url);
  await settle();
  assert.deepEqual(links.browserLinks, []);
  assert.deepEqual(links.appLinks, []);
});

test("opens links on the backend's other dashboard origins in the app", async () => {
  const links = desktopLinks("https://openswe.example", [
    "https://robin.example",
    "https://openswe.example",
  ]);
  links.open("https://robin.example/agents/thread-1");
  // A cold start on another origin opens a window first, then the link.
  assert.equal(links.ready(), false);
  await settle();
  links.open("https://robin.example/review");
  links.open("https://elsewhere.example/agents/thread-2");
  await settle();
  assert.deepEqual(links.appLinks, [
    "open-swe://app/agents/thread-1",
    "open-swe://app/review",
  ]);
  assert.deepEqual(links.browserLinks, [
    "https://elsewhere.example/agents/thread-2",
  ]);
});

test("falls back to the browser when the backend can't name its origins", async () => {
  const browserLinks = [];
  const links = new DesktopLinks({
    backendUrl: () => "https://openswe.example",
    dashboardOrigins: async () => {
      throw new Error("offline");
    },
    hasPage: async (pathname) =>
      /^\/(?:agents\/[^/]+(?:\/plan)?|review|my-settings\/connections)$/.test(
        pathname,
      ),
    openApp: () => assert.fail("opened in the app"),
    openExternal: async (url) => browserLinks.push(url),
  });
  links.ready();
  links.receive("https://robin.example/agents/thread-1");
  await settle();
  assert.deepEqual(browserLinks, ["https://robin.example/agents/thread-1"]);
});

test("resolves clicked artifact and settings URLs after dashboard discovery", async () => {
  let discover;
  const origins = new Promise((resolve) => {
    discover = resolve;
  });
  const links = new DesktopLinks({
    backendUrl: () => "https://openswe.example",
    dashboardOrigins: () => origins,
    hasPage: async (pathname) =>
      /^\/(?:agents\/[^/]+(?:\/plan)?|review|my-settings\/connections)$/.test(
        pathname,
      ),
    openApp: () => assert.fail("resolution must not navigate"),
    openExternal: async () => assert.fail("resolution must not open a browser"),
  });
  const artifact = links.appUrl(
    "https://robin.example/agents/thread-1/plan?view=full#section",
  );
  discover(["https://robin.example"]);
  assert.equal(
    await artifact,
    "open-swe://app/agents/thread-1/plan?view=full#section",
  );
  assert.equal(
    await links.appUrl("https://robin.example/my-settings/connections"),
    "open-swe://app/my-settings/connections",
  );
  assert.equal(
    await links.appUrl("https://elsewhere.example/agents/thread-1/plan"),
    null,
  );
  assert.equal(
    await links.appUrl("https://robin.example/dashboard/api/auth/login"),
    null,
  );
});
