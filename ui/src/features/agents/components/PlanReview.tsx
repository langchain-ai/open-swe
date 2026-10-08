import { useCallback, useEffect, useRef, useState } from "react"
import type { KeyboardEvent } from "react"

import type { PlanComment, PlanData, PlanTextAnchor } from "@/lib/plan"
import {
  addPlanComment,
  deletePlanComment,
  getPlanComments,
  submitPlanComments,
} from "@/lib/plan"
import { reportError } from "@/lib/errorReporting"
import { Button } from "@/components/ui/button"
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
  const format = plan.html.trim() ? "html" : "markdown"
  const content = format === "html" ? plan.html : plan.markdown
  const canComment = format === "html"

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      const mutation = commentMutation.current
      try {
        const next = await getPlanComments(plan.threadId)
        if (!cancelled && mutation === commentMutation.current)
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

  const submitComment = useCallback(async () => {
    const body = draft.trim()
    if (!body || !anchor) return
    setPosting(true)
    setError(null)
    commentMutation.current += 1
    try {
      const created = await addPlanComment(plan.threadId, body, anchor)
      setComments((current) => [...current, created])
      setSubmitted(false)
      setAnchor(null)
      setDraft("")
      setFocusComment({ id: created.id, key: Date.now() })
    } catch (commentError) {
      setError((commentError as Error).message)
    } finally {
      setPosting(false)
    }
  }, [anchor, draft, plan.threadId])

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
        setComments((current) => current.filter((comment) => comment.id !== id))
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

  return (
    <main
      data-testid="plan-review"
      className="@container flex min-h-0 flex-1 flex-col overflow-hidden bg-surface-level-1 text-primary"
    >
      <div className="flex min-h-0 w-full flex-1 flex-col gap-3 p-3 md:p-4">
        <header className="flex flex-col gap-3 border-b border-default pb-3 @3xl:flex-row @3xl:items-center @3xl:justify-between">
          <div data-testid="plan-summary" className="min-w-0">
            <h1 className="text-lg font-semibold text-primary">Artifact</h1>
            <p className="text-xs text-tertiary">
              Viewing as {plan.user.name}
            </p>
          </div>
          <div
            data-testid="plan-actions"
            className="flex flex-wrap items-center gap-2"
          >
            <Button
              data-testid="copy-plan"
              variant="secondary"
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
                  comments={comments}
                  onTextSelected={canComment ? setAnchor : undefined}
                  onCommentSelected={openComment}
                  focusCommentId={focusComment?.id ?? null}
                  focusCommentKey={focusComment?.key ?? 0}
                  className="h-full min-h-0 min-w-0 flex-1"
                />
                <aside
                  data-testid="plan-comments"
                  className="flex max-h-1/2 shrink-0 flex-col overflow-y-auto border-t border-default bg-surface-level-1/95 @3xl:max-h-none @3xl:w-80 @3xl:border-t-0 @3xl:border-l"
                >
                  <div className="border-b border-default p-3">
                    <h2 className="text-sm font-semibold">Comments</h2>
                    <p className="mt-0.5 text-xs text-secondary">
                      Highlight text in the preview to comment.
                    </p>
                  </div>
                  {anchor && (
                    <div
                      data-testid="comment-composer"
                      className="border-b border-default bg-surface-level-2/30 p-3"
                    >
                      <blockquote className="line-clamp-3 border-l-2 border-brand pl-2 text-xs text-secondary">
                        {anchor.exact}
                      </blockquote>
                      <textarea
                        data-testid="comment-input"
                        value={draft}
                        onChange={(event) => setDraft(event.target.value)}
                        onKeyDown={handleCommentKeyDown}
                        placeholder="Leave a comment"
                        rows={3}
                        autoFocus
                        className="mt-3 w-full resize-none rounded-md border border-default bg-surface-level-1 px-3 py-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-focus"
                      />
                      <div className="mt-2 flex justify-end gap-2">
                        <Button
                          variant="ghost"
                          size="sm"
                          disabled={posting}
                          onClick={() => {
                            setAnchor(null)
                            setDraft("")
                          }}
                        >
                          Cancel
                        </Button>
                        <Button
                          data-testid="comment-submit"
                          aria-keyshortcuts="Meta+Enter Control+Enter"
                          size="sm"
                          disabled={posting || !draft.trim()}
                          onClick={() => void submitComment()}
                        >
                          {posting ? "Posting…" : "Comment"}
                          {!posting && (
                            <kbd
                              aria-hidden="true"
                              className="ml-1 font-sans text-[0.625rem] opacity-80"
                            >
                              ⌘ ↵
                            </kbd>
                          )}
                        </Button>
                      </div>
                    </div>
                  )}
                  <div className="min-h-0 flex-1 overflow-y-auto p-3">
                    {comments.length === 0 ? (
                      <p className="text-xs text-secondary">
                        No comments yet.
                      </p>
                    ) : (
                      <div className="space-y-2">
                        {comments.map((comment, index) => (
                          <article
                            key={comment.id}
                            ref={(element) => {
                              if (element)
                                commentRefs.current.set(comment.id, element)
                              else commentRefs.current.delete(comment.id)
                            }}
                            data-testid="plan-comment"
                            className="rounded-lg border border-default bg-surface-level-1 p-3"
                          >
                            <button
                              type="button"
                              className="block w-full text-left focus-visible:outline-2 focus-visible:outline-[color:var(--border-focus)]"
                              onClick={() => openComment(comment.id)}
                            >
                              <span className="text-xs font-semibold">
                                {index + 1}. {comment.author}
                              </span>
                              {comment.anchor && (
                                <blockquote className="mt-2 line-clamp-2 border-l-2 border-yellow-400 pl-2 text-xs text-secondary">
                                  {comment.anchor.exact}
                                </blockquote>
                              )}
                              <span className="mt-2 block text-sm whitespace-pre-wrap">
                                {comment.body}
                              </span>
                            </button>
                            {comment.author_login === plan.user.login && (
                              <button
                                type="button"
                                data-testid="comment-delete"
                                className="mt-2 text-xs text-secondary hover:text-primary focus-visible:outline-2 focus-visible:outline-[color:var(--border-focus)]"
                                onClick={() => void removeComment(comment.id)}
                              >
                                Delete
                              </button>
                            )}
                          </article>
                        ))}
                      </div>
                    )}
                  </div>
                  {comments.some(
                    (comment) => comment.author_login === plan.user.login
                  ) && (
                    <div className="border-t border-default p-3">
                      <Button
                        className="w-full"
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
                className="h-full w-full overflow-y-auto p-4 md:p-6"
              >
                <Markdown content={content} />
              </div>
            )
          ) : (
            <p className="p-6 text-sm text-tertiary">
              The artifact hasn't been written yet.
            </p>
          )}
        </section>
      </div>
    </main>
  )
}
