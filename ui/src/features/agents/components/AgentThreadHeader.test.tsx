/** @vitest-environment jsdom */

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { ContextMenu } from "@base-ui/react/context-menu"
import { ThreadContextMenuPopup } from "./ThreadContextMenuPopup"
import { AgentThreadHeader } from "./AgentThreadHeader"
import type { AgentThread } from "@/features/agents/lib/types"

const projectState = vi.hoisted(() => ({
  projects: [] as Array<{
    cwd: string
    name: string
    addedAt: number
    scopeId: string
  }>,
}))
vi.mock("@/lib/session", () => ({ useSession: () => ({ data: null }) }))
vi.mock("@/features/agents/lib/desktopProjects", () => ({
  useDesktopProjects: () => ({
    projects: projectState.projects,
  }),
}))
const navigate = vi.fn()
const refreshLocalThreads = vi.fn()
const toggleLocalPin = vi.fn()
vi.mock("@tanstack/react-router", () => ({ useNavigate: () => navigate }))
vi.mock("@/features/agents/lib/desktopLocal", () => ({
  useRefreshLocalThreads: () => refreshLocalThreads,
}))
vi.mock("@/features/agents/lib/sidebarPrefs", () => ({
  useSidebarPrefs: () => ({
    prefs: {
      pinnedLocalIds: [],
      filters: {},
    },
    toggleLocalPin,
  }),
}))
const localThread = {
  id: "local-1",
  title: "Local thread",
  cwd: "/project",
  worktreePath: null,
  viewed: true,
  createdAt: 1,
  updatedAt: 1,
  modelId: null,
  effort: null,
}

const pinMutate = vi.fn()
const resolveMutate = vi.fn()
const deleteMutate = vi.fn()

vi.mock("@/features/agents/lib/queries", () => ({
  useSidebarPinnedThreads: () => ({ data: [] }),
  useSidebarProjects: () => ({ data: [] }),
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
  projectState.projects = []
  vi.clearAllMocks()
  delete window.openSweDesktop
})

describe("AgentThreadHeader", () => {
  it("hides project controls for unregistered local folders and ownerless cloud threads", () => {
    const { rerender } = render(
      <AgentThreadHeader
        title="Local"
        target="This Mac"
        panelCollapsed={false}
        localThread={localThread}
      />
    )
    expect(screen.queryByRole("button", { name: /^Project:/ })).toBeNull()
    rerender(
      <AgentThreadHeader
        title="Cloud"
        target="Cloud"
        panelCollapsed={false}
        thread={{ ...thread, repoFullName: "", repo: "" }}
      />
    )
    expect(screen.queryByRole("button", { name: /^Project:/ })).toBeNull()
  })

  it.each(["Cloud", "This Mac"] as const)(
    "shows the %s project icon before the title with a hover tooltip",
    async (target) => {
      projectState.projects = [
        {
          cwd: "/project",
          name: "My project",
          addedAt: 1,
          scopeId: "project-1",
        },
      ]
      const name = target === "Cloud" ? "open-swe" : "My project"
      render(
        <AgentThreadHeader
          title={thread.title}
          target={target}
          panelCollapsed={false}
          thread={target === "Cloud" ? thread : undefined}
          localThread={target === "This Mac" ? localThread : undefined}
          onRename={vi.fn()}
        />
      )
      const indicator = screen.getByRole("button", { name: `Project: ${name}` })
      expect(indicator.querySelector("svg")).toBeTruthy()
      expect(
        indicator.compareDocumentPosition(
          screen.getByRole("button", { name: "Rename thread" })
        ) & Node.DOCUMENT_POSITION_FOLLOWING
      ).toBeTruthy()
      expect(screen.queryByText(name)).toBeNull()
      fireEvent.mouseEnter(indicator)
      fireEvent.mouseMove(indicator)
      expect((await screen.findByText(name)).getAttribute("data-slot")).toBe(
        "tooltip-popup"
      )
      fireEvent.click(indicator)
      expect(screen.queryByRole("dialog")).toBeNull()
      expect(screen.queryByRole("menu")).toBeNull()
      expect(screen.getByText(name).textContent).toBe(name)
      fireEvent.pointerLeave(indicator)
      await waitFor(() => expect(screen.queryByText(name)).toBeNull())
      expect(screen.queryByText(/tasks?|\/project/)).toBeNull()
    }
  )

  it("opens the project tooltip on click and dismisses on Escape or blur", async () => {
    render(
      <AgentThreadHeader
        title={thread.title}
        target="Cloud"
        panelCollapsed={false}
        thread={thread}
      />
    )
    const indicator = screen.getByRole("button", { name: "Project: open-swe" })
    expect(screen.queryByText("open-swe")).toBeNull()
    fireEvent.click(indicator)
    expect((await screen.findByText("open-swe")).textContent).toBe("open-swe")
    fireEvent.keyDown(indicator, { key: "Escape" })
    await waitFor(() => expect(screen.queryByText("open-swe")).toBeNull())
    fireEvent.click(indicator)
    expect((await screen.findByText("open-swe")).textContent).toBe("open-swe")
    fireEvent.blur(indicator)
    await waitFor(() => expect(screen.queryByText("open-swe")).toBeNull())
  })

  it("shows the title and opens the thread menu on right-click", async () => {
    render(
      <AgentThreadHeader
        title={thread.title}
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
  it.each(["Cloud", "This Mac"] as const)(
    "matches the sidebar %s menu for click and right-click",
    async (target) => {
      const cloud =
        target === "Cloud"
          ? {
              ...thread,
              traceUrl: "https://example.com/trace",
              sourceUrl: "https://example.com/slack",
            }
          : undefined
      render(
        <ContextMenu.Root>
          <ContextMenu.Trigger>Sidebar row</ContextMenu.Trigger>
          <ThreadContextMenuPopup
            thread={cloud ?? null}
            pinned={false}
            archived={false}
            isDeleting={false}
            onTogglePin={vi.fn()}
            onToggleArchived={vi.fn()}
            onDelete={vi.fn()}
          />
        </ContextMenu.Root>
      )
      fireEvent.contextMenu(screen.getByText("Sidebar row"))
      await screen.findByRole("menuitem", { name: "Pin thread" })
      const items = () =>
        screen.getAllByRole("menuitem").map((item) => ({
          text: item.textContent,
          href: item.getAttribute("href"),
          disabled: item.getAttribute("aria-disabled"),
        }))
      const expected = items()
      cleanup()
      render(
        <AgentThreadHeader
          title={thread.title}
          target={target}
          panelCollapsed={false}
          thread={cloud}
          localThread={cloud ? undefined : localThread}
          onRename={vi.fn()}
        />
      )
      fireEvent.click(screen.getByRole("button", { name: "Thread actions" }))
      await screen.findByRole("menuitem", { name: "Pin thread" })
      expect(items()).toEqual(expected)
      fireEvent.keyDown(screen.getByRole("menu"), { key: "Escape" })
      await waitFor(() => expect(screen.queryByRole("menu")).toBeNull())
      fireEvent.contextMenu(screen.getByText(thread.title))
      await screen.findByRole("menuitem", { name: "Pin thread" })
      expect(items()).toEqual(expected)
    }
  )

  it("confirms local deletion, reports failure and navigates only after success", async () => {
    const deleteLocalThread = vi
      .fn()
      .mockResolvedValueOnce(false)
      .mockResolvedValueOnce(true)
    window.openSweDesktop = { deleteLocalThread } as unknown as NonNullable<
      Window["openSweDesktop"]
    >
    render(
      <AgentThreadHeader
        title={localThread.title}
        target="This Mac"
        panelCollapsed={false}
        localThread={{ ...localThread, ownedWorktrees: ["/worktree"] }}
      />
    )
    fireEvent.click(screen.getByRole("button", { name: "Thread actions" }))
    fireEvent.click(
      await screen.findByRole("menuitem", { name: "Delete thread" })
    )
    await screen.findByRole("dialog")
    expect(screen.getByText(/including any uncommitted changes/)).toBeTruthy()
    expect(deleteLocalThread).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole("button", { name: "Delete" }))
    await screen.findByText("Local Open SWE thread not found")
    expect(navigate).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole("button", { name: "Delete" }))
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledExactlyOnceWith({ to: "/agents" })
    )
    expect(deleteLocalThread).toHaveBeenLastCalledWith(localThread.id)
    expect(refreshLocalThreads).toHaveBeenCalledWith(localThread.id)
    expect(deleteMutate).not.toHaveBeenCalled()
  })

  it.each(["Pin thread", "Archive thread"])("runs local %s", async (action) => {
    const updateLocalThread = vi.fn().mockResolvedValue(localThread)
    window.openSweDesktop = { updateLocalThread } as unknown as NonNullable<
      Window["openSweDesktop"]
    >
    render(
      <AgentThreadHeader
        title={localThread.title}
        target="This Mac"
        panelCollapsed={false}
        localThread={localThread}
      />
    )
    fireEvent.click(screen.getByRole("button", { name: "Thread actions" }))
    fireEvent.click(await screen.findByRole("menuitem", { name: action }))
    expect(
      action === "Pin thread" ? toggleLocalPin : updateLocalThread
    ).toHaveBeenCalledExactlyOnceWith(
      action === "Pin thread"
        ? localThread.id
        : { threadId: localThread.id, archived: true }
    )
    await waitFor(() =>
      expect(refreshLocalThreads.mock.calls).toEqual(
        action === "Pin thread" ? [] : [[localThread.id]]
      )
    )
    expect(pinMutate).not.toHaveBeenCalled()
    expect(resolveMutate).not.toHaveBeenCalled()
  })

  it.each(["Pin thread", "Archive thread"])(
    "runs %s from the clicked cloud menu",
    async (action) => {
      render(
        <AgentThreadHeader
          title={thread.title}
          target="Cloud"
          panelCollapsed={false}
          thread={thread}
        />
      )
      fireEvent.click(screen.getByRole("button", { name: "Thread actions" }))
      fireEvent.click(await screen.findByRole("menuitem", { name: action }))
      expect(
        action === "Pin thread" ? pinMutate : resolveMutate
      ).toHaveBeenCalledExactlyOnceWith(
        action === "Pin thread"
          ? { threadId: thread.id, pinned: true }
          : { threadId: thread.id, resolved: true }
      )
    }
  )

  it.each(["Enter", "blur"])(
    "selects the title and saves on %s only once",
    async (action) => {
      let finish!: () => void
      const onRename = vi.fn(
        () =>
          new Promise<void>((resolve) => {
            finish = resolve
          })
      )
      render(
        <AgentThreadHeader
          title={thread.title}
          target="This Mac"
          panelCollapsed={false}
          onRename={onRename}
        />
      )
      const titleButton = screen.getByRole("button", { name: "Rename thread" })
      vi.spyOn(titleButton, "getBoundingClientRect").mockReturnValue({
        width: 240,
      } as DOMRect)
      fireEvent.click(titleButton)
      const input = screen.getByRole("textbox", {
        name: "Thread title",
      }) as HTMLInputElement
      expect(document.activeElement).toBe(input)
      expect(input.style.width).toBe("240px")
      expect(input.selectionStart).toBe(0)
      expect(input.selectionEnd).toBe(thread.title.length)
      fireEvent.change(input, { target: { value: "  New title  " } })
      if (action === "Enter") fireEvent.keyDown(input, { key: "Enter" })
      fireEvent.blur(input)
      expect(onRename).toHaveBeenCalledExactlyOnceWith("New title")
      expect(screen.getByText(thread.title)).toBeTruthy()
      expect(
        (
          screen.getByRole("button", {
            name: "Rename thread",
          }) as HTMLButtonElement
        ).disabled
      ).toBe(true)
      finish()
      await waitFor(() =>
        expect(
          (
            screen.getByRole("button", {
              name: "Rename thread",
            }) as HTMLButtonElement
          ).disabled
        ).toBe(false)
      )
    }
  )

  it.each(["Escape", "empty", "unchanged"])(
    "does not save %s edits",
    (action) => {
      const onRename = vi.fn()
      render(
        <AgentThreadHeader
          title={thread.title}
          target="Cloud"
          panelCollapsed={false}
          onRename={onRename}
        />
      )
      fireEvent.click(screen.getByRole("button", { name: "Rename thread" }))
      const input = screen.getByRole("textbox")
      fireEvent.change(input, {
        target: {
          value:
            action === "empty"
              ? "  "
              : action === "unchanged"
                ? thread.title
                : "Discard this",
        },
      })
      if (action === "Escape") fireEvent.keyDown(input, { key: "Escape" })
      fireEvent.blur(input)
      expect(onRename).not.toHaveBeenCalled()
      expect(screen.queryByRole("textbox")).toBeNull()
      expect(screen.getByText(thread.title)).toBeTruthy()
    }
  )

  it("reports failure, retains the saved title and allows retry", async () => {
    const onRename = vi
      .fn()
      .mockRejectedValueOnce(new Error("Rename failed"))
      .mockResolvedValueOnce(undefined)
    render(
      <AgentThreadHeader
        title={thread.title}
        target="Cloud"
        panelCollapsed={false}
        onRename={onRename}
      />
    )
    fireEvent.click(screen.getByRole("button", { name: "Rename thread" }))
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "New title" },
    })
    fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter" })
    expect((await screen.findByRole("alert")).textContent).toBe("Rename failed")
    expect(screen.getByText(thread.title)).toBeTruthy()
    fireEvent.click(screen.getByRole("button", { name: "Rename thread" }))
    expect(screen.queryByRole("alert")).toBeNull()
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "Retry title" },
    })
    fireEvent.blur(screen.getByRole("textbox"))
    await waitFor(() =>
      expect(onRename).toHaveBeenLastCalledWith("Retry title")
    )
  })

  it("keeps titles read-only without a rename callback", () => {
    render(
      <AgentThreadHeader
        title={thread.title}
        target="Cloud"
        panelCollapsed={false}
      />
    )
    expect(screen.queryByRole("button", { name: "Rename thread" })).toBeNull()
    expect(screen.getByText(thread.title)).toBeTruthy()
  })
})
