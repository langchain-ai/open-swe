import { describe, expect, test } from "bun:test"

import { ApiError } from "../src/api.ts"
import { CredentialError } from "../src/credentials.ts"
import {
  followRun,
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
        data: JSON.stringify({ status: 404, detail: "boom" }),
      })
    ).toEqual({ status: "failed", error: "boom", result: null })
  })

  test("treats an upstream 5xx error frame as a dropped stream", () => {
    const collector = new RunCollector(null)
    expect(
      collector.handle({
        event: "error",
        id: null,
        data: JSON.stringify({ status: 502, detail: "bad gateway" }),
      })
    ).toEqual({ status: "closed", error: "bad gateway", result: null })
  })
})

function sse(frames: readonly SseFrame[]): Response {
  return new Response(
    frames
      .map((f) => `${f.event ? `event: ${f.event}\n` : ""}data: ${f.data}\n\n`)
      .join("")
  )
}

describe("followRun", () => {
  const noSleep = async (): Promise<void> => undefined

  test("reconnects after a drop and reads the run from the replay", async () => {
    const streams = [
      [lifecycle("run-1", "running")],
      [
        lifecycle("run-1", "running"),
        resultCall({ stdout: "done", exit_code: 0 }),
        lifecycle("run-1", "completed"),
      ],
    ]
    let opened = 0
    const reconnects: number[] = []
    const outcome = await followRun(
      async () => sse(streams[opened++] ?? []),
      "run-1",
      { sleep: noSleep, onReconnect: (attempt) => reconnects.push(attempt) }
    )
    expect(outcome).toEqual({
      status: "completed",
      error: null,
      result: { stdout: "done", exitCode: 0 },
    })
    expect(reconnects).toEqual([1])
  })

  test("reconnects through a backend that is briefly unavailable", async () => {
    let opened = 0
    const outcome = await followRun(
      async () => {
        opened += 1
        if (opened === 1) throw new ApiError(503, "restarting")
        if (opened === 2) throw new TypeError("fetch failed")
        return sse([
          lifecycle("run-1", "running"),
          lifecycle("run-1", "completed"),
        ])
      },
      "run-1",
      { sleep: noSleep }
    )
    expect(outcome.status).toBe("completed")
    expect(opened).toBe(3)
  })

  test("gives up after repeated short-lived drops", async () => {
    let opened = 0
    const outcome = await followRun(
      async () => {
        opened += 1
        return sse([])
      },
      "run-1",
      { sleep: noSleep, now: () => 0 }
    )
    expect(outcome.status).toBe("closed")
    expect(opened).toBe(11)
  })

  test("reconnects on a rate-limited error frame", async () => {
    const streams = [
      [
        {
          event: "error",
          id: null,
          data: JSON.stringify({ status: 429, detail: "slow down" }),
        },
      ],
      [lifecycle("run-1", "running"), lifecycle("run-1", "completed")],
    ]
    let opened = 0
    const outcome = await followRun(
      async () => sse(streams[opened++] ?? []),
      "run-1",
      { sleep: noSleep }
    )
    expect(outcome.status).toBe("completed")
    expect(opened).toBe(2)
  })

  test("does not retry a credential the CLI could not obtain", async () => {
    let opened = 0
    const failing = followRun(
      async () => {
        opened += 1
        throw new CredentialError("GitHub Actions refused an OIDC token")
      },
      "run-1",
      { sleep: noSleep }
    )
    await expect(failing).rejects.toBeInstanceOf(CredentialError)
    expect(opened).toBe(1)
  })

  test("does not retry a rejected credential", async () => {
    let opened = 0
    const failing = followRun(
      async () => {
        opened += 1
        throw new ApiError(401, "unauthorized")
      },
      "run-1",
      { sleep: noSleep }
    )
    await expect(failing).rejects.toBeInstanceOf(ApiError)
    expect(opened).toBe(1)
  })
})
