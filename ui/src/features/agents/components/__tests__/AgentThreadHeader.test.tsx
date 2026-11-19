/** @vitest-environment jsdom */

import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { TooltipProvider } from "@langchain/macaw-components/Tooltip"
import { AgentThreadHeader } from "@/features/agents/components/AgentThreadHeader"
import type { AgentThread } from "@/features/agents/lib/types"

import { toast } from "sonner"

const { shareThread, useSidebarPinnedThreads, useSidebarRepos } = vi.hoisted(
  () => ({
    shareThread: vi.fn(),
    useSidebarPinnedThreads: vi.fn(() => ({ data: [] })),
    useSidebarRepos: vi.fn(() => ({ data: [] })),
  })
)
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }))
vi.mock("@/lib/session", () => ({
  useSession: () => ({ data: { login: "owner" } }),
}))
vi.mock("@/features/agents/lib/sidebarPrefs", () => ({
  useSidebarPrefs: () => ({ prefs: { filters: {} } }),
}))
vi.mock("@/features/agents/lib/queries", () => ({
  useSidebarPinnedThreads,
  useSidebarRepos,
  useSetAgentThreadViewed: () => ({ isPending: false, mutate: vi.fn() }),
  usePinAgentThread: () => ({ isPending: false, mutate: vi.fn() }),
  useResolveAgentThread: () => ({ isPending: false, mutate: vi.fn() }),
  useDeleteAgentThread: () => ({ isPending: false, mutate: vi.fn() }),
  useContinueThreadPrivately: () => ({ isPending: false, mutate: vi.fn() }),
  useShareThread: () => ({
    isPending: false,
    mutate: shareThread,
  }),
}))

const title = "Show the thread title"

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.clearAllMocks()
})

describe("AgentThreadHeader", () => {
  it("shares the thread without copying its link automatically", async () => {
    const copyPageLink = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal("openSweDesktop", { copyPageLink })
    render(
      <AgentThreadHeader
        panelCollapsed={false}
        thread={
          {
            id: "thread-1",
            repoFullName: "",
            ownerLogin: "owner",
            visibility: "private",
          } as AgentThread
        }
      />
    )
    fireEvent.keyDown(
      screen.getByRole("button", { name: "Thread visibility" }),
      {
        key: "Enter",
      }
    )
    fireEvent.click(
      await screen.findByRole("menuitemradio", { name: "Shared" })
    )
    fireEvent.click(screen.getByRole("checkbox"))
    fireEvent.click(screen.getByRole("button", { name: "Share entire thread" }))
    expect(copyPageLink).not.toHaveBeenCalled()
    expect(toast.success).not.toHaveBeenCalled()
    const [, callbacks] = shareThread.mock.calls[0] as [
      string,
      { onSuccess: () => void },
    ]
    await act(async () => callbacks.onSuccess())
    expect(copyPageLink).not.toHaveBeenCalled()
    expect(toast.success).not.toHaveBeenCalled()
    expect(screen.queryByRole("dialog")).toBeNull()
  })

  it("offers a web link after a private thread is shared in desktop", async () => {
    const copyPageLink = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal("openSweDesktop", { copyPageLink })
    const thread = {
      id: "thread-1",
      title,
      repoFullName: "",
      visibility: "private",
    } as AgentThread
    const { rerender } = render(
      <TooltipProvider>
        <AgentThreadHeader panelCollapsed={false} thread={thread} />
      </TooltipProvider>
    )
    fireEvent.keyDown(
      screen.getByRole("button", { name: "Thread visibility" }),
      {
        key: "Enter",
      }
    )
    await screen.findByRole("menuitemradio", { name: "Private" })
    expect(
      screen.queryByRole("menuitem", { name: "Copy thread link" })
    ).toBeNull()
    rerender(
      <TooltipProvider>
        <AgentThreadHeader
          panelCollapsed={false}
          thread={{ ...thread, visibility: "public" }}
        />
      </TooltipProvider>
    )
    fireEvent.click(
      await screen.findByRole("menuitem", { name: "Copy thread link" })
    )
    await waitFor(() => expect(copyPageLink).toHaveBeenCalledOnce())
  })

  it("saves a trimmed title once on Enter then blur", async () => {
    const onRename = vi.fn().mockResolvedValue(undefined)
    render(
      <TooltipProvider>
        <AgentThreadHeader
          panelCollapsed={false}
          thread={{ id: "thread-1", title, repoFullName: "" } as AgentThread}
          onRename={onRename}
        />
      </TooltipProvider>
    )
    fireEvent.click(screen.getByRole("button", { name: "Rename thread" }))
    const input = screen.getByRole("textbox") as HTMLInputElement
    expect(document.activeElement).toBe(input)
    expect(input.selectionEnd).toBe(title.length)
    fireEvent.change(input, { target: { value: "  New title  " } })
    fireEvent.keyDown(input, { key: "Enter" })
    fireEvent.blur(input)
    expect(onRename).toHaveBeenCalledExactlyOnceWith("New title")
    await waitFor(() =>
      expect(
        (
          screen.getByRole("button", {
            name: "Rename thread",
          }) as HTMLButtonElement
        ).disabled
      ).toBe(false)
    )
  })

  it("keeps rename focus and returns menu focus normally while a rename is saving", async () => {
    let finishRename!: () => void
    const onRename = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          finishRename = resolve
        })
    )
    render(
      <TooltipProvider>
        <AgentThreadHeader
          panelCollapsed={false}
          thread={{ id: "thread-1", title, repoFullName: "" } as AgentThread}
          onRename={onRename}
        />
      </TooltipProvider>
    )
    fireEvent.keyDown(screen.getByRole("button", { name: "Thread actions" }), {
      key: "Enter",
    })
    fireEvent.click(await screen.findByRole("menuitem", { name: "Rename" }))
    await waitFor(() =>
      expect(document.activeElement).toBe(
        screen.getByRole("textbox", { name: "Thread title" })
      )
    )
    const input = screen.getByRole("textbox", { name: "Thread title" })
    fireEvent.change(input, { target: { value: "New title" } })
    fireEvent.keyDown(input, { key: "Enter" })
    const actions = screen.getByRole("button", { name: "Thread actions" })
    fireEvent.keyDown(actions, { key: "Enter" })
    const pin = await screen.findByRole("menuitem", { name: "Pin" })
    expect(screen.queryByRole("menuitem", { name: "Rename" })).toBeNull()
    fireEvent.keyDown(pin, { key: "Escape" })
    await waitFor(() => expect(document.activeElement).toBe(actions))
    finishRename()
    await waitFor(() =>
      expect(
        (
          screen.getByRole("button", {
            name: "Rename thread",
          }) as HTMLButtonElement
        ).disabled
      ).toBe(false)
    )
  })

  it("discards edits on Escape", () => {
    const onRename = vi.fn()
    render(
      <TooltipProvider>
        <AgentThreadHeader
          panelCollapsed={false}
          thread={{ id: "thread-1", title, repoFullName: "" } as AgentThread}
          onRename={onRename}
        />
      </TooltipProvider>
    )
    fireEvent.click(screen.getByRole("button", { name: "Rename thread" }))
    const input = screen.getByRole("textbox")
    fireEvent.change(input, { target: { value: "Discard this" } })
    fireEvent.keyDown(input, { key: "Escape" })
    fireEvent.blur(input)
    expect(onRename).not.toHaveBeenCalled()
    expect(screen.queryByRole("textbox")).toBeNull()
    expect(screen.getByText(title)).toBeTruthy()
  })

  it("renders repository indicator from thread metadata without fetching repos or pins on remount", () => {
    render(
      <TooltipProvider>
        <AgentThreadHeader
          panelCollapsed={false}
          thread={
            {
              id: "thread-1",
              title,
              repo: "langchainplus",
              repoFullName: "langchain-ai/langchainplus",
            } as AgentThread
          }
        />
      </TooltipProvider>
    )
    expect(
      screen.getByRole("button", { name: "Repository: langchainplus" })
    ).toBeTruthy()
    expect(useSidebarRepos).toHaveBeenCalledWith(
      expect.objectContaining({ enabled: false })
    )
    expect(useSidebarPinnedThreads).toHaveBeenCalledWith({
      enabled: true,
      refetchOnMount: false,
    })
  })
})
