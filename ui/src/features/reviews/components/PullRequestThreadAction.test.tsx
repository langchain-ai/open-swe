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
  missingChecks: [],
  unresolvedThreads: 1,
}

it("keeps status errors in a popover and retries without starting work", async () => {
  const status = vi
    .spyOn(api, "pullRequestThreadStatus")
    .mockRejectedValueOnce(new Error("Couldn’t reach the server."))
    .mockResolvedValue({ running: false })
  const fix = vi.spyOn(api, "fixPullRequest")
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <PullRequestThreadAction pr={pr} login="me" actions={["fix-conflicts"]} />
    </QueryClientProvider>
  )

  const unavailable = await screen.findByRole("button", {
    name: "Fix conflicts unavailable",
  })
  expect(screen.queryByText("Couldn’t reach the server.")).toBeNull()
  fireEvent.click(unavailable)
  expect(await screen.findByText("Couldn’t reach the server.")).toBeTruthy()
  fireEvent.click(screen.getByRole("button", { name: "Retry" }))

  const ready = await screen.findByRole("button", {
    name: "Fix conflicts",
  })
  expect(ready.hasAttribute("disabled")).toBe(false)
  expect(status).toHaveBeenCalledTimes(2)
  expect(fix).not.toHaveBeenCalled()
})

it("blocks the sibling action as soon as one is queued", async () => {
  vi.spyOn(api, "pullRequestThreadStatus").mockResolvedValue({
    running: false,
  })
  let finish: (result: PullRequestThreadResult) => void = () => {}
  vi.spyOn(api, "fixPullRequest").mockImplementation(
    () => new Promise<PullRequestThreadResult>((resolve) => (finish = resolve))
  )
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <PullRequestThreadAction pr={pr} login="me" actions={["fix"]} />
      <PullRequestThreadAction
        pr={pr}
        login="me"
        actions={["address-comments"]}
      />
    </QueryClientProvider>
  )

  const fix = await screen.findByRole("button", { name: "Fix" })
  const address = screen.getByRole("button", { name: "Address comments" })
  await waitFor(() => expect(address.hasAttribute("disabled")).toBe(false))

  fireEvent.click(fix)
  await screen.findByRole("button", { name: "Fix queued" })
  finish({ thread_id: "t", already_running: false })

  await screen.findByRole("button", { name: "Fix queued" })
  const blocked = screen.getByRole("button", { name: "Addressing comments" })
  expect(blocked.hasAttribute("disabled")).toBe(true)
})
