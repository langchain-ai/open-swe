const test = require("node:test");
const assert = require("node:assert/strict");

const {
  classifyRecoverable,
  classifyStall,
  classifyThreadState,
  createNetworkResumeCoordinator,
  isOfflineErrorMessage,
} = require("../src/network-resume.cjs");

function harness(options = {}) {
  const timers = new Map();
  let nextTimer = 1;
  let clock = 1_000_000;
  const changes = [];
  const delays = [];
  const state = { reachable: options.reachable ?? false };
  const resumeCalls = [];
  const coordinator = createNetworkResumeCoordinator({
    enabled: true,
    probe: options.probe ?? (async () => state.reachable),
    resumeRun:
      options.resumeRun ??
      (async (input) => {
        resumeCalls.push(input);
        return { status: "dispatched", runId: `run-${input.generation + 1}` };
      }),
    logger: { info() {} },
    now: options.now ?? (() => clock),
    schedule: (callback, milliseconds) => {
      const id = nextTimer++;
      timers.set(id, callback);
      delays.push(milliseconds);
      return id;
    },
    cancel: (id) => timers.delete(id),
    waitingTimeoutMs: options.waitingTimeoutMs ?? 600_000,
    rejectedRetryMs: options.rejectedRetryMs ?? 60_000,
    inflightTimeoutMs: options.inflightTimeoutMs ?? 120_000,
  });
  coordinator.onChange((snapshot, change) => changes.push({ snapshot, change }));
  return {
    coordinator,
    state,
    resumeCalls,
    changes,
    timers,
    delays,
    advance(milliseconds) {
      clock += milliseconds;
    },
    async flush() {
      for (let index = 0; index < 3; index += 1) {
        await new Promise((resolve) => setImmediate(resolve));
      }
    },
    /** Run every scheduled monitor tick to completion. */
    async tick() {
      const pending = [...timers.values()];
      timers.clear();
      for (const callback of pending) await callback();
    },
    chain(threadId = "thread-1") {
      return coordinator.snapshot().chains.find((c) => c.threadId === threadId);
    },
  };
}

test("classifies recoverable failures and terminal ones", () => {
  assert.equal(classifyRecoverable({ online: false }), "network");
  assert.equal(
    classifyRecoverable({ online: true, message: "TypeError: fetch failed" }),
    "network",
  );
  assert.equal(
    classifyRecoverable({ message: "read ECONNRESET" }),
    "network",
  );
  assert.equal(classifyRecoverable({ probe: "timeout" }), "backend-down");
  assert.equal(classifyRecoverable({ probe: "unreachable" }), "backend-down");
  assert.equal(classifyRecoverable({ backendExited: true }), "backend-down");
  assert.equal(classifyRecoverable({ activeRun: false }), "run-lost");
  assert.equal(classifyRecoverable({ status: 401 }), null);
  assert.equal(classifyRecoverable({ status: 422 }), null);
  assert.equal(classifyRecoverable({ message: "bad request" }), null);
  assert.equal(isOfflineErrorMessage("connect ETIMEDOUT 1.2.3.4:443"), true);
  assert.equal(isOfflineErrorMessage("Prompt rejected"), false);
});

test("decides what a silent stream means from the backend's own answers", () => {
  // Unreachable: nothing on the renderer can tell why, so a chain takes over.
  assert.equal(
    classifyStall({ reachable: false, activity: null, threadId: "t" }),
    "arm",
  );
  // Reachable and running: a long tool call, not a broken link.
  assert.equal(
    classifyStall({
      reachable: true,
      activity: { t: "running" },
      threadId: "t",
      threadState: "missing",
    }),
    "none",
  );
  // Reachable, not running, and the thread state says the run finished while
  // the UI was disconnected: reconnect instead of dispatching a second run.
  assert.equal(
    classifyStall({
      reachable: true,
      activity: { t: "idle" },
      threadId: "t",
      threadState: "idle",
    }),
    "resync",
  );
  assert.equal(
    classifyStall({
      reachable: true,
      activity: {},
      threadId: "t",
      threadState: "idle",
    }),
    "resync",
  );
  assert.equal(
    classifyStall({
      reachable: true,
      activity: null,
      threadId: "t",
      threadState: "idle",
    }),
    "resync",
  );
  // The in-memory thread record died with the backend process: no run can
  // finish, so the recovery chain must take over.
  assert.equal(
    classifyStall({
      reachable: true,
      activity: {},
      threadId: "t",
      threadState: "missing",
    }),
    "arm",
  );
  // A pending super-step no live worker owns is the same kind of loss.
  assert.equal(
    classifyStall({
      reachable: true,
      activity: {},
      threadId: "t",
      threadState: "pending",
    }),
    "arm",
  );
  // An unreadable state is not proof that the run is gone.
  assert.equal(
    classifyStall({
      reachable: true,
      activity: {},
      threadId: "t",
      threadState: "unknown",
    }),
    "resync",
  );
});

test("classifies the thread state the stall decision reads", () => {
  assert.equal(classifyThreadState(404, null), "missing");
  assert.equal(classifyThreadState(200, { next: ["model"] }), "pending");
  assert.equal(classifyThreadState(200, { next: [] }), "idle");
  assert.equal(classifyThreadState(200, null), "idle");
  assert.equal(classifyThreadState(500, { next: [] }), "unknown");
  assert.equal(classifyThreadState(undefined, {}), "unknown");
});

test("reports a dispatch only once the continuation run is accepted", async () => {
  let release;
  const gate = new Promise((resolve) => (release = resolve));
  const h = harness({
    reachable: true,
    resumeRun: async () => {
      await gate;
      return { status: "dispatched", runId: "run-1" };
    },
  });
  h.coordinator.arm({ threadId: "thread-1", reason: "backend-down" });

  const tick = h.tick();
  await h.flush();

  // The renderer must not reconnect yet: the thread may not exist on the
  // restarted backend until the run POST has returned.
  assert.equal(h.chain().phase, "dispatching");
  assert.equal(h.chain().dispatched, false);

  release();
  await tick;

  assert.equal(h.chain().dispatched, true);
  assert.equal(h.chain().previousRunId, "run-1");
});

test("dispatches one continuation once the backend answers again", async () => {
  const h = harness();
  h.coordinator.arm({ threadId: "thread-1", reason: "backend-down" });

  await h.tick();
  assert.equal(h.chain().phase, "waiting");
  assert.equal(h.resumeCalls.length, 0);

  // A single failed probe is not a dead backend yet.
  h.state.reachable = true;
  await h.tick();
  assert.equal(h.resumeCalls.length, 1);
  assert.equal(h.chain().phase, "dispatching");
  assert.equal(h.chain().generation, 1);

  for (let index = 0; index < 3; index += 1) await h.tick();
  assert.equal(h.resumeCalls.length, 1, "a dispatched chain is not re-sent");
});

test("ignores a dispatch that lands after the chain was closed", async () => {
  let release;
  const gate = new Promise((resolve) => (release = resolve));
  const h = harness({
    reachable: true,
    resumeRun: async () => {
      await gate;
      return { status: "dispatched", runId: "run-2" };
    },
  });
  h.coordinator.arm({ threadId: "thread-1", reason: "network" });
  const pendingTick = h.tick();
  await h.flush();
  assert.equal(h.chain()?.phase, "dispatching");

  h.coordinator.disarm({ threadId: "thread-1" });
  release();
  await pendingTick;

  assert.equal(h.chain(), undefined);
  assert.equal(h.coordinator.snapshot().chains.length, 0);
});

test("a reconnect alone does not cancel the continuation", async () => {
  const h = harness({ reachable: true });
  h.coordinator.arm({ threadId: "thread-1", reason: "network" });
  // The SDK reconnects to the thread as soon as the backend answers, which is
  // not proof that the interrupted run survived.
  h.coordinator.notifyWorking({ threadId: "thread-1" });
  assert.equal(h.chain()?.phase, "waiting");

  await h.tick();
  assert.equal(h.resumeCalls.length, 1);
});

test("moving to inflight keeps the chain alive", async () => {
  const h = harness({ reachable: true });
  h.coordinator.arm({ threadId: "thread-1", reason: "run-lost" });
  await h.tick();
  h.coordinator.notifyWorking({ threadId: "thread-1" });

  assert.equal(h.chain().phase, "inflight");

  h.coordinator.complete({ threadId: "thread-1" });
  assert.equal(h.chain(), undefined);
  assert.equal(h.changes.at(-1).change.outcome, "completed");
});

test("an inflight chain the renderer never completed is released by its timeout", async () => {
  const h = harness({
    reachable: true,
    inflightTimeoutMs: 30_000,
  });
  h.coordinator.arm({ threadId: "thread-1", reason: "backend-down" });
  await h.tick();
  h.coordinator.notifyWorking({ threadId: "thread-1" });
  assert.equal(h.chain().phase, "inflight");

  // Still inside the window: the continuation is running and must not be
  // dropped just because no completion arrived yet.
  h.advance(20_000);
  await h.tick();
  assert.equal(h.chain()?.phase, "inflight");

  h.advance(15_000);
  await h.tick();
  assert.equal(h.chain(), undefined);
  assert.equal(h.changes.at(-1).change.outcome, "inflight-timeout");
  assert.equal(h.resumeCalls.length, 1, "no second continuation was sent");
});

test("the inflight timeout leaves other threads' chains alone", async () => {
  const h = harness({ reachable: true, inflightTimeoutMs: 10_000 });
  h.coordinator.arm({ threadId: "thread-1", reason: "backend-down" });
  h.coordinator.arm({ threadId: "thread-2", reason: "backend-down" });
  await h.tick();
  h.coordinator.notifyWorking({ threadId: "thread-1" });

  h.advance(15_000);
  await h.tick();

  assert.equal(h.chain("thread-1"), undefined);
  // Dispatching, not inflight: the inflight deadline is measured from the
  // renderer reporting the stream back, so another thread's chain is untouched.
  assert.equal(h.chain("thread-2")?.phase, "dispatching");
});

test("a fast failed probe retries sooner instead of waiting out the interval", async () => {
  const h = harness({ reachable: false });
  h.coordinator.arm({ threadId: "thread-1", reason: "backend-down" });
  await h.tick();

  // The probe returned immediately, so the next one is scheduled fast.
  assert.equal(h.delays.at(-1), 250);

  h.state.reachable = true;
  h.delays.length = 0;
  await h.tick();
  assert.equal(h.resumeCalls.length, 1);
  // The chain is inflight now, so the monitor only runs for the release
  // timeout: no probe gets scheduled and nothing is accelerated.
  assert.ok(!h.delays.includes(250));
});

test("a slow failed probe keeps the normal cadence", async () => {
  // Only the tick after a *fast* failure is accelerated: a probe that spent
  // its whole budget means a struggling backend is not hammered.
  const clock = { value: 1_000_000 };
  const h = harness({
    probe: async () => {
      clock.value += 10_000;
      return false;
    },
    now: () => clock.value,
  });
  h.coordinator.arm({ threadId: "thread-1", reason: "backend-down" });
  await h.tick();
  assert.equal(h.delays.at(-1), 2_000);
});

test("the probe budget outlasts a backend boot", async () => {
  // The probe restarts a dead backend, and the dev server takes seconds to
  // boot; a shorter budget reports "down" while its own restart is in flight.
  const bootMs = 3_000;
  const clock = { value: 1_000_000 };
  const h = harness({
    probe: async () => {
      clock.value += bootMs;
      return true;
    },
    now: () => clock.value,
  });
  h.coordinator.arm({ threadId: "thread-1", reason: "backend-down" });
  await h.tick();

  assert.equal(h.resumeCalls.length, 1, "the restarting backend counts as reachable");
  assert.equal(h.chain().phase, "dispatching");
});

test("releases a held turn once the wait window is spent", async () => {
  const h = harness({ waitingTimeoutMs: 5_000 });
  h.coordinator.arm({ threadId: "thread-1", reason: "backend-down" });
  h.advance(6_000);
  await h.tick();

  assert.equal(h.resumeCalls.length, 0);
  assert.equal(h.chain(), undefined);
  assert.equal(h.changes.at(-1).change.outcome, "timed-out");
});

test("a rejected dispatch goes back to waiting and retries later", async () => {
  let attempts = 0;
  const h = harness({
    reachable: true,
    resumeRun: async () => {
      attempts += 1;
      if (attempts === 1) throw new Error("Network resume run was rejected (409)");
      return { status: "dispatched", runId: "run-2" };
    },
  });
  h.coordinator.arm({ threadId: "thread-1", reason: "network" });
  await h.tick();
  assert.equal(h.chain().phase, "waiting");

  await h.tick();
  assert.equal(attempts, 1, "no immediate retry within the backoff window");

  h.advance(60_000);
  await h.tick();
  assert.equal(attempts, 2);
  assert.equal(h.chain().phase, "dispatching");
});

test("a chain whose run survived is dropped instead of duplicated", async () => {
  const h = harness({
    reachable: true,
    resumeRun: async () => ({ status: "already-running" }),
  });
  h.coordinator.arm({ threadId: "thread-1", reason: "network" });
  await h.tick();

  assert.equal(h.chain(), undefined);
  assert.equal(h.changes.at(-1).change.outcome, "taken-over");
});

test("arming twice keeps one chain and reports it in the snapshot", async () => {
  const h = harness();
  h.coordinator.arm({ threadId: "thread-1", reason: "network", runId: "run-1" });
  h.coordinator.arm({ threadId: "thread-1", reason: "backend-down", runId: "run-1" });

  const snapshot = h.coordinator.snapshot();
  assert.equal(snapshot.enabled, true);
  assert.equal(snapshot.chains.length, 1);
  assert.deepEqual(
    {
      reason: snapshot.chains[0].reason,
      rootRunId: snapshot.chains[0].rootRunId,
      phase: snapshot.chains[0].phase,
    },
    { reason: "backend-down", rootRunId: "run-1", phase: "waiting" },
  );
  assert.equal(typeof snapshot.epoch, "string");
});

test("a disabled coordinator never arms a chain", () => {
  const h = harness();
  h.coordinator.setEnabled(false);
  assert.equal(h.coordinator.arm({ threadId: "thread-1" }), null);
  assert.equal(h.coordinator.snapshot().chains.length, 0);
});
