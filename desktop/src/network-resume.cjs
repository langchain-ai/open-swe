/**
 * Network-resume coordinator for local (desktop) agent runs.
 *
 * The renderer cannot reconnect a stream whose backend died, so this owns the
 * recovery decision instead: it classifies a failure as recoverable, holds the
 * turn in a chain, polls the local backend for reachability, and dispatches one
 * hidden continuation run once the backend answers again. Every chain carries a
 * send ticket and a generation so a stale probe or a duplicate tick can never
 * dispatch twice.
 */

const DEFAULT_PROBE_INTERVAL_MS = 2_000;
const DEFAULT_PROBE_TIMEOUT_MS = 1_000;
const DEFAULT_WAITING_TIMEOUT_MS = 10 * 60 * 1_000;
const DEFAULT_REJECTED_RETRY_MS = 60_000;
/**
 * How long a chain may stay `inflight` after its continuation was accepted.
 * The normal release is the renderer's `complete()` on `onCompleted`; this
 * bounds the chain when that call never arrives (the renderer could not
 * resolve the thread id), so the thread is never left with a stale chain that
 * blocks the next arm and keeps the queue held.
 */
const DEFAULT_INFLIGHT_TIMEOUT_MS = 2 * 60 * 1_000;
/** A single slow /ok response under load must not look like a dead backend. */
const PROBE_FAILURES_BEFORE_DOWN = 2;

const OFFLINE_MESSAGE_PATTERNS = [
  /fetch failed/i,
  /failed to fetch/i,
  /networkerror/i,
  /network error/i,
  /ENOTFOUND/,
  /ECONNRESET/,
  /ECONNREFUSED/,
  /ETIMEDOUT/,
  /EAI_AGAIN/,
];

function isOfflineErrorMessage(message) {
  return (
    typeof message === "string" &&
    OFFLINE_MESSAGE_PATTERNS.some((pattern) => pattern.test(message))
  );
}

/**
 * Map an observation onto the recovery vocabulary, or `null` for a terminal
 * failure that must surface to the user instead of being retried.
 */
function classifyRecoverable(observation = {}) {
  const status = observation.status;
  if (typeof status === "number" && status >= 400 && status < 500) return null;
  if (observation.online === false) return "network";
  if (isOfflineErrorMessage(observation.message)) return "network";
  if (observation.backendExited === true) return "backend-down";
  if (observation.probe === "unreachable" || observation.probe === "timeout") {
    return "backend-down";
  }
  if (observation.activeRun === false) return "run-lost";
  return null;
}

/**
 * What to do about a local stream that stopped producing events while its run
 * is still loading. From the renderer a frozen backend and a long tool call look
 * identical, so the backend itself decides: unreachable → arm a chain, reachable
 * and still running → leave it alone, reachable but idle → the stream is stale,
 * reconnect it rather than dispatch a second run.
 *
 * `threadState` separates the two ways a reachable backend can report "not
 * running". A completed run is safe to re-hydrate. A missing thread record or a
 * non-empty `next` is a run whose process died: no worker will ever finish that
 * step, so merely reconnecting the stream can only hydrate a dead end.
 */
function classifyStall({
  reachable,
  activity,
  threadId,
  threadState = "unknown",
}) {
  if (!reachable) return "arm";
  if (activity && activity[threadId] === "running") return "none";
  if (threadState === "missing" || threadState === "pending") return "arm";
  return "resync";
}

/**
 * Classify `GET /threads/<id>/state` for the stall decision. A 404 means the
 * in-memory thread record died with the process; a non-empty `next` is a
 * pending super-step no live worker owns.
 */
function classifyThreadState(status, body) {
  if (status === 404) return "missing";
  if (typeof status !== "number" || status < 200 || status >= 300)
    return "unknown";
  return Array.isArray(body?.next) && body.next.length > 0 ? "pending" : "idle";
}

function snapshotChain(chain) {
  return {
    threadId: chain.threadId,
    phase: chain.phase,
    reason: chain.reason,
    rootRunId: chain.rootRunId,
    previousRunId: chain.previousRunId,
    generation: chain.generation,
    sendTicket: chain.sendTicket,
    closing: chain.closing,
    firstWaitingAt: chain.firstWaitingAt,
    // The run POST has returned. The renderer must not remount its stream
    // before this: on a backend that lost the thread, an early remount
    // hydrates a 404 and parks the stream against a thread that does not
    // exist yet.
    dispatched: chain.dispatched,
  };
}

function withTimeout(promise, milliseconds) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(
      () => reject(new Error("Probe timed out")),
      milliseconds,
    );
    timer.unref?.();
    promise.then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      (error) => {
        clearTimeout(timer);
        reject(error);
      },
    );
  });
}

function createNetworkResumeCoordinator(options) {
  const {
    probe,
    resumeRun,
    logger = console,
    now = () => Date.now(),
    probeIntervalMs = DEFAULT_PROBE_INTERVAL_MS,
    probeTimeoutMs = DEFAULT_PROBE_TIMEOUT_MS,
    waitingTimeoutMs = DEFAULT_WAITING_TIMEOUT_MS,
    rejectedRetryMs = DEFAULT_REJECTED_RETRY_MS,
    inflightTimeoutMs = DEFAULT_INFLIGHT_TIMEOUT_MS,
    schedule = (callback, milliseconds) => {
      const timer = setTimeout(callback, milliseconds);
      timer.unref?.();
      return timer;
    },
    cancel = (timer) => clearTimeout(timer),
    enabled: initialEnabled = false,
  } = options;
  if (typeof probe !== "function") {
    throw new Error("Network resume coordinator needs a probe");
  }
  if (typeof resumeRun !== "function") {
    throw new Error("Network resume coordinator needs a resumeRun");
  }

  const chains = new Map();
  const tombstones = new Set();
  const listeners = new Set();
  const network = { reachable: null, checkedAt: 0 };
  const epoch = `nr-${now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`;
  let enabled = initialEnabled === true;
  let revision = 0;
  let failures = 0;
  let timer = null;
  let running = false;

  function log(message, extra) {
    logger.info(message, extra);
  }

  function snapshot() {
    return {
      epoch,
      revision,
      enabled,
      network: { ...network },
      chains: [...chains.values()]
        .sort((left, right) => left.firstWaitingAt - right.firstWaitingAt)
        .map(snapshotChain),
    };
  }

  function emit(change) {
    revision += 1;
    const value = snapshot();
    for (const listener of listeners) {
      try {
        listener(value, change);
      } catch (error) {
        log("network resume listener failed", { error: String(error) });
      }
    }
  }

  function onChange(listener) {
    listeners.add(listener);
    return () => listeners.delete(listener);
  }

  function hasWaiting() {
    for (const chain of chains.values()) {
      if (chain.phase === "waiting") return true;
    }
    return false;
  }

  /** An inflight chain still needs ticks so `expireInflight` can bound it. */
  function hasInflight() {
    for (const chain of chains.values()) {
      if (chain.phase === "inflight") return true;
    }
    return false;
  }

  function stopMonitor() {
    if (timer === null) return;
    cancel(timer);
    timer = null;
  }

  function ensureMonitor() {
    if (!hasWaiting() && !hasInflight()) {
      stopMonitor();
      return;
    }
    if (timer !== null || running) return;
    timer = schedule(tick, probeIntervalMs);
  }

  async function tick() {
    timer = null;
    if (running) {
      ensureMonitor();
      return;
    }
    running = true;
    try {
      // Probing only matters for chains that have yet to dispatch; an inflight
      // chain just needs its timeout checked.
      if (hasWaiting()) {
        await sampleNetwork();
        await dispatchWaiting();
        expireWaiting();
      }
      expireInflight();
    } catch (error) {
      log("network resume tick failed", { error: String(error) });
    } finally {
      running = false;
      ensureMonitor();
    }
  }

  /** Probe immediately instead of waiting for the next interval. */
  function poke() {
    if (running) return Promise.resolve();
    stopMonitor();
    return tick();
  }

  async function sampleNetwork() {
    const startedAt = now();
    let reachable = false;
    try {
      reachable = (await withTimeout(Promise.resolve(probe()), probeTimeoutMs)) === true;
    } catch {
      reachable = false;
    }
    failures = reachable ? 0 : failures + 1;
    network.reachable =
      failures === 0
        ? true
        : failures >= PROBE_FAILURES_BEFORE_DOWN
          ? false
          : network.reachable;
    network.checkedAt = now();
    log("network resume probe", {
      reachable,
      checkedAt: network.checkedAt,
      elapsedMs: network.checkedAt - startedAt,
      failures,
    });
    emit({ kind: "network" });
  }

  function expireWaiting() {
    const cutoff = now();
    for (const chain of [...chains.values()]) {
      if (
        chain.phase === "waiting" &&
        cutoff - chain.firstWaitingAt >= waitingTimeoutMs
      ) {
        remove(chain, "timed-out");
      }
    }
  }

  /**
   * The renderer releases an `inflight` chain on `onCompleted`. If that call
   * never arrives the chain would sit here forever, blocking the next arm for
   * this thread and keeping the queue held, so bound it. The continuation run
   * itself keeps running; only the coordinator's bookkeeping is released.
   */
  function expireInflight() {
    const cutoff = now();
    for (const chain of [...chains.values()]) {
      if (
        chain.phase === "inflight" &&
        chain.dispatchedAt !== null &&
        cutoff - chain.dispatchedAt >= inflightTimeoutMs
      ) {
        remove(chain, "inflight-timeout");
      }
    }
  }

  function isStale(chain, ticket) {
    return chain.sendTicket !== ticket || chains.get(chain.threadId) !== chain;
  }

  async function dispatch(chain) {
    const ticket = ++chain.sendTicket;
    chain.generation += 1;
    chain.phase = "dispatching";
    emit({ kind: "chain-updated", threadId: chain.threadId });
    log("network resume dispatch", {
      threadId: chain.threadId,
      sendTicket: ticket,
      generation: chain.generation,
      outcome: "pending",
    });
    let result;
    const dispatchStartedAt = now();
    try {
      result = await resumeRun({
        threadId: chain.threadId,
        reason: chain.reason,
        rootRunId: chain.rootRunId,
        previousRunId: chain.previousRunId,
        generation: chain.generation,
      });
    } catch (error) {
      if (isStale(chain, ticket)) {
        log("network resume dispatch", {
          threadId: chain.threadId,
          sendTicket: ticket,
          generation: chain.generation,
          outcome: "discarded",
        });
        return;
      }
      chain.phase = "waiting";
      chain.dispatched = false;
      chain.nextDispatchAt = now() + rejectedRetryMs;
      log("network resume dispatch", {
        threadId: chain.threadId,
        sendTicket: ticket,
        generation: chain.generation,
        outcome: "rejected",
        error: String(error),
      });
      emit({ kind: "chain-updated", threadId: chain.threadId });
      return;
    }
    if (isStale(chain, ticket)) {
      log("network resume dispatch", {
        threadId: chain.threadId,
        sendTicket: ticket,
        generation: chain.generation,
        outcome: "discarded",
      });
      return;
    }
    if (result?.status === "already-running") {
      remove(chain, "taken-over");
      return;
    }
    chain.dispatched = true;
    if (typeof result?.runId === "string" && result.runId) {
      chain.previousRunId = result.runId;
    }
    log("network resume dispatch", {
      threadId: chain.threadId,
      sendTicket: ticket,
      generation: chain.generation,
      outcome: "dispatched",
      // Splits "reachable" from "continuation accepted": the coordinator polls
      // every 2s, so this is what the probe cadence costs on top of the call.
      elapsedMs: now() - dispatchStartedAt,
    });
    emit({ kind: "chain-updated", threadId: chain.threadId });
  }

  async function dispatchWaiting() {
    if (!network.reachable) return;
    const cutoff = now();
    const ready = [...chains.values()].filter(
      (chain) => chain.phase === "waiting" && cutoff >= chain.nextDispatchAt,
    );
    for (const chain of ready) await dispatch(chain);
  }

  function remove(chain, outcome) {
    if (!chains.delete(chain.threadId)) return;
    chain.closing = true;
    chain.sendTicket += 1;
    tombstones.add(`${chain.threadId}:${chain.rootRunId}`);
    log("network resume released", {
      threadId: chain.threadId,
      attempts: chain.generation,
      totalWaitMs: now() - chain.firstWaitingAt,
      outcome,
    });
    emit({ kind: "chain-removed", threadId: chain.threadId, outcome });
    ensureMonitor();
  }

  function arm({ threadId, reason = "network", runId = null } = {}) {
    if (!enabled || typeof threadId !== "string" || !threadId) return null;
    const rootRunId =
      typeof runId === "string" && runId
        ? runId
        : `${threadId}:${now().toString(36)}`;
    if (tombstones.has(`${threadId}:${rootRunId}`)) return null;
    const existing = chains.get(threadId);
    if (existing) {
      if (existing.phase === "waiting" && existing.reason !== reason) {
        existing.reason = reason;
        emit({ kind: "chain-updated", threadId });
      }
      return snapshotChain(existing);
    }
    const chain = {
      threadId,
      rootRunId,
      previousRunId: typeof runId === "string" && runId ? runId : null,
      generation: 0,
      phase: "waiting",
      reason,
      sendTicket: 0,
      closing: false,
      firstWaitingAt: now(),
      nextDispatchAt: 0,
      dispatched: false,
      /** Set when the renderer reports the continuation streaming (`inflight`). */
      dispatchedAt: null,
    };
    chains.set(threadId, chain);
    log("network resume armed", { threadId, reason, generation: 0 });
    emit({ kind: "chain-updated", threadId });
    ensureMonitor();
    return snapshotChain(chain);
  }

  function disarm({ threadId } = {}) {
    const chain = chains.get(threadId);
    if (!chain) return false;
    remove(chain, "disarmed");
    return true;
  }

  /**
   * The renderer's stream is connected again. While `waiting` that proves
   * nothing — the SDK reconnects to the thread as soon as the backend answers,
   * but the run it wanted may be gone, so only the probe's activity check may
   * decide the turn survived. While `dispatching` it is the run we just
   * created, which is now streaming.
   */
  function notifyWorking({ threadId } = {}) {
    const chain = chains.get(threadId);
    if (!chain) return false;
    if (chain.phase === "dispatching") {
      chain.phase = "inflight";
      chain.dispatchedAt = now();
      emit({ kind: "chain-updated", threadId });
      // The monitor stops when no chain is waiting; restart it so the inflight
      // timeout is enforced even if the renderer never reports completion.
      ensureMonitor();
    }
    return true;
  }

  function complete({ threadId } = {}) {
    const chain = chains.get(threadId);
    if (!chain) {
      // A late or duplicated completion after the timeout released the chain:
      // expected, but worth seeing when a turn looks like it never finished.
      log("network resume complete", { threadId, outcome: "no-chain" });
      return false;
    }
    remove(chain, "completed");
    return true;
  }

  function setEnabled(value) {
    enabled = value === true;
    if (!enabled) {
      for (const chain of [...chains.values()]) remove(chain, "disabled");
    }
    emit({ kind: "network" });
    return enabled;
  }

  return {
    arm,
    disarm,
    notifyWorking,
    complete,
    snapshot,
    onChange,
    poke,
    setEnabled,
    get enabled() {
      return enabled;
    },
  };
}

module.exports = {
  PROBE_FAILURES_BEFORE_DOWN,
  classifyRecoverable,
  classifyStall,
  classifyThreadState,
  createNetworkResumeCoordinator,
  isOfflineErrorMessage,
};
