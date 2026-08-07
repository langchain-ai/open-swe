import { AIMessage, ToolMessage } from "@langchain/core/messages"

import { sanitizeAgentText } from "./sanitizeText"
import { toolTitle } from "./streamMessagesToUi"
import type { BaseMessage } from "@langchain/core/messages"
import type { SubagentInfo, ToolExecutionChunk } from "./types"

export interface SubagentStep {
  id: string
  /** Raw tool name, e.g. `execute`. */
  name: string
  /** What the step actually did — the command or path, not just the tool. */
  label: string
  status: "running" | "completed" | "error"
}

/**
 * A subagent's tool calls, derived from the messages on its namespace.
 *
 * The SDK's `tools` projection is the natural source, but the server emits no
 * `tools` events for a subagent namespace — only `messages` — so the steps are
 * reassembled here the same way the root transcript does it: tool calls come
 * from the subagent's AI messages, and their outcome from the `ToolMessage`
 * carrying the matching `tool_call_id`.
 */
export function subagentSteps(messages: ReadonlyArray<BaseMessage>): Array<SubagentStep> {
  const results = new Map<string, ToolMessage>()
  for (const message of messages) {
    if (ToolMessage.isInstance(message) && typeof message.tool_call_id === "string") {
      results.set(message.tool_call_id, message)
    }
  }

  const steps: Array<SubagentStep> = []
  for (const message of messages) {
    if (!AIMessage.isInstance(message)) continue
    for (const call of message.tool_calls ?? []) {
      const id = call.id
      if (!id) continue
      const result = results.get(id)
      steps.push({
        id,
        name: call.name,
        label: sanitizeAgentText(toolTitle(call.name, call.args ?? {})),
        status: !result ? "running" : result.status === "error" ? "error" : "completed",
      })
    }
  }
  return steps
}

/** Longest a card's inline result/error preview runs before it is clipped. */
const RESULT_PREVIEW_CHARS = 280

/**
 * `stalled` is not a lifecycle the SDK reports: it is a subagent left mid-run
 * by a run that ended (crash, step limit, reload of a dead thread). Without it
 * the card spins forever, claiming work is in flight that nothing is doing.
 */
export type SubagentDisplayStatus = "running" | "stalled" | "completed" | "error"

export function subagentDisplayStatus(
  chunk: ToolExecutionChunk,
  runActive = true,
): SubagentDisplayStatus {
  if (chunk.status === "error") return "error"
  if (chunk.status === "completed") return "completed"
  return runActive ? "running" : "stalled"
}

export function isTerminalSubagentStatus(status: SubagentDisplayStatus): boolean {
  return status !== "running"
}

function parseTime(value: string | undefined): number | undefined {
  if (!value) return undefined
  const ms = Date.parse(value)
  return Number.isNaN(ms) ? undefined : ms
}

/**
 * Wall-clock duration of a subagent, measured to whichever end is earlier: the
 * snapshot's `completedAt` or the caller's `now`.
 *
 * Neither alone is right. The snapshot only stamps `completedAt` once the
 * parent graph checkpoints — after every sibling has returned — so on its own
 * it reports the whole fan-out's duration for a subagent that finished first.
 * A caller that freezes `now` at the moment it observed completion is more
 * accurate there, but too high when the card mounted after the fact. The
 * earlier of the two is the one that saw the subagent stop.
 */
export function subagentElapsedMs(info: SubagentInfo | undefined, now: number): number | undefined {
  const startedAt = parseTime(info?.startedAt)
  if (startedAt === undefined) return undefined
  const completedAt = parseTime(info?.completedAt)
  const endedAt = completedAt === undefined ? now : Math.min(completedAt, now)
  const elapsed = endedAt - startedAt
  return elapsed >= 0 ? elapsed : undefined
}

/**
 * Compact duration: sub-minute reads in seconds with one decimal (so a fast
 * subagent still visibly ticks), minute-plus switches to `m ss`.
 */
export function formatElapsed(ms: number): string {
  if (ms < 1000) return `${Math.max(0, Math.round(ms))}ms`
  const seconds = ms / 1000
  if (seconds < 60) return `${seconds.toFixed(1)}s`
  const minutes = Math.floor(seconds / 60)
  const rest = Math.floor(seconds % 60)
  return `${minutes}m ${String(rest).padStart(2, "0")}s`
}

/** Subagent type for display, falling back to the generic label. */
export function subagentLabel(chunk: ToolExecutionChunk): string {
  const fromInput =
    typeof chunk.input?.subagent_type === "string" ? chunk.input.subagent_type.trim() : ""
  return sanitizeAgentText(fromInput || chunk.subagent?.name?.trim() || "subagent")
}

/** The task description the parent handed to the subagent. */
export function subagentDescription(chunk: ToolExecutionChunk): string {
  const value = chunk.input?.description
  return typeof value === "string" ? sanitizeAgentText(value.trim()) : ""
}

/**
 * What a finished card shows below its activity: the failure when there is
 * one, otherwise the subagent's returned result.
 */
export function subagentOutcome(
  chunk: ToolExecutionChunk,
): { kind: "error" | "result"; text: string } | undefined {
  const error = chunk.subagent?.error?.trim()
  if (error) return { kind: "error", text: truncate(error) }
  const result = (chunk.subagent?.result ?? chunk.output)?.trim()
  if (result) return { kind: "result", text: truncate(result) }
  return undefined
}

function truncate(text: string): string {
  const safe = sanitizeAgentText(text)
  if (safe.length <= RESULT_PREVIEW_CHARS) return safe
  return `${safe.slice(0, RESULT_PREVIEW_CHARS).trimEnd()}…`
}

export interface SubagentGroupSummary {
  total: number
  /** Members in a terminal state, whether they succeeded or failed. */
  finished: number
  running: number
  failed: number
  /**
   * Wall-clock for the group: the slowest member, not the sum — members run
   * concurrently, so adding their durations would overstate the fan-out.
   */
  elapsedMs: number | undefined
  headline: string
}

/**
 * Rollup shown on a group's header row: dcode's `done/total` counter with
 * t3code's tense switch, so an in-flight fan-out reads differently from a
 * settled one.
 */
export function summarizeSubagentGroup(
  chunks: ReadonlyArray<ToolExecutionChunk>,
  now: number,
  runActive = true,
  // Statuses each card derived from its own message stream, which settles
  // per-subagent instead of all at once (see `subagentMessagesDone`).
  reported: ReadonlyMap<string, SubagentDisplayStatus> = new Map(),
): SubagentGroupSummary {
  let running = 0
  let failed = 0
  let finished = 0
  let elapsedMs: number | undefined

  for (const chunk of chunks) {
    const status = reported.get(chunk.toolCallId) ?? subagentDisplayStatus(chunk, runActive)
    if (status === "running") running += 1
    if (status === "error") failed += 1
    if (status === "completed" || status === "error") finished += 1
    const chunkElapsed = subagentElapsedMs(chunk.subagent, now)
    if (chunkElapsed !== undefined) elapsedMs = Math.max(elapsedMs ?? 0, chunkElapsed)
  }

  const total = chunks.length
  const noun = total === 1 ? "subagent" : "subagents"
  const headline =
    running > 0 ? `Running ${finished}/${total} ${noun}` : `Ran ${total} ${noun}`

  return { total, finished, running, failed, elapsedMs, headline }
}


/**
 * Whether a subagent's own messages show it has finished.
 *
 * The SDK's snapshot only flips to `complete` when the *parent* graph
 * checkpoints, which happens after every parallel `task` has returned — so
 * every card in a fan-out settles at once and each reports the whole run's
 * duration. A subagent's own loop ends when it emits a message with no further
 * tool calls, which is both earlier and per-subagent.
 */
export function subagentMessagesDone(messages: ReadonlyArray<BaseMessage>): boolean {
  const last = messages[messages.length - 1]
  if (last == null || !AIMessage.isInstance(last)) return false
  return (last.tool_calls?.length ?? 0) === 0
}

/**
 * A finished subagent's answer: the text of its final message.
 *
 * Preferred over the discovery snapshot's `output`, which is the raw
 * `ToolMessage` the parent received — an envelope of ids and status fields
 * wrapped around the same text.
 */
export function subagentResultFromMessages(
  messages: ReadonlyArray<BaseMessage>,
): string | undefined {
  if (!subagentMessagesDone(messages)) return undefined
  const last = messages[messages.length - 1]
  const text = last?.text?.trim()
  return text ? sanitizeAgentText(text) : undefined
}
