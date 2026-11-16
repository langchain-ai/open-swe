import {
  ApiError,
  ProtocolError,
  type CoordinatorThread,
  type TaskActivity,
} from "./api.ts"
import { CredentialError } from "./credentials.ts"
import {
  arrayAt,
  booleanAt,
  errorMessage,
  isRecord,
  numberAt,
  stringAt,
} from "./json.ts"

/** How the thread's last run ended. */
export type RunStatus = "completed" | "failed" | "interrupted"

/** What the agent handed the terminal through the result tool. */
export interface CliResult {
  stdout: string
  exitCode: number
  /** The run had nothing new: the terminal keeps the answer it already has. */
  unchanged: boolean
}

export interface RunOutcome {
  status: RunStatus
  result: CliResult | null
}

export const RESULT_TOOL = "cli_result"
const MAX_EXIT_CODE = 255

/** The result the terminal should end with once `next` is reported after `previous`. */
export function laterResult(
  previous: CliResult | null,
  next: CliResult | null
): CliResult | null {
  if (next === null) return previous
  return next.unchanged && previous !== null ? previous : next
}

export function parseCliResult(input: unknown): CliResult | null {
  const record = isRecord(input) ? input : null
  const stdout = stringAt(record, "stdout")
  const exitCode = numberAt(record, "exit_code")
  if (stdout === null || exitCode === null) return null
  if (!Number.isInteger(exitCode) || exitCode < 0 || exitCode > MAX_EXIT_CODE)
    return null
  return {
    stdout,
    exitCode,
    unchanged: booleanAt(record, "unchanged") ?? false,
  }
}

/**
 * The answer the agent gave the command whose message is `promptId`, from the
 * thread's top-level messages: every result call after that message, in turn.
 * Calls before it answered earlier commands on the thread, so a message that
 * is missing is an error, never a reason to read them.
 */
export function commandResult(
  messages: readonly unknown[],
  promptId: string
): CliResult | null {
  const prompt = messages.findIndex(
    (message) => isRecord(message) && message["id"] === promptId
  )
  if (prompt < 0)
    throw new ProtocolError(
      `the thread's state has no message ${promptId}, the one this command sent`
    )
  let result: CliResult | null = null
  for (const message of messages.slice(prompt + 1)) {
    const calls = arrayAt(isRecord(message) ? message : null, "tool_calls")
    for (const call of calls ?? []) {
      const record = isRecord(call) ? call : null
      if (stringAt(record, "name") === RESULT_TOOL)
        result = laterResult(result, parseCliResult(record?.["args"]))
    }
  }
  return result
}

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
 * Workers share the thread's sandbox, this machine, and each one's report
 * wakes the coordinator in a run of its own, so the coordinator's newest run
 * decides whether the task failed, and an answer given while anything was
 * left to run is not final: the last one is. A run's status and the thread's
 * state read the same whichever event format a run streams in.
 */
export async function followTask(
  thread: CoordinatorThread,
  runId: string | null,
  promptId: string,
  options: FollowOptions = {}
): Promise<RunOutcome> {
  const { runs } = await settled(thread, runId, options)
  const newest = runs.at(-1) ?? runId
  if (newest === null)
    throw new ProtocolError("the thread has no run to report on")
  const status = await retrying(() => thread.runStatus(newest), options)
  if (status !== "success")
    return {
      status: status === "interrupted" ? "interrupted" : "failed",
      result: null,
    }
  const state = await retrying(() => thread.state(), options)
  if (state.waiting) return { status: "interrupted", result: null }
  return {
    status: "completed",
    result: commandResult(state.messages, promptId),
  }
}
