/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { LLMGatewaySection, SlackIntegrationSection } from "./admin"
import { api } from "@/lib/api"

const STORAGE_KEY = "open-swe.admin.slack-code-channels-enabled"
const storage = new Map<string, string>()
const localStorage = {
  clear: () => storage.clear(),
  getItem: (key: string) => storage.get(key) ?? null,
  setItem: (key: string, value: string) => storage.set(key, value),
}

describe("LLMGatewaySection", () => {
  it("shows the resolved custom endpoint without exposing credentials", async () => {
    vi.spyOn(api, "getTeamSettings").mockResolvedValue({
      review_draft_prs: false,
      pr_summaries: true,
      review_trace_links: true,
      gateway_enabled: null,
    })
    vi.spyOn(api, "getGatewayConfiguration").mockResolvedValue({
      model_id: "openai:gpt-5.6-sol",
      override_enabled: false,
      resolution_reason: "LANGSMITH_GATEWAY_ENABLED",
      endpoint_kind: "custom_endpoint",
      endpoint: "https://proxy.example/v1",
      credential_sources: ["OPENAI_API_KEY"],
      restart_required: false,
    })
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })

    render(
      <QueryClientProvider client={client}>
        <LLMGatewaySection />
      </QueryClientProvider>
    )

    expect(
      await screen.findByText(/Inherit currently resolves to disabled/)
    ).toBeTruthy()
    expect(
      screen.getByText(/Custom endpoint — https:\/\/proxy.example\/v1/)
    ).toBeTruthy()
    expect(screen.getByText(/Credential source: OPENAI_API_KEY/)).toBeTruthy()
    expect(screen.getByText("Environment precedence and examples")).toBeTruthy()
  })
})

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
