import { useMutation, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { toast } from "sonner"

import type { ProposedComment } from "@/features/reviews/lib/chatDiffActions"
import { rangeLabel } from "@/features/reviews/lib/chatDiffActions"
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

type Outcome = { state: "posted"; url: string } | { state: "discarded" }

function storageKey(id: string): string {
  return `review-chat-comment:${id}`
}

function readOutcome(id: string): Outcome | null {
  try {
    const raw = window.localStorage.getItem(storageKey(id))
    return raw ? (JSON.parse(raw) as Outcome) : null
  } catch (error) {
    console.warn("Could not read proposed comment outcome", error)
    return null
  }
}

function writeOutcome(id: string, outcome: Outcome) {
  try {
    window.localStorage.setItem(storageKey(id), JSON.stringify(outcome))
  } catch (error) {
    console.warn("Could not save proposed comment outcome", error)
  }
}

/** A review comment the chat drafted; nothing posts until the user confirms. */
export function ProposedCommentCard({
  owner,
  repo,
  number,
  proposal,
  onShow,
}: {
  owner: string
  repo: string
  number: number
  proposal: ProposedComment
  onShow: () => void
}) {
  const queryClient = useQueryClient()
  const [body, setBody] = useState(proposal.body)
  const [outcome, setOutcome] = useState<Outcome | null>(() =>
    readOutcome(proposal.id)
  )
  const { range } = proposal
  const settle = (next: Outcome) => {
    writeOutcome(proposal.id, next)
    setOutcome(next)
  }
  const post = useMutation({
    mutationFn: () =>
      api.createReviewComment(owner, repo, number, {
        path: range.file,
        line: range.endLine,
        side: range.side,
        body: body.trim(),
        start_line: range.startLine < range.endLine ? range.startLine : null,
        start_side: range.startLine < range.endLine ? range.side : null,
      }),
    onSuccess: (result) => {
      settle({ state: "posted", url: result.html_url })
      void queryClient.invalidateQueries({
        queryKey: ["reviewComments", owner, repo, number],
      })
    },
    onError: (error) =>
      toast.error("Couldn't post the comment", { description: error.message }),
  })

  const location = `${range.file}:${rangeLabel(range)}`
  return (
    <Card size="sm" className="w-full">
      <CardHeader>
        <CardTitle>
          {outcome?.state === "posted"
            ? "Comment posted"
            : outcome?.state === "discarded"
              ? "Comment discarded"
              : "Draft review comment"}
        </CardTitle>
        <CardDescription>
          <button
            type="button"
            onClick={onShow}
            className="truncate font-mono underline-offset-2 hover:underline"
          >
            {location}
          </button>
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
            onChange={(event) => setBody(event.target.value)}
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
              onClick={() => settle({ state: "discarded" })}
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
