/** @vitest-environment jsdom */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  RouterProvider,
} from "@tanstack/react-router"
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"
import { afterEach, beforeEach, expect, it, vi } from "vitest"

import { type OpenPullRequest } from "@/lib/api"
import { frontendReviewApi, type FrontendReviewRun } from "./api"
import { QAAgentPage } from "./QAAgentPage"

vi.mock("@/lib/session", () => ({
  useSession: () => ({ data: { login: "octocat" } }),
}))

const pr = (number: number): OpenPullRequest => ({
  repo: "acme/app",
  number,
  title: `Refine navigation ${number}`,
  state: "open",
  draft: false,
  additions: 4,
  deletions: 2,
  mergeable: true,
  mergeState: "clean",
  headSha: "b".repeat(40),
  headRef: "frontend",
  reviewDecision: "none",
  reviewers: [],
  reviewRequired: false,
  statusAvailable: true,
  createdAt: null,
  updatedAt: null,
  ci: "none",
  failingChecks: [],
  pendingChecks: [],
  missingChecks: [],
  unresolvedThreads: 0,
})

const saved: FrontendReviewRun = {
  id: "00000000-0000-0000-0000-000000000001",
  status: "complete",
  auth_mode: "none",
  base_url: "https://base.vercel.app",
  head_url: "https://head.vercel.app",
  paths: ["/"],
  base_sha: "a".repeat(40),
  head_sha: "b".repeat(40),
  created_at: "2026-10-27T00:00:00Z",
  updated_at: "2026-10-27T00:00:00Z",
  error: null,
  report: {
    summary: "The updated navigation has clearer spacing.",
    ai_review_status: "complete",
    build_verification: "unverified",
    video_status: "unavailable",
    scenarios: [
      {
        id: "hover",
        title: "Button hover",
        path: "/",
        status: "complete",
        before_url:
          "/dashboard/api/reviews/acme/app/7/frontend-reviews/run/media/before.png",
        after_url:
          "/dashboard/api/reviews/acme/app/7/frontend-reviews/run/media/after.png",
        diff_url: null,
        mask_url: null,
        before_video_url: null,
        after_video_url: null,
        width: 100,
        height: 100,
        metrics: {
          changed_pixels: 100,
          total_pixels: 10000,
          changed_ratio: 0.01,
          threshold: 18,
        },
        regions: [],
        mask_rects: [],
        tile_size: 32,
        min_region_pixels: 4,
        annotations: [],
        error: null,
        interaction: true,
      },
    ],
  },
}

function mount() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const root = createRootRoute({ component: Outlet })
  const overview = createRoute({
    getParentRoute: () => root,
    path: "/agents/qa",
    component: QAAgentPage,
  })
  const details = createRoute({
    getParentRoute: () => root,
    path: "/agents/reviews/$owner/$repo/$number",
    validateSearch: (search: Record<string, unknown>) => search,
    component: () => <p>Frontend details</p>,
  })
  const router = createRouter({
    routeTree: root.addChildren([overview, details]),
    history: createMemoryHistory({ initialEntries: ["/agents/qa"] }),
  })
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  )
  return router
}

beforeEach(() => {
  vi.spyOn(frontendReviewApi, "inbox").mockResolvedValue({
    items: [
      {
        pr: pr(7),
        run: saved,
        requested_reviewers: ["octocat"],
        requested_teams: ["acme/design"],
      },
    ],
    next_page: null,
    incomplete: false,
  })
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

it("opens a saved comparison from a PR card and distinguishes older evidence", async () => {
  vi.mocked(frontendReviewApi.inbox).mockResolvedValue({
    items: [
      {
        pr: pr(7),
        run: { ...saved, head_sha: "c".repeat(40) },
        requested_reviewers: ["octocat"],
        requested_teams: ["acme/design"],
      },
    ],
    next_page: null,
    incomplete: false,
  })
  const router = mount()
  await screen.findByText("Earlier commit")
  expect(
    screen.getByText("Saved evidence is for an earlier commit.")
  ).toBeTruthy()
  expect(screen.getByText("Small visual change")).toBeTruthy()
  expect(screen.getByText("1/1 states compared · 1 interaction")).toBeTruthy()
  fireEvent.click(screen.getByRole("link", { name: /Refine navigation 7/ }))
  await screen.findByText("Frontend details")
  expect(router.state.location.pathname).toBe("/agents/reviews/acme/app/7")
  expect(router.state.location.search).toEqual({
    view: "frontend",
    run: saved.id,
  })
})

it("does not classify unpaired capture as a small or unchanged visual result", async () => {
  if (!saved.report) throw new Error("Missing report fixture")
  vi.mocked(frontendReviewApi.inbox).mockResolvedValue({
    items: [
      {
        pr: pr(7),
        requested_reviewers: [],
        requested_teams: [],
        run: {
          ...saved,
          status: "failed",
          report: {
            ...saved.report,
            scenarios: saved.report.scenarios.map((scenario) => ({
              ...scenario,
              after_url: null,
            })),
          },
        },
      },
    ],
    next_page: null,
    incomplete: false,
  })
  mount()
  await screen.findByText("Capture failed")
  expect(screen.queryByText("Small visual change")).toBeNull()
  expect(screen.queryByText("No changes above threshold")).toBeNull()
  expect(screen.getByText("0/1 states compared · 0 interactions")).toBeTruthy()
})

it("continues past a page with no saved reviews and switches to authored PRs", async () => {
  vi.mocked(frontendReviewApi.inbox).mockResolvedValueOnce({
    items: [],
    next_page: 2,
    incomplete: false,
  })
  mount()
  await screen.findByText("No saved reviews in your loaded review requests")
  fireEvent.click(screen.getByRole("button", { name: "Load more PRs" }))
  await screen.findByText("Refine navigation 7")
  expect(frontendReviewApi.inbox).toHaveBeenCalledWith("review-requested", 2)
  expect(screen.getByText("Requested: you, @acme/design")).toBeTruthy()
  fireEvent.click(screen.getByRole("button", { name: "My PRs" }))
  await waitFor(() =>
    expect(frontendReviewApi.inbox).toHaveBeenCalledWith("mine", 1)
  )
})

it("keeps PR lookup available during inbox failure and opens a LangChainPlus number", async () => {
  vi.mocked(frontendReviewApi.inbox).mockRejectedValue(
    new Error("Evidence service unavailable")
  )
  const router = mount()
  await screen.findByRole("alert")
  const input = screen.getByRole("textbox", {
    name: "Open Design QA for a pull request",
  })
  fireEvent.change(input, {
    target: { value: "#43399" },
  })
  const form = input.closest("form")
  if (!form) throw new Error("PR input form is missing")
  fireEvent.submit(form)
  await screen.findByText("Frontend details")
  expect(router.state.location.pathname).toBe(
    "/agents/reviews/langchain-ai/langchainplus/43399"
  )
  expect(router.state.location.search).toEqual({ view: "frontend" })
})

it("opens a full reference in another repo and explains invalid PR input", async () => {
  const router = mount()
  const input = await screen.findByRole("textbox", {
    name: "Open Design QA for a pull request",
  })
  fireEvent.change(input, { target: { value: "not a PR" } })
  fireEvent.click(screen.getByRole("button", { name: "Open PR" }))
  await screen.findByRole("alert")
  fireEvent.change(input, {
    target: { value: "https://github.com/acme/app/pull/42" },
  })
  fireEvent.click(screen.getByRole("button", { name: "Open PR" }))
  await screen.findByText("Frontend details")
  expect(router.state.location.pathname).toBe("/agents/reviews/acme/app/42")
})
