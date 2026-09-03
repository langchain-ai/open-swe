/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { EnvironmentsSection } from "./EnvironmentsSection"
import { api } from "@/lib/api"

const clients: Array<QueryClient> = []

afterEach(() => {
  for (const client of clients) client.clear()
  clients.length = 0
  vi.restoreAllMocks()
})

function renderSection(isAdmin: boolean) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  clients.push(client)
  return render(
    <QueryClientProvider client={client}>
      <EnvironmentsSection isAdmin={isAdmin} />
    </QueryClientProvider>
  )
}

describe("EnvironmentsSection", () => {
  it("shows refresh outcomes without edit controls", async () => {
    vi.spyOn(api, "listEnvironmentOptions").mockResolvedValue({
      default_slug: "default",
      environments: [
        {
          slug: "default",
          name: "Default",
          has_snapshot: true,
          refresh_status: "success",
          refresh_finished_at: new Date(Date.now() - 3_600_000).toISOString(),
          refresh_log_excerpt: "cloning acme/repo\ndone",
        },
        {
          slug: "preview",
          name: "Preview",
          has_snapshot: false,
          refresh_status: "failed",
          refresh_finished_at: new Date(Date.now() - 60_000).toISOString(),
          refresh_error: "setup script exited 1",
        },
      ],
    })

    const view = renderSection(true)

    expect(await screen.findByText("Preview")).toBeTruthy()
    expect(
      screen.getByText("Default environment · Snapshot ready")
    ).toBeTruthy()
    expect(screen.getByText(/Refreshed 1 hour ago/)).toBeTruthy()
    expect(screen.getByText(/Refresh failed/)).toBeTruthy()
    expect(screen.getByText("setup script exited 1")).toBeTruthy()
    expect(screen.getByText("Refresh log")).toBeTruthy()
    expect(view.container.querySelector("button, input, textarea")).toBeNull()
  })

  it("says so when an environment has never been refreshed", async () => {
    vi.spyOn(api, "listEnvironmentOptions").mockResolvedValue({
      default_slug: "default",
      environments: [{ slug: "default", name: "Default", has_snapshot: false }],
    })

    renderSection(true)

    expect(await screen.findByText("Never refreshed")).toBeTruthy()
    expect(
      screen.getByText("Default environment · No snapshot")
    ).toBeTruthy()
  })

  it("directs non-admins to a workspace admin", async () => {
    vi.spyOn(api, "listEnvironmentOptions").mockResolvedValue({
      default_slug: "default",
      environments: [],
    })

    renderSection(false)

    expect(
      await screen.findByText("No environments are configured.")
    ).toBeTruthy()
    expect(screen.getByText(/ask a workspace admin/)).toBeTruthy()
  })
})
