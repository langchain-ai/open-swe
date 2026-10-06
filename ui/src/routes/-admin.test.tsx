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

import {
  api,
  type WorkspaceSettings,
  type WorkspaceSettingsView,
} from "@/lib/api"

import {
  SlackIntegrationSection,
  UsersSection,
} from "@/features/settings/components/AdminSections"
import { ReviewSettings } from "@/features/settings/components/ReviewSettings"

afterEach(cleanup)

const STORAGE_KEY = "open-swe.admin.slack-code-channels-enabled"
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

  it("defaults to legacy Slack and copies Code Channels only when enabled", async () => {
    render(<SlackIntegrationSection backendUrl="https://openswe.example.com" />)

    const toggle = screen.getByRole("switch", {
      name: /^Slack Code Channels/,
    })
    expect(toggle.getAttribute("aria-checked")).toBe("false")

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

    fireEvent.click(toggle)
    expect(localStorage.getItem(STORAGE_KEY)).toBe("true")
    fireEvent.click(screen.getByRole("button", { name: "Copy manifest" }))
    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(2))
    expect(
      JSON.parse(writeText.mock.calls[1]![0]).features.code_channels.enabled
    ).toBe(true)
  })
})

describe("UsersSection", () => {
  afterEach(() => vi.restoreAllMocks())

  it("resets pagination when searching or changing page size", async () => {
    const people = Array.from({ length: 30 }, (_, index) => ({
      user_id: String(index),
      github_login: `user-${index}`,
      display_name: "",
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
    fireEvent.keyDown(await screen.findByRole("option", { name: "25" }), {
      key: "Enter",
    })
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
    expect(await screen.findByText("No users match your search.")).toBeTruthy()
    client.clear()
  })
})

describe("ReviewSettings", () => {
  const settings = (review_draft_prs: boolean): WorkspaceSettings => ({
    pr_summaries: false,
    review_trace_links: false,
    review_draft_prs,
  })
  const loaded = (review_draft_prs: boolean): WorkspaceSettingsView => ({
    effective: settings(review_draft_prs),
    overrides: {},
  })

  afterEach(() => vi.restoreAllMocks())

  it("caches a save under the workspace it started in, not the one shown when it finishes", async () => {
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    vi.spyOn(api, "getInstanceSettings").mockResolvedValue(settings(false))
    vi.spyOn(api, "getWorkspaceSettings").mockImplementation(async () =>
      loaded(false)
    )
    let finishSave: (saved: WorkspaceSettingsView) => void = () => {}
    const save = vi
      .spyOn(api, "saveWorkspaceSettings")
      .mockImplementation(
        () =>
          new Promise<WorkspaceSettingsView>(
            (resolve) => (finishSave = resolve)
          )
      )
    const section = (slug: string) => (
      <QueryClientProvider client={qc}>
        <ReviewSettings scope={{ kind: "workspace", slug }} canEdit />
      </QueryClientProvider>
    )

    const view = render(section("alpha"))
    const row = (
      await within(view.container).findByText("Review Draft PRs")
    ).closest("label")!.parentElement!
    const toggle = within(row).getByRole("switch")
    await waitFor(() => {
      expect(toggle.hasAttribute("disabled")).toBe(false)
      expect(toggle.hasAttribute("data-disabled")).toBe(false)
      expect(toggle.getAttribute("aria-disabled")).not.toBe("true")
    })
    fireEvent.click(toggle)
    await waitFor(() =>
      expect(save).toHaveBeenCalledWith("alpha", { review_draft_prs: true })
    )

    view.rerender(section("beta"))
    const saved: WorkspaceSettingsView = {
      effective: settings(true),
      overrides: { review_draft_prs: true },
    }
    finishSave(saved)

    await waitFor(() =>
      expect(qc.getQueryData(["workspaceSettings", "alpha"])).toEqual(saved)
    )
    await waitFor(() =>
      expect(qc.getQueryData(["workspaceSettings", "beta"])).toEqual(
        loaded(false)
      )
    )
  })
})
