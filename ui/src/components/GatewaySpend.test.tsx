/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"

import { GatewaySpend } from "./GatewaySpend"

vi.mock("@tanstack/react-start/server", () => ({
  getRequest: () => new Request("http://localhost:3000/"),
  getRequestHeader: () => undefined,
}))

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

it("shows personal cap windows without treating missing spend as zero", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      Response.json({
        status: "ready",
        limits: [
          {
            policy_id: "budget",
            name: "Personal budget",
            window: "daily",
            providers: [],
            limit_usd: "10",
            spend_usd: "12.50",
            resets_at: "2026-11-15T00:00:00Z",
          },
          {
            policy_id: "budget",
            name: "Personal budget",
            window: "monthly",
            providers: ["openai"],
            limit_usd: "100",
            spend_usd: null,
            resets_at: "2026-12-01T00:00:00Z",
          },
        ],
      })
    )
  )
  render(
    <QueryClientProvider client={new QueryClient()}>
      <GatewaySpend userId="user" />
    </QueryClientProvider>
  )
  expect(await screen.findByText("$12.50")).toBeTruthy()
  expect(screen.getByText("Cap reached")).toBeTruthy()
  expect(screen.getByText("Spend unavailable")).toBeTruthy()
  expect(screen.getByText("monthly · openai (BYOK)")).toBeTruthy()
  expect(screen.getAllByText(/^Resets\b/)).toHaveLength(2)
  expect(
    screen.getByText(/^Resets\b/, {
      selector: 'span:has(time[datetime="2026-12-01T00:00:00Z"])',
    })
  ).toBeTruthy()
})

it("does not describe an empty cap list as unlimited", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(Response.json({ status: "ready", limits: [] }))
  )
  render(
    <QueryClientProvider client={new QueryClient()}>
      <GatewaySpend userId="user" />
    </QueryClientProvider>
  )
  expect(
    await screen.findByText(
      /This does not mean your gateway usage is unlimited/
    )
  ).toBeTruthy()
})

it("explains missing permission instead of displaying zero spend", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(Response.json({ status: "forbidden", limits: [] }))
  )
  render(
    <QueryClientProvider client={new QueryClient()}>
      <GatewaySpend userId="user" />
    </QueryClientProvider>
  )
  expect((await screen.findByRole("status")).textContent).toContain(
    "cannot read gateway policies"
  )
  expect(screen.queryByText("$0.00")).toBeNull()
})
