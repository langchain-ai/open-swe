// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"
import type { ReactNode } from "react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { ProviderTokensSection } from "./ProviderTokensSection"

const mockApi = vi.hoisted(() => ({
  listMyProviderTokens: vi.fn(),
  saveMyProviderToken: vi.fn(),
  revokeMyProviderToken: vi.fn(),
}))

vi.mock("@/lib/api", () => ({
  api: mockApi,
}))

function renderWithQueryClient(children: ReactNode) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })
  return render(
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  )
}

describe("ProviderTokensSection", () => {
  afterEach(() => {
    cleanup()
  })

  beforeEach(() => {
    vi.clearAllMocks()
    mockApi.listMyProviderTokens.mockResolvedValue({
      items: [
        {
          connected: true,
          provider: "github",
          token_last4: "1234",
          updated_at: "2026-06-28T10:00:00Z",
        },
      ],
    })
    mockApi.saveMyProviderToken.mockImplementation((provider, body) =>
      Promise.resolve({
        connected: true,
        provider,
        token_last4: body.token.slice(-4),
        updated_at: "2026-06-28T11:00:00Z",
      })
    )
    mockApi.revokeMyProviderToken.mockImplementation((provider) =>
      Promise.resolve({ connected: false, provider })
    )
  })

  it("shows redacted connected metadata and missing-token capability blockers", async () => {
    renderWithQueryClient(<ProviderTokensSection />)

    expect(await screen.findByText("Provider Tokens")).not.toBeNull()
    expect(await screen.findByText(/token ••••1234/)).not.toBeNull()
    expect(
      await screen.findByText(
        /Missing token blocks personal Linear queue intake/
      )
    ).not.toBeNull()
    expect(document.body.textContent).not.toContain("ghp_secret-token-1234")
  })

  it("creates and updates provider tokens without displaying the entered value", async () => {
    renderWithQueryClient(<ProviderTokensSection />)

    expect(await screen.findByText(/token ••••1234/)).not.toBeNull()
    const linearToken = await screen.findByLabelText(
      "Linear personal access token"
    )
    fireEvent.change(linearToken, { target: { value: "lin_secret_9999" } })
    fireEvent.click(screen.getByRole("button", { name: "Save Linear token" }))

    await waitFor(() =>
      expect(mockApi.saveMyProviderToken).toHaveBeenCalledWith("linear", {
        token: "lin_secret_9999",
      })
    )
    await waitFor(() => expect(linearToken).toHaveProperty("value", ""))
    expect(document.body.textContent).not.toContain("lin_secret_9999")

    const githubToken = screen.getByLabelText("GitHub personal access token")
    fireEvent.change(githubToken, { target: { value: "ghp_updated_5555" } })
    fireEvent.click(screen.getByRole("button", { name: "Update GitHub token" }))

    await waitFor(() =>
      expect(mockApi.saveMyProviderToken).toHaveBeenCalledWith("github", {
        token: "ghp_updated_5555",
      })
    )
    await waitFor(() => expect(githubToken).toHaveProperty("value", ""))
    expect(document.body.textContent).not.toContain("ghp_updated_5555")
  })

  it("revokes a connected provider token", async () => {
    renderWithQueryClient(<ProviderTokensSection />)

    fireEvent.click(
      await screen.findByRole("button", { name: "Revoke GitHub token" })
    )

    await waitFor(() =>
      expect(mockApi.revokeMyProviderToken).toHaveBeenCalledWith("github")
    )
  })

  it("shows API errors for failed token saves", async () => {
    mockApi.saveMyProviderToken.mockRejectedValueOnce(
      new Error("token rejected")
    )

    renderWithQueryClient(<ProviderTokensSection />)

    expect(await screen.findByText(/token ••••1234/)).not.toBeNull()
    const linearToken = await screen.findByLabelText(
      "Linear personal access token"
    )
    fireEvent.change(linearToken, { target: { value: "bad-token" } })
    fireEvent.click(screen.getByRole("button", { name: "Save Linear token" }))

    expect(await screen.findByText("token rejected")).not.toBeNull()
  })
})
