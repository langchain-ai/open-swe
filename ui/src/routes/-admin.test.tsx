/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { ModelProvidersSection, SlackIntegrationSection } from "./admin"
import { api, type TeamSettings } from "@/lib/api"

const STORAGE_KEY = "open-swe.admin.slack-code-channels-enabled"
const storage = new Map<string, string>()
const localStorage = {
  clear: () => storage.clear(),
  getItem: (key: string) => storage.get(key) ?? null,
  setItem: (key: string, value: string) => storage.set(key, value),
}

const TEAM_SETTINGS: TeamSettings = {
  review_draft_prs: false,
  pr_summaries: true,
  review_trace_links: true,
  disabled_model_providers: [],
}

function renderModelProviders() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  client.setQueryData(["teamSettings"], TEAM_SETTINGS)
  render(
    <QueryClientProvider client={client}>
      <ModelProvidersSection
        providers={[
          { id: "anthropic", label: "Anthropic", enabled: true },
          { id: "openai", label: "OpenAI", enabled: true },
        ]}
      />
    </QueryClientProvider>
  )
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
    render(<SlackIntegrationSection />)

    const toggle = screen.getByRole("switch", {
      name: /^Slack Code Channels/,
    })
    expect(toggle.getAttribute("aria-checked")).toBe("false")

    fireEvent.click(screen.getByRole("button", { name: "Copy manifest" }))
    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1))
    expect(JSON.parse(writeText.mock.calls[0]![0]).features).not.toHaveProperty(
      "code_channels"
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

describe("ModelProvidersSection", () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it("disables a provider through team settings", async () => {
    const save = vi
      .spyOn(api, "saveTeamSettings")
      .mockImplementation(async (settings) => settings)
    vi.spyOn(api, "getTeamSettings").mockResolvedValue(TEAM_SETTINGS)
    renderModelProviders()

    fireEvent.click(screen.getByRole("switch", { name: "Anthropic models" }))

    await waitFor(() => expect(save).toHaveBeenCalledTimes(1))
    expect(save.mock.calls[0]![0].disabled_model_providers).toEqual([
      "anthropic",
    ])
  })
})
