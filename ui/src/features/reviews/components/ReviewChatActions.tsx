import { createContext, useContext, useEffect, useMemo, useState } from "react"
import type { ReactNode } from "react"
import type { Message } from "@/features/agents/lib/types"
import { chatDiffAction } from "@/features/reviews/lib/chatDiffActions"
import type { DiffRange } from "@/features/reviews/lib/chatDiffActions"
import { useChatDrafts } from "@/features/reviews/lib/chatDrafts"
import { ProposedCommentCard } from "@/features/reviews/components/ProposedCommentCard"
import { ProposedReviewCard } from "@/features/reviews/components/ProposedReviewCard"

export const ReviewChatActionsContext = createContext<{
  owner: string
  repo: string
  number: number
  showInDiff: (range: DiffRange) => void
  /** What an empty chat shows: ways in, specific to this pull request. */
  emptyState?: ReactNode
} | null>(null)

export function ReviewChatActions({ messages }: { messages: Array<Message> }) {
  const review = useContext(ReviewChatActionsContext)
  const drafts = useChatDrafts()
  const register = drafts?.register
  const [outputs, setOutputs] = useState<Record<string, string>>({})
  useEffect(() => {
    let cancelled = false
    for (const message of messages) {
      for (const chunk of message.chunks) {
        if (chunk.kind !== "tool-execution" || !chunk.loadOutput) continue
        void chunk
          .loadOutput()
          .then((output) => {
            if (!cancelled)
              setOutputs((previous) => ({
                ...previous,
                [chunk.toolCallId]: output,
              }))
          })
          .catch((error: unknown) =>
            console.error("Could not load review draft", error)
          )
      }
    }
    return () => {
      cancelled = true
    }
  }, [messages])
  const actions = useMemo(
    () =>
      review
        ? messages.flatMap((message) =>
            message.chunks.flatMap((chunk) => {
              if (chunk.kind !== "tool-execution") return []
              const action = chatDiffAction({
                type: "tool",
                name: chunk.title.toLowerCase().replaceAll(" ", "_"),
                tool_call_id: chunk.toolCallId,
                content:
                  outputs[chunk.toolCallId] ??
                  (chunk.loadOutput ? undefined : chunk.output),
              })
              return action ? [action] : []
            })
          )
        : [],
    [messages, review, outputs]
  )
  useEffect(() => {
    for (const action of actions) register?.(action)
  }, [actions, register])
  if (!review) return null
  return actions.map((action) =>
    action.kind === "comment" ? (
      <ProposedCommentCard
        key={action.id}
        owner={review.owner}
        repo={review.repo}
        number={review.number}
        id={action.id}
        onShow={() => review.showInDiff(action.range)}
      />
    ) : (
      <ProposedReviewCard
        key={action.id}
        owner={review.owner}
        repo={review.repo}
        number={review.number}
        id={action.id}
      />
    )
  )
}
