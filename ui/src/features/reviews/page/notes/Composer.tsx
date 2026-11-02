import { Button } from "@langchain/macaw-components/Button"
import { Kbd } from "@langchain/macaw-components/Kbd"
import { Textarea } from "@langchain/macaw-components/Textarea"

import { useShortcutLabel } from "@/lib/hotkeys"
import {
  buildCommentPayload,
  readableRangeLabel,
} from "@/features/reviews/lib/lineRange"
import { usePendingReview } from "@/features/reviews/lib/usePendingReview"
import { useReviewChat } from "@/features/reviews/lib/reviewKeys"
import {
  reviewSessionKey,
  useReviewSession,
  useReviewSessions,
} from "@/features/reviews/lib/reviewSession"
import { AgentMark } from "@/features/reviews/page/AgentMark"
import type { PullRequestRef } from "@/features/reviews/page/queries"
import {
  useReviewPage,
  type CommentDraftTarget,
} from "@/features/reviews/page/store"
import {
  useAskAboutLines,
  useDraftAgentComment,
} from "@/features/reviews/page/askAboutLines"
import { NoteFrame } from "./NoteFrame"

/** Composers that already took focus; the diff remounts them as it scrolls. */
const focused = new WeakSet<CommentDraftTarget>()

/**
 * The comment box on a line range. The same words can go to the author, as
 * part of your GitHub review, or to Open SWE: straight away, or, in a review
 * session, as a draft sent with the rest when the reviewer finishes.
 */
export function Composer({
  pr,
  target,
}: {
  pr: PullRequestRef
  target: CommentDraftTarget
}) {
  const { path, range } = target
  const pending = usePendingReview(pr.owner, pr.repo, pr.number)
  const setComposer = useReviewPage((state) => state.setComposer)
  const body = useReviewPage((state) => state.composerText)
  const setBody = useReviewPage((state) => state.setComposerText)
  const askAboutLines = useAskAboutLines(pr)
  const draftAgentComment = useDraftAgentComment(pr)
  const drafting = useReviewSession(pr).active
  const startSession = useReviewSessions((state) => state.start)
  // Only the chat delivers a session's drafts, as in the header.
  const chatAvailable = useReviewChat(pr).data?.available === true
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
    const text = body.trim()
    if (drafting) {
      if (!text) return
      draftAgentComment(path, range, text)
    } else void askAboutLines(path, range, text)
    close()
  }
  return (
    <NoteFrame>
      <form
        className="rounded-lg border border-brand-subtle bg-surface-level-1 p-space-2 text-xs shadow-sm"
        onSubmit={(event) => {
          event.preventDefault()
          ask()
        }}
      >
        <p className="mb-space-1 text-secondary">
          Comment on {readableRangeLabel(range)}
        </p>
        <Textarea
          size="md"
          ref={(node) => {
            // Focus once, as it opens; without moving the diff, which already scrolled to it.
            if (!node || focused.has(target)) return
            focused.add(target)
            node.focus({ preventScroll: true })
          }}
          aria-label="Comment body"
          rows={4}
          resize="vertical"
          value={body}
          placeholder={
            drafting
              ? "Leave a comment, or draft one for Open SWE"
              : "Leave a comment, or ask Open SWE about these lines"
          }
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
        {(drafting || chatAvailable) && (
          <p className="mt-space-1 text-secondary">
            {drafting ? (
              "For Open SWE only. A draft sends nothing; your drafts go together when you finish the session."
            ) : (
              <>
                Ask Open SWE sends now.{" "}
                <Button
                  type="button"
                  size="xs"
                  color="secondary"
                  variant="plain"
                  className="h-auto border-0 px-0 align-baseline text-brand-primary underline-offset-2 hover:underline"
                  onClick={() => startSession(reviewSessionKey(pr))}
                >
                  Start a review session
                </Button>{" "}
                to collect comments and send them together.
              </>
            )}
          </p>
        )}
        {pending.add.error && (
          <p role="alert" className="mt-space-1 text-error-secondary">
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
          <Button type="submit" size="sm" disabled={drafting && !body.trim()}>
            <AgentMark />
            {drafting ? "Add to agent drafts" : "Ask Open SWE"}
            <Kbd variant="inherit">{askKey}</Kbd>
          </Button>
        </div>
      </form>
    </NoteFrame>
  )
}
