import { describe, expect, test } from "bun:test"

import {
  EventRenderer,
  lifecycleRunId,
  SseParser,
  toolLine,
  type SseFrame,
  type Writer,
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

describe("toolLine", () => {
  test("renders shell tools as a shell prompt", () => {
    expect(toolLine("execute", { command: "pytest -q\nmore" })).toBe(
      "$ pytest -q more"
    )
    expect(toolLine("background_execute", { command: "npm run dev" })).toBe(
      "$ npm run dev"
    )
  })

  test("renders other tools with their primary argument", () => {
    expect(toolLine("read_file", { path: "agent/server.py" })).toBe(
      "read_file agent/server.py"
    )
    expect(toolLine("download_files", { paths: ["a.txt", "b.txt"] })).toBe(
      "download_files a.txt b.txt"
    )
    expect(toolLine("save_plan", {})).toBe("save_plan")
  })
})

function frame(payload: unknown): SseFrame {
  return { event: null, id: null, data: JSON.stringify(payload) }
}

function recorder(): { writer: Writer; text: () => string } {
  const parts: string[] = []
  return {
    writer: {
      write(text: string) {
        parts.push(text)
      },
      colors: false,
    },
    text: () => parts.join(""),
  }
}

describe("EventRenderer", () => {
  test("skips replayed events until its own run starts", () => {
    const { writer, text } = recorder()
    const renderer = new EventRenderer("run-2", writer)
    renderer.handle(
      frame({
        method: "messages",
        params: {
          namespace: [],
          data: {
            event: "content-block-delta",
            delta: { type: "text-delta", text: "old" },
          },
        },
      })
    )
    expect(text()).toBe("")
    renderer.handle(
      frame({
        method: "lifecycle",
        event_id: "synth:run-2:lc|1",
        params: { namespace: [], data: { event: "running" } },
      })
    )
    renderer.handle(
      frame({
        method: "messages",
        params: {
          namespace: [],
          data: {
            event: "content-block-delta",
            delta: { type: "text-delta", text: "new" },
          },
        },
      })
    )
    expect(text()).toBe("new")
  })

  test("renders tool lines and returns the terminal outcome", () => {
    const { writer, text } = recorder()
    const renderer = new EventRenderer(null, writer)
    renderer.handle(
      frame({
        method: "tools",
        params: {
          namespace: [],
          data: {
            event: "tool-started",
            tool_name: "execute",
            input: { command: "ls" },
          },
        },
      })
    )
    renderer.handle(
      frame({
        method: "tools",
        params: {
          namespace: ["sub"],
          data: {
            event: "tool-started",
            tool_name: "execute",
            input: { command: "pwd" },
          },
        },
      })
    )
    const outcome = renderer.handle(
      frame({
        method: "lifecycle",
        event_id: "synth:run-9:lc|1",
        params: { namespace: [], data: { event: "completed" } },
      })
    )
    expect(outcome).toEqual({ status: "completed", error: null })
    expect(text()).toBe("$ ls\n  $ pwd\nrun finished\n")
  })

  test("surfaces a proxy error frame", () => {
    const { writer, text } = recorder()
    const renderer = new EventRenderer(null, writer)
    const outcome = renderer.handle({
      event: "error",
      id: null,
      data: JSON.stringify({ status: 500, detail: "boom" }),
    })
    expect(outcome).toEqual({ status: "failed", error: "boom" })
    expect(text()).toBe("stream error: boom\n")
  })
})
