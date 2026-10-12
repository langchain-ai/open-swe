/** @vitest-environment jsdom */
import {
  focusManager,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query"
import {
  act,
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
  focusManager.setFocused(undefined)
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
    active: false,
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
  const requested = await screen.findByRole("button", {
    name: "Review requested",
  })
  expect(requested.hasAttribute("disabled")).toBe(true)
  expect(screen.queryByRole("button", { name: "Choose channel" })).toBeNull()
})

it("keeps the action visible while loading and reuses persisted status on remount and focus", async () => {
  const result =
    Promise.withResolvers<
      Awaited<ReturnType<typeof api.humanReviewAvailability>>
    >()
  const availability = vi
    .spyOn(api, "humanReviewAvailability")
    .mockReturnValue(result.promise)
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  })
  const content = (
    <QueryClientProvider client={client}>
      <RequestHumanReview pr={pr} />
    </QueryClientProvider>
  )
  const view = render(content)
  expect(
    screen
      .getByRole("button", { name: "Request review in Slack" })
      .hasAttribute("disabled")
  ).toBe(true)
  await act(async () =>
    result.resolve({ available: false, active: true, blockers: [] })
  )
  const requested = await screen.findByRole("button", {
    name: "Review requested",
  })
  expect(requested.hasAttribute("disabled")).toBe(true)
  expect(screen.queryByRole("button", { name: "Choose channel" })).toBeNull()
  view.unmount()
  render(content)
  await act(async () => {
    focusManager.setFocused(false)
    focusManager.setFocused(true)
  })
  expect(
    screen
      .getByRole("button", { name: "Review requested" })
      .hasAttribute("disabled")
  ).toBe(true)
  expect(availability).toHaveBeenCalledTimes(1)
})
