import { Button } from "@langchain/macaw-components/Button"
import { Kbd } from "@langchain/macaw-components/Kbd"
import { Textarea } from "@langchain/macaw-components/Textarea"
import type { SelectedLineRange } from "@pierre/diffs"

import { useShortcutLabel } from "@/lib/hotkeys"
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
  const askKey = useShortcutLabel("mod+enter")
  const addKey = useShortcutLabel("mod+shift+enter")
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
        className="rounded-lg border border-brand-subtle bg-surface-level-1 p-2.5 text-xs shadow-sm"
        onSubmit={(event) => {
          event.preventDefault()
          ask()
        }}
      >
        <p className="mb-1.5 text-secondary">
          Comment on {readableRangeLabel(range)}
        </p>
        <Textarea
          size="md"
          aria-label="Comment body"
          autoFocus
          rows={4}
          resize="vertical"
          value={body}
          placeholder="Leave a comment, or ask Open SWE about these lines"
          onChange={setBody}
          onKeyDown={(event) => {
            if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
              event.preventDefault()
              if (event.shiftKey) addToReview()
              else ask()
            }
            if (event.key === "Escape") {
              event.stopPropagation()
              // A typed comment survives Escape (Cancel drops it), and focus stays so keys keep typing.
              if (!body.trim()) close()
            }
          }}
        />
        {pending.add.error && (
          <p role="alert" className="mt-1.5 text-error-secondary">
            {pending.add.error.message}
          </p>
        )}
        <div className="mt-space-2 flex flex-wrap items-center gap-space-2">
          <Button
            type="button"
            size="sm"
            color="secondary"
            variant="plain"
            onClick={close}
          >
            Cancel
          </Button>
          <span className="flex-1" />
          <Button
            type="button"
            size="sm"
            color="secondary"
            variant="outlined"
            disabled={!body.trim() || pending.add.isPending}
            onClick={addToReview}
          >
            {pending.add.isPending ? "Adding…" : "Add review comment"}
            <Kbd>{addKey}</Kbd>
          </Button>
          <Button type="submit" size="sm">
            <AgentMark />
            Ask Open SWE
            <Kbd variant="inherit">{askKey}</Kbd>
          </Button>
        </div>
      </form>
    </NoteFrame>
  )
}
