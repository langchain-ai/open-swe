import { Badge } from "@langchain/macaw-components/Badge"
import { Button } from "@langchain/macaw-components/Button"
import { IconButton } from "@langchain/macaw-components/IconButton"
import { Textarea } from "@langchain/macaw-components/Textarea"
import { PencilSimpleIcon } from "@phosphor-icons/react/dist/ssr/PencilSimple"
import { TrashIcon } from "@phosphor-icons/react/dist/ssr/Trash"
import { useState } from "react"

import { readableRangeLabel } from "@/features/reviews/lib/lineRange"
import {
  reviewSessionKey,
  useReviewSession,
  useReviewSessions,
} from "@/features/reviews/lib/reviewSession"
import { AgentMark } from "@/features/reviews/page/AgentMark"
import type { PullRequestRef } from "@/features/reviews/page/queries"
import { NoteFrame } from "./NoteFrame"

/** A review session's unsent comment for Open SWE, on the lines it's about; editable until it's sent. */
export function AgentCommentNote({
  pr,
  id,
}: {
  pr: PullRequestRef
  id: string
}) {
  const key = reviewSessionKey(pr)
  const comment = useReviewSession(pr).comments.find(
    (candidate) => candidate.id === id
  )
  const edit = useReviewSessions((state) => state.edit)
  const remove = useReviewSessions((state) => state.remove)
  const [editing, setEditing] = useState<string | null>(null)
  if (!comment) return null
  const save = () => {
    const text = editing?.trim()
    if (!text) return
    edit(key, id, text)
    setEditing(null)
  }
  return (
    <NoteFrame>
      <article
        aria-label={`Unsent agent comment on ${readableRangeLabel(comment.range)}`}
        className="rounded-lg border border-dashed border-brand-subtle bg-surface-level-1 p-space-2 text-xs"
      >
        <header className="flex items-center gap-space-2">
          <AgentMark />
          <span className="font-medium text-primary">Agent comment</span>
          <Badge size="xs" color="secondary">
            Unsent draft
          </Badge>
          <span className="flex-1" />
          {editing === null && (
            <>
              <IconButton
                icon={PencilSimpleIcon}
                label="Edit draft"
                size="xs"
                color="secondary"
                variant="plain"
                onClick={() => setEditing(comment.body)}
              />
              <IconButton
                icon={TrashIcon}
                label="Remove draft"
                size="xs"
                color="secondary"
                variant="plain"
                onClick={() => remove(key, id)}
              />
            </>
          )}
        </header>
        {editing === null ? (
          <p className="mt-space-1 whitespace-pre-wrap text-primary">
            {comment.body}
          </p>
        ) : (
          <form
            className="mt-space-1"
            onSubmit={(event) => {
              event.preventDefault()
              save()
            }}
          >
            <Textarea
              size="md"
              aria-label="Draft body"
              rows={3}
              resize="vertical"
              value={editing}
              autoFocus
              onChange={setEditing}
              onKeyDown={(event) => {
                if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
                  event.preventDefault()
                  save()
                }
                if (event.key === "Escape") {
                  event.stopPropagation()
                  setEditing(null)
                }
              }}
            />
            <div className="mt-space-1 flex justify-end gap-space-2">
              <Button
                type="button"
                size="xs"
                color="secondary"
                variant="plain"
                onClick={() => setEditing(null)}
              >
                Cancel
              </Button>
              <Button type="submit" size="xs" disabled={!editing.trim()}>
                Save draft
              </Button>
            </div>
          </form>
        )}
        <p className="mt-space-1 text-secondary">
          For Open SWE only. Not sent; never posted to GitHub.
        </p>
      </article>
    </NoteFrame>
  )
}
