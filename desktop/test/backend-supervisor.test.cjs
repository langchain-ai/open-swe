const test = require("node:test")
const assert = require("node:assert/strict")
const path = require("node:path")

const {
  devBackendTarget,
  localBackendTarget,
  packagedBackendTarget,
} = require("../src/backend-supervisor.cjs")

test("development target runs the repository LangGraph app through uv", () => {
  const repoRoot = path.resolve("/work/open-swe")
  assert.deepEqual(devBackendTarget({ repoRoot, port: 49152, env: {} }), {
    command: "uv",
    args: [
      "run",
      "langgraph",
      "dev",
      "--no-browser",
      "--no-reload",
      "--host",
      "127.0.0.1",
      "--port",
      "49152",
      "--config",
      path.join(repoRoot, "langgraph.desktop.json"),
    ],
    cwd: repoRoot,
  })
})

test("packaged target runs the bundled backend without dcode", () => {
  const resourcesPath = path.resolve("/Applications/Open SWE.app/Contents/Resources")
  const target = packagedBackendTarget({ resourcesPath, port: 50000, platform: "darwin" })
  assert.equal(target.command, path.join(resourcesPath, "local-backend/runtime/bin/python3"))
  assert.deepEqual(target.args.slice(0, 3), ["-m", "langgraph_cli.cli", "dev"])
  assert.equal(target.args.includes("dcode"), false)
  assert.equal(target.cwd, path.join(resourcesPath, "local-backend"))
  assert.deepEqual(
    localBackendTarget({ isPackaged: true, resourcesPath, port: 50000, platform: "darwin" }),
    target
  )
})
