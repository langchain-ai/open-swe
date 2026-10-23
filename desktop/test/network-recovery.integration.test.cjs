/**
 * The main-process recovery path over real HTTP: supervisor probe → coordinator
 * chain → continuation run. Only Electron and the renderer are left out.
 */
const test = require("node:test");
const assert = require("node:assert/strict");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");
const { EventEmitter } = require("node:events");

const { BackendSupervisor } = require("../src/backend-supervisor.cjs");
const { createNetworkResumeCoordinator } = require("../src/network-resume.cjs");
const { resumeNetworkRun } = require("../src/network-resume-run.cjs");

const THREAD = {
  id: "thread-1",
  cwd: "/repo",
  worktreePath: null,
  modelId: null,
  effort: null,
};
const PROMPT = "Continue the task that was already underway.";

function backendStub() {
  const state = { healthy: false, runs: [], searches: 0, threadState: { next: [] } };
  const server = http.createServer((request, response) => {
    const json = (status, body) => {
      response.writeHead(status, { "content-type": "application/json" });
      response.end(JSON.stringify(body));
    };
    if (request.url === "/") {
      request.resume();
      return json(200, { status: "ok" });
    }
    if (request.url === "/ok") {
      request.resume();
      if (!state.healthy) return json(503, { status: "starting" });
      return json(200, { status: "ok" });
    }
    if (request.url === "/threads/search") {
      state.searches += 1;
      request.resume();
      return json(200, []);
    }
    if (request.url === "/threads/thread-1/state") {
      request.resume();
      return json(200, state.threadState);
    }
    if (request.url === "/runs/cancel?action=interrupt") {
      request.resume();
      response.writeHead(204);
      return response.end();
    }
    if (request.url === "/threads/thread-1/runs") {
      let body = "";
      request.setEncoding("utf8");
      request.on("data", (chunk) => (body += chunk));
      request.on("end", () => {
        state.runs.push(JSON.parse(body));
        json(200, { run_id: `run-${state.runs.length + 1}` });
      });
      return undefined;
    }
    return json(404, { detail: "not found" });
  });
  return { server, state };
}

test("a backend coming back dispatches exactly one continuation", async () => {
  const { server, state } = backendStub();
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const port = server.address().port;
  const supervisor = new BackendSupervisor({});
  supervisor.child = {};
  supervisor.port = port;
  supervisor.token = "token";
  supervisor.ready = Promise.resolve({});
  const coordinator = createNetworkResumeCoordinator({
    enabled: true,
    // Generous timeouts: the whole suite runs in parallel with this file.
    probe: async () => (await supervisor.probe(5_000)).reachable,
    probeTimeoutMs: 10_000,
    resumeRun: (input) =>
      resumeNetworkRun({
        supervisor,
        thread: THREAD,
        readPrompt: () => PROMPT,
        resume: input,
      }),
    logger: { info() {} },
    probeIntervalMs: 5,
  });

  try {
    coordinator.arm({ threadId: "thread-1", reason: "backend-down" });

    // The backend is still booting: the chain waits and sends nothing. One
    // failed probe under load is not a dead backend, two are.
    await coordinator.poke();
    assert.equal(coordinator.snapshot().chains[0]?.phase, "waiting");
    assert.equal(coordinator.snapshot().network.reachable, null);
    await coordinator.poke();
    assert.equal(coordinator.snapshot().network.reachable, false);
    assert.equal(state.runs.length, 0);

    state.healthy = true;
    await coordinator.poke();
    await coordinator.poke();

    assert.equal(coordinator.snapshot().chains[0]?.phase, "dispatching");
    assert.equal(state.runs.length, 1, "one continuation, not two");
    const [payload] = state.runs;
    assert.equal(payload.assistant_id, "agent");
    assert.match(
      payload.input.messages[0].content,
      /<input-message sender="system:network-resume" surface="desktop" kind="system">/
    );
    assert.match(payload.input.messages[0].content, new RegExp(PROMPT));
    assert.deepEqual(payload.metadata.open_swe.network_recovery, {
      rootRunId: coordinator.snapshot().chains[0].rootRunId,
      previousRunId: null,
      generation: 1,
      reason: "backend-down",
      isMeta: true,
    })
    // The chain remembers the run it just created for the next merge.
    assert.equal(coordinator.snapshot().chains[0].previousRunId, "run-2");
    assert.equal(payload.config.configurable.source, "desktop");
  } finally {
    coordinator.disarm({ threadId: "thread-1" });
    server.closeAllConnections?.();
    await new Promise((resolve) => server.close(resolve));
  }
});

test("a backend process exit arms the chain and dispatches one continuation", async () => {
  const { server, state } = backendStub();
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const port = server.address().port;
  state.healthy = true;
  const children = [];
  const supervisor = new BackendSupervisor({
    repoRoot: path.resolve("/work/open-swe"),
    projectsFile: path.join(os.tmpdir(), "projects.json"),
    worktreesDir: path.join(os.tmpdir(), "open-swe-test-worktrees"),
    reservePort: async () => port,
    spawn: () => {
      const child = Object.assign(new EventEmitter(), {
        stdout: null,
        stderr: null,
        exitCode: null,
        signalCode: null,
        kill() {},
      });
      children.push(child);
      return child;
    },
    // Mirrors main: the child's exit arms the chain immediately, instead of
    // waiting for the renderer's stream to exhaust the SDK retry budget.
    onExit: () =>
      coordinator.arm({ threadId: "thread-1", reason: "backend-down" }),
  });
  const coordinator = createNetworkResumeCoordinator({
    enabled: true,
    probe: async () => {
      await supervisor.start();
      return (await supervisor.probe(5_000)).reachable;
    },
    probeTimeoutMs: 20_000,
    resumeRun: (input) =>
      resumeNetworkRun({
        supervisor,
        thread: THREAD,
        readPrompt: () => PROMPT,
        resume: input,
      }),
    logger: { info() {} },
    probeIntervalMs: 5,
  });

  try {
    await supervisor.start();
    assert.equal(children.length, 1);

    children[0].exitCode = 1;
    children[0].emit("exit", 1, null);

    assert.equal(coordinator.snapshot().chains[0]?.phase, "waiting");
    assert.equal(coordinator.snapshot().chains[0]?.reason, "backend-down");

    await coordinator.poke();
    await coordinator.poke();

    assert.equal(state.runs.length, 1, "one continuation, not two");
    assert.equal(coordinator.snapshot().chains[0]?.phase, "dispatching");
    assert.equal(state.runs[0].metadata.open_swe.network_recovery.reason, "backend-down");
  } finally {
    coordinator.disarm({ threadId: "thread-1" });
    server.closeAllConnections?.();
    await new Promise((resolve) => server.close(resolve));
  }
});
