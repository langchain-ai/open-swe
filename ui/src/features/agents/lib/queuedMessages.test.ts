import { describe, expect, it } from "vitest"

import type { PendingThreadMessage } from "@/features/agents/lib/types"
import {
  queueEntryToTurn,
  visiblePendingMessages,
} from "@/features/agents/lib/queuedMessages"

const ALICE = { login: "alice", user_id: "u-alice" }

const envelope = (sender: string) =>
  `<input-message sender="${sender}" surface="web" kind="human"><content>Check the tests</content></input-message>`

function storedEntry(sender: string) {
  return {
    id: "stored-turn",
    runId: "stored-run",
    createdAt: new Date(2_000),
    values: {
      messages: [
        {
          id: "context",
          content:
            '<dynamic-context kind="person" id="user:u-alice">\ndisplay_name: Alice\n</dynamic-context>',
        },
        {
          id: "stored",
          content: [{ type: "text", text: envelope(sender) }],
          additional_kwargs: {
            open_swe_provenance: { kind: "human", sender, surface: "web" },
          },
        },
      ],
    },
  }
}

describe("queueEntryToTurn", () => {
  it("shows a queued message rebuilt from the server's run as its text, not its envelope", () => {
    expect(
      queueEntryToTurn(storedEntry("user:u-alice"), ALICE)?.message.chunks
    ).toEqual([{ kind: "text", text: "Check the tests" }])
  })

  it("leaves out a queued run of a plugin's tool results, which nobody wrote", () => {
    expect(
      queueEntryToTurn(
        {
          id: "tool-turn",
          createdAt: new Date(2_000),
          values: {
            messages: [
              {
                type: "tool",
                id: "client-tool-result-call-1",
                tool_call_id: "call-1",
                content: "42 rows",
              },
            ],
          },
        },
        ALICE
      )
    ).toBeNull()
  })

  it("keeps a message queued from this tab as typed and the viewer's, even when it reads like an envelope", () => {
    const typed = envelope("user:u-bob")
    const turn = queueEntryToTurn(
      {
        id: "local-turn",
        createdAt: new Date(2_000),
        values: {
          messages: [{ id: "typed", content: [{ type: "text", text: typed }] }],
        },
      },
      ALICE
    )

    expect(turn).toMatchObject({
      message: { chunks: [{ kind: "text", text: typed }] },
      senderLogin: "alice",
    })
  })

  it.each([
    ["user:u-alice", "alice"],
    ["github:Alice", "alice"],
    ["user:u-bob", null],
    ["slack:U0BOB", null],
  ])(
    "attributes a queued message the server says %s sent to %s",
    (sender, senderLogin) => {
      expect(queueEntryToTurn(storedEntry(sender), ALICE)?.senderLogin).toBe(
        senderLogin
      )
    }
  )

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
      ALICE
    )

    expect(turn).toMatchObject({
      message: {
        id: "reply",
        chunks: [{ kind: "text", text: content }],
        replies,
      },
      senderLogin: "alice",
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
