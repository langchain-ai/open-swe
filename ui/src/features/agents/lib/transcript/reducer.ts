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
import { fetchToolOutput } from "./api"
import type { StructuredEntity } from "@/features/agents/lib/structuredInputMessages"
import type {
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
  TranscriptSender,
  TranscriptSnapshot,
  TranscriptThreadStatus,
  TurnState,
} from "./types"

export interface TranscriptMessageState {
  messageId: string
  turnId: string
  role: MessageRole
  text: string
  reasoning: string
  streaming: boolean
  namespace: Namespace
  sender: TranscriptSender | null
  createdAt: string
}

export interface TranscriptToolCallState {
  toolCallId: string
  turnId: string
  messageId: string | null
  name: string
  input: JsonObject
  status: ToolCallStatus
  /** Preview from the snapshot, or the full output when it arrived by event. */
  output: string | null
  outputTruncated: boolean
  /** Whether {@link output} is everything the server has. */
  outputComplete: boolean
  /** Whether the server holds output worth fetching on expand. */
  hasOutput: boolean
  namespace: Namespace
  startedAt: string
  endedAt: string | null
}

/** A turn's contents in event order; messages and tool calls interleave. */
export type TurnItem =
  | { kind: "message"; id: string }
  | { kind: "tool"; id: string }

export interface TranscriptTurnState {
  turnId: string
  runId: string | null
  state: TurnState
  requestedAt: string
  startedAt: string | null
  completedAt: string | null
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
  activeRunId: string | null
  turnOrder: ReadonlyArray<string>
  turns: Readonly<Record<string, TranscriptTurnState>>
  messages: Readonly<Record<string, TranscriptMessageState>>
  toolCalls: Readonly<Record<string, TranscriptToolCallState>>
  /** Latest notice per kind; the server only replays them for the active turn. */
  notices: Readonly<Partial<Record<NoticeKind, TranscriptNoticeState>>>
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

export function fromSnapshot(snapshot: TranscriptSnapshot): TranscriptState {
  const messages: Record<string, TranscriptMessageState> = {}
  for (const row of snapshot.messages) {
    messages[row.message_id] = {
      messageId: row.message_id,
      turnId: row.turn_id,
      role: row.role,
      text: row.text,
      reasoning: row.reasoning,
      streaming: row.streaming,
      namespace: row.namespace,
      sender: row.sender ?? null,
      createdAt: row.created_at,
    }
  }
  const toolCalls: Record<string, TranscriptToolCallState> = {}
  for (const row of snapshot.tool_calls) {
    toolCalls[row.tool_call_id] = {
      toolCallId: row.tool_call_id,
      turnId: row.turn_id,
      messageId: row.message_id,
      name: row.name,
      input: row.input,
      status: row.status,
      output: row.output_preview,
      outputTruncated: row.output_truncated,
      outputComplete: false,
      hasOutput: row.has_output,
      namespace: row.namespace,
      startedAt: row.started_at,
      endedAt: row.ended_at,
    }
  }
  const turns: Record<string, TranscriptTurnState> = {}
  for (const row of snapshot.turns) {
    turns[row.turn_id] = {
      turnId: row.turn_id,
      runId: row.run_id,
      state: row.state,
      requestedAt: row.requested_at,
      startedAt: row.started_at,
      completedAt: row.completed_at,
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
    activeRunId: snapshot.thread.active_run_id,
    turnOrder: snapshot.turns.map((turn) => turn.turn_id),
    turns,
    messages,
    toolCalls,
    notices,
    entities: collectStructuredEntities(humanTexts(messages)),
  }
}

interface Draft {
  state: TranscriptState
  /** Turns whose revision has to be bumped before the draft is returned. */
  touched: Set<string>
}

function turnOf(draft: Draft, turnId: string, occurredAt: string): void {
  if (draft.state.turns[turnId]) return
  // A turn we never saw requested (a run started elsewhere, or a replay that
  // began mid-turn). Its events still belong somewhere in order.
  draft.state = {
    ...draft.state,
    turnOrder: [...draft.state.turnOrder, turnId],
    turns: {
      ...draft.state.turns,
      [turnId]: {
        turnId,
        runId: null,
        state: "running",
        requestedAt: occurredAt,
        startedAt: occurredAt,
        completedAt: null,
        error: null,
        items: [],
        revision: 0,
      },
    },
  }
  draft.touched.add(turnId)
}

function patchTurn(
  draft: Draft,
  turnId: string,
  patch: Partial<Omit<TranscriptTurnState, "turnId" | "revision">>
): void {
  const turn = draft.state.turns[turnId]
  if (!turn) return
  draft.state = {
    ...draft.state,
    turns: { ...draft.state.turns, [turnId]: { ...turn, ...patch } },
  }
  draft.touched.add(turnId)
}

function addItem(draft: Draft, turnId: string, item: TurnItem): void {
  const turn = draft.state.turns[turnId]
  if (!turn) return
  if (turn.items.some((held) => held.kind === item.kind && held.id === item.id))
    return
  patchTurn(draft, turnId, { items: [...turn.items, item] })
}

function putMessage(draft: Draft, message: TranscriptMessageState): void {
  draft.state = {
    ...draft.state,
    messages: { ...draft.state.messages, [message.messageId]: message },
  }
  draft.touched.add(message.turnId)
  addItem(draft, message.turnId, { kind: "message", id: message.messageId })
  if (message.role === "human") {
    draft.state = {
      ...draft.state,
      entities: collectStructuredEntities(humanTexts(draft.state.messages)),
    }
  }
}

function putToolCall(draft: Draft, call: TranscriptToolCallState): void {
  draft.state = {
    ...draft.state,
    toolCalls: { ...draft.state.toolCalls, [call.toolCallId]: call },
  }
  draft.touched.add(call.turnId)
  addItem(draft, call.turnId, { kind: "tool", id: call.toolCallId })
}

/** A turn ended, so nothing in it is still streaming. */
function settleStreaming(draft: Draft, turnId: string): void {
  const streaming = Object.values(draft.state.messages).filter(
    (message) => message.turnId === turnId && message.streaming
  )
  if (!streaming.length) return
  const messages = { ...draft.state.messages }
  for (const message of streaming) {
    messages[message.messageId] = { ...message, streaming: false }
  }
  draft.state = { ...draft.state, messages }
  draft.touched.add(turnId)
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
    case "thread.created": {
      break
    }
    case "thread.meta_updated": {
      const patch = event.payload.patch
      draft.state = {
        ...draft.state,
        ...(patch.status ? { status: patch.status } : {}),
        ...("active_run_id" in patch
          ? { activeRunId: patch.active_run_id ?? null }
          : {}),
      }
      break
    }
    case "turn.requested": {
      const payload = event.payload
      draft.state = {
        ...draft.state,
        turnOrder: draft.state.turns[payload.turn_id]
          ? draft.state.turnOrder
          : [...draft.state.turnOrder, payload.turn_id],
        turns: {
          ...draft.state.turns,
          [payload.turn_id]: {
            turnId: payload.turn_id,
            runId: event.run_id,
            state: "requested",
            requestedAt: at,
            startedAt: null,
            completedAt: null,
            error: null,
            items: [],
            revision: 0,
          },
        },
      }
      draft.touched.add(payload.turn_id)
      putMessage(draft, {
        messageId: payload.message_id,
        turnId: payload.turn_id,
        role: "human",
        text: payload.text,
        reasoning: "",
        streaming: false,
        namespace: [],
        sender: payload.sender,
        createdAt: at,
      })
      break
    }
    case "turn.started": {
      const payload = event.payload
      turnOf(draft, payload.turn_id, at)
      patchTurn(draft, payload.turn_id, {
        state: "running",
        runId: payload.run_id,
        startedAt: at,
      })
      draft.state = {
        ...draft.state,
        status: "running",
        activeRunId: payload.run_id,
      }
      break
    }
    case "turn.completed": {
      const payload = event.payload
      turnOf(draft, payload.turn_id, at)
      patchTurn(draft, payload.turn_id, {
        state: "completed",
        completedAt: at,
      })
      settleStreaming(draft, payload.turn_id)
      draft.state = { ...draft.state, status: "idle", activeRunId: null }
      break
    }
    case "turn.failed": {
      const payload = event.payload
      turnOf(draft, payload.turn_id, at)
      patchTurn(draft, payload.turn_id, {
        state: "failed",
        completedAt: at,
        error: payload.error,
      })
      settleStreaming(draft, payload.turn_id)
      draft.state = { ...draft.state, status: "error", activeRunId: null }
      break
    }
    case "turn.interrupted": {
      const payload = event.payload
      turnOf(draft, payload.turn_id, at)
      patchTurn(draft, payload.turn_id, {
        state: "interrupted",
        completedAt: at,
      })
      settleStreaming(draft, payload.turn_id)
      draft.state = { ...draft.state, status: "idle", activeRunId: null }
      break
    }
    case "message.appended": {
      const payload = event.payload
      turnOf(draft, payload.turn_id, at)
      const existing = draft.state.messages[payload.message_id]
      putMessage(draft, {
        messageId: payload.message_id,
        turnId: payload.turn_id,
        role: existing?.role ?? "ai",
        text: (existing?.text ?? "") + (payload.text ?? ""),
        reasoning: (existing?.reasoning ?? "") + (payload.reasoning ?? ""),
        streaming: true,
        namespace: payload.namespace,
        sender: existing?.sender ?? null,
        createdAt: existing?.createdAt ?? at,
      })
      break
    }
    case "message.completed": {
      const payload = event.payload
      turnOf(draft, payload.turn_id, at)
      const existing = draft.state.messages[payload.message_id]
      putMessage(draft, {
        messageId: payload.message_id,
        turnId: payload.turn_id,
        role: payload.role,
        text: payload.text,
        reasoning: payload.reasoning,
        streaming: false,
        namespace: payload.namespace,
        sender: payload.sender ?? existing?.sender ?? null,
        createdAt: payload.created_at || existing?.createdAt || at,
      })
      break
    }
    case "tool.started": {
      const payload = event.payload
      turnOf(draft, payload.turn_id, at)
      putToolCall(draft, {
        toolCallId: payload.tool_call_id,
        turnId: payload.turn_id,
        messageId: payload.message_id ?? null,
        name: payload.name,
        input: payload.input,
        status: "in_progress",
        output: null,
        outputTruncated: false,
        outputComplete: false,
        hasOutput: false,
        namespace: payload.namespace,
        startedAt: at,
        endedAt: null,
      })
      break
    }
    case "tool.completed": {
      const payload = event.payload
      turnOf(draft, payload.turn_id, at)
      const existing = draft.state.toolCalls[payload.tool_call_id]
      putToolCall(draft, {
        toolCallId: payload.tool_call_id,
        turnId: payload.turn_id,
        messageId: existing?.messageId ?? null,
        name: existing?.name ?? payload.tool_call_id,
        input: existing?.input ?? {},
        status: payload.status,
        output: payload.output,
        outputTruncated: payload.output_truncated,
        // The event carries the output the server kept, truncation aside.
        outputComplete: true,
        hasOutput: payload.output.length > 0,
        namespace: payload.namespace,
        startedAt: existing?.startedAt ?? at,
        endedAt: at,
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

export function isRunning(state: TranscriptState): boolean {
  return state.status === "running"
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
  const calls: Array<TranscriptToolCallState> = []
  for (const turnId of state.turnOrder) {
    const turn = state.turns[turnId]
    if (!turn) continue
    for (const item of turn.items) {
      if (item.kind !== "tool") continue
      const call = state.toolCalls[item.id]
      if (!call || call.namespace.length < namespace.length) continue
      if (
        namespace.every((segment, index) => call.namespace[index] === segment)
      )
        calls.push(call)
    }
  }
  return calls.map((call) => ({
    toolCallId: call.toolCallId,
    name: call.name,
    status: call.status,
  }))
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
    status: call.status === "in_progress" ? "in_progress" : call.status,
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

function senderNote(entity: StructuredEntity | undefined): string | undefined {
  if (entity?.senderType === "bot") return "bot"
  if (entity?.openSweAccount === "unlinked") return "not an Open SWE user"
  return undefined
}

interface HumanCacheEntry {
  entities: ReadonlyMap<string, StructuredEntity>
  /** `null` when the message is one the transcript deliberately hides. */
  message: Message | null
}

const humanCache = new WeakMap<TranscriptMessageState, HumanCacheEntry>()

function humanMessage(
  row: TranscriptMessageState,
  entities: ReadonlyMap<string, StructuredEntity>
): Message | null {
  const cached = humanCache.get(row)
  if (cached && cached.entities === entities) return cached.message
  const message = buildHumanMessage(row, entities)
  humanCache.set(row, { entities, message })
  return message
}

function buildHumanMessage(
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
  if (!text.trim()) return null
  return {
    id: row.messageId,
    author:
      parsed.type === "message" && parsed.senderKind === "system"
        ? "system"
        : "user",
    timestamp: row.createdAt,
    chunks: [{ kind: "text", text }],
    ...(parsed.type === "message"
      ? {
          structuredSenderId: parsed.sender,
          structuredSenderKind: parsed.senderKind,
          structuredSurface: parsed.surface,
          structuredSenderName:
            entity?.displayName ??
            (entity?.handle ? `@${entity.handle}` : undefined),
          structuredSenderNote: senderNote(entity),
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
        const message = humanMessage(row, state.entities)
        if (!message) continue
        turnKey = row.messageId
        out.push(message)
        continue
      }
      const chunks: Array<Chunk> = []
      const reasoning = row.reasoning.trim()
      if (reasoning) chunks.push({ kind: "reasoning", text: reasoning })
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

const messagesCache = new WeakMap<TranscriptState, Array<Message>>()

/**
 * The whole transcript as UI rows. Turns that did not change keep their exact
 * `Message` objects, so a fragment on the newest turn re-renders that turn
 * alone.
 */
export function toMessages(state: TranscriptState): Array<Message> {
  const cached = messagesCache.get(state)
  if (cached) return cached
  const messages: Array<Message> = []
  for (const turnId of state.turnOrder) {
    const turn = state.turns[turnId]
    if (turn) messages.push(...turnMessages(state, turn))
  }
  messagesCache.set(state, messages)
  return messages
}
