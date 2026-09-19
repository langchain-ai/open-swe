import { describe, expect, it } from "vitest"
import type { SubmissionQueueEntry } from "@langchain/react"

import type {
  PendingThreadMessage,
  QueuedThreadMessage,
} from "@/features/agents/lib/types"
import {
  queueEntryToMessage,
  visiblePendingMessages,
} from "@/features/agents/lib/queuedMessages"

function entry(
  values: SubmissionQueueEntry["values"]
): SubmissionQueueEntry {
  return { id: "entry-1", values, createdAt: new Date(2_000) }
}

describe("queueEntryToMessage", () => {
  it("extracts text and image blocks from the submitted message", () => {
    const message = queueEntryToMessage(
      entry({
        messages: [
          {
            id: "message-1",
            content: [
              { type: "image", base64: "img", mime_type: "image/png" },
              { type: "text", text: "follow up" },
            ],
          },
        ],
      })
    )

    expect(message).toEqual({
      id: "message-1",
      content: "follow up",
      images: [{ kind: "image", base64: "img", mimeType: "image/png" }],
      createdAt: 2_000,
    })
  })

  it("returns null when the queued entry has no message id", () => {
    expect(
      queueEntryToMessage(entry({ messages: [{ content: "no id" }] }))
    ).toBeNull()
  })

  it("handles plain string content", () => {
    const message = queueEntryToMessage(
      entry({ messages: [{ id: "message-1", content: "just text" }] })
    )

    expect(message).toEqual({
      id: "message-1",
      content: "just text",
      images: [],
      createdAt: 2_000,
    })
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

  it("excludes a pending message once it appears in the queue", () => {
    const pending: PendingThreadMessage = {
      id: "message-1",
      content: "hi",
      createdAt: 2_000,
      status: "sending",
    }
    const queued: Array<QueuedThreadMessage> = [
      { id: "message-1", content: "hi", images: [], createdAt: 2_000 },
    ]

    expect(visiblePendingMessages([pending], [], queued)).toEqual([])
  })
})
