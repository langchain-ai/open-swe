import { createContext, useContext, useEffect, useMemo, useState } from "react"
import type { ReactNode } from "react"
import { FileCodeIcon } from "@phosphor-icons/react/dist/ssr/FileCode"
import { XIcon } from "@phosphor-icons/react/dist/ssr/X"
import type { Message } from "@/features/agents/lib/types"
import type { CodeExcerpt } from "@/features/agents/utils/codeExcerpt"
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
  /** Code the reviewer attached to their next message. */
  excerpts?: ReadonlyArray<CodeExcerpt>
  removeExcerpt?: (index: number) => void
  clearExcerpts?: () => void
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

/** "R35-37" → "lines 35–37", with "old" for the deleted side. */
function excerptLines(label: string): string {
  const match = /^([LR])(\d+)(?:-(\d+))?$/.exec(label)
  if (!match) return label
  const [, side, start, end] = match
  const old = side === "L" ? "old " : ""
  return end ? `${old}lines ${start}–${end}` : `${old}line ${start}`
}

/** The code attached to the next message, as chips the reviewer can drop before sending. */
export function ReviewExcerptChips() {
  const review = useContext(ReviewChatActionsContext)
  const excerpts = review?.excerpts ?? []
  if (excerpts.length === 0) return null
  return (
    <ul
      aria-label="Code attached to your next message"
      className="flex flex-wrap gap-1.5 px-1 pb-1.5"
    >
      {excerpts.map((excerpt, index) => (
        <li
          key={`${excerpt.path}:${excerpt.lineLabel}`}
          title={excerpt.snippet}
          className="flex max-w-full items-center gap-1.5 rounded-md border border-default bg-surface-level-2 py-0.5 pr-0.5 pl-2 text-[11px]"
        >
          <FileCodeIcon className="size-3.5 shrink-0 text-secondary" />
          <span className="min-w-0 truncate font-mono">
            {excerpt.path.split("/").pop()}
          </span>
          <span className="shrink-0 text-secondary">
            {excerptLines(excerpt.lineLabel)}
          </span>
          <button
            type="button"
            aria-label={`Remove ${excerpt.path} ${excerptLines(excerpt.lineLabel)}`}
            onClick={() => review?.removeExcerpt?.(index)}
            className="flex size-4 shrink-0 items-center justify-center rounded text-secondary hover:bg-surface-level-1-hover hover:text-primary"
          >
            <XIcon className="size-3" />
          </button>
        </li>
      ))}
    </ul>
  )
}
