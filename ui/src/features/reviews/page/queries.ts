import { queryOptions, useQuery } from "@tanstack/react-query"
import type { QueryClient } from "@tanstack/react-query"
import { getOrCreateWorkerPoolSingleton } from "@pierre/diffs/worker"

import { api } from "@/lib/api"
import { DashboardRequestError } from "@/lib/dashboard-fetch"
import { reviewChatQuery } from "@/features/agents/lib/queries"
import {
  getReviewConversation,
  reviewConversationQueryKey,
} from "@/features/reviews/lib/conversationApi"
import {
  DIFF_WORKER_HIGHLIGHTER_OPTIONS,
  DIFF_WORKER_POOL_OPTIONS,
  warmDiffHighlighter,
} from "@/features/agents/utils/diffUtils"

export interface PullRequestRef {
  owner: string
  repo: string
  number: number
}

// Back/forward between a handful of PRs repaints from cache, then revalidates.
const KEEP_RECENT_MS = 10 * 60_000

// Every file header and note observes these, so each one scrolling in would otherwise revalidate them.
const SHARED = { gcTime: KEEP_RECENT_MS, staleTime: 60_000 } as const

// A missing PR or repository stays missing; retrying only delays saying so.
function retryUnlessClientError(failures: number, error: Error): boolean {
  const status = error instanceof DashboardRequestError ? error.status : 0
  return failures < 1 && !(status >= 400 && status < 500)
}

export const reviewQueries = {
  detail: ({ owner, repo, number }: PullRequestRef) =>
    queryOptions({
      queryKey: ["review", owner, repo, number] as const,
      queryFn: () => api.getReview(owner, repo, number),
      ...SHARED,
      retry: retryUnlessClientError,
      refetchInterval: (query) =>
        query.state.data?.status === "running" ||
        query.state.data?.walkthrough_running
          ? 5000
          : false,
    }),
  diff: ({ owner, repo, number }: PullRequestRef) =>
    queryOptions({
      queryKey: ["reviewDiff", owner, repo, number] as const,
      queryFn: () => api.getReviewDiff(owner, repo, number),
      ...SHARED,
      retry: retryUnlessClientError,
    }),
  conversation: ({ owner, repo, number }: PullRequestRef) =>
    queryOptions({
      queryKey: reviewConversationQueryKey(owner, repo, number),
      queryFn: () => getReviewConversation(owner, repo, number),
      ...SHARED,
      retry: retryUnlessClientError,
    }),
  status: ({ owner, repo, number }: PullRequestRef) =>
    queryOptions({
      queryKey: ["review-page-pr", `${owner}/${repo}`, number] as const,
      queryFn: () => api.myPullRequestDetails(`${owner}/${repo}`, number),
      ...SHARED,
      retry: retryUnlessClientError,
      refetchOnWindowFocus: false,
    }),
}

/** The page's chat; reading it creates the thread, so it waits until the PR is known to exist. */
export function useReviewChat(pr: PullRequestRef) {
  const exists = useQuery(reviewQueries.detail(pr)).isSuccess
  return useQuery({ ...reviewChatQuery(pr), enabled: exists })
}

/** The worker pool CodeView highlights with; the provider on the page shares this singleton. */
export function reviewWorkerPool() {
  return getOrCreateWorkerPoolSingleton({
    poolOptions: DIFF_WORKER_POOL_OPTIONS,
    highlighterOptions: DIFF_WORKER_HIGHLIGHTER_OPTIONS,
  })
}

/**
 * Everything the page needs before its first paint, started on hover intent.
 * Only reads without side effects: the chat read opens a thread, so it waits for the visit.
 */
export function warmReviewPage(queryClient: QueryClient, pr: PullRequestRef) {
  if (typeof window === "undefined" || !Number.isFinite(pr.number)) return
  void queryClient.prefetchQuery(reviewQueries.detail(pr))
  void queryClient.prefetchQuery(reviewQueries.diff(pr))
  void queryClient.prefetchQuery(reviewQueries.status(pr))
  void queryClient.prefetchQuery(reviewQueries.conversation(pr))
  reviewWorkerPool()
    .initialize()
    .catch((error: unknown) =>
      console.warn("Could not start the diff highlighter", error)
    )
  warmDiffHighlighter().catch((error: unknown) =>
    console.warn("Could not warm the diff highlighter", error)
  )
}
