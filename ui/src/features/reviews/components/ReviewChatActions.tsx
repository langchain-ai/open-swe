import { Button } from "@langchain/macaw-components/Button"
import { XIcon } from "@langchain/macaw-components/icons"
import { IconButton } from "@langchain/macaw-components/IconButton"
import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react"
import type { ReactNode } from "react"
import { FileCodeIcon } from "@phosphor-icons/react/dist/ssr/FileCode"
import type { Message, ToolExecutionChunk } from "@/features/agents/lib/types"
import type { CodeExcerpt } from "@/features/agents/utils/codeExcerpt"
import {
  chatDiffAction,
  isChatDiffActionTool,
  rangeLabel,
} from "@/features/reviews/lib/chatDiffActions"
import type { DiffRange } from "@/features/reviews/lib/chatDiffActions"
import { useChatDrafts } from "@/features/reviews/lib/chatDrafts"
import { usePendingReview } from "@/features/reviews/lib/usePendingReview"
import { sameLogin } from "@/features/reviews/lib/logins"
import { useSession } from "@/lib/session"
import { AgentNoteCard } from "@/features/reviews/components/AgentNoteCard"
import { ProposedCommentCard } from "@/features/reviews/components/ProposedCommentCard"
import { ProposedReviewCard } from "@/features/reviews/components/ProposedReviewCard"

interface ReviewChatActionsValue {
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
  /** A question to send as soon as the chat can. */
  question?: string
  clearQuestion?: () => void
}

export const ReviewChatActionsContext =
  createContext<ReviewChatActionsValue | null>(null)

export function ReviewChatActions({ messages }: { messages: Array<Message> }) {
  const review = useContext(ReviewChatActionsContext)
  return review ? (
    <ReviewChatActionList review={review} messages={messages} />
  ) : null
}

function chatToolName(chunk: ToolExecutionChunk): string {
  return chunk.title.toLowerCase().replaceAll(" ", "_")
}

function actionToolCalls(messages: Array<Message>): Array<ToolExecutionChunk> {
  return messages.flatMap((message) =>
    message.chunks.filter(
      (chunk): chunk is ToolExecutionChunk =>
        chunk.kind === "tool-execution" &&
        isChatDiffActionTool(chatToolName(chunk))
    )
  )
}

/**
 * The full results of the action tools whose transcript holds only a preview.
 * Each is fetched once, not on every streamed event; a failed fetch is retried.
 */
function useActionToolOutputs(
  calls: Array<ToolExecutionChunk>
): Record<string, string> {
  const [outputs, setOutputs] = useState<Record<string, string>>({})
  const requested = useRef(new Set<string>())
  useEffect(() => {
    for (const { toolCallId, loadOutput } of calls) {
      if (!loadOutput || requested.current.has(toolCallId)) continue
      requested.current.add(toolCallId)
      void loadOutput().then(
        (output) =>
          setOutputs((previous) => ({ ...previous, [toolCallId]: output })),
        (error: unknown) => {
          requested.current.delete(toolCallId)
          console.error("Could not load review draft", error)
        }
      )
    }
  }, [calls])
  return outputs
}

function ReviewChatActionList({
  review,
  messages,
}: {
  review: ReviewChatActionsValue
  messages: Array<Message>
}) {
  const drafts = useChatDrafts()
  const register = drafts?.register
  const calls = useMemo(() => actionToolCalls(messages), [messages])
  const outputs = useActionToolOutputs(calls)
  const actions = useMemo(
    () =>
      calls.flatMap((chunk) => {
        const action = chatDiffAction({
          type: "tool",
          name: chatToolName(chunk),
          tool_call_id: chunk.toolCallId,
          content:
            outputs[chunk.toolCallId] ??
            (chunk.loadOutput ? undefined : chunk.output),
        })
        return action ? [action] : []
      }),
    [calls, outputs]
  )
  useEffect(() => {
    for (const action of actions)
      if (action.kind !== "added") register?.(action)
  }, [actions, register])
  // A comment the agent added for the viewer sits in their pending review, which the page reads once.
  const pending = usePendingReview(review.owner, review.repo, review.number)
  const viewer = useSession().data?.login
  const invalidatePending = pending.invalidate
  const refreshedFor = useRef(new Set<string>())
  useEffect(() => {
    const added = actions.filter(
      (action) =>
        action.kind === "added" &&
        sameLogin(action.login, viewer) &&
        !refreshedFor.current.has(action.id)
    )
    if (added.length === 0) return
    for (const action of added) refreshedFor.current.add(action.id)
    void invalidatePending()
  }, [actions, viewer, invalidatePending])
  return actions.map((action) =>
    action.kind === "note" ? (
      <AgentNoteCard
        key={action.id}
        id={action.id}
        onShow={() => review.showInDiff(action.range)}
      />
    ) : action.kind === "added" ? (
      <p key={action.id} className="text-xs text-secondary">
        Added to{" "}
        {sameLogin(action.login, viewer) ? "your" : `@${action.login}'s`}{" "}
        pending review on{" "}
        <Button
          color="secondary"
          variant="plain"
          size="sm"
          aria-label={`${action.range.file}:${rangeLabel(action.range)}`}
          type="button"
          onClick={() => review.showInDiff(action.range)}
          className="h-auto border-0 px-0 font-mono underline-offset-2 hover:underline"
        >
          {action.range.file}:{rangeLabel(action.range)}
        </Button>
      </p>
    ) : action.kind === "comment" ? (
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
      className="flex flex-wrap gap-space-2 px-space-1 pb-space-1"
    >
      {excerpts.map((excerpt, index) => (
        <li
          key={`${excerpt.path}:${excerpt.lineLabel}`}
          title={excerpt.snippet}
          className="flex max-w-full items-center gap-space-2 rounded-md border border-default bg-surface-level-2 py-0.5 pr-0.5 pl-space-2 text-xxs"
        >
          <FileCodeIcon
            className="size-3.5 shrink-0 text-secondary"
            weight="regular"
          />
          <span className="min-w-0 truncate font-mono">
            {excerpt.path.split("/").pop()}
          </span>
          <span className="shrink-0 text-secondary">
            {excerptLines(excerpt.lineLabel)}
          </span>
          <IconButton
            icon={XIcon}
            label={`Remove ${excerpt.path} ${excerptLines(excerpt.lineLabel)}`}
            size="xs"
            variant="plain"
            color="secondary"
            className="shrink-0"
            onClick={() => review?.removeExcerpt?.(index)}
          />
        </li>
      ))}
    </ul>
  )
}
