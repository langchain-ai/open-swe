import { useMutation, useQueryClient } from "@tanstack/react-query"
import { useStreamContext as useAgentThreadStream } from "@langchain/react"

import type { SendAgentMessageVariables } from "@/features/agents/lib/queries"
import type { AgentThread } from "@/features/agents/lib/types"
import { AgentsApiError, agentsApi } from "@/features/agents/lib/api"
import {
  agentThreadKeys,
  invalidateAgentThreadLists,
} from "@/features/agents/lib/queries"

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

/**
 * User-initiated sends from the prompt bar. Prefer this over calling `stream.submit`
 * directly so cache updates and the busy-thread queue path stay consistent.
 *
 * When the thread is idle, submits a new run via the stream commands endpoint.
 * When a run is already in flight (`stream.isLoading`), posts to the dashboard
 * `/messages` endpoint instead of using LangGraph `multitaskStrategy: "enqueue"`.
 * That endpoint writes to the thread store; `check_message_queue_before_model`
 * injects the message into the *current* run before the next model call — the
 * same mid-run follow-up path used by Slack, Linear, and GitHub webhooks.
 *
 * @param threadId - The ID of the thread to submit the message to.
 * @returns The mutation object.
 */
export function useSubmitAgentMessage(threadId: string) {
  const queryClient = useQueryClient()
  const stream = useAgentThreadStream()

  return useMutation({
    mutationFn: async (vars: SendAgentMessageVariables) => {
      const queue = async () => {
        const queuedAt = Date.now()
        const queuedId = `queued-${queuedAt}-${Math.random().toString(36).slice(2)}`
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
        } catch (error) {
          queryClient.setQueryData<AgentThread>(
            agentThreadKeys.detail(threadId),
            (prev) => (prev ? removeQueuedMessage(prev, queuedId) : prev)
          )
          throw error
        }
      }

      if (stream.isLoading) {
        await queue()
        return
      }

      try {
        await queue()
        return
      } catch (error) {
        if (!(error instanceof AgentsApiError) || error.status !== 409) {
          throw error
        }
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

      // Don't await: `stream.submit` resolves only when the run *finishes*, so
      // awaiting would keep the mutation `isPending` (and the prompt bar
      // disabled) for the entire run, blocking the user from queueing a
      // follow-up while it streams.
      void stream
        .submit(
          { messages: [{ type: "human", content: messageContent(vars) }] },
          { config }
        )
        .catch(() => {
          // The run failed to start (e.g. expired OAuth token → 401, or a
          // 409 active-run race), but `onSuccess` already optimistically set
          // `status: "running"`. Surface the failure and clear the busy state
          // instead of leaving the thread falsely running.
          queryClient.setQueryData(agentThreadKeys.detail(threadId), (prev) =>
            prev ? { ...prev, status: "error" as const } : prev
          )
          invalidateAgentThreadLists(queryClient)
        })
    },
    onSuccess: () => {
      queryClient.setQueryData(agentThreadKeys.detail(threadId), (prev) =>
        prev ? { ...prev, status: "running" as const } : prev
      )
      invalidateAgentThreadLists(queryClient)
    },
  })
}
