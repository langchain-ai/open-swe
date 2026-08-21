import { Fragment, useEffect, useRef, useState } from "react"
import {
  ChatCircleIcon,
  CheckCircleIcon,
  PencilSimpleIcon,
  XIcon,
} from "@phosphor-icons/react"
import { IoLogoGithub } from "react-icons/io5"

import type { SelectedLineRange } from "@pierre/diffs"
import type { PrReviewComment } from "@/features/reviews/lib/api"
import type { MarkdownAction } from "@/features/reviews/lib/markdownEditing"
import { Markdown } from "@/components/markdown/Markdown"
import { IconButton } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import {
  buildCommentPayload,
  commentRangeLabel,
} from "@/features/reviews/lib/diffSelection"
import { useExpandedFinding } from "@/features/reviews/lib/findings"
import {
  MARKDOWN_TOOLBAR,
  applyMarkdownAction,
} from "@/features/reviews/lib/markdownEditing"
import {
  useCreateReviewComment,
  useUpdateReviewComment,
} from "@/features/reviews/lib/queries"
import { useSession } from "@/lib/session"
import { cn } from "@/lib/utils"

/**
 * The inline comment composer, opened by clicking the gutter "+" on a line.
 * Rendered through the same Pierre annotation portal as InlineFinding, so it sits
 * in place at the line. Mirrors GitHub's stock comment box (Write/Preview tabs +
 * markdown toolbar); submitting posts a real PR review comment as the user.
 */
export function CommentComposer({
  owner,
  repo,
  prNumber,
  path,
  range,
  onClose,
}: {
  owner: string
  repo: string
  prNumber: number
  path: string
  range: SelectedLineRange
  onClose: () => void
}) {
  const [value, setValue] = useState("")
  const [mode, setMode] = useState<"write" | "preview">("write")
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)
  useEffect(() => {
    textareaRef.current?.focus()
  }, [])
  const mutation = useCreateReviewComment(owner, repo, prNumber)
  const submit = () => {
    const body = value.trim()
    if (!body || mutation.isPending) return
    mutation.mutate(buildCommentPayload(path, range, body))
  }
  // Apply a toolbar action to the live textarea selection, then restore the
  // caret/selection on the next frame (after the controlled value re-renders).
  const applyAction = (action: MarkdownAction) => {
    const textarea = textareaRef.current
    if (!textarea) return
    const next = applyMarkdownAction(
      { value, start: textarea.selectionStart, end: textarea.selectionEnd },
      action
    )
    setValue(next.value)
    requestAnimationFrame(() => {
      textarea.focus()
      textarea.setSelectionRange(next.start, next.end)
    })
  }
  const posted = mutation.data
  const tabClass = (active: boolean) =>
    cn(
      "rounded px-2 py-0.5 text-[11px]",
      active
        ? "bg-accent font-medium text-foreground"
        : "text-muted-foreground hover:text-foreground"
    )
  return (
    <div className="px-2 py-1 font-sans">
      <div className="overflow-hidden rounded-md border border-border bg-card">
        <div className="flex items-center gap-1.5 border-b border-border px-2 py-1 text-[11px]">
          <ChatCircleIcon className="size-3 text-muted-foreground" />
          <span className="font-medium">
            Add a comment on line {commentRangeLabel(range)}
          </span>
          <IconButton
            type="button"
            variant="ghost"
            size="icon-xs"
            aria-label="Close comment"
            className="ml-auto"
            onClick={onClose}
          >
            <XIcon />
          </IconButton>
        </div>
        {posted ? (
          <div className="flex items-center gap-2 px-3 py-2.5 text-[11px] text-muted-foreground">
            <CheckCircleIcon className="size-3.5 text-emerald-500" />
            Comment posted
            <a
              href={posted.html_url}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 text-foreground hover:underline"
            >
              <IoLogoGithub className="size-3" />
              View on GitHub
            </a>
          </div>
        ) : (
          <>
            <div className="flex items-center gap-1 border-b border-border px-1.5 py-1">
              <button
                type="button"
                onClick={() => setMode("write")}
                aria-selected={mode === "write"}
                className={tabClass(mode === "write")}
              >
                Write
              </button>
              <button
                type="button"
                onClick={() => setMode("preview")}
                aria-selected={mode === "preview"}
                className={tabClass(mode === "preview")}
              >
                Preview
              </button>
              {mode === "write" && (
                <div className="ml-auto flex items-center gap-0.5">
                  {MARKDOWN_TOOLBAR.map((group, groupIndex) => (
                    <Fragment key={group[0]?.action ?? groupIndex}>
                      {groupIndex > 0 && (
                        <span className="mx-0.5 h-4 w-px bg-border" />
                      )}
                      {group.map(({ action, label, Icon }) => (
                        <IconButton
                          key={action}
                          type="button"
                          variant="ghost"
                          size="icon-sm"
                          aria-label={label}
                          title={label}
                          onMouseDown={(event) => event.preventDefault()}
                          onClick={() => applyAction(action)}
                        >
                          <Icon className="size-4" />
                        </IconButton>
                      ))}
                    </Fragment>
                  ))}
                </div>
              )}
            </div>
            <div className="p-2">
              {mode === "write" ? (
                <Textarea
                  ref={textareaRef}
                  value={value}
                  onChange={(event) => setValue(event.target.value)}
                  onKeyDown={(event) => {
                    if (
                      (event.metaKey || event.ctrlKey) &&
                      event.key === "Enter"
                    ) {
                      event.preventDefault()
                      submit()
                    } else if (event.key === "Escape") {
                      event.preventDefault()
                      onClose()
                    }
                  }}
                  placeholder="Leave a comment…"
                  rows={3}
                  className="resize-y text-xs"
                />
              ) : (
                <div className="min-h-16 rounded-md border border-input bg-input/20 px-2 py-2 text-xs">
                  {value.trim() ? (
                    <Markdown content={value} />
                  ) : (
                    <span className="text-muted-foreground">
                      Nothing to preview
                    </span>
                  )}
                </div>
              )}
              {mutation.isError && (
                <p className="mt-1.5 text-[11px] text-destructive">
                  {mutation.error instanceof Error
                    ? mutation.error.message
                    : "Failed to post comment"}
                </p>
              )}
              <div className="mt-2 flex items-center justify-end gap-2">
                <button
                  type="button"
                  onClick={onClose}
                  className="rounded border border-border px-2 py-1 text-[11px] text-muted-foreground hover:text-foreground"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={submit}
                  disabled={!value.trim() || mutation.isPending}
                  className="rounded bg-foreground px-2 py-1 text-[11px] font-medium text-background disabled:opacity-50"
                >
                  {mutation.isPending ? "Posting…" : "Comment"}
                </button>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

/**
 * An existing PR comment opened from the comments dropdown, rendered inline at
 * its line through the same annotation portal as InlineFinding. The node is
 * registered so the dropdown can scroll it into view. Its author can edit it in
 * place; everyone else follows the link to the full thread on GitHub.
 */
export function InlineComment({
  owner,
  repo,
  prNumber,
  comment,
  onUpdate,
  onClose,
}: {
  owner: string
  repo: string
  prNumber: number
  comment: PrReviewComment
  onUpdate?: (comment: PrReviewComment) => void
  onClose: () => void
}) {
  const { registerAnnotation } = useExpandedFinding()
  const session = useSession()
  const [body, setBody] = useState(comment.body)
  const [draft, setDraft] = useState(comment.body)
  const [editing, setEditing] = useState(false)
  const sideLabel = comment.side === "LEFT" ? "L" : "R"
  const editable =
    session.data?.login.toLowerCase() === comment.author.toLowerCase()
  const mutation = useUpdateReviewComment(owner, repo, prNumber, comment.id)
  useEffect(() => {
    setBody(comment.body)
    setDraft(comment.body)
    setEditing(false)
  }, [comment.id, comment.body])
  const submit = () => {
    const next = draft.trim()
    if (!next || next === body || mutation.isPending) return
    mutation.mutate(next, {
      onSuccess: () => {
        setBody(next)
        setDraft(next)
        setEditing(false)
        onUpdate?.({ ...comment, body: next })
      },
    })
  }
  const cancel = () => {
    setDraft(body)
    setEditing(false)
    mutation.reset()
  }
  return (
    <div
      ref={(node) => registerAnnotation(`comment:${comment.id}`, node)}
      className="px-2 py-1 font-sans"
    >
      <div className="overflow-hidden rounded-md border border-border bg-card">
        <div className="flex items-center gap-1.5 border-b border-border px-2 py-1 text-[11px]">
          {comment.author_avatar_url ? (
            <img
              src={comment.author_avatar_url}
              alt=""
              className="size-4 shrink-0 rounded-full"
            />
          ) : (
            <span className="size-4 shrink-0 rounded-full bg-muted" />
          )}
          <span className="font-medium">{comment.author}</span>
          {comment.line !== null && (
            <span className="font-mono text-muted-foreground">
              {sideLabel}
              {comment.line}
            </span>
          )}
          <div className="ml-auto flex items-center gap-0.5">
            {editable && !editing && (
              <IconButton
                type="button"
                variant="ghost"
                size="icon-xs"
                aria-label="Edit comment"
                onClick={() => setEditing(true)}
              >
                <PencilSimpleIcon />
              </IconButton>
            )}
            <a
              href={comment.html_url}
              target="_blank"
              rel="noreferrer"
              aria-label="View on GitHub"
              title="View on GitHub"
              className="inline-flex size-5 items-center justify-center rounded-sm text-muted-foreground hover:text-foreground"
            >
              <IoLogoGithub className="size-3" />
            </a>
            <IconButton
              type="button"
              variant="ghost"
              size="icon-xs"
              aria-label="Close comment"
              onClick={onClose}
            >
              <XIcon />
            </IconButton>
          </div>
        </div>
        {editing ? (
          <div className="p-2">
            <Textarea
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
                  event.preventDefault()
                  submit()
                } else if (event.key === "Escape") {
                  event.preventDefault()
                  cancel()
                }
              }}
              rows={3}
              className="resize-y text-xs"
              autoFocus
            />
            {mutation.isError && (
              <p className="mt-1.5 text-[11px] text-destructive">
                {mutation.error instanceof Error
                  ? mutation.error.message
                  : "Failed to update comment"}
              </p>
            )}
            <div className="mt-2 flex items-center justify-end gap-2">
              <button
                type="button"
                onClick={cancel}
                className="rounded border border-border px-2 py-1 text-[11px] text-muted-foreground hover:text-foreground"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={submit}
                disabled={
                  !draft.trim() || draft.trim() === body || mutation.isPending
                }
                className="rounded bg-foreground px-2 py-1 text-[11px] font-medium text-background disabled:opacity-50"
              >
                {mutation.isPending ? "Saving…" : "Save"}
              </button>
            </div>
          </div>
        ) : (
          <div className="px-3 py-2.5 text-xs text-muted-foreground">
            <Markdown content={body} />
          </div>
        )}
      </div>
    </div>
  )
}
