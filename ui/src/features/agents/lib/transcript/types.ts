/**
 * Wire types for the append-only thread transcript event log.
 *
 * Everything here mirrors the server's JSON (snake_case, ISO-8601 timestamps)
 * and is modelled down to the fields the client actually reads: nothing
 * downstream of the reducer speaks snake_case, and nothing here is reshaped on
 * the way in.
 */

type JsonValue =
  | string
  | number
  | boolean
  | null
  | Array<JsonValue>
  | { [key: string]: JsonValue }

export type JsonObject = { [key: string]: JsonValue }

export type TranscriptThreadStatus = "idle" | "running" | "error"

export type TurnState =
  | "requested"
  | "running"
  | "completed"
  | "failed"
  | "interrupted"

export type MessageRole = "human" | "ai"

export type ToolCallStatus = "in_progress" | "completed" | "error"

export type NoticeKind = "model_routed" | "conversation_offloading"

/**
 * A file attached to a message. The log stores metadata only — never base64
 * payloads: `attachment_id` addresses the bytes on the transcript attachments
 * endpoint, and is null for an attachment whose bytes were never captured (a
 * `url` without an attachment is a remote reference).
 */
export interface TranscriptAttachment {
  mime_type: string
  file_name: string | null
  url: string | null
  attachment_id: string | null
}

/** Token accounting for one AI message, as the provider reported it. */
export interface TranscriptUsage {
  input_tokens?: number | null
  output_tokens?: number | null
  total_tokens?: number | null
}

/** A subagent's position in the run tree: `[]` at the root, `[task_tool_call_id, …]` below it. */
export type Namespace = ReadonlyArray<string>

export interface TranscriptThreadRow {
  status: TranscriptThreadStatus
}

export interface TranscriptTurnRow {
  turn_id: string
  /** Set once a run serves the turn, or as soon as one is queued for it. */
  run_id: string | null
  state: TurnState
  requested_at: string
  started_at: string | null
  error: string | null
}

/** Who sent a human message; null on AI messages. */
export interface TranscriptSender {
  login: string
  kind: string
}

export interface TranscriptMessageRow {
  message_id: string
  turn_id: string
  role: MessageRole
  text: string
  reasoning: string
  namespace: Namespace
  sender?: TranscriptSender | null
  attachments: ReadonlyArray<TranscriptAttachment> | null
  /** Set on AI messages the provider reported usage for; null otherwise. */
  usage: TranscriptUsage | null
  created_at: string
}

export interface TranscriptToolCallRow {
  tool_call_id: string
  turn_id: string
  name: string
  input: JsonObject
  status: ToolCallStatus
  output_preview: string | null
  /** Whether the full output can be fetched from the tool-output endpoint. */
  has_output: boolean
  namespace: Namespace
  started_at: string
}

export interface TranscriptNoticeRow {
  turn_id: string
  kind: NoticeKind
  data: JsonObject
}

/**
 * The newest window of a thread. `version` is the head of the log whatever the
 * window holds — live events only ever concern the newest turn or the thread
 * row — so `after=version` remains the right subscription point.
 *
 * `older_cursor` is an opaque keyset cursor for the adjacent page of older
 * turns, or null once the window reaches the first turn.
 */
export interface TranscriptSnapshot {
  thread_id: string
  version: number
  thread: TranscriptThreadRow
  turns: ReadonlyArray<TranscriptTurnRow>
  messages: ReadonlyArray<TranscriptMessageRow>
  tool_calls: ReadonlyArray<TranscriptToolCallRow>
  notices: ReadonlyArray<TranscriptNoticeRow>
  older_cursor: string | null
}

/**
 * One page of turns strictly older than the cursor that asked for it. Settled
 * turns are immutable, so a page never has to be refetched — and carries no
 * notices or thread row, both of which describe the newest turn only.
 */
export interface TranscriptTurnPage {
  turns: ReadonlyArray<TranscriptTurnRow>
  messages: ReadonlyArray<TranscriptMessageRow>
  tool_calls: ReadonlyArray<TranscriptToolCallRow>
  older_cursor: string | null
}

export interface ToolOutputResponse {
  output: string
}

export interface TurnRequestedPayload {
  turn_id: string
  message_id: string
  text: string
  sender?: TranscriptSender
  attachments: ReadonlyArray<TranscriptAttachment>
}

export interface TurnPayload {
  turn_id: string
}

/** A requested turn now has a run waiting behind the live one. */
export interface TurnQueuedPayload extends TurnPayload {
  run_id: string
}

export interface TurnFailedPayload extends TurnPayload {
  error: string
}

/** A flush of buffered model output; `text`/`reasoning` are fragments to concatenate. */
export interface MessageAppendedPayload {
  turn_id: string
  message_id: string
  namespace: Namespace
  text: string | null
  reasoning: string | null
}

/** The canonical message, which replaces whatever the fragments accumulated. */
export interface MessageCompletedPayload {
  turn_id: string
  message_id: string
  namespace: Namespace
  role: MessageRole
  text: string
  reasoning: string
  sender?: TranscriptSender | null
  attachments: ReadonlyArray<TranscriptAttachment> | null
  usage: TranscriptUsage | null
  created_at: string
}

export interface ToolStartedPayload {
  turn_id: string
  tool_call_id: string
  name: string
  input: JsonObject
  namespace: Namespace
}

export interface ToolCompletedPayload {
  turn_id: string
  tool_call_id: string
  status: "completed" | "error"
  /** The first 2000 characters of the output; the rest is behind the endpoint. */
  output_preview: string | null
  output_truncated: boolean
  has_output: boolean
}

export interface RunNoticePayload {
  turn_id: string
  kind: NoticeKind
  data: JsonObject
}

interface StoredEventEnvelope {
  version: number
  occurred_at: string
}

/**
 * One stored event. Each `payload` also repeats the body's own `type`
 * discriminator, which duplicates `event_type` exactly and is deliberately not
 * modelled: `event_type` is the discriminator the reducer narrows on.
 */
type Stored<EventType extends string, Payload> = StoredEventEnvelope & {
  event_type: EventType
  payload: Payload
}

export type StoredEvent =
  | Stored<"turn.requested", TurnRequestedPayload>
  | Stored<"turn.started", TurnPayload>
  | Stored<"turn.queued", TurnQueuedPayload>
  | Stored<"turn.completed", TurnPayload>
  | Stored<"turn.failed", TurnFailedPayload>
  | Stored<"turn.interrupted", TurnPayload>
  | Stored<"message.appended", MessageAppendedPayload>
  | Stored<"message.completed", MessageCompletedPayload>
  | Stored<"tool.started", ToolStartedPayload>
  | Stored<"tool.completed", ToolCompletedPayload>
  | Stored<"run.notice", RunNoticePayload>
