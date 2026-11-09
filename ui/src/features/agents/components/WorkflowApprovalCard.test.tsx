/** @vitest-environment jsdom */

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { TooltipProvider } from "@langchain/macaw-components/Tooltip"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { WorkflowApprovalCard } from "./WorkflowApprovalCard"
import type { WorkflowPushApproval } from "@/features/agents/lib/types"

const mocks = vi.hoisted(() => ({
  mutate: vi.fn(),
  useWorkflowApprovals: vi.fn(),
  useWorkflowApprovalDecision: vi.fn(),
}))

vi.mock("@/features/agents/lib/queries", () => ({
  agentMutationKeys: {
    workflowDecision: (threadId: string) => ["workflow-approvals", threadId],
  },
  useWorkflowApprovals: mocks.useWorkflowApprovals,
  useWorkflowApprovalDecision: mocks.useWorkflowApprovalDecision,
}))

function renderCard(props: {
  threadId: string
  pollWhileActive?: boolean
  latestUserMessageAt?: string
}) {
  return render(<WorkflowApprovalCard {...props} />, {
    wrapper: ({ children }) => (
      <QueryClientProvider client={new QueryClient()}>
        <TooltipProvider>{children}</TooltipProvider>
      </QueryClientProvider>
    ),
  })
}

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
  mocks.useWorkflowApprovals.mockReturnValue({
    data: { approvals: [approval] },
  })
  mocks.useWorkflowApprovalDecision.mockReturnValue({
    mutate: mocks.mutate,
    isPending: false,
  })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("WorkflowApprovalCard", () => {
  it("renders the pending approval as a compact inline card", () => {
    renderCard({ threadId: "thread-1", pollWhileActive: true })

    const group = screen.getByTestId("workflow-approval-group")
    expect(group.className).toContain("mt-space-4")
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

  it("collapses after a follow-up and lets the user reopen the approval", () => {
    mocks.useWorkflowApprovals.mockReturnValue({
      data: {
        approvals: [{ ...approval, requestedAt: "2026-11-08T16:00:00Z" }],
      },
    })
    const { rerender } = renderCard({
      threadId: "thread-1",
      latestUserMessageAt: "2026-11-08T15:59:00Z",
    })
    expect(
      screen.getByRole("button", { name: "Approve & continue push" })
    ).toBeTruthy()

    rerender(
      <WorkflowApprovalCard
        threadId="thread-1"
        latestUserMessageAt="2026-11-08T16:01:00Z"
      />
    )
    expect(
      screen.queryByRole("button", { name: "Approve & continue push" })
    ).toBeNull()
    fireEvent.click(
      screen.getByRole("button", { name: /Expand workflow approval/ })
    )
    expect(
      screen.getByRole("button", { name: "Approve & continue push" })
    ).toBeTruthy()
    fireEvent.click(
      screen.getByRole("button", { name: "Collapse workflow approval" })
    )
    expect(
      screen.queryByRole("button", { name: "Approve & continue push" })
    ).toBeNull()
  })

  it("keeps a newly requested approval expanded after earlier chat", () => {
    mocks.useWorkflowApprovals.mockReturnValue({
      data: {
        approvals: [{ ...approval, requestedAt: "2026-11-08T16:02:00Z" }],
      },
    })
    renderCard({
      threadId: "thread-1",
      latestUserMessageAt: "2026-11-08T16:01:00Z",
    })
    expect(
      screen.getByRole("button", { name: "Approve & continue push" })
    ).toBeTruthy()
  })

  it("replaces a stale card for the same branch while keeping other branches", () => {
    const { rerender } = renderCard({ threadId: "thread-1" })
    const replacement = {
      ...approval,
      fingerprint: "fingerprint-2",
      requestedAt: "2026-11-08T16:02:00Z",
      diffStats: { files: 1, additions: 153, deletions: 0 },
    }
    mocks.useWorkflowApprovals.mockReturnValue({
      data: {
        approvals: [
          { ...approval, requestedAt: "2026-11-08T16:00:00Z" },
          replacement,
          { ...approval, fingerprint: "other-branch", branch: "another-fix" },
        ],
      },
    })
    rerender(<WorkflowApprovalCard threadId="thread-1" />)

    expect(screen.queryByText("Approval ID: fingerprint-1")).toBeNull()
    expect(screen.getByText("Approval ID: fingerprint-2")).toBeTruthy()
    expect(screen.getByText("Approval ID: other-branch")).toBeTruthy()
    fireEvent.click(
      screen.getAllByRole("button", { name: "Approve & continue push" })[0]!
    )
    expect(mocks.mutate).toHaveBeenCalledWith({
      fingerprint: "fingerprint-2",
      decision: "approve",
    })
  })

  it.each(["approved", "rejected"] as const)(
    "shows %s without restoring stale pending actions",
    (status) => {
      mocks.useWorkflowApprovals.mockReturnValue({
        data: {
          approvals: [
            { ...approval, requestedAt: "2026-11-08T16:00:00Z" },
            {
              ...approval,
              fingerprint: "fingerprint-2",
              requestedAt: "2026-11-08T16:02:00Z",
              status,
            },
          ],
        },
      })
      renderCard({ threadId: "thread-1" })
      expect(screen.getByText(`Workflow changes ${status}`)).toBeTruthy()
      expect(
        screen.queryByRole("button", { name: "Approve & continue push" })
      ).toBeNull()
      expect(screen.queryByText("Approval ID: fingerprint-1")).toBeNull()
    }
  )

  it("keeps worker approval actionable when the parent has approved", () => {
    mocks.useWorkflowApprovals.mockReturnValue({
      data: { approvals: [{ ...approval, coordinatorStatus: "approved" }] },
    })
    renderCard({ threadId: "worker" })
    expect(screen.getByText(/approved on parent/)).toBeTruthy()
    fireEvent.click(
      screen.getByRole("button", { name: "Approve & continue push" })
    )
    expect(mocks.mutate).toHaveBeenCalledWith({
      fingerprint: approval.fingerprint,
      decision: "approve",
    })
  })

  it("describes workflow changes inherited from the base branch", () => {
    mocks.useWorkflowApprovals.mockReturnValue({
      data: { approvals: [{ ...approval, inheritedFrom: "main" }] },
    })

    renderCard({ threadId: "thread-1" })

    expect(
      screen.getByText("Confirm workflow changes inherited from main")
    ).toBeTruthy()
    expect(screen.getByText(/Robin did not author/)).toBeTruthy()
  })

  it("submits the selected decision with the server fingerprint", async () => {
    renderCard({ threadId: "thread-1" })

    fireEvent.click(
      screen.getByRole("button", { name: "Approve & continue push" })
    )

    await waitFor(() =>
      expect(mocks.mutate).toHaveBeenCalledWith({
        fingerprint: "fingerprint-1",
        decision: "approve",
      })
    )
  })
})
