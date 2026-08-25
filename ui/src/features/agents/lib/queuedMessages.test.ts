import { describe, expect, it } from "vitest"

import type { Message, QueuedThreadMessage } from "@/features/agents/lib/types"
import { visibleQueuedMessages } from "@/features/agents/lib/queuedMessages"

describe("visibleQueuedMessages", () => {
  it("reconciles a queued follow-up with its streamed fallback timestamp", () => {
    const queued: QueuedThreadMessage = {
      id: "queued-1",
      content: "follow up",
      createdAt: 2_000,
    }
    const streamed: Message = {
      id: "message-1",
      author: "user",
      timestamp: new Date(3_000).toISOString(),
      timestampIsFallback: true,
      chunks: [{ kind: "text", text: "follow up" }],
    }

    expect(visibleQueuedMessages([queued], [streamed])).toEqual([])
    expect(
      visibleQueuedMessages(
        [queued],
        [{ ...streamed, timestamp: new Date(500).toISOString() }]
      )
    ).toEqual([queued])
  })
})
