import { describe, expect, it } from "vitest"

import { threadMessagesForDisplay } from "./threadMessageDisplay"
import type { Message } from "./types"

function message(id: string): Message {
  return {
    id,
    author: "agent",
    timestamp: "2026-08-18T12:00:00Z",
    chunks: [{ kind: "text", text: id }],
  }
}

describe("threadMessagesForDisplay", () => {
  const retained = [message("retained")]
  const optimistic = [message("optimistic")]

  it("retains the transcript while the stream detaches for recovery", () => {
    expect(
      threadMessagesForDisplay({
        live: [],
        retained,
        optimistic: [],
        streamThreadId: null,
        threadId: "thread-1",
        isThreadLoading: false,
      })
    ).toBe(retained)
  })

  it("retains the transcript while the same thread rehydrates", () => {
    expect(
      threadMessagesForDisplay({
        live: [],
        retained,
        optimistic: [],
        streamThreadId: "thread-1",
        threadId: "thread-1",
        isThreadLoading: true,
      })
    ).toBe(retained)
  })

  it("uses fresh live messages as soon as recovery completes", () => {
    const live = [message("live")]

    expect(
      threadMessagesForDisplay({
        live,
        retained,
        optimistic,
        streamThreadId: "thread-1",
        threadId: "thread-1",
        isThreadLoading: false,
      })
    ).toBe(live)
  })

  it("does not show a retained transcript for an empty hydrated thread", () => {
    expect(
      threadMessagesForDisplay({
        live: [],
        retained,
        optimistic,
        streamThreadId: "thread-1",
        threadId: "thread-1",
        isThreadLoading: false,
      })
    ).toBe(optimistic)
  })
})
