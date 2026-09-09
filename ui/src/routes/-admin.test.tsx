/** @vitest-environment jsdom */

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"

import { api } from "@/lib/api"

import { PRTraceResolutionSection, SlackIntegrationSection } from "./admin"

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

describe("PRTraceResolutionSection", () => {
  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it("connects reviewer credentials and saves the trace project independently", async () => {
    const settings = {
      review_draft_prs: false,
      pr_summaries: true,
      review_trace_links: true,
      review_tracing_project: "existing-project",
    }
    vi.spyOn(api, "getTeamSettings").mockResolvedValue(settings)
    vi.spyOn(api, "getTeamCredentials").mockResolvedValue({
      langsmith: { connected: false },
    })
    const connect = vi.spyOn(api, "connectLangSmith").mockResolvedValue({
      langsmith: { connected: true, api_key_last4: "1234" },
    })
    const disconnect = vi.spyOn(api, "disconnectLangSmith").mockResolvedValue({
      langsmith: { connected: false },
    })
    const save = vi
      .spyOn(api, "saveTeamSettings")
      .mockImplementation(async (body) => body)
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    render(
      <QueryClientProvider client={queryClient}>
        <PRTraceResolutionSection />
      </QueryClientProvider>
    )

    await screen.findByDisplayValue("existing-project")
    fireEvent.change(screen.getByPlaceholderText("API key"), {
      target: { value: "test-key-1234" },
    })
    fireEvent.change(screen.getByPlaceholderText("Endpoint (optional)"), {
      target: { value: "https://api.smith.langchain.com" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Connect" }))
    await screen.findByRole("button", { name: "Disconnect" })
    expect(connect).toHaveBeenCalledWith({
      api_key: "test-key-1234",
      endpoint: "https://api.smith.langchain.com",
    })
    expect(save).not.toHaveBeenCalled()

    fireEvent.change(screen.getByPlaceholderText("Project name or ID"), {
      target: { value: "review-project" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Save" }))
    await waitFor(() =>
      expect(save).toHaveBeenCalledWith({
        ...settings,
        review_tracing_project: "review-project",
      })
    )

    fireEvent.click(screen.getByRole("button", { name: "Disconnect" }))
    await screen.findByRole("button", { name: "Connect" })
    expect(disconnect).toHaveBeenCalledOnce()
    expect(
      (screen.getByPlaceholderText("API key") as HTMLInputElement).value
    ).toBe("")
    expect(
      (screen.getByPlaceholderText("Project name or ID") as HTMLInputElement)
        .value
    ).toBe("review-project")
  })
})
