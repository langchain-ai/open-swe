import type { Client } from "@langchain/langgraph-sdk"

/**
 * Resume a run's SSE stream where it left off instead of replaying it.
 *
 * `client.runs.joinStream` defaults `lastEventId` to `"-1"`, so every rejoin —
 * remounting the thread view, switching back to a running thread, a dropped
 * event stream — re-delivers the run's whole event history and re-animates
 * every tool call the user already watched. The SDK does track a last event id,
 * but only within one `joinStream` call, so it is lost across mounts.
 *
 * Recording the last id each run reaches, and handing it back on the next
 * rejoin, turns a rejoin into "send me what I missed". A server that ignores
 * `Last-Event-ID` just replays as before, so this can only narrow the payload.
 */

const STORAGE_KEY = "open-swe:stream-resume"
/** Enough for far more concurrent runs than a session realistically opens. */
const MAX_ENTRIES = 64

type ResumeMap = Record<string, string>

function storage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.sessionStorage
  } catch {
    // Private windows and blocked site data throw on access, not just on use.
    return null
  }
}

function readMap(): ResumeMap {
  const store = storage()
  if (!store) return {}
  try {
    const raw = store.getItem(STORAGE_KEY)
    if (!raw) return {}
    const parsed: unknown = JSON.parse(raw)
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? (parsed as ResumeMap)
      : {}
  } catch {
    return {}
  }
}

function writeMap(map: ResumeMap): void {
  const store = storage()
  if (!store) return
  try {
    store.setItem(STORAGE_KEY, JSON.stringify(map))
  } catch {
    // A full or unavailable store costs a replay, not correctness.
  }
}

function resumeKey(threadId: string | undefined | null, runId: string): string {
  return `${threadId ?? "-"}:${runId}`
}

export function recallStreamEvent(
  threadId: string | undefined | null,
  runId: string
): string | undefined {
  return readMap()[resumeKey(threadId, runId)]
}

export function rememberStreamEvent(
  threadId: string | undefined | null,
  runId: string,
  eventId: string
): void {
  const map = readMap()
  const key = resumeKey(threadId, runId)
  // Re-insert last so the oldest key is the one dropped when trimming.
  delete map[key]
  map[key] = eventId
  const keys = Object.keys(map)
  for (const stale of keys.slice(0, Math.max(0, keys.length - MAX_ENTRIES))) {
    delete map[stale]
  }
  writeMap(map)
}

export function forgetStreamEvents(threadId: string): void {
  const map = readMap()
  const prefix = `${threadId}:`
  let changed = false
  for (const key of Object.keys(map)) {
    if (key.startsWith(prefix)) {
      delete map[key]
      changed = true
    }
  }
  if (changed) writeMap(map)
}

interface ResumableRuns {
  joinStream: (
    threadId: string | undefined | null,
    runId: string,
    options?: unknown
  ) => AsyncGenerator<{ id?: string }>
  stream: (
    threadId: string | null,
    assistantId: string,
    payload?: Record<string, unknown>
  ) => AsyncGenerator<{ id?: string }>
  __openSweResume?: boolean
}

/**
 * Wrap a client so both the initial run stream and every rejoin record the ids
 * they see, and so a rejoin resumes from the last one. Mutates the client's
 * `runs` namespace in place — the SDK holds its own reference to it, so
 * returning a copy would not be seen.
 *
 * The initial `stream` has to record too: without it the first rejoin after a
 * submit has nothing to resume from and replays the run in full, which is the
 * common case (send a message, navigate away, come back).
 */
export function withStreamResume(client: Client): Client {
  const runs = client.runs as unknown as ResumableRuns | undefined
  // A client without a `runs` namespace is a stub (tests) or a shape this
  // wrapper does not understand; leave it exactly as it was.
  if (!runs?.joinStream || !runs.stream) return client
  if (runs.__openSweResume) return client
  runs.__openSweResume = true

  const run = runs.stream.bind(runs)
  runs.stream = async function* (threadId, assistantId, payload) {
    // The run id only exists once the server has created the run, so the ids
    // seen before that are buffered and flushed when `onRunCreated` fires.
    let activeRunId: string | null = null
    let activeThreadId = threadId
    let pendingEventId: string | null = null
    const callerOnRunCreated = payload?.["onRunCreated"]
    const nextPayload = {
      ...payload,
      onRunCreated: (params: { run_id?: string; thread_id?: string }) => {
        activeRunId = params.run_id ?? null
        activeThreadId = params.thread_id ?? activeThreadId
        if (activeRunId && pendingEventId) {
          rememberStreamEvent(activeThreadId, activeRunId, pendingEventId)
          pendingEventId = null
        }
        if (typeof callerOnRunCreated === "function") {
          ;(callerOnRunCreated as (p: unknown) => void)(params)
        }
      },
    }

    for await (const value of run(threadId, assistantId, nextPayload)) {
      if (value.id) {
        if (activeRunId)
          rememberStreamEvent(activeThreadId, activeRunId, value.id)
        else pendingEventId = value.id
      }
      yield value
    }
  }

  const join = runs.joinStream.bind(runs)
  runs.joinStream = async function* (threadId, runId, options) {
    // An AbortSignal in the options slot is the SDK's legacy call shape. It has
    // no field to carry a resume point, so it passes through untouched.
    const isSignal = options instanceof AbortSignal
    const supplied =
      !isSignal && typeof options === "object" && options !== null
        ? (options as { lastEventId?: string }).lastEventId
        : undefined
    // `"-1"` is the SDK's own "from the beginning" default rather than a real
    // caller intent, so it is treated as absent. The reconnect-on-mount path
    // passes no options at all, which is the case that matters most.
    const resumeFrom =
      supplied && supplied !== "-1"
        ? supplied
        : recallStreamEvent(threadId, runId)
    const nextOptions = isSignal
      ? options
      : {
          ...(typeof options === "object" && options !== null ? options : {}),
          ...(resumeFrom ? { lastEventId: resumeFrom } : {}),
        }

    for await (const value of join(threadId, runId, nextOptions)) {
      if (value.id) rememberStreamEvent(threadId, runId, value.id)
      yield value
    }
  }

  return client
}
