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
