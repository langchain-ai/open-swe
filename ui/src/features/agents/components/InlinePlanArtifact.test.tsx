/** @vitest-environment jsdom */

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"
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
  window.localStorage.clear()
  getPlan.mockReturnValue({ html: "", markdown: "Implementation plan" })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("InlinePlanArtifact", () => {
  it("keeps a dismissed plan hidden across renders", async () => {
    const { unmount } = render(<InlinePlanArtifact threadId="thread-1" />)
    await screen.findByTestId("inline-plan-artifact")

    fireEvent.click(screen.getByRole("button", { name: "Dismiss plan" }))
    expect(screen.queryByTestId("inline-plan-artifact")).toBeNull()

    unmount()
    render(<InlinePlanArtifact threadId="thread-1" />)
    await waitFor(() =>
      expect(screen.queryByTestId("inline-plan-artifact")).toBeNull()
    )
  })

  it("shows a revised plan after the previous version was dismissed", async () => {
    window.localStorage.setItem(
      "open-swe.agents.dismissed-plan.thread-1",
      "Previous plan"
    )

    render(<InlinePlanArtifact threadId="thread-1" />)

    expect(await screen.findByText("Implementation plan")).toBeTruthy()
  })
})
