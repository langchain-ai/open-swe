import { describe, expect, it } from "vitest"

import {
  buildRenderItems,
  countWorkActions,
  selectCollapsedTurnItems,
  splitWorkAndReply,
} from "./renderItems"
import type {
  Chunk,
  DiffData,
  ToolExecutionChunk,
} from "@/features/agents/lib/types"

function diff(
  filePath: string,
  originalContent: string,
  newContent: string
): DiffData {
  return {
    filePath,
    originalContent,
    newContent,
    isNewFile: false,
    isBinary: false,
    isTruncated: false,
    totalLines: 1,
  }
}

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
  it("keeps iframe output as a dedicated inline item", () => {
    expect(buildRenderItems([iframeChunk()])).toEqual([
      {
        type: "iframe-item",
        key: "tool-call-1",
        chunk: iframeChunk(),
      },
    ])
  })

  it("groups repeated edits to the same file without expanding individual calls", () => {
    const first: ToolExecutionChunk = {
      kind: "tool-execution",
      toolCallId: "edit-1",
      title: "edit_file /workspace/app.ts",
      toolKind: "edit",
      status: "completed",
      diffData: diff("/workspace/app.ts", "a\n", "b\n"),
    }
    const other: ToolExecutionChunk = {
      kind: "tool-execution",
      toolCallId: "edit-2",
      title: "edit_file /workspace/other.ts",
      toolKind: "edit",
      status: "completed",
      diffData: diff("/workspace/other.ts", "x\n", "y\n"),
    }
    const second: ToolExecutionChunk = {
      kind: "tool-execution",
      toolCallId: "edit-3",
      title: "edit_file /workspace/app.ts",
      toolKind: "edit",
      status: "completed",
      diffData: diff("/workspace/app.ts", "b\n", "c\n"),
    }

    expect(buildRenderItems([first, other, second])).toEqual([
      {
        type: "edit-group",
        key: "edit-group-edit-1",
        chunks: [first, second],
      },
      { type: "edit-item", key: "tool-edit-2", chunk: other },
    ])
  })

  it("keeps sent replies visible when later work runs", () => {
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

    const { workItems, replyItems } = splitWorkAndReply(items)
    expect(workItems.map((item) => item.type)).toEqual(["tool-item"])
    expect(replyItems.map((item) => item.type)).toEqual([
      "reply-item",
      "iframe-item",
      "text-chunk",
    ])
  })

  it("keeps tool failures collapsed while preserving approvals and final output", () => {
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

    const collapsed = selectCollapsedTurnItems(items)

    expect(collapsed.map((item) => item.type)).toEqual([
      "tool-item",
      "text-chunk",
    ])
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

    expect(selectCollapsedTurnItems(items)).toEqual([])
    expect(selectCollapsedTurnItems(items, true)).toEqual(items)
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
    const { workItems } = splitWorkAndReply(items)

    expect(countWorkActions(workItems)).toBe(3)
  })
})
