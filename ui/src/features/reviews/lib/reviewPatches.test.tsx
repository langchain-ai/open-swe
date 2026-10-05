/** @vitest-environment jsdom */
import { QueryClientProvider } from "@tanstack/react-query"
import { act, cleanup, renderHook, waitFor } from "@testing-library/react"
import type { ReactNode } from "react"
import { afterEach, expect, it, vi } from "vitest"

import { ApiError, api, type ReviewDiffPayload } from "@/lib/api"
import { makeQueryClient } from "@/lib/query"
import { useReviewDiffFiles } from "./reviewPatches"

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

const HEAD = "a".repeat(40)

const DIFF: ReviewDiffPayload = {
  base_sha: "b".repeat(40),
  head_sha: HEAD,
  patch_page_size: 2,
  files: ["f0.py", "f1.bin", "f2.py"].map((path, position) => ({
    path,
    previousPath: null,
    status: "modified",
    additions: 1,
    deletions: 1,
    position,
  })),
  total_additions: 3,
  total_deletions: 3,
  truncated: false,
}

function render(options?: { preload?: boolean }) {
  const queryClient = makeQueryClient()
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
  const hook = renderHook(
    () => useReviewDiffFiles("lc", "repo", 7, DIFF, options),
    { wrapper }
  )
  return { hook, queryClient }
}

it("merges each page's patches as it lands, marking files GitHub has no patch for", async () => {
  const pages = vi
    .spyOn(api, "getReviewPatches")
    .mockImplementation(async (_owner, _repo, _number, head, page) => ({
      head_sha: head,
      files:
        page === 1
          ? [{ path: "f0.py", patch: "diff --git a/f0.py b/f0.py\n" }]
          : [{ path: "f2.py", patch: "diff --git a/f2.py b/f2.py\n" }],
    }))
  const { hook } = render()

  await waitFor(() =>
    expect(hook.result.current.files?.map((file) => file.patch)).toEqual([
      "diff --git a/f0.py b/f0.py\n",
      null,
      "diff --git a/f2.py b/f2.py\n",
    ])
  )
  expect(pages.mock.calls.map((call) => call[4]).sort()).toEqual([1, 2])
  expect(hook.result.current.files?.[0]).toMatchObject({
    baseSha: DIFF.base_sha,
    headSha: HEAD,
  })
})

it("loads only the page a viewer asks for when preloading is off", async () => {
  const pages = vi
    .spyOn(api, "getReviewPatches")
    .mockImplementation(async (_owner, _repo, _number, head) => ({
      head_sha: head,
      files: [{ path: "f2.py", patch: "diff --git a/f2.py b/f2.py\n" }],
    }))
  const { hook } = render({ preload: false })
  expect(hook.result.current.files?.map((file) => file.patch)).toEqual([
    undefined,
    undefined,
    undefined,
  ])

  act(() => hook.result.current.requestPatch("f2.py"))

  await waitFor(() =>
    expect(hook.result.current.files?.[2]?.patch).toBe(
      "diff --git a/f2.py b/f2.py\n"
    )
  )
  expect(pages.mock.calls.map((call) => call[4])).toEqual([2])
  expect(hook.result.current.files?.[0]?.patch).toBeUndefined()
})

it("refetches the review when a page reports the head moved", async () => {
  vi.spyOn(api, "getReviewPatches").mockRejectedValue(
    new ApiError(409, "the pull request head moved")
  )
  const { queryClient } = render()
  queryClient.setQueryData(["reviewDiff", "lc", "repo", 7], DIFF)

  await waitFor(() =>
    expect(
      queryClient.getQueryState(["reviewDiff", "lc", "repo", 7])?.isInvalidated
    ).toBe(true)
  )
})
