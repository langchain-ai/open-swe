import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { agentsApi } from "./api"
import type { ThreadMembership } from "./api"
import type { AgentThread } from "./types"
import { useSession } from "@/lib/session"
import { invalidationTopic } from "@/lib/invalidations/topics"

const membershipKey = (threadId: string) =>
  ["thread-membership", threadId] as const

export function useThreadMembership(thread: AgentThread) {
  const session = useSession()
  const isOwner = Boolean(
    thread.ownerLogin &&
    thread.ownerLogin.toLowerCase() === session.data?.login.toLowerCase()
  )
  const needsApproval =
    Boolean(thread.ownerLogin) && thread.visibility !== "private"
  const query = useQuery({
    queryKey: membershipKey(thread.id),
    queryFn: () => agentsApi.threadMembership(thread.id),
    enabled: needsApproval,
    meta: {
      invalidatedBy: [invalidationTopic("thread-memberships", thread.id)],
    },
    retry: false,
  })
  return {
    data: query.data,
    isError: query.isError,
    refetch: query.refetch,
    isOwner,
    needsApproval,
    canParticipate:
      isOwner ||
      (thread.visibility !== "private" &&
        (!needsApproval || query.data?.canParticipate === true)),
  }
}

export function useChangeThreadMembership(threadId: string) {
  const client = useQueryClient()
  const key = membershipKey(threadId)
  return useMutation({
    mutationFn: (decision: { login: string; approve: boolean } | null) =>
      decision
        ? agentsApi.decideThreadMembership(
            threadId,
            decision.login,
            decision.approve
          )
        : agentsApi.requestThreadMembership(threadId),
    meta: { errorTitle: "Couldn't update thread membership" },
    onMutate: async (decision) => {
      await client.cancelQueries({ queryKey: key })
      const previous = client.getQueryData<ThreadMembership>(key)
      client.setQueryData<ThreadMembership>(key, (current) =>
        current
          ? decision
            ? {
                ...current,
                pendingLogins: current.pendingLogins.filter(
                  (login) => login !== decision.login
                ),
              }
            : { ...current, requestPending: true }
          : current
      )
      return previous
    },
    onError: (_error, _decision, previous) => {
      if (previous) client.setQueryData(key, previous)
    },
    onSuccess: (data) => client.setQueryData(key, data),
    onSettled: () => client.invalidateQueries({ queryKey: key }),
  })
}
