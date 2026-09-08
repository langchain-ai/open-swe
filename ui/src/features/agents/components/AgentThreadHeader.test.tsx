/** @vitest-environment jsdom */

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { AgentThreadHeader } from "./AgentThreadHeader"
import type { AgentThread } from "@/features/agents/lib/types"

const pinMutate = vi.fn()
const resolveMutate = vi.fn()
const deleteMutate = vi.fn()

vi.mock("@/features/agents/lib/queries", () => ({
  useSidebarPinnedThreads: () => ({ data: [] }),
  usePinAgentThread: () => ({ isPending: false, mutate: pinMutate }),
  useResolveAgentThread: () => ({ isPending: false, mutate: resolveMutate }),
  useDeleteAgentThread: () => ({ isPending: false, mutate: deleteMutate }),
}))

const thread = {
  id: "thread-1",
  title: "Show the thread title",
  repo: "open-swe",
  repoFullName: "langchain-ai/open-swe",
  branch: "main",
  model: "model",
  status: "idle",
  viewed: true,
  createdAt: 1,
  updatedAt: 1,
  messages: [],
  sandboxId: "sandbox-1",
} satisfies AgentThread

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("AgentThreadHeader", () => {
  it("shows the title and opens the thread menu on right-click", async () => {
    render(
      <AgentThreadHeader
        title={thread.title}
        project={thread.repoFullName}
        target="Cloud"
        panelCollapsed={false}
        thread={thread}
      />
    )

    const title = screen.getByText(thread.title)
    expect(title).toBeTruthy()
    fireEvent.contextMenu(title)

    await waitFor(() => expect(screen.getByText("Pin thread")).toBeTruthy())
  })
})
