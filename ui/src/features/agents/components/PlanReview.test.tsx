/** @vitest-environment jsdom */

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import type { PlanData } from "@/lib/plan"
import { PlanReview } from "./PlanReview"

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
}))

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => mocks.navigate,
}))
vi.mock("@/lib/plan", () => ({
  approvePlan: vi.fn(),
}))
vi.mock("@/features/agents/components/PlanArtifactFrame", () => ({
  PlanArtifactFrame: ({ html }: { html: string }) => <div>{html}</div>,
}))
vi.mock("@/components/markdown/Markdown", () => ({
  Markdown: ({ content }: { content: string }) => <div>{content}</div>,
}))

const plan: PlanData = {
  threadId: "thread-1",
  status: "ready",
  html: "<h1>Plan</h1>",
  markdown: "",
  isOwner: true,
  approvedBy: null,
  approvedAt: null,
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

describe("PlanReview", () => {
  it("returns request-change feedback to the conversation", async () => {
    render(<PlanReview plan={plan} />)

    expect(screen.queryByTestId("edit-plan")).toBeNull()
    expect(screen.queryByTestId("plan-editor")).toBeNull()
    expect(screen.queryByTestId("plan-comments")).toBeNull()

    const requestChanges = screen.getByRole("button", {
      name: "Request changes",
    })
    expect((requestChanges as HTMLButtonElement).disabled).toBe(false)
    fireEvent.click(requestChanges)

    await waitFor(() =>
      expect(mocks.navigate).toHaveBeenCalledWith({
        to: "/agents/$threadId",
        params: { threadId: "thread-1" },
        search: { feedback: true },
      })
    )
  })
})
