import { useMutation, useQueryClient } from "@tanstack/react-query"

import type { SendAgentMessageVariables } from "@/features/agents/lib/queries"
import type { AgentThread, PendingThreadMessage } from "@/features/agents/lib/types"
import {
  agentThreadKeys,
  setAgentThreadStatus,
} from "@/features/agents/lib/queries"
import { useAgentStream } from "@/features/agents/lib/stream/AgentStreamProvider"
import {
  modelConfigurable,
  promptMessage,
} from "@/features/agents/lib/stream/promptMessage"

function setPendingMessage(
  thread: AgentThread,
  message: PendingThreadMessage
): AgentThread {
  return {
    ...thread,
    pendingMessages: [
      ...(thread.pendingMessages ?? []).filter(
        (item) => item.id !== message.id
      ),
      message,
    ],
  }
}

/** Submit user messages through the active-run queue or a new stream run. */
export function useSubmitAgentMessage(threadId: string) {
  const queryClient = useQueryClient()
  const stream = useAgentStream()

  return useMutation({
    mutationFn: async (vars: SendAgentMessageVariables) => {
      if (vars.content.trim() === "/offload") {
        if (stream.isLoading) {
          throw new Error(
            "Wait for the current run to finish before offloading."
          )
        }
        if (vars.images?.length) {
          throw new Error("Offloading does not accept attachments.")
        }
        void stream
          .submit(
            {},
            { config: { configurable: { offload_conversation: true } } }
          )
          .catch(() => setAgentThreadStatus(queryClient, threadId, "error"))
        return
      }

      const id = vars.client_message_id ?? crypto.randomUUID()
      const pendingMessage: PendingThreadMessage = {
        id,
        content: vars.content.trim(),
        images: vars.images,
        createdAt: Date.now(),
        status: "sending",
      }
      const updateThread = (update: (thread: AgentThread) => AgentThread) =>
        queryClient.setQueryData<AgentThread>(
          agentThreadKeys.detail(threadId),
          (prev) => (prev ? update(prev) : prev)
        )
      updateThread((thread) => setPendingMessage(thread, pendingMessage))

      const configurable: Record<string, unknown> = modelConfigurable({
        modelId: vars.model_id,
        effort: vars.effort,
      })
      if (vars.plan_mode) configurable.plan_mode = true
      const config =
        Object.keys(configurable).length > 0 ? { configurable } : undefined

      const markFailed = () => {
        updateThread((thread) =>
          setPendingMessage(thread, { ...pendingMessage, status: "failed" })
        )
        setAgentThreadStatus(queryClient, threadId, "error")
      }

      const message = promptMessage(vars.content, vars.images)
      // `submit()` never rejects; failures are routed through `onError`.
      void stream.submit(
        { messages: [{ ...message, id }] },
        { config, multitaskStrategy: "enqueue", onError: markFailed }
      )
    },
    onSuccess: () => {
      setAgentThreadStatus(queryClient, threadId, "running")
    },
  })
}
