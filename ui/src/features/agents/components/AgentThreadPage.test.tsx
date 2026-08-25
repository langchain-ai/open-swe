/** @vitest-environment jsdom */

import { cleanup, render } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { AgentThreadPage } from "./AgentThreadPage"
import { useAgentThread } from "@/features/agents/lib/queries"

vi.mock("@tanstack/react-router", () => ({ Navigate: () => null }))
vi.mock("@/features/agents/components/AgentThreadView", () => ({
  AgentThreadView: () => null,
}))
vi.mock("@/features/agents/lib/provider/useIsInAgentThreadStream", () => ({
  AgentThreadStreamBoundary: ({ children }: { children: React.ReactNode }) =>
    children,
}))
vi.mock("@/features/agents/lib/queries", () => ({
  useAgentThread: vi.fn(),
}))

const threadQuery = {
  data: { id: "thread-1", title: "Fix web title" },
  isLoading: false,
  isError: false,
}

afterEach(() => {
  cleanup()
  document.title = "Open SWE"
})

describe("AgentThreadPage", () => {
  it("uses the active thread title as the document title", () => {
    vi.mocked(useAgentThread).mockReturnValue(threadQuery as never)

    const view = render(<AgentThreadPage threadId="thread-1" />)

    expect(document.title).toBe("Fix web title - Open SWE")
    view.unmount()
    expect(document.title).toBe("Open SWE")
  })

  it("does not update the title for an inactive cached thread", () => {
    vi.mocked(useAgentThread).mockReturnValue(threadQuery as never)
    document.title = "Current thread - Open SWE"

    render(<AgentThreadPage threadId="thread-1" active={false} />)

    expect(document.title).toBe("Current thread - Open SWE")
  })
})
