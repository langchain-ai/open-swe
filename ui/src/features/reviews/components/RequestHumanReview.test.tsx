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
import { api, type OpenPullRequest } from "@/lib/api"
import { RequestHumanReview } from "./RequestHumanReview"

vi.mock("@/components/SlackChannelCombobox", () => ({
  SlackChannelCombobox: ({
    onValueChange,
  }: {
    onValueChange: (value: string) => void
  }) => (
    <button onClick={() => onValueChange("C0123456789")}>Choose channel</button>
  ),
}))
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
  ci: "passing",
  failingChecks: ["lint"],
  pendingChecks: [],
  missingChecks: [],
  unresolvedThreads: 1,
}

it("requires a chosen channel when the repository has no review destination", async () => {
  vi.spyOn(api, "humanReviewAvailability").mockResolvedValue({
    available: false,
    blockers: [],
  })
  const request = vi.spyOn(api, "requestHumanReview").mockResolvedValue({
    success: true,
    reused: false,
    permalink: "",
    error: "",
    request_id: "request",
    channel: "C0123456789",
  })
  render(
    <QueryClientProvider client={new QueryClient()}>
      <RequestHumanReview pr={pr} />
    </QueryClientProvider>
  )
  await screen.findByRole("button", { name: "Choose channel" })
  const button = screen.getByRole("button", { name: "Request review in Slack" })
  expect(button.hasAttribute("disabled")).toBe(true)
  fireEvent.click(screen.getByRole("button", { name: "Choose channel" }))
  fireEvent.click(button)
  await waitFor(() => expect(request).toHaveBeenCalledWith(pr, "C0123456789"))
})
