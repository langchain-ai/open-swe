import { useMemo, useState } from "react"
import { ShieldCheck } from "lucide-react"

import type { WorkflowPushApproval } from "@/features/agents/lib/types"
import {
  useWorkflowApprovalDecision,
  useWorkflowApprovals,
} from "@/features/agents/lib/queries"
import {
  Confirmation,
  ConfirmationAction,
  ConfirmationActions,
  ConfirmationDescription,
  ConfirmationTitle,
} from "@/components/ai-elements/confirmation"

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

  const isOwner = query.data?.isOwner === true
  const busy = decision.isPending
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
    <div className="border-b border-border bg-card px-4 py-3">
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-3">
        {approvals.map((approval) => (
          <Confirmation
            key={approval.fingerprint}
            approval={{ id: approval.fingerprint }}
            data-testid="workflow-approval-card"
            state="approval-requested"
          >
            <ShieldCheck />
            <ConfirmationTitle>
              Workflow file approval required
            </ConfirmationTitle>
            <ConfirmationDescription>
              <p className="text-xs">
                {approval.repo || "Repository"} on{" "}
                {approval.branch || "Current branch"} ·{" "}
                {shortSha(approval.baseSha)} → {shortSha(approval.headSha)}
              </p>
              <p className="font-mono text-[0.68rem] break-all">
                Fingerprint: {approval.fingerprint}
              </p>

              {!isOwner && (
                <p className="text-xs">
                  Only the thread owner can approve or reject this workflow
                  push.
                </p>
              )}
              {error && <p className="text-xs text-destructive">{error}</p>}

              <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_auto]">
                <div className="min-w-0">
                  <p className="text-xs font-medium text-foreground">
                    {fileLabel(approval.files.length)} changed
                  </p>
                  <ul className="mt-1 space-y-1 text-xs">
                    {approval.files.slice(0, 8).map((file) => (
                      <li
                        key={file}
                        className="truncate font-mono"
                        title={file}
                      >
                        {file}
                      </li>
                    ))}
                    {approval.files.length > 8 && (
                      <li>…and {approval.files.length - 8} more</li>
                    )}
                  </ul>
                </div>
                <div className="self-start rounded-md border border-border px-3 py-2 text-xs">
                  <span>{approval.diffStats.files} files</span>
                  <span className="mx-2 text-success-foreground">
                    +{approval.diffStats.additions}
                  </span>
                  <span className="text-destructive">
                    -{approval.diffStats.deletions}
                  </span>
                </div>
              </div>

              {approval.diffPreview && (
                <details open>
                  <summary className="cursor-pointer text-xs font-medium text-foreground">
                    Diff preview
                    {approval.diffPreviewTruncated ? " (truncated)" : ""}
                  </summary>
                  <pre className="mt-2 max-h-72 overflow-auto rounded-md border border-border bg-background p-3 text-[0.68rem] leading-relaxed text-foreground">
                    {approval.diffPreview}
                  </pre>
                </details>
              )}
            </ConfirmationDescription>
            <ConfirmationActions>
              {approval.approvalUrl && (
                <ConfirmationAction
                  variant="secondary"
                  onClick={() => {
                    window.location.href = approval.approvalUrl ?? ""
                  }}
                >
                  Open in Web
                </ConfirmationAction>
              )}
              <ConfirmationAction
                disabled={!isOwner || busy}
                onClick={() => void decide(approval, "approve")}
              >
                Approve
              </ConfirmationAction>
              <ConfirmationAction
                variant="destructive"
                disabled={!isOwner || busy}
                onClick={() => void decide(approval, "reject")}
              >
                Reject
              </ConfirmationAction>
            </ConfirmationActions>
          </Confirmation>
        ))}
      </div>
    </div>
  )
}
