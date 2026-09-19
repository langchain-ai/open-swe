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
import { api } from "@/lib/api"

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
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input)
    if (init?.method === "PUT") {
      const body = JSON.parse(String(init.body)) as WorkspaceSettingsOverrides
      writes.push({ url, body })
      const view: WorkspaceSettingsView = {
        effective: { ...SETTINGS, ...body },
        overrides: body,
      }
      return new Response(JSON.stringify(view))
    }
    reads.push(url)
    return new Response(JSON.stringify(VIEW))
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
    expect(screen.getByRole("textbox")).toHaveProperty(
      "value",
      "oss guidelines"
    )
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

it("adopts inherited guidelines after a successful reset", async () => {
  const overridden: WorkspaceSettingsView = {
    effective: { ...SETTINGS, org_guidelines: "workspace old" },
    overrides: { org_guidelines: "workspace old" },
  }
  vi.spyOn(api, "getWorkspaceSettings").mockResolvedValue(overridden)
  const save = vi.spyOn(api, "saveWorkspaceSettings").mockResolvedValue({
    effective: { ...SETTINGS, org_guidelines: "instance inherited" },
    overrides: {},
  })
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <ReviewSettings scope={{ kind: "workspace", slug: "oss" }} canEdit />
    </QueryClientProvider>
  )
  const editor = await screen.findByLabelText("Shared review guidelines")
  await waitFor(() => expect(editor).toHaveProperty("value", "workspace old"))
  fireEvent.change(editor, { target: { value: "unsaved new" } })
  const reset = await screen.findByRole("button", { name: "Reset to instance" })
  await waitFor(() => expect(reset.matches(":disabled")).toBe(false))
  fireEvent.click(reset)

  await waitFor(() => expect(save).toHaveBeenCalled())
  await waitFor(() =>
    expect(editor).toHaveProperty("value", "instance inherited")
  )
  expect(screen.queryByText("Unsaved changes")).toBeNull()
})

it("retains a workspace guidelines draft when reset fails", async () => {
  const overridden: WorkspaceSettingsView = {
    effective: { ...SETTINGS, org_guidelines: "workspace old" },
    overrides: { org_guidelines: "workspace old" },
  }
  vi.spyOn(api, "getWorkspaceSettings").mockResolvedValue(overridden)
  vi.spyOn(api, "saveWorkspaceSettings").mockRejectedValue(
    new Error("reset failed")
  )
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <ReviewSettings scope={{ kind: "workspace", slug: "oss" }} canEdit />
    </QueryClientProvider>
  )
  const editor = await screen.findByLabelText("Shared review guidelines")
  await waitFor(() => expect(editor).toHaveProperty("value", "workspace old"))
  fireEvent.change(editor, { target: { value: "unsaved new" } })
  const reset = await screen.findByRole("button", { name: "Reset to instance" })
  await waitFor(() => expect(reset.matches(":disabled")).toBe(false))
  fireEvent.click(reset)

  expect((await screen.findAllByText(/reset failed/i)).length).toBeGreaterThan(
    0
  )
  expect(editor).toHaveProperty("value", "unsaved new")
  expect(screen.getByText("Unsaved changes")).toBeTruthy()
})
