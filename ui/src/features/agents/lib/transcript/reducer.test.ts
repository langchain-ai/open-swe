import { describe, expect, it, vi } from "vitest"

import {
  applyEvent,
  applySnapshot,
  fromSnapshot,
  isOffloading,
  prependTurns,
  queuedTurns,
  routedNotice,
  subagentMessages,
  toMessages,
} from "./reducer"
import type { TranscriptState } from "./reducer"
import type { Message, ToolExecutionChunk } from "@/features/agents/lib/types"
import type { AnyImageChunk } from "@/features/agents/lib/types"
import type {
  MessageCompletedPayload,
  RunNoticePayload,
  StoredEvent,
  ToolCompletedPayload,
  TranscriptAttachment,
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
  return {
    turn_id: turnId,
    run_id: null,
    state,
    requested_at: requestedAt,
    started_at: state === "requested" ? null : requestedAt,
    error: null,
  }
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
    attachments: null,
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
  it("projects a subagent's namespace as its own transcript", () => {
    const state = fromSnapshot(twoTurnSnapshot())

    const messages = subagentMessages(state, ["task-1"])
    expect(messages.map((entry) => [entry.author, entry.id])).toEqual([
      ["agent", "ai-nested"],
    ])
    expect(chunkKinds(messages[0]!)).toEqual(["text", "tool-execution"])
    const call = messages[0]!.chunks[1] as ToolExecutionChunk
    expect(call.toolCallId).toBe("grep-1")
    // The same state still renders the root without the subagent's rows.
    const rootCalls = toMessages(state).flatMap((entry) =>
      entry.chunks.flatMap((chunk) =>
        chunk.kind === "tool-execution" ? [chunk.toolCallId] : []
      )
    )
    expect(rootCalls).toEqual(["read-1", "task-1"])
  })

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
      attachments: null,
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
        attachments: [],
      },
    })

    expect(requested.status).toBe("running")
  })

  it("keeps a queued follow-up out of the record until its run starts", () => {
    const requested = applyEvent(fromSnapshot(twoTurnSnapshot()), {
      ...appended(11, {}),
      event_type: "turn.requested",
      payload: {
        turn_id: "turn-3",
        message_id: "human-3",
        text: "third ask",
        attachments: [],
      },
    })
    const queued = applyEvent(requested, {
      ...appended(12, {}),
      event_type: "turn.queued",
      payload: { turn_id: "turn-3", run_id: "run-3" },
    })
    const inRecord = (state: TranscriptState) =>
      toMessages(state).some((message) => message.id === "human-3")

    expect(queuedTurns(queued).map((entry) => entry.runId)).toEqual(["run-3"])
    expect(queuedTurns(queued)[0]?.message.id).toBe("human-3")
    expect(inRecord(queued)).toBe(false)

    // Withdrawn before it ran: gone from the queue and never in the record.
    const withdrawn = applyEvent(queued, {
      ...appended(13, {}),
      event_type: "turn.interrupted",
      payload: { turn_id: "turn-3" },
    })
    expect(queuedTurns(withdrawn)).toEqual([])
    expect(inRecord(withdrawn)).toBe(false)
    // turn-2 is still running in the fixture: withdrawing a queued follow-up
    // never idles the thread.
    expect(withdrawn.status).toBe("running")

    // Started: an ordinary turn from here on.
    const started = applyEvent(queued, {
      ...appended(13, {}),
      event_type: "turn.started",
      payload: { turn_id: "turn-3" },
    })
    expect(queuedTurns(started)).toEqual([])
    expect(inRecord(started)).toBe(true)
  })

  it("stays running while a queued follow-up waits behind the turn that ended", () => {
    const base = fromSnapshot(twoTurnSnapshot())
    const running = applyEvent(base, {
      ...appended(11, {}),
      event_type: "turn.started",
      payload: { turn_id: "turn-3" },
    })
    const queued = applyEvent(
      applyEvent(running, {
        ...appended(12, {}),
        event_type: "turn.requested",
        payload: {
          turn_id: "turn-4",
          message_id: "human-4",
          text: "and then this",
          attachments: [],
        },
      }),
      {
        ...appended(13, {}),
        event_type: "turn.queued",
        payload: { turn_id: "turn-4", run_id: "run-4" },
      }
    )
    const ended = applyEvent(queued, {
      ...appended(14, {}),
      event_type: "turn.completed",
      payload: { turn_id: "turn-3" },
    })
    expect(ended.status).toBe("running")
    expect(queuedTurns(ended)).toHaveLength(1)
  })

  it("settles while a requested turn has no run that could start it", () => {
    const orphaned = applyEvent(fromSnapshot(twoTurnSnapshot()), {
      ...appended(11, {}),
      event_type: "turn.requested",
      payload: {
        turn_id: "turn-3",
        message_id: "human-3",
        text: "never started",
        attachments: [],
      },
    })
    const ended = applyEvent(orphaned, {
      ...appended(12, {}),
      event_type: "turn.completed",
      payload: { turn_id: "turn-2" },
    })
    expect(ended.status).toBe("idle")
  })
})

function notice(version: number, payload: RunNoticePayload): StoredEvent {
  return { ...appended(version, {}), event_type: "run.notice", payload }
}

function attachment(
  overrides: Partial<TranscriptAttachment> = {}
): TranscriptAttachment {
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
        attachments: [],
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
              attachments: [
                attachment({
                  attachment_id: "11111111-2222-3333-4444-555555555555",
                }),
                attachment({
                  file_name: null,
                  url: "https://example.test/remote.png",
                }),
                // No bytes were ever captured for this one.
                attachment({ file_name: "lost.png" }),
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

  it("starts over from a snapshot whose window no longer reaches the history held", () => {
    const loaded = prependTurns(
      fromSnapshot(twoTurnSnapshot({ older_cursor: "page-2" })),
      olderPage({ older_cursor: null })
    )

    expect(loaded.olderCursor).toBeNull()

    const refreshed = applySnapshot(
      loaded,
      snapshot({
        version: 99,
        turns: [turn("turn-9", "2026-01-01T09:00:00Z")],
        messages: [
          messageRow({
            message_id: "human-9",
            turn_id: "turn-9",
            role: "human",
            text: "much later",
            created_at: "2026-01-01T09:00:00Z",
          }),
        ],
        older_cursor: "page-fresh",
      })
    )

    // Turns between the held history and the fresh window are missing, so the
    // fresh paging path has to survive for them to ever be reachable.
    expect(refreshed.turnOrder).toEqual(["turn-9"])
    expect(refreshed.olderCursor).toBe("page-fresh")
    expect(refreshed.messages["human-0"]).toBeUndefined()
  })
})

function toolCompleted(
  version: number,
  payload: Partial<ToolCompletedPayload> = {}
): StoredEvent {
  return {
    ...appended(version, {}),
    event_type: "tool.completed",
    payload: {
      turn_id: "turn-1",
      tool_call_id: "read-1",
      status: "completed",
      output_preview: null,
      output_truncated: false,
      has_output: true,
      ...payload,
    },
  }
}

function readChunk(state: TranscriptState): ToolExecutionChunk | undefined {
  return toMessages(state)
    .flatMap((entry) => entry.chunks)
    .find(
      (chunk): chunk is ToolExecutionChunk =>
        chunk.kind === "tool-execution" && chunk.toolCallId === "read-1"
    )
}

describe("tool output", () => {
  it("keeps the full output loadable when the preview stopped at the cap", () => {
    // A long result is not `output_truncated`: that flag is about the stored
    // output hitting its own cap, not about the preview being cut.
    const settled = applyEvent(
      fromSnapshot(twoTurnSnapshot()),
      toolCompleted(11, { output_preview: "x".repeat(2000) })
    )

    expect(readChunk(settled)?.loadOutput).toBeTypeOf("function")
  })

  it("offers no fetch once the preview is known to be the whole output", () => {
    const settled = applyEvent(
      fromSnapshot(twoTurnSnapshot()),
      toolCompleted(11, { output_preview: "short" })
    )

    expect(readChunk(settled)?.output).toBe("short")
    expect(readChunk(settled)?.loadOutput).toBeUndefined()
  })

  it("keeps the full output loadable when the stored output was capped", () => {
    const settled = applyEvent(
      fromSnapshot(twoTurnSnapshot()),
      toolCompleted(11, { output_preview: "short", output_truncated: true })
    )

    expect(readChunk(settled)?.loadOutput).toBeTypeOf("function")
  })
})

describe("context meter", () => {
  const usage = { input_tokens: 90, output_tokens: 10 }

  it("reads the newest root usage from a snapshot, not a subagent's", () => {
    const state = fromSnapshot(
      twoTurnSnapshot({
        messages: [
          messageRow({
            message_id: "ai-root",
            turn_id: "turn-1",
            usage,
            created_at: "2026-01-01T00:00:01Z",
          }),
          messageRow({
            message_id: "ai-nested",
            turn_id: "turn-1",
            namespace: ["task-1"],
            usage: { input_tokens: 5, output_tokens: 1 },
            created_at: "2026-01-01T00:00:02Z",
          }),
        ],
      })
    )

    expect(state.contextTokens).toBe(100)
  })

  it("leaves the meter alone when a subagent's message completes", () => {
    const base = applyEvent(
      fromSnapshot(twoTurnSnapshot()),
      completed(11, { usage })
    )
    const nested = applyEvent(
      base,
      completed(12, {
        message_id: "ai-sub",
        namespace: ["task-1"],
        usage: { input_tokens: 40_000 },
      })
    )

    expect(base.contextTokens).toBe(100)
    expect(nested.contextTokens).toBe(100)
  })
})
