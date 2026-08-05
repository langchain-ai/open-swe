const test = require("node:test")
const assert = require("node:assert/strict")
const {
  DEFAULT_DEVELOPMENT_URL,
  isTrustedPermissionRequest,
  resolveDashboardUrl,
  validateDashboardUrl,
} = require("../src/config.cjs")

test("uses the local dashboard for development", () => {
  assert.equal(
    resolveDashboardUrl({ argv: [], env: {}, isPackaged: false }),
    `${DEFAULT_DEVELOPMENT_URL}/`
  )
})

test("requires dashboard configuration in packaged builds", () => {
  assert.equal(
    resolveDashboardUrl({ argv: [], env: {}, isPackaged: true }),
    null
  )
})

test("uses the stored dashboard in packaged builds", () => {
  assert.equal(
    resolveDashboardUrl({
      argv: [],
      env: {},
      isPackaged: true,
      storedUrl: "https://open-swe.example.com",
    }),
    "https://open-swe.example.com/"
  )
})

test("allows environment and command-line URL overrides", () => {
  assert.equal(
    resolveDashboardUrl({
      argv: ["--url=https://cli.example/app"],
      env: { OPEN_SWE_DESKTOP_URL: "https://env.example" },
      isPackaged: true,
    }),
    "https://cli.example/app"
  )
  assert.equal(
    resolveDashboardUrl({
      argv: [],
      env: { OPEN_SWE_DESKTOP_URL: "http://localhost:4000" },
      isPackaged: true,
    }),
    "http://localhost:4000/"
  )
})

test("command-line and environment configuration override the stored dashboard", () => {
  assert.equal(
    resolveDashboardUrl({
      argv: ["--url=https://cli.example"],
      env: { OPEN_SWE_DESKTOP_URL: "https://env.example" },
      isPackaged: true,
      storedUrl: "https://stored.example",
    }),
    "https://cli.example/"
  )
})

test("rejects non-web URLs", () => {
  assert.throws(() => validateDashboardUrl("file:///tmp/index.html"), /http or https/)
  assert.throws(() => validateDashboardUrl("javascript:alert(1)"), /http or https/)
})

test("only grants expected permissions to the dashboard origin", () => {
  assert.equal(
    isTrustedPermissionRequest(
      "https://dashboard.example.com/app",
      "notifications",
      "https://dashboard.example.com/settings"
    ),
    true
  )
  assert.equal(
    isTrustedPermissionRequest(
      "https://dashboard.example.com",
      "clipboard-sanitized-write",
      "https://dashboard.example.com"
    ),
    true
  )
  assert.equal(
    isTrustedPermissionRequest(
      "https://dashboard.example.com",
      "camera",
      "https://dashboard.example.com"
    ),
    false
  )
  assert.equal(
    isTrustedPermissionRequest(
      "https://dashboard.example.com",
      "notifications",
      "https://example.com"
    ),
    false
  )
})
