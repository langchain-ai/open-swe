import { useCallback, useState } from "react"
import { useNavigate } from "@tanstack/react-router"

import type { PlanData } from "@/lib/plan"
import { approvePlan } from "@/lib/plan"
import { Button } from "@/components/ui/button"
import { PlanArtifactFrame } from "@/features/agents/components/PlanArtifactFrame"
import { Markdown } from "@/features/agents/components/chat/Markdown"
import { cn } from "@/lib/utils"

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
  const [busy, setBusy] = useState<"approve" | "reject" | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const format = plan.html.trim() ? "html" : "markdown"
  const content = format === "html" ? plan.html : plan.markdown
  const isShared = plan.status === "shared"
  const canApprove = plan.status === "ready"

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
      await navigate({
        to: "/agents/$threadId",
        params: { threadId: plan.threadId },
        search: { feedback: true },
      })
    } catch (navigationError) {
      setError((navigationError as Error).message)
    } finally {
      setBusy(null)
    }
  }, [navigate, plan.threadId])

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
      <div
        className={cn(
          "flex min-h-0 w-full flex-1 flex-col",
          isShared ? "gap-0" : "gap-3 p-3 md:p-4"
        )}
      >
        <header className="flex flex-col gap-3 border-b border-border pb-3">
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
              <PlanArtifactFrame html={content} className="h-full min-h-0" />
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
