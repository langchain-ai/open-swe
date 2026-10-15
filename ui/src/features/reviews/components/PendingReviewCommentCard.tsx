import { Badge } from "@langchain/macaw-components/Badge"
import { Button } from "@langchain/macaw-components/Button"
import { Textarea } from "@langchain/macaw-components/Textarea"
import { useState } from "react"

import type { PendingReviewComment } from "@/lib/api"
import { Markdown } from "@/features/agents/components/chat/Markdown"
import { usePendingReview } from "@/features/reviews/lib/usePendingReview"

/** A comment in the viewer's pending review, shown on its line until the review is submitted. */
export function PendingReviewCommentCard({
  owner,
  repo,
  number,
  comment,
}: {
  owner: string
  repo: string
  number: number
  comment: PendingReviewComment
}) {
  const pending = usePendingReview(owner, repo, number)
  const [editing, setEditing] = useState(false)
  const [body, setBody] = useState(comment.body)
  const busy = pending.update.isPending || pending.remove.isPending
  const save = () => {
    const next = body.trim()
    if (!next || busy) return
    pending.update.mutate(
      { id: comment.id, body: next },
      { onSuccess: () => setEditing(false) }
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
            {!editing && (
              <Button
                size="xs"
                color="secondary"
                variant="plain"
                disabled={busy}
                onClick={() => {
                  setBody(comment.body)
                  setEditing(true)
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
        {editing ? (
          <>
            <Textarea
              size="md"
              aria-label="Pending comment body"
              value={body}
              onChange={setBody}
              onKeyDown={(event) => {
                if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
                  event.preventDefault()
                  save()
                } else if (event.key === "Escape") {
                  setEditing(false)
                }
              }}
              rows={3}
              autoFocus
            />
            <div className="mt-space-2 flex justify-end gap-space-2">
              <Button
                size="xs"
                color="secondary"
                variant="outlined"
                disabled={busy}
                onClick={() => setEditing(false)}
              >
                Cancel
              </Button>
              <Button size="xs" disabled={busy || !body.trim()} onClick={save}>
                {pending.update.isPending ? "Saving…" : "Save"}
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
