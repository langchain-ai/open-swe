import { useCallback, useEffect, useState } from "react"
import { useNavigate } from "@tanstack/react-router"
import type { KeyboardEvent } from "react"

import type { PlanComment, PlanData } from "@/lib/plan"
import {
  addPlanComment,
  approvePlan,
  deletePlanComment,
  getPlanComments,
  rejectPlan,
  updatePlan,
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
  const [draft, setDraft] = useState("")
  const [posting, setPosting] = useState(false)
  const [decision, setDecision] = useState<string | null>(null)
  const [busy, setBusy] = useState<"approve" | "reject" | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const format = plan.html.trim() ? "html" : "markdown"
  const planContent = format === "html" ? plan.html : plan.markdown
  const [content, setContent] = useState(planContent)
  const [editing, setEditing] = useState(false)
  const [editDraft, setEditDraft] = useState(planContent)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (!editing) setContent(planContent)
  }, [planContent, editing])

  const isShared = plan.status === "shared"
  const canEdit =
    plan.isOwner &&
    !isShared &&
    plan.status !== "approved" &&
    plan.status !== "cancelled"

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      try {
        const next = await getPlanComments(plan.threadId)
        if (!cancelled) setComments(next)
      } catch {
        /* next poll retries */
      }
    }
    if (isShared) return
    void load()
    const timer = setInterval(load, POLL_MS)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [isShared, plan.threadId])

  const saveEdit = useCallback(async () => {
    const next = editDraft.trim()
    if (!next) {
      setError("The plan cannot be empty.")
      return
    }
    setSaving(true)
    setError(null)
    try {
      await updatePlan(plan.threadId, next, format)
      setContent(next)
      setEditing(false)
    } catch (editError) {
      setError((editError as Error).message)
    } finally {
      setSaving(false)
    }
  }, [editDraft, format, plan.threadId])

  const submitComment = useCallback(async () => {
    const body = draft.trim()
    if (!body) return
    setPosting(true)
    setError(null)
    try {
      const created = await addPlanComment(plan.threadId, body)
      setComments((current) => [...current, created])
      setDraft("")
    } catch (commentError) {
      setError((commentError as Error).message)
    } finally {
      setPosting(false)
    }
  }, [draft, plan.threadId])

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
      try {
        await deletePlanComment(plan.threadId, id)
        setComments((current) => current.filter((comment) => comment.id !== id))
      } catch (deleteError) {
        setError((deleteError as Error).message)
      }
    },
    [plan.threadId]
  )

  const decide = useCallback(
    async (kind: "approve" | "reject") => {
      setBusy(kind)
      setError(null)
      try {
        if (kind === "approve") {
          const { run_id: runId } = await approvePlan(plan.threadId)
          if (onApprove) onApprove(runId)
          else
            await navigate({
              to: "/agents/$threadId",
              params: { threadId: plan.threadId },
            })
          return
        }
        await rejectPlan(plan.threadId)
        setDecision("Changes requested — the agent is revising the plan.")
        await navigate({
          to: "/agents/$threadId",
          params: { threadId: plan.threadId },
        })
      } catch (decisionError) {
        setError((decisionError as Error).message)
      } finally {
        setBusy(null)
      }
    },
    [navigate, onApprove, plan.threadId]
  )

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
      className="min-h-0 flex-1 overflow-y-auto bg-background text-foreground"
    >
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-5 px-4 py-4 md:px-8 md:py-6">
        <header className="flex flex-col gap-3 border-b border-border pb-4 lg:flex-row lg:items-center lg:justify-between">
          <div data-testid="plan-summary" className="min-w-0">
            <h1 className="text-lg font-semibold text-foreground">
              {isShared ? "Shared response" : "Implementation plan"}
            </h1>
            <p className="text-xs text-muted-foreground/70">
              {isShared ? "Viewing" : "Reviewing"} as {plan.user.name}
              {plan.isOwner ? " (owner)" : ""} · status:{" "}
              <span data-testid="plan-status">{plan.status}</span>
            </p>
          </div>
          <div
            data-testid="plan-actions"
            className="flex flex-wrap items-center gap-2"
          >
            {decision && (
              <span
                data-testid="plan-decision"
                className="w-full text-xs text-muted-foreground"
              >
                {decision}
              </span>
            )}
            {editing ? (
              <>
                <Button
                  data-testid="cancel-edit-plan"
                  variant="secondary"
                  disabled={saving}
                  onClick={() => {
                    setEditing(false)
                    setError(null)
                  }}
                >
                  Cancel
                </Button>
                <Button
                  data-testid="save-plan"
                  disabled={saving || !editDraft.trim()}
                  onClick={() => void saveEdit()}
                >
                  {saving ? "Saving…" : "Save"}
                </Button>
              </>
            ) : (
              <>
                {canEdit && (
                  <Button
                    data-testid="edit-plan"
                    variant="secondary"
                    disabled={busy !== null}
                    onClick={() => {
                      setEditDraft(content)
                      setEditing(true)
                      setError(null)
                    }}
                  >
                    Edit {format === "html" ? "HTML" : "Markdown"}
                  </Button>
                )}
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
                {!isShared && plan.isOwner && (
                  <Button
                    data-testid="approve-plan"
                    disabled={busy !== null}
                    onClick={() => void decide("approve")}
                  >
                    Approve
                  </Button>
                )}
                {!isShared && (
                  <Button
                    data-testid="reject-plan"
                    variant="secondary"
                    disabled={busy !== null || comments.length === 0}
                    title={
                      comments.length === 0
                        ? "Leave a comment first"
                        : undefined
                    }
                    onClick={() => void decide("reject")}
                  >
                    Request changes
                  </Button>
                )}
              </>
            )}
          </div>
        </header>

        <section
          data-testid="plan-document"
          className="min-w-0 overflow-hidden rounded-xl border border-border bg-card"
        >
          {editing ? (
            <textarea
              data-testid="plan-editor"
              value={editDraft}
              onChange={(event) => setEditDraft(event.target.value)}
              spellCheck={false}
              className="min-h-[70vh] w-full resize-y bg-background px-4 py-3 font-mono text-sm text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring"
            />
          ) : content.trim() ? (
            format === "html" ? (
              <PlanArtifactFrame html={content} className="min-h-[70vh]" />
            ) : (
              <div data-testid="plan-markdown" className="min-h-[70vh] p-6">
                <Markdown content={content} />
              </div>
            )
          ) : (
            <p className="p-6 text-sm text-muted-foreground/70">
              The plan hasn't been written yet.
            </p>
          )}
        </section>

        {!isShared && (
          <section
            data-testid="plan-comments"
            className="flex flex-col gap-4 border-t border-border pt-5"
          >
            <div>
              <h2 className="text-base font-semibold">Comments</h2>
              <p className="text-xs text-muted-foreground">
                Feedback is sent to the agent with your decision.
              </p>
            </div>
            <div className="grid gap-3 md:grid-cols-2">
              {comments.length === 0 ? (
                <p className="text-sm text-muted-foreground/70">
                  No comments yet.
                </p>
              ) : (
                comments.map((comment) => (
                  <article
                    key={comment.id}
                    data-testid="plan-comment"
                    className="rounded-lg border border-border bg-card px-3 py-3"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-xs font-medium">
                        {comment.author}
                      </span>
                      <button
                        type="button"
                        data-testid="comment-delete"
                        className="text-xs text-muted-foreground hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring"
                        onClick={() => void removeComment(comment.id)}
                      >
                        Delete
                      </button>
                    </div>
                    <p className="mt-1 text-sm whitespace-pre-wrap">
                      {comment.body}
                    </p>
                  </article>
                ))
              )}
            </div>
            <div className="flex flex-col gap-2">
              {error && <p className="text-xs text-destructive">{error}</p>}
              <textarea
                data-testid="comment-input"
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                onKeyDown={handleCommentKeyDown}
                placeholder="Leave a comment on the plan"
                rows={3}
                className="w-full resize-y rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
              <div className="flex justify-end">
                <Button
                  data-testid="comment-submit"
                  size="sm"
                  disabled={posting || !draft.trim()}
                  onClick={() => void submitComment()}
                >
                  Comment
                </Button>
              </div>
            </div>
          </section>
        )}
      </div>
    </main>
  )
}
