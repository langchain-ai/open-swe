import { useMutation, useQueryClient } from "@tanstack/react-query"

import type { SendAgentMessageVariables } from "@/features/agents/lib/queries"
import type { AgentThread } from "@/features/agents/lib/types"
import { agentsApi } from "@/features/agents/lib/api"
import {
  agentThreadKeys,
  setAgentThreadStatus,
} from "@/features/agents/lib/queries"
import { useAgentThreadRuntime } from "@/features/agents/lib/AgentThreadStreamProvider"

function appendQueuedMessage(
  thread: AgentThread,
  vars: SendAgentMessageVariables,
  id: string,
  createdAt: number
): AgentThread {
  return {
    ...thread,
    queuedMessages: [
      ...(thread.queuedMessages ?? []),
      {
        id,
        content: vars.content.trim(),
        images: vars.images,
        createdAt,
      },
    ],
  }
}

function removeQueuedMessage(thread: AgentThread, id: string): AgentThread {
  return {
    ...thread,
    queuedMessages: thread.queuedMessages?.filter(
      (message) => message.id !== id
    ),
  }
}

/**
 * User-initiated sends from the prompt bar. Prefer this over calling `stream.submit`
 * directly so cache updates and the busy-thread queue path stay consistent.
 *
 * When the thread is busy, the endpoint queues the follow-up. When stop wins a
 * race and leaves it idle, the same endpoint starts a durable replacement run.
 *
 * @param threadId - The ID of the thread to submit the message to.
 * @returns The mutation object.
 */
export function useSubmitAgentMessage(threadId: string) {
  const queryClient = useQueryClient()
  const stream = useAgentThreadRuntime()

  return useMutation({
    mutationFn: async (vars: SendAgentMessageVariables) => {
      const waitForCancellation = async () => {
        if (
          !queryClient.isMutating({
            mutationKey: agentThreadKeys.cancel(threadId),
          })
        )
          return
        await new Promise<void>((resolve) => {
          let unsubscribe = () => {}
          const finishIfCancelled = () => {
            if (
              queryClient.isMutating({
                mutationKey: agentThreadKeys.cancel(threadId),
              })
            )
              return
            unsubscribe()
            resolve()
          }
          unsubscribe = queryClient
            .getMutationCache()
            .subscribe(finishIfCancelled)
          finishIfCancelled()
        })
      }
      await waitForCancellation()
      // `optimistic` is only safe when a run is known to be in flight. Idle
      // sends still probe `/messages` first (a run may have started elsewhere),
      // and that probe answers 409 — showing the bubble up front would flash a
      // "Queued next" card for the length of the round trip.
      const queue = async (optimistic: boolean) => {
        const queuedAt = Date.now()
        const queuedId = `queued-${queuedAt}-${Math.random().toString(36).slice(2)}`
        const showQueued = () =>
          queryClient.setQueryData<AgentThread>(
            agentThreadKeys.detail(threadId),
            (prev) =>
              prev ? appendQueuedMessage(prev, vars, queuedId, queuedAt) : prev
          )
        if (optimistic) showQueued()
        let queuedThread: AgentThread
        try {
          queuedThread = await agentsApi.queueMessage(threadId, {
            content: vars.content,
            images: vars.images,
            model_id: vars.model_id,
            effort: vars.effort,
            plan_mode: vars.plan_mode,
          })
        } catch (error) {
          if (optimistic) {
            queryClient.setQueryData<AgentThread>(
              agentThreadKeys.detail(threadId),
              (prev) => (prev ? removeQueuedMessage(prev, queuedId) : prev)
            )
          }
          throw error
        }
        if (queuedThread?.queuedMessages?.length) {
          queryClient.setQueryData(
            agentThreadKeys.detail(threadId),
            queuedThread
          )
        }
      }

      const thread = queryClient.getQueryData<AgentThread>(
        agentThreadKeys.detail(threadId)
      )
      const optimistic = stream.isLoading || thread?.status === "running"
      await queue(optimistic)
    },
    onSuccess: () => {
      setAgentThreadStatus(queryClient, threadId, "running")
    },
  })
}
