import type { SelectedLineRange } from "@pierre/diffs"

import { Button } from "@/components/ui/button"
import { Kbd } from "@/components/ui/kbd"
import { Textarea } from "@/components/ui/textarea"
import {
  buildCommentPayload,
  readableRangeLabel,
} from "@/features/reviews/lib/lineRange"
import { usePendingReview } from "@/features/reviews/lib/usePendingReview"
import { AgentMark } from "@/features/reviews/page/AgentMark"
import type { PullRequestRef } from "@/features/reviews/page/queries"
import { useReviewPage } from "@/features/reviews/page/store"
import { useAskAboutLines } from "@/features/reviews/page/askAboutLines"
import { NoteFrame } from "./NoteFrame"

/**
 * The comment box on a line range. The same words can go to the author, as
 * part of your GitHub review, or to Open SWE.
 */
export function Composer({
  pr,
  path,
  range,
}: {
  pr: PullRequestRef
  path: string
  range: SelectedLineRange
}) {
  const pending = usePendingReview(pr.owner, pr.repo, pr.number)
  const setComposer = useReviewPage((state) => state.setComposer)
  const body = useReviewPage((state) => state.composerText)
  const setBody = useReviewPage((state) => state.setComposerText)
  const askAboutLines = useAskAboutLines(pr)
  const close = () => setComposer(null)
  const addToReview = () => {
    const text = body.trim()
    if (!text || pending.add.isPending) return
    pending.add.mutate(buildCommentPayload(path, range, text), {
      onSuccess: close,
    })
  }
  const ask = () => {
    void askAboutLines(path, range, body.trim())
    close()
  }
  return (
    <NoteFrame>
      <form
        className="rounded-lg border border-ring/50 bg-card p-2.5 text-xs shadow-sm"
        onSubmit={(event) => {
          event.preventDefault()
          addToReview()
        }}
      >
        <p className="mb-1.5 text-muted-foreground">
          Comment on {readableRangeLabel(range)}
        </p>
        <Textarea
          aria-label="Comment body"
          autoFocus
          rows={4}
          value={body}
          placeholder="Leave a comment, or ask Open SWE about these lines"
          onChange={(event) => setBody(event.target.value)}
          onKeyDown={(event) => {
            if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
              event.preventDefault()
              if (event.shiftKey) ask()
              else addToReview()
            }
            if (event.key === "Escape") {
              event.stopPropagation()
              // A typed comment survives Escape; Cancel is the way to drop it.
              if (body.trim()) event.currentTarget.blur()
              else close()
            }
          }}
          className="resize-y text-[13px]"
        />
        {pending.add.error && (
          <p role="alert" className="mt-1.5 text-destructive">
            {pending.add.error.message}
          </p>
        )}
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <Button type="button" size="sm" variant="ghost" onClick={ask}>
            <AgentMark />
            Ask Open SWE
            <Kbd>⇧⌘↩</Kbd>
          </Button>
          <span className="flex-1" />
          <Button type="button" size="sm" variant="ghost" onClick={close}>
            Cancel
          </Button>
          <Button
            type="submit"
            size="sm"
            disabled={!body.trim() || pending.add.isPending}
          >
            {pending.add.isPending ? "Adding…" : "Add review comment"}
            <Kbd className="bg-primary-foreground/15 text-primary-foreground">
              ⌘↩
            </Kbd>
          </Button>
        </div>
      </form>
    </NoteFrame>
  )
}
