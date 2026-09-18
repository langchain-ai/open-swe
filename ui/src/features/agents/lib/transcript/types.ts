/**
 * Wire types for the append-only thread transcript event log.
 *
 * Everything here mirrors the server's JSON exactly (snake_case, ISO-8601
 * timestamps), so these types are the contract boundary: nothing downstream of
 * the reducer speaks snake_case, and nothing here is reshaped on the way in.
 */

export type JsonValue =
  | string
  | number
  | boolean
  | null
  | Array<JsonValue>
  | { [key: string]: JsonValue }

export type JsonObject = { [key: string]: JsonValue }

export type TranscriptKind = "agent" | "reviewer"

export type TranscriptThreadStatus = "idle" | "running" | "error"

export type TurnState =
  | "requested"
  | "running"
  | "completed"
  | "failed"
  | "interrupted"

export type MessageRole = "human" | "ai"

export type ToolCallStatus = "in_progress" | "completed" | "error"

export type ActorKind = "user" | "agent" | "system"

export type NoticeKind =
  | "model_routed"
  | "conversation_offloading"
  | "step_limit"

/**
 * Known sender kinds. The server may add surfaces without a UI change, so an
 * unknown kind stays readable rather than failing to type-check.
 */
export type SenderKind =
  | "dashboard"
  | "slack"
  | "github"
  | "linear"
  | "schedule"
  | "system"

export interface TranscriptSender {
  login: string
  kind: SenderKind | (string & {})
  display_name?: string | null
}

/** A human attachment. The log stores metadata only — never base64 payloads. */
export interface TranscriptImage {
  mime_type: string
  file_name?: string | null
  url?: string | null
}

/** A subagent's position in the run tree: `[]` at the root, `[task_tool_call_id, …]` below it. */
export type Namespace = ReadonlyArray<string>

export interface TranscriptThreadRow {
  kind: TranscriptKind
  status: TranscriptThreadStatus
  active_run_id: string | null
  title: string | null
  created_at: string
  updated_at: string
}

export interface TranscriptTurnRow {
  turn_id: string
  run_id: string | null
  state: TurnState
  requested_at: string
  started_at: string | null
  completed_at: string | null
  error: string | null
  head_commit?: string | null
}

export interface TranscriptMessageRow {
  message_id: string
  turn_id: string
  role: MessageRole
  text: string
  reasoning: string
  streaming: boolean
  namespace: Namespace
  sender?: TranscriptSender | null
  images?: ReadonlyArray<TranscriptImage> | null
  created_at: string
}

export interface TranscriptToolCallRow {
  tool_call_id: string
  turn_id: string
  message_id: string | null
  name: string
  input: JsonObject
  status: ToolCallStatus
  output_preview: string | null
  output_truncated: boolean
  /** Whether the full output can be fetched from the tool-output endpoint. */
  has_output: boolean
  namespace: Namespace
  started_at: string
  ended_at: string | null
}

export interface TranscriptNoticeRow {
  turn_id: string
  kind: NoticeKind
  data: JsonObject
}

export interface TranscriptSnapshot {
  thread_id: string
  version: number
  thread: TranscriptThreadRow
  turns: ReadonlyArray<TranscriptTurnRow>
  messages: ReadonlyArray<TranscriptMessageRow>
  tool_calls: ReadonlyArray<TranscriptToolCallRow>
  notices: ReadonlyArray<TranscriptNoticeRow>
}

export interface ToolOutputResponse {
  output: string
  truncated: boolean
}

export interface SynchronizedFrame {
  version: number
}

export interface ThreadCreatedPayload {
  title: string | null
  kind: TranscriptKind
  source: string
  owner_login: string | null
  visibility: "public" | "private"
  repo_owner?: string | null
  repo_name?: string | null
  model_id?: string | null
  effort?: string | null
  metadata: JsonObject
}

export interface ThreadMetaUpdatedPayload {
  patch: {
    title?: string | null
    status?: TranscriptThreadStatus
    active_run_id?: string | null
    metadata?: JsonObject
  }
}

export interface TurnRequestedPayload {
  turn_id: string
  message_id: string
  text: string
  sender: TranscriptSender
  images?: ReadonlyArray<TranscriptImage> | null
  model_id?: string | null
  effort?: string | null
  plan_mode: boolean
}

export interface TurnStartedPayload {
  turn_id: string
  run_id: string
}

export interface TurnCompletedPayload {
  turn_id: string
  run_id: string | null
  head_commit?: string | null
  base_commit?: string | null
  changed_files?: JsonValue
}

export interface TurnFailedPayload {
  turn_id: string
  run_id: string | null
  error: string
}

export interface TurnInterruptedPayload {
  turn_id: string
  run_id: string | null
}

/** A flush of buffered model output; `text`/`reasoning` are fragments to concatenate. */
export interface MessageAppendedPayload {
  turn_id: string
  message_id: string
  namespace: Namespace
  text?: string
  reasoning?: string
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
  images?: ReadonlyArray<TranscriptImage> | null
  created_at: string
}

export interface ToolStartedPayload {
  turn_id: string
  tool_call_id: string
  message_id?: string | null
  name: string
  input: JsonObject
  namespace: Namespace
}

export interface ToolCompletedPayload {
  turn_id: string
  tool_call_id: string
  status: "completed" | "error"
  output: string
  output_truncated: boolean
  namespace: Namespace
}

export interface RunNoticePayload {
  turn_id: string
  kind: NoticeKind
  data: JsonObject
}

interface StoredEventEnvelope {
  thread_id: string
  version: number
  event_id: string
  schema_version: number
  run_id: string | null
  turn_id: string | null
  command_id: string | null
  actor_kind: ActorKind
  occurred_at: string
}

type Stored<EventType extends string, Payload> = StoredEventEnvelope & {
  event_type: EventType
  payload: Payload
}

export type StoredEvent =
  | Stored<"thread.created", ThreadCreatedPayload>
  | Stored<"thread.meta_updated", ThreadMetaUpdatedPayload>
  | Stored<"turn.requested", TurnRequestedPayload>
  | Stored<"turn.started", TurnStartedPayload>
  | Stored<"turn.completed", TurnCompletedPayload>
  | Stored<"turn.failed", TurnFailedPayload>
  | Stored<"turn.interrupted", TurnInterruptedPayload>
  | Stored<"message.appended", MessageAppendedPayload>
  | Stored<"message.completed", MessageCompletedPayload>
  | Stored<"tool.started", ToolStartedPayload>
  | Stored<"tool.completed", ToolCompletedPayload>
  | Stored<"run.notice", RunNoticePayload>

export type TranscriptEventType = StoredEvent["event_type"]
