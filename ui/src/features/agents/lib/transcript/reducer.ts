/**
 * The client half of the transcript log: a normalised projection of the same
 * events the server projects into Postgres, plus the mapping onto the UI's
 * {@link Message} model.
 *
 * Two rules keep this honest:
 *
 * - **Identical concatenation.** `message.appended` carries fragments, which
 *   are appended exactly the way the server's projection appends them, and
 *   `message.completed` always replaces with the canonical text, so a lost
 *   fragment self-heals.
 * - **Structural sharing.** A turn object is replaced whenever anything inside
 *   it changes and only then, so {@link toMessages} can memoise per turn: a
 *   fragment on the newest turn leaves every earlier turn's `Message` object
 *   (and therefore its rendered subtree) untouched.
 */

import {
  INTERNAL_TOOLS,
  maybeDiffFromArgs,
  mergeTextChunks,
  toolKind,
  toolTitle,
} from "@/features/agents/lib/streamMessagesToUi"
import {
  collectStructuredEntities,
  parseStructuredInput,
} from "@/features/agents/lib/structuredInputMessages"
import { contextTokensFromUsageMetadata } from "@/features/agents/lib/contextUsage"
import { attachmentUrl, fetchToolOutput } from "./api"
import type { StructuredEntity } from "@/features/agents/lib/structuredInputMessages"
import type {
  AnyImageChunk,
  Chunk,
  Message,
  ToolExecutionChunk,
} from "@/features/agents/lib/types"
import type {
  JsonObject,
  MessageRole,
  Namespace,
  NoticeKind,
  StoredEvent,
  ToolCallStatus,
  TranscriptAttachment,
  TranscriptMessageRow,
  TranscriptSnapshot,
  TranscriptThreadStatus,
  TranscriptToolCallRow,
  TranscriptTurnPage,
  TranscriptTurnRow,
  TurnState,
} from "./types"

export interface TranscriptMessageState {
  messageId: string
  turnId: string
  role: MessageRole
  text: string
  reasoning: string
  namespace: Namespace
  attachments: ReadonlyArray<TranscriptAttachment>
  createdAt: string
}

export interface TranscriptToolCallState {
  toolCallId: string
  turnId: string
  name: string
  input: JsonObject
  status: ToolCallStatus
  /** Preview from the snapshot, or the full output when it arrived by event. */
  output: string | null
  /** Whether {@link output} is everything the server has. */
  outputComplete: boolean
  /** Whether the server holds output worth fetching on expand. */
  hasOutput: boolean
  namespace: Namespace
  startedAt: string
}

/** A turn's contents in event order; messages and tool calls interleave. */
export type TurnItem =
  | { kind: "message"; id: string }
  | { kind: "tool"; id: string }

export interface TranscriptTurnState {
  turnId: string
  state: TurnState
  requestedAt: string
  error: string | null
  items: ReadonlyArray<TurnItem>
  /** Bumped whenever this turn or anything it holds changed; the memo key. */
  revision: number
}

export interface TranscriptNoticeState {
  turnId: string
  kind: NoticeKind
  data: JsonObject
}

export interface TranscriptState {
  threadId: string
  /** Version of the last applied event; the resume point for a subscription. */
  version: number
  status: TranscriptThreadStatus
  turnOrder: ReadonlyArray<string>
  turns: Readonly<Record<string, TranscriptTurnState>>
  messages: Readonly<Record<string, TranscriptMessageState>>
  toolCalls: Readonly<Record<string, TranscriptToolCallState>>
  /**
   * Latest notice per kind, for the newest turn only — the same window the
   * snapshot serves, so a reload and a live stream agree.
   */
  notices: Readonly<Partial<Record<NoticeKind, TranscriptNoticeState>>>
  /**
   * Cursor for the page of turns older than the oldest one loaded, or null
   * when the whole thread is here. Older pages, once loaded, are kept: their
   * turns have settled and can no longer change.
   */
  olderCursor: string | null
  /** Context size the newest AI message reported, for the composer's meter. */
  contextTokens: number | null
  /** Sender entities parsed out of human message text, rebuilt only when that text changes. */
  entities: ReadonlyMap<string, StructuredEntity>
}

function orderItems(
  messages: ReadonlyArray<TranscriptMessageState>,
  toolCalls: ReadonlyArray<TranscriptToolCallState>
): Array<TurnItem> {
  const entries: Array<{ item: TurnItem; at: string; rank: number }> = [
    ...messages.map((message) => ({
      item: { kind: "message" as const, id: message.messageId },
      at: message.createdAt,
      rank: 0,
    })),
    ...toolCalls.map((call) => ({
      item: { kind: "tool" as const, id: call.toolCallId },
      at: call.startedAt,
      rank: 1,
    })),
  ]
  entries.sort(
    (a, b) =>
      a.at.localeCompare(b.at) ||
      a.rank - b.rank ||
      a.item.id.localeCompare(b.item.id)
  )
  return entries.map((entry) => entry.item)
}

function humanTexts(
  messages: Readonly<Record<string, TranscriptMessageState>>
): Array<string> {
  return Object.values(messages)
    .filter((message) => message.role === "human")
    .map((message) => message.text)
}

function indexMessages(
  rows: ReadonlyArray<TranscriptMessageRow>
): Record<string, TranscriptMessageState> {
  const messages: Record<string, TranscriptMessageState> = {}
  for (const row of rows) {
    messages[row.message_id] = {
      messageId: row.message_id,
      turnId: row.turn_id,
      role: row.role,
      text: row.text,
      reasoning: row.reasoning,
      namespace: row.namespace,
      attachments: row.attachments ?? [],
      createdAt: row.created_at,
    }
  }
  return messages
}

function indexToolCalls(
  rows: ReadonlyArray<TranscriptToolCallRow>
): Record<string, TranscriptToolCallState> {
  const toolCalls: Record<string, TranscriptToolCallState> = {}
  for (const row of rows) {
    toolCalls[row.tool_call_id] = {
      toolCallId: row.tool_call_id,
      turnId: row.turn_id,
      name: row.name,
      input: row.input,
      status: row.status,
      output: row.output_preview,
      outputComplete: false,
      hasOutput: row.has_output,
      namespace: row.namespace,
      startedAt: row.started_at,
    }
  }
  return toolCalls
}

function turnStates(
  rows: ReadonlyArray<TranscriptTurnRow>,
  messages: Readonly<Record<string, TranscriptMessageState>>,
  toolCalls: Readonly<Record<string, TranscriptToolCallState>>
): Record<string, TranscriptTurnState> {
  const turns: Record<string, TranscriptTurnState> = {}
  for (const row of rows) {
    turns[row.turn_id] = {
      turnId: row.turn_id,
      state: row.state,
      requestedAt: row.requested_at,
      error: row.error,
      items: orderItems(
        Object.values(messages).filter(
          (message) => message.turnId === row.turn_id
        ),
        Object.values(toolCalls).filter((call) => call.turnId === row.turn_id)
      ),
      revision: 0,
    }
  }
  return turns
}

/**
 * Turn ids in transcript order — `(requested_at, turn_id)`, the server's own
 * ordering — whatever order the turns were merged in. Pages arrive newest
 * first and older pages are prepended later, so insertion order says nothing.
 */
function sortedTurnOrder(
  turns: Readonly<Record<string, TranscriptTurnState>>
): Array<string> {
  return Object.values(turns)
    .sort(
      (a, b) =>
        a.requestedAt.localeCompare(b.requestedAt) ||
        a.turnId.localeCompare(b.turnId)
    )
    .map((turn) => turn.turnId)
}

export function fromSnapshot(snapshot: TranscriptSnapshot): TranscriptState {
  const messages = indexMessages(snapshot.messages)
  const toolCalls = indexToolCalls(snapshot.tool_calls)
  const turns = turnStates(snapshot.turns, messages, toolCalls)
  const notices: Partial<Record<NoticeKind, TranscriptNoticeState>> = {}
  for (const row of snapshot.notices) {
    notices[row.kind] = {
      turnId: row.turn_id,
      kind: row.kind,
      data: row.data,
    }
  }
  return {
    threadId: snapshot.thread_id,
    version: snapshot.version,
    status: snapshot.thread.status,
    turnOrder: sortedTurnOrder(turns),
    turns,
    messages,
    toolCalls,
    notices,
    olderCursor: snapshot.older_cursor,
    // `snapshot.messages` is ordered by `created_at`, so the last AI row is the
    // newest one, and its usage is what the composer's meter reads.
    contextTokens: contextTokensFromUsageMetadata(
      snapshot.messages.findLast((message) => message.role === "ai")?.usage
    ),
    entities: collectStructuredEntities(humanTexts(messages)),
  }
}

/**
 * Take a fresh snapshot without losing the history around it.
 *
 * The server sends one when a subscriber's replay gap is too large, and it
 * carries the newest window only. Turns older than that window have settled
 * and are immutable, so whatever the client already loaded stays loaded and
 * the cursor that reaches furthest back is the one that survives.
 */
export function applySnapshot(
  state: TranscriptState | null,
  snapshot: TranscriptSnapshot
): TranscriptState {
  const fresh = fromSnapshot(snapshot)
  if (!state) return fresh
  const messages = { ...state.messages, ...fresh.messages }
  const toolCalls = { ...state.toolCalls, ...fresh.toolCalls }
  const turns = { ...state.turns, ...fresh.turns }
  const turnOrder = sortedTurnOrder(turns)
  const freshOldest = fresh.turnOrder[0]
  const keptOlderHistory =
    freshOldest === undefined || turnOrder[0] !== freshOldest
  return {
    ...fresh,
    turnOrder,
    turns,
    messages,
    toolCalls,
    olderCursor: keptOlderHistory ? state.olderCursor : fresh.olderCursor,
    // A window whose AI messages reported no usage leaves the last known
    // context size in place rather than blanking the composer's meter.
    contextTokens: fresh.contextTokens ?? state.contextTokens,
    entities: collectStructuredEntities(humanTexts(messages)),
  }
}

/**
 * Merge a page of older turns in and advance the cursor. Nothing already in
 * state is overwritten: the page is strictly older, and a settled turn the
 * client holds is already final.
 */
export function prependTurns(
  state: TranscriptState,
  page: TranscriptTurnPage
): TranscriptState {
  const messages = { ...indexMessages(page.messages), ...state.messages }
  const toolCalls = { ...indexToolCalls(page.tool_calls), ...state.toolCalls }
  const turns = {
    ...turnStates(page.turns, messages, toolCalls),
    ...state.turns,
  }
  return {
    ...state,
    turnOrder: sortedTurnOrder(turns),
    turns,
    messages,
    toolCalls,
    olderCursor: page.older_cursor,
    entities: collectStructuredEntities(humanTexts(messages)),
  }
}

interface Draft {
  state: TranscriptState
  /** Turns whose revision has to be bumped before the draft is returned. */
  touched: Set<string>
}

/**
 * The turn an opening event concerns. Only `turn.requested` and `turn.started`
 * may fabricate one: a run started elsewhere, or a replay that began mid-turn,
 * still belongs somewhere in order. Every other turn-scoped event concerns a
 * turn the client already has.
 */
function ensureTurn(
  draft: Draft,
  turnId: string,
  state: TurnState,
  occurredAt: string
): TranscriptTurnState {
  const existing = draft.state.turns[turnId]
  if (existing) return existing
  const turn: TranscriptTurnState = {
    turnId,
    state,
    requestedAt: occurredAt,
    error: null,
    items: [],
    revision: 0,
  }
  draft.state = {
    ...draft.state,
    turnOrder: [...draft.state.turnOrder, turnId],
    turns: { ...draft.state.turns, [turnId]: turn },
  }
  draft.touched.add(turnId)
  return turn
}

function patchTurn(
  draft: Draft,
  turn: TranscriptTurnState,
  patch: Partial<Omit<TranscriptTurnState, "turnId" | "revision">>
): void {
  draft.state = {
    ...draft.state,
    turns: { ...draft.state.turns, [turn.turnId]: { ...turn, ...patch } },
  }
  draft.touched.add(turn.turnId)
}

function addItem(
  draft: Draft,
  turn: TranscriptTurnState,
  item: TurnItem
): void {
  if (turn.items.some((held) => held.kind === item.kind && held.id === item.id))
    return
  patchTurn(draft, turn, { items: [...turn.items, item] })
}

function putMessage(
  draft: Draft,
  turn: TranscriptTurnState,
  message: TranscriptMessageState
): void {
  draft.state = {
    ...draft.state,
    messages: { ...draft.state.messages, [message.messageId]: message },
  }
  draft.touched.add(turn.turnId)
  addItem(draft, turn, { kind: "message", id: message.messageId })
  if (message.role !== "human") return
  // Only a human message's own text can declare an entity, and a write that
  // declares none keeps the very same Map: every per-turn render cache keys on
  // it, and a fresh Map for an unchanged set would rebuild the whole transcript.
  const declared = collectStructuredEntities([message.text])
  if (!declared.size) return
  draft.state = {
    ...draft.state,
    entities: new Map([...draft.state.entities, ...declared]),
  }
}

function putToolCall(
  draft: Draft,
  turn: TranscriptTurnState,
  call: TranscriptToolCallState
): void {
  draft.state = {
    ...draft.state,
    toolCalls: { ...draft.state.toolCalls, [call.toolCallId]: call },
  }
  draft.touched.add(turn.turnId)
  addItem(draft, turn, { kind: "tool", id: call.toolCallId })
}

/**
 * Offloading describes what a run is doing right now, so it dies with its turn
 * — the snapshot drops it for a settled turn, and so does the live stream.
 */
function dropTransientNotices(draft: Draft, turnId: string): void {
  const notice = draft.state.notices["conversation_offloading"]
  if (!notice || notice.turnId !== turnId) return
  const notices = { ...draft.state.notices }
  delete notices["conversation_offloading"]
  draft.state = { ...draft.state, notices }
}

function commit(draft: Draft, version: number): TranscriptState {
  if (!draft.touched.size) return { ...draft.state, version }
  const turns = { ...draft.state.turns }
  for (const turnId of draft.touched) {
    const turn = turns[turnId]
    if (turn) turns[turnId] = { ...turn, revision: turn.revision + 1 }
  }
  return { ...draft.state, turns, version }
}

/**
 * Fold one event into the state. Events at or below the current version are
 * replays and are dropped, so a reconnect may safely resubscribe from an older
 * version than it has already applied.
 */
export function applyEvent(
  state: TranscriptState,
  event: StoredEvent
): TranscriptState {
  if (event.version <= state.version) return state
  const draft: Draft = { state, touched: new Set() }
  const at = event.occurred_at

  switch (event.event_type) {
    case "turn.requested": {
      const payload = event.payload
      const turn = ensureTurn(draft, payload.turn_id, "requested", at)
      // The server flips the thread to running as it accepts the command, so
      // the composer reads as busy without waiting for `turn.started`.
      // Notices describe the newest turn, which this event opens; the snapshot
      // serves them the same way, so a reload never resurrects an older turn's.
      draft.state = { ...draft.state, status: "running", notices: {} }
      putMessage(draft, turn, {
        messageId: payload.message_id,
        turnId: payload.turn_id,
        role: "human",
        text: payload.text,
        reasoning: "",
        namespace: [],
        attachments: payload.attachments,
        createdAt: at,
      })
      break
    }
    case "turn.started": {
      const payload = event.payload
      patchTurn(draft, ensureTurn(draft, payload.turn_id, "running", at), {
        state: "running",
      })
      draft.state = { ...draft.state, status: "running" }
      break
    }
    case "turn.completed": {
      const payload = event.payload
      const turn = draft.state.turns[payload.turn_id]
      if (!turn) break
      patchTurn(draft, turn, { state: "completed" })
      dropTransientNotices(draft, payload.turn_id)
      draft.state = { ...draft.state, status: "idle" }
      break
    }
    case "turn.failed": {
      const payload = event.payload
      const turn = draft.state.turns[payload.turn_id]
      if (!turn) break
      patchTurn(draft, turn, { state: "failed", error: payload.error })
      dropTransientNotices(draft, payload.turn_id)
      draft.state = { ...draft.state, status: "error" }
      break
    }
    case "turn.interrupted": {
      const payload = event.payload
      const turn = draft.state.turns[payload.turn_id]
      if (!turn) break
      patchTurn(draft, turn, { state: "interrupted" })
      dropTransientNotices(draft, payload.turn_id)
      draft.state = { ...draft.state, status: "idle" }
      break
    }
    case "message.appended": {
      const payload = event.payload
      const turn = draft.state.turns[payload.turn_id]
      if (!turn) break
      const existing = draft.state.messages[payload.message_id]
      putMessage(draft, turn, {
        messageId: payload.message_id,
        turnId: payload.turn_id,
        role: existing?.role ?? "ai",
        text: (existing?.text ?? "") + (payload.text ?? ""),
        reasoning: (existing?.reasoning ?? "") + (payload.reasoning ?? ""),
        namespace: payload.namespace,
        attachments: existing?.attachments ?? [],
        createdAt: existing?.createdAt ?? at,
      })
      break
    }
    case "message.completed": {
      const payload = event.payload
      const turn = draft.state.turns[payload.turn_id]
      if (!turn) break
      const existing = draft.state.messages[payload.message_id]
      putMessage(draft, turn, {
        messageId: payload.message_id,
        turnId: payload.turn_id,
        role: payload.role,
        text: payload.text,
        reasoning: payload.reasoning,
        namespace: payload.namespace,
        attachments: payload.attachments ?? existing?.attachments ?? [],
        createdAt: payload.created_at || existing?.createdAt || at,
      })
      if (payload.role === "ai") {
        // Events arrive in order, so the message that just completed is the
        // newest one. A message the provider reported no usage for leaves the
        // last known size in place rather than blanking the meter.
        const tokens = contextTokensFromUsageMetadata(payload.usage)
        if (tokens !== null)
          draft.state = { ...draft.state, contextTokens: tokens }
      }
      break
    }
    case "tool.started": {
      const payload = event.payload
      const turn = draft.state.turns[payload.turn_id]
      if (!turn) break
      putToolCall(draft, turn, {
        toolCallId: payload.tool_call_id,
        turnId: payload.turn_id,
        name: payload.name,
        input: payload.input,
        status: "in_progress",
        output: null,
        outputComplete: false,
        hasOutput: false,
        namespace: payload.namespace,
        startedAt: at,
      })
      break
    }
    case "tool.completed": {
      const payload = event.payload
      const turn = draft.state.turns[payload.turn_id]
      const existing = draft.state.toolCalls[payload.tool_call_id]
      if (!turn || !existing) break
      putToolCall(draft, turn, {
        ...existing,
        status: payload.status,
        output: payload.output_preview,
        // Anything the preview cut off is fetched from the endpoint on expand.
        outputComplete: !payload.output_truncated,
        hasOutput: payload.has_output,
      })
      break
    }
    case "run.notice": {
      const payload = event.payload
      draft.state = {
        ...draft.state,
        notices: {
          ...draft.state.notices,
          [payload.kind]: {
            turnId: payload.turn_id,
            kind: payload.kind,
            data: payload.data,
          },
        },
      }
      break
    }
  }

  return commit(draft, event.version)
}

/** The routed model the Auto router picked for the newest run, when it said. */
export function routedNotice(
  state: TranscriptState
): { route?: string; modelId?: string | null } | null {
  const notice = state.notices["model_routed"]
  if (!notice) return null
  const route = notice.data["route"]
  const modelId = notice.data["model_id"]
  return {
    ...(typeof route === "string" ? { route } : {}),
    modelId: typeof modelId === "string" ? modelId : null,
  }
}

export function isOffloading(state: TranscriptState): boolean {
  const notice = state.notices["conversation_offloading"]
  return notice?.data["status"] === "started"
}

export interface SubagentToolCall {
  toolCallId: string
  name: string
  status: ToolCallStatus
}

/**
 * Tool calls a subagent (and anything it spawned in turn) ran, in order. The
 * subagent card's activity line is the only consumer.
 */
export function subagentToolCalls(
  state: TranscriptState,
  namespace: Namespace
): Array<SubagentToolCall> {
  if (!namespace.length) return []
  const calls: Array<SubagentToolCall> = []
  for (const turnId of state.turnOrder) {
    const turn = state.turns[turnId]
    if (!turn) continue
    for (const item of turn.items) {
      if (item.kind !== "tool") continue
      const call = state.toolCalls[item.id]
      if (!call) continue
      if (
        namespace.every((segment, index) => call.namespace[index] === segment)
      )
        calls.push({
          toolCallId: call.toolCallId,
          name: call.name,
          status: call.status,
        })
    }
  }
  return calls
}

function toolChunk(
  threadId: string,
  call: TranscriptToolCallState
): ToolExecutionChunk {
  const kind = toolKind(call.name)
  const chunk: ToolExecutionChunk = {
    kind: "tool-execution",
    toolCallId: call.toolCallId,
    timestamp: call.startedAt,
    title: toolTitle(call.name, call.input),
    toolKind: kind,
    input: call.input,
    status: call.status,
  }
  const output = call.output?.trim()
  if (output) chunk.output = output
  if (call.hasOutput && !call.outputComplete) {
    chunk.loadOutput = async () =>
      (await fetchToolOutput(threadId, call.toolCallId)).output
  }
  const diffData = maybeDiffFromArgs(call.input)
  if (diffData) chunk.diffData = diffData
  // A `task` call owns the namespace of the subagent it spawned, which is what
  // the card subscribes to for nested activity.
  if (kind === "task") {
    chunk.subagentNamespace = [...call.namespace, call.toolCallId]
  }
  return chunk
}

/**
 * The attachments a message carries, as chunks a renderer can show: an
 * attachment is fetched from our own API with the session, a bare `url` is a
 * remote reference the browser loads itself, and one with neither has no bytes
 * to show.
 */
function imageChunks(
  threadId: string,
  attachments: ReadonlyArray<TranscriptAttachment>
): Array<AnyImageChunk> {
  const chunks: Array<AnyImageChunk> = []
  for (const image of attachments) {
    const source = image.attachment_id
      ? {
          url: attachmentUrl(threadId, image.attachment_id),
          credentials: "session" as const,
        }
      : image.url
        ? { url: image.url, credentials: "none" as const }
        : null
    if (!source) continue
    chunks.push({
      kind: "image",
      ...source,
      ...(image.mime_type ? { mimeType: image.mime_type } : {}),
      ...(image.file_name ? { fileName: image.file_name } : {}),
    })
  }
  return chunks
}

interface HumanCacheEntry {
  entities: ReadonlyMap<string, StructuredEntity>
  /** `null` when the message is one the transcript deliberately hides. */
  message: Message | null
}

const humanCache = new WeakMap<TranscriptMessageState, HumanCacheEntry>()

// `threadId` is not part of the cache key: a message row belongs to exactly
// one thread for its whole life.
function humanMessage(
  threadId: string,
  row: TranscriptMessageState,
  entities: ReadonlyMap<string, StructuredEntity>
): Message | null {
  const cached = humanCache.get(row)
  if (cached && cached.entities === entities) return cached.message
  const message = buildHumanMessage(threadId, row, entities)
  humanCache.set(row, { entities, message })
  return message
}

function buildHumanMessage(
  threadId: string,
  row: TranscriptMessageState,
  entities: ReadonlyMap<string, StructuredEntity>
): Message | null {
  const parsed = parseStructuredInput(row.text, entities)
  if (parsed.type === "entity") return null
  if (parsed.type === "message" && parsed.sender === "system:sender-context")
    return null
  const entity =
    parsed.type === "message" ? entities.get(parsed.sender) : undefined
  // Our own replies reach the transcript twice: once forwarded as thread
  // context, once as the `slack_thread_reply` call that sent them.
  if (entity?.senderType === "self") return null
  const text = parsed.content
  const chunks: Array<Chunk> = imageChunks(threadId, row.attachments)
  if (text.trim()) chunks.push({ kind: "text", text })
  if (!chunks.length) return null
  return {
    id: row.messageId,
    author:
      parsed.type === "message" && parsed.senderKind === "system"
        ? "system"
        : "user",
    timestamp: row.createdAt,
    chunks,
    ...(parsed.type === "message"
      ? {
          structuredSenderId: parsed.sender,
          structuredSenderKind: parsed.senderKind,
          structuredSurface: parsed.surface,
          structuredSenderName:
            entity?.displayName ??
            (entity?.handle ? `@${entity.handle}` : undefined),
          structuredSenderNote:
            entity?.senderType === "bot"
              ? "bot"
              : entity?.openSweAccount === "unlinked"
                ? "not an Open SWE user"
                : undefined,
        }
      : {}),
  }
}

interface AgentDraft {
  id: string
  timestamp: string
  startedAt: string
  turnKey?: string
  chunks: Array<Chunk>
}

interface TurnCacheEntry {
  revision: number
  entities: ReadonlyMap<string, StructuredEntity>
  messages: Array<Message>
}

const turnCache = new WeakMap<TranscriptTurnState, TurnCacheEntry>()

/**
 * The turn's rows as the transcript renders them: the human message that opened
 * it, then one agent message whose chunks interleave reasoning, prose and tool
 * calls in event order — the same shape `streamMessagesToUi` produces.
 */
function turnMessages(
  state: TranscriptState,
  turn: TranscriptTurnState
): Array<Message> {
  const cached = turnCache.get(turn)
  if (
    cached &&
    cached.revision === turn.revision &&
    cached.entities === state.entities
  ) {
    return cached.messages
  }

  const out: Array<Message> = []
  let agent: AgentDraft | null = null
  let turnKey: string | undefined

  const flush = () => {
    if (!agent) return
    out.push({
      id: agent.id,
      author: "agent",
      timestamp: agent.timestamp,
      startedAt: agent.startedAt,
      ...(agent.turnKey ? { turnKey: agent.turnKey } : {}),
      chunks: mergeTextChunks(agent.chunks),
    })
    agent = null
  }

  const append = (id: string, timestamp: string, chunks: Array<Chunk>) => {
    if (!chunks.length) return
    if (!agent) {
      agent = {
        id,
        timestamp,
        startedAt: timestamp,
        turnKey,
        chunks: [...chunks],
      }
      return
    }
    agent.timestamp = timestamp
    agent.chunks.push(...chunks)
  }

  for (const item of turn.items) {
    if (item.kind === "message") {
      const row = state.messages[item.id]
      // Subagent output is not part of the root transcript; the subagent card
      // that spawned it renders its activity instead.
      if (!row || row.namespace.length) continue
      if (row.role === "human") {
        flush()
        const message = humanMessage(state.threadId, row, state.entities)
        if (!message) continue
        turnKey = row.messageId
        out.push(message)
        continue
      }
      const chunks: Array<Chunk> = []
      const reasoning = row.reasoning.trim()
      if (reasoning) chunks.push({ kind: "reasoning", text: reasoning })
      chunks.push(...imageChunks(state.threadId, row.attachments))
      const text = row.text.trim()
      if (text) chunks.push({ kind: "text", text })
      append(row.messageId, row.createdAt, chunks)
      continue
    }
    const call = state.toolCalls[item.id]
    if (!call || call.namespace.length) continue
    if (INTERNAL_TOOLS.has(call.name)) continue
    append(call.toolCallId, call.startedAt, [toolChunk(state.threadId, call)])
  }
  flush()

  turnCache.set(turn, {
    revision: turn.revision,
    entities: state.entities,
    messages: out,
  })
  return out
}

interface MessagesCacheEntry {
  turnOrder: ReadonlyArray<string>
  entities: ReadonlyMap<string, StructuredEntity>
  messages: Array<Message>
}

// Keyed on the turn table rather than the state: a state whose version alone
// advanced renders identically.
const messagesCache = new WeakMap<
  Readonly<Record<string, TranscriptTurnState>>,
  MessagesCacheEntry
>()

/**
 * The whole transcript as UI rows. Turns that did not change keep their exact
 * `Message` objects, so a fragment on the newest turn re-renders that turn
 * alone.
 */
export function toMessages(state: TranscriptState): Array<Message> {
  const cached = messagesCache.get(state.turns)
  if (
    cached &&
    cached.turnOrder === state.turnOrder &&
    cached.entities === state.entities
  ) {
    return cached.messages
  }
  const messages: Array<Message> = []
  for (const turnId of state.turnOrder) {
    const turn = state.turns[turnId]
    if (turn) messages.push(...turnMessages(state, turn))
  }
  messagesCache.set(state.turns, {
    turnOrder: state.turnOrder,
    entities: state.entities,
    messages,
  })
  return messages
}
