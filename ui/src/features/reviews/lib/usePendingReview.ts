import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import type { PendingReview, ReviewCommentCreate } from "@/lib/api"
import { api } from "@/lib/api"

export function pendingReviewQueryKey(
  owner: string,
  repo: string,
  number: number
): ReadonlyArray<string | number> {
  return ["pending-review", owner, repo, number]
}

/** The viewer's GitHub pending review for a PR, plus the mutations that edit it. */
export function usePendingReview(owner: string, repo: string, number: number) {
  const queryClient = useQueryClient()
  const key = pendingReviewQueryKey(owner, repo, number)
  const query = useQuery({
    queryKey: key,
    queryFn: () => api.getPendingReview(owner, repo, number),
    refetchOnWindowFocus: false,
  })
  const setPending = (review: PendingReview | null) =>
    queryClient.setQueryData(key, review)

  const add = useMutation({
    mutationFn: (comment: ReviewCommentCreate) =>
      api.addPendingReviewComment(owner, repo, number, comment),
    meta: { errorTitle: "Couldn't add the comment to your review" },
    onSuccess: setPending,
  })
  const update = useMutation({
    mutationFn: ({ id, body }: { id: number; body: string }) =>
      api.updatePendingReviewComment(owner, repo, number, id, body),
    meta: { errorTitle: "Couldn't update the comment" },
    onSuccess: setPending,
  })
  const remove = useMutation({
    mutationFn: (id: number) =>
      api.deletePendingReviewComment(owner, repo, number, id),
    meta: { errorTitle: "Couldn't delete the comment" },
    onSuccess: setPending,
  })
  const discard = useMutation({
    mutationFn: () => api.discardPendingReview(owner, repo, number),
    meta: { errorTitle: "Couldn't discard the review" },
    onSuccess: () => setPending(null),
  })
  return {
    review: query.data ?? null,
    comments: query.data?.comments ?? [],
    /** The pending review has been read, so `review === null` means there is none. */
    loaded: query.isSuccess && !query.isFetching,
    updatedAt: query.dataUpdatedAt,
    add,
    update,
    remove,
    discard,
    invalidate: () => queryClient.invalidateQueries({ queryKey: key }),
  }
}
