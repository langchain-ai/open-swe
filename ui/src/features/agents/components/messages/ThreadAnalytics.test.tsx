/** @vitest-environment jsdom */

import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"

import { ThreadAnalytics } from "./ThreadAnalytics"
import type { AgentRunUsage, Message } from "@/features/agents/lib/types"

afterEach(() => cleanup())

const runs: Array<AgentRunUsage> = [
  {
    run_id: "run-1",
    model_id: "claude",
    turn_key: "user-1",
    created_at_ms: 1,
    finished_at_ms: 2,
    input_tokens: 80,
    output_tokens: 20,
    total_tokens: 100,
    cost_usd: 0.02,
  },
  {
    run_id: "run-2",
    model_id: "claude",
    turn_key: "user-2",
    created_at_ms: 3,
    finished_at_ms: 4,
    input_tokens: 120,
    output_tokens: 30,
    total_tokens: 150,
    cost_usd: null,
  },
]

const messages: Array<Message> = [
  {
    id: "user-1",
    author: "user",
    timestamp: "2026-01-01T00:00:00Z",
    chunks: [{ kind: "text", text: "First" }],
  },
  {
    id: "agent-1",
    author: "agent",
    timestamp: "2026-01-01T00:00:01Z",
    turnKey: "user-1",
    chunks: [
      {
        kind: "tool-execution",
        toolCallId: "read",
        title: "Read file",
        toolKind: "read",
        status: "completed",
      },
      {
        kind: "tool-execution",
        toolCallId: "edit",
        title: "Edit file",
        toolKind: "edit",
        status: "error",
      },
    ],
  },
  {
    id: "user-2",
    author: "user",
    timestamp: "2026-01-01T00:00:02Z",
    chunks: [{ kind: "text", text: "Second" }],
  },
  {
    id: "agent-2",
    author: "agent",
    timestamp: "2026-01-01T00:00:03Z",
    turnKey: "user-2",
    chunks: [
      {
        kind: "tool-execution",
        toolCallId: "execute",
        title: "Run tests",
        toolKind: "execute",
        status: "completed",
      },
    ],
  },
]

describe("ThreadAnalytics", () => {
  it("renders token and cost charts by turn", () => {
    render(<ThreadAnalytics messages={messages} runs={runs} />)

    expect(screen.getByRole("img", { name: "Tokens by turn" })).toBeTruthy()
    expect(screen.getByRole("img", { name: "Cost by turn" })).toBeTruthy()
    expect(screen.getByText("250 total")).toBeTruthy()
    expect(screen.getByText("$0.02 total")).toBeTruthy()
  })

  it("renders tool calls in turn order", () => {
    render(<ThreadAnalytics messages={messages} runs={runs} />)

    const read = screen.getByText("Read file")
    const edit = screen.getByText("Edit file")
    const execute = screen.getByText("Run tests")
    expect(
      read.compareDocumentPosition(edit) & Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy()
    expect(
      edit.compareDocumentPosition(execute) & Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy()
  })

  it("renders tool analytics without usage records", () => {
    render(<ThreadAnalytics messages={messages} runs={[]} />)

    expect(screen.queryByRole("img", { name: "Tokens by turn" })).toBeNull()
    expect(screen.getByText("Tool call sequence")).toBeTruthy()
  })

  it("does not shift uncorrelated usage onto later turns", () => {
    render(
      <ThreadAnalytics
        messages={messages}
        runs={[{ ...runs[1]!, turn_key: "missing-turn" }]}
      />
    )

    expect(
      screen.getByRole("img", { name: "Tokens by turn" }).textContent
    ).toContain("Turn 1: Unavailable")
    expect(
      screen.getByRole("img", { name: "Tokens by turn" }).textContent
    ).toContain("Turn 2: Unavailable")
  })

  it("does not connect trend lines through unavailable values", () => {
    const threeMessages = [
      ...messages,
      {
        ...messages[3]!,
        id: "agent-3",
        turnKey: "user-3",
        chunks: messages[3]!.chunks.map((chunk) =>
          chunk.kind === "tool-execution"
            ? { ...chunk, toolCallId: "execute-3" }
            : chunk
        ),
      },
    ]
    render(
      <ThreadAnalytics
        messages={threeMessages}
        runs={[
          runs[0]!,
          { ...runs[1]!, turn_key: "missing-turn" },
          { ...runs[1]!, run_id: "run-3", turn_key: "user-3" },
        ]}
      />
    )

    expect(
      screen
        .getByRole("img", { name: "Tokens by turn" })
        .querySelectorAll("polyline")
    ).toHaveLength(2)
  })
})
