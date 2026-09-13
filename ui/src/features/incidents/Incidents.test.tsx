/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"
import { useState } from "react"
import { afterEach, expect, it, vi } from "vitest"

import { IncidentList } from "./IncidentList"
import { IncidentDetail } from "./IncidentDetail"
import { IncidentSettings } from "./IncidentSettings"
import type { IncidentView } from "./api"

vi.mock("@tanstack/react-router", () => ({
  Link: ({
    children,
    to,
    params,
    ...props
  }: React.ComponentProps<"a"> & {
    to: string
    params?: { incidentId: string }
  }) => (
    <a
      href={params ? to.replace("$incidentId", params.incidentId) : to}
      {...props}
    >
      {children}
    </a>
  ),
}))

const summary = {
  id: "incident-1",
  channel_id: "C123",
  channel_name: "inc-checkout",
  title: "Checkout latency",
  is_archived: false,
  status: "watching",
  reason: null,
  latest_finding: "Elevated latency after deploy",
  updated_at: 1788714000,
  slack_url: "https://example.slack.com/archives/C123",
}
const detail = {
  incident: summary,
  report: null,
  coverage: { gaps: ["Missing deployed revision"] },
  activity: [],
  allowed_actions: ["ask", "investigate_again", "pause", "complete"],
  trace_url: "https://smith.langchain.com/o/org/projects/p/proj/t/thread-1",
}
const settings = {
  policy: {
    enabled: false,
    workspace_id: "T123",
    slack_app_id: "A123",
    channel_prefix: "inc-",
    excluded_channel_ids: [],
    model: null,
    max_model_calls: 20,
    max_pass_seconds: 300,
    idle_timeout_seconds: 7200,
    max_watch_seconds: 86400,
    version: 4,
    enabled_at: 0,
  },
  connection: {
    slack_configured: true,
    workspace_id: "T123",
    slack_app_id: "A123",
    verified_at: null,
    error: null,
  },
  last_operation: null,
}

function stubFetch(
  handler: (input: string, init: RequestInit) => Promise<Response>
) {
  vi.stubGlobal("fetch", (input: string, init: RequestInit) => {
    const path = new URL(input, "http://localhost").pathname
    if (path === "/dashboard/api/incidents/providers/connections")
      return Promise.resolve(Response.json({ items: [] }))
    if (path.startsWith("/dashboard/api/incidents/providers/"))
      return Promise.resolve(
        Response.json({
          binding: null,
          snapshot: null,
          capabilities: {},
          last_synced_at: null,
          error: null,
          error_kind: null,
        })
      )
    if (
      path.startsWith("/dashboard/api/incidents/documents/") &&
      !path.endsWith("/history")
    )
      return Promise.resolve(
        Response.json({
          incident_id: "incident-1",
          postmortem: null,
          status_page_draft: null,
          operations: [],
        })
      )
    return handler(input, init)
  })
}

function mount(component: React.ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  render(<QueryClientProvider client={client}>{component}</QueryClientProvider>)
  return client
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

it("applies view and search filters through the API and links the matching incident", async () => {
  stubFetch(async (input: string) => {
    const url = new URL(input, "http://localhost")
    const matches =
      url.searchParams.get("view") === "paused" &&
      url.searchParams.get("q") === "checkout"
    return Response.json({
      items: matches ? [{ ...summary, status: "paused" }] : [],
      next_cursor: null,
    })
  })
  function Page() {
    const [view, setView] = useState<IncidentView>("active")
    return <IncidentList view={view} onViewChange={setView} />
  }
  mount(<Page />)
  await screen.findByText("Waiting for matching channel events")
  fireEvent.click(screen.getByRole("button", { name: "Paused" }))
  fireEvent.change(screen.getByRole("searchbox"), {
    target: { value: "checkout" },
  })
  const link = await screen.findByRole("link", { name: /Checkout latency/ })
  expect(link.getAttribute("href")).toBe("/incidents/incident-1")
  expect(screen.queryByText("Waiting for matching channel events")).toBeNull()
})

it("shows access errors instead of an empty inbox and retries a failed read", async () => {
  let available = false
  stubFetch(async () =>
    available
      ? Response.json({ items: [summary], next_cursor: null })
      : Response.json(
          { detail: "Incidents access is required" },
          { status: 403 }
        )
  )
  mount(<IncidentList view="active" onViewChange={() => {}} />)
  expect(await screen.findByRole("alert")).toHaveProperty(
    "textContent",
    expect.stringContaining("Incidents access is required")
  )
  expect(screen.queryByText("Waiting for matching channel events")).toBeNull()
  available = true
  fireEvent.click(screen.getByRole("button", { name: "Try again" }))
  await screen.findByRole("link", { name: /Checkout latency/ })
})

it("loads additional authorized incidents from the next cursor", async () => {
  stubFetch(async (input: string) => {
    const cursor = new URL(input, "http://localhost").searchParams.get("cursor")
    return Response.json(
      cursor === "25"
        ? {
            items: [{ ...summary, id: "incident-2", title: "Worker backlog" }],
            next_cursor: null,
          }
        : { items: [summary], next_cursor: "25" }
    )
  })
  mount(<IncidentList view="active" onViewChange={() => {}} />)
  await screen.findByRole("link", { name: /Checkout latency/ })
  fireEvent.click(screen.getByRole("button", { name: "Load more incidents" }))
  await screen.findByRole("link", { name: /Worker backlog/ })
  expect(screen.getByRole("link", { name: /Checkout latency/ })).toBeTruthy()
  expect(
    screen.queryByRole("button", { name: "Load more incidents" })
  ).toBeNull()
})

it("keeps the applied watch status until a pause receipt has been processed", async () => {
  const commands: Array<Record<string, string>> = []
  let currentStatus = "watching"
  stubFetch(async (_input: string, init: RequestInit) => {
    if (init?.method === "POST") {
      commands.push(JSON.parse(init.body as string))
      return Response.json(
        { command_id: "command-1", status: "accepted" },
        { status: 202 }
      )
    }
    return Response.json({
      ...detail,
      incident: { ...summary, status: currentStatus },
    })
  })
  const client = mount(<IncidentDetail incidentId="incident-1" />)
  fireEvent.click(await screen.findByRole("button", { name: "Pause" }))
  await screen.findByText(/Pause requested/)
  expect(screen.getByText("Watching")).toBeTruthy()
  expect(commands[0]).toMatchObject({
    action: "pause",
    request_id: expect.any(String),
  })
  expect(screen.queryByRole("button", { name: "Resume" })).toBeNull()
  currentStatus = "investigating"
  await client.invalidateQueries({ queryKey: ["incidents"] })
  await screen.findByText("Investigating")
  expect(screen.getByText(/Pause requested/)).toBeTruthy()
})

it("retains a failed question and retries it with the same command identity", async () => {
  const commands: Array<Record<string, string>> = []
  stubFetch(async (_input: string, init: RequestInit) => {
    if (init?.method === "POST") {
      commands.push(JSON.parse(init.body as string))
      return commands.length === 1
        ? Response.json({ detail: "Dispatch unavailable" }, { status: 503 })
        : Response.json(
            { command_id: "command-question", status: "accepted" },
            { status: 202 }
          )
    }
    return Response.json(detail)
  })
  mount(<IncidentDetail incidentId="incident-1" />)
  const input = await screen.findByRole("textbox", { name: "Ask Open SWE" })
  fireEvent.change(input, {
    target: { value: "  Did database latency change?  " },
  })
  fireEvent.click(screen.getByRole("button", { name: "Send question" }))
  expect((await screen.findByRole("alert")).textContent).toContain(
    "Dispatch unavailable"
  )
  expect(input).toHaveProperty("value", "  Did database latency change?  ")
  fireEvent.click(screen.getByRole("button", { name: "Send question" }))
  await screen.findByText(/Question submitted/)
  expect(input).toHaveProperty("value", "")
  expect(commands).toHaveLength(2)
  expect(commands[0]).toMatchObject({
    action: "ask",
    text: "Did database latency change?",
  })
  expect(commands[1]?.request_id).toBe(commands[0]?.request_id)
})

it("renders findings and referenced evidence while hiding unauthorized commands", async () => {
  stubFetch(async () =>
    Response.json({
      ...detail,
      allowed_actions: [],
      report: {
        id: "report-1",
        summary:
          "Retry volume increased [trace-1]. Unknown source [unverified].",
        impact: "Checkout requests are slow [trace-1]",
        next_steps: ["Consider reducing retries [trace-1]"],
        outcome: "inconclusive",
        hypotheses: [
          {
            title: "Retry amplification",
            assessment: "plausible",
            evidence_ids: ["trace-1"],
          },
        ],
        evidence: [
          {
            id: "trace-1",
            source: "Datadog",
            url: "https://app.datadoghq.com/apm/trace/1",
            summary: "Retries dominate the trace",
            retrieved_at: 1788714000,
          },
        ],
        checked: ["Database latency unchanged"],
        gaps: [],
        questions: [],
        created_at: 1788714000,
      },
    })
  )
  mount(<IncidentDetail incidentId="incident-1" />)
  await screen.findByText("Retry amplification")
  expect(screen.getByText("Plausible")).toBeTruthy()
  const citations = screen.getAllByRole("link", { name: "Evidence 1: Datadog" })
  expect(citations).toHaveLength(3)
  expect(screen.getByText(/Consider reducing retries/)).toBeTruthy()
  expect(citations[0]?.getAttribute("href")).toBe(
    "https://app.datadoghq.com/apm/trace/1"
  )
  expect(screen.getByText(/Unknown source \[unverified\]/)).toBeTruthy()
  expect(screen.queryByText(/\[trace-1\]/)).toBeNull()
  expect(
    screen
      .getByRole("link", { name: /Retries dominate the trace/ })
      .getAttribute("href")
  ).toBe("https://app.datadoghq.com/apm/trace/1")
  expect(screen.getByText("Missing deployed revision")).toBeTruthy()
  expect(screen.queryByRole("button", { name: "Pause" })).toBeNull()
  expect(screen.queryByRole("textbox", { name: "Ask Open SWE" })).toBeNull()
  expect(
    screen.getByRole("link", { name: /Open trace/ }).getAttribute("href")
  ).toBe("https://smith.langchain.com/o/org/projects/p/proj/t/thread-1")
})

it("saves settings as a versioned request and preserves unsaved values on conflict", async () => {
  const writes: Array<{
    expected_version: number
    policy: typeof settings.policy
  }> = []
  stubFetch(async (_input: string, init: RequestInit) => {
    if (init?.method === "PATCH") {
      writes.push(JSON.parse(init.body as string))
      return Response.json(
        { detail: "Settings changed. Reload before saving." },
        { status: 409 }
      )
    }
    return Response.json(settings)
  })
  mount(<IncidentSettings />)
  const prefix = await screen.findByRole("textbox", { name: "Channel prefix" })
  fireEvent.change(prefix, { target: { value: "incident-" } })
  fireEvent.change(
    screen.getByRole("textbox", { name: "Excluded channel IDs" }),
    { target: { value: "C1, C2, C1" } }
  )
  fireEvent.click(screen.getByRole("button", { name: "Save settings" }))
  await waitFor(() =>
    expect(screen.getByRole("alert").textContent).toContain("Settings changed")
  )
  expect(writes[0]?.expected_version).toBe(4)
  expect(writes[0]?.policy.channel_prefix).toBe("incident-")
  expect(screen.queryByRole("textbox", { name: "Workspace ID" })).toBeNull()
  expect(screen.getByText("A123")).toBeTruthy()
  expect(writes[0]?.policy.workspace_id).toBe("T123")
  expect(writes[0]?.policy.excluded_channel_ids).toEqual(["C1", "C2"])
  expect(prefix).toHaveProperty("value", "incident-")
})

it("searches durable incident history and links records after operational cleanup", async () => {
  const paths: string[] = []
  stubFetch(async (input) => {
    paths.push(input)
    return Response.json({
      items: [
        {
          id: "old-1",
          title: "Retained outage",
          channel_name: "inc-old",
          status: "completed",
          updated_at: 1788714000,
          postmortem_revision: 5,
        },
      ],
      next_cursor: null,
    })
  })
  mount(<IncidentList view="history" onViewChange={() => {}} />)
  expect(
    (await screen.findByRole("link", { name: /Retained outage/ })).getAttribute(
      "href"
    )
  ).toBe("/incidents/old-1")
  expect(paths[0]).toContain("/dashboard/api/incidents/documents/history?")
  fireEvent.change(screen.getByRole("searchbox"), {
    target: { value: "outage" },
  })
  await waitFor(() =>
    expect(paths.some((path) => path.includes("q=outage"))).toBe(true)
  )
})

it("removes machine citation IDs from incident list previews", async () => {
  stubFetch(async () =>
    Response.json({
      items: [
        {
          ...summary,
          latest_finding:
            "Synthetic errors recovered [slack:1, datadog:2]. Impact [unverified].",
        },
      ],
      next_cursor: null,
    })
  )
  mount(<IncidentList view="active" onViewChange={() => {}} />)
  const row = await screen.findByRole("link", { name: /Checkout latency/ })
  expect(row.textContent).toContain(
    "Synthetic errors recovered. Impact [unverified]."
  )
  expect(row.textContent).not.toContain("slack:1")
})
