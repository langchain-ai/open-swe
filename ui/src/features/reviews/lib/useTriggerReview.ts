import { useMutation, useQueryClient } from "@tanstack/react-query"

import { api, type ReviewDetail } from "@/lib/api"

/**
 * Start a reviewer run for a PR, from the side panel or the start-review card.
 *
 * The trigger returns once the reviewer thread exists, so the cached detail is
 * moved to `running` rather than invalidated: that is what turns on the detail
 * query's 5s poll, which then replaces the guess with the real run status.
 */
export function useTriggerReview(owner: string, repo: string, number: number) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => api.reReview(owner, repo, number),
    onSuccess: () =>
      queryClient.setQueryData<ReviewDetail>(
        ["review", owner, repo, number],
        (previous) => (previous ? { ...previous, status: "running" } : previous)
      ),
  })
}
