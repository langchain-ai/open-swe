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

import {
  api,
  type OpenPullRequest,
  type PullRequestThreadResult,
} from "@/lib/api"
import { InvalidationTab } from "@/lib/invalidations/client"
import { PullRequestThreadAction } from "./PullRequestThreadAction"

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

const pr: OpenPullRequest = {
  repo: "acme/app",
  number: 7,
  title: "Change",
  state: "open",
  draft: false,
  additions: 1,
  deletions: 1,
  mergeable: true,
  mergeState: "blocked",
  headSha: "a".repeat(40),
  headRef: "feature",
  reviewDecision: "none",
  reviewers: [],
  reviewRequired: false,
  statusAvailable: true,
  createdAt: null,
  updatedAt: null,
  ci: "failing",
  failingChecks: ["lint"],
  pendingChecks: [],
  requiredChecksPassing: true,
  missingChecks: [],
  unresolvedThreads: 1,
  conversationResolutionRequired: false,
}

it("blocks duplicate fixes and enables both cards again when the run stops", async () => {
  const status = vi.spyOn(api, "pullRequestThreadStatus").mockResolvedValue({
    running: false,
  })
  let finish: (result: PullRequestThreadResult) => void = () => {}
  vi.spyOn(api, "fixUpPullRequest").mockImplementation(
    () => new Promise<PullRequestThreadResult>((resolve) => (finish = resolve))
  )
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <div data-testid="first">
        <PullRequestThreadAction pr={pr} login="me" />
      </div>
      <div data-testid="second">
        <PullRequestThreadAction pr={pr} login="me" />
      </div>
    </QueryClientProvider>
  )

  const [first, second] = await screen.findAllByRole("button", {
    name: "Fix up",
  })
  await waitFor(() => expect(second!.hasAttribute("disabled")).toBe(false))

  fireEvent.click(first!)
  await screen.findByRole("button", { name: "Fix up queued" })
  finish({ thread_id: "t", already_running: false })

  await screen.findByRole("button", { name: "Fix up queued" })
  const blocked = screen.getByRole("button", { name: "Fixing up" })
  expect(blocked.hasAttribute("disabled")).toBe(true)

  status.mockResolvedValue({ running: true })
  await client.invalidateQueries({ queryKey: ["pr-thread-status"] })
  expect(
    screen
      .getAllByRole("button")
      .every((button) => button.hasAttribute("disabled"))
  ).toBe(true)

  const channel: { receive?: (event: { data: unknown }) => void } = {}
  vi.stubGlobal(
    "BroadcastChannel",
    class {
      set onmessage(handler: (event: { data: unknown }) => void) {
        channel.receive = handler
      }
      postMessage() {}
      close() {}
    }
  )
  vi.stubGlobal("navigator", {
    locks: { request: () => new Promise(() => {}) },
  })
  const tab = new InvalidationTab(client, () => {})
  tab.start()
  status.mockResolvedValue({ running: false })
  channel.receive?.({
    data: { kind: "invalidated", topics: ["pull-requests/acme/app/7"] },
  })
  await waitFor(() => {
    const buttons = screen.getAllByRole("button", { name: "Fix up" })
    expect(buttons).toHaveLength(2)
    expect(buttons.every((button) => !button.hasAttribute("disabled"))).toBe(
      true
    )
  })
  tab.stop()
  client.clear()
})
