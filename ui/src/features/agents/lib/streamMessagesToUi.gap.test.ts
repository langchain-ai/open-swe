import { AIMessage, HumanMessage } from "@langchain/core/messages"
import { describe, expect, it } from "vitest"

import { streamMessagesToUi } from "./streamMessagesToUi"

describe("streamMessagesToUi performance contracts", () => {
  it("preserves historical row identity when only the live tail changes", () => {
    const history = Array.from({ length: 250 }, (_, index) => [
      new HumanMessage({ id: `user-${index}`, content: `Question ${index}` }),
      new AIMessage({ id: `agent-${index}`, content: `Answer ${index}` }),
    ]).flat()
    const first = streamMessagesToUi(history)
    const nextHistory = [
      ...history.slice(0, -1),
      new AIMessage({ id: "agent-249", content: "Updated live tail" }),
    ]

    const second = streamMessagesToUi(nextHistory)

    expect(second).toHaveLength(first.length)
    for (let index = 0; index < first.length - 1; index += 1) {
      expect(second[index]).toBe(first[index])
      expect(second[index]?.chunks).toBe(first[index]?.chunks)
    }
    expect(second.at(-1)).not.toBe(first.at(-1))
  })
})
