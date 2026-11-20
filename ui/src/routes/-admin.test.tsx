/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { api } from "@/lib/api"

import {
  SlackIntegrationSection,
  UsersSection,
} from "@/features/settings/components/AdminSections"
import { AnnouncementsSection } from "@/features/settings/components/AnnouncementsSection"

afterEach(cleanup)

const storage = new Map<string, string>()
const localStorage = {
  clear: () => storage.clear(),
  getItem: (key: string) => storage.get(key) ?? null,
  setItem: (key: string, value: string) => storage.set(key, value),
}

describe("SlackIntegrationSection", () => {
  const writeText = vi
    .fn<(value: string) => Promise<void>>()
    .mockResolvedValue(undefined)

  beforeEach(() => {
    localStorage.clear()
    writeText.mockClear()
    Object.defineProperty(window, "localStorage", {
      configurable: true,
      value: localStorage,
    })
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    })
  })

  it("copies the ordinary Slack manifest", async () => {
    render(<SlackIntegrationSection backendUrl="https://openswe.example.com" />)

    fireEvent.click(screen.getByRole("button", { name: "Copy manifest" }))
    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1))
    const legacy = JSON.parse(writeText.mock.calls[0]![0])
    expect(legacy.features).not.toHaveProperty("code_channels")
    expect(legacy.settings.event_subscriptions.request_url).toBe(
      "https://openswe.example.com/webhooks/slack"
    )
    expect(legacy.oauth_config.redirect_urls).toContain(
      "https://openswe.example.com/dashboard/api/slack/callback"
    )
  })
})

describe("UsersSection", () => {
  afterEach(() => vi.restoreAllMocks())

  it("resets pagination when searching or changing page size", async () => {
    const people = Array.from({ length: 30 }, (_, index) => ({
      user_id: String(index),
      github_login: `user-${index}`,
      display_name: "",
      avatar_url: "",
      email: `user-${index}@example.com`,
      slack_user_id: null,
      is_admin: false,
    }))
    vi.spyOn(api, "adminListUsers").mockImplementation(
      async (page = 1, pageSize = 20, search = "") => {
        const matches = people.filter((user) =>
          user.github_login.includes(search)
        )
        return {
          items: matches.slice((page - 1) * pageSize, page * pageSize),
          total: matches.length,
          page,
          page_size: pageSize,
        }
      }
    )
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 } },
    })
    render(
      <QueryClientProvider client={client}>
        <UsersSection enabled />
      </QueryClientProvider>
    )

    fireEvent.click(await screen.findByRole("button", { name: "Next" }))
    expect(await screen.findByText("Page 2 of 3")).toBeTruthy()
    fireEvent.click(screen.getByRole("combobox", { name: "Rows per page" }))
    fireEvent.click(await screen.findByRole("option", { name: "25" }))
    expect(await screen.findByText("1–25 of 30")).toBeTruthy()
    expect(screen.getByText("Page 1 of 2")).toBeTruthy()

    fireEvent.click(screen.getByRole("button", { name: "Next" }))
    expect(await screen.findByText("Page 2 of 2")).toBeTruthy()
    fireEvent.change(screen.getByRole("textbox", { name: "Search users" }), {
      target: { value: "user-24" },
    })
    expect(await screen.findByText("user-24")).toBeTruthy()
    expect(screen.queryByText("user-25")).toBeNull()

    fireEvent.change(screen.getByRole("textbox", { name: "Search users" }), {
      target: { value: "nobody" },
    })
    expect(await screen.findByText("No users match your search")).toBeTruthy()
    client.clear()
  })
})

describe("AnnouncementsSection", () => {
  afterEach(() => vi.restoreAllMocks())

  it("shows the saved scope and message before explicitly approving", async () => {
    vi.spyOn(api, "listAnnouncements").mockResolvedValue({
      recipients: 15,
      items: [],
      approvals: [
        {
          id: "approval-1",
          thread_id: "thread-1",
          text: "Saved *message*",
          recipients: 12,
          payload_digest: "digest-1",
          created_at: "2026-01-01T00:00:00Z",
          expires_at: "2026-01-02T00:00:00Z",
        },
      ],
    })
    const decide = vi
      .spyOn(api, "decideAnnouncement")
      .mockResolvedValue({ status: "approved" })
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 } },
    })
    render(
      <QueryClientProvider client={client}>
        <AnnouncementsSection />
      </QueryClientProvider>
    )
    expect(
      await screen.findByText(/12 people with Slack connected at preparation/)
    ).toBeTruthy()
    fireEvent.click(screen.getByRole("button", { name: "Review and send" }))
    const dialog = await screen.findByRole("dialog")
    expect(within(dialog).getByText("Saved *message*")).toBeTruthy()
    expect(within(dialog).getByText("DM 12 people?")).toBeTruthy()
    expect(decide).not.toHaveBeenCalled()
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Approve and send" })
    )
    await waitFor(() =>
      expect(decide).toHaveBeenCalledWith("approval-1", "approve", "digest-1")
    )
    client.clear()
  })

  it("sends to everyone only after the admin confirms the exact text", async () => {
    vi.spyOn(api, "listAnnouncements").mockResolvedValue({
      recipients: 12,
      items: [],
      approvals: [],
    })
    const send = vi
      .spyOn(api, "sendAnnouncement")
      .mockResolvedValue({ test: false, recipients: 12 })
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 } },
    })
    render(
      <QueryClientProvider client={client}>
        <AnnouncementsSection />
      </QueryClientProvider>
    )

    expect(
      await screen.findByText("12 people with Slack connected")
    ).toBeTruthy()
    fireEvent.change(screen.getByRole("textbox", { name: "Announcement" }), {
      target: { value: "  We shipped walkthroughs  " },
    })
    fireEvent.click(screen.getByRole("button", { name: "Send to everyone" }))
    const dialog = await screen.findByRole("dialog")
    expect(within(dialog).getByText("We shipped walkthroughs")).toBeTruthy()
    expect(send).not.toHaveBeenCalled()

    fireEvent.click(
      within(dialog).getByRole("button", { name: "Send announcement" })
    )
    await waitFor(() =>
      expect(send).toHaveBeenCalledWith("We shipped walkthroughs", false)
    )
    expect(
      await screen.findByText(
        "Queued for 12 people. If your Slack account is connected, your DM is sent first."
      )
    ).toBeTruthy()
    client.clear()
  })

  it("opens delivery history in a modal and cancels remaining deliveries", async () => {
    const item = {
      id: "announcement-1",
      text: "*Release notes*\nRead <https://example.com|the update>",
      author: "An admin",
      created_at: "2026-11-17T03:39:33Z",
      finished_at: null,
      cancelled_at: null,
      recipients: 230,
      sent: 23,
      failed: 0,
    }
    const list = vi
      .spyOn(api, "listAnnouncements")
      .mockResolvedValue({ recipients: 230, items: [item], approvals: [] })
    const cancel = vi
      .spyOn(api, "cancelAnnouncement")
      .mockImplementation(async () => {
        list.mockResolvedValue({
          recipients: 230,
          items: [{ ...item, cancelled_at: "2026-11-17T03:40:00Z" }],
          approvals: [],
        })
      })
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 } },
    })
    render(
      <QueryClientProvider client={client}>
        <AnnouncementsSection />
      </QueryClientProvider>
    )
    expect(
      await screen.findByText("230 people with Slack connected")
    ).toBeTruthy()
    expect(screen.queryByText("Release notes")).toBeNull()
    const historyButton = screen.getByRole("button", {
      name: "View delivery history",
    })
    fireEvent.click(historyButton)
    const dialog = await screen.findByRole("dialog", {
      name: "Delivery history",
    })
    expect(within(dialog).getByText("23 of 230 delivered")).toBeTruthy()
    expect(screen.getByText("Release notes").tagName).toBe("STRONG")
    expect(
      screen.getByRole("link", { name: "the update" }).getAttribute("href")
    ).toBe("https://example.com")
    fireEvent.click(screen.getByRole("button", { name: "Cancel remaining" }))
    await waitFor(() => expect(cancel).toHaveBeenCalledWith("announcement-1"))
    expect(await screen.findByText("Cancelled")).toBeTruthy()
    expect(
      screen.queryByRole("button", { name: "Cancel remaining" })
    ).toBeNull()
    fireEvent.click(within(dialog).getByRole("button", { name: "Close" }))
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull()
      expect(document.activeElement).toBe(historyButton)
    })
    expect(screen.queryByText("Release notes")).toBeNull()
    client.clear()
  })
})
