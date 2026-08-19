import { AIMessage, HumanMessage, ToolMessage } from "@langchain/core/messages"
import { describe, expect, it } from "vitest"

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
      chunks: [{ kind: "text", text: "Hello <b>world</b>" }],
    })
    expect(messages[1]).toMatchObject({
      author: "system",
      structuredSenderKind: "system",
      structuredSenderName: "Scheduler",
      chunks: [{ kind: "text", text: "Check CI" }],
    })
    expect(messages[2]).toMatchObject({
      author: "user",
      chunks: [{ kind: "text", text: "Legacy message" }],
    })
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
          html: "<h1>Chart</h1>",
          title: "Chart",
          filename: "chart.html",
        },
      }),
    ])

    const agent = messages.find((message) => message.author === "agent")
    const tool = agent?.chunks.find((chunk) => chunk.kind === "tool-execution")
    expect(tool?.kind === "tool-execution" ? tool.display : undefined).toEqual({
      type: "output_iframe",
      html: "<h1>Chart</h1>",
      title: "Chart",
      filename: "chart.html",
    })
  })
})
