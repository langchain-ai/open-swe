const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const {
  APP_URL,
  DEFAULT_DEVELOPMENT_BACKEND_URL,
  appRedirectUrl,
  backendRequestUrl,
  desktopExchangeUrl,
  desktopLoginUrl,
  desktopDeepLinkUrl,
  connectExchangeUrl,
  connectLoginUrl,
  isAppLoginUrl,
  isTrustedPermissionRequest,
  isTrustedProxyRequest,
  localCallbackUrl,
  parseDesktopEnvironments,
  resolveBackendUrl,
  resolveAppRuntime,
  staticFilePath,
  validateBackendUrl,
} = require("../build/config.cjs");

const { DesktopDistribution } = require("../build/distribution.cjs");

test("Agent Workspace cannot be redirected by Open SWE backend settings", () => {
  const workspace = new DesktopDistribution("agent-workspace");
  assert.equal(
    workspace.backendUrl({
      argv: ["--backend-url=https://other.example"],
      env: { OPEN_SWE_BACKEND_URL: "https://env.example" },
      isPackaged: true,
      storedUrl: "https://stored.example",
    }),
    "https://openswe.langchain.dev/",
  );
  assert.equal(
    workspace.backendUrl({ argv: [], env: {}, isPackaged: false }),
    "https://openswe.langchain.dev/",
  );
  assert.equal(workspace.usesSharedConfig(false), false);
  assert.equal(workspace.usesSharedConfig(true), false);
});

test("Agent Workspace isolates packaged and development Electron storage", () => {
  const workspace = new DesktopDistribution("agent-workspace");
  const appDataPath = path.join("/tmp", "app-data");
  const packaged = workspace.runtime({
    argv: [],
    isPackaged: true,
    appDataPath,
  });
  const development = workspace.runtime({
    argv: [],
    isPackaged: false,
    appDataPath,
  });
  assert.equal(packaged.name, "Agent Workspace");
  assert.equal(
    packaged.userDataPath,
    path.join(appDataPath, "Agent Workspace"),
  );
  assert.notEqual(packaged.userDataPath, development.userDataPath);
  assert.notEqual(packaged.appUserModelId, development.appUserModelId);
  assert.equal(
    new DesktopDistribution("open-swe").usesSharedConfig(false),
    true,
  );
});

test("Agent Workspace starts LangSmith login with the same PKCE loopback handoff", () => {
  const flow = { challenge: "pkce-challenge", port: 4567 };
  const backend = "https://openswe.langchain.dev/";
  const workspaceUrl = new URL(
    new DesktopDistribution("agent-workspace").loginUrl(backend, flow),
  );
  const codingUrl = new URL(
    new DesktopDistribution("open-swe").loginUrl(backend, flow),
  );
  assert.equal(workspaceUrl.pathname, "/dashboard/api/auth/langsmith/login");
  assert.equal(codingUrl.pathname, "/dashboard/api/auth/login");
  assert.equal(
    workspaceUrl.searchParams.get("desktop_handoff"),
    flow.challenge,
  );
  assert.equal(
    workspaceUrl.searchParams.get("desktop_port"),
    String(flow.port),
  );
  assert.equal(workspaceUrl.searchParams.get("desktop"), "true");
  assert.equal(
    isAppLoginUrl(`${APP_URL}dashboard/api/auth/langsmith/login`),
    true,
  );
});

test("uses the local backend for development", () => {
  assert.equal(
    resolveBackendUrl({ argv: [], env: {}, isPackaged: false }),
    `${DEFAULT_DEVELOPMENT_BACKEND_URL}/`,
  );
});

test("uses an isolated app profile for development runs", () => {
  const appDataPath = path.join("/tmp", "open-swe-app-data");
  const expected = {
    isDevelopment: true,
    name: "Open SWE Development",
    appUserModelId: "com.langchain.openswe.dev",
    userDataPath: path.join(appDataPath, "Open SWE Development"),
  };
  assert.deepEqual(
    resolveAppRuntime({ argv: [], isPackaged: false, appDataPath }),
    expected,
  );
  assert.deepEqual(
    resolveAppRuntime({ argv: ["--dev"], isPackaged: true, appDataPath }),
    expected,
  );
  assert.deepEqual(
    resolveAppRuntime({ argv: [], isPackaged: true, appDataPath }),
    {
      isDevelopment: false,
      name: "Open SWE",
      appUserModelId: "com.langchain.openswe",
      userDataPath: null,
    },
  );
});

test("requires backend configuration in packaged builds", () => {
  assert.equal(
    resolveBackendUrl({ argv: [], env: {}, isPackaged: true }),
    null,
  );
});

test("uses the stored backend in packaged builds", () => {
  assert.equal(
    resolveBackendUrl({
      argv: [],
      env: {},
      isPackaged: true,
      storedUrl: "https://open-swe.example.com",
    }),
    "https://open-swe.example.com/",
  );
});

test("command-line and environment configuration override the stored backend", () => {
  assert.equal(
    resolveBackendUrl({
      argv: ["--backend-url=https://cli.example"],
      env: { OPEN_SWE_BACKEND_URL: "https://env.example" },
      isPackaged: true,
      storedUrl: "https://stored.example",
    }),
    "https://cli.example/",
  );
});

test("supports the original desktop URL overrides", () => {
  assert.equal(
    resolveBackendUrl({
      argv: [],
      env: { OPEN_SWE_DESKTOP_URL: "http://localhost:4000" },
      isPackaged: true,
    }),
    "http://localhost:4000/",
  );
  assert.equal(
    resolveBackendUrl({
      argv: ["--url=https://legacy.example/app"],
      env: {},
      isPackaged: true,
    }),
    "https://legacy.example/app",
  );
});

test("rejects non-web backend URLs", () => {
  assert.throws(
    () => validateBackendUrl("file:///tmp/index.html"),
    /http or https/,
  );
  assert.throws(
    () => validateBackendUrl("javascript:alert(1)"),
    /http or https/,
  );
});

test("only grants expected permissions to the bundled app", () => {
  assert.equal(
    isTrustedPermissionRequest("notifications", `${APP_URL}settings`),
    true,
  );
  assert.equal(isTrustedPermissionRequest("media", APP_URL), false);
  assert.equal(isTrustedPermissionRequest("camera", APP_URL), false);
  assert.equal(
    isTrustedPermissionRequest("notifications", "https://dashboard.example"),
    false,
  );
});

test("only proxies requests from the bundled app window", () => {
  assert.equal(isTrustedProxyRequest(APP_URL), true);
  assert.equal(isTrustedProxyRequest("https://evil.example"), false);
  assert.equal(
    isTrustedProxyRequest("https://github.com/login/oauth/authorize"),
    false,
  );
});

test("sends login to the user's browser instead of the app window", () => {
  assert.equal(isAppLoginUrl(`${APP_URL}dashboard/api/auth/login`), true);
  assert.equal(
    isAppLoginUrl(`${APP_URL}dashboard/api/auth/login?redirect_to=%2F`),
    true,
  );
  assert.equal(isAppLoginUrl(`${APP_URL}dashboard/api/auth/callback`), false);
  assert.equal(
    isAppLoginUrl("https://backend.example/dashboard/api/auth/login"),
    false,
  );
});

test("carries the loopback port and PKCE challenge into the browser login", () => {
  assert.equal(
    desktopLoginUrl("https://backend.example", {
      challenge: "abc",
      port: 51234,
    }),
    "https://backend.example/dashboard/api/auth/login?desktop=true&desktop_handoff=abc&desktop_port=51234",
  );
  assert.equal(
    desktopExchangeUrl("https://backend.example/base/"),
    "https://backend.example/dashboard/api/auth/desktop/exchange",
  );
  assert.equal(
    connectLoginUrl("https://backend.example", "slack", {
      challenge: "abc",
      port: 51234,
    }),
    "https://backend.example/dashboard/api/slack/login?desktop_handoff=abc&desktop_port=51234",
  );
  assert.equal(
    connectExchangeUrl("https://backend.example", "langsmith"),
    "https://backend.example/dashboard/api/langsmith/desktop/exchange",
  );
});

test("maps desktop API requests to the selected backend", () => {
  assert.equal(
    backendRequestUrl(
      "https://backend.example/base/",
      `${APP_URL}dashboard/api/threads?limit=20`,
    ),
    "https://backend.example/dashboard/api/threads?limit=20",
  );
  assert.equal(
    backendRequestUrl(
      "https://backend.example",
      `${APP_URL}dashboard/api/auth/login`,
    ),
    "https://backend.example/dashboard/api/auth/login?desktop=true",
  );
});

test("localizes backend OAuth callbacks and post-login redirects", () => {
  assert.equal(
    localCallbackUrl(
      "https://backend.example/dashboard/api/auth/callback?code=123&state=456",
      "https://backend.example",
    ),
    `${APP_URL}dashboard/api/auth/callback?code=123&state=456`,
  );
  assert.equal(
    localCallbackUrl(
      "https://evil.example/dashboard/api/auth/callback",
      "https://backend.example",
    ),
    null,
  );
  assert.equal(
    localCallbackUrl("javascript:alert(1)", "https://backend.example"),
    null,
  );
  assert.equal(
    localCallbackUrl(
      "https://backend.example/dashboard/api/me",
      "https://backend.example",
    ),
    null,
  );
  assert.equal(
    appRedirectUrl(
      "https://dashboard.example/agents/thread-1?from=oauth#latest",
    ),
    `${APP_URL}agents/thread-1?from=oauth#latest`,
  );
});

test("opens only dashboard links from the configured backend", () => {
  const backend = "https://openswe.langchain.dev";
  assert.equal(
    desktopDeepLinkUrl(`${backend}/agents/thread-1?tab=plan#latest`, backend),
    `${APP_URL}agents/thread-1?tab=plan#latest`,
  );
  assert.equal(
    desktopDeepLinkUrl(`${backend}/review`, backend),
    `${APP_URL}review`,
  );
  assert.equal(
    desktopDeepLinkUrl(`${backend}/agents/local/private-thread`, backend),
    `${APP_URL}agents/local/private-thread`,
  );
  for (const url of [
    "https://openswe.vercel.app/agents/thread-1",
    `${backend}/dashboard/api/auth/callback?code=secret`,
    `${backend}/assets/app.js`,
    "open-swe://link/agents/thread-1",
    "javascript:alert(1)",
  ])
    assert.equal(desktopDeepLinkUrl(url, backend), null);
});

test("keeps static file resolution inside the bundled UI root", () => {
  const root = path.resolve("/tmp/open-swe-ui");
  assert.equal(
    staticFilePath(root, `${APP_URL}assets/app.js`),
    path.join(root, "assets/app.js"),
  );
  assert.equal(staticFilePath(root, `${APP_URL}%2e%2e%2fsecret`), null);
});

test("keeps only well-formed alternate environments", () => {
  assert.deepEqual(
    parseDesktopEnvironments({
      environments: [
        { name: "staging", url: "https://staging.example" },
        { name: "bad", url: "file:///etc/passwd" },
        { name: 1, url: "https://x.example" },
      ],
    }),
    [{ name: "staging", url: "https://staging.example/" }],
  );
  assert.deepEqual(parseDesktopEnvironments(null), []);
});
