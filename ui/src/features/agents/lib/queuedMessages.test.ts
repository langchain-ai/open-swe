import { describe, expect, it } from "vitest"

import { visibleQueuedMessages } from "@/features/agents/lib/queuedMessages"
import type { Message, QueuedThreadMessage } from "@/features/agents/lib/types"

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
      timestamp: new Date(1_000).toISOString(),
      timestampIsFallback: true,
      chunks: [{ kind: "text", text: "follow up" }],
    }

    expect(visibleQueuedMessages([queued], [streamed])).toEqual([])
  })
})
