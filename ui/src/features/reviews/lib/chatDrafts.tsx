import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
} from "react"

import type {
  ProposedComment,
  ProposedReview,
  ReviewEvent,
} from "@/features/reviews/lib/chatDiffActions"

export type DraftOutcome =
  | { state: "posted"; url: string }
  | { state: "discarded" }

export interface CommentDraft {
  proposal: ProposedComment
  body: string
  outcome: DraftOutcome | null
}

export interface ReviewDraft {
  proposal: ProposedReview
  event: ReviewEvent
  body: string
  outcome: DraftOutcome | null
}

interface ChatDrafts {
  comments: ReadonlyArray<CommentDraft>
  reviews: ReadonlyArray<ReviewDraft>
  register: (proposal: ProposedComment | ProposedReview) => void
  edit: (id: string, change: { body?: string; event?: ReviewEvent }) => void
  settle: (id: string, outcome: DraftOutcome) => void
}

const ChatDraftsContext = createContext<ChatDrafts | null>(null)

function storageKey(id: string): string {
  return `review-chat-comment:${id}`
}

function readOutcome(id: string): DraftOutcome | null {
  try {
    const raw = window.localStorage.getItem(storageKey(id))
    return raw ? (JSON.parse(raw) as DraftOutcome) : null
  } catch (error) {
    console.warn("Could not read a chat draft outcome", error)
    return null
  }
}

function writeOutcome(id: string, outcome: DraftOutcome) {
  try {
    window.localStorage.setItem(storageKey(id), JSON.stringify(outcome))
  } catch (error) {
    console.warn("Could not save a chat draft outcome", error)
  }
}

/** Chat-drafted comments and reviews, shared by the chat and the diff so both show one draft. */
export function ChatDraftsProvider({
  children,
}: {
  children: React.ReactNode
}) {
  const [comments, setComments] = useState<Record<string, CommentDraft>>({})
  const [reviews, setReviews] = useState<Record<string, ReviewDraft>>({})

  const register = useCallback((proposal: ProposedComment | ProposedReview) => {
    if (proposal.kind === "comment") {
      setComments((prev) =>
        prev[proposal.id]
          ? prev
          : {
              ...prev,
              [proposal.id]: {
                proposal,
                body: proposal.body,
                outcome: readOutcome(proposal.id),
              },
            }
      )
      return
    }
    setReviews((prev) =>
      prev[proposal.id]
        ? prev
        : {
            ...prev,
            [proposal.id]: {
              proposal,
              event: proposal.event,
              body: proposal.body,
              outcome: readOutcome(proposal.id),
            },
          }
    )
  }, [])

  const edit = useCallback(
    (id: string, change: { body?: string; event?: ReviewEvent }) => {
      setComments((prev) =>
        prev[id] && change.body !== undefined
          ? { ...prev, [id]: { ...prev[id], body: change.body } }
          : prev
      )
      setReviews((prev) =>
        prev[id] ? { ...prev, [id]: { ...prev[id], ...change } } : prev
      )
    },
    []
  )

  const settle = useCallback((id: string, outcome: DraftOutcome) => {
    writeOutcome(id, outcome)
    setComments((prev) =>
      prev[id] ? { ...prev, [id]: { ...prev[id], outcome } } : prev
    )
    setReviews((prev) =>
      prev[id] ? { ...prev, [id]: { ...prev[id], outcome } } : prev
    )
  }, [])

  const value = useMemo<ChatDrafts>(
    () => ({
      comments: Object.values(comments),
      reviews: Object.values(reviews),
      register,
      edit,
      settle,
    }),
    [comments, reviews, register, edit, settle]
  )
  return (
    <ChatDraftsContext.Provider value={value}>
      {children}
    </ChatDraftsContext.Provider>
  )
}

export function useChatDrafts(): ChatDrafts | null {
  return useContext(ChatDraftsContext)
}
