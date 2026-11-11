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
import { PullRequestThreadAction } from "./PullRequestThreadAction"

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
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

it("blocks the button on another card as soon as one is queued", async () => {
  vi.spyOn(api, "pullRequestThreadStatus").mockResolvedValue({
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
})
