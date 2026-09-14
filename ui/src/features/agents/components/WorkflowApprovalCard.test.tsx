/** @vitest-environment jsdom */

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { WorkflowApprovalCard } from "./WorkflowApprovalCard"
import type { WorkflowPushApproval } from "@/features/agents/lib/types"

const mocks = vi.hoisted(() => ({
  mutateAsync: vi.fn(),
  useWorkflowApprovals: vi.fn(),
  useWorkflowApprovalDecision: vi.fn(),
}))

vi.mock("@/features/agents/lib/queries", () => ({
  useWorkflowApprovals: mocks.useWorkflowApprovals,
  useWorkflowApprovalDecision: mocks.useWorkflowApprovalDecision,
}))

const approval: WorkflowPushApproval = {
  fingerprint: "fingerprint-1",
  status: "pending",
  repo: "langchain-ai/open-swe",
  branch: "open-swe/inline-workflow-approval",
  baseSha: "1234567890",
  headSha: "abcdef1234",
  files: [".github/workflows/ci.yml"],
  diffStats: { files: 1, additions: 3, deletions: 1 },
  diffPreview:
    "diff --git a/.github/workflows/ci.yml b/.github/workflows/ci.yml",
  diffPreviewTruncated: false,
  inheritedFrom: null,
  approvalUrl: null,
  requestedAt: null,
  decidedAt: null,
  decidedBy: null,
}

beforeEach(() => {
  mocks.mutateAsync.mockResolvedValue({})
  mocks.useWorkflowApprovals.mockReturnValue({
    data: { approvals: [approval] },
  })
  mocks.useWorkflowApprovalDecision.mockReturnValue({
    mutateAsync: mocks.mutateAsync,
    isPending: false,
  })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("WorkflowApprovalCard", () => {
  it("renders the pending approval as a compact inline card", () => {
    render(<WorkflowApprovalCard threadId="thread-1" pollWhileActive />)

    const group = screen.getByTestId("workflow-approval-group")
    expect(group.className).toContain("mt-4")
    expect(
      screen.getByText("Confirm GitHub Actions workflow changes")
    ).toBeTruthy()
    expect(screen.getByText("Why you need to confirm")).toBeTruthy()
    expect(
      screen.getByText("Review files and diff").closest("details")?.open
    ).toBe(false)
    expect(mocks.useWorkflowApprovals).toHaveBeenCalledWith("thread-1", {
      pollWhileActive: true,
    })
  })

  it("describes workflow changes inherited from the base branch", () => {
    mocks.useWorkflowApprovals.mockReturnValue({
      data: { approvals: [{ ...approval, inheritedFrom: "main" }] },
    })

    render(<WorkflowApprovalCard threadId="thread-1" />)

    expect(
      screen.getByText("Confirm workflow changes inherited from main")
    ).toBeTruthy()
    expect(screen.getByText(/Open SWE did not author/)).toBeTruthy()
  })

  it("submits the selected decision with the server fingerprint", async () => {
    render(<WorkflowApprovalCard threadId="thread-1" />)

    fireEvent.click(
      screen.getByRole("button", { name: "Approve & continue push" })
    )

    await waitFor(() =>
      expect(mocks.mutateAsync).toHaveBeenCalledWith({
        fingerprint: "fingerprint-1",
        decision: "approve",
      })
    )
  })
})
