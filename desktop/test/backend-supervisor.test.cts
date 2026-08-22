const test = require("node:test")
const assert = require("node:assert/strict")
const path = require("node:path")

const {
  BackendSupervisor,
  devBackendTarget,
  localBackendTarget,
  modelCredentialStatus,
  packagedBackendTarget,
} = require("../build/backend-supervisor.cjs")

test("development target runs the compiled graph server with Node 24", () => {
  const repoRoot = path.resolve("/work/open-swe")
  assert.deepEqual(devBackendTarget({ repoRoot, port: 49152, env: {} }), {
    command: path.join(repoRoot, "desktop/node_modules/node/bin/node"),
    args: [
      path.join(repoRoot, "apps/graphs/dist/server.js"),
      "--host",
      "127.0.0.1",
      "--port",
      "49152",
      "--state-dir",
      path.join(repoRoot, ".local-graph-state"),
    ],
    cwd: repoRoot,
  })
})

test("development target accepts an explicit server script", () => {
  const repoRoot = path.resolve("/work/open-swe")
  const script = path.resolve("/work/e2e/server.js")
  const target = devBackendTarget({
    repoRoot,
    port: 49152,
    env: { OPEN_SWE_LOCAL_BACKEND_SCRIPT: script },
  })

  assert.equal(target.args[0], script)
})

test("development target accepts a TypeScript graph entrypoint", () => {
  const repoRoot = path.resolve("/work/open-swe")
  const graph = path.resolve("/work/e2e/agent.ts")
  const target = devBackendTarget({
    repoRoot,
    port: 49152,
    env: { OPEN_SWE_LOCAL_GRAPH_ENTRYPOINT: graph },
  })

  assert.deepEqual(target.args.slice(-2), ["--graph-entrypoint", graph])
})

test("reports whether the selected provider is configured", () => {
  assert.deepEqual(modelCredentialStatus("openai:gpt-test", {}), {
    available: false,
    variable: "OPENAI_API_KEY",
    canSignIn: true,
  })
  assert.deepEqual(modelCredentialStatus("openai:gpt-test", { OPENAI_API_KEY: "test-key" }), {
    available: true,
    variable: "OPENAI_API_KEY",
  })
  assert.deepEqual(modelCredentialStatus("openai:gpt-test", {}, { openAiOAuth: true }), {
    available: true,
    variable: null,
    canSignIn: true,
  })
  assert.deepEqual(modelCredentialStatus("google_genai:test", { GEMINI_API_KEY: "test-key" }), {
    available: true,
    variable: "GEMINI_API_KEY",
  })
  assert.deepEqual(modelCredentialStatus("custom:test", {}), {
    available: true,
    variable: null,
  })
})

test("creates the local LangGraph thread before stream hydration", async () => {
  const supervisor = new BackendSupervisor({})
  let request
  supervisor.request = async (pathname, init) => {
    request = { pathname, init }
    return new Response(null, { status: 200 })
  }

  await supervisor.createThread("thread-1")

  assert.equal(request.pathname, "/threads")
  assert.equal(request.init.method, "POST")
  assert.equal(new Headers(request.init.headers).get("content-type"), "application/json")
  assert.deepEqual(JSON.parse(request.init.body), {
    thread_id: "thread-1",
    if_exists: "do_nothing",
    metadata: { graph_id: "agent" },
  })
})

test("rejects a failed local LangGraph thread creation", async () => {
  const supervisor = new BackendSupervisor({})
  supervisor.request = async () => new Response(null, { status: 503 })

  await assert.rejects(
    supervisor.createThread("thread-1"),
    /Could not create local LangGraph thread \(503\)/
  )
})

test("derives thread activity from the backend without starting it", async () => {
  const idle = new BackendSupervisor({
    fetch: () => assert.fail("must not reach a backend that is not running"),
  })
  assert.deepEqual(await idle.threadActivity(), {})

  const supervisor = new BackendSupervisor({
    fetch: async () =>
      Response.json([
        { thread_id: "thread-1", status: "busy" },
        { thread_id: "thread-2", status: "idle" },
        { thread_id: "thread-3", status: "error" },
      ]),
  })
  supervisor.child = {}
  supervisor.port = 49152
  supervisor.token = "test-token"

  assert.deepEqual(await supervisor.threadActivity(), {
    "thread-1": "running",
    "thread-3": "error",
  })

  supervisor.fetch = async () => {
    throw new Error("connection refused")
  }
  assert.equal(await supervisor.threadActivity(), null)
})

test("packaged target runs the bundled backend", () => {
  const resourcesPath = path.resolve("/Applications/Open SWE.app/Contents/Resources")
  const target = packagedBackendTarget({ resourcesPath, port: 50000, platform: "darwin" })
  assert.equal(target.command, path.join(resourcesPath, "local-backend/runtime/bin/node"))
  assert.equal(target.args[0], path.join(resourcesPath, "local-backend/dist/server.js"))
  assert.equal(target.cwd, path.join(resourcesPath, "local-backend"))
  const stateDir = path.resolve("/tmp/open-swe-state")
  assert.equal(
    packagedBackendTarget({ resourcesPath, stateDir, port: 50000, platform: "darwin" }).cwd,
    stateDir
  )
  assert.deepEqual(
    localBackendTarget({ isPackaged: true, resourcesPath, port: 50000, platform: "darwin" }),
    target
  )
})
