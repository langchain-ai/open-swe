import { useContext } from "react"
import { EmbeddedThreadContext } from "@/features/agents/lib/embeddedThread"
import { useThreadRunConfig } from "@/features/agents/lib/threadRunConfig"
import { ReviewChatActionsContext } from "@/features/reviews/components/ReviewChatActions"
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
import { usePageToolBridge } from "@/features/agents/lib/pageToolBridge"
import { modelConfigurable } from "@/features/agents/lib/stream/promptMessage"
import { reportError } from "@/lib/errorReporting"
import { productAnalytics, type ProductAction } from "@/lib/analytics"

export interface SubmitAgentMessageVariables extends SendAgentMessageVariables {
  /** The stream can reject after the optimistic mutation has already resolved. */
  onStartError?: () => void
  analyticsActorId?: string | null
  analyticsAction?: ProductAction
}

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
 * transcript or the queue. A rejected start marks that row failed.
 */
export function useSubmitAgentMessage(threadId: string) {
  const queryClient = useQueryClient()
  const source = useThreadSource()
  const review = useContext(ReviewChatActionsContext)
  const embedded = useContext(EmbeddedThreadContext)
  const pageTools = usePageToolBridge()

  return useMutation({
    meta: { errorTitle: "Couldn't send message" },
    mutationFn: async (vars: SubmitAgentMessageVariables) => {
      const action =
        vars.analyticsAction ??
        productAnalytics.beginAction(
          vars.content.trim() === "/offload"
            ? "offload"
            : vars.enqueue
              ? "queue"
              : source.isRunning
                ? "steer"
                : "new_turn",
          vars.analyticsActorId
        )
      if (vars.content.trim() === "/offload") {
        const error = source.isRunning
          ? new Error("Wait for the current run to finish before offloading.")
          : vars.images?.length
            ? new Error("Offloading does not accept attachments.")
            : null
        if (error) {
          action.failed(error)
          throw error
        }
      }
      try {
        if (vars.content.trim() === "/offload") {
          setAgentThreadStatus(queryClient, threadId, "running")
          void source
            .startRun({
              configurable: { offload_conversation: true },
              onAccepted: () => action.accepted(),
              onRejected: (error: unknown) => action.failed(error),
            })
            .catch((error: unknown) => {
              action.failed(error)
              setAgentThreadStatus(queryClient, threadId, "error")
            })
          return
        }
        productAnalytics.track(
          "agents_message_submitted",
          {
            mode: vars.enqueue
              ? "queue"
              : source.isRunning
                ? "steer"
                : "new_turn",
            has_attachments: Boolean(vars.images?.length),
          },
          vars.analyticsActorId
        )
        const id = vars.client_message_id ?? crypto.randomUUID()
        const pendingMessage: PendingThreadMessage = {
          id,
          content: vars.content.trim(),
          images: vars.images,
          ...(vars.replies?.length ? { replies: vars.replies } : {}),
          createdAt: Date.now(),
          status: "sending",
          ...(vars.enqueue ? { queued: true } : {}),
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

        const configurable =
          vars.inherit_model_selection && !vars.model_selection_changed
            ? {}
            : modelConfigurable(
                { modelId: vars.model_id, effort: vars.effort },
                vars.model_selection_changed
              )
        const surface = useThreadRunConfig.getState().byThread[threadId]
        const [offered] = await Promise.all([
          pageTools.offer(),
          embedded?.run?.prepare?.(),
          surface?.prepare?.(),
        ])
        // Robin queues commands for this tab's page from this message on.
        if (offered.length) pageTools.sent()

        void source
          .startRun({
            onAccepted: () => action.accepted(),
            onRejected: (error: unknown) => action.failed(error),
            message: {
              id,
              text: vars.content,
              images: vars.images,
              replies: vars.replies,
            },
            configurable: {
              ...configurable,
              ...(offered.length
                ? { page_tools: offered, page_session: pageTools.session }
                : {}),
              ...embedded?.run?.configurable,
              ...surface?.configurable,
              ...(review
                ? {
                    review_chat_pr_url: `https://github.com/${review.owner}/${review.repo}/pull/${review.number}`,
                  }
                : {}),
            },
            ...(vars.enqueue ? { enqueue: true } : {}),
          })
          .catch((error: unknown) => {
            action.failed(error)
            const errorId = reportError({
              title: "Couldn't send message",
              error,
            })
            updateThread((thread) =>
              setPendingMessage(thread, {
                ...pendingMessage,
                status: "failed",
                error: `${describeSendError(error)} · ID ${errorId}`,
                // A failed enqueue never reaches the SDK's queue, so this
                // must fall back into the normal timeline instead of
                // rendering as a permanently pending queued row.
                queued: false,
              })
            )
            setAgentThreadStatus(queryClient, threadId, "error")
            vars.onStartError?.()
          })
      } catch (error) {
        action.failed(error)
        throw error
      }
    },
  })
}
