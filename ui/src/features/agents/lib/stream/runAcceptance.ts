import { AgentsApiError } from "@/features/agents/lib/api"
import { runStartOf } from "@/features/agents/lib/runStartOrder"

/** Whether the server started a run for a message, and its reason when it refused. */
export type RunAcceptance =
  | { accepted: true }
  | { accepted: false; refusal?: AgentsApiError }

const NOT_ACCEPTED: RunAcceptance = { accepted: false }
/** Says a message joined the live run instead of starting one. */
const STEERED_HEADER = "x-robin-steered"

/**
 * Match a run start to its submitted human message, as the runs routes answer
 * it, not to the run's end. `runs/stream` names its run in `Content-Location`,
 * or else in its first `metadata` event; `POST /runs` answers with the run. A
 * message steered into the live run is accepted too.
 */
export function createRunAcceptanceTracker(fetcher: typeof fetch) {
  const submissions = new Map<
    string | null,
    {
      response?: Promise<RunAcceptance>
      onAccepted?: () => void
      onRejected?: (error: unknown) => void
    }
  >()
  const trackedFetch = Object.assign((...args: Parameters<typeof fetch>) => {
    const [, init] = args
    const response = fetcher(...args)
    const start = runStartOf(...args)
    if (!start || typeof init?.body !== "string") return response
    const run: unknown = JSON.parse(init.body)
    if (!isRecord(run)) return response
    const messages =
      isRecord(run.input) && Array.isArray(run.input.messages)
        ? run.input.messages
        : [{ id: null }]
    for (const message of messages) {
      if (
        !isRecord(message) ||
        (typeof message.id !== "string" && message.id !== null)
      )
        continue
      const submission = submissions.get(message.id)
      if (!submission) continue
      submission.response = response
        .then((result) => acceptanceOf(result, start.streamed))
        .catch(() => NOT_ACCEPTED)
        .then((acceptance) => {
          if (acceptance.accepted) submission.onAccepted?.()
          else submission.onRejected?.(acceptance.refusal)
          return acceptance
        })
    }
    return response
  }, fetcher)
  return {
    fetch: trackedFetch,
    track(
      messageId: string | null,
      onAccepted?: () => void,
      onRejected?: (error: unknown) => void
    ) {
      const submission: {
        response?: Promise<RunAcceptance>
        onAccepted?: () => void
        onRejected?: (error: unknown) => void
      } = { onAccepted, onRejected }
      submissions.set(messageId, submission)
      return async (): Promise<RunAcceptance> => {
        try {
          return (await submission.response) ?? NOT_ACCEPTED
        } finally {
          if (submissions.get(messageId) === submission)
            submissions.delete(messageId)
        }
      }
    },
  }
}

async function acceptanceOf(
  response: Response,
  streamed: boolean
): Promise<RunAcceptance> {
  // The SDK reports a refusal without the body's reason, so keep it here.
  if (response.status >= 400 && response.status < 500)
    return {
      accepted: false,
      refusal: await AgentsApiError.fromResponse(response.clone()),
    }
  if (!response.ok) return NOT_ACCEPTED
  if (response.headers.get(STEERED_HEADER) === "true") return { accepted: true }
  const runId = streamed
    ? (runIdOf(response.headers.get("content-location")) ??
      (await firstMetadataRunId(response.clone())))
    : response.status === 200
      ? await response
          .clone()
          .json()
          .then((run: unknown) =>
            isRecord(run) && typeof run.run_id === "string" ? run.run_id : null
          )
      : null
  return runId ? { accepted: true } : NOT_ACCEPTED
}

function runIdOf(location: string | null): string | null {
  return location ? (/\/runs\/([^/?]+)$/.exec(location)?.[1] ?? null) : null
}

/** The run a stream's first `metadata` event names, leaving the rest unread. */
async function firstMetadataRunId(response: Response): Promise<string | null> {
  const reader = response.body?.getReader()
  if (!reader) return null
  const decoder = new TextDecoder()
  let buffered = ""
  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) return null
      buffered += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n")
      let end: number
      while ((end = buffered.indexOf("\n\n")) >= 0) {
        const frame = buffered.slice(0, end)
        buffered = buffered.slice(end + 2)
        const lines = frame.split("\n")
        const event = lines
          .find((line) => line.startsWith("event:"))
          ?.slice(6)
          .trim()
        if (event !== "metadata") continue
        const data: unknown = JSON.parse(
          lines
            .filter((line) => line.startsWith("data:"))
            .map((line) => line.slice(5).trim())
            .join("\n")
        )
        return isRecord(data) && typeof data.run_id === "string"
          ? data.run_id
          : null
      }
    }
  } finally {
    void reader.cancel().catch(() => undefined)
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null
}
