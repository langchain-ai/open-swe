import { WarningRegularIcon } from "@langchain/macaw-components/icons"
import { Button } from "@langchain/macaw-components/Button"
import { GitMergeIcon } from "@phosphor-icons/react/dist/ssr/GitMerge"
import { ShieldCheckIcon } from "@phosphor-icons/react/dist/ssr/ShieldCheck"
import { useMemo } from "react"

import type { WorkflowPushApproval } from "@/features/agents/lib/types"
import {
  agentMutationKeys,
  useWorkflowApprovalDecision,
  useWorkflowApprovals,
} from "@/features/agents/lib/queries"
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
            className="rounded-xl border border-default bg-surface-level-1 p-4 shadow-sm"
          >
            <div className="flex items-start gap-3">
              <ShieldCheckIcon
                size={20}
                weight="regular"
                className="mt-0.5 shrink-0 text-icon-brand"
              />
              <div className="min-w-0">
                <p className="text-[0.68rem] font-semibold tracking-wider text-brand-primary uppercase">
                  Push paused for review
                </p>
                <h2 className="mt-1 text-base font-semibold text-primary">
                  {inherited
                    ? `Confirm workflow changes inherited from ${inherited}`
                    : "Confirm GitHub Actions workflow changes"}
                </h2>
                <p className="mt-1 text-sm text-secondary">
                  {inherited
                    ? `This branch now includes ${fileLabel(approval.files.length)} from merging ${inherited}. Open SWE did not author these workflow changes.`
                    : `Open SWE is ready to push ${fileLabel(approval.files.length)} in .github/workflows.`}
                </p>
              </div>
            </div>

            {inherited && (
              <div className="mt-4 flex gap-3 rounded-md border border-brand-subtle bg-brand-muted p-3">
                <GitMergeIcon
                  size={16}
                  weight="regular"
                  className="mt-0.5 shrink-0 text-icon-brand"
                />
                <div>
                  <p className="text-xs font-medium text-primary">
                    Where these changes came from
                  </p>
                  <p className="mt-0.5 text-xs text-secondary">
                    Merging {inherited} into{" "}
                    {approval.branch || "the current branch"}
                  </p>
                </div>
              </div>
            )}

            <div className="mt-3 flex gap-3 border-l-2 border-warning bg-warning p-3">
              <WarningRegularIcon
                size={16}
                className="mt-0.5 shrink-0 text-icon-warning"
              />
              <div>
                <p className="text-xs font-medium text-primary">
                  Why you need to confirm
                </p>
                <p className="mt-0.5 text-xs text-secondary">
                  Workflow files control CI jobs and may access repository
                  secrets. Open SWE pauses before pushing any workflow change.
                </p>
              </div>
            </div>

            <div className="mt-4 flex flex-wrap gap-2">
              <Button
                size="md"
                disabled={busy}
                onClick={() => decide(approval, "approve")}
              >
                Approve &amp; continue push
              </Button>
              <Button
                size="md"
                color="secondary"
                variant="outlined"
                disabled={busy}
                onClick={() => decide(approval, "reject")}
              >
                Cancel push
              </Button>
            </div>
            <p className="mt-2 text-[0.7rem] text-secondary">
              Approval resumes this exact push only. If the workflow files
              change, Open SWE will ask again.
            </p>

            <div className="mt-4 grid gap-2 sm:grid-cols-2">
              <div className="rounded-md border border-default bg-surface-level-1 p-3">
                <p className="text-[0.65rem] font-medium tracking-wide text-secondary uppercase">
                  What happens next
                </p>
                <p className="mt-1 text-xs text-primary">
                  The paused push resumes; no other changes are approved.
                </p>
              </div>
              <div className="rounded-md border border-default bg-surface-level-1 p-3">
                <p className="text-[0.65rem] font-medium tracking-wide text-secondary uppercase">
                  Branch update
                </p>
                <p className="mt-1 font-mono text-xs text-primary">
                  {shortSha(approval.baseSha)} → {shortSha(approval.headSha)}
                </p>
              </div>
            </div>

            <details className="mt-4 rounded-md border border-default">
              <summary className="flex cursor-pointer items-center justify-between gap-3 p-3 text-xs font-medium text-primary">
                <span>Review files and diff</span>
                <span className="font-normal text-secondary">
                  {approval.diffStats.files} files
                  <span className="ml-2 text-success-secondary">
                    +{approval.diffStats.additions}
                  </span>
                  <span className="ml-2 text-error-secondary">
                    -{approval.diffStats.deletions}
                  </span>
                </span>
              </summary>
              <div className="border-t border-default p-3">
                <ul className="space-y-1 text-xs text-secondary">
                  {approval.files.map((file) => (
                    <li key={file} className="truncate font-mono" title={file}>
                      {file}
                    </li>
                  ))}
                </ul>
                {approval.diffPreview && (
                  <pre
                    className={cn(
                      "mt-3 max-h-72 overflow-auto rounded-md border border-default",
                      "bg-surface-level-1 p-3 text-[0.68rem] leading-relaxed text-primary"
                    )}
                  >
                    {approval.diffPreview}
                  </pre>
                )}
                {approval.diffPreviewTruncated && (
                  <p className="mt-2 text-[0.7rem] text-secondary">
                    Diff preview is truncated.
                  </p>
                )}
              </div>
            </details>

            <p className="mt-3 font-mono text-[0.65rem] break-all text-secondary">
              Approval ID: {approval.fingerprint}
            </p>
          </section>
        )
      })}
    </div>
  )
}
