import { Button } from "@langchain/macaw-components/Button"
import { Text } from "@langchain/macaw-components/Text"
import { Textarea } from "@langchain/macaw-components/Textarea"
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react"
import type { KeyboardEvent } from "react"

import { cn } from "@/lib/utils"
import { SubmitShortcut } from "@/components/SubmitShortcut"
import type { PlanComment, PlanData, PlanTextAnchor } from "@/lib/plan"
import {
  addPlanComment,
  deletePlanComment,
  getPlanComments,
  submitPlanComments,
} from "@/lib/plan"
import { reportError } from "@/lib/errorReporting"
import { useRefreshThreadSource } from "@/features/agents/lib/threadSource/ThreadSourceProvider"
import type { ArtifactSelectionBounds } from "@/features/agents/components/PlanArtifactFrame"
import { PlanArtifactFrame } from "@/features/agents/components/PlanArtifactFrame"
import { Markdown } from "@/features/agents/components/chat/Markdown"
import { PlusIcon } from "@langchain/macaw-components/icons"

const POLL_MS = 4000

async function copyToClipboard(text: string): Promise<boolean> {
  const nav = navigator as { clipboard?: Clipboard }
  try {
    if (window.isSecureContext && nav.clipboard) {
      await nav.clipboard.writeText(text)
      return true
    }
  } catch {
    /* fall through */
  }
  try {
    const textarea = document.createElement("textarea")
    textarea.value = text
    textarea.setAttribute("readonly", "")
    textarea.style.position = "fixed"
    textarea.style.top = "-9999px"
    document.body.appendChild(textarea)
    textarea.select()
    textarea.setSelectionRange(0, text.length)
    const copied = document.execCommand("copy")
    document.body.removeChild(textarea)
    return copied
  } catch {
    return false
  }
}

export function PlanReview({
  plan,
  embedded = false,
}: {
  plan: PlanData
  embedded?: boolean
}) {
  const refreshThreadSource = useRefreshThreadSource()
  const [popoverMode, setPopoverMode] = useState<
    "selection" | "comments" | null
  >(null)
  const commentsOpen = popoverMode !== null
  const selectionMode = embedded && popoverMode === "selection"

  const [selectionBounds, setSelectionBounds] =
    useState<ArtifactSelectionBounds | null>(null)
  const documentRef = useRef<HTMLDivElement>(null)
  const popoverRef = useRef<HTMLElement>(null)
  const commentsToggleRef = useRef<HTMLButtonElement>(null)

  const [documentSize, setDocumentSize] = useState({ width: 0, height: 0 })
  const [measuredHeight, setMeasuredHeight] = useState(0)
  const [buttonAnchor, setButtonAnchor] = useState({ x: 0, y: 0 })

  useLayoutEffect(() => {
    const element = documentRef.current
    const popover = popoverRef.current
    if (!element || !popover || !embedded) return

    const measure = () => {
      const bounds = element.getBoundingClientRect()
      const height = popover.getBoundingClientRect().height
      const button = commentsToggleRef.current?.getBoundingClientRect()
      if (button) {
        const x = button.right - bounds.left
        const y = button.bottom - bounds.top + 8
        setButtonAnchor((previous) =>
          previous.x === x && previous.y === y ? previous : { x, y }
        )
      }
      setDocumentSize((previous) =>
        previous.width === bounds.width && previous.height === bounds.height
          ? previous
          : { width: bounds.width, height: bounds.height }
      )
      setMeasuredHeight(height)
    }
    measure()

    const observer = new ResizeObserver(measure)
    observer.observe(element)
    observer.observe(popover)
    if (commentsToggleRef.current) observer.observe(commentsToggleRef.current)

    return () => observer.disconnect()
  }, [embedded, commentsOpen, selectionMode])

  const popoverWidth = Math.min(320, Math.max(0, documentSize.width - 24))
  const maxPopoverHeight = Math.max(0, documentSize.height - 24)
  const popoverHeight = Math.min(measuredHeight, maxPopoverHeight)

  const popoverPosition = (() => {
    if (documentSize.width === 0) return undefined
    const left = selectionBounds
      ? selectionBounds.left
      : buttonAnchor.x - popoverWidth
    const top = selectionBounds
      ? selectionBounds.bottom + 8 + popoverHeight <= documentSize.height - 12
        ? selectionBounds.bottom + 8
        : selectionBounds.top - popoverHeight - 8
      : buttonAnchor.y

    return {
      right: "auto",
      left: Math.max(
        12,
        Math.min(left, documentSize.width - popoverWidth - 12)
      ),
      top: Math.max(
        12,
        Math.min(top, documentSize.height - popoverHeight - 12)
      ),
      width: popoverWidth,
      maxHeight: maxPopoverHeight,
    }
  })()

  const [comments, setComments] = useState<Array<PlanComment>>([])
  const [anchor, setAnchor] = useState<PlanTextAnchor | null>(null)
  const [generalComment, setGeneralComment] = useState(false)
  const [replyTo, setReplyTo] = useState<PlanComment | null>(null)
  const [draft, setDraft] = useState("")
  const [posting, setPosting] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [submitted, setSubmitted] = useState(false)
  const [focusComment, setFocusComment] = useState<{
    id: string
    key: number
  } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const commentRefs = useRef(new Map<string, HTMLElement>())
  const commentMutation = useRef(0)
  const pendingComment = useRef<string | null>(null)
  const format = plan.html.trim() ? "html" : "markdown"
  const content = format === "html" ? plan.html : plan.markdown
  const canComment = format === "html"

  const dismissPopover = useCallback(() => {
    setPopoverMode(null)
    setAnchor(null)
    setGeneralComment(false)
    setReplyTo(null)
    setDraft("")
  }, [])

  useEffect(() => {
    if (!embedded || !commentsOpen) return
    const dismissOutside = (event: PointerEvent) => {
      if (
        event.target instanceof Node &&
        !popoverRef.current?.contains(event.target) &&
        !commentsToggleRef.current?.contains(event.target)
      ) {
        dismissPopover()
      }
    }
    const dismissOnEscape = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault()
        event.stopPropagation()
        dismissPopover()
      }
    }
    document.addEventListener("pointerdown", dismissOutside, true)
    document.addEventListener("keydown", dismissOnEscape, true)
    return () => {
      document.removeEventListener("pointerdown", dismissOutside, true)
      document.removeEventListener("keydown", dismissOnEscape, true)
    }
  }, [embedded, commentsOpen, dismissPopover])

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      const mutation = commentMutation.current
      try {
        const next = await getPlanComments(plan.threadId)
        if (
          !cancelled &&
          !pendingComment.current &&
          mutation === commentMutation.current
        )
          setComments(next)
      } catch {
        /* next poll retries */
      }
    }
    void load()
    const timer = window.setInterval(load, POLL_MS)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [plan.threadId])

  const replyParentMissing =
    replyTo !== null && !comments.some((comment) => comment.id === replyTo.id)

  const submitComment = useCallback(async () => {
    const body = draft.trim()
    if (!body || pendingComment.current || replyParentMissing) return
    const id = crypto.randomUUID()
    pendingComment.current = id
    setPosting(true)
    setError(null)
    commentMutation.current += 1
    setComments((current) => [
      ...current,
      {
        id,
        body,
        anchor: replyTo ? null : anchor,
        parent_id: replyTo?.id,
        author: plan.user.name,
        author_login: plan.user.login,
        created_at: new Date().toISOString(),
      },
    ])
    try {
      const created = replyTo
        ? await addPlanComment(plan.threadId, body, null, replyTo.id)
        : await addPlanComment(plan.threadId, body, anchor)
      setComments((current) =>
        current.map((comment) => (comment.id === id ? created : comment))
      )
      setSubmitted(false)
      setPopoverMode((mode) => (mode === "selection" ? null : mode))
      setAnchor(null)
      setGeneralComment(false)
      setReplyTo(null)
      setDraft("")
      setFocusComment({ id: replyTo?.id ?? created.id, key: Date.now() })
    } catch (commentError) {
      setComments((current) => current.filter((comment) => comment.id !== id))
      reportError({ title: "Couldn't add comment", error: commentError })
    } finally {
      pendingComment.current = null
      commentMutation.current += 1
      setPosting(false)
    }
  }, [
    anchor,
    draft,
    replyTo,
    replyParentMissing,
    plan.threadId,
    plan.user.name,
    plan.user.login,
  ])

  const handleCommentKeyDown = useCallback(
    (event: KeyboardEvent<HTMLTextAreaElement>) => {
      if (event.key !== "Enter" || (!event.metaKey && !event.ctrlKey)) return
      event.preventDefault()
      if (!posting && draft.trim()) void submitComment()
    },
    [draft, posting, submitComment]
  )

  const submitComments = useCallback(async () => {
    setSubmitting(true)
    setError(null)
    try {
      await submitPlanComments(plan.threadId)
      setSubmitted(true)
      refreshThreadSource?.()
    } catch (submitError) {
      setError((submitError as Error).message)
    } finally {
      setSubmitting(false)
    }
  }, [plan.threadId, refreshThreadSource])

  const removeComment = useCallback(
    async (id: string) => {
      commentMutation.current += 1
      try {
        await deletePlanComment(plan.threadId, id)
        setComments((current) =>
          current
            .filter((comment) => comment.id !== id)
            .map((comment) =>
              comment.parent_id === id
                ? { ...comment, parent_id: null }
                : comment
            )
        )
        setSubmitted(false)
      } catch (deleteError) {
        reportError({ title: "Couldn't delete comment", error: deleteError })
      }
    },
    [plan.threadId]
  )

  const openComment = useCallback(
    (id: string, position: ArtifactSelectionBounds | null = null) => {
      if (position) setSelectionBounds(position)
      setPopoverMode("comments")
      setFocusComment({ id, key: Date.now() })
      commentRefs.current
        .get(id)
        ?.scrollIntoView({ behavior: "smooth", block: "nearest" })
    },
    []
  )

  const copyPlan = useCallback(async () => {
    setError(null)
    if (await copyToClipboard(content)) {
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    } else {
      setError(`Couldn't copy the artifact ${format} to the clipboard.`)
    }
  }, [content, format])

  const rootComments = comments.filter((comment) => !comment.parent_id)
  const composer = (anchor || generalComment || replyTo) && (
    <div
      data-testid="comment-composer"
      className="border-b border-default bg-surface-level-2/30 p-space-3"
    >
      {replyTo && (
        <p className="text-xs text-secondary">Reply to {replyTo.author}</p>
      )}
      {replyParentMissing && (
        <p role="status" className="mt-space-2 text-xs text-secondary">
          The original comment was deleted. Your draft is preserved. Use Add
          comment to post it as a general comment, or cancel.
        </p>
      )}
      {anchor && (
        <blockquote className="line-clamp-3 border-l-2 border-brand pl-space-2 text-xs text-secondary">
          {anchor.exact}
        </blockquote>
      )}
      <Textarea
        data-testid="comment-input"
        size="md"
        value={draft}
        disabled={posting}
        onChange={setDraft}
        onKeyDown={handleCommentKeyDown}
        placeholder={replyTo ? "Write a reply" : "Leave a comment"}
        rows={3}
        resize="none"
        autoFocus
        className="mt-space-3"
        inputClassName="text-sm"
      />
      <div className="mt-space-2 flex justify-end gap-space-2">
        <Button
          color="secondary"
          variant="plain"
          size="xs"
          disabled={posting}
          onClick={() => {
            setAnchor(null)
            setGeneralComment(false)
            setReplyTo(null)
            setDraft("")
            if (embedded) dismissPopover()
          }}
        >
          Cancel
        </Button>
        <Button
          data-testid="comment-submit"
          aria-keyshortcuts="Meta+Enter Control+Enter"
          aria-label={posting ? "Posting…" : replyTo ? "Reply" : "Comment"}
          size="xs"
          disabled={posting || replyParentMissing || !draft.trim()}
          onClick={() => void submitComment()}
        >
          {posting ? "Posting…" : replyTo ? "Reply" : "Comment"}
          {!posting && <SubmitShortcut />}
        </Button>
      </div>
    </div>
  )

  return (
    <main
      data-testid="plan-review"
      className="@container flex min-h-0 flex-1 flex-col overflow-hidden bg-surface-level-1 text-primary"
    >
      <div
        className={cn(
          "flex min-h-0 w-full flex-1 flex-col",
          !embedded && "gap-space-3 p-space-3 md:p-space-4"
        )}
      >
        <header
          className={cn(
            embedded
              ? "flex shrink-0 items-center justify-between gap-space-2 border-b border-muted px-space-3 pb-space-2"
              : "flex flex-col gap-space-3 border-b border-default pb-space-3 @3xl:flex-row @3xl:items-center @3xl:justify-between"
          )}
        >
          <div data-testid="plan-summary" className="min-w-0">
            <Text
              as="h1"
              variant="sm"
              weight={embedded ? undefined : "semibold"}
              color="primary"
              className="truncate"
            >
              Artifact
            </Text>
          </div>
          <div
            data-testid="plan-actions"
            className="flex flex-wrap items-center gap-space-2"
          >
            {embedded && canComment && (
              <Button
                size="xs"
                color="secondary"
                variant="plain"
                ref={commentsToggleRef}
                aria-expanded={popoverMode === "comments"}
                aria-controls="artifact-comments"
                onClick={() => {
                  if (popoverMode === "comments") dismissPopover()
                  else {
                    setSelectionBounds(null)
                    setPopoverMode("comments")
                  }
                }}
              >
                {comments.length > 0
                  ? `Comments (${comments.length})`
                  : "Comments"}
              </Button>
            )}
            <Button
              size={embedded ? "xs" : "md"}
              data-testid="copy-plan"
              color="secondary"
              variant="outlined"
              disabled={!content.trim()}
              onClick={() => void copyPlan()}
            >
              {copied
                ? "Copied!"
                : `Copy ${format === "html" ? "HTML" : "Markdown"}`}
            </Button>
          </div>
        </header>

        {error && <p className="text-xs text-error-secondary">{error}</p>}

        <section
          data-testid="plan-document"
          className={cn(
            "flex min-h-0 min-w-0 flex-1 overflow-hidden bg-surface-level-1",
            !embedded && "rounded-xl border border-default"
          )}
        >
          {content.trim() ? (
            format === "html" ? (
              <div
                ref={documentRef}
                className={cn(
                  "relative flex min-h-0 min-w-0 flex-1",
                  !embedded && "flex-col @3xl:flex-row"
                )}
              >
                <PlanArtifactFrame
                  html={content}
                  comments={rootComments}
                  scrollLocked={embedded && commentsOpen}
                  {...(embedded ? { onDismissComments: dismissPopover } : {})}
                  onTextSelected={
                    canComment && !posting
                      ? (next, position) => {
                          if (next) {
                            if (position) setSelectionBounds(position)
                            setReplyTo(null)
                            setPopoverMode("selection")
                          }
                          if (next || !draft.trim()) setAnchor(next)
                        }
                      : undefined
                  }
                  onCommentSelected={openComment}
                  focusCommentId={focusComment?.id ?? null}
                  focusCommentKey={focusComment?.key ?? 0}
                  className="h-full min-h-0 min-w-0 flex-1"
                />
                {embedded && commentsOpen && (
                  <div
                    aria-hidden="true"
                    className="absolute inset-0"
                    onPointerDown={dismissPopover}
                  />
                )}
                <aside
                  ref={popoverRef}
                  data-testid="plan-comments"
                  id={embedded ? "artifact-comments" : undefined}
                  style={embedded ? popoverPosition : undefined}
                  className={cn(
                    "flex flex-col overflow-y-auto",
                    embedded
                      ? "top-space-2 right-space-3 absolute max-h-[calc(100%-1.5rem)] w-80 max-w-[calc(100%-1.5rem)] rounded-sm border border-subtle bg-popover shadow-lg"
                      : "max-h-1/2 shrink-0 border-t border-default bg-surface-level-1/95 @3xl:max-h-none @3xl:w-80 @3xl:border-t-0 @3xl:border-l",
                    embedded && !commentsOpen && "hidden"
                  )}
                >
                  {!selectionMode && (
                    <div className="flex flex-col items-stretch justify-stretch gap-space-1 border-b border-subtle p-space-3">
                      <div className="flex items-center justify-between gap-space-2">
                        <Text as="h2" variant="h5" weight="semibold">
                          Comments
                        </Text>
                        <Button
                          size="xs"
                          variant={embedded ? "outlined" : "plain"}
                          color="secondary"
                          disabled={posting}
                          onClick={() => {
                            setAnchor(null)
                            setReplyTo(null)
                            setGeneralComment(true)
                          }}
                          leftDecorator={PlusIcon}
                        >
                          Add comment
                        </Button>
                      </div>
                      <p className="mt-0.5 text-xs text-secondary">
                        Add a general comment or highlight text to annotate.
                      </p>
                    </div>
                  )}
                  {(!replyTo || replyParentMissing) && composer}
                  {!selectionMode && (
                    <div className="min-h-0 flex-1 overflow-y-auto p-space-3">
                      {comments.length === 0 ? (
                        <p className="text-xs text-secondary">
                          No comments yet.
                        </p>
                      ) : (
                        <div className="space-y-space-2">
                          {rootComments.map((comment, index) => (
                            <article
                              key={comment.id}
                              ref={(element) => {
                                if (element)
                                  commentRefs.current.set(comment.id, element)
                                else commentRefs.current.delete(comment.id)
                              }}
                              data-testid="plan-comment"
                              className="rounded-lg border border-default bg-surface-level-1 p-space-3 pt-2.5"
                            >
                              <Button
                                color="secondary"
                                variant="plain"
                                size="sm"
                                aria-label={`Comment ${index + 1} by ${comment.author}: ${comment.body}`}
                                type="button"
                                className="block h-auto w-full justify-start border-0 p-0 text-left whitespace-normal text-inherit hover:bg-transparent focus-visible:ring-2 focus-visible:ring-focus focus-visible:outline-none dark:hover:bg-transparent"
                                onClick={() => openComment(comment.id)}
                              >
                                <span className="text-xs font-semibold">
                                  {comment.author}
                                </span>
                                {comment.anchor && (
                                  <blockquote className="mt-space-2 line-clamp-2 border-l-2 border-warning pl-space-2 text-xs text-secondary">
                                    {comment.anchor.exact}
                                  </blockquote>
                                )}
                                <span className="mt-space-2 block text-sm whitespace-pre-wrap">
                                  {comment.body}
                                </span>
                              </Button>
                              <div className="mt-space-2 flex gap-space-2">
                                <Button
                                  size="xs"
                                  variant="outlined"
                                  color="secondary"
                                  disabled={posting}
                                  onClick={() => {
                                    setAnchor(null)
                                    setGeneralComment(false)
                                    setReplyTo(comment)
                                  }}
                                >
                                  Reply
                                </Button>
                                {comment.author_login === plan.user.login &&
                                  !posting && (
                                    <Button
                                      data-testid="comment-delete"
                                      size="xs"
                                      variant="outlined"
                                      color="secondary"
                                      onClick={() =>
                                        void removeComment(comment.id)
                                      }
                                    >
                                      Delete
                                    </Button>
                                  )}
                              </div>
                              {comments
                                .filter(
                                  (reply) => reply.parent_id === comment.id
                                )
                                .map((reply) => (
                                  <div
                                    key={reply.id}
                                    data-testid="plan-reply"
                                    className="mt-space-3 border-l border-default pl-space-3"
                                  >
                                    <span className="text-xs font-semibold">
                                      {reply.author}
                                    </span>
                                    <p className="mt-space-2 text-sm whitespace-pre-wrap">
                                      {reply.body}
                                    </p>
                                    {reply.author_login === plan.user.login &&
                                      !posting && (
                                        <Button
                                          size="xs"
                                          variant="plain"
                                          color="secondary"
                                          className="mt-space-2 focus-visible:ring-2 focus-visible:ring-focus focus-visible:outline-none"
                                          onClick={() =>
                                            void removeComment(reply.id)
                                          }
                                        >
                                          Delete
                                        </Button>
                                      )}
                                  </div>
                                ))}
                              {replyTo?.id === comment.id && composer}
                            </article>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                  {!selectionMode &&
                    comments.some(
                      (comment) => comment.author_login === plan.user.login
                    ) && (
                      <div className="border-t border-default p-space-3">
                        <Button
                          className="w-full focus-visible:ring-2 focus-visible:ring-focus focus-visible:outline-none"
                          disabled={submitting || posting || submitted}
                          onClick={() => void submitComments()}
                        >
                          {submitting
                            ? "Submitting…"
                            : submitted
                              ? "Comments submitted"
                              : "Submit to Robin"}
                        </Button>
                      </div>
                    )}
                </aside>
              </div>
            ) : (
              <div
                data-testid="plan-markdown"
                className="h-full w-full overflow-y-auto p-space-4 md:p-space-5"
              >
                <Markdown content={content} />
              </div>
            )
          ) : (
            <p className="p-space-5 text-sm text-tertiary">
              The artifact hasn't been written yet.
            </p>
          )}
        </section>
      </div>
    </main>
  )
}
