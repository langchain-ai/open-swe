import { describe, expect, test } from "bun:test"

import {
  lifecycleRunId,
  parseCliResult,
  RunCollector,
  SseParser,
  type SseFrame,
} from "../src/stream.ts"

function collect(chunks: readonly string[]): SseFrame[] {
  const parser = new SseParser()
  return chunks.flatMap((chunk) => parser.push(chunk))
}

describe("SseParser", () => {
  test("parses a frame split across chunks", () => {
    expect(collect(['data: {"a"', ": 1}\n", "\n"])).toEqual([
      { event: null, id: null, data: '{"a": 1}' },
    ])
  })

  test("joins multi-line data and keeps the event name and id", () => {
    expect(collect(["event: error\nid: 7\ndata: one\ndata: two\n\n"])).toEqual([
      { event: "error", id: "7", data: "one\ntwo" },
    ])
  })

  test("handles CRLF line endings and comment lines", () => {
    expect(collect([": keep-alive\r\ndata: hi\r\n\r\n"])).toEqual([
      { event: null, id: null, data: "hi" },
    ])
  })

  test("treats a value-less field and no leading space alike", () => {
    expect(collect(["data:hi\ndata\n\n"])).toEqual([
      { event: null, id: null, data: "hi\n" },
    ])
  })

  test("emits nothing for blank separators between frames", () => {
    expect(collect(["\n\n", "data: x\n\n"])).toEqual([
      { event: null, id: null, data: "x" },
    ])
  })

  test("yields each frame of a multi-frame chunk in order", () => {
    expect(collect(["data: one\n\ndata: two\n\n"]).map((f) => f.data)).toEqual([
      "one",
      "two",
    ])
  })
})

describe("lifecycleRunId", () => {
  test("reads the run id out of a synthesized lifecycle event id", () => {
    expect(lifecycleRunId("synth:run-1:lc|abc")).toBe("run-1")
  })

  test("ignores other event ids", () => {
    expect(lifecycleRunId("synth:run-1:other")).toBeNull()
    expect(lifecycleRunId("real:run-1:lc|abc")).toBeNull()
    expect(lifecycleRunId(null)).toBeNull()
  })
})

describe("parseCliResult", () => {
  test("reads stdout and the exit code", () => {
    expect(parseCliResult({ stdout: "ok\n", exit_code: 3 })).toEqual({
      stdout: "ok\n",
      exitCode: 3,
    })
  })

  test("rejects an exit code a process cannot return", () => {
    expect(parseCliResult({ stdout: "", exit_code: 256 })).toBeNull()
    expect(parseCliResult({ stdout: "", exit_code: -1 })).toBeNull()
    expect(parseCliResult({ stdout: "", exit_code: 1.5 })).toBeNull()
    expect(parseCliResult({ exit_code: 0 })).toBeNull()
  })
})

function frame(payload: unknown): SseFrame {
  return { event: null, id: null, data: JSON.stringify(payload) }
}

function lifecycle(runId: string, event: string): SseFrame {
  return frame({
    method: "lifecycle",
    event_id: `synth:${runId}:lc|1`,
    params: { namespace: [], data: { event } },
  })
}

function resultCall(
  input: Record<string, unknown>,
  namespace: string[] = []
): SseFrame {
  return frame({
    method: "tools",
    params: {
      namespace,
      data: { event: "tool-started", tool_name: "cli_result", input },
    },
  })
}

describe("RunCollector", () => {
  test("ignores results replayed from before its own run", () => {
    const collector = new RunCollector("run-2")
    collector.handle(resultCall({ stdout: "old", exit_code: 0 }))
    collector.handle(lifecycle("run-2", "running"))
    expect(collector.handle(lifecycle("run-2", "completed"))).toEqual({
      status: "completed",
      error: null,
      result: null,
    })
  })

  test("keeps the top-level agent's last result, not a subagent's", () => {
    const collector = new RunCollector("run-1")
    collector.handle(lifecycle("run-1", "running"))
    collector.handle(resultCall({ stdout: "first", exit_code: 1 }))
    collector.handle(resultCall({ stdout: "second", exit_code: 0 }))
    collector.handle(resultCall({ stdout: "sub", exit_code: 9 }, ["task:1"]))
    expect(collector.handle(lifecycle("run-1", "completed"))).toEqual({
      status: "completed",
      error: null,
      result: { stdout: "second", exitCode: 0 },
    })
  })

  test("surfaces a proxy error frame", () => {
    const collector = new RunCollector(null)
    expect(
      collector.handle({
        event: "error",
        id: null,
        data: JSON.stringify({ status: 500, detail: "boom" }),
      })
    ).toEqual({ status: "failed", error: "boom", result: null })
  })
})
