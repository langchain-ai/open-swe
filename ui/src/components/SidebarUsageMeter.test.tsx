/** @vitest-environment jsdom */

import {
  QueryClient,
  QueryClientProvider,
  focusManager,
} from "@tanstack/react-query"
import { act, cleanup, render, screen, waitFor } from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"

import { SidebarUsageMeter } from "./SidebarUsageMeter"

vi.mock("@tanstack/react-router", () => ({
  Link: ({
    children,
    "aria-label": label,
  }: {
    children: React.ReactNode
    "aria-label"?: string
  }) => (
    <a href="/usage" aria-label={label}>
      {children}
    </a>
  ),
}))

afterEach(() => {
  cleanup()
  focusManager.setFocused(undefined)
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

function renderMeter(body: object) {
  const fetch = vi.fn().mockResolvedValue(Response.json(body))
  vi.stubGlobal("fetch", fetch)
  render(
    <QueryClientProvider client={new QueryClient()}>
      <SidebarUsageMeter userId="user" />
    </QueryClientProvider>
  )
  return fetch
}

const limit = {
  policy_id: "budget",
  name: "Personal budget",
  providers: [],
  resets_at: "2099-12-01T00:00:00Z",
}

it("shows the cap closest to running out, skipping windows without spend", async () => {
  renderMeter({
    status: "ready",
    limits: [
      { ...limit, window: "monthly", limit_usd: "2000", spend_usd: "701.08" },
      { ...limit, window: "daily", limit_usd: "100", spend_usd: "20" },
      { ...limit, window: "weekly", limit_usd: "10", spend_usd: null },
    ],
  })
  expect(
    await screen.findByRole("link", { name: /^\$701 of \$2,000 monthly/ })
  ).toBeTruthy()
})

it("never rounds spend up to a cap it has not reached", async () => {
  renderMeter({
    status: "ready",
    limits: [{ ...limit, window: "daily", limit_usd: "10", spend_usd: "9.75" }],
  })
  expect(
    await screen.findByRole("link", { name: /^\$9 of \$10 daily/ })
  ).toBeTruthy()
})

it("renders nothing and does not refetch on focus when spend is unavailable", async () => {
  const fetch = renderMeter({ status: "sign_in_required", limits: [] })
  await waitFor(() => expect(fetch).toHaveBeenCalled())
  expect(screen.queryByRole("link")).toBeNull()
  vi.useFakeTimers({ toFake: ["Date"] })
  vi.setSystemTime(Date.now() + 5 * 60_000)
  act(() => {
    focusManager.setFocused(false)
    focusManager.setFocused(true)
  })
  await new Promise((resolve) => setTimeout(resolve, 50))
  expect(fetch).toHaveBeenCalledTimes(1)
})
