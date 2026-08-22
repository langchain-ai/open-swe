import { useMutation, useQueryClient } from "@tanstack/react-query"
import { useStreamContext as useAgentThreadStream } from "@langchain/react"

import type { SendAgentMessageVariables } from "@/features/agents/lib/queries"
import type { AgentThread } from "@/features/agents/lib/types"
import { AgentsApiError, agentsApi } from "@/features/agents/lib/api"
import {
  agentThreadKeys,
  setAgentThreadStatus,
} from "@/features/agents/lib/queries"
import { PlanApiError, rejectPlan } from "@/lib/plan"
import { onAgentRunCreated } from "@/features/agents/lib/provider/runAcceptance"

function messageContent(vars: SendAgentMessageVariables) {
  const text = vars.content.trim()
  const imageBlocks =
    vars.images?.map((image) => ({
      type: "image",
      base64: image.base64,
      mime_type: image.mimeType,
      ...(image.fileName ? { file_name: image.fileName } : {}),
    })) ?? []
  return [...imageBlocks, ...(text ? [{ type: "text", text }] : [])]
}

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
        status: "sending",
      },
    ],
  }
}

function updateQueuedMessage(
  thread: AgentThread,
  id: string,
  status: "sending" | "queued"
): AgentThread {
  return {
    ...thread,
    queuedMessages: thread.queuedMessages?.map((message) =>
      message.id === id ? { ...message, status } : message
    ),
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

export function useSubmitAgentMessage(threadId: string) {
  const queryClient = useQueryClient()
  const stream = useAgentThreadStream()

  return useMutation({
    mutationFn: async (vars: SendAgentMessageVariables) => {
      const queuedAt = Date.now()
      const queuedId = `queued-${queuedAt}-${Math.random().toString(36).slice(2)}`
      const removeOptimisticMessage = () =>
        queryClient.setQueryData<AgentThread>(
          agentThreadKeys.detail(threadId),
          (prev) => (prev ? removeQueuedMessage(prev, queuedId) : prev)
        )
      queryClient.setQueryData<AgentThread>(
        agentThreadKeys.detail(threadId),
        (prev) =>
          prev ? appendQueuedMessage(prev, vars, queuedId, queuedAt) : prev
      )

      try {
        await agentsApi.queueMessage(threadId, {
          content: vars.content,
          images: vars.images,
          model_id: vars.model_id,
          effort: vars.effort,
          plan_mode: vars.plan_mode,
        })
        queryClient.setQueryData<AgentThread>(
          agentThreadKeys.detail(threadId),
          (prev) =>
            prev ? updateQueuedMessage(prev, queuedId, "queued") : prev
        )
        return
      } catch (error) {
        if (!(error instanceof AgentsApiError) || error.status !== 409) {
          removeOptimisticMessage()
          throw error
        }
      }

      if (vars.reject_plan) {
        try {
          await rejectPlan(threadId, false)
        } catch (error) {
          if (!(error instanceof PlanApiError) || error.status !== 409) {
            removeOptimisticMessage()
            throw error
          }
        }
      }

      const configurable: Record<string, unknown> = {}
      if (vars.model_id && vars.effort) {
        configurable.agent_model_id = vars.model_id
        configurable.agent_effort = vars.effort
      }
      if (vars.plan_mode) configurable.plan_mode = true
      const config =
        Object.keys(configurable).length > 0 ? { configurable } : undefined

      const accepted = new Promise<void>((resolve, reject) => {
        const unsubscribe = onAgentRunCreated(threadId, () => {
          unsubscribe()
          resolve()
        })
        void stream
          .submit(
            { messages: [{ type: "human", content: messageContent(vars) }] },
            {
              config,
              onError: (error) => {
                unsubscribe()
                reject(error)
              },
            }
          )
          .catch((error) => {
            unsubscribe()
            reject(error)
          })
      })
      try {
        await accepted
      } catch (error) {
        removeOptimisticMessage()
        setAgentThreadStatus(queryClient, threadId, "error")
        throw error
      }
    },
    onSuccess: () => setAgentThreadStatus(queryClient, threadId, "running"),
  })
}
