import { useCallback, useEffect, useRef, useState } from "react"
import { useNavigate } from "@tanstack/react-router"
import type { KeyboardEvent } from "react"

import type { PlanComment, PlanData, PlanTextAnchor } from "@/lib/plan"
import {
  addPlanComment,
  approvePlan,
  deletePlanComment,
  getPlanComments,
  rejectPlan,
} from "@/lib/plan"
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

export function PlanReview({
  plan,
  onApprove,
}: {
  plan: PlanData
  onApprove?: (runId: string) => void
}) {
  const navigate = useNavigate()
  const [comments, setComments] = useState<Array<PlanComment>>([])
  const [anchor, setAnchor] = useState<PlanTextAnchor | null>(null)
  const [draft, setDraft] = useState("")
  const [posting, setPosting] = useState(false)
  const [focusComment, setFocusComment] = useState<{
    id: string
    key: number
  } | null>(null)
  const [busy, setBusy] = useState<"approve" | "reject" | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const commentRefs = useRef(new Map<string, HTMLElement>())
  const commentMutation = useRef(0)
  const format = plan.html.trim() ? "html" : "markdown"
  const content = format === "html" ? plan.html : plan.markdown
  const isShared = plan.status === "shared"
  const canApprove = plan.status === "ready"
  const canComment = format === "html" && !isShared && canApprove

  useEffect(() => {
    if (isShared) return
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
  }, [isShared, plan.threadId])

  const submitComment = useCallback(async () => {
    const body = draft.trim()
    if (!body || !anchor) return
    setPosting(true)
    setError(null)
    commentMutation.current += 1
    try {
      const created = await addPlanComment(plan.threadId, body, anchor)
      setComments((current) => [...current, created])
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

  const removeComment = useCallback(
    async (id: string) => {
      setError(null)
      commentMutation.current += 1
      try {
        await deletePlanComment(plan.threadId, id)
        setComments((current) => current.filter((comment) => comment.id !== id))
      } catch (deleteError) {
        setError((deleteError as Error).message)
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

  const approve = useCallback(async () => {
    setBusy("approve")
    setError(null)
    try {
      const { run_id: runId } = await approvePlan(plan.threadId)
      if (onApprove) onApprove(runId)
      else
        await navigate({
          to: "/agents/$threadId",
          params: { threadId: plan.threadId },
        })
    } catch (decisionError) {
      setError((decisionError as Error).message)
    } finally {
      setBusy(null)
    }
  }, [navigate, onApprove, plan.threadId])

  const requestChanges = useCallback(async () => {
    setBusy("reject")
    setError(null)
    try {
      if (comments.length > 0) {
        await rejectPlan(plan.threadId)
        await navigate({
          to: "/agents/$threadId",
          params: { threadId: plan.threadId },
        })
      } else {
        await navigate({
          to: "/agents/$threadId",
          params: { threadId: plan.threadId },
          search: { feedback: true },
        })
      }
    } catch (decisionError) {
      setError((decisionError as Error).message)
    } finally {
      setBusy(null)
    }
  }, [comments.length, navigate, plan.threadId])

  const copyPlan = useCallback(async () => {
    setError(null)
    if (await copyToClipboard(content)) {
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    } else {
      setError(`Couldn't copy the plan ${format} to the clipboard.`)
    }
  }, [content, format])

  return (
    <main
      data-testid="plan-review"
      className="@container flex min-h-0 flex-1 flex-col overflow-hidden bg-background text-foreground"
    >
      <div className="flex min-h-0 w-full flex-1 flex-col gap-3 p-3 md:p-4">
        <header className="flex flex-col gap-3 border-b border-border pb-3 @3xl:flex-row @3xl:items-center @3xl:justify-between">
          <div data-testid="plan-summary" className="min-w-0">
            <h1 className="text-lg font-semibold text-foreground">
              {isShared ? "Shared response" : "Implementation plan"}
            </h1>
            <p className="text-xs text-muted-foreground/70">
              {isShared ? "Viewing" : "Reviewing"} as {plan.user.name} · status:{" "}
              <span data-testid="plan-status">{plan.status}</span>
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
            {canApprove && (
              <Button
                data-testid="approve-plan"
                disabled={busy !== null}
                onClick={() => void approve()}
              >
                Approve
              </Button>
            )}
            {!isShared && (
              <Button
                data-testid="reject-plan"
                variant="secondary"
                disabled={busy !== null}
                onClick={() => void requestChanges()}
              >
                Request changes
              </Button>
            )}
          </div>
        </header>

        {error && <p className="text-xs text-destructive">{error}</p>}

        <section
          data-testid="plan-document"
          className="flex min-h-0 min-w-0 flex-1 overflow-hidden rounded-xl border border-border bg-card"
        >
          {content.trim() ? (
            format === "html" ? (
              <div className="flex min-h-0 min-w-0 flex-1">
                <PlanArtifactFrame
                  html={content}
                  comments={comments}
                  onTextSelected={canComment ? setAnchor : undefined}
                  onCommentSelected={openComment}
                  focusCommentId={focusComment?.id ?? null}
                  focusCommentKey={focusComment?.key ?? 0}
                  className="h-full min-h-0 min-w-0 flex-1"
                />
                {!isShared && (
                  <aside
                    data-testid="plan-comments"
                    className="flex w-80 shrink-0 flex-col border-l border-border bg-background/95"
                  >
                    <div className="border-b border-border p-3">
                      <h2 className="text-sm font-semibold">Comments</h2>
                      <p className="mt-0.5 text-xs text-muted-foreground">
                        Highlight text in the preview to comment.
                      </p>
                    </div>
                    {anchor && (
                      <div
                        data-testid="comment-composer"
                        className="border-b border-border bg-muted/30 p-3"
                      >
                        <blockquote className="line-clamp-3 border-l-2 border-primary pl-2 text-xs text-muted-foreground">
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
                          className="mt-3 w-full resize-none rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
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
                            size="sm"
                            disabled={posting || !draft.trim()}
                            onClick={() => void submitComment()}
                          >
                            {posting ? "Posting…" : "Comment"}
                          </Button>
                        </div>
                      </div>
                    )}
                    <div className="min-h-0 flex-1 overflow-y-auto p-3">
                      {comments.length === 0 ? (
                        <p className="text-xs text-muted-foreground">
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
                              className="rounded-lg border border-border bg-card p-3"
                            >
                              <button
                                type="button"
                                className="block w-full text-left focus-visible:outline-2 focus-visible:outline-ring"
                                onClick={() => openComment(comment.id)}
                              >
                                <span className="text-xs font-semibold">
                                  {index + 1}. {comment.author}
                                </span>
                                {comment.anchor && (
                                  <blockquote className="mt-2 line-clamp-2 border-l-2 border-yellow-400 pl-2 text-xs text-muted-foreground">
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
                                  className="mt-2 text-xs text-muted-foreground hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring"
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
                  </aside>
                )}
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
            <p className="p-6 text-sm text-muted-foreground/70">
              The plan hasn't been written yet.
            </p>
          )}
        </section>
      </div>
    </main>
  )
}
