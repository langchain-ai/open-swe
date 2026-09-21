import { useMutation, useQueryClient } from "@tanstack/react-query"

import type { SendAgentMessageVariables } from "@/features/agents/lib/queries"
import type {
  AgentThread,
  PendingThreadMessage,
} from "@/features/agents/lib/types"
import { AgentsApiError } from "@/features/agents/lib/api"
import {
  agentThreadKeys,
  setAgentThreadStatus,
} from "@/features/agents/lib/queries"
import { useThreadSource } from "@/features/agents/lib/threadSource/ThreadSourceProvider"
import { modelConfigurable } from "@/features/agents/lib/stream/promptMessage"

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

function removePendingMessage(thread: AgentThread, id: string): AgentThread {
  return {
    ...thread,
    pendingMessages: thread.pendingMessages?.filter(
      (message) => message.id !== id
    ),
  }
}

/** Human-readable reason a send failed, shown under the failed bubble. */
export function describeSendError(error: unknown): string {
  if (error instanceof AgentsApiError) {
    return error.message
      ? `${error.status} ${error.message}`
      : `${error.status}`
  }
  if (error instanceof Error) return error.message || error.name
  return String(error)
}

/**
 * Send a user message as a `run.start`. The server starts a run on an idle
 * thread and steers the live run otherwise, so the client never has to know
 * which; holding a message back until a boundary is the queue's job.
 *
 * The start is not awaited: the SDK stream's promise settles only when the
 * run ends. The optimistic row stands in until the message shows up in the
 * transcript. A rejected start marks that row failed, or hands the message
 * back through `onFailure` when the caller wants to keep it.
 */
export function useSubmitAgentMessage(threadId: string) {
  const queryClient = useQueryClient()
  const source = useThreadSource()

  return useMutation({
    mutationFn: async (vars: SendAgentMessageVariables) => {
      if (vars.content.trim() === "/offload") {
        if (source.isRunning) {
          throw new Error(
            "Wait for the current run to finish before offloading."
          )
        }
        if (vars.images?.length) {
          throw new Error("Offloading does not accept attachments.")
        }
        setAgentThreadStatus(queryClient, threadId, "running")
        void source
          .startRun({ configurable: { offload_conversation: true } })
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
      // Set before the start is fired: a rejection below flips it to error and
      // nothing may flip it back afterwards.
      setAgentThreadStatus(queryClient, threadId, "running")

      const configurable: Record<string, unknown> = modelConfigurable({
        modelId: vars.model_id,
        effort: vars.effort,
      })
      if (vars.plan_mode) configurable.plan_mode = true

      const onFailure = vars.onFailure
      void source
        .startRun({
          message: { id, text: vars.content, images: vars.images },
          configurable,
        })
        .catch((error: unknown) => {
          if (onFailure) {
            updateThread((thread) => removePendingMessage(thread, id))
            onFailure()
            return
          }
          updateThread((thread) =>
            setPendingMessage(thread, {
              ...pendingMessage,
              status: "failed",
              error: describeSendError(error),
            })
          )
          setAgentThreadStatus(queryClient, threadId, "error")
        })
    },
  })
}
