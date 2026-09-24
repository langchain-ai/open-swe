import { rangeLabel } from "@/features/reviews/lib/chatDiffActions"
import { useChatDrafts } from "@/features/reviews/lib/chatDrafts"
import { usePendingReview } from "@/features/reviews/lib/usePendingReview"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Textarea } from "@/components/ui/textarea"

/** A line comment the chat drafted; the user adds it to their pending review or discards it. */
export function ProposedCommentCard({
  owner,
  repo,
  number,
  id,
  onShow,
}: {
  owner: string
  repo: string
  number: number
  id: string
  /** Scrolls the diff to the comment's lines; omitted when already shown there. */
  onShow?: () => void
}) {
  const drafts = useChatDrafts()
  const draft = drafts?.comments.find((item) => item.proposal.id === id)
  const pending = usePendingReview(owner, repo, number)
  const post = pending.add
  const addToReview = () => {
    if (!draft) return
    const { range } = draft.proposal
    const multiLine = range.startLine < range.endLine
    post.mutate(
      {
        path: range.file,
        line: range.endLine,
        side: range.side,
        body: draft.body.trim(),
        start_line: multiLine ? range.startLine : null,
        start_side: multiLine ? range.side : null,
      },
      { onSuccess: () => drafts?.settle(id, { state: "added" }) }
    )
  }
  if (!drafts || !draft) return null

  const { outcome, body } = draft
  const { range } = draft.proposal
  const location = `${range.file}:${rangeLabel(range)}`
  return (
    <Card size="sm" className="w-full shrink-0" data-testid="proposed-comment">
      <CardHeader>
        <CardTitle>
          {outcome?.state === "added" || outcome?.state === "posted"
            ? "Added to your review"
            : outcome?.state === "discarded"
              ? "Comment discarded"
              : "Draft review comment"}
        </CardTitle>
        <CardDescription>
          {onShow ? (
            <button
              type="button"
              onClick={onShow}
              className="truncate font-mono underline-offset-2 hover:underline"
            >
              {location}
            </button>
          ) : (
            <span className="font-mono">{location}</span>
          )}
        </CardDescription>
      </CardHeader>
      <CardContent>
        {outcome ? (
          <p className="line-clamp-3 whitespace-pre-wrap text-muted-foreground">
            {body}
          </p>
        ) : (
          <Textarea
            aria-label="Comment body"
            value={body}
            onChange={(event) => drafts.edit(id, { body: event.target.value })}
            rows={4}
            disabled={post.isPending}
          />
        )}
      </CardContent>
      <CardFooter className="justify-end gap-2">
        {outcome ? null : (
          <>
            <Button
              size="sm"
              variant="ghost"
              disabled={post.isPending}
              onClick={() => drafts.settle(id, { state: "discarded" })}
            >
              Discard
            </Button>
            <Button
              size="sm"
              disabled={post.isPending || !body.trim()}
              onClick={addToReview}
            >
              {post.isPending ? "Adding…" : "Add to review"}
            </Button>
          </>
        )}
      </CardFooter>
    </Card>
  )
}
