const test = require("node:test");
const assert = require("node:assert/strict");

const {
  localThreadConfigurable,
  networkResumeMessage,
  resumeNetworkRun,
} = require("../src/network-resume-run.cjs");

const THREAD = {
  id: "thread-1",
  cwd: "/repo",
  worktreePath: "/repo/.worktrees/thread-1",
  modelId: "anthropic:claude",
  effort: "high",
};

function supervisor({ activity = {}, response } = {}) {
  const calls = [];
  return {
    calls,
    threadActivity: async () => activity,
    request: async (path, options) => {
      calls.push({ path, options });
      if (typeof response === "function") return response(path, options);
      return (
        response ??
        new Response(JSON.stringify({ run_id: "run-2" }), { status: 200 })
      );
    },
  };
}

const RESUME = {
  threadId: "thread-1",
  reason: "backend-down",
  rootRunId: "run-1",
  previousRunId: null,
  generation: 1,
};

test("the continuation is a system message the transcript hides", () => {
  const message = networkResumeMessage("continue the task", "thread-1", 2);

  assert.equal(message.role, "user");
  assert.equal(message.id, "network-resume:thread-1:2");
  assert.match(
    message.content,
    /^<input-message sender="system:network-resume" surface="desktop" kind="system">\n<content>/
  );
  assert.match(message.content, /<content>continue the task<\/content>/);
});

test("prompt text cannot break out of the envelope", () => {
  const message = networkResumeMessage(
    "check <b>state</b> & a[i] > 0",
    "thread-1",
    1
  );

  assert.match(message.content, /check &lt;b&gt;state&lt;\/b&gt; &amp; a\[i\] &gt; 0/);
  assert.equal(message.content.match(/<\/input-message>/g).length, 1);
});

test("the run config mirrors the renderer's model selection", () => {
  assert.deepEqual(localThreadConfigurable(THREAD), {
    source: "desktop",
    local_project_path: "/repo/.worktrees/thread-1",
    agent_model_id: "anthropic:claude",
    agent_effort: "high",
    model_selection: "explicit",
  });
  assert.deepEqual(
    localThreadConfigurable({ cwd: "/repo", worktreePath: null }),
    { source: "desktop", local_project_path: "/repo", model_selection: "auto" }
  );
});

test("dispatches one continuation carrying the recovery lineage", async () => {
  const fake = supervisor();
  let promptReads = 0;
  const result = await resumeNetworkRun({
    supervisor: fake,
    thread: THREAD,
    readPrompt: () => {
      promptReads += 1;
      return "continue the task";
    },
    resume: RESUME,
  });

  assert.deepEqual(result, { status: "dispatched", runId: "run-2" });
  assert.equal(promptReads, 1);
  assert.equal(fake.calls.length, 1);
  const [call] = fake.calls;
  assert.equal(call.path, "/threads/thread-1/runs");
  assert.equal(call.options.method, "POST");
  const body = JSON.parse(call.options.body);
  assert.equal(body.assistant_id, "agent");
  // A dead run's pending tasks would otherwise queue the continuation forever.
  assert.equal(body.multitask_strategy, "interrupt");
  assert.equal(body.input.messages.length, 1);
  assert.deepEqual(body.metadata.open_swe.network_recovery, {
    rootRunId: "run-1",
    previousRunId: null,
    generation: 1,
    reason: "backend-down",
    isMeta: true,
  });
  assert.equal(body.config.configurable.local_project_path, THREAD.worktreePath);
});

test("a surviving run is reported instead of continued", async () => {
  const fake = supervisor({ activity: { "thread-1": "running" } });
  let promptReads = 0;
  const result = await resumeNetworkRun({
    supervisor: fake,
    thread: THREAD,
    readPrompt: () => {
      promptReads += 1;
      return "never used";
    },
    resume: RESUME,
  });

  assert.deepEqual(result, { status: "already-running" });
  assert.equal(fake.calls.length, 0);
  assert.equal(promptReads, 0);
});

test("a crash that took the thread record re-creates it and retries once", async () => {
  const paths = [];
  let runs = 0;
  const fake = supervisor({
    response: (pathname) => {
      paths.push(pathname);
      if (pathname === "/threads") return new Response("{}", { status: 200 });
      runs += 1;
      return runs === 1
        ? new Response("not found", { status: 404 })
        : new Response(JSON.stringify({ run_id: "run-2" }), { status: 200 });
    },
  });

  const result = await resumeNetworkRun({
    supervisor: fake,
    thread: THREAD,
    readPrompt: () => "continue",
    resume: RESUME,
  });

  assert.deepEqual(result, { status: "dispatched", runId: "run-2" });
  assert.deepEqual(paths, [
    "/threads/thread-1/runs",
    "/threads",
    "/threads/thread-1/runs",
  ]);
  assert.deepEqual(JSON.parse(fake.calls[1].options.body), {
    thread_id: "thread-1",
  });
});

test("a rejected run throws so the chain can back off", async () => {
  const fake = supervisor({ response: new Response("nope", { status: 409 }) });

  await assert.rejects(
    () =>
      resumeNetworkRun({
        supervisor: fake,
        thread: THREAD,
        readPrompt: () => "continue",
        resume: RESUME,
      }),
    /rejected \(409\)/
  );
});

test("a run without an id still counts as dispatched", async () => {
  const fake = supervisor({
    response: new Response("{}", { status: 200 }),
  });

  const result = await resumeNetworkRun({
    supervisor: fake,
    thread: THREAD,
    readPrompt: () => "continue",
    resume: RESUME,
  });

  assert.deepEqual(result, { status: "dispatched", runId: null });
});
