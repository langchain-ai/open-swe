import { useMutation, useQueryClient } from "@tanstack/react-query"

import type { SendAgentMessageVariables } from "@/features/agents/lib/queries"
import type { AgentThread } from "@/features/agents/lib/types"
import { AgentsApiError, agentsApi } from "@/features/agents/lib/api"
import {
  agentThreadKeys,
  setAgentThreadStatus,
} from "@/features/agents/lib/queries"
import { useAgentThreadRuntime } from "@/features/agents/lib/AgentThreadStreamProvider"

/**
 * Construct the message content for the LangGraph run.
 *
 * @param vars - The variables for the message.
 * @returns The message content.
 */
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

/** Sends resolve when the server accepts a run or a durable queued followup. */
export function useSubmitAgentMessage(threadId: string) {
  const queryClient = useQueryClient()
  const stream = useAgentThreadRuntime()

  return useMutation({
    mutationFn: async (vars: SendAgentMessageVariables) => {
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
        try {
          await agentsApi.queueMessage(threadId, {
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
        if (!optimistic) showQueued()
      }

      try {
        await queue(stream.isLoading)
        return
      } catch (error) {
        if (!(error instanceof AgentsApiError) || error.status !== 409)
          throw error
      }

      const configurable: Record<string, unknown> = {}
      if (vars.model_id && vars.effort) {
        configurable.agent_model_id = vars.model_id
        configurable.agent_effort = vars.effort
      }
      if (vars.plan_mode) {
        configurable.plan_mode = true
      }
      const config =
        Object.keys(configurable).length > 0 ? { configurable } : undefined

      await stream.submit(
        { messages: [{ type: "human", content: messageContent(vars) }] },
        { config }
      )
    },
    onSuccess: () => {
      setAgentThreadStatus(queryClient, threadId, "running")
    },
  })
}
