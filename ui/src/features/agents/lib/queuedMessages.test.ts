import { describe, expect, it } from "vitest"

import type { PendingThreadMessage } from "@/features/agents/lib/types"
import {
  queueEntryToTurn,
  visiblePendingMessages,
} from "@/features/agents/lib/queuedMessages"

describe("queueEntryToTurn", () => {
  it("retains batched text and replies for display and cancellation recovery", () => {
    const content = "Thanks!"
    const replies = [
      {
        source: "conversation" as const,
        before: "",
        selection: "Opening a pull request.",
        after: "",
        comment: "Which branch?",
      },
    ]
    const turn = queueEntryToTurn(
      {
        id: "queued-turn",
        runId: "queued-run",
        createdAt: new Date(2_000),
        values: {
          messages: [
            { id: "context", content: "Context" },
            { id: "reply", content, additional_kwargs: { replies } },
          ],
        },
      },
      "alice"
    )

    expect(turn?.message).toMatchObject({
      id: "reply",
      chunks: [{ kind: "text", text: content }],
      replies,
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
})
