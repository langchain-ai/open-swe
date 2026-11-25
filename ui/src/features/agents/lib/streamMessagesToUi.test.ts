import {
  AIMessage,
  AIMessageChunk,
  HumanMessage,
  ToolMessage,
} from "@langchain/core/messages"
import { describe, expect, it, vi } from "vitest"
import type { BaseMessage } from "@langchain/core/messages"

import { MessageClock } from "./messageTimestamps"
import { streamMessagesToUi } from "./streamMessagesToUi"
import { attachmentUrl } from "./transcript/api"
import { groupQuietRuns } from "@/features/agents/components/messages/quietRuns"

describe("streamMessagesToUi", () => {
  it("drops superseded partial replies and their unexecuted tools after a retry", () => {
    const messages = streamMessagesToUi([
      new AIMessageChunk({
        id: "interrupted",
        content: "Incomplete answer",
        tool_call_chunks: [
          {
            name: "execute",
            args: '{"command":"wrong"}',
            id: "never-ran",
            index: 0,
          },
        ],
      }).concat(
        new AIMessageChunk({
          id: "interrupted",
          content: "",
          response_metadata: { open_swe_superseded: true },
        })
      ),
      new AIMessage({ id: "replacement", content: "Recovered answer" }),
    ])

    expect(messages).toHaveLength(1)
    expect(messages[0]?.chunks).toEqual([
      expect.objectContaining({ kind: "text", text: "Recovered answer" }),
    ])
  })

  it("shows an image the state read links to from its stored copy, beside inline ones", () => {
    const messages = streamMessagesToUi([
      new HumanMessage({
        id: "person-message",
        content: [
          {
            type: "image",
            url: "/dashboard/api/threads/t-1/transcript/attachments/a-1",
            mime_type: "image/jpeg",
            file_name: "a.jpg",
          },
          { type: "image", base64: "aW1hZ2U=", mime_type: "image/png" },
          { type: "image", url: "https://example.com/elsewhere.png" },
          { type: "text", text: "What's wrong here?" },
        ],
      }),
    ])

    expect(messages[0]?.chunks).toEqual([
      {
        kind: "image",
        url: attachmentUrl("t-1", "a-1"),
        credentials: "session",
        mimeType: "image/jpeg",
        fileName: "a.jpg",
      },
      { kind: "image", base64: "aW1hZ2U=", mimeType: "image/png" },
      { kind: "text", text: "What's wrong here?" },
    ])
  })

  it("keeps platform turn metadata out of the transcript", () => {
    const messages = streamMessagesToUi([
      new HumanMessage({
        id: "person-block",
        content:
          '<dynamic-context kind="person" id="github:alice">\ndisplay_name: Alice\ncommit_name: Alice\ncommit_email: alice@users.noreply.github.com\n</dynamic-context>',
      }),
      new HumanMessage({
        id: "person-message",
        content:
          '<input-message sender="github:alice" surface="web" kind="human">\nShip it\n</input-message>',
      }),
    ])

    expect(messages).toHaveLength(1)
    expect(messages[0]).toMatchObject({
      author: "user",
      chunks: [{ kind: "text", text: "Ship it" }],
    })
  })

  it("hides entity introductions and renders structured senders distinctly", () => {
    const messages = streamMessagesToUi([
      new HumanMessage({
        id: "person-entity",
        content:
          '<dynamic-context kind="person" id="github:alice">\ndisplay_name: Alice\n</dynamic-context>',
      }),
      new HumanMessage({
        id: "system-entity",
        content:
          '<dynamic-context kind="system" id="system:scheduler">\ndisplay_name: Scheduler\n</dynamic-context>',
      }),
      new HumanMessage({
        id: "person-message",
        content:
          '<input-message sender="github:alice" surface="web" kind="human">\nHello &lt;b&gt;world&lt;/b&gt;\n</input-message>',
      }),
      new HumanMessage({
        id: "system-message",
        content:
          '<input-message sender="system:scheduler" surface="automation">\nCheck CI\n</input-message>',
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
          '<dynamic-context kind="system" id="system:open-swe">\ndisplay_name: Open SWE\nsender_type: self\n</dynamic-context>',
      }),
      new HumanMessage({
        id: "self-message",
        content:
          '<input-message sender="system:open-swe" surface="slack" kind="system">\non it\n</input-message>',
      }),
      new HumanMessage({
        id: "internal-slack-note",
        content:
          '<input-message sender="system:slack-note" surface="automation" kind="system">\nReview accepted\n</input-message>',
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
      chunks: [
        { kind: "text", text: "Working" },
        { kind: "text", text: "Done" },
      ],
    })
  })

  it("preserves structured message whitespace", () => {
    const messages = streamMessagesToUi([
      new HumanMessage({
        id: "structured",
        content:
          '<input-message sender="github:alice" surface="web" kind="human">\n  indented\n\n</input-message>',
      }),
      new HumanMessage({
        id: "stored",
        content:
          '<input-message sender="github:alice" surface="web" kind="human"><content>  indented\n</content></input-message>',
      }),
    ])

    expect(messages[0]?.chunks).toEqual([
      { kind: "text", text: "  indented\n" },
    ])
    expect(messages[1]?.chunks).toEqual([
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

  it("times messages by the server or Slack, or by arrival only while live", () => {
    vi.useFakeTimers({ now: new Date("2026-11-25T16:00:00.000Z") })
    const clock = new MessageClock()
    const read = (messages: Array<BaseMessage>, live: boolean) =>
      streamMessagesToUi(messages, [], (id, sentAt) =>
        clock.timeOf(id, sentAt, live)
      ).map(({ id, timestamp, timestampIsFallback }) =>
        timestampIsFallback ? { id } : { id, timestamp }
      )
    const history = [
      new HumanMessage({
        id: "slack",
        content:
          '<input-message sender="slack:U123" channel="slack:C123" surface="slack" kind="human" timestamp="1795579249.622689" slack_message_ts="1795579249.622689">Fix it</input-message>',
      }),
      new AIMessage({
        id: "ai-1",
        content: "timed",
        response_metadata: { created_at: "2026-11-25T15:21:00.000Z" },
      }),
      new AIMessage({ id: "ai-untimed", content: "untimed" }),
      new HumanMessage({ id: "old", content: "before the transcript" }),
      new AIMessage({ id: "ai-old", content: "old reply" }),
    ]
    const timedHistory = [
      { id: "slack", timestamp: "2026-11-25T04:00:49.622Z" },
      { id: "ai-1", timestamp: "2026-11-25T15:21:00.000Z" },
      { id: "old" },
      { id: "ai-old" },
    ]
    expect(read(history, false)).toEqual(timedHistory)

    // A stream update carries messages without the server's time.
    vi.setSystemTime(new Date("2026-11-25T16:05:00.000Z"))
    const streamed = [
      history[0]!,
      new AIMessage({ id: "ai-1", content: "timed" }),
      ...history.slice(2),
      new HumanMessage({ id: "follow-up", content: "and now?" }),
      new AIMessage({ id: "ai-live", content: "live" }),
    ]
    expect(read(streamed, true)).toEqual([
      ...timedHistory,
      { id: "follow-up", timestamp: "2026-11-25T16:05:00.000Z" },
      { id: "ai-live", timestamp: "2026-11-25T16:05:00.000Z" },
    ])
    vi.useRealTimers()
  })

  it("treats a running subagent's first read as history", () => {
    vi.useFakeTimers({ now: new Date("2026-11-25T16:00:00.000Z") })
    const clock = new MessageClock()
    const read = (messages: Array<BaseMessage>) =>
      streamMessagesToUi(messages, [], clock.reading(true)).map(
        ({ id, timestampIsFallback }) => ({ id, timed: !timestampIsFallback })
      )
    const opened = [
      new HumanMessage({ id: "task", content: "Look into it" }),
      new AIMessage({ id: "step", content: "Looking" }),
    ]
    expect(read(opened)).toEqual([
      { id: "task", timed: false },
      { id: "step", timed: false },
    ])
    expect(
      read([...opened, new HumanMessage({ id: "next", content: "Thanks" })])
    ).toEqual([
      { id: "task", timed: false },
      { id: "step", timed: false },
      { id: "next", timed: true },
    ])
    vi.useRealTimers()
  })

  it("keeps wake-up runs separate and groups their quiet work", () => {
    const messages = streamMessagesToUi([
      new HumanMessage({ id: "user", content: "Watch CI" }),
      new AIMessage({ id: "answer", content: "Watching." }),
      new HumanMessage({
        id: "wake-1",
        content:
          '<input-message sender="system:monitor" kind="system">Check CI</input-message>',
      }),
      new AIMessage({
        id: "run-1",
        content: "",
        tool_calls: [{ id: "check-1", name: "execute", args: {} }],
      }),
      new ToolMessage({ tool_call_id: "check-1", content: "pending" }),
      new HumanMessage({
        id: "wake-2",
        content:
          '<input-message sender="system:thread-wakeup" kind="system">Check again</input-message>',
      }),
      new AIMessage({
        id: "run-2",
        content: "",
        tool_calls: [{ id: "check-2", name: "execute", args: {} }],
      }),
      new ToolMessage({ tool_call_id: "check-2", content: "pending" }),
      new HumanMessage({
        id: "wake-3",
        content:
          '<input-message sender="system:goal" kind="system">Final check</input-message>',
      }),
      new AIMessage({ id: "answer-2", content: "CI passed." }),
    ])

    expect(messages.map((message) => message.id)).toEqual([
      "user",
      "answer",
      "wake-1",
      "run-1",
      "wake-2",
      "run-2",
      "wake-3",
      "answer-2",
    ])
    expect(
      messages
        .filter((message) => message.author === "agent")
        .map((message) => message.turnKey)
    ).toEqual(["user", "wake-1", "wake-2", "wake-3"])
    const grouped = groupQuietRuns(messages)
    expect(grouped.find((entry) => entry.type === "quiet")).toMatchObject({
      type: "quiet",
      stretch: { runCount: 2, actionCount: 2, triggers: [["Context", 2]] },
    })
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

  it("names a result's media blocks and leaves their base64 out of its output", () => {
    const content = [
      { type: "text", text: "Read 3 files." },
      { type: "image", base64: "iVBORw0KGgo", mime_type: "image/png" },
      { type: "file", base64: "A".repeat(4096), mime_type: "application/pdf" },
      {
        type: "image",
        url: "/dashboard/api/threads/t-1/transcript/attachments/a-1",
        mime_type: "image/png",
      },
    ]
    const messages = streamMessagesToUi(
      [
        new AIMessage({
          id: "ai-1",
          content: "",
          tool_calls: [
            { id: "call-1", name: "read_file", args: {}, type: "tool_call" },
          ],
        }),
        new ToolMessage({ tool_call_id: "call-1", content }),
      ],
      [
        {
          id: "call-1",
          callId: "call-1",
          name: "read_file",
          namespace: ["tools:call-1"],
          input: {},
          args: {},
          output: content,
          status: "finished",
          error: undefined,
        },
      ]
    )

    expect(messages[0]?.chunks[0]).toMatchObject({
      kind: "tool-execution",
      output: "Read 3 files.\n[file application/pdf, 3 KB]",
      images: [
        { kind: "image", mimeType: "image/png" },
        {
          kind: "image",
          url: attachmentUrl("t-1", "a-1"),
          credentials: "session",
        },
      ],
    })
  })

  it("keeps the links of media a result only points to", () => {
    const content = [
      { type: "text", text: "Found 2 links." },
      { type: "image", url: "https://example.com/chart.png" },
      {
        type: "file",
        url: "https://example.com/report.pdf",
        mime_type: "application/pdf",
      },
    ]
    const messages = streamMessagesToUi(
      [
        new AIMessage({
          id: "ai-1",
          content: "",
          tool_calls: [
            { id: "call-1", name: "fetch", args: {}, type: "tool_call" },
          ],
        }),
        new ToolMessage({ tool_call_id: "call-1", content }),
      ],
      [
        {
          id: "call-1",
          callId: "call-1",
          name: "fetch",
          namespace: ["tools:call-1"],
          input: {},
          args: {},
          output: content,
          status: "finished",
          error: undefined,
        },
      ]
    )

    expect(messages[0]?.chunks[0]).toMatchObject({
      kind: "tool-execution",
      output: [
        "Found 2 links.",
        "[image] https://example.com/chart.png",
        "[file application/pdf] https://example.com/report.pdf",
      ].join("\n"),
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
})
