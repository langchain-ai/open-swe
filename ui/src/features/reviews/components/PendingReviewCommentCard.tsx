import { useState } from "react"

import type { PendingReviewComment } from "@/lib/api"
import { Markdown } from "@/features/agents/components/chat/Markdown"
import { usePendingReview } from "@/features/reviews/lib/usePendingReview"
import { ConfirmableAction } from "@langchain/gtm-platform-design-system/patterns/confirmable-action"
import { Badge } from "@langchain/gtm-platform-design-system/ui/badge"
import {
  Box,
  Inline,
  Stack,
} from "@langchain/gtm-platform-design-system/ui/box"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import { Textarea } from "@langchain/gtm-platform-design-system/ui/textarea"

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
    <Box
      padding="xs"
      className="px-2 font-sans"
      data-testid="pending-review-comment"
    >
      <Stack
        gap="sm"
        bg="panel"
        border="line"
        radius="compact"
        className="px-3 py-2 text-label"
      >
        <Inline gap="sm">
          <Badge tier="notable" tone="attention">
            Pending
          </Badge>
          <Inline gap="xs" className="ml-auto">
            {!editing && (
              <Button
                size="compact"
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
            <ConfirmableAction
              title="Delete this pending comment?"
              description="It is removed from your pending review on GitHub and cannot be recovered."
              confirmLabel="Delete comment"
              onConfirm={() =>
                pending.remove.mutateAsync(comment.id).then(() => undefined)
              }
              trigger={
                <Button size="compact" variant="ghost" disabled={busy}>
                  Delete
                </Button>
              }
            />
          </Inline>
        </Inline>
        {editing ? (
          <Stack gap="sm">
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
              className="resize-y"
              autoFocus
            />
            <Inline gap="sm" justify="end">
              <Button
                size="compact"
                variant="ghost"
                disabled={busy}
                onClick={() => setEditing(false)}
              >
                Cancel
              </Button>
              <Button
                size="compact"
                disabled={busy || !body.trim()}
                loading={pending.update.isPending}
                onClick={save}
              >
                Save
              </Button>
            </Inline>
          </Stack>
        ) : (
          <Markdown content={comment.body} />
        )}
      </Stack>
    </Box>
  )
}
