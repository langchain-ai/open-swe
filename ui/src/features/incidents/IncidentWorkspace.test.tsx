/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"
import { IncidentDetail } from "./IncidentDetail"

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children, to }: { children: React.ReactNode; to: string }) => (
    <a href={to}>{children}</a>
  ),
}))

const record = {
  incident: {
    id: "local-1",
    title: "Checkout latency",
    channel_name: "inc-checkout",
    channel_id: "C1",
    is_archived: false,
    status: "paused",
    reason: null,
    latest_finding: null,
    updated_at: 1788714000,
    slack_url: null,
  },
  report: null,
  coverage: { gaps: [] },
  activity: [],
  allowed_actions: ["resume"],
  trace_url: null,
}
const markdown =
  "## Impact\n\n- **Checkout** requests failed\n\n[Monitor](https://example.com/monitor)"

function setup(overrides?: (path: string) => Response | undefined) {
  vi.stubGlobal("fetch", async (input: string) => {
    const path = new URL(input, "http://localhost").pathname
    const response = overrides?.(path)
    if (response) return response
    if (path === "/dashboard/api/incidents/records/local-1")
      return Response.json(record)
    if (path === "/dashboard/api/incidents/documents/local-1")
      return Response.json({ incident_id: "local-1", postmortem: { markdown } })
    return Response.json({ detail: "Unexpected request" }, { status: 404 })
  })
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <IncidentDetail incidentId="local-1" />
    </QueryClientProvider>
  )
  return client
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

it("renders a read-only postmortem and copies its Markdown with source links", async () => {
  const copy = vi.fn().mockResolvedValue(undefined)
  vi.stubGlobal("navigator", { clipboard: { writeText: copy } })
  setup()
  fireEvent.click(await screen.findByRole("tab", { name: "Postmortem" }))
  await screen.findByRole("heading", { name: "Impact", level: 2 })
  expect(screen.getByText("Checkout", { selector: "li strong" })).toBeTruthy()
  expect(screen.getByRole("link", { name: "Monitor" })).toHaveProperty(
    "href",
    "https://example.com/monitor"
  )
  fireEvent.click(screen.getByRole("button", { name: "Copy incident" }))
  await waitFor(() => expect(copy).toHaveBeenCalledWith(markdown))
  await screen.findByText("Incident copied as Markdown.")
  expect(screen.queryByRole("button", { name: "Edit postmortem" })).toBeNull()
  expect(screen.queryByRole("button", { name: "Revision history" })).toBeNull()
})

it("keeps the summary selectable after a clipboard failure", async () => {
  vi.stubGlobal("navigator", {
    clipboard: {
      writeText: vi.fn().mockRejectedValue(new Error("Clipboard denied")),
    },
  })
  setup()
  fireEvent.click(await screen.findByRole("tab", { name: "Postmortem" }))
  fireEvent.click(await screen.findByRole("button", { name: "Copy incident" }))
  await screen.findByText(
    "Unable to copy. Select the incident text to copy it manually."
  )
  expect(screen.getByRole("heading", { name: "Impact" })).toBeTruthy()
})

it.each([403, 404])(
  "hides a previously loaded summary when access returns %s",
  async (status) => {
    let revoked = false
    const client = setup((path) =>
      revoked && path.endsWith("/documents/local-1")
        ? Response.json({ detail: "Access revoked" }, { status })
        : undefined
    )
    fireEvent.click(await screen.findByRole("tab", { name: "Postmortem" }))
    await screen.findByRole("heading", { name: "Impact" })
    revoked = true
    await client.invalidateQueries({
      queryKey: ["incidents", "documents", "local-1", "current"],
    })
    await screen.findByText("Access revoked")
    expect(screen.queryByRole("heading", { name: "Impact" })).toBeNull()
    expect(screen.queryByRole("button", { name: "Copy incident" })).toBeNull()
  }
)
