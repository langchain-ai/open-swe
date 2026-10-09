import { queryOptions, useQuery, useQueryClient } from "@tanstack/react-query"
import { useMemo } from "react"
import type { QueryClient, QueryKey } from "@tanstack/react-query"
import { getOrCreateWorkerPoolSingleton } from "@pierre/diffs/worker"

import { api } from "@/lib/api"
import { BROWSER_CACHE_MAX_AGE_MS } from "@/lib/query"
import { DashboardRequestError } from "@/lib/dashboard-fetch"
import { useSession } from "@/lib/session"
import {
  pullRequestStatusQuery,
  pullRequestTopic,
} from "@/features/reviews/lib/cache"
import {
  reviewKeys,
  type PullRequestRef,
} from "@/features/reviews/lib/reviewKeys"
import { getReviewConversation } from "@/features/reviews/lib/conversationApi"
import { usePullRequestStatus } from "@/features/reviews/lib/usePullRequestStatus"
import {
  DIFF_WORKER_HIGHLIGHTER_OPTIONS,
  DIFF_WORKER_POOL_OPTIONS,
  warmDiffHighlighter,
} from "@/features/agents/utils/diffUtils"
import {
  isOpenAnchored,
  openConversations,
  rankFindings,
  threadsNeedingAttention,
  type AnchoredFinding,
  type PlacedThread,
} from "./findings"

export type { PullRequestRef }

// Every file header and note observes these, so each one scrolling in would
// otherwise revalidate them; a missing PR stays missing, so 4xx isn't retried.
// The backend says when the PR or its review changes, so nothing polls.
function shared(pr: PullRequestRef) {
  return {
    gcTime: BROWSER_CACHE_MAX_AGE_MS,
    staleTime: 60_000,
    retry: (failures: number, error: Error) => {
      const status = error instanceof DashboardRequestError ? error.status : 0
      return failures < 1 && !(status >= 400 && status < 500)
    },
    meta: { invalidatedBy: [pullRequestTopic(statusRef(pr))] },
  } as const
}

/** The PR as the status cache and its topic name it: `repo` is `owner/name`. */
function statusRef({ owner, repo, number }: PullRequestRef) {
  return { repo: `${owner}/${repo}`, number }
}

export const reviewQueries = {
  detail: (pr: PullRequestRef) =>
    queryOptions({
      queryKey: reviewKeys.detail(pr),
      queryFn: () => api.getReview(pr.owner, pr.repo, pr.number),
      ...shared(pr),
    }),
  diff: (pr: PullRequestRef) =>
    queryOptions({
      queryKey: reviewKeys.diff(pr),
      queryFn: () => api.getReviewDiff(pr.owner, pr.repo, pr.number),
      ...shared(pr),
    }),
  conversation: (pr: PullRequestRef) =>
    queryOptions({
      queryKey: reviewKeys.conversation(pr),
      queryFn: () => getReviewConversation(pr),
      ...shared(pr),
    }),
}

/** The PR's live state on GitHub, from the same cache entry as the PR list. */
export function useReviewStatus(pr: PullRequestRef) {
  const { repo, number } = statusRef(pr)
  return usePullRequestStatus(repo, number)
}

/**
 * The conversations people still owe an answer, counted one way for the whole
 * page: `current` are on the diff's lines, `outdated` the rest.
 */
export function useOpenConversations(pr: PullRequestRef) {
  const threads = useQuery(reviewQueries.conversation(pr)).data?.threads
  const findings = useQuery(reviewQueries.detail(pr)).data?.findings
  if (!threads || !findings) return null
  const open = openConversations(threads, findings)
  const current = threadsNeedingAttention(threads, findings).length
  return { threads: open, current, outdated: open.length - current }
}

export interface FileMarkers {
  /** Open findings on the file's diff lines, worst first. */
  findings: Array<AnchoredFinding>
  threads: Array<PlacedThread>
}

/** What needs attention in each file, computed once for every header and tree row. */
export function useFileMarkers(
  pr: PullRequestRef
): ReadonlyMap<string, FileMarkers> {
  const findings = useQuery(reviewQueries.detail(pr)).data?.findings
  const threads = useQuery(reviewQueries.conversation(pr)).data?.threads
  return useMemo(() => {
    const byFile = new Map<string, FileMarkers>()
    const of = (path: string) => {
      const markers = byFile.get(path) ?? { findings: [], threads: [] }
      byFile.set(path, markers)
      return markers
    }
    for (const finding of rankFindings((findings ?? []).filter(isOpenAnchored)))
      of(finding.file).findings.push(finding)
    for (const thread of threadsNeedingAttention(threads ?? [], findings ?? []))
      of(thread.path).threads.push(thread)
    return byFile
  }, [findings, threads])
}

/** Refetches what a PR action changed: its status, its detail once merged, its conversation once reviewed. */
export function useRefreshPullRequest(pr: PullRequestRef) {
  const queryClient = useQueryClient()
  const login = useSession().data?.login ?? ""
  const refetch = (...keys: ReadonlyArray<QueryKey>) => {
    for (const queryKey of keys)
      void queryClient.invalidateQueries({ queryKey })
  }
  const status = pullRequestStatusQuery(login, statusRef(pr)).queryKey
  return {
    status: () => refetch(status),
    merged: () => refetch(reviewKeys.detail(pr), status),
    reviewed: () =>
      refetch(reviewKeys.detail(pr), status, reviewKeys.conversation(pr)),
  }
}

/** Prefetches the page's side-effect-free reads on hover intent, and wakes the highlighter. */
export function warmReviewPage(queryClient: QueryClient, pr: PullRequestRef) {
  if (typeof window === "undefined" || !Number.isInteger(pr.number)) return
  void queryClient.prefetchQuery(reviewQueries.detail(pr))
  void queryClient.prefetchQuery(reviewQueries.diff(pr))
  void queryClient.prefetchQuery(reviewQueries.conversation(pr))
  getOrCreateWorkerPoolSingleton({
    poolOptions: DIFF_WORKER_POOL_OPTIONS,
    highlighterOptions: DIFF_WORKER_HIGHLIGHTER_OPTIONS,
  })
    .initialize()
    .catch((error: unknown) =>
      console.warn("Could not start the diff highlighter", error)
    )
  warmDiffHighlighter().catch((error: unknown) =>
    console.warn("Could not warm the diff highlighter", error)
  )
}
