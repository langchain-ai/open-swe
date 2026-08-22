import { describe, expect, it } from "vitest"

import { selectThreadMessages } from "./threadMessages"
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

describe("selectThreadMessages", () => {
  it("replaces the optimistic transcript after hydration", () => {
    expect(selectThreadMessages([optimistic, response], [optimistic])).toEqual([
      optimistic,
      response,
    ])
  })

  it("keeps the optimistic transcript while hydration is empty", () => {
    expect(selectThreadMessages([], [optimistic])).toEqual([optimistic])
  })
})
