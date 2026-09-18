import { describe, expect, it, vi } from "vitest"

import {
  applyEvent,
  applySnapshot,
  fromSnapshot,
  isOffloading,
  prependTurns,
  routedNotice,
  toMessages,
} from "./reducer"
import type { Message, ToolExecutionChunk } from "@/features/agents/lib/types"
import type { AnyImageChunk } from "@/features/agents/lib/types"
import type {
  MessageCompletedPayload,
  RunNoticePayload,
  StoredEvent,
  TranscriptImage,
  TranscriptMessageRow,
  TranscriptSnapshot,
  TranscriptToolCallRow,
  TranscriptTurnPage,
  TranscriptTurnRow,
} from "./types"

// The reducer only reaches the network to lazily load a tool's full output,
// which no test here expands; attachment URLs are built, never fetched.
vi.mock(import("./api"), async (importOriginal) => ({
  ...(await importOriginal()),
  fetchToolOutput: vi.fn(),
}))

function turn(
  turnId: string,
  requestedAt: string,
  state: TranscriptTurnRow["state"] = "completed"
): TranscriptTurnRow {
  return { turn_id: turnId, state, requested_at: requestedAt, error: null }
}

function messageRow(
  row: Partial<TranscriptMessageRow> & {
    message_id: string
    turn_id: string
    created_at: string
  }
): TranscriptMessageRow {
  return {
    role: "ai",
    text: "",
    reasoning: "",
    namespace: [],
    images: null,
    usage: null,
    ...row,
  }
}

function toolCall(
  row: Partial<TranscriptToolCallRow> & {
    tool_call_id: string
    turn_id: string
    name: string
    started_at: string
  }
): TranscriptToolCallRow {
  return {
    input: {},
    status: "completed",
    output_preview: null,
    has_output: false,
    namespace: [],
    ...row,
  }
}

function snapshot(
  overrides: Partial<TranscriptSnapshot> = {}
): TranscriptSnapshot {
  return {
    thread_id: "thread-1",
    version: 10,
    thread: { status: "idle" },
    turns: [],
    messages: [],
    tool_calls: [],
    notices: [],
    older_cursor: null,
    ...overrides,
  }
}

function twoTurnSnapshot(
  overrides: Partial<TranscriptSnapshot> = {}
): TranscriptSnapshot {
  return snapshot({
    turns: [
      turn("turn-1", "2026-01-01T00:00:00Z"),
      turn("turn-2", "2026-01-01T00:01:00Z", "running"),
    ],
    messages: [
      messageRow({
        message_id: "human-1",
        turn_id: "turn-1",
        role: "human",
        text: "first ask",
        created_at: "2026-01-01T00:00:00Z",
      }),
      messageRow({
        message_id: "ai-1",
        turn_id: "turn-1",
        text: "done",
        reasoning: "thinking",
        created_at: "2026-01-01T00:00:01Z",
      }),
      messageRow({
        message_id: "human-2",
        turn_id: "turn-2",
        role: "human",
        text: "second ask",
        created_at: "2026-01-01T00:01:00Z",
      }),
      messageRow({
        message_id: "ai-2",
        turn_id: "turn-2",
        text: "delegating",
        created_at: "2026-01-01T00:01:01Z",
      }),
      // A subagent's own message: not part of the root transcript.
      messageRow({
        message_id: "ai-nested",
        turn_id: "turn-2",
        text: "looking",
        namespace: ["task-1"],
        created_at: "2026-01-01T00:01:03Z",
      }),
    ],
    tool_calls: [
      toolCall({
        tool_call_id: "read-1",
        turn_id: "turn-1",
        name: "read_file",
        input: { file_path: "app.py" },
        started_at: "2026-01-01T00:00:02Z",
      }),
      toolCall({
        tool_call_id: "task-1",
        turn_id: "turn-2",
        name: "task",
        input: { subagent_type: "explorer" },
        status: "in_progress",
        started_at: "2026-01-01T00:01:02Z",
      }),
      toolCall({
        tool_call_id: "grep-1",
        turn_id: "turn-2",
        name: "grep",
        namespace: ["task-1"],
        started_at: "2026-01-01T00:01:04Z",
      }),
    ],
    ...overrides,
  })
}

function chunkKinds(entry: Message): Array<string> {
  return entry.chunks.map((chunk) => chunk.kind)
}

describe("transcript snapshot", () => {
  it("renders a turn as one human message followed by one agent message", () => {
    const messages = toMessages(fromSnapshot(twoTurnSnapshot()))

    expect(
      messages.map((entry) => [entry.author, entry.id, entry.turnKey])
    ).toEqual([
      ["user", "human-1", undefined],
      ["agent", "ai-1", "human-1"],
      ["user", "human-2", undefined],
      ["agent", "ai-2", "human-2"],
    ])
    expect(chunkKinds(messages[1]!)).toEqual([
      "reasoning",
      "text",
      "tool-execution",
    ])
    expect(messages[1]?.startedAt).toBe("2026-01-01T00:00:01Z")
    expect(messages[1]?.timestamp).toBe("2026-01-01T00:00:02Z")
  })

  it("keeps a subagent's nested work out of the root transcript, on its task call", () => {
    const messages = toMessages(fromSnapshot(twoTurnSnapshot()))
    const chunks = messages[3]?.chunks ?? []
    const tools = chunks.filter(
      (chunk): chunk is ToolExecutionChunk => chunk.kind === "tool-execution"
    )

    expect(tools.map((tool) => tool.toolCallId)).toEqual(["task-1"])
    expect(tools[0]?.toolKind).toBe("task")
    expect(tools[0]?.status).toBe("in_progress")
    expect(tools[0]?.subagentNamespace).toEqual(["task-1"])
  })
})

function appended(
  version: number,
  fragment: { text?: string; reasoning?: string }
): StoredEvent {
  return {
    version,
    occurred_at: "2026-01-01T00:02:00Z",
    event_type: "message.appended",
    payload: {
      turn_id: "turn-2",
      message_id: "ai-3",
      namespace: [],
      text: fragment.text ?? null,
      reasoning: fragment.reasoning ?? null,
    },
  }
}

function completed(
  version: number,
  payload: Partial<MessageCompletedPayload> = {}
): StoredEvent {
  return {
    ...appended(version, {}),
    event_type: "message.completed",
    payload: {
      turn_id: "turn-2",
      message_id: "ai-3",
      namespace: [],
      role: "ai",
      text: "",
      reasoning: "",
      images: null,
      usage: null,
      created_at: "2026-01-01T00:02:00Z",
      ...payload,
    },
  }
}

describe("transcript events", () => {
  it("concatenates fragments and then takes the completed text as canonical", () => {
    const base = fromSnapshot(twoTurnSnapshot())
    const streamed = applyEvent(
      applyEvent(base, appended(11, { text: "Hel" })),
      appended(12, { text: "lo" })
    )

    expect(streamed.messages["ai-3"]?.text).toBe("Hello")

    const settled = applyEvent(streamed, completed(13, { text: "Hello there" }))

    expect(settled.messages["ai-3"]?.text).toBe("Hello there")
    expect(settled.version).toBe(13)
  })

  it("drops events at or below the version already applied", () => {
    const base = fromSnapshot(twoTurnSnapshot())
    const applied = applyEvent(base, appended(11, { text: "once" }))

    expect(applyEvent(applied, appended(11, { text: "twice" }))).toBe(applied)
    expect(applyEvent(applied, appended(10, { text: "stale" }))).toBe(applied)
    expect(applied.messages["ai-3"]?.text).toBe("once")
  })

  it("keeps untouched turns' messages identical when another turn changes", () => {
    const base = fromSnapshot(twoTurnSnapshot())
    const before = toMessages(base)
    const after = toMessages(applyEvent(base, appended(11, { text: "more" })))

    expect(after[0]).toBe(before[0])
    expect(after[1]).toBe(before[1])
    // The changed turn's human message survives too; only its agent row is rebuilt.
    expect(after[2]).toBe(before[2])
    expect(after[3]).not.toBe(before[3])
  })

  it("reports the thread as running as soon as a turn is requested", () => {
    const requested = applyEvent(fromSnapshot(twoTurnSnapshot()), {
      ...appended(11, {}),
      event_type: "turn.requested",
      payload: {
        turn_id: "turn-3",
        message_id: "human-3",
        text: "third ask",
        images: [],
      },
    })

    expect(requested.status).toBe("running")
  })
})

function notice(version: number, payload: RunNoticePayload): StoredEvent {
  return { ...appended(version, {}), event_type: "run.notice", payload }
}

function image(overrides: Partial<TranscriptImage> = {}): TranscriptImage {
  return {
    mime_type: "image/png",
    file_name: "shot.png",
    url: null,
    attachment_id: null,
    ...overrides,
  }
}

function imagesOf(entry: Message | undefined): Array<AnyImageChunk> {
  return (entry?.chunks ?? []).filter(
    (chunk): chunk is AnyImageChunk => chunk.kind === "image"
  )
}

describe("notices", () => {
  it("keeps a routed badge from the snapshot and drops it when a new turn opens", () => {
    const reloaded = fromSnapshot(
      snapshot({
        turns: [turn("turn-2", "2026-01-01T00:01:00Z")],
        notices: [
          {
            turn_id: "turn-2",
            kind: "model_routed",
            data: { route: "deep", model_id: "opus" },
          },
        ],
      })
    )
    expect(routedNotice(reloaded)).toEqual({ route: "deep", modelId: "opus" })

    const nextTurn = applyEvent(reloaded, {
      ...appended(11, {}),
      event_type: "turn.requested",
      payload: {
        turn_id: "turn-3",
        message_id: "human-3",
        text: "again",
        images: [],
      },
    })

    expect(routedNotice(nextTurn)).toBeNull()
  })

  it("stops reporting offloading once the turn it described settles", () => {
    const started = applyEvent(
      fromSnapshot(twoTurnSnapshot()),
      notice(11, {
        turn_id: "turn-2",
        kind: "conversation_offloading",
        data: { status: "started" },
      })
    )
    expect(isOffloading(started)).toBe(true)

    const settled = applyEvent(started, {
      ...appended(12, {}),
      event_type: "turn.completed",
      payload: { turn_id: "turn-2" },
    })

    expect(isOffloading(settled)).toBe(false)
  })
})

describe("message images", () => {
  it("renders an attachment as an image chunk pointing at the transcript API", () => {
    const messages = toMessages(
      fromSnapshot(
        snapshot({
          turns: [turn("turn-1", "2026-01-01T00:00:00Z")],
          messages: [
            messageRow({
              message_id: "human-1",
              turn_id: "turn-1",
              role: "human",
              text: "look at this",
              created_at: "2026-01-01T00:00:00Z",
              images: [
                image({
                  attachment_id: "11111111-2222-3333-4444-555555555555",
                }),
                image({
                  file_name: null,
                  url: "https://example.test/remote.png",
                }),
                // No bytes were ever captured for this one.
                image({ file_name: "lost.png" }),
              ],
            }),
          ],
        })
      )
    )

    expect(imagesOf(messages[0])).toEqual([
      {
        kind: "image",
        url: expect.stringContaining(
          "/threads/thread-1/transcript/attachments/11111111-2222-3333-4444-555555555555"
        ) as unknown as string,
        credentials: "session",
        mimeType: "image/png",
        fileName: "shot.png",
      },
      {
        kind: "image",
        url: "https://example.test/remote.png",
        credentials: "none",
        mimeType: "image/png",
      },
    ])
  })
})

describe("windowed reads", () => {
  function olderPage(
    overrides: Partial<TranscriptTurnPage> = {}
  ): TranscriptTurnPage {
    return {
      turns: [turn("turn-0", "2025-12-31T23:00:00Z")],
      messages: [
        messageRow({
          message_id: "human-0",
          turn_id: "turn-0",
          role: "human",
          text: "the oldest ask",
          created_at: "2025-12-31T23:00:00Z",
        }),
      ],
      tool_calls: [],
      older_cursor: null,
      ...overrides,
    }
  }

  it("orders a prepended page ahead of the window, and advances the cursor", () => {
    const state = fromSnapshot(twoTurnSnapshot({ older_cursor: "page-2" }))
    const merged = prependTurns(state, olderPage({ older_cursor: "page-3" }))

    expect(merged.turnOrder).toEqual(["turn-0", "turn-1", "turn-2"])
    expect(toMessages(merged)[0]?.chunks).toEqual([
      { kind: "text", text: "the oldest ask" },
    ])
    expect(merged.olderCursor).toBe("page-3")
    // Settled turns are immutable, so their rendered rows are kept as-is.
    expect(merged.turns["turn-1"]).toBe(state.turns["turn-1"])
  })

  it("a snapshot frame keeps history older than the window it carries", () => {
    const loaded = prependTurns(
      fromSnapshot(twoTurnSnapshot({ older_cursor: "page-2" })),
      olderPage({ older_cursor: "page-3" })
    )
    const refreshed = applySnapshot(
      loaded,
      snapshot({
        version: 42,
        turns: [turn("turn-2", "2026-01-01T00:01:00Z")],
        messages: [
          messageRow({
            message_id: "ai-2",
            turn_id: "turn-2",
            text: "the newest answer",
            created_at: "2026-01-01T00:01:30Z",
          }),
        ],
        older_cursor: "page-fresh",
      })
    )

    expect(refreshed.version).toBe(42)
    expect(refreshed.turnOrder).toEqual(["turn-0", "turn-1", "turn-2"])
    expect(refreshed.messages["human-0"]?.text).toBe("the oldest ask")
    expect(refreshed.messages["ai-2"]?.text).toBe("the newest answer")
    // The window's own cursor points at history this client already holds.
    expect(refreshed.olderCursor).toBe("page-3")
  })
})
