import { useCallback, useEffect, useRef } from "react"
import { toast } from "sonner"

import type { FollowUpBehavior } from "@/lib/api"
import type { PullRequestRef } from "@/features/reviews/lib/reviewKeys"
import {
  reviewSessionKey,
  useReviewSessions,
  type Sighting,
} from "@/features/reviews/lib/reviewSession"

/** What the review chat offers a review session's handoff. */
interface HandoffChat {
  pr: PullRequestRef | null
  canPost: boolean
  /** Where the chat has the message, or null until it has loaded enough to say. */
  sighting: (messageId: string) => Sighting | null
  /** Sends as the follow-up `delivery` asks for, resolving once the chat shows it as sending. */
  send: (
    text: string,
    messageId: string,
    delivery: FollowUpBehavior
  ) => Promise<void>
  /** Withdraws the message from the queue behind the current run. */
  withdraw: (messageId: string) => Promise<void>
}

/**
 * Carries a review session's finished batch into the chat, and follows it
 * there until the agent has it, so the review page can say whether it is on
 * its way, scheduled behind the current run, or being worked on. A batch that
 * can't be delivered, or that the reviewer takes back, goes back to the
 * session's drafts.
 */
export function useReviewSessionHandoff(chat: HandoffChat): {
  owns: (messageId: string) => boolean
  /**
   * Cancels the batch with `cancel`, which says whether it went through, and
   * returns its comments to the drafts if so. Only an explicit withdrawal
   * returns them: a batch merely missing from the chat may still reach the agent.
   */
  takeBack: (
    messageId: string,
    cancel: () => Promise<boolean>
  ) => Promise<boolean>
} {
  const { pr, canPost, sighting, send, withdraw } = chat
  const key = pr ? reviewSessionKey(pr) : null
  const handoff = useReviewSessions((state) =>
    key ? (state.sessions[key]?.handoff ?? null) : null
  )
  const busy = useRef(new Set<string>())
  // Sent from this page but not yet shown by the chat, whose cache catches up a render later.
  const unseen = useRef(new Set<string>())
  const seen = handoff ? sighting(handoff.messageId) : null

  const takeBack = useCallback(
    async (id: string, cancel: () => Promise<boolean>) => {
      if (!key) return cancel()
      // Held while it cancels, so the batch leaving the queue isn't read as anything else.
      busy.current.add(id)
      let cancelled = false
      try {
        cancelled = await cancel()
      } finally {
        busy.current.delete(id)
      }
      if (!cancelled) return false
      useReviewSessions.getState().restore(key, id, null)
      toast("Your comments are back in your review session drafts")
      return true
    },
    [key]
  )

  useEffect(() => {
    if (!key || !handoff) return
    const store = useReviewSessions.getState()
    const id = handoff.messageId
    if (busy.current.has(id)) return
    // Each settles the store itself: the effect may not run again once it's done.
    if (handoff.status === "sending") {
      if (!canPost) {
        store.restore(
          key,
          id,
          "You can't post in this pull request's chat, so nothing was sent; your drafts are kept."
        )
        return
      }
      busy.current.add(id)
      send(handoff.text, id, handoff.delivery).then(
        () => {
          busy.current.delete(id)
          unseen.current.add(id)
          store.dispatched(key, id)
        },
        (error: unknown) => {
          busy.current.delete(id)
          console.error("Could not send the review session", error)
          store.restore(
            key,
            id,
            `Couldn't send: ${error instanceof Error ? error.message : String(error)}. Nothing was delivered; your drafts are kept.`
          )
        }
      )
      return
    }
    if (handoff.withdrawing && handoff.status === "scheduled") {
      takeBack(id, async () => {
        await withdraw(id)
        return true
      }).catch((error: unknown) => {
        console.error("Could not withdraw the review session", error)
        store.keepScheduled(key, id)
        toast.error(
          "Couldn't take the comments back; they're still scheduled after the current run"
        )
      })
      return
    }
    if (!seen) return
    if (seen.kind !== "missing") unseen.current.delete(id)
    const outcome = store.observe(
      key,
      id,
      seen.kind === "missing" && unseen.current.has(id)
        ? { ...seen, settled: false }
        : seen
    )
    if (outcome === "failed" || outcome === "lost")
      toast.error(
        "Your comments weren't delivered; they're back in your drafts"
      )
  }, [key, handoff, seen, canPost, send, withdraw, takeBack])

  return {
    owns: (messageId) => handoff?.messageId === messageId,
    takeBack,
  }
}
