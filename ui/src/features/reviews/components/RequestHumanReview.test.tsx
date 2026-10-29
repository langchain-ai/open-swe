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

it.each([
  "",
  "https://workspace.slack.com/archives/C0123456789/p1234567890123456",
])(
  "silently reuses an existing request returned after choosing a channel with permalink %j",
  async (permalink) => {
    const successToast = vi.spyOn(toast, "success")
    const open = vi.spyOn(window, "open").mockReturnValue(null)
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
    const requested = await screen.findByRole("button", {
      name: "Review requested",
    })
    await waitFor(() => expect(requested.hasAttribute("disabled")).toBe(false))
    if (permalink) fireEvent.click(requested)
    expect(open.mock.calls).toEqual(
      permalink ? [[permalink, "_blank", "noopener,noreferrer"]] : []
    )
    expect(request).toHaveBeenCalledTimes(1)
    expect(successToast).not.toHaveBeenCalled()
    expect(screen.queryByRole("button", { name: "Choose channel" })).toBeNull()
  }
)

it.each([
  "",
  "https://workspace.slack.com/archives/C0123456789/p1234567890123456",
])(
  "reuses persisted status and permalink %j on remount and focus",
  async (permalink) => {
    const successToast = vi.spyOn(toast, "success")
    const open = vi.spyOn(window, "open").mockReturnValue(null)
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
          <RequestHumanReview pr={pr} />
        </TooltipProvider>
      </QueryClientProvider>
    )
    const view = render(content)
    expect(
      screen
        .getByRole("button", { name: "Request review in Slack" })
        .hasAttribute("disabled")
    ).toBe(true)
    await act(async () =>
      result.resolve({
        available: false,
        active: true,
        permalink,
        blockers: [],
      })
    )
    const requested = await screen.findByRole("button", {
      name: "Review requested",
    })
    expect(requested.hasAttribute("disabled")).toBe(false)
    expect(screen.queryByRole("button", { name: "Choose channel" })).toBeNull()
    view.unmount()
    render(content)
    await act(async () => {
      focusManager.setFocused(false)
      focusManager.setFocused(true)
    })
    const restored = screen.getByRole("button", { name: "Review requested" })
    expect(restored.hasAttribute("disabled")).toBe(false)
    if (permalink) fireEvent.click(restored)
    expect(open.mock.calls).toEqual(
      permalink ? [[permalink, "_blank", "noopener,noreferrer"]] : []
    )
    expect(request).not.toHaveBeenCalled()
    expect(successToast).not.toHaveBeenCalled()
    expect(availability).toHaveBeenCalledTimes(1)
  }
)

it.each(["loading", "error", "blocked", "approved"])(
  "requires a known default or selected channel when availability is %s",
  async (state) => {
    const current = {
      ...pr,
      reviewDecision:
        state === "approved" ? ("approved" as const) : pr.reviewDecision,
    }
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
      await screen.findByRole("button", { name: "Retry review status" })
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

it("recovers channel selection after availability fails", async () => {
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
  const retry = await screen.findByRole("button", {
    name: "Retry review status",
  })
  fireEvent.click(screen.getByRole("button", { name: "Choose channel" }))
  fireEvent.click(retry)
  await waitFor(() => expect(availability).toHaveBeenCalledTimes(2))
  await waitFor(() =>
    expect(
      screen.queryByRole("button", { name: "Retry review status" })
    ).toBeNull()
  )
  fireEvent.click(
    screen.getByRole("button", { name: "Request review in Slack" })
  )
  await screen.findByRole("button", { name: "Review requested" })
  expect(request).toHaveBeenCalledWith(pr, "C0123456789")
  expect(successToast).toHaveBeenCalledWith(
    "Asked #reviews to review acme/app#7",
    undefined
  )
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

it("does not offer a review request for a draft", () => {
  vi.spyOn(api, "humanReviewAvailability").mockReturnValue(
    new Promise(() => {})
  )
  render(
    <QueryClientProvider client={new QueryClient()}>
      <RequestHumanReview pr={{ ...pr, draft: true }} />
    </QueryClientProvider>
  )
  expect(screen.queryByRole("button")).toBeNull()
})
