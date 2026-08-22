import { describe, expect, it } from "vitest"

import { threadMessages } from "./threadMessages"
import type { Message } from "./types"

const optimistic: Message = {
  id: "optimistic",
  author: "user",
  timestamp: "2026-08-22T00:00:00Z",
  chunks: [{ kind: "text", text: "Initial prompt" }],
}
const response: Message = {
  id: "response",
  author: "agent",
  timestamp: "2026-08-22T00:01:00Z",
  chunks: [{ kind: "text", text: "Done" }],
}

describe("threadMessages", () => {
  it("replaces the optimistic transcript after hydration", () => {
    expect(threadMessages([optimistic, response], [optimistic])).toEqual([
      optimistic,
      response,
    ])
  })

  it("keeps the optimistic transcript while hydration is empty", () => {
    expect(threadMessages([], [optimistic])).toEqual([optimistic])
  })
})
