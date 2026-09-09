import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect } from "react"

import { agentsApi } from "./api"
import type { ThreadFeedback, ThreadFeedbackRating } from "./api"

export const threadFeedbackKey = (threadId: string, login?: string | null) =>
  ["agent-threads", threadId, "feedback", ...(login ? [login] : [])] as const

export function useThreadFeedback(
  threadId: string,
  login: string | null,
  isActive: boolean
) {
  const queryClient = useQueryClient()
  const queryKey = threadFeedbackKey(threadId, login)
  const query = useQuery({
    queryKey,
    queryFn: () => agentsApi.getThreadFeedback(threadId),
    enabled: Boolean(login) && !isActive,
    staleTime: 0,
    refetchInterval: (current) => {
      const data = current.state.data
      if (data?.status === "completed" || data?.status === "dismissed")
        return false
      if (data?.status === "waiting" && data.promptAt) {
        return Math.max(1000, Math.min(30_000, data.promptAt - Date.now()))
      }
      return 30_000
    },
    retry: false,
  })
  useEffect(() => {
    if (!isActive) return
    const key = threadFeedbackKey(threadId, login)
    void queryClient.cancelQueries({ queryKey: key })
    queryClient.setQueryData<ThreadFeedback>(key, (data) =>
      data?.status === "ready"
        ? { ...data, status: "unavailable", promptAt: null }
        : data
    )
  }, [isActive, login, queryClient, threadId])
  const mutation = useMutation({
    mutationFn: (
      value: { rating: ThreadFeedbackRating; comment: string } | "dismiss"
    ) =>
      value === "dismiss"
        ? agentsApi.dismissThreadFeedback(threadId)
        : agentsApi.submitThreadFeedback(threadId, value),
    onSuccess: async (data: ThreadFeedback) => {
      await queryClient.cancelQueries({ queryKey })
      queryClient.setQueryData(queryKey, data)
    },
  })
  return { query, mutation }
}
