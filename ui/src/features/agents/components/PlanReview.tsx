import { Button } from "@langchain/macaw-components/Button"
import { Text } from "@langchain/macaw-components/Text"
import { Textarea } from "@langchain/macaw-components/Textarea"
import { useCallback, useEffect, useRef, useState } from "react"
import type { KeyboardEvent } from "react"

import { SubmitShortcut } from "@/components/SubmitShortcut"
import type { PlanComment, PlanData, PlanTextAnchor } from "@/lib/plan"
import {
  addPlanComment,
  deletePlanComment,
  getPlanComments,
  submitPlanComments,
} from "@/lib/plan"
import { reportError } from "@/lib/errorReporting"
import { PlanArtifactFrame } from "@/features/agents/components/PlanArtifactFrame"
import { Markdown } from "@/features/agents/components/chat/Markdown"

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

export function PlanReview({ plan }: { plan: PlanData }) {
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
    } catch (submitError) {
      setError((submitError as Error).message)
    } finally {
      setSubmitting(false)
    }
  }, [plan.threadId])

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

  const openComment = useCallback((id: string) => {
    setFocusComment({ id, key: Date.now() })
    commentRefs.current
      .get(id)
      ?.scrollIntoView({ behavior: "smooth", block: "nearest" })
  }, [])

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
      <div className="flex min-h-0 w-full flex-1 flex-col gap-space-3 p-space-3 md:p-space-4">
        <header className="flex flex-col gap-space-3 border-b border-default pb-space-3 @3xl:flex-row @3xl:items-center @3xl:justify-between">
          <div data-testid="plan-summary" className="min-w-0">
            <Text as="h1" variant="h2" weight="semibold" color="primary">
              Artifact
            </Text>
            <p className="text-xs text-tertiary">Viewing as {plan.user.name}</p>
          </div>
          <div
            data-testid="plan-actions"
            className="flex flex-wrap items-center gap-space-2"
          >
            <Button
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
          className="flex min-h-0 min-w-0 flex-1 overflow-hidden rounded-xl border border-default bg-surface-level-1"
        >
          {content.trim() ? (
            format === "html" ? (
              <div className="flex min-h-0 min-w-0 flex-1 flex-col @3xl:flex-row">
                <PlanArtifactFrame
                  html={content}
                  comments={rootComments}
                  onTextSelected={
                    canComment && !posting
                      ? (next) => {
                          if (next) setReplyTo(null)
                          if (next || !draft.trim()) setAnchor(next)
                        }
                      : undefined
                  }
                  onCommentSelected={openComment}
                  focusCommentId={focusComment?.id ?? null}
                  focusCommentKey={focusComment?.key ?? 0}
                  className="h-full min-h-0 min-w-0 flex-1"
                />
                <aside
                  data-testid="plan-comments"
                  className="flex max-h-1/2 shrink-0 flex-col overflow-y-auto border-t border-default bg-surface-level-1/95 @3xl:max-h-none @3xl:w-80 @3xl:border-t-0 @3xl:border-l"
                >
                  <div className="border-b border-default p-space-3">
                    <div className="flex items-center justify-between gap-space-2">
                      <Text as="h2" variant="h5" weight="semibold">
                        Comments
                      </Text>
                      <Button
                        size="xs"
                        variant="plain"
                        color="secondary"
                        disabled={posting}
                        onClick={() => {
                          setAnchor(null)
                          setReplyTo(null)
                          setGeneralComment(true)
                        }}
                      >
                        Add comment
                      </Button>
                    </div>
                    <p className="mt-0.5 text-xs text-secondary">
                      Add a general comment or highlight text to annotate.
                    </p>
                  </div>
                  {(!replyTo || replyParentMissing) && composer}
                  <div className="min-h-0 flex-1 overflow-y-auto p-space-3">
                    {comments.length === 0 ? (
                      <p className="text-xs text-secondary">No comments yet.</p>
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
                            className="rounded-lg border border-default bg-surface-level-1 p-space-3"
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
                                {index + 1}. {comment.author}
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
                                variant="plain"
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
                                    variant="plain"
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
                              .filter((reply) => reply.parent_id === comment.id)
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
                  {comments.some(
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
                            : "Submit comments"}
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
