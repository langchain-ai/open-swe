/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { AppCommandPalette } from "./AppCommandPalette"
import type { AgentThread } from "@/features/agents/lib/types"
import type { AppCommand } from "@/lib/appCommands"

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  fetchNextPage: vi.fn(),
  search: vi.fn(),
  cloudThread: {
    id: "cloud-1",
    title: "Cloud result",
    experience: "agent_workspace",
    resolved: true,
  } as AgentThread,
}))

vi.mock("@/lib/session", () => ({
  useSession: () => ({ data: { login: "alice" } }),
}))

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => mocks.navigate,
  useRouterState: () => ({
    home: "/agents",
    thread: "/agents/$threadId",
  }),
}))
vi.mock("@/features/agents/lib/queries", () => ({
  useInfiniteThreadsPages: (...args: unknown[]) => {
    mocks.search(...args)
    return {
      data: { pages: [{ items: [mocks.cloudThread] }] },
      isFetching: false,
      isError: false,
      hasNextPage: true,
      isFetchingNextPage: false,
      fetchNextPage: mocks.fetchNextPage,
    }
  },
}))
vi.mock("@/features/agents/lib/chatProjects", () => ({
  useChatProjects: () => ({
    data: {
      projects: [{ id: "project-1", name: "Launch" }],
      memberships: { "cloud-1": "project-1" },
    },
  }),
}))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  localStorage.clear()
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

    expect(
      screen.getByRole("option", { name: /Cloud result.*Launch.*Archived/ })
    ).toBeTruthy()
    expect(mocks.search).toHaveBeenCalledWith(
      { limit: 20, q: undefined, scope: "all" },
      { enabled: true, staleWhileRevalidate: true }
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

    fireEvent.click(screen.getByRole("button", { name: "Load more chats" }))

    expect(mocks.fetchNextPage).toHaveBeenCalledOnce()
  })

  it("keeps server-resolved Slack threads and opens a PR review from a link", async () => {
    render(
      <AppCommandPalette commands={commands} open onOpenChange={vi.fn()} />
    )
    const input = screen.getByRole("combobox")
    fireEvent.change(input, {
      target: {
        value: "https://example.slack.com/archives/C123/p1788431248678809",
      },
    })
    expect(
      await screen.findByRole("option", {
        name: /Cloud result.*Launch.*Archived/,
      })
    ).toBeTruthy()

    fireEvent.change(input, {
      target: {
        value:
          "https://github.com/langchain-ai/open-swe/pull/42/files#diff-123",
      },
    })
    fireEvent.click(
      await screen.findByRole("option", { name: /Open Review Page/ })
    )
    expect(mocks.navigate).toHaveBeenCalledWith({
      to: "/agents/reviews/$owner/$repo/$number",
      params: { owner: "langchain-ai", repo: "open-swe", number: "42" },
    })
  })

  it("runs commands without showing chats or a review result for general title searches", async () => {
    render(
      <AppCommandPalette commands={commands} open onOpenChange={vi.fn()} />
    )
    const input = screen.getByRole("combobox")
    fireEvent.change(input, { target: { value: "fix search regression" } })
    expect(
      await screen.findByRole("option", { name: /Cloud result/ })
    ).toBeTruthy()
    expect(
      screen.queryByRole("option", { name: /Open Review Page/ })
    ).toBeNull()
    expect(mocks.search).toHaveBeenLastCalledWith(
      { limit: 20, q: "fix search regression", scope: "all" },
      { enabled: true, staleWhileRevalidate: true }
    )

    fireEvent.change(input, { target: { value: ">settings" } })
    expect(screen.queryByRole("option", { name: /Cloud result/ })).toBeNull()
    fireEvent.click(screen.getByRole("option", { name: /Open settings/ }))
    expect(commands[0]?.run).toHaveBeenCalledOnce()
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
