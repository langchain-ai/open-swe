import { describe, expect, it } from "vitest"

import { selectThreadMessages } from "./threadMessages"
import type { Message } from "./types"

const previous: Message = {
  id: "previous",
  author: "agent",
  timestamp: "2026-08-22T00:00:00Z",
  chunks: [{ kind: "text", text: "Previous thread" }],
}
const current: Message = {
  id: "current",
  author: "user",
  timestamp: "2026-08-22T00:01:00Z",
  chunks: [{ kind: "text", text: "Current thread" }],
}

describe("selectThreadMessages", () => {
  it("ignores stale messages until the destination thread hydrates", () => {
    expect(selectThreadMessages([previous], [current], false)).toEqual([
      current,
    ])
    expect(selectThreadMessages([current], [], true)).toEqual([current])
  })
})
