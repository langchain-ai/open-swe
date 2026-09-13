import { describe, expect, it } from "vitest"

import type {
  Message,
  PendingThreadMessage,
  QueuedThreadMessage,
} from "@/features/agents/lib/types"
import {
  visiblePendingMessages,
  visibleQueuedMessages,
} from "@/features/agents/lib/queuedMessages"

describe("visibleQueuedMessages", () => {
  it("reconciles a queued follow-up by exact message id", () => {
    const queued: QueuedThreadMessage = {
      id: "queued-1",
      content: "same text",
      createdAt: 2_000,
    }
    const streamed: Message = {
      id: "queued-1",
      author: "user",
      timestamp: new Date(500).toISOString(),
      chunks: [{ kind: "text", text: "different server envelope" }],
    }

    expect(visibleQueuedMessages([queued], [streamed])).toEqual([])
  })

  it("retains timestamp reconciliation for legacy queued records", () => {
    const queued: QueuedThreadMessage = {
      id: "queued-legacy-1",
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
  })
})

describe("visiblePendingMessages", () => {
  it("renders image-only optimistic messages and reconciles by id", () => {
    const pending: PendingThreadMessage = {
      id: "message-1",
      content: "",
      createdAt: 2_000,
      status: "sending",
      images: [
        {
          kind: "image",
          base64: "image-data",
          mimeType: "image/png",
        },
      ],
    }

    expect(visiblePendingMessages([pending], [])).toEqual([
      expect.objectContaining({
        id: "message-1",
        author: "user",
        deliveryStatus: "sending",
        chunks: pending.images,
      }),
    ])
    expect(
      visiblePendingMessages(
        [pending],
        [
          {
            id: "message-1",
            author: "user",
            timestamp: new Date(3_000).toISOString(),
            chunks: [],
          },
        ]
      )
    ).toEqual([])
  })
})
