import { AIMessage, HumanMessage, ToolMessage } from "@langchain/core/messages"
import { describe, expect, it } from "vitest"

import {
  createUiMessageProjector,
  streamMessagesToUi,
} from "./streamMessagesToUi"
import { buildThreadFixture, streamTicks } from "./threadStreamFixture"
import type { BaseMessage } from "@langchain/core/messages"

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

describe("createUiMessageProjector", () => {
  it("keeps identities for untouched units across a stream flush", () => {
    const fixture = buildThreadFixture(6)
    const project = createUiMessageProjector()
    const base = project(fixture.messages, fixture.toolCalls)
    const [tick] = streamTicks(fixture, 1)

    const next = project(tick!.messages, tick!.toolCalls)

    expect(next).toHaveLength(base.length)
    for (let i = 0; i < next.length - 1; i += 1) {
      expect(next[i]).toBe(base[i])
    }
    const baseLast = base[base.length - 1]!
    const nextLast = next[next.length - 1]!
    expect(nextLast).not.toBe(baseLast)
    expect(nextLast.chunks[0]).toBe(baseLast.chunks[0])
  })

  it("returns the identical array when no input reference changed", () => {
    const fixture = buildThreadFixture(3)
    const project = createUiMessageProjector()
    const base = project(fixture.messages, fixture.toolCalls)

    expect(project(fixture.messages.slice(), fixture.toolCalls)).toBe(base)
  })

  it("matches the one-shot conversion across a mutation sequence", () => {
    const fixture = buildThreadFixture(4)
    const project = createUiMessageProjector()
    expect(project(fixture.messages, fixture.toolCalls)).toEqual(
      streamMessagesToUi(fixture.messages, fixture.toolCalls)
    )

    for (const tick of streamTicks(fixture, 3)) {
      expect(project(tick.messages, tick.toolCalls)).toEqual(
        streamMessagesToUi(tick.messages, tick.toolCalls)
      )
    }
  })

  it("rebuilds a turn when its tool result lands", () => {
    const stampAt = (message: BaseMessage, iso: string) =>
      Object.assign(message, { created_at: iso }) as BaseMessage
    const call = {
      id: "call-1",
      name: "execute",
      args: { command: "pnpm test" },
      type: "tool_call" as const,
    }
    const before: Array<BaseMessage> = [
      stampAt(
        new HumanMessage({ id: "user-1", content: "run the tests" }),
        "2026-01-01T00:00:00.000Z"
      ),
      stampAt(
        new AIMessage({ id: "ai-1", content: "", tool_calls: [call] }),
        "2026-01-01T00:00:01.000Z"
      ),
    ]
    const project = createUiMessageProjector()
    const initial = project(before)
    expect(initial[1]?.chunks[0]).toMatchObject({ status: "in_progress" })

    const after = [
      ...before,
      stampAt(
        new ToolMessage({
          id: "tool-1",
          tool_call_id: "call-1",
          content: "1 passed",
          status: "success",
        }),
        "2026-01-01T00:00:02.000Z"
      ),
    ]
    const next = project(after)
    expect(next[0]).toBe(initial[0])
    expect(next[1]).not.toBe(initial[1])
    expect(next[1]?.chunks[0]).toMatchObject({
      status: "completed",
      output: "1 passed",
    })
    expect(next).toEqual(streamMessagesToUi(after))
  })

  it("rebuilds a user message when its sender entity arrives later", () => {
    const stampAt = (message: BaseMessage, iso: string) =>
      Object.assign(message, { created_at: iso }) as BaseMessage
    const structured = stampAt(
      new HumanMessage({
        id: "structured-1",
        content:
          '<input-message sender="github:alice" surface="slack"><content>hello</content></input-message>',
      }),
      "2026-01-01T00:00:00.000Z"
    )
    const project = createUiMessageProjector()
    const initial = project([structured])
    expect(initial[0]?.structuredSenderName).toBeUndefined()

    const withEntity = [
      structured,
      stampAt(
        new HumanMessage({
          id: "entity-1",
          content:
            '<dynamic-context kind="person" id="github:alice"><display_name>Alice</display_name></dynamic-context>',
        }),
        "2026-01-01T00:00:01.000Z"
      ),
    ]
    const next = project(withEntity)
    expect(next[0]).not.toBe(initial[0])
    expect(next[0]?.structuredSenderName).toBe("Alice")
    expect(next).toEqual(streamMessagesToUi(withEntity))
  })
})
