import { AIMessage, HumanMessage, ToolMessage } from "@langchain/core/messages"
import { describe, expect, it } from "vitest"

import { LAZY_MARKER_KEY } from "./stream/lazyHydration"
import { streamMessagesToUi } from "./streamMessagesToUi"

describe("streamMessagesToUi", () => {
  it("hides entity introductions and renders structured senders distinctly", () => {
    const messages = streamMessagesToUi([
      new HumanMessage({
        id: "person-entity",
        content:
          '<dynamic-context kind="person" id="github:alice"><display_name>Alice</display_name></dynamic-context>',
      }),
      new HumanMessage({
        id: "system-entity",
        content:
          '<dynamic-context kind="system" id="system:scheduler"><display_name>Scheduler</display_name></dynamic-context>',
      }),
      new HumanMessage({
        id: "person-message",
        content:
          '<input-message sender="github:alice" surface="web" kind="human"><content>Hello &lt;b&gt;world&lt;/b&gt;</content></input-message>',
      }),
      new HumanMessage({
        id: "system-message",
        content:
          '<input-message sender="system:scheduler" surface="automation"><content>Check CI</content></input-message>',
      }),
      new HumanMessage({ id: "legacy", content: "Legacy message" }),
    ])

    expect(messages).toHaveLength(3)
    expect(messages[0]).toMatchObject({
      author: "user",
      structuredSenderId: "github:alice",
      structuredSenderKind: "person",
      structuredSenderName: "Alice",
      structuredSurface: "web",
      chunks: [{ kind: "text", text: "Hello <b>world</b>" }],
    })
    expect(messages[1]).toMatchObject({
      author: "system",
      structuredSenderKind: "system",
      structuredSenderName: "Scheduler",
      structuredSurface: "automation",
      chunks: [{ kind: "text", text: "Check CI" }],
    })
    expect(messages[2]).toMatchObject({
      author: "user",
      chunks: [{ kind: "text", text: "Legacy message" }],
    })
  })

  it("drops our own forwarded Slack replies, which already render as tool calls", () => {
    const messages = streamMessagesToUi([
      new HumanMessage({
        id: "self-entity",
        content:
          '<dynamic-context kind="system" id="system:open-swe"><display_name>Open SWE</display_name><sender_type>self</sender_type></dynamic-context>',
      }),
      new HumanMessage({
        id: "self-message",
        content:
          '<input-message sender="system:open-swe" surface="slack" kind="system"><content>on it</content></input-message>',
      }),
      new HumanMessage({ id: "legacy", content: "Legacy message" }),
    ])

    expect(messages).toHaveLength(1)
    expect(messages[0]).toMatchObject({
      chunks: [{ kind: "text", text: "Legacy message" }],
    })
  })

  it("hides compaction summary messages", () => {
    const messages = streamMessagesToUi([
      new HumanMessage({ id: "user-1", content: "Fix the bug" }),
      new AIMessage({ id: "ai-1", content: "Working" }),
      new AIMessage({
        id: "live-summary",
        content: "SESSION INTENT: secret context",
        additional_kwargs: { lc_source: "summarization" },
      }),
      new HumanMessage({
        id: "persisted-summary",
        content:
          "Here is a summary of the conversation to date: secret context",
        additional_kwargs: { lc_source: "summarization" },
      }),
      new AIMessage({ id: "ai-2", content: "Done" }),
    ])

    expect(messages).toHaveLength(2)
    expect(messages[0]).toMatchObject({
      author: "user",
      chunks: [{ kind: "text", text: "Fix the bug" }],
    })
    expect(messages[1]).toMatchObject({
      author: "agent",
      turnKey: "user-1",
      chunks: [{ kind: "text", text: "Done" }],
    })
  })

  it("preserves structured message whitespace", () => {
    const messages = streamMessagesToUi([
      new HumanMessage({
        id: "structured",
        content:
          '<input-message sender="github:alice" surface="web" kind="human"><content>  indented\n</content></input-message>',
      }),
    ])

    expect(messages[0]?.chunks).toEqual([
      { kind: "text", text: "  indented\n" },
    ])
  })

  it("keys each agent turn by the user message that opened it", () => {
    const messages = streamMessagesToUi([
      new HumanMessage({ id: "user-1", content: "first" }),
      new AIMessage({ id: "ai-1", content: "one" }),
      new HumanMessage({ id: "user-2", content: "second" }),
      new AIMessage({ id: "ai-2", content: "two" }),
    ])

    expect(
      messages
        .filter((message) => message.author === "agent")
        .map((message) => message.turnKey)
    ).toEqual(["user-1", "user-2"])
  })

  it("identifies local task calls as subagents", () => {
    const messages = streamMessagesToUi([
      new AIMessage({
        id: "ai-1",
        content: "",
        tool_calls: [
          {
            id: "call-1",
            name: "task",
            args: { description: "Investigate the issue" },
            type: "tool_call",
          },
        ],
      }),
    ])

    expect(messages[0]?.chunks[0]).toMatchObject({
      kind: "tool-execution",
      toolKind: "task",
    })
  })

  it("identifies read-only SQL calls for table rendering", () => {
    const messages = streamMessagesToUi([
      new AIMessage({
        id: "ai-1",
        content: "",
        tool_calls: [
          {
            id: "call-1",
            name: "read_only_sql",
            args: { query: "SELECT 1" },
            type: "tool_call",
          },
        ],
      }),
    ])

    expect(messages[0]?.chunks[0]).toMatchObject({
      kind: "tool-execution",
      toolKind: "sql",
    })
  })

  it("attaches validated output iframe artifacts to their tool call", () => {
    const messages = streamMessagesToUi([
      new HumanMessage({ id: "user-1", content: "draw a chart" }),
      new AIMessage({
        id: "ai-1",
        content: "",
        tool_calls: [
          {
            id: "call-1",
            name: "output_iframe",
            args: { path: "/tmp/chart.html" },
            type: "tool_call",
          },
        ],
      }),
      new ToolMessage({
        tool_call_id: "call-1",
        content: "Displayed the HTML output in the dashboard.",
        artifact: {
          type: "output_iframe",
          preview_url: "https://downloads.example/preview?token=secret",
          download_url: "https://downloads.example/download?token=secret",
          title: "Chart",
          filename: "chart.html",
        },
      }),
    ])

    const agent = messages.find((message) => message.author === "agent")
    const tool = agent?.chunks.find((chunk) => chunk.kind === "tool-execution")
    expect(tool?.kind === "tool-execution" ? tool.display : undefined).toEqual({
      type: "output_iframe",
      previewUrl: "https://downloads.example/preview?token=secret",
      downloadUrl: "https://downloads.example/download?token=secret",
      title: "Chart",
      filename: "chart.html",
    })
  })

  it("preserves historical embedded iframe artifacts", () => {
    const messages = streamMessagesToUi([
      new AIMessage({
        id: "ai-1",
        content: "",
        tool_calls: [
          {
            id: "call-1",
            name: "output_iframe",
            args: { path: "/tmp/chart.html" },
            type: "tool_call",
          },
        ],
      }),
      new ToolMessage({
        tool_call_id: "call-1",
        content: "Displayed the HTML output in the dashboard.",
        artifact: {
          type: "output_iframe",
          html: "<h1>Historical chart</h1>",
          title: "Chart",
          filename: "chart.html",
        },
      }),
    ])

    const agent = messages.find((message) => message.author === "agent")
    const tool = agent?.chunks.find((chunk) => chunk.kind === "tool-execution")
    expect(tool?.kind === "tool-execution" ? tool.display : undefined).toEqual({
      type: "output_iframe",
      html: "<h1>Historical chart</h1>",
      title: "Chart",
      filename: "chart.html",
    })
  })

  it("rejects non-HTTP iframe artifact URLs", () => {
    const messages = streamMessagesToUi([
      new AIMessage({
        id: "ai-1",
        content: "",
        tool_calls: [
          {
            id: "call-1",
            name: "output_iframe",
            args: { path: "/tmp/chart.html" },
            type: "tool_call",
          },
        ],
      }),
      new ToolMessage({
        tool_call_id: "call-1",
        content: "Displayed the HTML output in the dashboard.",
        artifact: {
          type: "output_iframe",
          preview_url: "javascript:alert(1)",
          download_url: "https://downloads.example/download",
          title: "Chart",
          filename: "chart.html",
        },
      }),
    ])

    const agent = messages.find((message) => message.author === "agent")
    const tool = agent?.chunks.find((chunk) => chunk.kind === "tool-execution")
    expect(
      tool?.kind === "tool-execution" ? tool.display : undefined
    ).toBeUndefined()
  })

  it("overlays deferred parts on a skeleton hydrate and shows them as pending meanwhile", () => {
    const marker = (parts: Array<string>) => ({
      [LAZY_MARKER_KEY]: { truncated: true, size: 9000, parts },
    })
    const messages = [
      new HumanMessage({
        id: "h-1",
        content: [
          { type: "text", text: "look" },
          { type: "image", mime_type: "", data: "" },
        ],
        additional_kwargs: marker(["images"]),
      }),
      new AIMessage({
        id: "ai-1",
        content: [{ type: "reasoning", reasoning: "" }],
        tool_calls: [
          {
            id: "call-1",
            name: "execute",
            args: { command: "ls" },
            type: "tool_call",
          },
        ],
        additional_kwargs: marker(["reasoning"]),
      }),
      new ToolMessage({
        tool_call_id: "call-1",
        content: "",
        status: "error",
        additional_kwargs: marker(["content", "artifact"]),
      }),
    ]
    const build = (deferred?: Parameters<typeof streamMessagesToUi>[3]) => {
      const built = streamMessagesToUi(messages, [], undefined, deferred)
      const agent = built.find((m) => m.author === "agent")
      return {
        image: built[0]?.chunks.find((c) => c.kind === "image"),
        reasoning: agent?.chunks.find((c) => c.kind === "reasoning"),
        tool: agent?.chunks.find((c) => c.kind === "tool-execution"),
      }
    }

    const pending = build()
    expect(pending.image).toMatchObject({ kind: "image", pending: true })
    expect(pending.reasoning).toMatchObject({ text: "", pending: true })
    // The persisted status wins over the SDK projection, which saw no output.
    expect(pending.tool).toMatchObject({
      status: "error",
      outputPending: true,
    })
    expect(
      pending.tool?.kind === "tool-execution" && pending.tool.output
    ).toBeUndefined()

    const loaded = build({
      tool_results: {
        "call-1": {
          content: [{ type: "text", text: "the full output" }],
          artifact: {
            type: "output_iframe",
            html: "<p>hi</p>",
            title: "Result",
            filename: "r.html",
          },
          status: "error",
        },
      },
      reasoning: { "ai-1": "let me think" },
      images: {
        "h-1": [{ type: "image", mime_type: "image/png", data: "AAAA" }],
      },
    })
    expect(loaded.image).toMatchObject({
      base64: "AAAA",
      mimeType: "image/png",
    })
    expect(
      loaded.image?.kind === "image" && loaded.image.pending
    ).toBeUndefined()
    expect(loaded.reasoning).toMatchObject({ text: "let me think" })
    expect(loaded.tool).toMatchObject({
      status: "error",
      output: "the full output",
      display: { type: "output_iframe", title: "Result" },
    })
    expect(
      loaded.tool?.kind === "tool-execution" && loaded.tool.outputPending
    ).toBeUndefined()
  })
})
