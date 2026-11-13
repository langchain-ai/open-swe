import { describe, expect, it } from "vitest"

import {
  buildRenderItems,
  collapsedWorkItems,
  countWorkActions,
  segmentTurn,
  WorkTail,
} from "./renderItems"
import type { Chunk, ToolExecutionChunk } from "@/features/agents/lib/types"

function iframeChunk(): ToolExecutionChunk {
  return {
    kind: "tool-execution",
    toolCallId: "call-1",
    title: "output_iframe /tmp/chart.html",
    toolKind: "other",
    input: { path: "/tmp/chart.html" },
    status: "completed",
    output: "Displayed the HTML output in the dashboard.",
    display: {
      type: "output_iframe",
      previewUrl: "https://downloads.example/preview?token=secret",
      downloadUrl: "https://downloads.example/download?token=secret",
      title: "Chart",
      filename: "chart.html",
    },
  }
}

describe("buildRenderItems", () => {
  it("shows a managed tools offer as a card, and a failed or outdated one as a tool call", () => {
    const missing = [{ id: "srv-1", name: "Linear", kind: "oauth" }]
    const card: ToolExecutionChunk = {
      kind: "tool-execution",
      toolCallId: "managed-1",
      title: "Connect managed tools",
      toolKind: "managed-tools",
      status: "completed",
      output: JSON.stringify({
        success: true,
        status: "connection_required",
        missing,
        continues_automatically: true,
      }),
    }
    const failed: ToolExecutionChunk = {
      ...card,
      toolCallId: "managed-2",
      output: JSON.stringify({ success: false, error: "Not private" }),
    }
    // Before the instance tool kit went away, cards named services by slug.
    const outdated: ToolExecutionChunk = {
      ...card,
      toolCallId: "managed-3",
      output: JSON.stringify({
        success: true,
        status: "connection_required",
        gateway: { id: "gw-1", name: "Engineering" },
        missing: [{ slug: "linear", display_name: "Linear", kind: "oauth" }],
      }),
    }
    expect(buildRenderItems([card, failed, outdated])).toEqual([
      {
        type: "managed-tools-item",
        key: "tool-managed-1",
        chunk: card,
        offer: {
          status: "connection_required",
          missing,
          continuesAutomatically: true,
        },
      },
      { type: "tool-item", key: "tool-managed-2", chunk: failed },
      { type: "tool-item", key: "tool-managed-3", chunk: outdated },
    ])
  })

  it("keeps iframe output as a dedicated inline item", () => {
    expect(buildRenderItems([iframeChunk()])).toEqual([
      {
        type: "iframe-item",
        key: "tool-call-1",
        chunk: iframeChunk(),
      },
    ])
  })

  it("folds SQL results into work and counts the query as an action", () => {
    const chunk: ToolExecutionChunk = {
      kind: "tool-execution",
      toolCallId: "call-sql",
      title: "Read only sql",
      toolKind: "sql",
      status: "completed",
      output:
        '{"ok":true,"columns":["id"],"rows":[[1]],"row_count":1,"truncated":false}',
    }

    expect(buildRenderItems([chunk])).toEqual([
      { type: "sql-item", key: "tool-call-sql", chunk },
    ])
    const items = buildRenderItems([chunk])
    expect(collapsedWorkItems(items)).toEqual([])
    expect(countWorkActions(items)).toBe(1)
  })

  it.each([
    ["in-progress", "in_progress", undefined],
    ["failed", "error", '{"ok":false,"error":"Query failed"}'],
    ["malformed", "completed", '{"ok":false,"error":"Query rejected"}'],
  ] as const)(
    "keeps %s SQL calls as ordinary tool entries",
    (_label, status, output) => {
      const chunk: ToolExecutionChunk = {
        kind: "tool-execution",
        toolCallId: "call-sql",
        title: "Read only sql",
        toolKind: "sql",
        status,
        output,
      }

      expect(buildRenderItems([chunk])).toEqual([
        { type: "tool-item", key: "tool-call-sql", chunk },
      ])
    }
  )

  it("splits work into separate folds at each sent reply", () => {
    const sentReply: ToolExecutionChunk = {
      kind: "tool-execution",
      toolCallId: "call-reply",
      title: "Replied",
      toolKind: "slack",
      status: "completed",
    }
    const laterTool: ToolExecutionChunk = {
      kind: "tool-execution",
      toolCallId: "call-2",
      title: "Fetch",
      toolKind: "fetch",
      status: "completed",
    }
    const items = buildRenderItems([
      sentReply,
      iframeChunk(),
      laterTool,
      { kind: "text", text: "Done" },
    ])

    expect(
      segmentTurn(items).map((segment) =>
        segment.type === "reply"
          ? segment.item.type
          : segment.items.map((item) => item.type)
      )
    ).toEqual(["reply-item", "iframe-item", ["tool-item"], "text-chunk"])
  })

  it("keeps tool failures collapsed while preserving approvals", () => {
    const chunks: Array<Chunk> = [
      { kind: "reasoning", text: "Inspecting the code" },
      {
        kind: "tool-execution",
        toolCallId: "read-1",
        title: "read_file",
        toolKind: "read",
        status: "completed",
      },
      {
        kind: "tool-execution",
        toolCallId: "shell-1",
        title: "shell",
        toolKind: "execute",
        status: "error",
      },
      {
        kind: "tool-execution",
        toolCallId: "search-1",
        title: "search",
        toolKind: "search",
        status: "pending",
      },
      { kind: "text", text: "Done" },
    ]
    const items = buildRenderItems(chunks)

    const collapsed = collapsedWorkItems(items)

    expect(collapsed.map((item) => item.type)).toEqual(["tool-item"])
    expect(
      collapsed.flatMap((item) =>
        "chunk" in item && item.chunk.kind === "tool-execution"
          ? [item.chunk.toolCallId]
          : []
      )
    ).toEqual(["search-1"])
  })

  it("keeps unfinished work visible after a turn is interrupted", () => {
    const items = buildRenderItems([
      {
        kind: "tool-execution",
        toolCallId: "approval-1",
        title: "edit_file",
        toolKind: "edit",
        status: "in_progress",
      },
    ])

    expect(collapsedWorkItems(items)).toEqual([])
    expect(collapsedWorkItems(items, true)).toEqual(items)
  })

  it("counts tool actions without counting reasoning or final output", () => {
    const items = buildRenderItems([
      { kind: "reasoning", text: "Inspecting" },
      {
        kind: "tool-execution",
        toolCallId: "read-1",
        title: "read_file",
        toolKind: "read",
        status: "completed",
      },
      {
        kind: "tool-execution",
        toolCallId: "read-2",
        title: "read_file",
        toolKind: "read",
        status: "completed",
      },
      {
        kind: "tool-execution",
        toolCallId: "shell-1",
        title: "shell",
        toolKind: "execute",
        status: "completed",
      },
      { kind: "text", text: "Done" },
    ])
    expect(countWorkActions(items)).toBe(3)
  })
})

describe("WorkTail", () => {
  const tool = (
    toolCallId: string,
    status: ToolExecutionChunk["status"] = "completed"
  ): ToolExecutionChunk => ({
    kind: "tool-execution",
    toolCallId,
    title: "read_file",
    toolKind: "read",
    status,
  })
  const summary = (tail: WorkTail) =>
    tail.entries.map((entry) =>
      entry.kind === "tool"
        ? entry.chunk.toolCallId
        : [entry.heading, entry.body]
    )

  it("keeps only the latest thought and tool and leaves approvals to the fold", () => {
    const tail = new WorkTail(
      buildRenderItems([
        tool("read-0"),
        { kind: "reasoning", text: "Looking around" },
        ...Array.from({ length: 5 }, (_, index) => tool(`read-${index + 1}`)),
        {
          kind: "reasoning",
          text: `**Checking the write path**\n\nAn old paragraph.\n\n${"Now I'm reading the queue store. ".repeat(12)}`,
        },
        tool("approve-1", "pending"),
        tool("read-7", "in_progress"),
      ])
    )

    expect(summary(tail)).toEqual([
      [
        "Checking the write path",
        "Now I'm reading the queue store. ".repeat(12).trim(),
      ],
      "read-7",
    ])
  })
  it("drops thinking after ten newer tools", () => {
    const tail = new WorkTail(
      buildRenderItems([
        { kind: "reasoning", text: "Reading the configuration" },
        ...Array.from({ length: 10 }, (_, index) => tool(`read-${index}`)),
      ])
    )
    expect(summary(tail)).toEqual(["read-9"])
  })
})
