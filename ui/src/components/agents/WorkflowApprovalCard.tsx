import { useMemo, useState } from "react"
import { ShieldCheck } from "lucide-react"

import type { WorkflowPushApproval } from "@/lib/agents/types"
import {
  useWorkflowApprovalDecision,
  useWorkflowApprovals,
} from "@/lib/agents/queries"
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

  const isOwner = query.data?.isOwner === true
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
    <div className="border-b border-[var(--ui-border)] bg-[var(--ui-panel)] px-4 py-3">
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-3">
        {approvals.map((approval) => {
          const busy = decision.isPending
          return (
            <section
              key={approval.fingerprint}
              data-testid="workflow-approval-card"
              className="rounded-lg border border-[var(--ui-border)] bg-[var(--ui-bg)] p-3 shadow-sm"
            >
              <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
                <div className="min-w-0 space-y-1">
                  <div className="flex items-center gap-2 text-sm font-semibold text-[var(--ui-text)]">
                    <ShieldCheck className="size-4 text-[var(--ui-accent)]" />
                    Workflow file approval required
                  </div>
                  <p className="text-xs text-[var(--ui-text-dim)]">
                    {approval.repo || "Repository"} on{" "}
                    {approval.branch || "current branch"} ·{" "}
                    {shortSha(approval.baseSha)} → {shortSha(approval.headSha)}
                  </p>
                  <p className="font-mono text-[0.68rem] break-all text-[var(--ui-text-dim)]">
                    fingerprint: {approval.fingerprint}
                  </p>
                </div>
                <div className="flex shrink-0 flex-wrap gap-2">
                  {approval.approvalUrl && (
                    <Button
                      variant="secondary"
                      onClick={() => {
                        window.location.href = approval.approvalUrl ?? ""
                      }}
                    >
                      Open in Web
                    </Button>
                  )}
                  <Button
                    disabled={!isOwner || busy}
                    onClick={() => void decide(approval, "approve")}
                  >
                    Approve
                  </Button>
                  <Button
                    variant="destructive"
                    disabled={!isOwner || busy}
                    onClick={() => void decide(approval, "reject")}
                  >
                    Reject
                  </Button>
                </div>
              </div>

              {!isOwner && (
                <p className="mt-3 text-xs text-[var(--ui-text-dim)]">
                  Only the thread owner can approve or reject this workflow
                  push.
                </p>
              )}
              {error && (
                <p className="mt-3 text-xs text-[color:var(--ui-danger)]">
                  {error}
                </p>
              )}

              <div className="mt-3 grid gap-3 md:grid-cols-[minmax(0,1fr)_auto]">
                <div className="min-w-0">
                  <p className="text-xs font-medium text-[var(--ui-text)]">
                    {fileLabel(approval.files.length)} changed
                  </p>
                  <ul className="mt-1 space-y-1 text-xs text-[var(--ui-text-dim)]">
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
                <div className="rounded-md border border-[var(--ui-border)] px-3 py-2 text-xs text-[var(--ui-text-dim)]">
                  <span>{approval.diffStats.files} files</span>
                  <span className="mx-2 text-[var(--ui-success)]">
                    +{approval.diffStats.additions}
                  </span>
                  <span className="text-[var(--ui-danger)]">
                    -{approval.diffStats.deletions}
                  </span>
                </div>
              </div>

              {approval.diffPreview && (
                <details className="mt-3" open>
                  <summary className="cursor-pointer text-xs font-medium text-[var(--ui-text)]">
                    Diff preview
                    {approval.diffPreviewTruncated ? " (truncated)" : ""}
                  </summary>
                  <pre
                    className={cn(
                      "mt-2 max-h-72 overflow-auto rounded-md border border-[var(--ui-border)]",
                      "bg-[var(--ui-panel)] p-3 text-[0.68rem] leading-relaxed text-[var(--ui-text)]"
                    )}
                  >
                    {approval.diffPreview}
                  </pre>
                </details>
              )}
            </section>
          )
        })}
      </div>
    </div>
  )
}
