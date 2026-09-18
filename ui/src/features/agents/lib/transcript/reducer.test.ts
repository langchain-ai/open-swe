import { describe, expect, it, vi } from "vitest"

import { applyEvent, fromSnapshot, toMessages } from "./reducer"
import type { Message, ToolExecutionChunk } from "@/features/agents/lib/types"
import type {
  StoredEvent,
  TranscriptMessageRow,
  TranscriptSnapshot,
  TranscriptToolCallRow,
  TranscriptTurnRow,
} from "./types"

// The reducer only reaches the network to lazily load a tool's full output,
// which no test here expands.
vi.mock("./api", () => ({ fetchToolOutput: vi.fn() }))

function turn(
  turnId: string,
  requestedAt: string,
  state: TranscriptTurnRow["state"] = "completed"
): TranscriptTurnRow {
  return {
    turn_id: turnId,
    run_id: `run-${turnId}`,
    state,
    requested_at: requestedAt,
    started_at: requestedAt,
    completed_at: state === "completed" ? requestedAt : null,
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
    streaming: false,
    namespace: [],
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
    message_id: null,
    input: {},
    status: "completed",
    output_preview: null,
    output_truncated: false,
    has_output: false,
    namespace: [],
    ended_at: null,
    ...row,
  }
}

function snapshot(overrides: Partial<TranscriptSnapshot> = {}): TranscriptSnapshot {
  return {
    thread_id: "thread-1",
    version: 10,
    thread: {
      kind: "agent",
      status: "idle",
      active_run_id: null,
      title: "Fix the build",
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:10:00Z",
    },
    turns: [],
    messages: [],
    tool_calls: [],
    notices: [],
    ...overrides,
  }
}

function twoTurnSnapshot(): TranscriptSnapshot {
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
    thread_id: "thread-1",
    version,
    event_id: `event-${version}`,
    schema_version: 1,
    run_id: "run-turn-2",
    turn_id: "turn-2",
    command_id: null,
    actor_kind: "agent",
    occurred_at: "2026-01-01T00:02:00Z",
    event_type: "message.appended",
    payload: {
      turn_id: "turn-2",
      message_id: "ai-3",
      namespace: [],
      ...fragment,
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
    expect(streamed.messages["ai-3"]?.streaming).toBe(true)

    const completed = applyEvent(streamed, {
      ...appended(13, {}),
      event_type: "message.completed",
      payload: {
        turn_id: "turn-2",
        message_id: "ai-3",
        namespace: [],
        role: "ai",
        text: "Hello there",
        reasoning: "",
        created_at: "2026-01-01T00:02:00Z",
      },
    })

    expect(completed.messages["ai-3"]?.text).toBe("Hello there")
    expect(completed.messages["ai-3"]?.streaming).toBe(false)
    expect(completed.version).toBe(13)
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
})
