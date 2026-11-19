import {
  ApiError,
  ProtocolError,
  type CoordinatorThread,
  type TaskActivity,
} from "./api.ts"
import { CredentialError } from "./credentials.ts"
import { errorMessage } from "./json.ts"

/** How the thread's last run ended. */
export type RunStatus = "completed" | "failed" | "interrupted"

const MIN_RETRY_MS = 1_000
const MAX_RETRY_MS = 30_000
const MAX_RETRIES = 10
/** Often enough to end within a second or two of the thread settling. */
const POLL_MS = 1_000

export interface FollowOptions {
  onActivity?: (activity: TaskActivity) => void
  onRetry?: (attempt: number, reason: string) => void
  sleep?: (ms: number) => Promise<void>
}

function wait(ms: number): Promise<void> {
  return new Promise<void>((done) => setTimeout(done, ms))
}

function retryable(cause: unknown): boolean {
  if (cause instanceof ApiError)
    return cause.status >= 500 || cause.status === 408 || cause.status === 429
  return !(cause instanceof ProtocolError || cause instanceof CredentialError)
}

/** Calls `read` until it answers, backing off through failures a backend restart causes. */
async function retrying<T>(
  read: () => Promise<T>,
  options: FollowOptions
): Promise<T> {
  const sleep = options.sleep ?? wait
  let backoff = MIN_RETRY_MS
  for (let attempt = 1; ; attempt += 1) {
    try {
      return await read()
    } catch (cause) {
      if (!retryable(cause) || attempt > MAX_RETRIES) throw cause
      options.onRetry?.(attempt, errorMessage(cause))
      await sleep(backoff)
      backoff = Math.min(backoff * 2, MAX_RETRY_MS)
    }
  }
}

/** The thread's activity once nothing is left to run on it: no run, worker or report. */
async function settled(
  thread: CoordinatorThread,
  runId: string | null,
  options: FollowOptions
): Promise<TaskActivity> {
  const sleep = options.sleep ?? wait
  for (;;) {
    const activity = await retrying(() => thread.activity(runId), options)
    options.onActivity?.(activity)
    if (activity.settled) return activity
    await sleep(POLL_MS)
  }
}

/**
 * Waits for the coordinator's thread to settle, then reads how the task ended.
 * Each worker's report wakes the coordinator in a run of its own, so the
 * coordinator's newest run decides whether the task failed. A run's status and
 * the thread's state read the same whichever event format a run streams in.
 */
export async function followTask(
  thread: CoordinatorThread,
  runId: string | null,
  options: FollowOptions = {}
): Promise<RunStatus> {
  const { runs } = await settled(thread, runId, options)
  const newest = runs.at(-1) ?? runId
  if (newest === null)
    throw new ProtocolError("the thread has no run to report on")
  const status = await retrying(() => thread.runStatus(newest), options)
  if (status !== "success")
    return status === "interrupted" ? "interrupted" : "failed"
  const waiting = await retrying(() => thread.waiting(), options)
  return waiting ? "interrupted" : "completed"
}
