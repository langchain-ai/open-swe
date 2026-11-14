/** @vitest-environment jsdom */

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, expect, it, vi } from "vitest"
import { TooltipProvider } from "@langchain/macaw-components/Tooltip"

import { AgentRightPanel } from "./AgentRightPanel"
import { useRightPanelStore } from "@/features/agents/lib/rightPanelStore"
import type { TerminalGroupsController } from "@/features/agents/lib/terminalGroups"

vi.mock("@/lib/appCommands", () => ({ useRegisterAppCommands: () => {} }))

const threadRef = { scope: "cloud", threadId: "panel-thread" } as const
const terminals: TerminalGroupsController = {
  state: {
    terminalIds: [],
    activeTerminalId: "",
    terminalGroups: [],
    activeTerminalGroupId: "",
    nextTerminalNumber: 1,
  },
  metadataById: new Map(),
  error: null,
  addGroup: vi.fn(),
  closeGroup: vi.fn(),
  closeTerminal: vi.fn(),
  focus: vi.fn(),
  split: vi.fn(),
  clear: vi.fn(),
  restart: vi.fn(),
  clearRequests: new Map(),
  restartRequests: new Map(),
}

beforeEach(() => {
  useRightPanelStore.setState({ byThreadKey: {} })
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  }))
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

it("collapses only when the last surface closes and allows reopening the empty launcher", () => {
  const onCollapsedChange = vi.fn()
  const props = {
    threadRef,
    terminals,
    terminalTarget: { kind: "cloud", threadId: threadRef.threadId } as const,
    cwd: "",
    terminalAvailable: false,
    diffAvailable: true,
    renderDiff: () => <div>Changes content</div>,
    renderBrowser: () => <div>Browser content</div>,
    collapsed: false,
    onCollapsedChange,
  }
  useRightPanelStore.getState().open(threadRef, "diff")
  useRightPanelStore.getState().open(threadRef, "preview")
  const { rerender } = render(
    <TooltipProvider>
      <AgentRightPanel {...props} />
    </TooltipProvider>
  )

  fireEvent.click(screen.getByRole("button", { name: "Close Browser" }))
  expect(screen.getByText("Changes content")).toBeTruthy()
  expect(onCollapsedChange).not.toHaveBeenCalled()

  fireEvent.click(screen.getByRole("button", { name: "Close Changes" }))
  expect(onCollapsedChange).toHaveBeenCalledExactlyOnceWith(true)
  rerender(
    <TooltipProvider>
      <AgentRightPanel {...props} collapsed />
    </TooltipProvider>
  )
  expect(screen.queryByLabelText("Open a surface")).toBeNull()
  fireEvent.click(screen.getByRole("button", { name: "Show panel" }))
  expect(onCollapsedChange).toHaveBeenLastCalledWith(false)
  rerender(
    <TooltipProvider>
      <AgentRightPanel {...props} />
    </TooltipProvider>
  )
  expect(screen.getByLabelText("Open a surface")).toBeTruthy()
  act(() => useRightPanelStore.getState().open(threadRef, "preview"))
  fireEvent.click(screen.getByRole("button", { name: "Close Browser" }))
  expect(onCollapsedChange).toHaveBeenLastCalledWith(true)
})
