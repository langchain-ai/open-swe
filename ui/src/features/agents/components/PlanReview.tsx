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
import { Markdown } from "@/features/agents/components/chat/Markdown"
import { useResolvedTheme } from "@/lib/theme"
import { cn } from "@/lib/utils"

const POLL_MS = 4000

// Copy text to the clipboard across browsers: prefer the async Clipboard API
// (needs a secure context), and fall back to a hidden-textarea + execCommand
// for older Safari/Firefox and non-HTTPS origins. Returns whether it copied.
async function copyToClipboard(text: string): Promise<boolean> {
  // The DOM types mark navigator.clipboard required, but it's absent in older
  // browsers and non-secure origins — treat it as optional.
  const nav = navigator as { clipboard?: Clipboard }
  try {
    if (window.isSecureContext && nav.clipboard) {
      await nav.clipboard.writeText(text)
      return true
    }
  } catch {
    /* fall through to the legacy path */
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
    const ok = document.execCommand("copy")
    document.body.removeChild(textarea)
    return ok
  } catch {
    return false
  }
}

export function PlanReview({
  plan,
  compact = false,
  onApprove,
}: {
  plan: PlanData
  compact?: boolean
  onApprove?: (runId: string) => void
}) {
  const navigate = useNavigate()
  const resolvedTheme = useResolvedTheme()
  const [comments, setComments] = useState<Array<PlanComment>>([])
  const [draft, setDraft] = useState("")
  const [posting, setPosting] = useState(false)
  const [decision, setDecision] = useState<string | null>(null)
  const [busy, setBusy] = useState<"approve" | "reject" | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  // Locally track the displayed markdown so a manual edit shows immediately; the
  // route's query stops polling once content exists, so the prop won't refetch.
  const [markdown, setMarkdown] = useState(plan.markdown)
  const [editing, setEditing] = useState(false)
  const [editDraft, setEditDraft] = useState(plan.markdown)
  const [saving, setSaving] = useState(false)

  // Reflect external plan updates (e.g. an agent revision) while not editing.
  useEffect(() => {
    if (!editing) setMarkdown(plan.markdown)
  }, [plan.markdown, editing])

  const isShared = plan.status === "shared"
  const canEdit =
    plan.isOwner &&
    !isShared &&
    plan.status !== "approved" &&
    plan.status !== "cancelled"

  const startEditing = useCallback(() => {
    setEditDraft(markdown)
    setEditing(true)
    setError(null)
  }, [markdown])

  const cancelEditing = useCallback(() => {
    setEditing(false)
    setError(null)
  }, [])

  const saveEdit = useCallback(async () => {
    const next = editDraft.trim()
    if (!next) {
      setError("The plan cannot be empty.")
      return
    }
    setSaving(true)
    setError(null)
    try {
      const result = await updatePlan(plan.threadId, next)
      setMarkdown(result.markdown)
      setEditing(false)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setSaving(false)
    }
  }, [editDraft, plan.threadId])

  // Poll so reviewers see each other's comments without a realtime transport.
  useEffect(() => {
    let cancelled = false
    const load = async () => {
      try {
        const next = await getPlanComments(plan.threadId)
        if (!cancelled) setComments(next)
      } catch {
        /* transient; next tick retries */
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

  const submitComment = useCallback(async () => {
    const body = draft.trim()
    if (!body) return
    setPosting(true)
    setError(null)
    try {
      const created = await addPlanComment(plan.threadId, body)
      setComments((prev) => [...prev, created])
      setDraft("")
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setPosting(false)
    }
  }, [draft, plan.threadId])

  const handleCommentKeyDown = useCallback(
    (event: KeyboardEvent<HTMLTextAreaElement>) => {
      if (event.key !== "Enter" || (!event.metaKey && !event.ctrlKey)) return
      event.preventDefault()
      if (posting || !draft.trim()) return
      void submitComment()
    },
    [draft, posting, submitComment]
  )

  const removeComment = useCallback(
    async (id: string) => {
      try {
        await deletePlanComment(plan.threadId, id)
        setComments((prev) => prev.filter((c) => c.id !== id))
      } catch (e) {
        setError((e as Error).message)
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
      } catch (e) {
        setError((e as Error).message)
      } finally {
        setBusy(null)
      }
    },
    [navigate, onApprove, plan.threadId]
  )

  const copyPlan = useCallback(async () => {
    setError(null)
    if (await copyToClipboard(markdown)) {
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    } else {
      setError("Couldn't copy the plan to the clipboard.")
    }
  }, [markdown])

  return (
    <div
      data-testid="plan-review"
      className="flex min-h-0 flex-1 flex-col bg-background text-foreground"
    >
      <div
        className={cn(
          "flex flex-col gap-3 border-b border-border px-4 py-3 md:flex-row md:items-center md:justify-between md:gap-4",
          !compact && "md:px-6"
        )}
      >
        <div className="min-w-0">
          <h1 className="text-base font-semibold text-foreground">
            {isShared ? "Shared response" : "Implementation plan"}
          </h1>
          <p className="text-xs text-muted-foreground/70">
            {isShared ? "Viewing" : "Reviewing"} as {plan.user.name}
            {plan.isOwner ? " (owner)" : ""} · status:{" "}
            <span data-testid="plan-status">{plan.status}</span>
          </p>
        </div>
        <div className="flex min-w-0 flex-wrap items-center gap-2 md:shrink-0 md:justify-end">
          {decision && (
            <span
              data-testid="plan-decision"
              className="w-full text-xs text-muted-foreground/70 md:w-auto"
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
                onClick={cancelEditing}
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
                  disabled={busy !== null || decision !== null}
                  onClick={startEditing}
                >
                  Edit
                </Button>
              )}
              <Button
                data-testid="copy-plan"
                variant="secondary"
                disabled={!markdown.trim()}
                onClick={() => void copyPlan()}
              >
                {copied ? "Copied!" : "Copy markdown"}
              </Button>
              {!isShared && plan.isOwner && (
                <Button
                  data-testid="approve-plan"
                  disabled={busy !== null || decision !== null}
                  onClick={() => void decide("approve")}
                >
                  Approve
                </Button>
              )}
              {!isShared && (
                <Button
                  data-testid="reject-plan"
                  variant="secondary"
                  // Requesting changes feeds the comments to the agent, so it's
                  // meaningless with none — disable until at least one is left.
                  disabled={
                    busy !== null || decision !== null || comments.length === 0
                  }
                  title={
                    comments.length === 0
                      ? "Leave a comment first to request changes"
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
      </div>

      <div
        className={cn(
          "flex min-h-0 flex-1 flex-col overflow-y-auto",
          !compact && "md:flex-row md:overflow-hidden"
        )}
      >
        <div
          className={cn(
            "min-w-0 px-4 py-4 md:min-h-0 md:flex-1 md:overflow-auto",
            !compact && "md:px-6"
          )}
          data-testid="plan-document"
          data-color-scheme={resolvedTheme}
        >
          {editing ? (
            <div className="flex h-full flex-col gap-2">
              {error && <p className="text-xs text-destructive">{error}</p>}
              <textarea
                data-testid="plan-editor"
                value={editDraft}
                onChange={(e) => setEditDraft(e.target.value)}
                spellCheck={false}
                className="min-h-[20rem] w-full flex-1 resize-none rounded-md border border-border bg-background px-3 py-2 font-mono text-sm text-foreground outline-none focus:border-primary"
              />
            </div>
          ) : markdown.trim() ? (
            <Markdown content={markdown} />
          ) : (
            <p className="text-sm text-muted-foreground/70">
              The plan hasn't been written yet.
            </p>
          )}
        </div>

        {!isShared && (
          <aside
            className={cn(
              "flex shrink-0 flex-col border-t border-border",
              !compact && "md:w-80 md:border-t-0 md:border-l"
            )}
          >
            <div className="border-b border-border px-4 py-3">
              <h2 className="text-sm font-semibold text-foreground">
                Comments
              </h2>
            </div>
            <div
              className="max-h-80 space-y-3 overflow-auto px-4 py-3 md:max-h-none md:min-h-0 md:flex-1"
              data-testid="plan-comments"
            >
              {comments.length === 0 ? (
                <p className="text-xs text-muted-foreground/70">
                  No comments yet.
                </p>
              ) : (
                comments.map((c) => (
                  <div
                    key={c.id}
                    data-testid="plan-comment"
                    className="rounded-md border border-border bg-card px-3 py-2"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-xs font-medium text-foreground">
                        {c.author}
                      </span>
                      <button
                        type="button"
                        data-testid="comment-delete"
                        className="text-xs text-muted-foreground/70 hover:text-foreground"
                        onClick={() => void removeComment(c.id)}
                      >
                        Delete
                      </button>
                    </div>
                    <p className="mt-1 text-sm whitespace-pre-wrap text-foreground">
                      {c.body}
                    </p>
                  </div>
                ))
              )}
            </div>
            <div className="border-t border-border p-3">
              {error && (
                <p className="mb-2 text-xs text-destructive">{error}</p>
              )}
              <textarea
                data-testid="comment-input"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={handleCommentKeyDown}
                placeholder="Leave a comment on the plan"
                rows={3}
                className="w-full resize-none rounded-md border border-border bg-background px-2 py-1.5 text-sm text-foreground outline-none focus:border-primary"
              />
              <div className="mt-2 flex justify-end">
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
          </aside>
        )}
      </div>
    </div>
  )
}
