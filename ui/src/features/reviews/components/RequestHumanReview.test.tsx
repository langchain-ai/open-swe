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
import { TooltipProvider } from "@langchain/macaw-components/Tooltip"
import { afterEach, expect, it, vi } from "vitest"
import { toast } from "sonner"
import { api, type OpenPullRequest } from "@/lib/api"
import { InvalidationTab } from "@/lib/invalidations/client"
import { pullRequestTopic } from "../lib/cache"
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
  ci: "passing",
  failingChecks: ["lint"],
  pendingChecks: [],
  requiredChecksPassing: true,
  missingChecks: [],
  unresolvedThreads: 1,
  conversationResolutionRequired: false,
}

function expectOpensInSlack(control: HTMLElement, permalink: string) {
  expect({
    href: control.getAttribute("href"),
    target: control.getAttribute("target"),
    disabled: control.hasAttribute("disabled"),
  }).toEqual(
    permalink
      ? { href: permalink, target: "_blank", disabled: false }
      : { href: null, target: null, disabled: true }
  )
}

it.each([
  "",
  "https://workspace.slack.com/archives/C0123456789/p1234567890123456",
])(
  "silently reuses an existing request returned after choosing a channel with permalink %j",
  async (permalink) => {
    const successToast = vi.spyOn(toast, "success")
    vi.spyOn(api, "humanReviewAvailability").mockResolvedValue({
      available: false,
      active: false,
      permalink: "",
      blockers: [],
    })
    const result =
      Promise.withResolvers<
        Awaited<ReturnType<typeof api.requestHumanReview>>
      >()
    const request = vi
      .spyOn(api, "requestHumanReview")
      .mockReturnValue(result.promise)
    render(
      <QueryClientProvider client={new QueryClient()}>
        <TooltipProvider>
          <RequestHumanReview pr={pr} />
        </TooltipProvider>
      </QueryClientProvider>
    )
    await screen.findByRole("button", { name: "Choose channel" })
    const button = screen.getByRole("button", {
      name: "Request review in Slack",
    })
    expect(button.hasAttribute("disabled")).toBe(true)
    fireEvent.click(button)
    expect(request).not.toHaveBeenCalled()
    fireEvent.click(
      screen.getByRole("button", { name: "Why a review can't be requested" })
    )
    expect(
      await screen.findByText(
        "No default Slack review channel is configured. Choose a review channel manually to enable this button."
      )
    ).toBeTruthy()
    fireEvent.click(screen.getByRole("button", { name: "Choose channel" }))
    expect(button.hasAttribute("disabled")).toBe(false)
    fireEvent.click(button)
    const requesting = await screen.findByRole("button", {
      name: "Requesting…",
    })
    expect(requesting.hasAttribute("disabled")).toBe(false)
    fireEvent.click(requesting)
    expect(request).toHaveBeenCalledTimes(1)
    expect(request).toHaveBeenCalledWith(pr, "C0123456789")
    expect(
      screen.queryByRole("button", { name: "Review requested" })
    ).toBeNull()
    await act(async () =>
      result.resolve({
        success: true,
        reused: true,
        permalink,
        error: "",
        request_id: "request",
        channel: "C0123456789",
        channel_name: "reviews",
      })
    )
    expectOpensInSlack(
      await screen.findByRole(permalink ? "link" : "button", {
        name: "Review requested",
      }),
      permalink
    )
    expect(request).toHaveBeenCalledTimes(1)
    expect(successToast).not.toHaveBeenCalled()
    expect(screen.queryByRole("button", { name: "Choose channel" })).toBeNull()
  }
)

it.each([
  { draft: false, permalink: "" },
  { draft: true, permalink: "" },
  ...[false, true].map((draft) => ({
    draft,
    permalink:
      "https://workspace.slack.com/archives/C0123456789/p1234567890123456",
  })),
])(
  "reuses persisted status $permalink with draft=$draft on remount and focus",
  async ({ draft, permalink }) => {
    const successToast = vi.spyOn(toast, "success")
    const request = vi.spyOn(api, "requestHumanReview")
    const result =
      Promise.withResolvers<
        Awaited<ReturnType<typeof api.humanReviewAvailability>>
      >()
    const availability = vi
      .spyOn(api, "humanReviewAvailability")
      .mockReturnValue(result.promise)
    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false, refetchOnWindowFocus: false },
      },
    })
    const content = (
      <QueryClientProvider client={client}>
        <TooltipProvider>
          <RequestHumanReview pr={{ ...pr, draft }} />
        </TooltipProvider>
      </QueryClientProvider>
    )
    const view = render(content)
    expect(
      screen
        .queryByRole("button", { name: "Request review in Slack" })
        ?.hasAttribute("disabled")
    ).toBe(draft ? undefined : true)
    await act(async () =>
      result.resolve({
        available: false,
        active: true,
        permalink,
        blockers: [],
      })
    )
    const role = permalink ? "link" : "button"
    expectOpensInSlack(
      await screen.findByRole(role, { name: "Review requested" }),
      permalink
    )
    expect(screen.queryByRole("button", { name: "Choose channel" })).toBeNull()
    view.unmount()
    render(content)
    await act(async () => {
      focusManager.setFocused(false)
      focusManager.setFocused(true)
    })
    expectOpensInSlack(
      screen.getByRole(role, { name: "Review requested" }),
      permalink
    )
    expect(request).not.toHaveBeenCalled()
    expect(successToast).not.toHaveBeenCalled()
    expect(availability).toHaveBeenCalledTimes(2)
  }
)

it.each(["loading", "error", "blocked", "additional approval required"])(
  "requires a known default or selected channel when availability is %s",
  async (state) => {
    const current: OpenPullRequest =
      state === "additional approval required"
        ? { ...pr, reviewDecision: "approved", reviewRequired: true }
        : pr
    const availability = vi.spyOn(api, "humanReviewAvailability")
    if (state === "loading") availability.mockReturnValue(new Promise(() => {}))
    else if (state === "error")
      availability.mockRejectedValue(new Error("Unavailable"))
    else
      availability.mockResolvedValue({
        available: true,
        active: false,
        permalink: "",
        blockers: ["it has merge conflicts"],
      })
    const request = vi
      .spyOn(api, "requestHumanReview")
      .mockReturnValue(new Promise(() => {}))
    render(
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <TooltipProvider>
          <RequestHumanReview pr={current} />
        </TooltipProvider>
      </QueryClientProvider>
    )
    const button = screen.getByRole("button", {
      name: "Request review in Slack",
    })
    if (state === "error")
      await screen.findByRole("button", { name: "Choose channel" })
    const needsChannel = state === "loading" || state === "error"
    await waitFor(() =>
      expect(button.hasAttribute("disabled")).toBe(needsChannel)
    )
    fireEvent.click(button)
    await waitFor(() =>
      expect(request.mock.calls).toEqual(needsChannel ? [] : [[current, ""]])
    )
  }
)

it.each([
  "",
  "https://workspace.slack.com/archives/C0123456789/p1234567890123456",
])(
  "can request another review after a Slack dismissal with cached link %j",
  async (permalink) => {
    const errorToast = vi.spyOn(toast, "error")
    vi.spyOn(api, "humanReviewAvailability")
      .mockResolvedValueOnce({
        available: true,
        active: true,
        permalink,
        blockers: [],
      })
      .mockResolvedValue({
        available: true,
        active: false,
        permalink: "",
        blockers: [],
      })
    const request = vi.spyOn(api, "requestHumanReview").mockResolvedValue({
      success: true,
      reused: false,
      permalink:
        "https://workspace.slack.com/archives/C0123456789/p2234567890123456",
      error: "",
      request_id: "replacement",
      channel: "C0123456789",
      channel_name: "reviews",
    })
    class ReviewEvents extends EventTarget {
      static sources: ReviewEvents[] = []
      constructor() {
        super()
        ReviewEvents.sources.push(this)
      }
      close() {}
    }
    vi.stubGlobal("EventSource", ReviewEvents)
    const client = new QueryClient()
    const tab = new InvalidationTab(client, () => {})
    tab.start()
    try {
      render(
        <QueryClientProvider client={client}>
          <TooltipProvider>
            <RequestHumanReview pr={pr} />
          </TooltipProvider>
        </QueryClientProvider>
      )
      await screen.findByRole(permalink ? "link" : "button", {
        name: "Review requested",
      })
      expect(
        screen.queryByRole("button", { name: "Retry review status" })
      ).toBeNull()
      await waitFor(() =>
        expect(ReviewEvents.sources.length).toBeGreaterThan(0)
      )
      act(() => {
        ReviewEvents.sources.at(-1)!.dispatchEvent(
          new MessageEvent("hello", {
            data: JSON.stringify({
              invalidated: [],
              denied: [],
              backend_commit: null,
            }),
          })
        )
        ReviewEvents.sources.at(-1)!.dispatchEvent(
          new MessageEvent("invalidated", {
            data: JSON.stringify({ topics: [pullRequestTopic(pr)] }),
          })
        )
      })
      fireEvent.click(
        await screen.findByRole("button", { name: "Request review in Slack" })
      )
      // The replacement request returns a card link, so the control opens Slack.
      await screen.findByRole("link", { name: "Review requested" })
      expect(request).toHaveBeenCalledWith(pr, "")
      expect(errorToast).not.toHaveBeenCalled()
    } finally {
      tab.stop()
    }
  }
)

it("keeps the channel picker mounted when live PR status changes", async () => {
  const availability = vi
    .spyOn(api, "humanReviewAvailability")
    .mockResolvedValueOnce({
      available: false,
      active: false,
      permalink: "",
      blockers: [],
    })
    .mockReturnValue(new Promise(() => {}))
  const client = new QueryClient()
  const view = render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <RequestHumanReview pr={pr} />
      </TooltipProvider>
    </QueryClientProvider>
  )
  const picker = await screen.findByRole("button", { name: "Choose channel" })
  picker.focus()
  view.rerender(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <RequestHumanReview
          pr={{ ...pr, ci: "pending", updatedAt: "2026-11-01T12:00:00Z" }}
        />
      </TooltipProvider>
    </QueryClientProvider>
  )
  expect(screen.queryByRole("button", { name: "Choose channel" })).toBe(picker)
  expect(document.activeElement).toBe(picker)
  expect(availability).toHaveBeenCalledTimes(1)
})

it("recovers channel selection when availability is invalidated", async () => {
  const successToast = vi.spyOn(toast, "success")
  const availability = vi
    .spyOn(api, "humanReviewAvailability")
    .mockRejectedValueOnce(new Error("Unavailable"))
    .mockResolvedValue({
      available: false,
      active: false,
      permalink: "",
      blockers: [],
    })
  const request = vi.spyOn(api, "requestHumanReview").mockResolvedValue({
    success: true,
    reused: false,
    permalink: "",
    error: "",
    request_id: "request",
    channel: "C0123456789",
    channel_name: "reviews",
  })
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <RequestHumanReview pr={pr} />
      </TooltipProvider>
    </QueryClientProvider>
  )
  await screen.findByRole("button", { name: "Choose channel" })
  fireEvent.click(screen.getByRole("button", { name: "Choose channel" }))
  await act(() =>
    client.invalidateQueries({
      queryKey: ["human-review-availability", pr.repo, pr.number],
    })
  )
  await waitFor(() => expect(availability).toHaveBeenCalledTimes(2))
  fireEvent.click(
    screen.getByRole("button", { name: "Request review in Slack" })
  )
  await screen.findByRole("button", { name: "Review requested" })
  await act(() =>
    client.invalidateQueries({
      queryKey: ["human-review-availability", pr.repo, pr.number],
    })
  )
  await screen.findByRole("button", { name: "Choose channel" })
  expect(request).toHaveBeenCalledWith(pr, "C0123456789")
  expect(successToast).toHaveBeenCalledWith(
    "Asked #reviews to review acme/app#7",
    undefined
  )
})

it("recovers a failed default-channel lookup when the window regains focus", async () => {
  const availability = vi
    .spyOn(api, "humanReviewAvailability")
    .mockRejectedValueOnce(new Error("Unavailable"))
    .mockResolvedValue({
      available: true,
      active: false,
      permalink: "",
      blockers: [],
    })
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <RequestHumanReview pr={pr} />
      </TooltipProvider>
    </QueryClientProvider>
  )
  await screen.findByRole("button", { name: "Choose channel" })
  const button = screen.getByRole("button", {
    name: "Request review in Slack",
  })
  expect(button.hasAttribute("disabled")).toBe(true)
  act(() => {
    focusManager.setFocused(false)
    focusManager.setFocused(true)
  })
  await waitFor(() => expect(button.hasAttribute("disabled")).toBe(false))
  expect(availability).toHaveBeenCalledTimes(2)
})

it("can request a selected channel without recovering availability", async () => {
  vi.spyOn(api, "humanReviewAvailability").mockRejectedValue(
    new Error("Unavailable")
  )
  const request = vi
    .spyOn(api, "requestHumanReview")
    .mockReturnValue(new Promise(() => {}))
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <TooltipProvider>
        <RequestHumanReview pr={pr} />
      </TooltipProvider>
    </QueryClientProvider>
  )
  fireEvent.click(await screen.findByRole("button", { name: "Choose channel" }))
  fireEvent.click(
    screen.getByRole("button", { name: "Request review in Slack" })
  )
  await waitFor(() => expect(request).toHaveBeenCalledWith(pr, "C0123456789"))
})

it.each<Partial<OpenPullRequest>>([
  { draft: true },
  { reviewDecision: "approved" },
])("does not offer a new review request for %j", async (status) => {
  const availability = vi
    .spyOn(api, "humanReviewAvailability")
    .mockResolvedValue({
      available: true,
      active: false,
      permalink: "",
      blockers: [],
    })
  render(
    <QueryClientProvider client={new QueryClient()}>
      <RequestHumanReview pr={{ ...pr, ...status }} />
    </QueryClientProvider>
  )
  await waitFor(() =>
    expect(availability).toHaveBeenCalledTimes(status.draft ? 1 : 0)
  )
  expect(screen.queryByRole("button")).toBeNull()
})

it("removes the channel picker when the PR becomes approved", async () => {
  vi.spyOn(api, "humanReviewAvailability").mockResolvedValue({
    available: false,
    active: false,
    permalink: "",
    blockers: [],
  })
  const client = new QueryClient()
  const view = render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <RequestHumanReview pr={pr} />
      </TooltipProvider>
    </QueryClientProvider>
  )
  await screen.findByRole("button", { name: "Choose channel" })
  view.rerender(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <RequestHumanReview pr={{ ...pr, reviewDecision: "approved" }} />
      </TooltipProvider>
    </QueryClientProvider>
  )
  expect(screen.queryByRole("button")).toBeNull()
})
