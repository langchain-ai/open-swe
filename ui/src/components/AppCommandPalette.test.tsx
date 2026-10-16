/** @vitest-environment jsdom */

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { AppCommandPalette } from "./AppCommandPalette"
import type { AgentThread } from "@/features/agents/lib/types"
import type { AppCommand } from "@/lib/appCommands"

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  fetchNextPage: vi.fn(),
  cloudThread: { id: "cloud-1", title: "Cloud result" } as AgentThread,
  relatedThread: { id: "related-1", title: "Slack DM fix" } as AgentThread,
}))

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => mocks.navigate,
  useRouterState: () => ({
    home: "/agents",
    thread: "/agents/$threadId",
  }),
}))
vi.mock("@/features/agents/lib/queries", () => ({
  useInfiniteThreadsPages: () => ({
    data: { pages: [{ items: [mocks.cloudThread] }] },
    isFetching: false,
    isError: false,
    hasNextPage: true,
    isFetchingNextPage: false,
    fetchNextPage: mocks.fetchNextPage,
  }),
  useThreadSearch: (
    _params: { q: string },
    { enabled }: { enabled?: boolean }
  ) => ({
    data: enabled
      ? { items: [mocks.cloudThread, mocks.relatedThread] }
      : undefined,
    isFetching: false,
  }),
}))
vi.mock("@/features/reviews/lib/usePullRequestSearch", () => ({
  usePullRequestSearch: () => ({ data: undefined }),
}))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  vi.useRealTimers()
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

  it("loads additional cloud thread pages", () => {
    render(
      <AppCommandPalette commands={commands} open onOpenChange={vi.fn()} />
    )

    fireEvent.click(screen.getByRole("button", { name: "Load more threads" }))

    expect(mocks.fetchNextPage).toHaveBeenCalledOnce()
  })

  it("adds AI-related threads the keyword results do not already show", () => {
    vi.useFakeTimers()
    render(
      <AppCommandPalette commands={commands} open onOpenChange={vi.fn()} />
    )

    fireEvent.change(screen.getByRole("combobox"), {
      target: { value: "cloud" },
    })
    expect(screen.queryByText("Related threads")).toBeNull()
    act(() => vi.advanceTimersByTime(500))

    expect(screen.getByText("Related threads")).toBeTruthy()
    expect(screen.getByText("Slack DM fix")).toBeTruthy()
    expect(screen.getAllByText("Cloud result")).toHaveLength(1)
  })

  it("closes on Escape", () => {
    const onOpenChange = vi.fn()
    render(
      <AppCommandPalette commands={commands} open onOpenChange={onOpenChange} />
    )

    fireEvent.keyDown(screen.getByRole("combobox"), { key: "Escape" })
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })
})
