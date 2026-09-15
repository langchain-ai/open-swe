/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { InlinePlanArtifact } from "./InlinePlanArtifact"

const navigate = vi.fn()
const getPlan = vi.fn()

vi.mock("@tanstack/react-query", () => ({
  useQuery: ({ queryFn }: { queryFn: () => unknown }) => ({ data: queryFn() }),
}))
vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => navigate,
}))
vi.mock("@/lib/plan", () => ({ getPlan: () => getPlan() }))
vi.mock("@/features/agents/components/chat/Markdown", () => ({
  Markdown: ({ content }: { content: string }) => <div>{content}</div>,
}))

beforeEach(() => {
  getPlan.mockReturnValue({ html: "", markdown: "Implementation plan" })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("InlinePlanArtifact", () => {
  it("dismisses the plan for the current view", async () => {
    render(<InlinePlanArtifact threadId="thread-1" />)
    await screen.findByTestId("inline-plan-artifact")

    fireEvent.click(screen.getByRole("button", { name: "Dismiss plan" }))

    expect(screen.queryByTestId("inline-plan-artifact")).toBeNull()
  })
})
