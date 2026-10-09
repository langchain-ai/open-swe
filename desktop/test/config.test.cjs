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
  resolveBackendUrl,
  resolveAppRuntime,
  staticFilePath,
  validateBackendUrl,
} = require("../build/config.cjs");

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
    isTrustedPermissionRequest("notifications", "https://web.example"),
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
  assert.equal(isAppLoginUrl(`${APP_URL}api/auth/login`), true);
  assert.equal(isAppLoginUrl(`${APP_URL}api/auth/login?redirect_to=%2F`), true);
  assert.equal(isAppLoginUrl(`${APP_URL}dashboard/api/auth/callback`), false);
  assert.equal(isAppLoginUrl("https://backend.example/api/auth/login"), false);
});

test("carries the loopback port and PKCE challenge into the browser login", () => {
  assert.equal(
    desktopLoginUrl("https://backend.example", {
      challenge: "abc",
      port: 51234,
    }),
    "https://backend.example/api/auth/login?desktop=true&desktop_handoff=abc&desktop_port=51234",
  );
  assert.equal(
    desktopExchangeUrl("https://backend.example/base/"),
    "https://backend.example/api/auth/desktop/exchange",
  );
  assert.equal(
    connectLoginUrl("https://backend.example", "slack", {
      challenge: "abc",
      port: 51234,
    }),
    "https://backend.example/api/slack/login?desktop_handoff=abc&desktop_port=51234",
  );
  assert.equal(
    connectExchangeUrl("https://backend.example", "langsmith"),
    "https://backend.example/api/langsmith/desktop/exchange",
  );
});

test("maps desktop API requests to the selected backend", () => {
  assert.equal(
    backendRequestUrl(
      "https://backend.example/base/",
      `${APP_URL}api/threads?limit=20`,
    ),
    "https://backend.example/api/threads?limit=20",
  );
  assert.equal(
    backendRequestUrl("https://backend.example", `${APP_URL}api/auth/login`),
    "https://backend.example/api/auth/login?desktop=true",
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
      "https://backend.example/api/me",
      "https://backend.example",
    ),
    null,
  );
  assert.equal(
    appRedirectUrl("https://web.example/agents/thread-1?from=oauth#latest"),
    `${APP_URL}agents/thread-1?from=oauth#latest`,
  );
});

test("opens only web links from the configured backend", () => {
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
