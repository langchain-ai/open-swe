const test = require("node:test")
const assert = require("node:assert/strict")
const path = require("node:path")
const {
  APP_URL,
  DEFAULT_DEVELOPMENT_BACKEND_URL,
  appRedirectUrl,
  backendRequestUrl,
  isTrustedPermissionRequest,
  localCallbackUrl,
  resolveBackendUrl,
  staticFilePath,
  validateBackendUrl,
} = require("../src/config.cjs")

test("uses the local backend for development", () => {
  assert.equal(
    resolveBackendUrl({ argv: [], env: {}, isPackaged: false }),
    `${DEFAULT_DEVELOPMENT_BACKEND_URL}/`
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
      "https://backend.example/dashboard/api/auth/callback?code=123&state=456"
    ),
    `${APP_URL}dashboard/api/auth/callback?code=123&state=456`
  )
  assert.equal(
    localCallbackUrl("https://evil.example/dashboard/api/auth/callback"),
    `${APP_URL}dashboard/api/auth/callback`
  )
  assert.equal(localCallbackUrl("javascript:alert(1)"), null)
  assert.equal(localCallbackUrl("https://backend.example/dashboard/api/me"), null)
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
