/** @vitest-environment jsdom */

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, expect, it, vi } from "vitest"

import { ReviewSettings } from "@/features/settings/components/ReviewSettings"
import type {
  WorkspaceSettings,
  WorkspaceSettingsOverrides,
  WorkspaceSettingsView,
} from "@/lib/api"

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

/** The switch in the settings row carrying ``label``; rows have no label link. */
function rowSwitch(label: string): HTMLElement {
  const row = screen.getByText(label).closest("div")
  if (!row) throw new Error(`no settings row for ${label}`)
  return within(row).getByRole("switch")
}

const SETTINGS: WorkspaceSettings = {
  review_draft_prs: false,
  pr_summaries: true,
  review_trace_links: true,
  org_guidelines: "oss guidelines",
  default_agent_model: null,
  default_agent_reasoning_effort: null,
  default_agent_subagent_model: null,
  default_agent_subagent_reasoning_effort: null,
  default_reviewer_model: null,
  default_reviewer_reasoning_effort: null,
  default_reviewer_subagent_model: null,
  default_reviewer_subagent_reasoning_effort: null,
}

const VIEW: WorkspaceSettingsView = { effective: SETTINGS, overrides: {} }

it("reads the workspace's settings and writes only its own override", async () => {
  const reads: string[] = []
  const writes: Array<{ url: string; body: WorkspaceSettingsOverrides }> = []
  let stored = VIEW
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input)
    if (init?.method === "PUT") {
      const body = JSON.parse(String(init.body)) as WorkspaceSettingsOverrides
      writes.push({ url, body })
      stored = { effective: { ...SETTINGS, ...body }, overrides: body }
      return new Response(JSON.stringify(stored))
    }
    reads.push(url)
    return new Response(JSON.stringify(stored))
  })
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })

  render(
    <QueryClientProvider client={client}>
      <ReviewSettings scope={{ kind: "workspace", slug: "oss" }} canEdit />
    </QueryClientProvider>
  )

  await waitFor(() => expect(reads.length).toBeGreaterThan(0))
  expect(reads.every((url) => url.includes("/workspaces/oss/settings"))).toBe(
    true
  )
  await waitFor(() =>
    expect(
      screen.getByRole("textbox", { name: "Review guidelines" })
    ).toHaveProperty("value", "oss guidelines")
  )
  expect(screen.getByText("Inherited from the instance.")).toBeTruthy()

  fireEvent.click(rowSwitch("PR Summaries"))

  await waitFor(() => expect(writes.length).toBe(1))
  expect(writes[0]!.url).toContain("/workspaces/oss/settings")
  // Only the field that changed becomes an override; guidelines stay inherited.
  expect(writes[0]!.body).toEqual({ pr_summaries: false })
  expect(
    await screen.findByRole("button", {
      name: "Reset PR Summaries to the instance value",
    })
  ).toBeTruthy()
  client.clear()
})

it("saves and resets approval criteria without changing the review guidelines", async () => {
  const writes: WorkspaceSettingsOverrides[] = []
  const base = { ...SETTINGS, approval_policy: "Documentation only" }
  let stored: WorkspaceSettingsView = { effective: base, overrides: {} }
  vi.spyOn(globalThis, "fetch").mockImplementation(async (_input, init) => {
    if (init?.method === "PUT") {
      const body = JSON.parse(String(init.body)) as WorkspaceSettingsOverrides
      writes.push(body)
      stored = { effective: { ...base, ...body }, overrides: body }
      return new Response(JSON.stringify(stored))
    }
    return new Response(JSON.stringify(stored))
  })
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <ReviewSettings scope={{ kind: "workspace", slug: "oss" }} canEdit />
    </QueryClientProvider>
  )
  const policy = screen.getByRole("textbox", { name: "Approval policy" })
  await waitFor(() =>
    expect(policy).toHaveProperty("value", "Documentation only")
  )
  fireEvent.change(policy, {
    target: { value: "Allow small tested refactors" },
  })
  fireEvent.change(screen.getByRole("textbox", { name: "Review guidelines" }), {
    target: { value: "Unsaved review guidance" },
  })
  fireEvent.click(screen.getByRole("button", { name: "Save approval policy" }))
  await waitFor(() =>
    expect(writes).toEqual([
      { approval_policy: "Allow small tested refactors" },
    ])
  )
  const reset = screen.getByRole("button", {
    name: "Reset approval policy to instance",
  })
  await waitFor(() => expect(reset).toHaveProperty("disabled", false))
  fireEvent.click(reset)
  await waitFor(() => expect(writes[1]).toEqual({}))
  await waitFor(() =>
    expect(policy).toHaveProperty("value", "Documentation only")
  )
  expect(
    screen.getByRole("textbox", { name: "Review guidelines" })
  ).toHaveProperty("value", "Unsaved review guidance")
  client.clear()
})

it("does not write before the workspace's settings have loaded", async () => {
  const writes: string[] = []
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    if (init?.method === "PUT") {
      writes.push(String(input))
      return new Response(JSON.stringify(VIEW))
    }
    return new Promise(() => {})
  })
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })

  render(
    <QueryClientProvider client={client}>
      <ReviewSettings scope={{ kind: "workspace", slug: "oss" }} canEdit />
    </QueryClientProvider>
  )

  fireEvent.click(rowSwitch("PR Summaries"))
  expect(writes).toEqual([])
  client.clear()
})

it("keeps the approval policy independent of submission settings", async () => {
  let settings: WorkspaceSettings = {
    ...SETTINGS,
    approval_policy: null,
  }
  const writes: WorkspaceSettingsOverrides[] = []
  vi.spyOn(globalThis, "fetch").mockImplementation(async (_input, init) => {
    if (init?.method === "PUT") {
      const body = JSON.parse(String(init.body)) as WorkspaceSettingsOverrides
      writes.push(body)
      settings = { ...settings, ...body }
    }
    return new Response(JSON.stringify(settings), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    })
  })
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <ReviewSettings scope={{ kind: "instance" }} canEdit />
    </QueryClientProvider>
  )
  const policy = await screen.findByRole("textbox", { name: "Approval policy" })
  await waitFor(() => expect(policy).toHaveProperty("disabled", false))
  expect(policy).toHaveProperty("value", "")
  fireEvent.change(policy, { target: { value: "Docs only" } })
  fireEvent.click(screen.getByRole("button", { name: "Save approval policy" }))
  await waitFor(() => expect(writes).toHaveLength(1))
  expect(writes[0]?.approval_policy).toBe("Docs only")
  fireEvent.click(screen.getByRole("button", { name: "Clear approval policy" }))
  await waitFor(() => expect(writes).toHaveLength(2))
  expect(writes[1]?.approval_policy).toBeNull()
})
