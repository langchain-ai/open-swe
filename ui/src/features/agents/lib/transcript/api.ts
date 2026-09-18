/**
 * Read and write endpoints for the transcript event log. Reads never touch
 * LangGraph; writes are the same v3 `run.start` command `useStream` posts, so
 * the server's existing enrichment (ownership, model, thread creation) applies
 * unchanged.
 */

import { AgentsApiError, agentsRequest } from "@/features/agents/lib/api"
import { promptMessage } from "@/features/agents/lib/stream/promptMessage"
import { dashboardApiUrl } from "@/lib/dashboard-fetch"
import { withRequestTiming } from "@/lib/perf/fetchTiming"
import type { ImageChunk } from "@/features/agents/lib/types"
import type {
  StoredEvent,
  ToolOutputResponse,
  TranscriptSnapshot,
  TranscriptTurnPage,
} from "./types"

const timedFetch = withRequestTiming((input, init) => fetch(input, init))

function transcriptPath(threadId: string, suffix = ""): string {
  return `/threads/${encodeURIComponent(threadId)}/transcript${suffix}`
}

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

/** The whole thread as of `version`; 404 for threads that predate the log. */
export function fetchTranscript(threadId: string): Promise<TranscriptSnapshot> {
  return agentsRequest<TranscriptSnapshot>(transcriptPath(threadId))
}

/**
 * The page of turns immediately older than `cursor`. The cursor is opaque and
 * thread-scoped: the server rejects one minted for another thread rather than
 * serving a first page that would duplicate history.
 */
export function fetchOlderTurns(
  threadId: string,
  cursor: string,
  signal?: AbortSignal
): Promise<TranscriptTurnPage> {
  return agentsRequest<TranscriptTurnPage>(
    transcriptPath(threadId, `/turns?before=${encodeURIComponent(cursor)}`),
    signal ? { signal } : {}
  )
}

/** Full output for one tool call. The snapshot carries only a preview. */
export function fetchToolOutput(
  threadId: string,
  toolCallId: string
): Promise<ToolOutputResponse> {
  return agentsRequest<ToolOutputResponse>(
    transcriptPath(
      threadId,
      `/tool-calls/${encodeURIComponent(toolCallId)}/output`
    )
  )
}

/** Where one image attachment's bytes are served from. */
export function attachmentUrl(threadId: string, attachmentId: string): string {
  return dashboardApiUrl(
    transcriptPath(threadId, `/attachments/${encodeURIComponent(attachmentId)}`)
  )
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

export interface TranscriptEventHandlers {
  onEvent: (event: StoredEvent) => void
  /** The replay gap was too large to send event by event; reset to this. */
  onSnapshot: (snapshot: TranscriptSnapshot) => void
  /** Replay finished and the connection is now live. */
  onSynchronized: () => void
  /** The thread is gone. The server ends the stream; do not reopen it. */
  onDeleted: () => void
  onOpen?: () => void
  /** The connection dropped or a frame was unreadable. Reopening is the caller's call. */
  onError: (error: unknown) => void
}

export interface TranscriptEventStream {
  close: () => void
}

/**
 * Subscribe to everything after `after`. Same-origin (or the configured API
 * origin) with the session cookie, which is all `EventSource` can carry — the
 * route takes no headers for that reason.
 *
 * The stream is not reopened here: `EventSource`'s own retry would replay from
 * the stale `after` it was opened with, so the caller closes it and reopens
 * from the last applied version instead.
 */
export function openTranscriptEvents(
  threadId: string,
  after: number,
  handlers: TranscriptEventHandlers
): TranscriptEventStream {
  const url = `${dashboardApiUrl(transcriptPath(threadId, "/events"))}?after=${after}`
  const source = new EventSource(url, { withCredentials: true })
  let closed = false

  const close = () => {
    closed = true
    source.close()
  }

  const parse = <T>(event: MessageEvent<string>, apply: (value: T) => void) => {
    if (closed) return
    try {
      apply(JSON.parse(event.data) as T)
    } catch (error) {
      handlers.onError(error)
    }
  }

  source.addEventListener("transcript", (event) =>
    parse<StoredEvent>(event, handlers.onEvent)
  )
  source.addEventListener("snapshot", (event) =>
    parse<TranscriptSnapshot>(event, handlers.onSnapshot)
  )
  // Both frames carry an empty body; their arrival is the whole signal.
  source.addEventListener("synchronized", () => {
    if (!closed) handlers.onSynchronized()
  })
  source.addEventListener("deleted", () => {
    if (closed) return
    // The stream ends here, and `EventSource` would treat that end as a drop
    // worth retrying, so it is closed before the handler can ask for more.
    close()
    handlers.onDeleted()
  })
  source.addEventListener("open", () => {
    if (!closed) handlers.onOpen?.()
  })
  source.addEventListener("error", () => {
    if (closed) return
    // `EventSource` gives no detail beyond readyState, so report the state.
    handlers.onError(
      new Error(
        source.readyState === EventSource.CLOSED
          ? "Transcript event stream closed"
          : "Transcript event stream interrupted"
      )
    )
  })

  return { close }
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
