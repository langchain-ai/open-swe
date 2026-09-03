import { useMemo, useState } from "react"
import { GitMerge, ShieldCheck, TriangleAlert } from "lucide-react"

import type { WorkflowPushApproval } from "@/features/agents/lib/types"
import {
  useWorkflowApprovalDecision,
  useWorkflowApprovals,
} from "@/features/agents/lib/queries"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

function shortSha(value: string): string {
  return value ? value.slice(0, 7) : "unknown"
}

function pendingApprovals(
  approvals: Array<WorkflowPushApproval> | undefined
): Array<WorkflowPushApproval> {
  return (approvals ?? []).filter((approval) => approval.status === "pending")
}

function fileLabel(count: number): string {
  return count === 1 ? "1 file" : `${count} files`
}

export function WorkflowApprovalCard({
  threadId,
  pollWhileActive = false,
}: {
  threadId: string
  pollWhileActive?: boolean
}) {
  const query = useWorkflowApprovals(threadId, { pollWhileActive })
  const decision = useWorkflowApprovalDecision(threadId)
  const [error, setError] = useState<string | null>(null)
  const approvals = useMemo(
    () => pendingApprovals(query.data?.approvals),
    [query.data?.approvals]
  )

  if (approvals.length === 0) return null

  const decide = async (
    approval: WorkflowPushApproval,
    kind: "approve" | "reject"
  ) => {
    setError(null)
    try {
      await decision.mutateAsync({
        fingerprint: approval.fingerprint,
        decision: kind,
      })
    } catch (e) {
      setError((e as Error).message)
    }
  }

  return (
    <div
      data-testid="workflow-approval-group"
      className="mt-4 flex w-full flex-col gap-3"
    >
      {approvals.map((approval) => {
        const busy = decision.isPending
        const inherited = approval.inheritedFrom
        return (
          <section
            key={approval.fingerprint}
            data-testid="workflow-approval-card"
            className="rounded-xl border border-border bg-card p-4 shadow-sm"
          >
            <div className="flex items-start gap-3">
              <ShieldCheck className="mt-0.5 size-5 shrink-0 text-primary" />
              <div className="min-w-0">
                <p className="text-[0.68rem] font-semibold tracking-wider text-primary uppercase">
                  Push paused for review
                </p>
                <h2 className="mt-1 text-base font-semibold text-foreground">
                  {inherited
                    ? `Confirm workflow changes inherited from ${inherited}`
                    : "Confirm GitHub Actions workflow changes"}
                </h2>
                <p className="mt-1 text-sm text-muted-foreground">
                  {inherited
                    ? `This branch now includes ${fileLabel(approval.files.length)} from merging ${inherited}. Open SWE did not author these workflow changes.`
                    : `Open SWE is ready to push ${fileLabel(approval.files.length)} in .github/workflows.`}
                </p>
              </div>
            </div>

            {inherited && (
              <div className="mt-4 flex gap-3 rounded-md border border-primary/20 bg-primary/5 p-3">
                <GitMerge className="mt-0.5 size-4 shrink-0 text-primary" />
                <div>
                  <p className="text-xs font-medium text-foreground">
                    Where these changes came from
                  </p>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    Merging {inherited} into{" "}
                    {approval.branch || "the current branch"}
                  </p>
                </div>
              </div>
            )}

            <div className="mt-3 flex gap-3 border-l-2 border-warning-foreground bg-warning/10 p-3">
              <TriangleAlert className="mt-0.5 size-4 shrink-0 text-warning-foreground" />
              <div>
                <p className="text-xs font-medium text-foreground">
                  Why you need to confirm
                </p>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  Workflow files control CI jobs and may access repository
                  secrets. Open SWE pauses before pushing any workflow change.
                </p>
              </div>
            </div>

            {error && <p className="mt-3 text-xs text-destructive">{error}</p>}

            <div className="mt-4 flex flex-wrap gap-2">
              <Button
                disabled={busy}
                onClick={() => void decide(approval, "approve")}
              >
                Approve &amp; continue push
              </Button>
              <Button
                variant="secondary"
                disabled={busy}
                onClick={() => void decide(approval, "reject")}
              >
                Cancel push
              </Button>
            </div>
            <p className="mt-2 text-[0.7rem] text-muted-foreground">
              Approval resumes this exact push only. If the workflow files
              change, Open SWE will ask again.
            </p>

            <div className="mt-4 grid gap-2 sm:grid-cols-2">
              <div className="rounded-md border border-border bg-background p-3">
                <p className="text-[0.65rem] font-medium tracking-wide text-muted-foreground uppercase">
                  What happens next
                </p>
                <p className="mt-1 text-xs text-foreground">
                  The paused push resumes; no other changes are approved.
                </p>
              </div>
              <div className="rounded-md border border-border bg-background p-3">
                <p className="text-[0.65rem] font-medium tracking-wide text-muted-foreground uppercase">
                  Branch update
                </p>
                <p className="mt-1 font-mono text-xs text-foreground">
                  {shortSha(approval.baseSha)} → {shortSha(approval.headSha)}
                </p>
              </div>
            </div>

            <details className="mt-4 rounded-md border border-border">
              <summary className="flex cursor-pointer items-center justify-between gap-3 p-3 text-xs font-medium text-foreground">
                <span>Review files and diff</span>
                <span className="font-normal text-muted-foreground">
                  {approval.diffStats.files} files
                  <span className="ml-2 text-success-foreground">
                    +{approval.diffStats.additions}
                  </span>
                  <span className="ml-2 text-destructive">
                    -{approval.diffStats.deletions}
                  </span>
                </span>
              </summary>
              <div className="border-t border-border p-3">
                <ul className="space-y-1 text-xs text-muted-foreground">
                  {approval.files.map((file) => (
                    <li key={file} className="truncate font-mono" title={file}>
                      {file}
                    </li>
                  ))}
                </ul>
                {approval.diffPreview && (
                  <pre
                    className={cn(
                      "mt-3 max-h-72 overflow-auto rounded-md border border-border",
                      "bg-background p-3 text-[0.68rem] leading-relaxed text-foreground"
                    )}
                  >
                    {approval.diffPreview}
                  </pre>
                )}
                {approval.diffPreviewTruncated && (
                  <p className="mt-2 text-[0.7rem] text-muted-foreground">
                    Diff preview is truncated.
                  </p>
                )}
              </div>
            </details>

            <p className="mt-3 font-mono text-[0.65rem] break-all text-muted-foreground">
              Approval ID: {approval.fingerprint}
            </p>
          </section>
        )
      })}
    </div>
  )
}
