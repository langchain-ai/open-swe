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

vi.mock("@/lib/session", () => ({ useSession: () => ({ data: null }) }))
vi.mock("@/features/agents/lib/desktopProjects", () => ({
  useDesktopProjects: () => ({ projects: [] }),
}))
vi.mock("@tanstack/react-router", () => ({ useNavigate: () => vi.fn() }))
vi.mock("@/features/agents/lib/desktopLocal", () => ({
  useRefreshLocalThreads: () => vi.fn(),
}))
vi.mock("@/features/agents/lib/sidebarPrefs", () => ({
  useSidebarPrefs: () => ({
    prefs: { pinnedLocalIds: [], filters: {} },
    toggleLocalPin: vi.fn(),
  }),
}))
vi.mock("@/features/agents/lib/queries", () => ({
  useSidebarPinnedThreads: () => ({ data: [] }),
  useSidebarProjects: () => ({ data: [] }),
  usePinAgentThread: () => ({ isPending: false, mutate: vi.fn() }),
  useResolveAgentThread: () => ({ isPending: false, mutate: vi.fn() }),
  useDeleteAgentThread: () => ({ isPending: false, mutate: vi.fn() }),
}))

const title = "Show the thread title"

afterEach(cleanup)

describe("AgentThreadHeader", () => {
  it("saves a trimmed title once on Enter then blur", async () => {
    const onRename = vi.fn().mockResolvedValue(undefined)
    render(
      <AgentThreadHeader
        title={title}
        target="Cloud"
        panelCollapsed={false}
        onRename={onRename}
      />
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

  it("discards edits on Escape", () => {
    const onRename = vi.fn()
    render(
      <AgentThreadHeader
        title={title}
        target="Cloud"
        panelCollapsed={false}
        onRename={onRename}
      />
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
})
