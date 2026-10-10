import { WarningRegularIcon } from "@langchain/macaw-components/icons"
import { Text } from "@langchain/macaw-components/Text"
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
      className="mt-space-4 flex w-full flex-col gap-space-3"
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
            className="rounded-xl border border-default bg-surface-level-1 p-space-4 shadow-sm"
          >
            <div className="flex items-start gap-space-3">
              <ShieldCheckIcon
                size={20}
                weight="regular"
                className="mt-0.5 shrink-0 text-icon-brand"
              />
              <div className="min-w-0">
                <p className="text-xxs font-semibold tracking-wider text-brand-primary uppercase">
                  Push paused for review
                </p>
                <Text
                  as="h2"
                  variant="h3"
                  color="primary"
                  className="mt-space-1"
                >
                  {inherited
                    ? `Confirm workflow changes inherited from ${inherited}`
                    : "Confirm GitHub Actions workflow changes"}
                </Text>
                <p className="mt-space-1 text-sm text-secondary">
                  {inherited
                    ? `This branch now includes ${fileLabel(approval.files.length)} from merging ${inherited}. Open SWE did not author these workflow changes.`
                    : `Open SWE is ready to push ${fileLabel(approval.files.length)} in .github/workflows.`}
                </p>
              </div>
            </div>

            {inherited && (
              <div className="mt-space-4 flex gap-space-3 rounded-md border border-brand-subtle bg-brand-muted p-space-3">
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

            <div className="mt-space-3 flex gap-space-3 border-l-2 border-warning bg-warning p-space-3">
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

            <div className="mt-space-4 flex flex-wrap gap-space-2">
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
            <p className="mt-space-2 text-xxs text-secondary">
              Approval resumes this exact push only. If the workflow files
              change, Open SWE will ask again.
            </p>

            <div className="mt-space-4 grid gap-space-2 sm:grid-cols-2">
              <div className="rounded-md border border-default bg-surface-level-1 p-space-3">
                <p className="text-xxs font-medium tracking-wide text-secondary uppercase">
                  What happens next
                </p>
                <p className="mt-space-1 text-xs text-primary">
                  The paused push resumes; no other changes are approved.
                </p>
              </div>
              <div className="rounded-md border border-default bg-surface-level-1 p-space-3">
                <p className="text-xxs font-medium tracking-wide text-secondary uppercase">
                  Branch update
                </p>
                <p className="mt-space-1 font-mono text-xs text-primary">
                  {shortSha(approval.baseSha)} → {shortSha(approval.headSha)}
                </p>
              </div>
            </div>

            <details className="mt-space-4 rounded-md border border-default">
              <summary className="flex cursor-pointer items-center justify-between gap-space-3 p-space-3 text-xs font-medium text-primary">
                <span>Review files and diff</span>
                <span className="font-normal text-secondary">
                  {approval.diffStats.files} files
                  <span className="ml-space-2 text-success-secondary">
                    +{approval.diffStats.additions}
                  </span>
                  <span className="ml-space-2 text-error-secondary">
                    -{approval.diffStats.deletions}
                  </span>
                </span>
              </summary>
              <div className="border-t border-default p-space-3">
                <ul className="space-y-space-1 text-xs text-secondary">
                  {approval.files.map((file) => (
                    <li key={file} className="truncate font-mono" title={file}>
                      {file}
                    </li>
                  ))}
                </ul>
                {approval.diffPreview && (
                  <pre
                    className={cn(
                      "mt-space-3 max-h-72 overflow-auto rounded-md border border-default",
                      "bg-surface-level-1 p-space-3 text-xxs leading-relaxed text-primary"
                    )}
                  >
                    {approval.diffPreview}
                  </pre>
                )}
                {approval.diffPreviewTruncated && (
                  <p className="mt-space-2 text-xxs text-secondary">
                    Diff preview is truncated.
                  </p>
                )}
              </div>
            </details>

            <p className="mt-space-3 font-mono text-xxs break-all text-secondary">
              Approval ID: {approval.fingerprint}
            </p>
          </section>
        )
      })}
    </div>
  )
}
