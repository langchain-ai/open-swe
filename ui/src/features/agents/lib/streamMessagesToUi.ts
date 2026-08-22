import { AIMessage, HumanMessage, ToolMessage } from "@langchain/core/messages"
import { messageArrivalTimestamp } from "./messageTimestamps"
import { parseStructuredInput } from "./structuredInputMessages"
import { humanizeToolName } from "./toolNames"
import type { BaseMessage, ContentBlock } from "@langchain/core/messages"
import type { AssembledToolCall } from "@langchain/react"

import type {
  ParsedStructuredInput,
  StructuredEntity,
} from "./structuredInputMessages"
import type {
  Chunk,
  DiffData,
  Message,
  OutputIframeDisplay,
  ToolExecutionChunk,
} from "./types"

function senderNote(entity: StructuredEntity | undefined): string | undefined {
  if (entity?.senderType === "bot") return "bot"
  if (entity?.openSweAccount === "unlinked") return "not an Open SWE user"
  return undefined
}

const READ_TOOLS = new Set(["read_file", "read", "ls"])
const EDIT_TOOLS = new Set([
  "write_file",
  "edit_file",
  "str_replace",
  "write",
  "edit",
  "patch",
])
const EXECUTE_TOOLS = new Set(["execute", "bash", "shell", "run_terminal_cmd"])
const SEARCH_TOOLS = new Set(["glob", "grep", "web_search", "search"])
const FETCH_TOOLS = new Set(["fetch", "fetch_url", "http_request"])
const INTERNAL_TOOLS = new Set(["confirming_completion", "no_op"])

type ToolKind = ToolExecutionChunk["toolKind"]

function toolKind(name: string): ToolKind {
  const lowered = name.toLowerCase()
  if (lowered === "task") return "task"
  if (lowered === "slack_thread_reply") return "slack"
  if (lowered === "linear_comment") return "linear"
  if (lowered === "write_todos") return "other"
  if (
    EDIT_TOOLS.has(lowered) ||
    ["edit", "write", "replace"].some((t) => lowered.includes(t))
  ) {
    return "edit"
  }
  if (EXECUTE_TOOLS.has(lowered)) return "execute"
  if (FETCH_TOOLS.has(lowered)) return "fetch"
  if (SEARCH_TOOLS.has(lowered)) return "search"
  if (READ_TOOLS.has(lowered) || lowered.includes("read")) return "read"
  if (lowered === "think") return "think"
  return "other"
}

function toolTitle(name: string, args: Record<string, unknown>): string {
  const path = args.path ?? args.file_path ?? args.target_file
  if (typeof path === "string" && path.trim()) return `${name} ${path.trim()}`
  const command = args.command
  if (typeof command === "string" && command.trim()) {
    return command.trim().split("\n")[0]?.slice(0, 120) ?? ""
  }
  return humanizeToolName(name)
}

function parseToolArgs(raw: unknown): Record<string, unknown> {
  if (raw && typeof raw === "object" && !Array.isArray(raw))
    return raw as Record<string, unknown>
  if (typeof raw === "string") {
    try {
      const parsed = JSON.parse(raw)
      return parsed && typeof parsed === "object" && !Array.isArray(parsed)
        ? (parsed as Record<string, unknown>)
        : { raw }
    } catch {
      return { raw }
    }
  }
  return {}
}

function maybeDiffFromArgs(args: Record<string, unknown>): DiffData | null {
  const path = args.path ?? args.file_path ?? args.target_file
  if (typeof path !== "string" || !path.trim()) return null
  const oldContent = args.old_string ?? args.original_content
  const newContent = args.new_string ?? args.content ?? args.new_content
  if (typeof newContent !== "string") return null
  const original = typeof oldContent === "string" ? oldContent : null
  return {
    originalContent: original,
    newContent,
    filePath: path.trim(),
    isNewFile: original === null,
    isBinary: false,
    isTruncated: false,
    totalLines: Math.max(newContent.split("\n").length, 1),
  }
}

function mergeTextChunks(chunks: Array<Chunk>): Array<Chunk> {
  const textIndices = chunks.flatMap((c, i) => (c.kind === "text" ? [i] : []))
  if (textIndices.length <= 1) return chunks
  const lastText = textIndices[textIndices.length - 1]
  return chunks.filter((c, i) => c.kind !== "text" || i === lastText)
}

type AgentTurn = {
  id: string
  author: Message["author"]
  timestamp: string
  turnKey?: string
  startedAt: string
  timestampIsFallback?: boolean
  chunks: Array<Chunk>
}

type MessageTimestamp = {
  value: string
  isFallback: boolean
}

function messageTimestamp(
  raw: BaseMessage,
  msgId: string,
  resolveCreatedAt?: (messageId: string) => string | undefined
): MessageTimestamp {
  const msg = raw as unknown as Record<string, unknown>
  const createdAt = msg.created_at
  if (typeof createdAt === "string" && createdAt) {
    return { value: createdAt, isFallback: false }
  }
  const responseMetadata = msg.response_metadata
  if (responseMetadata && typeof responseMetadata === "object") {
    const metadataCreatedAt = (responseMetadata as Record<string, unknown>)
      .created_at
    if (typeof metadataCreatedAt === "string" && metadataCreatedAt) {
      return { value: metadataCreatedAt, isFallback: false }
    }
  }
  const resolved = resolveCreatedAt?.(msgId)
  if (typeof resolved === "string" && resolved) {
    return { value: resolved, isFallback: true }
  }
  return { value: new Date().toISOString(), isFallback: true }
}

/**
 * Pull reasoning ("thinking") text out of a message's standard content blocks.
 * `@langchain/core` v1 normalizes provider-specific reasoning (Anthropic
 * `thinking`, OpenAI reasoning, …) into `{ type: "reasoning", reasoning }`
 * blocks via the `contentBlocks` getter, so we don't have to parse each
 * provider's raw shape ourselves.
 */
function reasoningText(raw: BaseMessage): string {
  let blocks: Array<ContentBlock.Standard>
  try {
    blocks = raw.contentBlocks
  } catch {
    return ""
  }
  let text = ""
  for (const block of blocks) {
    if (block.type !== "reasoning") continue
    // Reasoning blocks can arrive without a summary (e.g. OpenAI reasoning
    // models emit `{ type: "reasoning", extras: { content: [] } }` with no
    // `reasoning` field) — skip those so we don't render a "Thought" block
    // whose body is the literal string "undefined".
    const reasoning: unknown = block.reasoning
    if (typeof reasoning === "string") text += reasoning
  }
  return text.trim()
}

function imageChunks(content: unknown): Array<Chunk> {
  if (!Array.isArray(content)) return []

  const chunks: Array<Chunk> = []
  for (const item of content) {
    if (!item || typeof item !== "object" || Array.isArray(item)) continue
    const block = item as Record<string, unknown>
    const type = block.type
    let base64: string | undefined
    let mimeType: string | undefined

    if (type === "image") {
      const data = block.data ?? block.base64
      const mime = block.mime_type ?? block.mimeType
      if (typeof data === "string" && typeof mime === "string") {
        base64 = data
        mimeType = mime
      }
    } else if (type === "image_url") {
      const imageUrl = block.image_url
      const url =
        imageUrl && typeof imageUrl === "object"
          ? (imageUrl as Record<string, unknown>).url
          : undefined
      if (typeof url === "string") {
        const match = /^data:(image\/[^;]+);base64,(.+)$/s.exec(url)
        if (match) {
          mimeType = match[1]
          base64 = match[2]
        }
      }
    }

    if (base64 && mimeType) {
      const fileName = block.fileName ?? block.file_name
      chunks.push({
        kind: "image",
        base64,
        mimeType,
        ...(typeof fileName === "string" && fileName ? { fileName } : {}),
      })
    }
  }
  return chunks
}

/**
 * Map the SDK's assembled tool-call lifecycle status onto the UI status.
 * `stream.toolCalls` exposes a fully-assembled, reactive view of each call
 * ({@link AssembledToolCall}) so we no longer hand-match AI `tool_calls` to
 * their `ToolMessage` results to derive status/output.
 */
function toolStatus(
  assembled: AssembledToolCall | undefined,
  toolMessage: ToolMessage | undefined
): ToolExecutionChunk["status"] {
  if (assembled) {
    if (assembled.status === "finished") return "completed"
    if (assembled.status === "error") return "error"
    return "in_progress"
  }
  if (toolMessage) return toolMessage.status === "error" ? "error" : "completed"
  return "in_progress"
}

function toolOutputText(
  assembled: AssembledToolCall | undefined,
  toolMessage: ToolMessage | undefined
): string | undefined {
  const value = assembled?.output
  if (value != null) {
    if (typeof value === "string") return value.trim() || undefined
    try {
      return JSON.stringify(value)
    } catch {
      return String(value)
    }
  }
  const text = toolMessage?.text.trim()
  return text || undefined
}

function isHttpUrl(value: unknown): value is string {
  if (typeof value !== "string") return false
  try {
    const url = new URL(value)
    return url.protocol === "https:" || url.protocol === "http:"
  } catch {
    return false
  }
}

function outputIframeDisplay(
  toolMessage: ToolMessage | undefined
): OutputIframeDisplay | undefined {
  const artifact = toolMessage?.artifact
  if (!artifact || typeof artifact !== "object" || Array.isArray(artifact)) {
    return undefined
  }
  const value = artifact as Record<string, unknown>
  if (
    value.type !== "output_iframe" ||
    typeof value.title !== "string" ||
    typeof value.filename !== "string"
  ) {
    return undefined
  }
  if (isHttpUrl(value.preview_url) && isHttpUrl(value.download_url)) {
    return {
      type: "output_iframe",
      previewUrl: value.preview_url,
      downloadUrl: value.download_url,
      title: value.title,
      filename: value.filename,
    }
  }
  if (typeof value.html === "string") {
    return {
      type: "output_iframe",
      html: value.html,
      title: value.title,
      filename: value.filename,
    }
  }
  return undefined
}

function aiMessageChunks(
  raw: AIMessage,
  index: number,
  toolCallsById: ReadonlyMap<string, AssembledToolCall>,
  toolMessagesById: ReadonlyMap<string, ToolMessage>
): Array<Chunk> {
  const chunks: Array<Chunk> = []
  const reasoning = reasoningText(raw)
  if (reasoning) chunks.push({ kind: "reasoning", text: reasoning })
  const text = raw.text.trim()
  if (text) chunks.push({ kind: "text", text })

  for (const toolCall of raw.tool_calls ?? []) {
    const name = toolCall.name || "tool"
    if (INTERNAL_TOOLS.has(name)) continue
    const toolCallId = toolCall.id || `tool-${index}-${chunks.length}`
    const args = parseToolArgs(toolCall.args)
    const assembled = toolCallsById.get(toolCallId)
    const toolMessage = toolMessagesById.get(toolCallId)
    const chunk: ToolExecutionChunk = {
      kind: "tool-execution",
      toolCallId,
      timestamp: messageArrivalTimestamp(toolCallId),
      title: toolTitle(name, args),
      toolKind: toolKind(name),
      input: args,
      status: toolStatus(assembled, toolMessage),
    }
    const output = toolOutputText(assembled, toolMessage)
    if (output) chunk.output = output
    const display = outputIframeDisplay(toolMessage)
    if (display) chunk.display = display
    const diffData = maybeDiffFromArgs(args)
    if (diffData) chunk.diffData = diffData
    chunks.push(chunk)
  }

  return chunks
}

function humanUiMessage(
  raw: HumanMessage,
  msgId: string,
  parsed: ParsedStructuredInput,
  entity: StructuredEntity | undefined,
  resolveCreatedAt?: (messageId: string) => string | undefined
): Message | null {
  if (parsed.type === "entity") return null
  // Our own replies reach the transcript twice: once forwarded as thread
  // context, once as the `slack_thread_reply` call that sent them.
  if (entity?.senderType === "self") return null

  const content = (raw as unknown as { content?: unknown }).content
  const chunks = imageChunks(content)
  const text = parsed.content
  if (text.trim()) chunks.push({ kind: "text", text })
  if (!chunks.length) return null

  const { value: timestamp, isFallback: timestampIsFallback } =
    messageTimestamp(raw, msgId, resolveCreatedAt)
  return {
    id: msgId,
    author:
      parsed.type === "message" && parsed.senderKind === "system"
        ? "system"
        : "user",
    timestamp,
    timestampIsFallback,
    chunks,
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

interface HumanParseCacheEntry {
  base: ParsedStructuredInput
  entity: StructuredEntity | null
  withEntity?: {
    entity: StructuredEntity | undefined
    parsed: ParsedStructuredInput
  }
}

interface AiChunkCacheEntry {
  deps: Array<unknown>
  chunks: Array<Chunk>
}

interface UnitCacheEntry {
  deps: Array<unknown>
  message: Message | null
}

function depsEqual(a: Array<unknown>, b: Array<unknown>): boolean {
  if (a.length !== b.length) return false
  for (let i = 0; i < a.length; i += 1) {
    if (!Object.is(a[i], b[i])) return false
  }
  return true
}

/**
 * Convert the SDK's live projections into the dashboard chunk model so the
 * transcript streams (and hydrates) directly from the SDK instead of a
 * hand-rolled, server-mirrored adapter.
 *
 * - `messages` ({@link BaseMessage}[]) drives ordering, text, and reasoning.
 * - `toolCalls` ({@link AssembledToolCall}[], i.e. `stream.toolCalls`) drives
 *   each tool call's status and output — no `pendingTools` bookkeeping.
 * - `toolKind` / `title` stay a pure mapping of name+args (known at call time,
 *   already persisted) so the in-progress card renders instantly.
 * - `diffData` is derived from the call's own args, which is all that is
 *   available before an edit is applied (what a pending approval renders). What
 *   a turn actually changed comes from its persisted run diff.
 *
 * The returned projector is incremental: the SDK keeps object identity stable
 * for messages a stream flush did not touch, so each output unit (one user
 * message, or one agent turn spanning consecutive AI messages) is cached
 * against the references it was derived from and reused verbatim when none of
 * them changed. Stable `Message`/`Chunk` identities are what let the memoized
 * message components skip everything but the turn that is actually streaming.
 */
export function createUiMessageProjector(): (
  messages: Array<BaseMessage>,
  toolCalls?: ReadonlyArray<AssembledToolCall>,
  resolveCreatedAt?: (messageId: string) => string | undefined
) => Array<Message> {
  let prevUnits = new Map<string, UnitCacheEntry>()
  let prevResult: Array<Message> = []
  const humanParses = new WeakMap<BaseMessage, HumanParseCacheEntry>()
  const aiChunks = new WeakMap<BaseMessage, AiChunkCacheEntry>()

  return function project(messages, toolCalls = [], resolveCreatedAt?) {
    const toolCallsById = new Map<string, AssembledToolCall>()
    for (const toolCall of toolCalls) {
      const id = toolCall.id || toolCall.callId
      if (id) toolCallsById.set(id, toolCall)
    }

    const toolMessagesById = new Map<string, ToolMessage>()
    for (const raw of messages) {
      if (ToolMessage.isInstance(raw) && typeof raw.tool_call_id === "string") {
        toolMessagesById.set(raw.tool_call_id, raw)
      }
    }

    const baseParse = (raw: HumanMessage): HumanParseCacheEntry => {
      const cached = humanParses.get(raw)
      if (cached) return cached
      const base = parseStructuredInput(raw.text)
      const entry: HumanParseCacheEntry = {
        base,
        entity:
          base.type === "entity"
            ? {
                kind: base.kind,
                displayName: base.displayName,
                handle: base.handle,
                senderType: base.senderType,
                openSweAccount: base.openSweAccount,
              }
            : null,
      }
      humanParses.set(raw, entry)
      return entry
    }

    const structuredEntities = new Map<string, StructuredEntity>()
    for (const raw of messages) {
      if (!HumanMessage.isInstance(raw)) continue
      const { base, entity } = baseParse(raw)
      if (base.type === "entity" && entity)
        structuredEntities.set(base.id, entity)
    }

    // Sender kind falls back to the sender's entity, so the parse is keyed on
    // that entity's identity and only re-runs when the entity itself changed.
    const parseWithEntities = (raw: HumanMessage): ParsedStructuredInput => {
      const entry = baseParse(raw)
      if (entry.base.type !== "message") return entry.base
      const entity = structuredEntities.get(entry.base.sender)
      if (entry.withEntity && entry.withEntity.entity === entity) {
        return entry.withEntity.parsed
      }
      const parsed = parseStructuredInput(raw.text, structuredEntities)
      entry.withEntity = { entity, parsed }
      return parsed
    }

    const nextUnits = new Map<string, UnitCacheEntry>()
    const result: Array<Message> = []
    let turnKey: string | undefined

    const finishUnit = (
      key: string,
      deps: Array<unknown>,
      build: () => Message | null
    ) => {
      const cached = prevUnits.get(key)
      const entry =
        cached && depsEqual(cached.deps, deps)
          ? cached
          : { deps, message: build() }
      nextUnits.set(key, entry)
      if (entry.message) result.push(entry.message)
    }

    let turnRaws: Array<{
      raw: AIMessage
      index: number
      rawDeps: Array<unknown>
    }> = []
    let turnDeps: Array<unknown> = []

    const flushAgentTurn = () => {
      if (turnRaws.length === 0) return
      const raws = turnRaws
      const deps = turnDeps
      turnRaws = []
      turnDeps = []
      const first = raws[0]
      if (!first) return
      const firstId =
        typeof first.raw.id === "string" && first.raw.id
          ? first.raw.id
          : `msg-${first.index}`
      finishUnit(`a:${firstId}`, deps, () => {
        let turn: AgentTurn | null = null
        for (const { raw, index, rawDeps } of raws) {
          const cached = aiChunks.get(raw)
          let chunks: Array<Chunk>
          if (cached && depsEqual(cached.deps, rawDeps)) {
            chunks = cached.chunks
          } else {
            chunks = aiMessageChunks(
              raw,
              index,
              toolCallsById,
              toolMessagesById
            )
            aiChunks.set(raw, { deps: rawDeps, chunks })
          }
          if (!chunks.length) continue
          const msgId =
            typeof raw.id === "string" && raw.id ? raw.id : `msg-${index}`
          const { value: timestamp, isFallback: timestampIsFallback } =
            messageTimestamp(raw, msgId, resolveCreatedAt)
          if (!turn) {
            turn = {
              id: msgId,
              author: "agent",
              timestamp,
              turnKey: deps[0] as string | undefined,
              startedAt: timestamp,
              timestampIsFallback,
              chunks: [...chunks],
            }
          } else {
            turn.timestamp = timestamp
            turn.timestampIsFallback =
              turn.timestampIsFallback || timestampIsFallback
            turn.chunks.push(...chunks)
          }
        }
        if (!turn) return null
        return { ...turn, chunks: mergeTextChunks(turn.chunks) }
      })
    }

    messages.forEach((raw, index) => {
      if (HumanMessage.isInstance(raw)) {
        flushAgentTurn()
        turnKey = typeof raw.id === "string" ? raw.id : undefined
        const msgId =
          typeof raw.id === "string" && raw.id ? raw.id : `msg-${index}`
        const parsed = parseWithEntities(raw)
        const entity =
          parsed.type === "message"
            ? structuredEntities.get(parsed.sender)
            : undefined
        finishUnit(
          `h:${msgId}`,
          [raw, index, entity ?? null, resolveCreatedAt ?? null],
          () => humanUiMessage(raw, msgId, parsed, entity, resolveCreatedAt)
        )
        return
      }

      if (AIMessage.isInstance(raw)) {
        if (turnRaws.length === 0) {
          turnDeps = [turnKey, resolveCreatedAt ?? null]
        }
        const rawDeps: Array<unknown> = [index]
        for (const toolCall of raw.tool_calls ?? []) {
          const id = toolCall.id
          rawDeps.push(
            id ? (toolCallsById.get(id) ?? null) : null,
            id ? (toolMessagesById.get(id) ?? null) : null
          )
        }
        turnRaws.push({ raw, index, rawDeps })
        turnDeps.push(raw, ...rawDeps)
      }

      // `ToolMessage`s no longer produce their own chunk — their status/output
      // is attached to the originating tool-call chunk above via
      // `stream.toolCalls`, and their identity participates in each turn's
      // dependency list.
    })

    flushAgentTurn()
    prevUnits = nextUnits

    if (
      result.length === prevResult.length &&
      result.every((message, i) => message === prevResult[i])
    ) {
      return prevResult
    }
    prevResult = result
    return result
  }
}

/** One-shot projection; equivalent to a fresh projector applied once. */
export function streamMessagesToUi(
  messages: Array<BaseMessage>,
  toolCalls: ReadonlyArray<AssembledToolCall> = [],
  resolveCreatedAt?: (messageId: string) => string | undefined
): Array<Message> {
  return createUiMessageProjector()(messages, toolCalls, resolveCreatedAt)
}
