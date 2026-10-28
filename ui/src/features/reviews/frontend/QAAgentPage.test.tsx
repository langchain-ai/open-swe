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

import { api, type OpenPullRequest } from "@/lib/api"
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
    scenarios: [],
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
  vi.spyOn(api, "openPullRequests").mockResolvedValue({
    pullRequests: [pr(7)],
    nextPage: null,
    incomplete: false,
    updatedAt: "2026-10-27T00:00:00Z",
  })
  vi.spyOn(frontendReviewApi, "list").mockResolvedValue({ runs: [] })
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

it("opens a saved comparison from a PR card and distinguishes older evidence", async () => {
  vi.mocked(frontendReviewApi.list).mockResolvedValue({
    runs: [{ ...saved, head_sha: "c".repeat(40) }],
  })
  const router = mount()
  await screen.findByText("Earlier commit")
  expect(
    screen.getByText("Saved evidence is for an earlier commit.")
  ).toBeTruthy()
  fireEvent.click(screen.getByRole("link", { name: /Refine navigation 7/ }))
  await screen.findByText("Frontend details")
  expect(router.state.location.pathname).toBe("/agents/reviews/acme/app/7")
  expect(router.state.location.search).toEqual({
    view: "frontend",
    run: saved.id,
  })
})

it("limits evidence requests to visible cards until more PRs are requested", async () => {
  vi.mocked(api.openPullRequests).mockResolvedValue({
    pullRequests: Array.from({ length: 14 }, (_, index) => pr(index + 1)),
    nextPage: null,
    incomplete: false,
    updatedAt: "2026-10-27T00:00:00Z",
  })
  mount()
  await waitFor(() => expect(frontendReviewApi.list).toHaveBeenCalledTimes(12))
  expect(screen.queryByText("Refine navigation 13")).toBeNull()
  fireEvent.click(screen.getByRole("button", { name: "Load more PRs" }))
  await screen.findByText("Refine navigation 14")
  await waitFor(() => expect(frontendReviewApi.list).toHaveBeenCalledTimes(14))
})

it("keeps an unavailable evidence card usable and opens pasted PRs directly in frontend QA", async () => {
  vi.mocked(frontendReviewApi.list).mockRejectedValue(
    new Error("Evidence service unavailable")
  )
  const router = mount()
  await screen.findByText("Evidence unavailable", {}, { timeout: 5000 })
  expect(screen.getByRole("link", { name: /Refine navigation 7/ })).toBeTruthy()
  const input = screen.getByRole("textbox", {
    name: "Open Design QA for a pull request",
  })
  fireEvent.change(input, {
    target: { value: "https://github.com/acme/app/pull/42" },
  })
  const form = input.closest("form")
  if (!form) throw new Error("PR input form is missing")
  fireEvent.submit(form)
  await screen.findByText("Frontend details")
  expect(router.state.location.pathname).toBe("/agents/reviews/acme/app/42")
  expect(router.state.location.search).toEqual({ view: "frontend" })
})
