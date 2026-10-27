/** @vitest-environment jsdom */
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, expect, it, vi } from "vitest"

import { FrontendReportView, FrontendReviewer } from "./FrontendReviewer"
import { frontendReviewApi, type FrontendReviewRun } from "./api"

const run: FrontendReviewRun = {
  id: "00000000-0000-0000-0000-000000000001",
  status: "blocked",
  auth_mode: "dummy",
  base_url: "https://base.example.com",
  head_url: "https://pr.example.com",
  paths: ["/projects", "/settings"],
  base_sha: "a".repeat(40),
  head_sha: "b".repeat(40),
  created_at: "2026-10-26T00:00:00Z",
  updated_at: "2026-10-26T00:00:00Z",
  error: "Dummy authentication is not configured.",
  report: {
    summary: "Authentication blocked",
    ai_review_status: "not_run",
    build_verification: "unverified",
    video_status: "unavailable",
    scenarios: [],
  },
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

it("does not present dummy authentication as captured coverage or AI review", () => {
  render(<FrontendReportView run={run} />)
  expect(screen.getByText("Coverage: 0/2 routes captured")).toBeTruthy()
  expect(screen.getByText("AI review: Not run")).toBeTruthy()
  expect(
    screen.getByText(/No preview was contacted and no page was reviewed/)
  ).toBeTruthy()
  expect(screen.queryByRole("img")).toBeNull()
})

it("does not count a scenario marked complete without its paired screenshots", () => {
  render(
    <FrontendReportView
      run={{
        ...run,
        status: "complete",
        auth_mode: "none",
        report: {
          ...run.report!,
          scenarios: [
            {
              id: "page",
              title: "Projects",
              path: "/projects",
              status: "complete",
              before_url: null,
              after_url: null,
              diff_url: null,
              mask_url: null,
              before_video_url: null,
              after_video_url: null,
              width: null,
              height: null,
              metrics: null,
              regions: [],
              annotations: [],
              mask_rects: [],
              tile_size: 32,
              min_region_pixels: 4,
              error: null,
            },
          ],
        },
      }}
    />
  )
  expect(screen.getByText("Coverage: 0/2 routes captured")).toBeTruthy()
  expect(screen.getByText(/does not count as visually reviewed/)).toBeTruthy()
})

it("warns when a saved report covers a different PR commit and retains the report", () => {
  const completed = {
    ...run,
    status: "complete" as const,
    auth_mode: "none" as const,
  }
  const { rerender } = render(
    <FrontendReportView run={completed} currentHeadSha={"c".repeat(40)} />
  )
  expect(screen.getByText("New PR changes have not been reviewed")).toBeTruthy()
  expect(screen.getByText(/The current PR commit is/)).toBeTruthy()
  expect(screen.getByText("Review evidence")).toBeTruthy()

  rerender(<FrontendReportView run={completed} currentHeadSha={run.head_sha} />)
  expect(screen.queryByText("New PR changes have not been reviewed")).toBeNull()
  expect(screen.getByText("Review evidence")).toBeTruthy()
})

it("keeps an in-flight start disabled after remount and never navigates from its unmounted form", async () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  vi.spyOn(frontendReviewApi, "list")
    .mockResolvedValueOnce({ runs: [] })
    .mockResolvedValue({ runs: [run] })
  vi.spyOn(frontendReviewApi, "get").mockResolvedValue(run)
  let resolveStart!: (value: FrontendReviewRun) => void
  const start = vi.spyOn(frontendReviewApi, "start").mockImplementation(
    () =>
      new Promise<FrontendReviewRun>((resolve) => {
        resolveStart = resolve
      })
  )
  const originalNavigation = vi.fn()
  const remountedNavigation = vi.fn()
  const pr = { owner: "acme", repo: "repo", number: 7 }
  const original = render(
    <QueryClientProvider client={client}>
      <FrontendReviewer pr={pr} onRunChange={originalNavigation} />
    </QueryClientProvider>
  )
  await screen.findByRole("button", { name: "Create dummy-auth review" })
  fireEvent.change(screen.getByRole("textbox", { name: "Base preview URL" }), {
    target: { value: "https://base.vercel.app" },
  })
  fireEvent.change(screen.getByRole("textbox", { name: "PR preview URL" }), {
    target: { value: "https://head.vercel.app" },
  })
  fireEvent.click(
    screen.getByRole("button", { name: "Create dummy-auth review" })
  )
  await waitFor(() => expect(start).toHaveBeenCalledTimes(1))
  original.unmount()
  render(
    <QueryClientProvider client={client}>
      <FrontendReviewer pr={pr} onRunChange={remountedNavigation} />
    </QueryClientProvider>
  )
  const pendingButton = await screen.findByRole("button", {
    name: "Starting frontend review…",
  })
  expect((pendingButton as HTMLButtonElement).disabled).toBe(true)
  fireEvent.click(pendingButton)
  expect(start).toHaveBeenCalledTimes(1)
  await act(async () => {
    resolveStart(run)
  })
  await screen.findByText("Authentication required")
  expect(originalNavigation).not.toHaveBeenCalled()
  expect(remountedNavigation).not.toHaveBeenCalled()
  expect(client.getQueryData(["frontend-review", "acme", "repo", 7])).toEqual({
    runs: [run],
  })
  client.clear()
})

it("refreshes terminal progress when the worker finishes before the enqueue response arrives", async () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const finished: FrontendReviewRun = {
    ...run,
    auth_mode: "none",
    report: null,
    error: "Browser provider network isolation is required",
  }
  vi.spyOn(frontendReviewApi, "list")
    .mockResolvedValueOnce({ runs: [] })
    .mockResolvedValue({ runs: [finished] })
  vi.spyOn(frontendReviewApi, "get").mockResolvedValue(finished)
  vi.spyOn(frontendReviewApi, "start").mockResolvedValue({
    ...finished,
    status: "pending",
    error: null,
  })
  render(
    <QueryClientProvider client={client}>
      <FrontendReviewer
        pr={{ owner: "acme", repo: "repo", number: 7 }}
        onRunChange={vi.fn()}
      />
    </QueryClientProvider>
  )
  await screen.findByRole("button", { name: "Create dummy-auth review" })
  fireEvent.change(screen.getByRole("textbox", { name: "Base preview URL" }), {
    target: { value: "https://base.vercel.app" },
  })
  fireEvent.change(screen.getByRole("textbox", { name: "PR preview URL" }), {
    target: { value: "https://head.vercel.app" },
  })
  fireEvent.click(screen.getByText("Public preview"))
  fireEvent.click(
    screen.getByRole("button", { name: "Check public preview capture" })
  )
  await screen.findByText("Capture blocked")
  expect(
    screen.getByText("Browser provider network isolation is required")
  ).toBeTruthy()
  expect(screen.queryByText("Frontend review queued…")).toBeNull()
  client.clear()
})
