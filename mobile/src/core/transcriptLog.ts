import { isObject, stringAt, type JSONValue } from "./models"
import { TranscriptBuilder, type TranscriptItem } from "./transcript"

/** Wire types, snake_case as the server sends them. */
export interface TurnRow {
  turn_id: string
  run_id?: string | null
  state: string
  error?: string | null
}

export interface MessageRow {
  message_id: string
  role: string
  text: string
  namespace: string[]
  created_at: string
}

export interface ToolCallRow {
  tool_call_id: string
  name: string
  input: JSONValue
  status: string
  namespace: string[]
  started_at: string
}

export interface Snapshot {
  version: number
  thread: { status: string }
  turns: TurnRow[]
  messages: MessageRow[]
  tool_calls: ToolCallRow[]
  older_cursor?: string | null
}

/** Turns older than a cursor, from `GET …/transcript/turns?before=`. */
export interface TurnPage {
  turns: TurnRow[]
  messages: MessageRow[]
  tool_calls: ToolCallRow[]
  older_cursor?: string | null
}

export interface LogEvent {
  version: number
  event_type: string
  run_id?: string | null
  payload: JSONValue
}

/** What the transcript event stream sends. */
export type TranscriptStreamEvent =
  | { kind: "event"; event: LogEvent }
  /** The gap since `after` was too large to replay; start over from this. */
  | { kind: "snapshot"; snapshot: Snapshot }
  /** Replay is done and the stream is live. */
  | { kind: "synchronized" }
  /** The thread is gone or no longer readable; the server ends the stream. */
  | { kind: "ended" }

interface Turn {
  id: string
  state: string
  error?: string
  /** Set once a run serves the turn, or as soon as one is queued for it. */
  runID?: string
  /**
   * Whether this session saw the turn change on the live stream. A turn the
   * snapshot left open may be one whose run died without closing it.
   */
  seenLive: boolean
}

type Entry = { kind: "message"; id: string } | { kind: "tool"; id: string }

function turnFromRow(row: TurnRow): Turn {
  return {
    id: row.turn_id,
    state: row.state,
    error: row.error ?? undefined,
    runID: row.run_id ?? undefined,
    seenLive: false,
  }
}

function isActive(turn: Turn): boolean {
  return (
    turn.state === "running" ||
    (turn.state === "requested" && turn.runID !== undefined)
  )
}

/**
 * A thread's transcript as the server's append-only event log describes it:
 * loaded from `GET …/transcript`, then kept current by applying the events
 * `GET …/transcript/events` streams.
 *
 * Only the root conversation is kept; subagents' messages and tool calls
 * (a non-empty `namespace`) stay inside the task that ran them.
 */
export class TranscriptLog {
  /** The head of the log; a live subscription resumes after it. */
  version: number
  threadStatus: string
  /** The page of turns before the loaded ones, or null once the first turn is loaded. */
  olderCursor: string | null
  private turns: Turn[]
  private entries: Entry[]
  private messages = new Map<string, { role: string; text: string }>()
  private tools = new Map<
    string,
    { name: string; input: JSONValue; status: string }
  >()

  constructor(snapshot: Snapshot) {
    this.version = snapshot.version
    this.threadStatus = snapshot.thread.status
    this.olderCursor = snapshot.older_cursor ?? null
    this.turns = snapshot.turns.map(turnFromRow)
    this.entries = this.index(snapshot.messages, snapshot.tool_calls)
  }

  /** Add a page of older turns ahead of the loaded ones. */
  prepend(page: TurnPage) {
    const known = new Set(this.turns.map((turn) => turn.id))
    this.turns = [
      ...page.turns.map(turnFromRow).filter((turn) => !known.has(turn.id)),
      ...this.turns,
    ]
    this.entries = [
      ...this.index(page.messages, page.tool_calls),
      ...this.entries,
    ]
    this.olderCursor = page.older_cursor ?? null
  }

  /**
   * Record messages and tool calls, returning their entries in the order they
   * happened: the server lists the two apart, so their timestamps interleave them.
   */
  private index(rows: MessageRow[], calls: ToolCallRow[]): Entry[] {
    const timed: [string, number, Entry][] = []
    rows.forEach((row, offset) => {
      if (row.namespace.length > 0 || this.messages.has(row.message_id)) return
      this.messages.set(row.message_id, { role: row.role, text: row.text })
      timed.push([
        row.created_at,
        offset,
        { kind: "message", id: row.message_id },
      ])
    })
    calls.forEach((row, offset) => {
      if (row.namespace.length > 0 || this.tools.has(row.tool_call_id)) return
      this.tools.set(row.tool_call_id, {
        name: row.name,
        input: row.input,
        status: row.status,
      })
      timed.push([
        row.started_at,
        rows.length + offset,
        { kind: "tool", id: row.tool_call_id },
      ])
    })
    timed.sort((a, b) => (a[0] === b[0] ? a[1] - b[1] : a[0] < b[0] ? -1 : 1))
    return timed.map((entry) => entry[2])
  }

  /**
   * Whether the newest turn has ended. Positive evidence a run is over, which a
   * thread status still saying running cannot outweigh.
   */
  get latestTurnEnded(): boolean {
    const last = this.turns[this.turns.length - 1]
    return (
      last !== undefined &&
      ["completed", "failed", "interrupted"].includes(last.state)
    )
  }

  /**
   * Whether a turn this session watched start on the live stream is still open.
   * The snapshot can hold a turn whose run died without closing it; one seen
   * live is closed by the stream itself.
   */
  get hasLiveActiveTurn(): boolean {
    return this.turns.some((turn) => turn.seenLive && isActive(turn))
  }

  /** The error of the newest turn, if it failed. */
  get failure(): string | null {
    const last = this.turns[this.turns.length - 1]
    if (!last || last.state !== "failed") return null
    return last.error ?? "The run failed."
  }

  /**
   * Apply one streamed event. Returns whether a turn started or finished, so
   * the caller can refresh what that changes (status, pull request, diff).
   */
  apply(event: LogEvent): boolean {
    if (event.version <= this.version) return false
    this.version = event.version
    const payload = event.payload
    const turnID = stringAt(payload, "turn_id") ?? ""
    const namespace = isObject(payload) ? payload.namespace : undefined
    const rootNamespace = !Array.isArray(namespace) || namespace.length === 0
    const runID = event.run_id ?? undefined
    switch (event.event_type) {
      case "turn.requested": {
        this.setTurn(turnID, "requested", undefined, runID)
        const messageID = stringAt(payload, "message_id")
        if (messageID)
          this.upsertMessage(
            messageID,
            "human",
            stringAt(payload, "text") ?? ""
          )
        break
      }
      case "turn.queued":
        this.setTurn(
          turnID,
          "requested",
          undefined,
          stringAt(payload, "run_id") ?? runID
        )
        break
      case "turn.started":
        this.setTurn(turnID, "running", undefined, runID)
        this.threadStatus = "running"
        return true
      case "turn.completed":
      case "turn.interrupted":
        this.setTurn(turnID, event.event_type.slice("turn.".length))
        this.threadStatus = "idle"
        return true
      case "turn.failed":
        this.setTurn(turnID, "failed", stringAt(payload, "error"))
        this.threadStatus = "error"
        return true
      case "message.appended": {
        const messageID = stringAt(payload, "message_id")
        if (!rootNamespace || !messageID) break
        const fragment = stringAt(payload, "text") ?? ""
        this.upsertMessage(
          messageID,
          "ai",
          (this.messages.get(messageID)?.text ?? "") + fragment
        )
        break
      }
      case "message.completed": {
        const messageID = stringAt(payload, "message_id")
        if (!rootNamespace || !messageID) break
        this.upsertMessage(
          messageID,
          stringAt(payload, "role") ?? "ai",
          stringAt(payload, "text") ?? ""
        )
        break
      }
      case "tool.started": {
        const callID = stringAt(payload, "tool_call_id")
        if (!rootNamespace || !callID) break
        if (!this.tools.has(callID))
          this.entries.push({ kind: "tool", id: callID })
        this.tools.set(callID, {
          name: stringAt(payload, "name") ?? "tool",
          input: isObject(payload) ? (payload.input ?? null) : null,
          status: "in_progress",
        })
        break
      }
      case "tool.completed": {
        const call = this.tools.get(stringAt(payload, "tool_call_id") ?? "")
        if (call) call.status = stringAt(payload, "status") ?? "completed"
        break
      }
    }
    return false
  }

  private setTurn(id: string, state: string, error?: string, runID?: string) {
    if (!id) return
    const turn = this.turns.find((candidate) => candidate.id === id)
    if (turn) {
      turn.state = state
      turn.seenLive = true
      if (error !== undefined) turn.error = error
      if (runID !== undefined) turn.runID = runID
    } else {
      this.turns.push({ id, state, error, runID, seenLive: true })
    }
  }

  private upsertMessage(id: string, role: string, text: string) {
    if (!this.messages.has(id)) this.entries.push({ kind: "message", id })
    this.messages.set(id, { role, text })
  }

  /** The items the transcript view draws. */
  transcript(): TranscriptItem[] {
    const builder = new TranscriptBuilder()
    for (const entry of this.entries) {
      if (entry.kind === "message") {
        const message = this.messages.get(entry.id)
        if (!message) continue
        if (message.role === "human") builder.user(entry.id, message.text)
        else builder.assistant(entry.id, message.text)
      } else {
        const call = this.tools.get(entry.id)
        if (call) builder.tool(entry.id, call.name, call.input)
      }
    }
    return builder.finish()
  }
}
