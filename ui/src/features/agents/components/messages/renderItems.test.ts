import { describe, expect, it } from "vitest"

import { buildRenderItems, splitWorkAndReply } from "./renderItems"
import type { ToolExecutionChunk } from "@/features/agents/lib/types"

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
      html: "<h1>Chart</h1>",
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
})
