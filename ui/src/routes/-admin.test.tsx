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

import { api, type TeamSettings } from "@/lib/api"

import { FableSection, SlackIntegrationSection } from "./admin"

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

describe("FableSection", () => {
  const settings = (fable_enabled: boolean): TeamSettings => ({
    review_draft_prs: false,
    pr_summaries: false,
    review_trace_links: false,
    fable_enabled,
  })

  afterEach(() => vi.restoreAllMocks())

  it("caches a save under the workspace it started in, not the one selected when it finishes", async () => {
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    vi.spyOn(api, "getTeamSettings").mockImplementation(async () =>
      settings(false)
    )
    let finishSave: (saved: TeamSettings) => void = () => {}
    const saveTeamSettings = vi
      .spyOn(api, "saveTeamSettings")
      .mockImplementation(
        () => new Promise<TeamSettings>((resolve) => (finishSave = resolve))
      )
    const section = (workspace: string) => (
      <QueryClientProvider client={qc}>
        <FableSection workspace={workspace} />
      </QueryClientProvider>
    )

    const view = render(section("alpha"))
    // The section renders a single switch, disabled until the settings load.
    const toggle = await within(view.container).findByRole("switch")
    await waitFor(() => {
      expect(toggle.hasAttribute("disabled")).toBe(false)
      expect(toggle.hasAttribute("data-disabled")).toBe(false)
      expect(toggle.getAttribute("aria-disabled")).not.toBe("true")
    })
    fireEvent.click(toggle)
    await waitFor(() =>
      expect(saveTeamSettings).toHaveBeenCalledWith(settings(true), "alpha")
    )

    view.rerender(section("beta"))
    finishSave(settings(true))

    await waitFor(() =>
      expect(qc.getQueryData(["teamSettings", "alpha"])).toEqual(settings(true))
    )
    await waitFor(() =>
      expect(qc.getQueryData(["teamSettings", "beta"])).toEqual(settings(false))
    )
  })
})
