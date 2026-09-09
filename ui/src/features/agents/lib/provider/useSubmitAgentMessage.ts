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

/** Submit user messages through the active-run queue or a new stream run. */
export function useSubmitAgentMessage(threadId: string) {
  const queryClient = useQueryClient()
  const stream = useAgentStream()

  return useMutation({
    mutationFn: async (vars: SendAgentMessageVariables) => {
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
      const queue = async () => {
        await agentsApi.queueMessage(threadId, {
          content: vars.content,
          images: vars.images,
          model_id: vars.model_id,
          effort: vars.effort,
          plan_mode: vars.plan_mode,
          client_message_id: id,
        })
        updateThread((thread) => setQueuedMessage(thread, queuedMessage))
      }

      if (stream.isLoading) {
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
