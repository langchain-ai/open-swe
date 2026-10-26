const test = require("node:test");
const assert = require("node:assert/strict");
const os = require("node:os");
const path = require("node:path");

const {
  BackendSupervisor,
  devBackendTarget,
  localBackendTarget,
  modelCredentialStatus,
  packagedBackendTarget,
} = require("../src/backend-supervisor.cjs");

test("development target isolates repository LangGraph state", () => {
  const repoRoot = path.resolve("/work/open-swe");
  const stateDir = path.resolve("/tmp/open-swe-state");
  const target = devBackendTarget({ repoRoot, stateDir, port: 49152, env: {} });
  assert.deepEqual(target, {
    command: "uv",
    args: [
      "run",
      "--project",
      repoRoot,
      "langgraph",
      "dev",
      "--no-browser",
      "--no-reload",
      "--host",
      "127.0.0.1",
      "--port",
      "49152",
      "--n-jobs-per-worker",
      "10",
      "--allow-blocking",
      "--config",
      path.join(repoRoot, "langgraph.desktop.json"),
    ],
    cwd: stateDir,
  });
});

test("development target accepts an explicit LangGraph config", () => {
  const repoRoot = path.resolve("/work/open-swe");
  const config = path.resolve("/work/e2e/langgraph.json");
  const target = devBackendTarget({
    repoRoot,
    port: 49152,
    env: { OPEN_SWE_LOCAL_BACKEND_CONFIG: config },
  });

  assert.equal(target.args.at(-1), config);
});

test("reports whether the selected provider is configured", () => {
  assert.deepEqual(modelCredentialStatus("openai:gpt-test", {}), {
    available: false,
    variable: "OPENAI_API_KEY",
    canSignIn: true,
  });
  assert.deepEqual(
    modelCredentialStatus("openai:gpt-test", { OPENAI_API_KEY: "secret" }),
    {
      available: true,
      variable: "OPENAI_API_KEY",
    },
  );
  assert.deepEqual(
    modelCredentialStatus("openai:gpt-test", {}, { openAiOAuth: true }),
    {
      available: true,
      variable: null,
      canSignIn: true,
    },
  );
  assert.deepEqual(
    modelCredentialStatus("google_genai:test", { GEMINI_API_KEY: "secret" }),
    {
      available: true,
      variable: "GEMINI_API_KEY",
    },
  );
  assert.deepEqual(modelCredentialStatus("custom:test", {}), {
    available: true,
    variable: null,
  });
  assert.deepEqual(
    modelCredentialStatus("anthropic:test", {
      LANGSMITH_GATEWAY_API_KEY: "gateway-key",
      LANGSMITH_GATEWAY_ENABLED: "true",
    }),
    { available: true, variable: null },
  );
});

test("caches the managed macOS gateway key", () => {
  let probes = 0;
  const supervisor = new BackendSupervisor({
    env: {},
    gatewayEnvironment: {
      platform: "darwin",
      execFileSync: () => {
        probes += 1;
        return "managed-key\n";
      },
    },
  });

  assert.deepEqual(supervisor.credentialStatus("anthropic:test"), {
    available: true,
    variable: null,
  });
  supervisor.credentialStatus("openai:test");
  assert.equal(probes, 1);
});

test("concurrent starts share one backend launch", async () => {
  const { EventEmitter } = require("node:events");
  const os = require("node:os");
  let spawned = 0;
  let releaseTracing;
  const tracingGate = new Promise((resolve) => (releaseTracing = resolve));
  const supervisor = new BackendSupervisor({
    repoRoot: path.resolve("/work/open-swe"),
    projectsFile: path.join(os.tmpdir(), "projects.json"),
    worktreesDir: path.join(os.tmpdir(), "open-swe-test-worktrees"),
    reservePort: async () => 49152,
    tracingEnv: () => tracingGate,
    spawn: () => {
      spawned += 1;
      return Object.assign(new EventEmitter(), {
        stdout: null,
        stderr: null,
        exitCode: null,
        signalCode: null,
        kill() {},
      });
    },
    fetch: async () => new Response(null, { status: 200 }),
  });

  const first = supervisor.start();
  const second = supervisor.start();
  releaseTracing({});
  await Promise.all([first, second]);

  assert.equal(spawned, 1);
  assert.equal(second, first);
});

test("the startup health check uses a short per-attempt budget", async () => {
  const { EventEmitter } = require("node:events");
  const os = require("node:os");
  const budgets = [];
  const originalTimeout = AbortSignal.timeout;
  // Capture the budget the supervisor asks for instead of waiting it out.
  AbortSignal.timeout = (milliseconds) => {
    budgets.push(milliseconds);
    return originalTimeout.call(AbortSignal, 5);
  };
  const supervisor = new BackendSupervisor({
    repoRoot: path.resolve("/work/open-swe"),
    projectsFile: path.join(os.tmpdir(), "projects.json"),
    worktreesDir: path.join(os.tmpdir(), "open-swe-test-worktrees"),
    reservePort: async () => 49152,
    spawn: () =>
      Object.assign(new EventEmitter(), {
        stdout: null,
        stderr: null,
        exitCode: null,
        signalCode: null,
        kill() {},
      }),
    // The child is not listening on the first attempt, so the caller must not
    // be held for a full second while it boots.
    fetch: async () => new Response(null, { status: 200 }),
  });

  try {
    await supervisor.start();
  } finally {
    AbortSignal.timeout = originalTimeout;
  }
  assert.ok(budgets.length > 0);
  assert.ok(
    budgets[0] <= 500,
    `startup health check waited ${budgets[0]}ms before giving up on a booting child`
  );
});

test("boot interrupts runs a previous backend left marked as running", async () => {
  const { EventEmitter } = require("node:events");
  const os = require("node:os");
  const requests = [];
  const supervisor = new BackendSupervisor({
    repoRoot: path.resolve("/work/open-swe"),
    projectsFile: path.join(os.tmpdir(), "projects.json"),
    worktreesDir: path.join(os.tmpdir(), "open-swe-test-worktrees"),
    reservePort: async () => 49152,
    spawn: () =>
      Object.assign(new EventEmitter(), {
        stdout: null,
        stderr: null,
        exitCode: null,
        signalCode: null,
        kill() {},
      }),
    fetch: async (url, init) => {
      requests.push({ url: String(url), init });
      return new Response(null, {
        status: String(url).includes("/runs/cancel") ? 204 : 200,
      });
    },
  });

  await supervisor.start();

  const cancel = requests.find((request) =>
    request.url.includes("/runs/cancel")
  );
  assert.ok(cancel, "boot must reconcile orphaned runs");
  assert.equal(cancel.init.method, "POST");
  assert.match(cancel.url, /\/runs\/cancel\?action=interrupt$/);
  assert.deepEqual(JSON.parse(cancel.init.body), { status: "all" });
  assert.equal(
    new Headers(cancel.init.headers).get("authorization"),
    `Bearer ${supervisor.token}`
  );
});

test("creates the local LangGraph thread before stream hydration", async () => {
  const supervisor = new BackendSupervisor({});
  let request;
  supervisor.request = async (pathname, init) => {
    request = { pathname, init };
    return new Response(null, { status: 200 });
  };

  await supervisor.createThread("thread-1");

  assert.equal(request.pathname, "/threads");
  assert.equal(request.init.method, "POST");
  assert.equal(
    new Headers(request.init.headers).get("content-type"),
    "application/json",
  );
  assert.deepEqual(JSON.parse(request.init.body), {
    thread_id: "thread-1",
    if_exists: "do_nothing",
    metadata: { graph_id: "agent" },
  });
});

test("rejects a failed local LangGraph thread creation", async () => {
  const supervisor = new BackendSupervisor({});
  supervisor.request = async () => new Response(null, { status: 503 });

  await assert.rejects(
    supervisor.createThread("thread-1"),
    /Could not create local LangGraph thread \(503\)/,
  );
});

test("derives thread activity from the backend without starting it", async () => {
  const idle = new BackendSupervisor({
    fetch: () => assert.fail("must not reach a backend that is not running"),
  });
  assert.deepEqual(await idle.threadActivity(), {});

  const supervisor = new BackendSupervisor({
    fetch: async () =>
      Response.json([
        { thread_id: "thread-1", status: "busy" },
        { thread_id: "thread-2", status: "idle" },
        { thread_id: "thread-3", status: "error" },
      ]),
  });
  supervisor.child = {};
  supervisor.port = 49152;
  supervisor.token = "token";

  assert.deepEqual(await supervisor.threadActivity(), {
    "thread-1": "running",
    "thread-3": "error",
  });

  supervisor.fetch = async () => {
    throw new Error("connection refused");
  };
  assert.equal(await supervisor.threadActivity(), null);
});

test("probes a running backend without starting one", async () => {
  const idle = new BackendSupervisor({
    fetch: () => assert.fail("must not reach a backend that is not running"),
  });
  assert.equal((await idle.probe()).reachable, false);

  const supervisor = new BackendSupervisor({
    fetch: async () => new Response(null, { status: 200 }),
  });
  supervisor.child = {};
  supervisor.port = 49152;
  supervisor.token = "token";
  const probed = await supervisor.probe();
  assert.equal(probed.reachable, true);
  assert.equal(probed.status, 200);
  assert.equal(typeof probed.checkedAt, "number");

  supervisor.fetch = async () => {
    throw new Error("connection refused");
  };
  assert.equal((await supervisor.probe()).reachable, false);
});

test("a lost backend fails the streaming proxy responses it opened", async () => {
  const http = require("node:http");
  const server = http.createServer((request, response) => {
    response.writeHead(200, { "content-type": "text/event-stream" });
    response.write("data: {}\n\n");
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const port = server.address().port;
  const supervisor = new BackendSupervisor({});
  supervisor.child = {};
  supervisor.port = port;
  supervisor.token = "token";
  supervisor.ready = Promise.resolve({});

  try {
    const response = await supervisor.proxy(
      new Request("http://app/local-graph/threads/one/stream/events", {
        method: "POST",
        body: "{}",
      }),
    );
    const reader = response.body.getReader();
    const first = await reader.read();
    assert.equal(first.done, false);
    assert.equal(supervisor.inflight.size, 1);

    supervisor.abortInflight();

    await assert.rejects(() => reader.read());
    assert.equal(supervisor.inflight.size, 0);
  } finally {
    server.closeAllConnections?.();
    await new Promise((resolve) => server.close(resolve));
  }
});

test("a child exit reports the lost backend and aborts in-flight responses", async () => {
  const { EventEmitter } = require("node:events");
  const child = Object.assign(new EventEmitter(), {
    stdout: null,
    stderr: null,
    exitCode: null,
    signalCode: null,
    kill() {},
  });
  const exits = [];
  const supervisor = new BackendSupervisor({
    repoRoot: path.resolve("/work/open-swe"),
    projectsFile: path.join(os.tmpdir(), "projects.json"),
    worktreesDir: path.join(os.tmpdir(), "open-swe-test-worktrees"),
    reservePort: async () => 49152,
    spawn: () => child,
    fetch: async () => new Response(null, { status: 200 }),
    onExit: (error) => exits.push(error),
  });
  await supervisor.start();
  const controller = new AbortController();
  supervisor.inflight.add(controller);

  child.exitCode = 1;
  child.emit("exit", 1, null);

  assert.equal(exits.length, 1);
  assert.match(exits[0].message, /exit code 1/);
  assert.equal(controller.signal.aborted, true);
  assert.equal(supervisor.inflight.size, 0);
});

test("packaged target runs the bundled backend", () => {
  const resourcesPath = path.resolve(
    "/Applications/Open SWE.app/Contents/Resources",
  );
  const target = packagedBackendTarget({
    resourcesPath,
    port: 50000,
    platform: "darwin",
  });
  assert.equal(
    target.command,
    path.join(resourcesPath, "local-backend/runtime/bin/python3"),
  );
  assert.deepEqual(target.args.slice(0, 3), ["-m", "langgraph_cli", "dev"]);
  assert.equal(target.cwd, path.join(resourcesPath, "local-backend"));
  const stateDir = path.resolve("/tmp/open-swe-state");
  assert.equal(
    packagedBackendTarget({
      resourcesPath,
      stateDir,
      port: 50000,
      platform: "darwin",
    }).cwd,
    stateDir,
  );
  assert.deepEqual(
    localBackendTarget({
      isPackaged: true,
      resourcesPath,
      port: 50000,
      platform: "darwin",
    }),
    target,
  );
});
