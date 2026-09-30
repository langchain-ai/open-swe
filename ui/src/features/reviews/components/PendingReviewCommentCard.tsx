import { useState } from "react"

import type { PendingReviewComment } from "@/lib/api"
import { Markdown } from "@/features/agents/components/chat/Markdown"
import { usePendingReview } from "@/features/reviews/lib/usePendingReview"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"

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
    <div className="px-2 py-1 font-sans" data-testid="pending-review-comment">
      <div className="rounded-md border border-border bg-card px-3 py-2 text-xs">
        <div className="mb-1.5 flex items-center gap-2">
          <Badge variant="outline">Pending</Badge>
          <div className="ml-auto flex items-center gap-1">
            {!editing && (
              <Button
                size="sm"
                variant="ghost"
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
              size="sm"
              variant="ghost"
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
              aria-label="Pending comment body"
              value={body}
              onChange={(event) => setBody(event.target.value)}
              onKeyDown={(event) => {
                if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
                  event.preventDefault()
                  save()
                } else if (event.key === "Escape") {
                  setEditing(false)
                }
              }}
              rows={3}
              className="resize-y text-xs"
              autoFocus
            />
            <div className="mt-2 flex justify-end gap-2">
              <Button
                size="sm"
                variant="outline"
                disabled={busy}
                onClick={() => setEditing(false)}
              >
                Cancel
              </Button>
              <Button size="sm" disabled={busy || !body.trim()} onClick={save}>
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
