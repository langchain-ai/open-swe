import type { QueryClient } from "@tanstack/react-query"
import { useMutation, useQueryClient } from "@tanstack/react-query"

import type { SendAgentMessageVariables } from "@/features/agents/lib/queries"
import type {
  AgentThread,
  PendingThreadMessage,
  QueuedThreadMessage,
} from "@/features/agents/lib/types"
import { AgentsApiError, agentsApi } from "@/features/agents/lib/api"
import {
  agentThreadKeys,
  setAgentThreadStatus,
} from "@/features/agents/lib/queries"
import { useAgentStream } from "@/features/agents/lib/stream/AgentStreamProvider"
import {
  modelConfigurable,
  promptMessage,
} from "@/features/agents/lib/stream/promptMessage"

function upsertMessage<T extends QueuedThreadMessage>(
  messages: Array<T> | undefined,
  message: T
): Array<T> {
  return [...(messages ?? []).filter((item) => item.id !== message.id), message]
}

/** A send racing an in-flight stop must land after it, or the stop cancels the run the follow-up just joined. */
async function waitForCancellation(
  queryClient: QueryClient,
  threadId: string
): Promise<void> {
  const cancelling = () =>
    queryClient.isMutating({ mutationKey: agentThreadKeys.cancel(threadId) }) >
    0
  if (!cancelling()) return
  await new Promise<void>((resolve) => {
    let unsubscribe = () => {}
    const finishIfCancelled = () => {
      if (cancelling()) return
      unsubscribe()
      resolve()
    }
    unsubscribe = queryClient.getMutationCache().subscribe(finishIfCancelled)
    finishIfCancelled()
  })
}

function setPendingMessage(
  thread: AgentThread,
  message: PendingThreadMessage
): AgentThread {
  return {
    ...thread,
    pendingMessages: upsertMessage(thread.pendingMessages, message),
  }
}

function removePendingMessage(thread: AgentThread, id: string): AgentThread {
  return {
    ...thread,
    pendingMessages: thread.pendingMessages?.filter(
      (message) => message.id !== id
    ),
  }
}

function setQueuedMessage(
  thread: AgentThread,
  message: QueuedThreadMessage
): AgentThread {
  return {
    ...removePendingMessage(thread, message.id),
    queuedMessages: upsertMessage(thread.queuedMessages, message),
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
 * Submit user messages through the active-run queue or a new stream run.
 *
 * A busy thread persists the follow-up to the dashboard queue. If stop wins the
 * race after that persistence, the endpoint starts a replacement run that
 * consumes it, so the text is never dropped.
 */
export function useSubmitAgentMessage(threadId: string) {
  const queryClient = useQueryClient()
  const stream = useAgentStream()

  return useMutation({
    mutationFn: async (vars: SendAgentMessageVariables) => {
      await waitForCancellation(queryClient, threadId)
      const createdAt = Date.now()
      const id = vars.client_message_id ?? crypto.randomUUID()
      const queuedMessage = {
        id,
        content: vars.content.trim(),
        images: vars.images,
        createdAt,
      }
      const pendingMessage = {
        ...queuedMessage,
        status: "sending" as const,
      }
      const updateThread = (update: (thread: AgentThread) => AgentThread) =>
        queryClient.setQueryData<AgentThread>(
          agentThreadKeys.detail(threadId),
          (prev) => (prev ? update(prev) : prev)
        )
      const knownRunning =
        stream.isLoading ||
        queryClient.getQueryData<AgentThread>(agentThreadKeys.detail(threadId))
          ?.status === "running"
      const queue = async () => {
        await agentsApi.queueMessage(threadId, {
          content: vars.content,
          images: vars.images,
          model_id: vars.model_id,
          effort: vars.effort,
          plan_mode: vars.plan_mode,
          client_message_id: id,
          expect_active: knownRunning,
        })
        updateThread((thread) => setQueuedMessage(thread, queuedMessage))
      }

      if (knownRunning) {
        updateThread((thread) => setQueuedMessage(thread, queuedMessage))
        try {
          await queue()
        } catch (error) {
          updateThread((thread) =>
            setPendingMessage(removeQueuedMessage(thread, id), {
              ...pendingMessage,
              status: "failed",
            })
          )
          throw error
        }
        return
      }

      updateThread((thread) => setPendingMessage(thread, pendingMessage))
      try {
        await queue()
        return
      } catch (error) {
        if (!(error instanceof AgentsApiError) || error.status !== 409) {
          updateThread((thread) =>
            setPendingMessage(thread, { ...pendingMessage, status: "failed" })
          )
          throw error
        }
      }

      const configurable: Record<string, unknown> = modelConfigurable({
        modelId: vars.model_id,
        effort: vars.effort,
      })
      if (vars.plan_mode) configurable.plan_mode = true
      const config =
        Object.keys(configurable).length > 0 ? { configurable } : undefined

      const message = promptMessage(vars.content, vars.images)
      void stream
        .submit({ messages: [{ ...message, id }] }, { config })
        .catch(() => {
          updateThread((thread) =>
            setPendingMessage(thread, { ...pendingMessage, status: "failed" })
          )
          setAgentThreadStatus(queryClient, threadId, "error")
        })
    },
    onSuccess: () => {
      setAgentThreadStatus(queryClient, threadId, "running")
    },
  })
}
