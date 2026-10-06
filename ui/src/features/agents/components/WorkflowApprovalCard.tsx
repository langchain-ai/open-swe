import { useMemo } from "react"
import { GitMerge, ShieldCheck, TriangleAlert } from "lucide-react"

import type { WorkflowPushApproval } from "@/features/agents/lib/types"
import {
  agentMutationKeys,
  useWorkflowApprovalDecision,
  useWorkflowApprovals,
} from "@/features/agents/lib/queries"
import { Button } from "@langchain/gtm-platform-design-system/ui/button"
import { usePendingVariables } from "@/lib/optimistic"
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
  const pendingDecisions = usePendingVariables<{ fingerprint: string }>(
    agentMutationKeys.workflowDecision(threadId)
  )
  const approvals = useMemo(
    () => pendingApprovals(query.data?.approvals),
    [query.data?.approvals]
  )

  if (approvals.length === 0) return null

  const decide = (approval: WorkflowPushApproval, kind: "approve" | "reject") =>
    decision.mutate({ fingerprint: approval.fingerprint, decision: kind })

  return (
    <div
      data-testid="workflow-approval-group"
      className="mt-4 flex w-full flex-col gap-3"
    >
      {approvals.map((approval) => {
        const busy = pendingDecisions.some(
          (vars) => vars.fingerprint === approval.fingerprint
        )
        const inherited = approval.inheritedFrom
        return (
          <section
            key={approval.fingerprint}
            data-testid="workflow-approval-card"
            className="rounded-control border border-line bg-panel p-4 shadow-control"
          >
            <div className="flex items-start gap-3">
              <ShieldCheck className="mt-0.5 size-5 shrink-0 text-primary" />
              <div className="min-w-0">
                <p className="text-meta font-semibold tracking-wider text-primary uppercase">
                  Push paused for review
                </p>
                <h2 className="mt-1 text-title font-semibold text-ink">
                  {inherited
                    ? `Confirm workflow changes inherited from ${inherited}`
                    : "Confirm GitHub Actions workflow changes"}
                </h2>
                <p className="mt-1 text-body text-ink-subtle">
                  {inherited
                    ? `This branch now includes ${fileLabel(approval.files.length)} from merging ${inherited}. Open SWE did not author these workflow changes.`
                    : `Open SWE is ready to push ${fileLabel(approval.files.length)} in .github/workflows.`}
                </p>
              </div>
            </div>

            {inherited && (
              <div className="mt-4 flex gap-3 rounded-badge border border-primary/20 bg-primary/5 p-3">
                <GitMerge className="mt-0.5 size-4 shrink-0 text-primary" />
                <div>
                  <p className="text-label font-medium text-ink">
                    Where these changes came from
                  </p>
                  <p className="mt-0.5 text-meta text-ink-subtle">
                    Merging {inherited} into{" "}
                    {approval.branch || "the current branch"}
                  </p>
                </div>
              </div>
            )}

            <div className="mt-3 flex gap-3 border-l-2 border-attention bg-attention-bg p-3">
              <TriangleAlert className="mt-0.5 size-4 shrink-0 text-attention" />
              <div>
                <p className="text-label font-medium text-ink">
                  Why you need to confirm
                </p>
                <p className="mt-0.5 text-meta text-ink-subtle">
                  Workflow files control CI jobs and may access repository
                  secrets. Open SWE pauses before pushing any workflow change.
                </p>
              </div>
            </div>

            <div className="mt-4 flex flex-wrap gap-2">
              <Button
                disabled={busy}
                onClick={() => decide(approval, "approve")}
              >
                Approve &amp; continue push
              </Button>
              <Button
                variant="outline"
                disabled={busy}
                onClick={() => decide(approval, "reject")}
              >
                Cancel push
              </Button>
            </div>
            <p className="mt-2 text-meta text-ink-subtle">
              Approval resumes this exact push only. If the workflow files
              change, Open SWE will ask again.
            </p>

            <div className="mt-4 grid gap-2 sm:grid-cols-2">
              <div className="rounded-badge border border-line bg-canvas p-3">
                <p className="text-meta font-medium tracking-wide text-ink-subtle uppercase">
                  What happens next
                </p>
                <p className="mt-1 text-label text-ink">
                  The paused push resumes; no other changes are approved.
                </p>
              </div>
              <div className="rounded-badge border border-line bg-canvas p-3">
                <p className="text-meta font-medium tracking-wide text-ink-subtle uppercase">
                  Branch update
                </p>
                <p className="mt-1 font-mono text-label text-ink">
                  {shortSha(approval.baseSha)} → {shortSha(approval.headSha)}
                </p>
              </div>
            </div>

            <details className="mt-4 rounded-badge border border-line">
              <summary className="flex cursor-pointer items-center justify-between gap-3 p-3 text-label font-medium text-ink">
                <span>Review files and diff</span>
                <span className="font-normal text-ink-subtle">
                  {approval.diffStats.files} files
                  <span className="ml-2 text-positive">
                    +{approval.diffStats.additions}
                  </span>
                  <span className="ml-2 text-risk">
                    -{approval.diffStats.deletions}
                  </span>
                </span>
              </summary>
              <div className="border-t border-line p-3">
                <ul className="space-y-1 text-meta text-ink-subtle">
                  {approval.files.map((file) => (
                    <li key={file} className="truncate font-mono" title={file}>
                      {file}
                    </li>
                  ))}
                </ul>
                {approval.diffPreview && (
                  <pre
                    className={cn(
                      "mt-3 max-h-72 overflow-auto rounded-badge border border-line",
                      "bg-canvas p-3 text-meta leading-relaxed text-ink"
                    )}
                  >
                    {approval.diffPreview}
                  </pre>
                )}
                {approval.diffPreviewTruncated && (
                  <p className="mt-2 text-meta text-ink-subtle">
                    Diff preview is truncated.
                  </p>
                )}
              </div>
            </details>

            <p className="mt-3 font-mono text-meta break-all text-ink-subtle">
              Approval ID: {approval.fingerprint}
            </p>
          </section>
        )
      })}
    </div>
  )
}
