import { Badge } from "@langchain/macaw-components/Badge"
import { Button } from "@langchain/macaw-components/Button"
import { Textarea } from "@langchain/macaw-components/Textarea"
import { useRef } from "react"

import { SubmitShortcut } from "@/components/SubmitShortcut"
import type { PendingReviewComment } from "@/lib/api"
import { Markdown } from "@/features/agents/components/chat/Markdown"
import { usePendingReview } from "@/features/reviews/lib/usePendingReview"

/**
 * A comment in the viewer's pending review, shown on its line until the review
 * is submitted. The owner keeps the edit, so it outlives this card unmounting.
 */
export function PendingReviewCommentCard({
  owner,
  repo,
  number,
  comment,
  edit,
  onEdit,
}: {
  owner: string
  repo: string
  number: number
  comment: PendingReviewComment
  /** The text being edited, or null when not editing. */
  edit: string | null
  onEdit: (text: string | null) => void
}) {
  const pending = usePendingReview(owner, repo, number)
  // Only the click on Edit takes focus; remounting mid-edit doesn't.
  const focusNext = useRef(false)
  const busy = pending.update.isPending || pending.remove.isPending
  const save = () => {
    const next = edit?.trim()
    if (!next || busy) return
    pending.update.mutate(
      { id: comment.id, body: next },
      { onSuccess: () => onEdit(null) }
    )
  }
  return (
    <div
      className="px-space-2 py-space-1 font-sans"
      data-testid="pending-review-comment"
    >
      <div className="rounded-md border border-default bg-surface-level-1 px-space-3 py-space-2 text-xs">
        <div className="mb-space-1 flex items-center gap-space-2">
          <Badge size="sm" color="warning">
            Pending
          </Badge>
          <div className="ml-auto flex items-center gap-space-1">
            {edit === null && (
              <Button
                size="xs"
                color="secondary"
                variant="plain"
                disabled={busy}
                onClick={() => {
                  focusNext.current = true
                  onEdit(comment.body)
                }}
              >
                Edit
              </Button>
            )}
            <Button
              size="xs"
              color="secondary"
              variant="plain"
              disabled={busy}
              onClick={() => pending.remove.mutate(comment.id)}
            >
              Delete
            </Button>
          </div>
        </div>
        {edit !== null ? (
          <>
            <Textarea
              ref={(node) => {
                if (!node || !focusNext.current) return
                focusNext.current = false
                node.focus({ preventScroll: true })
              }}
              size="md"
              aria-label="Pending comment body"
              value={edit}
              onChange={onEdit}
              onKeyDown={(event) => {
                if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
                  event.preventDefault()
                  save()
                } else if (event.key === "Escape") {
                  onEdit(null)
                }
              }}
              rows={3}
            />
            <div className="mt-space-2 flex justify-end gap-space-2">
              <Button
                size="xs"
                color="secondary"
                variant="outlined"
                disabled={busy}
                onClick={() => onEdit(null)}
              >
                Cancel
              </Button>
              <Button
                size="xs"
                aria-label={pending.update.isPending ? "Saving…" : "Save"}
                aria-keyshortcuts="Meta+Enter Control+Enter"
                disabled={busy || !edit.trim()}
                onClick={save}
              >
                {pending.update.isPending ? "Saving…" : "Save"}
                {!busy && <SubmitShortcut />}
              </Button>
            </div>
          </>
        ) : (
          <Markdown content={comment.body} />
        )}
      </div>
    </div>
  )
}
