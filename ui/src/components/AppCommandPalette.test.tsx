/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import type { DesktopLocalThreadSummary } from "@/desktop"
import type { AgentThread } from "@/features/agents/lib/types"
import type { AppCommand } from "@/lib/appCommands"
import { AppCommandPalette } from "./AppCommandPalette"

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  cloudThread: { id: "cloud-1", title: "Cloud result" } as AgentThread,
  localThread: {
    id: "local-1",
    title: "Local result",
    cwd: "/tmp/repo",
    status: "idle",
    createdAt: 1,
    updatedAt: 2,
    modelId: null,
    effort: null,
  } as DesktopLocalThreadSummary,
}))

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => mocks.navigate,
}))
vi.mock("@/features/agents/lib/queries", () => ({
  useThreadsPage: () => ({
    data: { items: [mocks.cloudThread] },
    isFetching: false,
    isError: false,
  }),
}))
vi.mock("@/features/agents/lib/desktopLocal", () => ({
  useDesktopLocalThreads: () => ({ data: [mocks.localThread] }),
}))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

const commands: Array<AppCommand> = [
  {
    id: "settings",
    label: "Open settings",
    shortcuts: ["mod+,"],
    group: "Navigation",
    run: vi.fn(),
  },
]

describe("AppCommandPalette", () => {
  it("moves through results with the keyboard and opens a cloud thread", () => {
    render(
      <AppCommandPalette commands={commands} open onOpenChange={vi.fn()} />
    )

    const input = screen.getByRole("combobox")
    fireEvent.keyDown(input, { key: "ArrowDown" })
    fireEvent.keyDown(input, { key: "Enter" })

    expect(mocks.navigate).toHaveBeenCalledWith({
      to: "/agents/$threadId",
      params: { threadId: "cloud-1" },
    })
  })

  it("filters thread titles as text and routes local results", () => {
    render(
      <AppCommandPalette commands={commands} open onOpenChange={vi.fn()} />
    )

    fireEvent.change(screen.getByRole("combobox"), {
      target: { value: "LOCAL RESULT" },
    })
    fireEvent.click(screen.getByRole("option", { name: /Local result/ }))

    expect(mocks.navigate).toHaveBeenCalledWith({
      to: "/agents/local/$sessionId",
      params: { sessionId: "local-1" },
    })
  })

  it("closes on Escape", () => {
    const onOpenChange = vi.fn()
    render(
      <AppCommandPalette commands={commands} open onOpenChange={onOpenChange} />
    )

    fireEvent.keyDown(screen.getByRole("combobox"), { key: "Escape" })
    expect(onOpenChange).toHaveBeenCalledWith(false, expect.anything())
  })
})
