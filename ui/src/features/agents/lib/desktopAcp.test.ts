import { describe, expect, it } from "vitest"

import { desktopAcpMessages } from "./desktopAcpMessages"

describe("desktopAcpMessages", () => {
  it("builds a local conversation and updates tool calls in place", () => {
    const messages = desktopAcpMessages([
      {
        sequence: 0,
        timestamp: "2026-08-05T20:00:00Z",
        type: "user-message",
        text: "Fix it",
        images: [],
      },
      {
        sequence: 1,
        timestamp: "2026-08-05T20:00:01Z",
        type: "agent-text",
        text: "I’ll inspect it.",
      },
      {
        sequence: 2,
        timestamp: "2026-08-05T20:00:02Z",
        type: "tool",
        tool: {
          toolCallId: "tool-1",
          title: "Read file",
          toolKind: "read",
          status: "in_progress",
        },
      },
      {
        sequence: 3,
        timestamp: "2026-08-05T20:00:03Z",
        type: "tool",
        tool: {
          toolCallId: "tool-1",
          title: "Read file",
          toolKind: "read",
          status: "completed",
          output: "done",
        },
      },
    ])

    expect(messages).toHaveLength(2)
    expect(messages[0]?.author).toBe("user")
    expect(messages[1]?.chunks).toEqual([
      { kind: "text", text: "I’ll inspect it." },
      {
        kind: "tool-execution",
        toolCallId: "tool-1",
        title: "Read file",
        toolKind: "read",
        input: {},
        status: "completed",
        output: "done",
        locations: undefined,
      },
    ])
  })
})
