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

  it("reconciles image-only messages", () => {
    const image = {
      kind: "image" as const,
      base64: "abc",
      mimeType: "image/png",
    }
    const queued: QueuedThreadMessage = {
      id: "queued-1",
      content: "",
      images: [image],
      createdAt: 2_000,
    }
    const streamed: Message = {
      id: "message-1",
      author: "user",
      timestamp: new Date(3_000).toISOString(),
      chunks: [image],
    }

    expect(visibleQueuedMessages([queued], [streamed])).toEqual([])
  })

  it("does not reconcile a substring match", () => {
    const queued: QueuedThreadMessage = {
      id: "queued-1",
      content: "ok",
      createdAt: 2_000,
    }
    const streamed: Message = {
      id: "message-1",
      author: "user",
      timestamp: new Date(3_000).toISOString(),
      chunks: [{ kind: "text", text: "looks ok" }],
    }

    expect(visibleQueuedMessages([queued], [streamed])).toEqual([queued])
  })
})
