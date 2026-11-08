// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, waitFor } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { api } from "@/lib/api"
import type {
  ReviewDetail,
  ReviewDiffFile,
  ReviewFinding,
  ReviewWalkthrough,
} from "@/lib/api"
import { PageModelContext, PageToolsProvider } from "@/lib/pageTools"
import { buildEntries } from "./diffEntries"
import { isRead, useReviewPage } from "./store"
import { useReviewPageTools } from "./useReviewPageTools"

vi.mock("@/lib/api", async (original) => {
  const actual = await original<typeof import("@/lib/api")>()
  return {
    ...actual,
    api: { ...actual.api, getReview: vi.fn(), getReviewDiff: vi.fn() },
  }
})

const pr = { owner: "acme", repo: "app", number: 7 }

function file(path: string): ReviewDiffFile {
  return {
    baseSha: "a".repeat(40),
    headSha: "b".repeat(40),
    path,
    previousPath: null,
    status: "modified",
    additions: 1,
    deletions: 1,
    patch: null,
  }
}

const finding = {
  id: "f1",
  title: "Off by one",
  group: "bug",
  status: "open",
  file: "src/loop.ts",
  start_line: 3,
  end_line: 4,
  side: "RIGHT",
  in_diff: true,
  outdated: false,
} as ReviewFinding

function Page() {
  useReviewPageTools(pr)
  return null
}

async function mounted() {
  const context = new PageModelContext()
  render(
    <QueryClientProvider client={new QueryClient()}>
      <PageToolsProvider context={context}>
        <Page />
      </PageToolsProvider>
    </QueryClientProvider>
  )
  await waitFor(async () =>
    expect((await call(context, "review_list_files")).files).toHaveLength(2)
  )
  return context
}

async function call(
  context: PageModelContext,
  name: string,
  input: object = {}
) {
  const tool = (await context.getTools()).find((t) => t.name === name)
  if (!tool) throw new Error(`no tool ${name}`)
  return JSON.parse(await context.executeTool(tool, input))
}

describe("the review page's tools", () => {
  beforeEach(() => {
    useReviewPage.getState().open(pr, "b".repeat(40))
    vi.mocked(api.getReviewDiff).mockResolvedValue({
      files: [file("src/loop.ts"), file("docs/readme.md")],
      total_additions: 2,
      total_deletions: 2,
      truncated: false,
    })
    vi.mocked(api.getReview).mockResolvedValue({
      findings: [finding],
    } as ReviewDetail)
  })

  it("show code and findings through the page's own navigation", async () => {
    const context = await mounted()

    useReviewPage.getState().setFileFilter("docs")
    await call(context, "review_show_code", {
      path: "src/loop.ts",
      start_line: 2,
      line: 5,
    })
    const state = useReviewPage.getState()
    // A file the filter hid is shown again before the page scrolls to it.
    expect(state.fileFilter).toBe("")
    expect(state.jump?.target).toEqual({
      kind: "line",
      path: "src/loop.ts",
      line: 5,
      start: 2,
      side: "RIGHT",
    })

    await call(context, "review_show_finding", { id: "f1" })
    expect(useReviewPage.getState().expandedFinding).toBe("f1")

    await expect(
      call(context, "review_show_code", { path: "nope.ts" })
    ).rejects.toMatchObject({
      name: "OperationError",
      message: "nope.ts is not one of this pull request's files",
    })
  })

  it("only drafts a comment, leaving the person to post it", async () => {
    const context = await mounted()

    await call(context, "review_draft_comment", {
      path: "src/loop.ts",
      line: 4,
      body: "Should this be `<=`?",
    })
    const state = useReviewPage.getState()
    expect(state.composer).toEqual({
      path: "src/loop.ts",
      range: { start: 4, end: 4, side: "additions" },
    })
    expect(state.composerText).toBe("Should this be `<=`?")

    await expect(
      call(context, "review_draft_comment", {
        path: "src/loop.ts",
        line: 4,
        body: "Another",
      })
    ).rejects.toMatchObject({ name: "OperationError" })
    expect(useReviewPage.getState().composerText).toBe("Should this be `<=`?")
  })
  it("moves to a walkthrough step and marks steps and files viewed", async () => {
    const files = [file("src/loop.ts"), file("docs/readme.md")]
    const walkthrough: ReviewWalkthrough = {
      head_sha: "b".repeat(40),
      human_input: "",
      steps: [
        {
          index: 1,
          title: "Both files",
          summary: "",
          other: false,
          files: files.map((f) => ({ path: f.path, added: [], deleted: [] })),
        },
      ],
    }
    vi.mocked(api.getReview).mockResolvedValue({
      findings: [finding],
      walkthrough,
    } as unknown as ReviewDetail)
    useReviewPage.getState().setOrder("files")
    const context = await mounted()
    await waitFor(async () =>
      expect((await call(context, "review_list_steps")).steps).toHaveLength(1)
    )

    // The page's diff lays the walkthrough's entries out once the order switches.
    const shown = call(context, "review_show_step", { index: 1 })
    await waitFor(() => expect(useReviewPage.getState().order).toBe("guide"))
    const entries = buildEntries(files, walkthrough, "guide")
    useReviewPage.getState().setEntryOrder(entries)
    await shown
    expect(useReviewPage.getState().jump?.target).toEqual({
      kind: "entry",
      id: entries[0]!.id,
    })

    // A filter hides one of the step's files; marking the step still marks all of it.
    useReviewPage.getState().setFileFilter("loop")
    useReviewPage.getState().setEntryOrder([entries[0]!])
    const marking = call(context, "review_mark_step_viewed", { index: 1 })
    await waitFor(() => expect(useReviewPage.getState().fileFilter).toBe(""))
    useReviewPage.getState().setEntryOrder(entries)
    await marking
    const viewed = () =>
      entries.map((entry) => isRead(useReviewPage.getState().viewed, entry))
    expect(viewed()).toEqual([true, true])
    // Marking again leaves it marked rather than toggling it back.
    await call(context, "review_mark_step_viewed", { index: 1 })
    expect(viewed()).toEqual([true, true])

    await call(context, "review_mark_file_viewed", {
      path: entries[0]!.file.path,
      viewed: false,
    })
    expect(viewed()).toEqual([false, true])
    const [step] = (await call(context, "review_list_steps")).steps
    expect(step).toMatchObject({ index: 1, viewed: false })
  })
})
