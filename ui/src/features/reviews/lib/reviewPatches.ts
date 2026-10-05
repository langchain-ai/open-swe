import { useQueries, useQueryClient } from "@tanstack/react-query"
import { createContext, useCallback, useEffect, useMemo, useState } from "react"

import { ApiError, api } from "@/lib/api"
import type {
  ReviewDiffFile,
  ReviewDiffPayload,
  ReviewListedFile,
} from "@/lib/api"
import { expiresInBrowser } from "@/lib/query"

/** Pages fetched at once by the background preload. */
const PRELOAD_CONCURRENCY = 2
/** Pages preloaded unasked; beyond them, a file's page loads as it nears the viewport. */
const PRELOAD_PAGE_BUDGET = 10

export function patchPage(file: ReviewListedFile, pageSize: number): number {
  return Math.floor(file.position / pageSize) + 1
}

/** How many pages, from the first, have finished loading or failing. */
export function settledPrefix(
  statuses: ReadonlyArray<string | undefined>
): number {
  const pending = statuses.findIndex(
    (status) => status !== "success" && status !== "error"
  )
  return pending === -1 ? statuses.length : pending
}

export interface ReviewDiffFiles {
  /** null until the diff lists the current head's files. */
  files: Array<ReviewDiffFile> | null
  /** Load the page holding `path` now, ahead of the background preload. */
  requestPatch: (path: string) => void
}

/** Lets a diff card ask for its file's patch when it nears the viewport. */
export const RequestPatchContext = createContext<(path: string) => void>(
  () => {}
)

/**
 * The listed files with their patches merged in as their pages load.
 *
 * Patches come a page at a time from GitHub's own diff, so commentable lines
 * always match GitHub. Pages load in listing order a few at a time; a page a
 * viewer needs sooner (a file scrolled near, a finding jumped to) loads at
 * once. Query results are structurally shared, so a file keeps its object
 * identity until its own patch arrives and a landing page re-renders only its
 * files.
 */
export function useReviewDiffFiles(
  owner: string,
  repo: string,
  number: number,
  diff: ReviewDiffPayload | undefined,
  { preload = true }: { preload?: boolean } = {}
): ReviewDiffFiles {
  const queryClient = useQueryClient()
  const listed = diff?.files ?? null
  const pageSize = diff?.patch_page_size ?? 1
  const headSha = diff?.head_sha ?? ""
  const baseSha = diff?.base_sha ?? ""
  const pages = Array.from(
    { length: listed ? Math.ceil(listed.length / pageSize) : 0 },
    (_, index) => index + 1
  )
  const [requested, setRequested] = useState<{
    head: string
    pages: ReadonlySet<number>
  }>({ head: "", pages: new Set() })
  const requestedPages = requested.head === headSha ? requested.pages : null

  const settled = settledPrefix(
    pages.map(
      (page) =>
        queryClient.getQueryState([
          "reviewPatches",
          owner,
          repo,
          number,
          headSha,
          page,
        ])?.status
    )
  )
  const preloadThrough = preload
    ? Math.min(PRELOAD_PAGE_BUDGET, settled + PRELOAD_CONCURRENCY)
    : 0

  const { files, headMoved } = useQueries({
    queries: pages.map((page) => ({
      queryKey: ["reviewPatches", owner, repo, number, headSha, page],
      queryFn: () => api.getReviewPatches(owner, repo, number, headSha, page),
      enabled: (requestedPages?.has(page) ?? false) || page <= preloadThrough,
      ...expiresInBrowser,
      retry: (failures: number, error: Error) =>
        !(error instanceof ApiError && error.status === 409) && failures < 1,
    })),
    combine: (results) => ({
      files:
        listed?.map((file): ReviewDiffFile => {
          const page = results[patchPage(file, pageSize) - 1]?.data
          const patch = page
            ? (page.files.find((entry) => entry.path === file.path)?.patch ??
              null)
            : undefined
          return { ...file, baseSha, headSha, patch }
        }) ?? null,
      headMoved: results.some(
        (result) =>
          result.error instanceof ApiError && result.error.status === 409
      ),
    }),
  })

  useEffect(() => {
    if (!headMoved) return
    void queryClient.invalidateQueries({
      queryKey: ["reviewDiff", owner, repo, number],
    })
    void queryClient.invalidateQueries({
      queryKey: ["review", owner, repo, number],
    })
  }, [headMoved, queryClient, owner, repo, number])

  const pageOf = useMemo(
    () =>
      new Map(
        (listed ?? []).map((file) => [file.path, patchPage(file, pageSize)])
      ),
    [listed, pageSize]
  )
  const requestPatch = useCallback(
    (path: string) => {
      const page = pageOf.get(path)
      if (page === undefined) return
      setRequested((current) => {
        const known =
          current.head === headSha ? current.pages : new Set<number>()
        return known.has(page)
          ? current
          : { head: headSha, pages: new Set(known).add(page) }
      })
    },
    [pageOf, headSha, setRequested]
  )

  return { files, requestPatch }
}
