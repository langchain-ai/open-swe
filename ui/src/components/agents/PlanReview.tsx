import { useCallback, useEffect, useState } from "react"

import type { PlanComment, PlanData } from "@/lib/plan"
import {
  addPlanComment,
  approvePlan,
  deletePlanComment,
  getPlanComments,
  rejectPlan,
} from "@/lib/plan"
import { Button } from "@/components/ui/button"
import { Markdown } from "@/components/agents/ported"
import { useResolvedTheme } from "@/lib/theme"

const POLL_MS = 4000

export function PlanReview({ plan }: { plan: PlanData }) {
  const resolvedTheme = useResolvedTheme()
  const [comments, setComments] = useState<Array<PlanComment>>([])
  const [draft, setDraft] = useState("")
  const [posting, setPosting] = useState(false)
  const [decision, setDecision] = useState<string | null>(null)
  const [busy, setBusy] = useState<"approve" | "reject" | null>(null)
  const [error, setError] = useState<string | null>(null)

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
    void load()
    const timer = setInterval(load, POLL_MS)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [plan.threadId])

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
        if (kind === "approve") await approvePlan(plan.threadId)
        else await rejectPlan(plan.threadId)
        setDecision(
          kind === "approve"
            ? "Plan approved — the agent is implementing it."
            : "Changes requested — the agent is revising the plan."
        )
      } catch (e) {
        setError((e as Error).message)
      } finally {
        setBusy(null)
      }
    },
    [plan.threadId]
  )

  return (
    <div
      data-testid="plan-review"
      className="flex min-h-0 flex-1 flex-col bg-[var(--ui-bg)] text-[var(--ui-text)]"
    >
      <div className="flex items-center justify-between gap-4 border-b border-[var(--ui-border)] px-6 py-3">
        <div>
          <h1 className="text-base font-semibold text-[var(--ui-text)]">
            Implementation plan
          </h1>
          <p className="text-xs text-[var(--ui-text-dim)]">
            Reviewing as {plan.user.name}
            {plan.isOwner ? " (owner)" : ""} · status:{" "}
            <span data-testid="plan-status">{plan.status}</span>
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {decision && (
            <span
              data-testid="plan-decision"
              className="text-xs text-[var(--ui-text-dim)]"
            >
              {decision}
            </span>
          )}
          {plan.isOwner && (
            <Button
              data-testid="approve-plan"
              disabled={busy !== null || decision !== null}
              onClick={() => void decide("approve")}
            >
              Approve
            </Button>
          )}
          <Button
            data-testid="reject-plan"
            variant="secondary"
            // Requesting changes feeds the comments to the agent, so it's
            // meaningless with none — disable until at least one is left.
            disabled={busy !== null || decision !== null || comments.length === 0}
            title={
              comments.length === 0
                ? "Leave a comment first to request changes"
                : undefined
            }
            onClick={() => void decide("reject")}
          >
            Request changes
          </Button>
        </div>
      </div>

      <div className="flex min-h-0 flex-1 overflow-hidden">
        <div
          className="min-h-0 flex-1 overflow-auto px-6 py-4"
          data-testid="plan-document"
          data-color-scheme={resolvedTheme}
        >
          {plan.markdown.trim() ? (
            <Markdown content={plan.markdown} />
          ) : (
            <p className="text-sm text-[var(--ui-text-dim)]">
              The plan hasn't been written yet.
            </p>
          )}
        </div>

        <aside className="flex w-80 shrink-0 flex-col border-l border-[var(--ui-border)]">
          <div className="border-b border-[var(--ui-border)] px-4 py-3">
            <h2 className="text-sm font-semibold text-[var(--ui-text)]">
              Comments
            </h2>
          </div>
          <div
            className="min-h-0 flex-1 space-y-3 overflow-auto px-4 py-3"
            data-testid="plan-comments"
          >
            {comments.length === 0 ? (
              <p className="text-xs text-[var(--ui-text-dim)]">
                No comments yet.
              </p>
            ) : (
              comments.map((c) => (
                <div
                  key={c.id}
                  data-testid="plan-comment"
                  className="rounded-md border border-[var(--ui-border)] bg-[var(--ui-panel)] px-3 py-2"
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-xs font-medium text-[var(--ui-text)]">
                      {c.author}
                    </span>
                    <button
                      type="button"
                      data-testid="comment-delete"
                      className="text-xs text-[var(--ui-text-dim)] hover:text-[var(--ui-text)]"
                      onClick={() => void removeComment(c.id)}
                    >
                      Delete
                    </button>
                  </div>
                  <p className="mt-1 text-sm whitespace-pre-wrap text-[var(--ui-text)]">
                    {c.body}
                  </p>
                </div>
              ))
            )}
          </div>
          <div className="border-t border-[var(--ui-border)] p-3">
            {error && (
              <p className="mb-2 text-xs text-[color:var(--ui-danger)]">
                {error}
              </p>
            )}
            <textarea
              data-testid="comment-input"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder="Leave a comment on the plan"
              rows={3}
              className="w-full resize-none rounded-md border border-[var(--ui-border)] bg-[var(--ui-bg)] px-2 py-1.5 text-sm text-[var(--ui-text)] outline-none focus:border-[var(--ui-accent)]"
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
      </div>
    </div>
  )
}
