import {
  keepPreviousData,
  queryOptions,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query"

import { reviewsApi } from "./api"
import type { ReviewChatThread, ReviewCommentCreate } from "./api"

export const reviewKeys = {
  list: (page: number, mine: boolean) => ["reviews", mine, page] as const,
  detail: (owner: string, repo: string, number: number) =>
    ["review", owner, repo, number] as const,
  diff: (owner: string, repo: string, number: number) =>
    ["reviewDiff", owner, repo, number] as const,
  comments: (owner: string, repo: string, number: number) =>
    ["reviewComments", owner, repo, number] as const,
  chat: (owner: string, repo: string, number: number) =>
    ["review-chat", owner, repo, number] as const,
  chatThreads: (owner: string, repo: string, number: number) =>
    ["review-chat-threads", owner, repo, number] as const,
  autoReviewRepos: ["autoReviewRepos"] as const,
  styles: ["reviewStyles"] as const,
  style: (fullName: string | null) => ["reviewStyle", fullName] as const,
}

/** Shared by the list query and the neighbouring-page prefetch. */
export function reviewListQueryOptions(page: number, mine: boolean) {
  return queryOptions({
    queryKey: reviewKeys.list(page, mine),
    queryFn: () => reviewsApi.list(page, mine),
    placeholderData: keepPreviousData,
    refetchInterval: (query) =>
      query.state.data?.reviews.some((review) => review.status === "running")
        ? 5000
        : false,
  })
}

export function useReviewList(
  page: number,
  mine: boolean,
  options: { enabled?: boolean } = {}
) {
  return useQuery({ ...reviewListQueryOptions(page, mine), ...options })
}

/** A PR under review, kept live while the reviewer agent is still working. */
export function useReviewDetail(
  owner: string,
  repo: string,
  number: number,
  options: { enabled?: boolean; retry?: boolean } = {}
) {
  return useQuery({
    queryKey: reviewKeys.detail(owner, repo, number),
    queryFn: () => reviewsApi.get(owner, repo, number),
    enabled: options.enabled,
    retry: options.retry,
    refetchInterval: (query) =>
      query.state.data?.status === "running" ? 5000 : false,
  })
}

export function useReviewDiff(
  owner: string,
  repo: string,
  number: number,
  options: { enabled?: boolean } = {}
) {
  return useQuery({
    queryKey: reviewKeys.diff(owner, repo, number),
    queryFn: () => reviewsApi.getDiff(owner, repo, number),
    enabled: options.enabled,
  })
}

/** Human review comments on the PR, refreshed as the reader posts their own. */
export function useReviewComments(owner: string, repo: string, number: number) {
  return useQuery({
    queryKey: reviewKeys.comments(owner, repo, number),
    queryFn: () => reviewsApi.listComments(owner, repo, number),
    enabled: Number.isFinite(number),
    staleTime: 30_000,
  })
}

export function useReReview(owner: string, repo: string, number: number) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => reviewsApi.reReview(owner, repo, number),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: reviewKeys.detail(owner, repo, number),
      })
    },
  })
}

export function useCreateReviewComment(
  owner: string,
  repo: string,
  number: number
) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: ReviewCommentCreate) =>
      reviewsApi.createComment(owner, repo, number, body),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: reviewKeys.comments(owner, repo, number),
      })
    },
  })
}

export function useUpdateReviewComment(
  owner: string,
  repo: string,
  number: number,
  commentId: number
) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: string) =>
      reviewsApi.updateComment(owner, repo, number, commentId, body),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: reviewKeys.comments(owner, repo, number),
      })
    },
  })
}

export function useReviewChatMeta(owner: string, repo: string, number: number) {
  return useQuery({
    queryKey: reviewKeys.chat(owner, repo, number),
    queryFn: () => reviewsApi.getChat(owner, repo, number),
  })
}

export function useReviewChatThreads(
  owner: string,
  repo: string,
  number: number
) {
  return useQuery({
    queryKey: reviewKeys.chatThreads(owner, repo, number),
    queryFn: () => reviewsApi.listChatThreads(owner, repo, number),
  })
}

export function useDeleteReviewChatThread(
  owner: string,
  repo: string,
  number: number
) {
  const queryClient = useQueryClient()
  const queryKey = reviewKeys.chatThreads(owner, repo, number)
  return useMutation({
    mutationFn: (threadId: string) =>
      reviewsApi.deleteChatThread(owner, repo, number, threadId),
    onMutate: (threadId: string) => {
      queryClient.setQueryData<{ threads: Array<ReviewChatThread> }>(
        queryKey,
        (old) =>
          old
            ? {
                threads: old.threads.filter(
                  (thread) => thread.thread_id !== threadId
                ),
              }
            : old
      )
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey })
    },
  })
}
