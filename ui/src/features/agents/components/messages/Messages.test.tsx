/** @vitest-environment jsdom */

import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { Messages } from "./Messages"

vi.mock("@/features/agents/components/WorkflowApprovalCard", () => ({
  WorkflowApprovalCard: ({ threadId }: { threadId: string }) => (
    <div data-testid="workflow-approval-card">Approval for {threadId}</div>
  ),
}))

afterEach(() => cleanup())

describe("Messages", () => {
  it("shows run activity while a stream is starting with no messages", () => {
    render(<Messages messages={[]} isStreaming />)

    expect(screen.getByRole("status").textContent).toBe("Working…")
  })

  it("renders a sent Slack reply before the work that follows it", () => {
    render(
      <Messages
        isStreaming
        messages={[
          {
            id: "agent-turn",
            author: "agent",
            timestamp: "2026-09-03T10:30:00.000Z",
            chunks: [
              {
                kind: "tool-execution",
                toolCallId: "reply",
                title: "Slack thread reply",
                toolKind: "slack",
                status: "completed",
                input: { message: "On it!" },
              },
              {
                kind: "tool-execution",
                toolCallId: "shell",
                title: "sleep 20",
                toolKind: "execute",
                status: "in_progress",
              },
            ],
          },
        ]}
      />
    )

    const reply = screen.getByText("On it!")
    const work = screen.getByRole("button", {
      name: "Running… · 1 action",
    })
    expect(
      reply.compareDocumentPosition(work) & Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy()
  })

  it("renders unfinished grouped work after its fold row", () => {
    render(
      <Messages
        isStreaming={false}
        messages={[
          {
            id: "agent-turn",
            author: "agent",
            timestamp: "2026-09-03T10:30:00.000Z",
            chunks: [
              {
                kind: "tool-execution",
                toolCallId: "reply",
                title: "Slack thread reply",
                toolKind: "slack",
                status: "completed",
                input: { message: "On it!" },
              },
              {
                kind: "tool-execution",
                toolCallId: "task",
                title: "Task",
                toolKind: "task",
                status: "in_progress",
              },
            ],
          },
        ]}
      />
    )

    const reply = screen.getByText("On it!")
    const fold = screen.getByRole("button", { name: "Worked · 1 action" })
    const groupedWork = screen.getByText("Task")

    expect(
      reply.compareDocumentPosition(fold) & Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy()
    expect(
      fold.compareDocumentPosition(groupedWork) &
        Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy()
  })

  it("keeps workflow approval available alongside an empty-state error", () => {
    render(
      <Messages
        messages={[]}
        threadId="thread-1"
        emptyState={<div>Messages could not be loaded</div>}
        isStreaming={false}
      />
    )

    expect(screen.getByText("Messages could not be loaded")).toBeTruthy()
    expect(screen.getByTestId("workflow-approval-card").textContent).toBe(
      "Approval for thread-1"
    )
  })
})
