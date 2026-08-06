import { AIMessage, HumanMessage } from "@langchain/core/messages"
import { describe, expect, it } from "vitest"

import { streamMessagesToUi } from "./streamMessagesToUi"

describe("streamMessagesToUi", () => {
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
