import {
  createContext,
  useContext,
  useMemo,
  useRef,
  useEffect,
  useState,
} from "react"
import { useQuery } from "@tanstack/react-query"
import type { CodeExcerpt } from "@/features/agents/utils/codeExcerpt"
import { AgentThreadPage } from "@/features/agents/components/AgentThreadPage"
import { reviewChatQuery } from "@/features/agents/lib/queries"
import { Skeleton } from "@langchain/macaw-components/Skeleton"
import { ReviewChatActionsContext } from "@/features/reviews/components/ReviewChatActions"
import type { DiffRange } from "@/features/reviews/lib/chatDiffActions"
import { ChatDraftsProvider } from "@/features/reviews/lib/chatDrafts"

export interface ChatAttachment extends CodeExcerpt {
  id: string
}

interface ReviewChatComposer {
  addAttachment: (attachment: ChatAttachment) => void
  registerSink: (fn: ((attachment: ChatAttachment) => void) | null) => void
  /** Scrolls the diff column to `range` and pulses it. */
  showInDiff: (range: DiffRange) => void
  registerShowHandler: (fn: ((range: DiffRange) => void) | null) => void
}

const ReviewChatComposerContext = createContext<ReviewChatComposer | null>(null)

export function ReviewChatComposerProvider({
  children,
}: {
  children: React.ReactNode
}) {
  const sinkRef = useRef<((attachment: ChatAttachment) => void) | null>(null)
  const pendingRef = useRef<Array<ChatAttachment>>([])
  const showRef = useRef<((range: DiffRange) => void) | null>(null)
  const value = useMemo<ReviewChatComposer>(
    () => ({
      showInDiff: (range) => showRef.current?.(range),
      registerShowHandler: (fn) => {
        showRef.current = fn
      },
      addAttachment: (attachment) => {
        if (sinkRef.current) sinkRef.current(attachment)
        else pendingRef.current.push(attachment)
      },
      registerSink: (fn) => {
        sinkRef.current = fn
        if (fn && pendingRef.current.length > 0) {
          for (const attachment of pendingRef.current) fn(attachment)
          pendingRef.current = []
        }
      },
    }),
    []
  )
  return (
    <ReviewChatComposerContext.Provider value={value}>
      <ChatDraftsProvider>{children}</ChatDraftsProvider>
    </ReviewChatComposerContext.Provider>
  )
}

export function useReviewChatComposer(): ReviewChatComposer | null {
  return useContext(ReviewChatComposerContext)
}

export function ReviewChat({
  owner,
  repo,
  number,
}: {
  owner: string
  repo: string
  number: number
  reviewed: boolean
}) {
  const composer = useReviewChatComposer()
  const [composerDraft, setComposerDraft] = useState<{
    key: number
    text: string
  }>()
  const reviewActions = useMemo(
    () => ({
      owner,
      repo,
      number,
      showInDiff: (range: DiffRange) => composer?.showInDiff(range),
    }),
    [owner, repo, number, composer]
  )
  useEffect(() => {
    composer?.registerSink((attachment) =>
      setComposerDraft((previous) => ({
        key: (previous?.key ?? 0) + 1,
        text: `\`${attachment.path}:${attachment.lineLabel}\`\n\`\`\`${attachment.language}\n${attachment.snippet}\n\`\`\``.trim(),
      }))
    )
    return () => composer?.registerSink(null)
  }, [composer])
  const meta = useQuery(reviewChatQuery({ owner, repo, number }))
  if (meta.isPending) return <Skeleton className="h-40 w-full" />
  if (meta.isError || !meta.data.available)
    return <p>Chat is unavailable right now. Reload the page to try again.</p>
  return (
    <ReviewChatActionsContext.Provider value={reviewActions}>
      <AgentThreadPage
        threadId={meta.data.thread_id}
        composerDraft={composerDraft}
      />
    </ReviewChatActionsContext.Provider>
  )
}
