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

function isRelisted(error: unknown): boolean {
  return error instanceof ApiError && error.status === 409
}

export interface PatchRequests {
  /** Load the page holding `path` now, ahead of the background preload. */
  request: (path: string) => void
  /** Fetch the page holding `path` again after it failed. */
  retry: (path: string) => void
}

export interface ReviewDiffFiles {
  /** null until the diff lists the current head's files. */
  files: Array<ReviewDiffFile> | null
  requests: PatchRequests
}

/** Lets a diff card ask for its file's patch, or retry it, from where it renders. */
export const PatchRequestsContext = createContext<PatchRequests>({
  request: () => {},
  retry: () => {},
})

/**
 * The listed files with their patches merged in as their pages load.
 *
 * Patches come a page at a time from GitHub's own diff, so commentable lines
 * always match GitHub. A page is keyed by the merge base and head its file
 * list came with, so a relisted PR never reads another listing's pages. Pages
 * load in listing order a few at a time; a page a viewer needs sooner (a file
 * scrolled near, a finding jumped to) loads at once. Query results are
 * structurally shared, so a file keeps its object identity until its own
 * patch arrives and a landing page re-renders only its files.
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
  const revision = `${baseSha}..${headSha}`
  const pages = Array.from(
    { length: listed ? Math.ceil(listed.length / pageSize) : 0 },
    (_, index) => index + 1
  )
  const [requested, setRequested] = useState<{
    revision: string
    pages: ReadonlySet<number>
  }>({ revision: "", pages: new Set() })
  const requestedPages =
    requested.revision === revision ? requested.pages : null

  const settled = settledPrefix(
    pages.map(
      (page) =>
        queryClient.getQueryState([
          "reviewPatches",
          owner,
          repo,
          number,
          baseSha,
          headSha,
          page,
        ])?.status
    )
  )
  const preloadThrough = preload
    ? Math.min(PRELOAD_PAGE_BUDGET, settled + PRELOAD_CONCURRENCY)
    : 0

  const { files, relisted } = useQueries({
    queries: pages.map((page) => ({
      queryKey: ["reviewPatches", owner, repo, number, baseSha, headSha, page],
      queryFn: () =>
        api.getReviewPatches(owner, repo, number, headSha, baseSha, page),
      enabled: (requestedPages?.has(page) ?? false) || page <= preloadThrough,
      ...expiresInBrowser,
      retry: (failures: number, error: Error) =>
        !isRelisted(error) && failures < 1,
    })),
    combine: (results) => ({
      files:
        listed?.map((file): ReviewDiffFile => {
          const result = results[patchPage(file, pageSize) - 1]
          const page = result?.data
          const patch = page
            ? (page.files.find((entry) => entry.path === file.path)?.patch ??
              null)
            : undefined
          const patchFailed =
            !page && result?.isError === true && !isRelisted(result.error)
          return { ...file, baseSha, headSha, patch, patchFailed }
        }) ?? null,
      relisted: results.some((result) => isRelisted(result.error)),
    }),
  })

  useEffect(() => {
    if (!relisted) return
    void queryClient.invalidateQueries({
      queryKey: ["reviewDiff", owner, repo, number],
    })
    void queryClient.invalidateQueries({
      queryKey: ["review", owner, repo, number],
    })
  }, [relisted, queryClient, owner, repo, number])

  const pageOf = useMemo(
    () =>
      new Map(
        (listed ?? []).map((file) => [file.path, patchPage(file, pageSize)])
      ),
    [listed, pageSize]
  )
  const request = useCallback(
    (path: string) => {
      const page = pageOf.get(path)
      if (page === undefined) return
      setRequested((current) => {
        const known =
          current.revision === revision ? current.pages : new Set<number>()
        return known.has(page)
          ? current
          : { revision, pages: new Set(known).add(page) }
      })
    },
    [pageOf, revision, setRequested]
  )
  const retry = useCallback(
    (path: string) => {
      const page = pageOf.get(path)
      if (page === undefined) return
      void queryClient.refetchQueries({
        queryKey: [
          "reviewPatches",
          owner,
          repo,
          number,
          baseSha,
          headSha,
          page,
        ],
        exact: true,
      })
    },
    [pageOf, queryClient, owner, repo, number, baseSha, headSha]
  )
  const requests = useMemo(() => ({ request, retry }), [request, retry])

  return { files, requests }
}
