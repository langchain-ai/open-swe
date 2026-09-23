import { useMutation, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

import { rangeLabel } from "@/features/reviews/lib/chatDiffActions"
import { useChatDrafts } from "@/features/reviews/lib/chatDrafts"
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
import { api } from "@/lib/api"

/** A line comment the chat drafted; nothing posts until the user confirms. */
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
  const queryClient = useQueryClient()
  const drafts = useChatDrafts()
  const draft = drafts?.comments.find((item) => item.proposal.id === id)
  const post = useMutation({
    mutationFn: async () => {
      if (!draft) throw new Error("The draft is no longer available")
      const { range } = draft.proposal
      const multiLine = range.startLine < range.endLine
      return api.createReviewComment(owner, repo, number, {
        path: range.file,
        line: range.endLine,
        side: range.side,
        body: draft.body.trim(),
        start_line: multiLine ? range.startLine : null,
        start_side: multiLine ? range.side : null,
      })
    },
    onSuccess: (result) => {
      drafts?.settle(id, { state: "posted", url: result.html_url })
      void queryClient.invalidateQueries({
        queryKey: ["reviewComments", owner, repo, number],
      })
    },
    onError: (error) =>
      toast.error("Couldn't post the comment", { description: error.message }),
  })
  if (!drafts || !draft) return null

  const { outcome, body } = draft
  const { range } = draft.proposal
  const location = `${range.file}:${rangeLabel(range)}`
  return (
    <Card size="sm" className="w-full shrink-0" data-testid="proposed-comment">
      <CardHeader>
        <CardTitle>
          {outcome?.state === "posted"
            ? "Comment posted"
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
        {outcome?.state === "posted" ? (
          <Button
            size="sm"
            variant="outline"
            render={
              <a href={outcome.url} target="_blank" rel="noopener noreferrer" />
            }
          >
            View on GitHub
          </Button>
        ) : outcome ? null : (
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
              onClick={() => post.mutate()}
            >
              {post.isPending ? "Posting…" : "Post as you"}
            </Button>
          </>
        )}
      </CardFooter>
    </Card>
  )
}
