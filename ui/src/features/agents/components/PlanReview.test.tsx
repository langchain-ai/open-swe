/** @vitest-environment jsdom */

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { QueryClientProvider } from "@tanstack/react-query"
import { TooltipProvider } from "@langchain/macaw-components/Tooltip"

import { InlinePlanArtifact } from "./InlinePlanArtifact"
import { PlanReview } from "./PlanReview"
import { makeQueryClient } from "@/lib/query"
import type { PlanData, PlanTextAnchor } from "@/lib/plan"
import { parseExcerpts } from "@/features/agents/utils/codeExcerpt"

const mocks = vi.hoisted(() => ({
  addPlanComment: vi.fn(),
  getPlanComments: vi.fn(),
  deletePlanComment: vi.fn(),
  submitPlanComments: vi.fn(),
  reportError: vi.fn(),
  getPlan: vi.fn(),
  dismissPlan: vi.fn(),
}))

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => vi.fn(),
  Link: ({ children }: { children: React.ReactNode }) => <a>{children}</a>,
}))
vi.mock("@/lib/useIsMobile", () => ({ useMediaQuery: () => true }))
vi.mock("@/features/agents/components/AgentThreadPage", async () => {
  const { useContext } = await import("react")
  const { ChatExcerptChips, ChatExcerptsContext } =
    await import("./composer/ChatExcerptChips")
  const { serializeExcerpts } =
    await import("@/features/agents/utils/codeExcerpt")
  return {
    AgentThreadPage: ({
      threadId,
      embedded,
    }: {
      threadId: string
      embedded: boolean
    }) => {
      const excerpts = useContext(ChatExcerptsContext)
      return (
        <div
          data-testid="thread-chat"
          data-thread-id={threadId}
          data-embedded={String(embedded)}
        >
          {excerpts && <ChatExcerptChips {...excerpts} />}
          <output data-testid="model-input">
            {serializeExcerpts("Question", excerpts?.excerpts ?? [])}
          </output>
        </div>
      )
    },
  }
})

vi.mock("@/lib/errorReporting", () => ({ reportError: mocks.reportError }))
vi.mock("@/lib/plan", () => ({
  addPlanComment: mocks.addPlanComment,
  deletePlanComment: mocks.deletePlanComment,
  getPlanComments: mocks.getPlanComments,
  submitPlanComments: mocks.submitPlanComments,
  getPlan: mocks.getPlan,
  dismissPlan: mocks.dismissPlan,
}))
vi.mock("@/features/agents/components/PlanArtifactFrame", () => ({
  PlanArtifactFrame: ({
    html,
    className,
    onTextSelected,
  }: {
    html: string
    className?: string
    onTextSelected?: (anchor: PlanTextAnchor | null) => void
  }) => (
    <div data-testid="plan-artifact-frame" className={className}>
      {html}
      <button
        type="button"
        onClick={() =>
          onTextSelected?.({
            exact: "Plan",
            prefix: "",
            suffix: " details",
            context_before: "Distant before\nSection heading\nPreceding line",
            context_after:
              "Following line\nSecond following line\nDistant after",
            start: 0,
            end: 4,
          })
        }
      >
        Select text
      </button>
      <button type="button" onClick={() => onTextSelected?.(null)}>
        Clear selection
      </button>
    </div>
  ),
}))
vi.mock("@/features/agents/components/chat/Markdown", () => ({
  Markdown: ({ content }: { content: string }) => <div>{content}</div>,
}))

const plan: PlanData = {
  threadId: "thread-1",
  status: "shared",
  html: "<h1>Plan</h1>",
  markdown: "",
  dismissed: false,
  user: {
    id: "user-1",
    login: "alice",
    email: "alice@example.com",
    name: "Alice",
  },
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("InlinePlanArtifact", () => {
  it("hides immediately and restores the artifact if dismissal fails", async () => {
    let rejectDismiss!: (error: Error) => void
    mocks.dismissPlan.mockReturnValueOnce(
      new Promise((_, reject) => {
        rejectDismiss = reject
      })
    )
    const artifact = { ...plan, html: "", markdown: "Plan details" }
    mocks.getPlan.mockResolvedValue(artifact)
    const client = makeQueryClient()
    client.setQueryData(["plan", plan.threadId], artifact)
    render(
      <QueryClientProvider client={client}>
        <TooltipProvider>
          <InlinePlanArtifact threadId={plan.threadId} />
        </TooltipProvider>
      </QueryClientProvider>
    )

    fireEvent.click(screen.getByRole("button", { name: "Dismiss artifact" }))
    await waitFor(() =>
      expect(screen.queryByTestId("inline-plan-artifact")).toBeNull()
    )
    rejectDismiss(new Error("Offline"))
    expect(await screen.findByTestId("inline-plan-artifact")).toBeTruthy()
    client.clear()
  })
})

describe("PlanReview", () => {
  it("attaches selections to the conversation composer without a separate submission flow", () => {
    render(
      <TooltipProvider>
        <PlanReview plan={plan} />
      </TooltipProvider>
    )

    const chat = screen.getByTestId("thread-chat")
    expect(chat.dataset.threadId).toBe(plan.threadId)
    expect(chat.dataset.embedded).toBe("true")
    expect(screen.queryByRole("button", { name: "Submit comments" })).toBeNull()
    expect(screen.queryByTestId("comment-input")).toBeNull()

    fireEvent.click(screen.getByRole("button", { name: "Select text" }))
    const selections = screen.getByRole("list", {
      name: "Selections attached to your next message",
    })
    expect(selections.textContent).toContain("Artifact")
    fireEvent.click(screen.getByRole("button", { name: "Clear selection" }))
    expect(selections.textContent).toContain("Artifact")
    const modelInput = screen.getByTestId("model-input").textContent ?? ""
    expect(modelInput).toContain(
      "Context before (not selected):\n  | Section heading\n  | Preceding line\nSelected text:\n> 1 | Plan\nContext after (not selected):\n  | Following line\n  | Second following line"
    )
    expect(modelInput).not.toContain("Distant before")
    expect(modelInput).not.toContain("Distant after")
    expect(parseExcerpts(modelInput).excerpts[0]?.code).toBe("Plan")
    fireEvent.click(screen.getByRole("button", { name: "Select text" }))
    expect(
      screen.getAllByRole("button", { name: "Remove Artifact selection" })
    ).toHaveLength(1)
    fireEvent.click(
      screen.getByRole("button", { name: "Remove Artifact selection" })
    )
    expect(
      screen.queryByRole("list", {
        name: "Selections attached to your next message",
      })
    ).toBeNull()
    expect(mocks.addPlanComment).not.toHaveBeenCalled()
    expect(mocks.submitPlanComments).not.toHaveBeenCalled()
  })
})
