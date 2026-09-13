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
  allowed_actions: [
    "resume",
    "edit_document",
    "attach_provider",
    "update_provider",
  ],
  trace_url: null,
}
const capabilities = {
  read: "supported",
  search: "supported",
  update_status: "supported",
  update_severity: "unsupported",
  postmortem_write: "unsupported",
  status_page_publish: "unsupported",
}
const snapshot = {
  title: "Checkout latency",
  status: { id: "investigating-1", name: "Investigating impact" },
  severity: { id: "sev2", name: "Major" },
  url: "https://app.incident.io/incidents/ext-1",
  postmortem: null,
}
const provider = {
  default_connection_name: null,
  binding: {
    provider: "incident_io",
    connection_name: "incident-io",
    external_id: "ext-1",
    url: snapshot.url,
  },
  snapshot,
  capabilities,
  status_options: [
    { id: "investigating-1", name: "Investigating impact" },
    { id: "resolved-1", name: "Resolved" },
  ],
  severity_options: [],
  configuration_error: null,
  configuration_error_kind: null,
  last_synced_at: 1788714000,
  error: null,
  error_kind: null,
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
    if (path === "/dashboard/api/incidents/providers/connections")
      return Response.json({
        items: [{ name: "incident-io", capabilities, schemas: {} }],
      })
    if (path === "/dashboard/api/incidents/providers/local-1")
      return Response.json(provider)
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

it("retains provider status until a responder update is confirmed, separately from paused analysis", async () => {
  let succeeded = false
  const { client, writes } = setup((path, init) => {
    if (path.endsWith("/status") && init?.method === "POST")
      return Response.json({ command_id: "op-1", status: "accepted" })
    if (path.endsWith("/operations/op-1"))
      return Response.json({
        id: "op-1",
        status: succeeded ? "succeeded" : "accepted",
        error: null,
      })
    if (succeeded && path === "/dashboard/api/incidents/providers/local-1")
      return Response.json({
        ...provider,
        snapshot: {
          ...snapshot,
          status: { id: "resolved-1", name: "Resolved" },
        },
      })
  })
  await screen.findByText("Investigating impact", { selector: "dd" })
  expect(screen.getByText("Paused")).toBeTruthy()
  fireEvent.change(screen.getByRole("combobox", { name: "Provider status" }), {
    target: { value: "resolved-1" },
  })
  fireEvent.click(
    screen.getByRole("button", { name: "Update incident status" })
  )
  await screen.findByText(/Provider update pending/)
  expect(
    screen.getByText("Investigating impact", { selector: "dd" })
  ).toBeTruthy()
  expect(writes[0]).toMatchObject({
    path: "/dashboard/api/incidents/providers/local-1/status",
    body: { status_id: "resolved-1", request_id: expect.any(String) },
  })
  succeeded = true
  await client.invalidateQueries({ queryKey: ["incidents", "provider"] })
  await screen.findByText("Resolved", { selector: "dd" })
  expect(screen.getByText("Paused")).toBeTruthy()
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

it("loads external historical incident detail using reads without attaching or enrolling", async () => {
  const { writes } = setup((path) => {
    if (path.endsWith("/search"))
      return Response.json({
        external: true,
        items: [
          {
            external_id: "old-1",
            title: "Past checkout outage",
            url: snapshot.url,
            status: snapshot.status,
            severity: snapshot.severity,
          },
        ],
      })
    if (path.endsWith("/external"))
      return Response.json({
        external: true,
        incident: {
          ...snapshot,
          title: "Past checkout outage",
          postmortem: "## Prior cause\n\nConnection pool exhausted",
        },
      })
  })
  fireEvent.click(await screen.findByText("Related provider history"))
  fireEvent.change(
    await screen.findByRole("searchbox", { name: "Search provider history" }),
    { target: { value: "checkout" } }
  )
  fireEvent.click(screen.getByRole("button", { name: "Search provider" }))
  fireEvent.click(
    await screen.findByRole("button", { name: "Past checkout outage" })
  )
  fireEvent.click(await screen.findByText("Provider postmortem"))
  await screen.findByRole("heading", { name: "Prior cause", level: 2 })
  expect(screen.getByText("Connection pool exhausted")).toBeTruthy()
  expect(writes).toEqual([
    {
      path: "/dashboard/api/incidents/providers/search",
      body: {
        incident_id: "local-1",
        connection_name: "incident-io",
        query: "checkout",
      },
    },
    {
      path: "/dashboard/api/incidents/providers/external",
      body: {
        incident_id: "local-1",
        connection_name: "incident-io",
        external_id: "old-1",
      },
    },
  ])
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
  expect(
    screen.queryByRole("button", { name: "Update incident status" })
  ).toBeNull()
  expect(
    screen.queryByRole("button", { name: "Attach provider incident" })
  ).toBeNull()
})

it("keeps status controls disabled while the provider is sending an update", async () => {
  const { client } = setup((path, init) => {
    if (path.endsWith("/status") && init?.method === "POST")
      return Response.json({ command_id: "sending-1", status: "accepted" })
    if (path.endsWith("/operations/sending-1"))
      return Response.json({ id: "sending-1", status: "sending", error: null })
  })
  fireEvent.change(
    await screen.findByRole("combobox", { name: "Provider status" }),
    { target: { value: "resolved-1" } }
  )
  fireEvent.click(
    screen.getByRole("button", { name: "Update incident status" })
  )
  await screen.findByText(/Provider update pending/)
  await waitFor(() =>
    expect(
      client.getQueryData([
        "incidents",
        "provider",
        "local-1",
        "operation",
        "sending-1",
      ])
    ).toMatchObject({ status: "sending" })
  )
  expect(
    screen.getByRole("button", { name: "Update incident status" })
  ).toHaveProperty("disabled", true)
})

it("attaches a selected workspace connection through an explicit responder command", async () => {
  const { writes } = setup((path, init) => {
    if (path === "/dashboard/api/incidents/providers/local-1")
      return Response.json({ ...provider, binding: null, snapshot: null })
    if (path.endsWith("/attach") && init?.method === "POST")
      return Response.json({ command_id: "attach-1", status: "accepted" })
    if (path.endsWith("/operations/attach-1"))
      return Response.json({ id: "attach-1", status: "accepted", error: null })
  })
  fireEvent.click(
    await screen.findByText("Attach provider incident", { selector: "summary" })
  )
  fireEvent.change(
    await screen.findByRole("textbox", { name: "Provider incident ID" }),
    { target: { value: "new-provider-1" } }
  )
  fireEvent.click(
    screen.getByRole("button", { name: "Attach provider incident" })
  )
  await screen.findByText(/Provider update pending/)
  expect(writes[0]).toMatchObject({
    path: "/dashboard/api/incidents/providers/local-1/attach",
    body: {
      connection_name: "incident-io",
      external_id: "new-provider-1",
      request_id: expect.any(String),
    },
  })
  expect(screen.getByText("No provider incident attached.")).toBeTruthy()
})

it("shows last confirmed provider state and sync failure without hiding document editing", async () => {
  setup((path) =>
    path === "/dashboard/api/incidents/providers/local-1"
      ? Response.json({
          ...provider,
          error: "Provider could not be reached",
          error_kind: "unavailable",
        })
      : undefined
  )
  await screen.findByText("Investigating impact", { selector: "dd" })
  expect(screen.getByText(/Provider could not be reached/)).toBeTruthy()
  fireEvent.click(await screen.findByRole("tab", { name: "Postmortem" }))
  fireEvent.click(
    await screen.findByRole("button", { name: "Edit postmortem" })
  )
  expect(
    await screen.findByRole("textbox", { name: "Postmortem" })
  ).toHaveProperty("value", "Cause remains unknown")
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

it("disables lifecycle updates when provider status configuration is unavailable", async () => {
  setup((path) =>
    path === "/dashboard/api/incidents/providers/local-1"
      ? Response.json({
          ...provider,
          status_options: [],
          configuration_error: "Unable to load provider statuses",
          configuration_error_kind: "unavailable",
        })
      : undefined
  )
  await screen.findByText("Unable to load provider statuses")
  expect(
    screen.getByRole("combobox", { name: "Provider status" })
  ).toHaveProperty("disabled", true)
  expect(
    screen.getByRole("button", { name: "Update incident status" })
  ).toHaveProperty("disabled", true)
})

it("explains unavailable provider configuration when writes are disabled", async () => {
  setup((path) =>
    path === "/dashboard/api/incidents/providers/local-1"
      ? Response.json({
          ...provider,
          capabilities: { ...capabilities, update_status: "unavailable" },
          status_options: [],
          configuration_error: "Organisation configuration unavailable",
          configuration_error_kind: "unavailable",
        })
      : undefined
  )
  await screen.findByText("Organisation configuration unavailable")
  expect(
    screen.queryByRole("button", { name: "Update incident status" })
  ).toBeNull()
  expect(
    screen.getByText("Investigating impact", { selector: "dd" })
  ).toBeTruthy()
})

it("preselects the workspace default and attaches only after an explicit submission", async () => {
  const { writes } = setup((path, init) => {
    if (path === "/dashboard/api/incidents/providers/connections")
      return Response.json({
        items: [
          { name: "incident-io", capabilities },
          { name: "incident-default", capabilities },
        ],
      })
    if (path === "/dashboard/api/incidents/providers/local-1")
      return Response.json({
        ...provider,
        binding: null,
        snapshot: null,
        default_connection_name: "incident-default",
      })
    if (path.endsWith("/attach") && init?.method === "POST")
      return Response.json({ command_id: "attach-default", status: "accepted" })
    if (path.endsWith("/operations/attach-default"))
      return Response.json({
        id: "attach-default",
        status: "accepted",
        error: null,
      })
  })
  fireEvent.click(
    await screen.findByText("Attach provider incident", { selector: "summary" })
  )
  const connection = await screen.findByRole("combobox", {
    name: "Workspace connection",
  })
  await waitFor(() =>
    expect(connection).toHaveProperty("value", "incident-default")
  )
  expect(writes).toEqual([])
  fireEvent.change(
    screen.getByRole("textbox", { name: "Provider incident ID" }),
    { target: { value: "ext-default" } }
  )
  fireEvent.click(
    screen.getByRole("button", { name: "Attach provider incident" })
  )
  await screen.findByText(/Provider update pending/)
  expect(writes).toHaveLength(1)
  expect(writes[0]).toMatchObject({
    path: "/dashboard/api/incidents/providers/local-1/attach",
    body: {
      connection_name: "incident-default",
      external_id: "ext-default",
      request_id: expect.any(String),
    },
  })
})

it("preserves the current binding and an explicit connection choice ahead of the default", async () => {
  const { client } = setup((path) => {
    if (path === "/dashboard/api/incidents/providers/connections")
      return Response.json({
        items: [
          { name: "incident-io", capabilities },
          { name: "incident-default", capabilities },
        ],
      })
    if (path === "/dashboard/api/incidents/providers/local-1")
      return Response.json({
        ...provider,
        default_connection_name: "incident-default",
      })
  })
  fireEvent.click(await screen.findByText("Related provider history"))
  const connection = await screen.findByRole("combobox", {
    name: "History connection",
  })
  expect(connection).toHaveProperty("value", "incident-io")
  fireEvent.change(connection, { target: { value: "incident-default" } })
  await client.invalidateQueries({
    queryKey: ["incidents", "provider", "local-1", "state"],
  })
  expect(connection).toHaveProperty("value", "incident-default")
})

it.each(["permission_denied", "unavailable"])(
  "refreshes hidden provider state after %s without reattaching",
  async (errorKind) => {
    let refreshed = false
    const { writes } = setup((path, init) => {
      if (path === "/dashboard/api/incidents/providers/local-1" && !refreshed)
        return Response.json({
          ...provider,
          binding: null,
          snapshot: null,
          capabilities: {},
          last_synced_at: null,
          error: "Provider access requires verification",
          error_kind: errorKind,
        })
      if (path.endsWith("/refresh") && init?.method === "POST")
        return Response.json({ command_id: "recovery-1", status: "accepted" })
      if (path.endsWith("/operations/recovery-1")) {
        refreshed = true
        return Response.json({
          id: "recovery-1",
          status: "succeeded",
          error: null,
        })
      }
    })
    await screen.findByText(/Provider access requires verification/)
    expect(screen.queryByText("No provider incident attached.")).toBeNull()
    expect(screen.queryByText(/ext-1/)).toBeNull()
    expect(
      screen.queryByRole("link", { name: "Open provider incident" })
    ).toBeNull()
    fireEvent.click(screen.getByRole("button", { name: "Refresh provider" }))
    await screen.findByText("Investigating impact", { selector: "dd" })
    expect(
      screen
        .getByRole("link", { name: "Open provider incident" })
        .getAttribute("href")
    ).toBe("https://app.incident.io/incidents/ext-1")
    expect(writes).toEqual([
      {
        path: "/dashboard/api/incidents/providers/local-1/refresh",
        body: { request_id: expect.any(String) },
      },
    ])
  }
)
