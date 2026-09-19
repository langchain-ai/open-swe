// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"
import { api, ApiError, type ReviewRiskResponse } from "@/lib/api"
import { ReviewRiskCard } from "./ReviewRiskCard"

vi.mock("@tanstack/react-router", () => ({
  useLocation: () => ({
    hash: "review-risk-11111111-1111-1111-1111-111111111111",
  }),
}))

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

it("opens feedback immediately while keeping policy evidence collapsed", async () => {
  vi.spyOn(api, "getReviewRisk").mockResolvedValue({
    assessment: {
      ...response.assessment!,
      approval_evaluation: {
        mode: "shadow",
        decision: "needs_human_review",
        evaluated_at: "2026-09-18T12:00:00Z",
        policy: {
          source: "repository",
          version: "0123456789ababcdef",
          base_sha: "b".repeat(40),
        },
        criteria: [
          {
            id: "paths",
            title: "Human review paths",
            status: "fail",
            evidence: "auth/session.py requires an owner.",
            source: "code",
          },
        ],
      },
    },
    feedback: null,
  })
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <ReviewRiskCard
        owner="org"
        repo="repo"
        number={7}
        headSha={"a".repeat(40)}
      />
    </QueryClientProvider>
  )
  await screen.findByText("Approval: Needs human review")
  expect(screen.getByText(/historical repository policy/)).toBeTruthy()
  expect(screen.getByText("0123456789ab")).toBeTruthy()
  expect(screen.getByText("auth/session.py requires an owner.")).toBeTruthy()
  expect(
    screen.getByText("auth/session.py requires an owner.").closest("details")
      ?.open
  ).toBe(false)
  expect(
    screen.getByRole("button", { name: "Needs human review" })
  ).toBeTruthy()
  expect(
    screen.getByText("What should the approval decision have been?")
  ).toBeTruthy()
})

it("offers sign-in when the session has expired", async () => {
  vi.spyOn(api, "getReviewRisk").mockRejectedValue(
    new ApiError(401, "Not authenticated")
  )
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <ReviewRiskCard
        owner="org"
        repo="repo"
        number={7}
        headSha={"a".repeat(40)}
      />
    </QueryClientProvider>
  )
  const login = await screen.findByRole("link", { name: "Sign in with GitHub" })
  expect(login.getAttribute("href")).toContain("/dashboard/api/auth/login")
})

it("lets the reader retry a failed assessment load", async () => {
  vi.spyOn(api, "getReviewRisk")
    .mockRejectedValueOnce(new ApiError(503, "Unavailable"))
    .mockResolvedValue(response)
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <ReviewRiskCard
        owner="org"
        repo="repo"
        number={7}
        headSha={"a".repeat(40)}
      />
    </QueryClientProvider>
  )
  fireEvent.click(await screen.findByRole("button", { name: "Try again" }))
  expect(
    await screen.findByRole("button", { name: "Needs human review" })
  ).toBeTruthy()
  expect(screen.queryByRole("alert")).toBeNull()
})

const response: ReviewRiskResponse = {
  assessment: {
    id: "11111111-1111-1111-1111-111111111111",
    head_sha: "a".repeat(40),
    score: 2,
    proposed_score: 2,
    confidence: "high",
    rationale: "Localized change.",
    limitations: [],
    open_findings: 0,
  },
  feedback: null,
}

it("opens the linked historical assessment without voting, then submits explicit feedback for that assessment", async () => {
  const read = vi.spyOn(api, "getReviewRisk").mockResolvedValue(response)
  const submit = vi.spyOn(api, "submitReviewRiskFeedback").mockResolvedValue({
    assessment_id: response.assessment!.id,
    decision: "needs_review",
    comment: "Shared contract",
  })
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <ReviewRiskCard
        owner="org"
        repo="repo"
        number={7}
        headSha={"b".repeat(40)}
      />
    </QueryClientProvider>
  )
  await screen.findByText("Localized change.")
  expect(read).toHaveBeenCalledWith("org", "repo", 7, response.assessment!.id)
  expect(submit).not.toHaveBeenCalled()
  expect(screen.getByText(/earlier commit/)).toBeTruthy()
  fireEvent.click(screen.getByRole("button", { name: "Needs human review" }))
  fireEvent.change(screen.getByLabelText("Why? (optional)"), {
    target: { value: "Shared contract" },
  })
  fireEvent.click(screen.getByRole("button", { name: "Save feedback" }))
  const saved = await screen.findByText("Feedback saved. Thank you.")
  const details = saved.closest("details")!
  expect(details.open).toBe(false)
  const summary = details.querySelector("summary")!
  expect(summary.contains(saved)).toBe(true)
  expect(document.activeElement).toBe(summary)
  fireEvent.click(summary)
  await waitFor(() => expect(details.open).toBe(true))
  expect(screen.getByDisplayValue("Shared contract")).toBeTruthy()
  expect(submit).toHaveBeenCalledWith(
    "org",
    "repo",
    7,
    response.assessment!.id,
    {
      decision: "needs_review",
      comment: "Shared contract",
    }
  )
})

it("keeps feedback editable after a failed save", async () => {
  vi.spyOn(api, "getReviewRisk").mockResolvedValue(response)
  vi.spyOn(api, "submitReviewRiskFeedback").mockRejectedValue(
    new Error("Offline")
  )
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <ReviewRiskCard
        owner="org"
        repo="repo"
        number={7}
        headSha={"a".repeat(40)}
      />
    </QueryClientProvider>
  )
  await screen.findByText("Localized change.")
  fireEvent.click(screen.getByRole("button", { name: "Unsure" }))
  fireEvent.click(screen.getByRole("button", { name: "Save feedback" }))
  await waitFor(() =>
    expect(screen.getByRole("alert").textContent).toContain("Could not save")
  )
  expect(screen.getByRole("alert").closest("details")?.open).toBe(true)
  expect(
    screen
      .getByRole("button", { name: "Save feedback" })
      .hasAttribute("disabled")
  ).toBe(false)
})

it("preserves the draft when authentication expires during a save and refresh", async () => {
  const read = vi.spyOn(api, "getReviewRisk").mockResolvedValue(response)
  vi.spyOn(api, "submitReviewRiskFeedback").mockRejectedValue(
    new ApiError(401, "Expired")
  )
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <ReviewRiskCard
        owner="org"
        repo="repo"
        number={7}
        headSha={"a".repeat(40)}
      />
    </QueryClientProvider>
  )
  fireEvent.click(
    await screen.findByRole("button", { name: "Needs human review" })
  )
  fireEvent.change(screen.getByLabelText("Why? (optional)"), {
    target: { value: "Keep this draft" },
  })
  fireEvent.click(screen.getByRole("button", { name: "Save feedback" }))
  const login = await screen.findByRole("link", { name: "Sign in with GitHub" })
  expect(login.getAttribute("target")).toBe("_blank")
  read.mockRejectedValue(new ApiError(401, "Expired"))
  await client.invalidateQueries({ queryKey: ["review-risk"] })
  expect(screen.getByDisplayValue("Keep this draft")).toBeTruthy()
  expect(
    screen
      .getByRole("button", { name: "Save feedback" })
      .hasAttribute("disabled")
  ).toBe(false)
})

it("shows native reaction feedback without inferring an approval vote, and accepts an explanation alone", async () => {
  vi.spyOn(api, "getReviewRisk").mockResolvedValue({
    ...response,
    assessment: { ...response.assessment!, github_review_id: 123 },
    reactions: {
      helpful: 2,
      unhelpful: 1,
      viewer_rating: "unhelpful",
      synced_at: "2026-09-19T00:00:00Z",
    },
  })
  const submit = vi.spyOn(api, "submitReviewRiskFeedback").mockResolvedValue({
    assessment_id: response.assessment!.id,
    decision: null,
    comment: "Risk is overstated",
  })
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <ReviewRiskCard
        owner="org"
        repo="repo"
        number={7}
        headSha={"a".repeat(40)}
      />
    </QueryClientProvider>
  )
  const link = await screen.findByRole("link", { name: /React on GitHub/ })
  expect(link.getAttribute("href")).toBe(
    "https://github.com/org/repo/pull/7#pullrequestreview-123"
  )
  expect(screen.getByText("Your GitHub feedback: 👎 unhelpful.")).toBeTruthy()
  expect(
    screen
      .getAllByRole("button")
      .filter((button) => button.getAttribute("aria-pressed") === "true")
  ).toHaveLength(0)
  fireEvent.change(screen.getByLabelText("Why? (optional)"), {
    target: { value: "Risk is overstated" },
  })
  fireEvent.click(screen.getByRole("button", { name: "Save feedback" }))
  await screen.findByText("Feedback saved. Thank you.")
  expect(submit).toHaveBeenCalledWith(
    "org",
    "repo",
    7,
    response.assessment!.id,
    { decision: null, comment: "Risk is overstated" }
  )
})
