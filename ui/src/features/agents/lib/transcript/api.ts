/**
 * Write endpoints for the transcript event log, plus the image fetch its
 * attachments need. Writes are the same v3 `run.start` command `useStream`
 * posts, so the server's existing enrichment (ownership, model, thread
 * creation) applies unchanged.
 */

import { AgentsApiError } from "@/features/agents/lib/api"
import { promptMessage } from "@/features/agents/lib/stream/promptMessage"
import { dashboardApiUrl } from "@/lib/dashboard-fetch"
import { withRequestTiming } from "@/lib/perf/fetchTiming"
import type { ImageChunk } from "@/features/agents/lib/types"

const timedFetch = withRequestTiming((input, init) => fetch(input, init))

async function apiError(response: Response): Promise<AgentsApiError> {
  let message = response.statusText
  try {
    const body: unknown = await response.json()
    const detail =
      body !== null && typeof body === "object" && "detail" in body
        ? (body as { detail: unknown }).detail
        : undefined
    if (typeof detail === "string" && detail) message = detail
    else if (detail !== undefined) message = JSON.stringify(detail)
  } catch {
    // Not a JSON error body; the status text is all we have.
  }
  return new AgentsApiError(response.status, message)
}

/**
 * The bytes behind a dashboard image URL, fetched with the session cookie. An
 * `<img src>` would carry it only same-origin (a split deployment's images are
 * a third-party request), so the caller shows the blob instead.
 */
export async function fetchImageBlob(url: string): Promise<Blob> {
  const response = await timedFetch(url, { credentials: "include" })
  if (!response.ok) throw await apiError(response)
  return await response.blob()
}

export interface RunStartMessage {
  /** Client-minted id; the graph's HumanMessage and the log's message row share it. */
  id: string
  text: string
  images?: ReadonlyArray<ImageChunk>
}

export interface RunStartCommand {
  id: number
  method: "run.start"
  params: {
    input: { messages: Array<Record<string, unknown>> } | null
    config: { configurable: Record<string, unknown> }
    assistant_id: string
  }
}

const AGENT_ASSISTANT_ID = "agent"

/**
 * The command `useStream.submit` would have sent: one `run.start` carrying the
 * new human message plus the run `configurable` (with `thread_id` bound, the
 * way the SDK binds it).
 */
export function runStartCommand({
  threadId,
  message,
  configurable = {},
}: {
  threadId: string
  /** Omitted for a message-less run such as `/offload`. */
  message?: RunStartMessage
  configurable?: Record<string, unknown>
}): RunStartCommand {
  return {
    id: 1,
    method: "run.start",
    params: {
      input: message
        ? {
            messages: [
              {
                ...promptMessage(message.text, message.images),
                id: message.id,
              },
            ],
          }
        : null,
      config: { configurable: { ...configurable, thread_id: threadId } },
      assistant_id: AGENT_ASSISTANT_ID,
    },
  }
}

interface ProtocolFailure {
  type: "error"
  error?: string
  message?: string
}

function isProtocolFailure(value: unknown): value is ProtocolFailure {
  return (
    value !== null &&
    typeof value === "object" &&
    (value as { type?: unknown }).type === "error"
  )
}

/**
 * Post a thread command. The first `run.start` on a client-minted thread id is
 * what creates the thread, so this doubles as the creation call.
 */
export async function startRun(
  threadId: string,
  command: RunStartCommand,
  options: { signal?: AbortSignal } = {}
): Promise<void> {
  const response = await timedFetch(
    dashboardApiUrl(`/threads/${encodeURIComponent(threadId)}/commands`),
    {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(command),
      ...(options.signal ? { signal: options.signal } : {}),
    }
  )
  if (!response.ok) throw await apiError(response)
  const payload: unknown = await response.json().catch(() => null)
  if (isProtocolFailure(payload)) {
    throw new AgentsApiError(
      response.status,
      payload.message ?? payload.error ?? "run.start failed"
    )
  }
}
