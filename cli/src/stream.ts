import {
  isRecord,
  numberAt,
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

/** What the agent handed the terminal through the result tool. */
export interface CliResult {
  stdout: string
  exitCode: number
}

export interface RunOutcome {
  status: RunStatus
  error: string | null
  result: CliResult | null
}

export const RESULT_TOOL = "cli_result"
const MAX_EXIT_CODE = 255

const TERMINAL: ReadonlySet<string> = new Set([
  "completed",
  "failed",
  "interrupted",
])

/** The run id a synthesized root lifecycle event belongs to (`synth:<run>:lc|…`). */
export function lifecycleRunId(eventId: string | null): string | null {
  if (eventId === null) return null
  const parts = eventId.split(":", 3)
  if (parts.length !== 3 || parts[0] !== "synth") return null
  const runId = parts[1] ?? ""
  return runId && (parts[2] ?? "").startsWith("lc|") ? runId : null
}

export function parseCliResult(input: unknown): CliResult | null {
  const record = isRecord(input) ? input : null
  const stdout = stringAt(record, "stdout")
  const exitCode = numberAt(record, "exit_code")
  if (stdout === null || exitCode === null) return null
  if (!Number.isInteger(exitCode) || exitCode < 0 || exitCode > MAX_EXIT_CODE)
    return null
  return { stdout, exitCode }
}

/**
 * Follows one run's events and keeps only the last result the top-level agent
 * supplied. Everything before the run's own `running` lifecycle is skipped, so
 * a replayed thread's earlier results are never mistaken for this run's.
 */
export class RunCollector {
  private open: boolean
  private result: CliResult | null = null

  constructor(private readonly runId: string | null) {
    this.open = runId === null
  }

  finish(status: RunStatus, error: string | null): RunOutcome {
    return { status, error, result: this.result }
  }

  handle(frame: SseFrame): RunOutcome | null {
    if (frame.event === "error") {
      const payload = parseJson(frame.data)
      const detail =
        stringAt(isRecord(payload) ? payload : null, "detail") ?? frame.data
      return this.finish("failed", detail)
    }
    const payload = parseJson(frame.data)
    if (!isRecord(payload)) return null
    const method = stringAt(payload, "method")
    const params = recordAt(payload, "params")
    const data = recordAt(params, "data")
    if (method === null || data === null) return null
    if ((stringArrayAt(params, "namespace") ?? []).length > 0) return null
    const phase = stringAt(data, "event")
    if (phase === null) return null

    if (method === "lifecycle") {
      return this.lifecycle(phase, data, stringAt(payload, "event_id"))
    }
    if (
      this.open &&
      method === "tools" &&
      phase === "tool-started" &&
      stringAt(data, "tool_name") === RESULT_TOOL
    ) {
      this.result = parseCliResult(data["input"]) ?? this.result
    }
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
    return this.finish(
      phase === "completed"
        ? "completed"
        : phase === "failed"
          ? "failed"
          : "interrupted",
      stringAt(data, "error")
    )
  }
}

export async function collectRun(
  response: Response,
  runId: string | null
): Promise<RunOutcome> {
  const collector = new RunCollector(runId)
  const body = response.body
  if (body === null) return collector.finish("closed", "empty event stream")
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
        const outcome = collector.handle(frame)
        if (outcome !== null) return outcome
      }
    }
  } finally {
    void reader.cancel().catch(() => undefined)
  }
  return collector.finish("closed", null)
}
