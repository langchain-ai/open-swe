import { AIMessage, HumanMessage } from "@langchain/core/messages"
import { describe, expect, it } from "vitest"

import { streamMessagesToUi } from "./streamMessagesToUi"

describe("streamMessagesToUi", () => {
  it("hides entity introductions and renders structured senders distinctly", () => {
    const messages = streamMessagesToUi([
      new HumanMessage({
        id: "person-entity",
        content:
          '<dynamic_context kind="person" id="github:alice"><display_name>Alice</display_name></dynamic_context>',
      }),
      new HumanMessage({
        id: "system-entity",
        content:
          '<dynamic_context kind="system" id="system:scheduler"><display_name>Scheduler</display_name></dynamic_context>',
      }),
      new HumanMessage({
        id: "person-message",
        content:
          '<chat_message sender="github:alice" surface="web" kind="human"><content>Hello &lt;b&gt;world&lt;/b&gt;</content></chat_message>',
      }),
      new HumanMessage({
        id: "system-message",
        content:
          '<chat_message sender="system:scheduler" surface="automation"><content>Check CI</content></chat_message>',
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
})
