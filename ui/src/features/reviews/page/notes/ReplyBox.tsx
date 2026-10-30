import { Button } from "@langchain/macaw-components/Button"
import { Textarea } from "@langchain/macaw-components/Textarea"
import { useRef } from "react"

import { SubmitShortcut } from "@/components/SubmitShortcut"
import { useReviewPage } from "@/features/reviews/page/store"

/**
 * A one-line "Reply…" that grows into a composer, as on GitHub. ⌘↩ sends.
 * The draft lives in the page store by thread, so it outlives the diff
 * scrolling this note away and never lands under another thread.
 */
export function ReplyBox({
  threadId,
  reply,
  placeholder = "Reply…",
}: {
  threadId: number
  reply: {
    isPending: boolean
    mutate: (body: string, options: { onError: () => void }) => void
  }
  placeholder?: string
}) {
  const body = useReviewPage((state) => state.replyDrafts.get(threadId))
  const setDraft = useReviewPage((state) => state.setReplyDraft)
  // Only the click that opens the box takes focus; remounting on scroll doesn't.
  const focusNext = useRef(false)
  const pending = reply.isPending
  const close = () => setDraft(threadId, null)
  const send = () => {
    const text = body?.trim()
    if (!text || pending) return
    // The reply shows in the thread at once; a failed one comes back here to retry.
    close()
    reply.mutate(text, { onError: () => setDraft(threadId, text) })
  }
  if (body === undefined)
    return (
      <Button
        color="secondary"
        variant="plain"
        size="sm"
        aria-label={placeholder}
        type="button"
        onClick={() => {
          focusNext.current = true
          setDraft(threadId, "")
        }}
        className="w-full justify-start border border-default bg-surface-level-1 px-space-2 py-space-1 text-left text-secondary hover:border-strong"
      >
        {placeholder}
      </Button>
    )
  return (
    <div className="flex flex-col gap-space-2">
      <Textarea
        ref={(node) => {
          if (!node || !focusNext.current) return
          focusNext.current = false
          node.focus({ preventScroll: true })
        }}
        size="sm"
        aria-label="Reply"
        rows={3}
        resize="vertical"
        value={body}
        onChange={(text) => setDraft(threadId, text)}
        onKeyDown={(event) => {
          if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
            event.preventDefault()
            send()
          }
          if (event.key === "Escape") {
            event.stopPropagation()
            // A typed reply survives Escape (Cancel drops it).
            if (!body.trim()) close()
          }
        }}
      />
      <div className="flex justify-end gap-space-2">
        <Button size="sm" color="secondary" variant="plain" onClick={close}>
          Cancel
        </Button>
        <Button
          size="sm"
          aria-label="Reply"
          aria-keyshortcuts="Meta+Enter Control+Enter"
          disabled={!body.trim() || pending}
          onClick={send}
        >
          Reply
          {!pending && <SubmitShortcut />}
        </Button>
      </div>
    </div>
  )
}
