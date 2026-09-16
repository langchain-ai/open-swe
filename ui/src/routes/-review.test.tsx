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

import { ReviewTeamSettings } from "@/features/settings/components/ReviewTeamSettings"
import type { TeamSettings } from "@/lib/api"

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

const SETTINGS: TeamSettings = {
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

it("reads and writes the settings of the selected workspace", async () => {
  const reads: string[] = []
  const writes: Array<{ url: string; body: TeamSettings }> = []
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input)
    if (init?.method === "PUT") {
      const body = JSON.parse(String(init.body)) as TeamSettings
      writes.push({ url, body })
      return new Response(JSON.stringify(body))
    }
    reads.push(url)
    return new Response(JSON.stringify(SETTINGS))
  })
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })

  render(
    <QueryClientProvider client={client}>
      <ReviewTeamSettings workspace="oss" canEdit={true} />
    </QueryClientProvider>
  )

  await waitFor(() => expect(reads.length).toBeGreaterThan(0))
  expect(
    reads.every((url) => url.includes("/team-settings?workspace=oss"))
  ).toBe(true)
  await waitFor(() =>
    expect(screen.getByRole("textbox")).toHaveProperty(
      "value",
      "oss guidelines"
    )
  )

  fireEvent.click(rowSwitch("PR Summaries"))

  await waitFor(() => expect(writes.length).toBe(1))
  expect(writes[0]!.url).toContain("/team-settings?workspace=oss")
  expect(writes[0]!.body.pr_summaries).toBe(false)
  expect(writes[0]!.body.org_guidelines).toBe("oss guidelines")
  client.clear()
})

it("does not write before the workspace's settings have loaded", async () => {
  const writes: string[] = []
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    if (init?.method === "PUT") {
      writes.push(String(input))
      return new Response(JSON.stringify(SETTINGS))
    }
    return new Promise(() => {})
  })
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })

  render(
    <QueryClientProvider client={client}>
      <ReviewTeamSettings workspace="oss" canEdit={true} />
    </QueryClientProvider>
  )

  fireEvent.click(rowSwitch("PR Summaries"))
  expect(writes).toEqual([])
  client.clear()
})
