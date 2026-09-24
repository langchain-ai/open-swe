import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

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
    onSuccess: setPending,
    onError: (error) =>
      toast.error("Couldn't add the comment to your review", {
        description: error.message,
      }),
  })
  const update = useMutation({
    mutationFn: ({ id, body }: { id: number; body: string }) =>
      api.updatePendingReviewComment(owner, repo, number, id, body),
    onSuccess: setPending,
    onError: (error) =>
      toast.error("Couldn't update the comment", {
        description: error.message,
      }),
  })
  const remove = useMutation({
    mutationFn: (id: number) =>
      api.deletePendingReviewComment(owner, repo, number, id),
    onSuccess: setPending,
    onError: (error) =>
      toast.error("Couldn't delete the comment", {
        description: error.message,
      }),
  })
  const discard = useMutation({
    mutationFn: () => api.discardPendingReview(owner, repo, number),
    onSuccess: () => setPending(null),
    onError: (error) =>
      toast.error("Couldn't discard the review", {
        description: error.message,
      }),
  })
  return {
    review: query.data ?? null,
    comments: query.data?.comments ?? [],
    add,
    update,
    remove,
    discard,
    invalidate: () => queryClient.invalidateQueries({ queryKey: key }),
  }
}
