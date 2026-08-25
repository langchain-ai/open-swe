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
  it("shows environment status without edit controls", async () => {
    vi.spyOn(api, "listEnvironmentOptions").mockResolvedValue({
      default_slug: "default",
      environments: [
        { slug: "default", name: "Default", has_snapshot: true },
        { slug: "preview", name: "Preview", has_snapshot: false },
      ],
    })

    const view = renderSection(true)

    expect(await screen.findByText("Preview")).toBeTruthy()
    expect(screen.getByText("Default environment")).toBeTruthy()
    expect(screen.getByText("Snapshot ready")).toBeTruthy()
    expect(screen.getByText("No snapshot")).toBeTruthy()
    expect(screen.getByText(/enable admin mode/)).toBeTruthy()
    expect(view.container.querySelector("button, input, textarea")).toBeNull()
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
