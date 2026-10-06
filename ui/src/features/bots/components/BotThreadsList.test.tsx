/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { useQuery } from "@tanstack/react-query"
import { afterEach, describe, expect, it, vi } from "vitest"

import { BotThreadsList } from "./BotThreadsList"
import type { AgentThread } from "@/features/agents/lib/types"
import { useThreadsPage } from "@/features/agents/lib/queries"
import { useSession } from "@/lib/session"

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children }: { children: React.ReactNode }) => <a>{children}</a>,
}))
vi.mock("@tanstack/react-query", () => ({ useQuery: vi.fn() }))
vi.mock("@/features/agents/lib/queries", () => ({
  useThreadsPage: vi.fn(),
}))
vi.mock("@/lib/session", () => ({ useSession: vi.fn() }))
vi.mock("./AllowedSlackBotsSection", () => ({
  AllowedSlackBotsSection: () => <section>Manage allowed bots</section>,
}))

const BOT_KEY = "T123:B123"

function mockPage(items: Array<AgentThread>, { isAdmin = false } = {}) {
  vi.mocked(useSession).mockReturnValue({
    data: { is_admin: isAdmin },
  } as unknown as ReturnType<typeof useSession>)
  vi.mocked(useQuery).mockReturnValue({
    data: [{ key: BOT_KEY, name: "Release bot", image_url: "" }],
  } as unknown as ReturnType<typeof useQuery>)
  vi.mocked(useThreadsPage).mockReturnValue({
    data: { items, hasMore: false },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useThreadsPage>)
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("BotThreadsList", () => {
  it("lists bot threads with their bot and Slack link", () => {
    mockPage([
      {
        id: "bot-thread",
        title: "Flaky test alert",
        repo: "open-swe",
        repoFullName: "langchain-ai/open-swe",
        branch: "main",
        model: "Default",
        source: "slack",
        triggerKind: "slack_bot",
        triggeringBot: { key: BOT_KEY, name: "Release bot" },
        sourceUrl: "https://example.slack.com/archives/C1/p1",
        status: "running",
        viewed: false,
        createdAt: Date.now(),
        updatedAt: Date.now(),
        messages: [],
      },
    ])

    render(<BotThreadsList onBotChange={vi.fn()} />)

    expect(vi.mocked(useThreadsPage).mock.calls[0]?.[0]).toMatchObject({
      scope: "bot",
      bot: undefined,
    })
    expect(screen.getByText("Flaky test alert")).toBeTruthy()
    expect(screen.queryByText("Manage allowed bots")).toBeNull()
    expect(screen.getAllByText("Release bot")).toHaveLength(2)
    expect(
      screen
        .getByRole("link", { name: "Open the Slack thread" })
        .getAttribute("href")
    ).toBe("https://example.slack.com/archives/C1/p1")
  })

  it("filters by bot", () => {
    mockPage([])
    const onBotChange = vi.fn()

    render(<BotThreadsList bot={BOT_KEY} onBotChange={onBotChange} />)

    expect(vi.mocked(useThreadsPage).mock.calls[0]?.[0]).toMatchObject({
      scope: "bot",
      bot: BOT_KEY,
    })
    expect(screen.getByText(/No bot threads yet/)).toBeTruthy()
    fireEvent.click(screen.getByRole("button", { name: "All bots" }))
    expect(onBotChange).toHaveBeenCalledWith(undefined)
  })

  it("lets admins manage the allowlist on the same page", () => {
    mockPage([], { isAdmin: true })

    render(<BotThreadsList onBotChange={vi.fn()} />)

    expect(screen.getByText("Manage allowed bots")).toBeTruthy()
  })
})
