import {
  isRecord,
  parseJson,
  recordAt,
  stringArrayAt,
  stringAt,
} from "./json.ts"

export interface SseFrame {
  event: string | null
  id: string | null
  data: string
}

/** Incremental `text/event-stream` decoder: feed chunks, take whole frames. */
export class SseParser {
  private buffer = ""
  private event: string | null = null
  private id: string | null = null
  private data: string[] = []

  push(chunk: string): SseFrame[] {
    this.buffer += chunk
    const frames: SseFrame[] = []
    for (;;) {
      const newline = this.buffer.indexOf("\n")
      if (newline < 0) break
      const line = this.buffer.slice(0, newline).replace(/\r$/, "")
      this.buffer = this.buffer.slice(newline + 1)
      const frame = this.consume(line)
      if (frame !== null) frames.push(frame)
    }
    return frames
  }

  private consume(line: string): SseFrame | null {
    if (line === "") {
      if (this.data.length === 0 && this.event === null) {
        this.id = null
        return null
      }
      const frame: SseFrame = {
        event: this.event,
        id: this.id,
        data: this.data.join("\n"),
      }
      this.event = null
      this.id = null
      this.data = []
      return frame
    }
    if (line.startsWith(":")) return null
    const colon = line.indexOf(":")
    const field = colon < 0 ? line : line.slice(0, colon)
    const raw = colon < 0 ? "" : line.slice(colon + 1)
    const value = raw.startsWith(" ") ? raw.slice(1) : raw
    if (field === "event") this.event = value
    else if (field === "data") this.data.push(value)
    else if (field === "id") this.id = value
    return null
  }
}

export type RunStatus = "completed" | "failed" | "interrupted" | "closed"

export interface RunOutcome {
  status: RunStatus
  error: string | null
}

const TERMINAL: ReadonlySet<string> = new Set([
  "completed",
  "failed",
  "interrupted",
])
const SHELL_TOOLS: ReadonlySet<string> = new Set([
  "execute",
  "background_execute",
])
const PRIMARY_ARGS = [
  "command",
  "path",
  "file_path",
  "paths",
  "url",
  "query",
  "title",
  "description",
  "name",
  "prompt",
]
const MAX_ARG_CHARS = 120

/** The run id a synthesized root lifecycle event belongs to (`synth:<run>:lc|…`). */
export function lifecycleRunId(eventId: string | null): string | null {
  if (eventId === null) return null
  const parts = eventId.split(":", 3)
  if (parts.length !== 3 || parts[0] !== "synth") return null
  const runId = parts[1] ?? ""
  return runId && (parts[2] ?? "").startsWith("lc|") ? runId : null
}

export function oneLine(value: string, max = MAX_ARG_CHARS): string {
  const collapsed = value.replace(/\s+/g, " ").trim()
  return collapsed.length > max ? `${collapsed.slice(0, max)}…` : collapsed
}

export function toolLine(name: string, input: unknown): string {
  const record = isRecord(input) ? input : null
  if (SHELL_TOOLS.has(name)) {
    const command = stringAt(record, "command")
    if (command !== null) return `$ ${oneLine(command)}`
  }
  for (const key of PRIMARY_ARGS) {
    const value = record?.[key]
    if (typeof value === "string" && value) return `${name} ${oneLine(value)}`
    if (Array.isArray(value) && value.length > 0) {
      return `${name} ${oneLine(value.map(String).join(" "))}`
    }
  }
  return name
}

export interface Writer {
  write(text: string): void
  readonly colors: boolean
}

export const terminalWriter: Writer = {
  write(text: string) {
    process.stdout.write(text)
  },
  colors: process.stdout.isTTY === true,
}

/**
 * Renders one run's events. Everything before the run's own `running`
 * lifecycle is skipped, so a replayed thread does not reprint its history.
 */
export class EventRenderer {
  private open: boolean
  private streamingText = false

  constructor(
    private readonly runId: string | null,
    private readonly out: Writer = terminalWriter
  ) {
    this.open = runId === null
  }

  private dim(text: string): void {
    this.endText()
    this.out.write(this.out.colors ? `[2m${text}[0m\n` : `${text}\n`)
  }

  private endText(): void {
    if (!this.streamingText) return
    this.streamingText = false
    this.out.write("\n")
  }

  finish(outcome: RunOutcome): RunOutcome {
    this.endText()
    return outcome
  }

  handle(frame: SseFrame): RunOutcome | null {
    if (frame.event === "error") {
      const payload = parseJson(frame.data)
      const detail =
        stringAt(isRecord(payload) ? payload : null, "detail") ?? frame.data
      this.dim(`stream error: ${oneLine(detail, 200)}`)
      return this.finish({ status: "failed", error: detail })
    }
    const payload = parseJson(frame.data)
    if (!isRecord(payload)) return null
    const method = stringAt(payload, "method")
    const params = recordAt(payload, "params")
    const data = recordAt(params, "data")
    if (method === null || data === null) return null
    const namespace = stringArrayAt(params, "namespace") ?? []
    const phase = stringAt(data, "event")
    if (phase === null) return null

    if (method === "lifecycle" && namespace.length === 0) {
      return this.lifecycle(phase, data, stringAt(payload, "event_id"))
    }
    if (!this.open) return null
    if (method === "messages" && namespace.length === 0) {
      this.message(phase, data)
      return null
    }
    if (method === "tools") this.tool(phase, data, namespace.length)
    return null
  }

  private lifecycle(
    phase: string,
    data: Record<string, unknown>,
    eventId: string | null
  ): RunOutcome | null {
    const runId = lifecycleRunId(eventId)
    const mine = this.runId === null || runId === null || runId === this.runId
    if (phase === "running") {
      if (mine) this.open = true
      return null
    }
    if (!this.open || !mine || !TERMINAL.has(phase)) return null
    const error = stringAt(data, "error")
    if (phase === "failed") this.dim(`run failed${error ? `: ${error}` : ""}`)
    else if (phase === "interrupted") this.dim("run interrupted")
    else this.dim("run finished")
    return this.finish({
      status:
        phase === "completed"
          ? "completed"
          : phase === "failed"
            ? "failed"
            : "interrupted",
      error,
    })
  }

  private message(phase: string, data: Record<string, unknown>): void {
    if (phase === "message-start") {
      if (stringAt(data, "role") === "ai") this.streamingText = false
      return
    }
    if (phase === "message-finish") {
      this.endText()
      return
    }
    if (phase !== "content-block-delta") return
    const delta = recordAt(data, "delta")
    if (stringAt(delta, "type") !== "text-delta") return
    const text = stringAt(delta, "text")
    if (text === null || text === "") return
    this.streamingText = true
    this.out.write(text)
  }

  private tool(
    phase: string,
    data: Record<string, unknown>,
    depth: number
  ): void {
    const indent = "  ".repeat(Math.min(depth, 3))
    if (phase === "tool-started") {
      const name = stringAt(data, "tool_name")
      if (name === null) return
      this.dim(`${indent}${toolLine(name, data["input"])}`)
      return
    }
    if (phase === "tool-error") {
      const message = stringAt(data, "message") ?? "tool failed"
      this.dim(`${indent}! ${oneLine(message, 200)}`)
    }
  }
}

export async function renderRunEvents(
  response: Response,
  runId: string | null,
  out: Writer = terminalWriter
): Promise<RunOutcome> {
  const body = response.body
  if (body === null) return { status: "closed", error: "empty event stream" }
  const renderer = new EventRenderer(runId, out)
  const parser = new SseParser()
  const decoder = new TextDecoder()
  const reader = body.getReader()
  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      if (value === undefined) continue
      for (const frame of parser.push(
        decoder.decode(value, { stream: true })
      )) {
        const outcome = renderer.handle(frame)
        if (outcome !== null) return outcome
      }
    }
  } finally {
    void reader.cancel().catch(() => undefined)
  }
  return renderer.finish({ status: "closed", error: null })
}
