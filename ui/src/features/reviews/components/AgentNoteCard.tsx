import { Button } from "@langchain/macaw-components/Button"
import { XIcon } from "@langchain/macaw-components/icons"
import { IconButton } from "@langchain/macaw-components/IconButton"

import { Markdown } from "@/features/agents/components/chat/Markdown"
import { rangeLabel } from "@/features/reviews/lib/chatDiffActions"
import { useChatDrafts } from "@/features/reviews/lib/chatDrafts"
import { AgentMark } from "@/features/reviews/page/AgentMark"

/**
 * The agent's note on diff lines, for the viewer alone: drawn as a margin note
 * in the agent's colors, never a comment bubble, since nothing reaches GitHub.
 * Closing it hides it in the diff and dims it in the chat.
 */
export function AgentNoteCard({
  id,
  onShow,
}: {
  id: string
  /** Scrolls the diff to the note's lines; omitted where the note already sits on them. */
  onShow?: () => void
}) {
  const drafts = useChatDrafts()
  const draft = drafts?.notes.find((item) => item.note.id === id)
  if (!drafts || !draft) return null
  const { range, body } = draft.note
  const location = `${range.file}:${rangeLabel(range)}`
  const closed = draft.outcome !== null
  return (
    <div
      data-testid="agent-note"
      className="w-full rounded-r-lg border-y border-r border-default bg-brand-muted text-xs"
      style={{ borderLeft: "3px solid var(--icon-brand)" }}
    >
      <div className="flex items-center gap-space-2 px-space-3 pt-space-2">
        <AgentMark />
        <span className="shrink-0 font-medium text-primary">Note</span>
        {onShow ? (
          <Button
            color="secondary"
            variant="plain"
            size="sm"
            aria-label={location}
            type="button"
            onClick={onShow}
            className="h-auto min-w-0 truncate border-0 px-0 font-mono text-secondary underline-offset-2 hover:underline"
          >
            <span className="min-w-0 truncate">{location}</span>
          </Button>
        ) : (
          <span className="min-w-0 flex-1 truncate text-secondary">
            Only you see this
          </span>
        )}
        {!closed && (
          <IconButton
            icon={XIcon}
            label="Close note"
            size="xs"
            variant="plain"
            color="secondary"
            className="ml-auto shrink-0"
            onClick={() => drafts.settle(id, { state: "discarded" })}
          />
        )}
      </div>
      <div
        className={
          closed
            ? "line-clamp-2 px-space-3 pt-space-1 pb-space-2 leading-[1.6] text-secondary"
            : "px-space-3 pt-space-1 pb-space-2 leading-[1.6]"
        }
      >
        <Markdown content={body} />
      </div>
    </div>
  )
}
