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
  allowed_actions: ["resume", "edit_document"],
  trace_url: null,
}
const revision = {
  kind: "postmortem",
  revision: 3,
  markdown: "Cause remains unknown",
  author: "Alex",
  created_at: 1788714000,
  source: "responder",
  run_id: null,
  evidence: [],
}
const documents = {
  incident_id: "local-1",
  postmortem: revision,
  status_page_draft: null,
  operations: [],
}

function setup(
  overrides?: (path: string, init?: RequestInit) => Response | undefined
) {
  const writes: Array<{ path: string; body: Record<string, unknown> }> = []
  vi.stubGlobal("fetch", async (input: string, init?: RequestInit) => {
    const path = new URL(input, "http://localhost").pathname
    if (init?.body) writes.push({ path, body: JSON.parse(init.body as string) })
    const response = overrides?.(path, init)
    if (response) return response
    if (path === "/dashboard/api/incidents/records/local-1")
      return Response.json(record)
    if (path === "/dashboard/api/incidents/documents/local-1")
      return Response.json(documents)
    if (path.endsWith("/revisions")) return Response.json({ items: [revision] })
    return Response.json(
      { detail: `Unexpected request: ${path}` },
      { status: 404 }
    )
  })
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <IncidentDetail incidentId="local-1" />
    </QueryClientProvider>
  )
  return { client, writes }
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

it("preserves a postmortem draft while switching between overview and timeline", async () => {
  setup()
  const overview = await screen.findByRole("tab", { name: "Overview" })
  expect(overview.getAttribute("aria-selected")).toBe("true")
  expect(screen.queryByRole("button", { name: "Edit postmortem" })).toBeNull()
  fireEvent.click(screen.getByRole("tab", { name: "Postmortem" }))
  fireEvent.click(
    await screen.findByRole("button", { name: "Edit postmortem" })
  )
  fireEvent.change(screen.getByRole("textbox", { name: "Postmortem" }), {
    target: { value: "Unsaved mitigation notes" },
  })
  fireEvent.click(screen.getByRole("tab", { name: "Timeline" }))
  expect(screen.queryByRole("textbox", { name: "Postmortem" })).toBeNull()
  expect(screen.getByRole("tabpanel", { name: "Timeline" })).toBeTruthy()
  fireEvent.click(overview)
  expect(screen.getByRole("tabpanel", { name: "Overview" })).toBeTruthy()
  fireEvent.click(screen.getByRole("tab", { name: "Postmortem" }))
  expect(screen.getByRole("textbox", { name: "Postmortem" })).toHaveProperty(
    "value",
    "Unsaved mitigation notes"
  )
})

it("renders document Markdown by default and preserves unsaved edits across preview changes", async () => {
  const markdown =
    "## Impact\n\n- **Checkout** requests failed\n\n[Monitor](https://example.com/monitor)"
  const { writes } = setup((path) =>
    path === "/dashboard/api/incidents/documents/local-1"
      ? Response.json({ ...documents, postmortem: { ...revision, markdown } })
      : undefined
  )
  fireEvent.click(await screen.findByRole("tab", { name: "Postmortem" }))
  await screen.findByRole("heading", { name: "Impact", level: 2 })
  expect(screen.getByText("Checkout", { selector: "li strong" })).toBeTruthy()
  expect(screen.getByRole("link", { name: "Monitor" })).toHaveProperty(
    "href",
    "https://example.com/monitor"
  )
  expect(screen.queryByRole("textbox", { name: "Postmortem" })).toBeNull()
  fireEvent.click(screen.getByRole("button", { name: "Edit postmortem" }))
  const editor = screen.getByRole("textbox", { name: "Postmortem" })
  expect(editor).toHaveProperty("value", markdown)
  const edited = `${markdown}\n\n### Recovery\n\nRollback completed.`
  fireEvent.change(editor, { target: { value: edited } })
  fireEvent.click(screen.getByRole("button", { name: "Preview postmortem" }))
  expect(
    screen.getByRole("heading", { name: "Recovery", level: 3 })
  ).toBeTruthy()
  fireEvent.click(screen.getByRole("button", { name: "Edit postmortem" }))
  expect(screen.getByRole("textbox", { name: "Postmortem" })).toHaveProperty(
    "value",
    edited
  )
  expect(writes).toEqual([])
})

it("preserves a responder edit when the expected document revision conflicts", async () => {
  const { writes } = setup((path, init) => {
    if (path.endsWith("/postmortem") && init?.method === "PUT")
      return Response.json({
        id: "doc-1",
        kind: "postmortem",
        status: "pending",
        expected_revision: 3,
        revision: null,
        error: null,
      })
    if (path.endsWith("/operations/doc-1"))
      return Response.json({
        id: "doc-1",
        kind: "postmortem",
        status: "conflict",
        expected_revision: 3,
        revision: 4,
        error: "A newer revision was saved.",
      })
  })
  fireEvent.click(await screen.findByRole("tab", { name: "Postmortem" }))
  fireEvent.click(
    await screen.findByRole("button", { name: "Edit postmortem" })
  )
  const editor = screen.getByRole("textbox", { name: "Postmortem" })
  fireEvent.change(editor, {
    target: { value: "Confirmed: database failover" },
  })
  fireEvent.click(screen.getByRole("button", { name: "Save postmortem" }))
  await screen.findByText(/A newer revision was saved/)
  expect(editor).toHaveProperty("value", "Confirmed: database failover")
  expect(writes[0]).toMatchObject({
    path: "/dashboard/api/incidents/documents/local-1/postmortem",
    body: {
      markdown: "Confirmed: database failover",
      expected_revision: 3,
      request_id: expect.any(String),
    },
  })
})

it("copies the visible incident Markdown and source links without saving edits", async () => {
  const copy = vi.fn().mockResolvedValue(undefined)
  vi.stubGlobal("navigator", { clipboard: { writeText: copy } })
  const { writes } = setup((path) =>
    path === "/dashboard/api/incidents/documents/local-1"
      ? Response.json({
          ...documents,
          postmortem: {
            ...revision,
            markdown: "## Findings\n\nRecovered [slack:1].",
            evidence: [
              {
                id: "slack:1",
                source: "slack",
                url: "https://slack.com/archives/C1/p1",
                available: true,
              },
            ],
          },
        })
      : undefined
  )
  fireEvent.click(await screen.findByRole("tab", { name: "Postmortem" }))
  fireEvent.click(await screen.findByRole("button", { name: "Copy incident" }))
  await waitFor(() =>
    expect(copy).toHaveBeenLastCalledWith(
      "## Findings\n\nRecovered [1](<https://slack.com/archives/C1/p1>)."
    )
  )
  await screen.findByText("Incident copied as Markdown.")
  fireEvent.click(screen.getByRole("button", { name: "Edit postmortem" }))
  fireEvent.change(screen.getByRole("textbox", { name: "Postmortem" }), {
    target: { value: "## Follow-ups\n\n- Compare the rollout [slack:1]." },
  })
  fireEvent.click(screen.getByRole("button", { name: "Copy incident" }))
  await waitFor(() =>
    expect(copy).toHaveBeenLastCalledWith(
      "## Follow-ups\n\n- Compare the rollout [1](<https://slack.com/archives/C1/p1>)."
    )
  )
  expect(writes).toEqual([])
  expect(
    screen.queryByRole("heading", { name: "Status-page draft" })
  ).toBeNull()
})

it("preserves incident text and explains a clipboard failure", async () => {
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
  expect(screen.getByText("Cause remains unknown")).toBeTruthy()
})

it("keeps historical document reads available while hiding unauthorized mutation controls", async () => {
  setup((path) => {
    if (path === "/dashboard/api/incidents/records/local-1")
      return Response.json({ ...record, allowed_actions: [] })
    if (path === "/dashboard/api/incidents/documents/local-1")
      return Response.json({
        ...documents,
        postmortem: {
          ...revision,
          markdown: "## Findings\n\nCause remains unknown",
        },
        status_page_draft: {
          ...revision,
          kind: "status_page_draft",
          markdown: "## Customer update\n\nInvestigating checkout errors.",
        },
      })
  })
  fireEvent.click(await screen.findByRole("tab", { name: "Postmortem" }))
  await screen.findByRole("heading", { name: "Findings", level: 2 })
  expect(
    screen.queryByRole("heading", { name: "Customer update", level: 2 })
  ).toBeNull()
  expect(screen.getByRole("button", { name: "Copy incident" })).toBeTruthy()
  expect(screen.queryByRole("button", { name: "Edit postmortem" })).toBeNull()
  expect(screen.queryByRole("textbox", { name: "Postmortem" })).toBeNull()
  expect(screen.queryByRole("button", { name: "Save postmortem" })).toBeNull()
})

it("loads immutable revision content and displays unavailable evidence without links", async () => {
  setup((path) =>
    path === "/dashboard/api/incidents/documents/local-1"
      ? Response.json({
          ...documents,
          postmortem: {
            ...revision,
            evidence: [
              { id: "e1", source: "Expired trace", url: "", available: false },
            ],
          },
        })
      : path.endsWith("/revisions")
        ? Response.json({
            items: [
              {
                ...revision,
                revision: 1,
                markdown: "## First observation\n\n- Elevated latency",
              },
            ],
          })
        : undefined
  )
  fireEvent.click(await screen.findByRole("tab", { name: "Postmortem" }))
  await screen.findByText("Expired trace: evidence unavailable")
  fireEvent.click(
    screen.getAllByRole("button", { name: "Revision history" })[0]!
  )
  fireEvent.click(await screen.findByText(/Revision 1 · Alex/))
  await screen.findByRole("heading", { name: "First observation", level: 2 })
  expect(screen.getByText("Elevated latency", { selector: "li" })).toBeTruthy()
  expect(screen.queryByRole("link", { name: "Expired trace" })).toBeNull()
})

it("preserves unsaved documents through a transient refresh failure", async () => {
  let unavailable = false
  const { client } = setup((path) =>
    unavailable && path === "/dashboard/api/incidents/documents/local-1"
      ? Response.json({ detail: "Temporarily unavailable" }, { status: 503 })
      : undefined
  )
  fireEvent.click(await screen.findByRole("tab", { name: "Postmortem" }))
  fireEvent.click(
    await screen.findByRole("button", { name: "Edit postmortem" })
  )
  const editor = screen.getByRole("textbox", { name: "Postmortem" })
  fireEvent.change(editor, { target: { value: "Unsaved responder finding" } })
  unavailable = true
  await client.invalidateQueries({
    queryKey: ["incidents", "documents", "local-1", "current"],
  })
  expect(await screen.findByText("Temporarily unavailable")).toBeTruthy()
  expect(screen.getByRole("textbox", { name: "Postmortem" })).toHaveProperty(
    "value",
    "Unsaved responder finding"
  )
})

it.each([403, 404])(
  "hides previously loaded documents when read permission returns %s",
  async (status) => {
    let revoked = false
    const { client } = setup((path) =>
      revoked && path === "/dashboard/api/incidents/documents/local-1"
        ? Response.json({ detail: "Access revoked" }, { status })
        : undefined
    )
    fireEvent.click(await screen.findByRole("tab", { name: "Postmortem" }))
    await screen.findByText("Cause remains unknown")
    revoked = true
    await client.invalidateQueries({
      queryKey: ["incidents", "documents", "local-1", "current"],
    })
    await screen.findByText("Access revoked")
    expect(screen.queryByText("Cause remains unknown")).toBeNull()
    expect(screen.queryByRole("textbox", { name: "Postmortem" })).toBeNull()
  }
)
