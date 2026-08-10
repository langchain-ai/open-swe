const test = require("node:test")
const assert = require("node:assert/strict")
const path = require("node:path")
const {
  APP_URL,
  DEFAULT_DEVELOPMENT_BACKEND_URL,
  appRedirectUrl,
  backendRequestUrl,
  isGithubOAuthUrl,
  isTrustedPermissionRequest,
  isTrustedProxyRequest,
  localCallbackUrl,
  resolveBackendUrl,
  resolveAppRuntime,
  staticFilePath,
  validateBackendUrl,
} = require("../src/config.cjs")

test("uses the local backend for development", () => {
  assert.equal(
    resolveBackendUrl({ argv: [], env: {}, isPackaged: false }),
    `${DEFAULT_DEVELOPMENT_BACKEND_URL}/`
  )
})

test("uses an isolated app profile for development runs", () => {
  const appDataPath = path.join("/tmp", "open-swe-app-data")
  const expected = {
    isDevelopment: true,
    name: "Open SWE Development",
    appUserModelId: "com.langchain.openswe.dev",
    userDataPath: path.join(appDataPath, "Open SWE Development"),
  }
  assert.deepEqual(
    resolveAppRuntime({ argv: [], isPackaged: false, appDataPath }),
    expected
  )
  assert.deepEqual(
    resolveAppRuntime({ argv: ["--dev"], isPackaged: true, appDataPath }),
    expected
  )
  assert.deepEqual(
    resolveAppRuntime({ argv: [], isPackaged: true, appDataPath }),
    {
      isDevelopment: false,
      name: "Open SWE",
      appUserModelId: "com.langchain.openswe",
      userDataPath: null,
    }
  )
})

test("requires backend configuration in packaged builds", () => {
  assert.equal(resolveBackendUrl({ argv: [], env: {}, isPackaged: true }), null)
})

test("uses the stored backend in packaged builds", () => {
  assert.equal(
    resolveBackendUrl({
      argv: [],
      env: {},
      isPackaged: true,
      storedUrl: "https://open-swe.example.com",
    }),
    "https://open-swe.example.com/"
  )
})

test("command-line and environment configuration override the stored backend", () => {
  assert.equal(
    resolveBackendUrl({
      argv: ["--backend-url=https://cli.example"],
      env: { OPEN_SWE_BACKEND_URL: "https://env.example" },
      isPackaged: true,
      storedUrl: "https://stored.example",
    }),
    "https://cli.example/"
  )
})

test("supports the original desktop URL overrides", () => {
  assert.equal(
    resolveBackendUrl({
      argv: [],
      env: { OPEN_SWE_DESKTOP_URL: "http://localhost:4000" },
      isPackaged: true,
    }),
    "http://localhost:4000/"
  )
  assert.equal(
    resolveBackendUrl({
      argv: ["--url=https://legacy.example/app"],
      env: {},
      isPackaged: true,
    }),
    "https://legacy.example/app"
  )
})

test("rejects non-web backend URLs", () => {
  assert.throws(() => validateBackendUrl("file:///tmp/index.html"), /http or https/)
  assert.throws(() => validateBackendUrl("javascript:alert(1)"), /http or https/)
})

test("only grants expected permissions to the bundled app", () => {
  assert.equal(isTrustedPermissionRequest("notifications", `${APP_URL}settings`), true)
  assert.equal(isTrustedPermissionRequest("camera", APP_URL), false)
  assert.equal(
    isTrustedPermissionRequest("notifications", "https://dashboard.example"),
    false
  )
})

test("only proxies requests from the bundled app window", () => {
  const requestUrl = `${APP_URL}dashboard/api/threads/thread-id/commands`
  assert.equal(isTrustedProxyRequest("POST", APP_URL, requestUrl), true)
  assert.equal(
    isTrustedProxyRequest("POST", "https://evil.example", requestUrl),
    false
  )
  assert.equal(
    isTrustedProxyRequest(
      "GET",
      "https://github.com/login/oauth/authorize",
      `${APP_URL}dashboard/api/auth/callback?code=123`
    ),
    true
  )
  assert.equal(
    isTrustedProxyRequest(
      "GET",
      "https://github.com/login/oauth/authorize",
      `${APP_URL}dashboard/api/me`
    ),
    false
  )
})

test("only keeps GitHub login pages in the app window", () => {
  assert.equal(isGithubOAuthUrl("https://github.com/login/oauth/authorize?client_id=1"), true)
  assert.equal(isGithubOAuthUrl("https://github.com/login?return_to=%2Flogin%2Foauth"), true)
  assert.equal(isGithubOAuthUrl("https://github.com/session"), true)
  assert.equal(isGithubOAuthUrl("https://github.com/sessions/two-factor"), true)
  assert.equal(isGithubOAuthUrl("https://github.com/langchain-ai/open-swe"), false)
  assert.equal(isGithubOAuthUrl("https://evil.example/login/oauth/authorize"), false)
})

test("maps desktop API requests to the selected backend", () => {
  assert.equal(
    backendRequestUrl(
      "https://backend.example/base/",
      `${APP_URL}dashboard/api/threads?limit=20`
    ),
    "https://backend.example/dashboard/api/threads?limit=20"
  )
  assert.equal(
    backendRequestUrl("https://backend.example", `${APP_URL}dashboard/api/auth/login`),
    "https://backend.example/dashboard/api/auth/login?desktop=true"
  )
})

test("localizes backend OAuth callbacks and post-login redirects", () => {
  assert.equal(
    localCallbackUrl(
      "https://backend.example/dashboard/api/auth/callback?code=123&state=456",
      "https://backend.example"
    ),
    `${APP_URL}dashboard/api/auth/callback?code=123&state=456`
  )
  assert.equal(
    localCallbackUrl(
      "https://evil.example/dashboard/api/auth/callback",
      "https://backend.example"
    ),
    null
  )
  assert.equal(localCallbackUrl("javascript:alert(1)", "https://backend.example"), null)
  assert.equal(
    localCallbackUrl("https://backend.example/dashboard/api/me", "https://backend.example"),
    null
  )
  assert.equal(
    appRedirectUrl("https://dashboard.example/agents/thread-1?from=oauth#latest"),
    `${APP_URL}agents/thread-1?from=oauth#latest`
  )
})

test("keeps static file resolution inside the bundled UI root", () => {
  const root = path.resolve("/tmp/open-swe-ui")
  assert.equal(
    staticFilePath(root, `${APP_URL}assets/app.js`),
    path.join(root, "assets/app.js")
  )
  assert.equal(staticFilePath(root, `${APP_URL}%2e%2e%2fsecret`), null)
})
