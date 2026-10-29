/** @vitest-environment jsdom */
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, expect, it, vi } from "vitest"

import { FrontendReportView, FrontendReviewer } from "./FrontendReviewer"
import { useReviewPage } from "@/features/reviews/page/store"
import {
  frontendReviewApi,
  type FrontendReviewRun,
  type FrontendScenario,
} from "./api"

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
  vi.unstubAllGlobals()
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
  expect(screen.getByText(/has not been visually compared/)).toBeTruthy()
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
  expect(screen.getByText(/New PR changes have not been reviewed/)).toBeTruthy()
  expect(screen.getByText(/Build unverified/)).toBeTruthy()

  rerender(<FrontendReportView run={completed} currentHeadSha={run.head_sha} />)
  expect(screen.queryByText(/New PR changes have not been reviewed/)).toBeNull()
  expect(screen.getByText(/Build unverified/)).toBeTruthy()
})

it("shows all saved states together and preserves route queries in page links", () => {
  vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("fixture"))
  const media =
    "/dashboard/api/reviews/acme/app/7/frontend-reviews/run/media/state.png"
  const scenarios: FrontendScenario[] = Array.from({ length: 6 }, (_, i) => ({
    id: `state-${i}`,
    title: `State ${i}`,
    path: `/projects/42?tab=traces&filter=success`,
    status: "complete",
    before_url: media,
    after_url: media,
    diff_url: null,
    mask_url: null,
    before_video_url: null,
    after_video_url: null,
    width: 100,
    height: 100,
    metrics: null,
    regions: [],
    mask_rects: [],
    annotations: [],
    tile_size: 24,
    min_region_pixels: 4,
    error: null,
    interaction: i > 1,
  }))
  render(
    <FrontendReportView
      pr={{ owner: "langchain-ai", repo: "langchainplus", number: 7 }}
      run={{
        ...run,
        status: "complete",
        error: null,
        base_url: "https://smith.langchain.com",
        report: { ...run.report!, scenarios },
      }}
    />
  )
  for (const scenario of scenarios) {
    const card = within(screen.getByRole("region", { name: scenario.title }))
    expect(
      card.getByRole("link", { name: "Production" }).getAttribute("href")
    ).toBe("https://smith.langchain.com/projects/42?tab=traces&filter=success")
    expect(
      card.getByRole("link", { name: "Preview" }).getAttribute("href")
    ).toBe("https://pr.example.com/projects/42?tab=traces&filter=success")
    expect(card.queryByRole("link", { name: "Base" })).toBeNull()
  }
})

it("links recap notes to their screenshot region and clears the selection", async () => {
  vi.stubGlobal(
    "Image",
    class {
      naturalWidth = 100
      naturalHeight = 100
      src = ""
      async decode() {}
    }
  )
  vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:fixture")
  vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {})
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null)
  vi.spyOn(globalThis, "fetch").mockImplementation(
    async () => new Response(new Blob(), { status: 200 })
  )
  const media = "/dashboard/api/reviews/acme/app/7/frontend-reviews/run/media/"
  const note = {
    title: "Clipped label",
    body: "The updated button cuts off its label.",
    x: 20,
    y: 30,
    width: 40,
    height: 10,
  }
  const scenario: FrontendScenario = {
    id: "button",
    title: "Button hover",
    path: "/",
    status: "complete",
    before_url: `${media}before.png`,
    after_url: `${media}after.png`,
    diff_url: null,
    mask_url: null,
    before_video_url: null,
    after_video_url: null,
    width: 100,
    height: 100,
    metrics: null,
    regions: [],
    mask_rects: [],
    tile_size: 32,
    min_region_pixels: 4,
    annotations: [note],
    error: null,
  }
  render(
    <FrontendReportView
      run={{
        ...run,
        status: "complete",
        error: null,
        report: {
          ...run.report!,
          ai_review_status: "complete",
          scenarios: [
            scenario,
            { ...scenario, id: "other", title: "Other state" },
          ],
        },
      }}
    />
  )
  const card = within(screen.getByRole("region", { name: scenario.title }))
  await card.findByRole("region", { name: "Visual comparison" })
  expect(card.queryByRole("img", { name: /Clipped label/ })).toBeNull()
  fireEvent.click(
    screen.getAllByRole("button", { name: "Inspect Clipped label" })[0]!
  )
  expect(
    screen
      .getAllByRole("button", { name: "Inspect Clipped label" })[0]!
      .getAttribute("aria-pressed")
  ).toBe("true")
  expect(
    card
      .getAllByRole("status")
      .some((item) => item.textContent?.includes(note.body))
  ).toBe(true)
  const marker = card.getByRole("img", {
    name: "After screenshot: Clipped label",
  })
  expect(marker.getAttribute("style")).toContain(
    "left: 20%; top: 30%; width: 40%; height: 10%"
  )
  expect(
    card.getByRole("img", { name: "Before screenshot: Clipped label" })
  ).toBeTruthy()
  const other = within(screen.getByRole("region", { name: "Other state" }))
  expect(other.queryByRole("img", { name: /Clipped label/ })).toBeNull()
  fireEvent.click(card.getByRole("button", { name: "Clear highlight" }))
  expect(card.queryByRole("img", { name: /Clipped label/ })).toBeNull()
})

it("shows capture as unavailable without offering a blocked start", async () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  vi.spyOn(frontendReviewApi, "list").mockResolvedValue({ runs: [] })
  render(
    <QueryClientProvider client={client}>
      <FrontendReviewer
        pr={{ owner: "acme", repo: "repo", number: 7 }}
        onRunChange={vi.fn()}
      />
    </QueryClientProvider>
  )
  expect(await screen.findByText(/Live capture is unavailable/)).toBeTruthy()
  expect(screen.queryByRole("textbox")).toBeNull()
  expect(screen.queryByRole("button")).toBeNull()
  client.clear()
})

it("attaches the selected interaction's available image and replay context without claiming a paired comparison", async () => {
  const fetch = vi
    .spyOn(globalThis, "fetch")
    .mockRejectedValue(new Error("fixture image"))
  useReviewPage.setState({
    chatDraft: undefined,
    railOpen: false,
    railTab: "discussion",
  })
  const media =
    "/dashboard/api/reviews/acme/widgets/7/frontend-reviews/00000000-0000-0000-0000-000000000001/media/state.png"
  const baseline: FrontendScenario = {
    id: "route",
    title: "Projects",
    path: "/projects",
    status: "complete",
    before_url: media.replace("state.png", "base.png"),
    after_url: media,
    diff_url: media,
    mask_url: media,
    before_video_url: null,
    after_video_url: null,
    width: 100,
    height: 100,
    metrics: {
      changed_pixels: 0,
      total_pixels: 10000,
      changed_ratio: 0,
      threshold: 18,
    },
    regions: [],
    mask_rects: [],
    annotations: [],
    tile_size: 32,
    min_region_pixels: 4,
    error: null,
  }
  const { unmount } = render(
    <FrontendReportView
      currentHeadSha={"c".repeat(40)}
      run={{
        ...run,
        status: "complete",
        auth_mode: "none",
        paths: ["/projects"],
        error: null,
        report: {
          ...run.report!,
          scenarios: [
            baseline,
            {
              ...baseline,
              id: "dialog",
              title: "Create dialog · Filled",
              interaction: true,
              status: "blocked",
              before_url: null,
              metrics: null,
              before_error: "Control missing on base",
              actions: ["click #create", "type #name"],
              layout_warnings: ["PR: Dialog extends beyond viewport"],
            },
          ],
        },
      }}
    />
  )
  expect(screen.getByText("Coverage: 1/1 routes captured")).toBeTruthy()
  expect(screen.getByText("Interactions: 0/1 states paired")).toBeTruthy()
  expect(screen.getByText("Base: Control missing on base")).toBeTruthy()
  expect(screen.getByAltText("PR interaction state")).toBeTruthy()
  expect(
    screen.getByText("PR state only · No pixel comparison available")
  ).toBeTruthy()
  expect(screen.getByText("PR: Dialog extends beyond viewport")).toBeTruthy()
  expect(screen.queryByRole("region", { name: "Visual comparison" })).toBeNull()

  fetch.mockResolvedValue(
    Object.assign(new Response(), {
      blob: async () => new Blob(["pr-pixels"], { type: "image/png" }),
    })
  )
  fireEvent.click(
    within(
      screen.getByRole("region", { name: "Create dialog · Filled" })
    ).getByRole("button", { name: "Ask about this change" })
  )
  await waitFor(() =>
    expect(useReviewPage.getState().chatDraft?.images).toEqual([
      {
        kind: "image",
        mimeType: "image/png",
        base64: btoa("pr-pixels"),
        fileName: `design-qa-${run.id}-dialog-pr.png`,
      },
    ])
  )
  const { chatDraft, railOpen, railTab } = useReviewPage.getState()
  expect(chatDraft!.text).toContain(run.id)
  expect(chatDraft!.text).toContain(`PR commit: ${run.head_sha}`)
  expect(chatDraft!.text).toContain("Stale capture:")
  expect(chatDraft!.text).toContain("Base screenshot: not captured.")
  expect(chatDraft!.text).toContain("Control missing on base")
  expect(chatDraft!.text).toContain("click #create\n2. type #name")
  expect(chatDraft!.text).toContain("PR: Dialog extends beyond viewport")
  expect(railOpen).toBe(true)
  expect(railTab).toBe("chat")

  fetch.mockImplementation(async (input) =>
    Object.assign(new Response(), {
      blob: async () =>
        new Blob(
          [String(input).endsWith("base.png") ? "base-pixels" : "pr-pixels"],
          { type: "image/png" }
        ),
    })
  )
  fireEvent.click(
    within(screen.getByRole("region", { name: "Projects" })).getByRole(
      "button",
      { name: "Ask about this change" }
    )
  )
  await waitFor(() =>
    expect(
      useReviewPage
        .getState()
        .chatDraft?.images.map(({ base64 }) => atob(base64))
    ).toEqual(["base-pixels", "pr-pixels"])
  )
  const pairedDraft = useReviewPage.getState().chatDraft

  // An expired protected image must not replace a draft with a partial pair.
  fetch.mockImplementation(async (input) =>
    String(input).endsWith("state.png")
      ? new Response(null, { status: 403 })
      : new Response(null, { status: 404 })
  )
  fireEvent.click(
    within(screen.getByRole("region", { name: "Projects" })).getByRole(
      "button",
      { name: "Ask about this change" }
    )
  )
  await waitFor(() =>
    expect(
      (
        within(screen.getByRole("region", { name: "Projects" })).getByRole(
          "button",
          {
            name: "Ask about this change",
          }
        ) as HTMLButtonElement
      ).disabled
    ).toBe(false)
  )
  expect(useReviewPage.getState().chatDraft).toBe(pairedDraft)

  // Navigating away during an attachment fetch cannot inject it into another PR.
  const finish: Array<(response: Response) => void> = []
  fetch.mockImplementation(
    () =>
      new Promise<Response>((resolve) => {
        finish.push(resolve)
      })
  )
  fireEvent.click(
    within(screen.getByRole("region", { name: "Projects" })).getByRole(
      "button",
      { name: "Ask about this change" }
    )
  )
  unmount()
  await act(async () => {
    for (const resolve of finish)
      resolve(
        Object.assign(new Response(), {
          blob: async () => new Blob(["late-pixels"], { type: "image/png" }),
        })
      )
  })
  expect(useReviewPage.getState().chatDraft).toBe(pairedDraft)
})
