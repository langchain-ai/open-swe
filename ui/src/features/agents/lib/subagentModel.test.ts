import { AIMessage, ToolMessage } from "@langchain/core/messages"
import { describe, expect, it } from "vitest"

import {
  formatElapsed,
  subagentDescription,
  subagentDisplayStatus,
  subagentElapsedMs,
  subagentLabel,
  subagentMessagesDone,
  subagentOutcome,
  subagentResultFromMessages,
  subagentSteps,
  summarizeSubagentGroup,
} from "./subagentModel"
import type { SubagentInfo, ToolExecutionChunk } from "./types"

const START = Date.parse("2026-08-07T12:00:00.000Z")

function chunk(
  overrides: Partial<ToolExecutionChunk> = {}
): ToolExecutionChunk {
  return {
    kind: "tool-execution",
    toolCallId: "call-1",
    title: "task",
    toolKind: "task",
    status: "in_progress",
    ...overrides,
  }
}

function subagent(overrides: Partial<SubagentInfo> = {}): SubagentInfo {
  return {
    namespace: ["tools:call-1"],
    name: "researcher",
    depth: 1,
    parentId: null,
    startedAt: new Date(START).toISOString(),
    ...overrides,
  }
}

describe("subagentDisplayStatus", () => {
  it("maps chunk status onto the subagent states", () => {
    expect(subagentDisplayStatus(chunk({ status: "in_progress" }))).toBe(
      "running"
    )
    expect(subagentDisplayStatus(chunk({ status: "pending" }))).toBe("running")
    expect(subagentDisplayStatus(chunk({ status: "completed" }))).toBe(
      "completed"
    )
    expect(subagentDisplayStatus(chunk({ status: "error" }))).toBe("error")
  })

  it("calls an unfinished subagent stalled once its run is no longer active", () => {
    expect(subagentDisplayStatus(chunk({ status: "in_progress" }), false)).toBe(
      "stalled"
    )
  })

  it("leaves a settled subagent alone when the run ends", () => {
    expect(subagentDisplayStatus(chunk({ status: "completed" }), false)).toBe(
      "completed"
    )
    expect(subagentDisplayStatus(chunk({ status: "error" }), false)).toBe(
      "error"
    )
  })
})

describe("sanitization of agent-authored text", () => {
  const RLO = String.fromCharCode(0x202e)
  const PDF = String.fromCharCode(0x202c)
  const ESC = String.fromCharCode(0x1b)
  const ZWSP = String.fromCharCode(0x200b)

  it("strips bidi overrides from the description so it cannot render reversed", () => {
    const item = chunk({
      input: { description: `delete ${RLO}nothing${PDF} here` },
    })
    expect(subagentDescription(item)).toBe("delete nothing here")
  })

  it("strips escapes and zero-width characters from the result", () => {
    const item = chunk({
      subagent: subagent({ result: `ok${ESC}[31m${ZWSP}done` }),
    })
    expect(subagentOutcome(item)).toEqual({
      kind: "result",
      text: "ok[31mdone",
    })
  })

  it("keeps newlines and tabs, which carry meaning in agent output", () => {
    const item = chunk({ input: { description: "line one\n\tindented" } })
    expect(subagentDescription(item)).toBe("line one\n\tindented")
  })
})

describe("subagentElapsedMs", () => {
  it("measures a running subagent against now", () => {
    expect(subagentElapsedMs(subagent(), START + 4500)).toBe(4500)
  })

  it("freezes a finished subagent at completedAt, ignoring a later now", () => {
    const info = subagent({ completedAt: new Date(START + 2000).toISOString() })
    expect(subagentElapsedMs(info, START + 90_000)).toBe(2000)
  })

  it("prefers an observed stop over a completedAt stamped once the fan-out ended", () => {
    const info = subagent({
      completedAt: new Date(START + 23_000).toISOString(),
    })
    expect(subagentElapsedMs(info, START + 13_000)).toBe(13_000)
  })

  it("returns undefined without a start time", () => {
    expect(subagentElapsedMs(undefined, START)).toBeUndefined()
    expect(
      subagentElapsedMs(subagent({ startedAt: undefined }), START)
    ).toBeUndefined()
  })

  it("ignores a clock that ran backwards rather than showing a negative timer", () => {
    expect(subagentElapsedMs(subagent(), START - 1000)).toBeUndefined()
  })
})

describe("formatElapsed", () => {
  it("scales the unit with the duration", () => {
    expect(formatElapsed(0)).toBe("0ms")
    expect(formatElapsed(850)).toBe("850ms")
    expect(formatElapsed(4500)).toBe("4.5s")
    expect(formatElapsed(59_900)).toBe("59.9s")
    expect(formatElapsed(60_000)).toBe("1m 00s")
    expect(formatElapsed(125_000)).toBe("2m 05s")
  })
})

describe("subagentLabel", () => {
  it("prefers the requested subagent_type over the discovered name", () => {
    const item = chunk({
      input: { subagent_type: "reviewer" },
      subagent: subagent(),
    })
    expect(subagentLabel(item)).toBe("reviewer")
  })

  it("falls back to the discovered name, then a generic label", () => {
    expect(subagentLabel(chunk({ subagent: subagent() }))).toBe("researcher")
    expect(subagentLabel(chunk())).toBe("subagent")
  })
})

describe("subagentOutcome", () => {
  it("surfaces an error ahead of any result", () => {
    const item = chunk({
      status: "error",
      subagent: subagent({ error: "boom", result: "partial" }),
    })
    expect(subagentOutcome(item)).toEqual({ kind: "error", text: "boom" })
  })

  it("falls back to the tool output when the snapshot carries no result", () => {
    const item = chunk({
      status: "completed",
      output: "from tool message",
      subagent: subagent(),
    })
    expect(subagentOutcome(item)).toEqual({
      kind: "result",
      text: "from tool message",
    })
  })

  it("truncates a long result", () => {
    const item = chunk({ subagent: subagent({ result: "x".repeat(400) }) })
    const outcome = subagentOutcome(item)
    expect(outcome?.text).toHaveLength(281)
    expect(outcome?.text.endsWith("…")).toBe(true)
  })

  it("is absent when there is nothing to show", () => {
    expect(subagentOutcome(chunk())).toBeUndefined()
  })
})

describe("summarizeSubagentGroup", () => {
  it("reads as present tense while any member runs", () => {
    const summary = summarizeSubagentGroup(
      [
        chunk({ toolCallId: "a", status: "completed", subagent: subagent() }),
        chunk({ toolCallId: "b", status: "in_progress", subagent: subagent() }),
      ],
      START + 3000
    )
    expect(summary.headline).toBe("Running 1/2 subagents")
    expect(summary.running).toBe(1)
    expect(summary.finished).toBe(1)
  })

  it("counts every member stalled when the run died mid-fan-out", () => {
    const summary = summarizeSubagentGroup(
      [
        chunk({ toolCallId: "a", status: "in_progress", subagent: subagent() }),
        chunk({ toolCallId: "b", status: "in_progress", subagent: subagent() }),
      ],
      START + 3000,
      false
    )
    expect(summary.running).toBe(0)
    expect(summary.finished).toBe(0)
    expect(summary.headline).toBe("Ran 2 subagents")
  })

  it("switches to past tense and counts failures once settled", () => {
    const done = new Date(START + 1000).toISOString()
    const summary = summarizeSubagentGroup(
      [
        chunk({
          toolCallId: "a",
          status: "completed",
          subagent: subagent({ completedAt: done }),
        }),
        chunk({
          toolCallId: "b",
          status: "error",
          subagent: subagent({ completedAt: done }),
        }),
      ],
      START + 50_000
    )
    expect(summary.headline).toBe("Ran 2 subagents")
    expect(summary.failed).toBe(1)
    expect(summary.running).toBe(0)
  })

  it("reports the slowest member, not the sum, since members overlap", () => {
    const summary = summarizeSubagentGroup(
      [
        chunk({
          toolCallId: "a",
          status: "completed",
          subagent: subagent({
            completedAt: new Date(START + 1000).toISOString(),
          }),
        }),
        chunk({
          toolCallId: "b",
          status: "completed",
          subagent: subagent({
            completedAt: new Date(START + 7000).toISOString(),
          }),
        }),
      ],
      START + 9000
    )
    expect(summary.elapsedMs).toBe(7000)
  })

  it("uses the singular for a lone subagent", () => {
    expect(
      summarizeSubagentGroup([chunk({ status: "completed" })], START).headline
    ).toBe("Ran 1 subagent")
  })
})

describe("subagentSteps", () => {
  const call = (id: string, name: string) => ({ id, name, args: {} })

  it("pairs each tool call with the ToolMessage that resolved it", () => {
    const steps = subagentSteps([
      new AIMessage({
        content: "",
        tool_calls: [call("a", "execute"), call("b", "grep")],
      }),
      new ToolMessage({ content: "ok", tool_call_id: "a" }),
    ])
    expect(steps).toEqual([
      { id: "a", name: "execute", label: "Execute", status: "completed" },
      { id: "b", name: "grep", label: "Grep", status: "running" },
    ])
  })

  it("marks a failed tool call as an error", () => {
    const steps = subagentSteps([
      new AIMessage({ content: "", tool_calls: [call("a", "execute")] }),
      new ToolMessage({ content: "boom", tool_call_id: "a", status: "error" }),
    ])
    expect(steps[0]?.status).toBe("error")
  })

  it("ignores messages that carry no tool calls", () => {
    expect(subagentSteps([new AIMessage({ content: "just text" })])).toEqual([])
    expect(subagentSteps([])).toEqual([])
  })
})

describe("subagentMessagesDone", () => {
  it("is done once the subagent answers without calling another tool", () => {
    expect(
      subagentMessagesDone([
        new AIMessage({
          content: "",
          tool_calls: [{ id: "a", name: "execute", args: {} }],
        }),
        new ToolMessage({ content: "ok", tool_call_id: "a" }),
        new AIMessage({ content: "Scout done: nothing found." }),
      ])
    ).toBe(true)
  })

  it("is not done while a tool call is still outstanding", () => {
    expect(
      subagentMessagesDone([
        new AIMessage({
          content: "",
          tool_calls: [{ id: "a", name: "execute", args: {} }],
        }),
      ])
    ).toBe(false)
    expect(subagentMessagesDone([])).toBe(false)
  })
})

describe("subagentResultFromMessages", () => {
  it("returns the final answer, not the tool envelope", () => {
    expect(
      subagentResultFromMessages([
        new ToolMessage({ content: "ok", tool_call_id: "a" }),
        new AIMessage({ content: "Scout done: nothing found." }),
      ])
    ).toBe("Scout done: nothing found.")
  })

  it("withholds a result while the subagent is still working", () => {
    expect(
      subagentResultFromMessages([
        new AIMessage({
          content: "",
          tool_calls: [{ id: "a", name: "execute", args: {} }],
        }),
      ])
    ).toBeUndefined()
  })
})

describe("summarizeSubagentGroup with reported statuses", () => {
  it("counts a subagent its own messages settled ahead of the snapshot", () => {
    const chunks = [
      chunk({ toolCallId: "a", status: "in_progress", subagent: subagent() }),
      chunk({ toolCallId: "b", status: "in_progress", subagent: subagent() }),
    ]
    const reported = new Map([["a", "completed" as const]])
    expect(
      summarizeSubagentGroup(chunks, START + 3000, true, reported).headline
    ).toBe("Running 1/2 subagents")
  })
})
